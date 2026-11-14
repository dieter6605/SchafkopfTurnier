import csv
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from lxml import html

from app import db
from app.services import addressbook_io
from app.web import create_app
from tests.form_client import form_client


SAMPLE = (Path(__file__).parent / "fixtures" / "Adressprobe.csv").read_bytes()
HEADER = "nachname;vorname;wohnort"


class AddressbookTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        app = create_app(db_path=base / "test.sqlite3", backup_dir=base / "backups")
        app.testing = True
        self.client = form_client(app)
        self.con = db.connect()
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort) VALUES(1,'Alt','Ada','Ort')")
        self.con.commit()

    def send(self, data, step="preview", token="", as_json=True):
        if isinstance(data, str):
            data = data.encode()
        return self.client.post("/addresses/import", data={
            "file": (BytesIO(data), "adressen.csv"), "step": step, "preview_token": token,
        }, headers={"Accept": "application/json" if as_json else "text/html"})

    def snapshot(self):
        return {table: list(map(tuple, self.con.execute("SELECT * FROM " + table + " ORDER BY 1"))) for table in (
            "addressbooks", "addresses", "wohnorte", "sqlite_sequence", "tournament_participants",
        )}

    def apply(self, data):
        preview = self.send(data).get_json()
        self.assertTrue(preview["token"], preview["errors"])
        result = self.send(data, step="apply", token=preview["token"])
        self.assertEqual(result.location, "/addresses")

    def register(self):
        with self.con:
            self.con.execute("INSERT INTO tournaments(title,event_date,start_time) VALUES('Probe','2026-11-12','18:00')")
            self.con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name) "
                             "VALUES(1,1,1,'Probe')")

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return html.fromstring(response.get_data(as_text=True))

    def test_directory_search_filters_export_and_return_after_edit(self):
        with self.con:
            self.con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort,telefon) "
                             "VALUES(1,'Alt','Ada','Ort','08123')")
        stats = self.page("/addresses/stats")
        quality = stats.xpath("//a[span='Telefon fehlt']/@href")[0]
        filters = self.page(quality).xpath("//form[@method='get']")[0]
        self.assertEqual(filters.fields["phone"], "fehlt")
        self.assertTrue(filters.xpath("details[@open]"))
        data = dict(filters.fields)
        data["q"] = "Ada Alt"
        path = filters.action + "?" + urlencode({k: v for k, v in data.items() if v is not None})
        page = self.page(path)
        row, = page.xpath("//tbody/tr")
        self.assertIn("Alt, Ada", row.text_content())
        edit = self.page(row.xpath("td[1]/a/@href")[0])
        form = next(f for f in edit.forms if f.action == "/addresses/1/edit")
        self.assertEqual(form.fields["next"], path)
        values = dict(form.fields)
        values["notizen"] = "Besuch am Samstag"
        self.assertEqual(self.client.post(form.action, data=values).location, path)
        self.assertEqual(self.con.execute("SELECT notizen FROM addresses WHERE id=1").fetchone()[0], values["notizen"])
        for query in (path, "/addresses?q=%231", "/addresses?q=Alt+Ada"):
            listing = self.page(query)
            link = listing.get_element_by_id("btnAddrExportFiltered").get("href")
            exported = list(csv.DictReader(StringIO(self.client.get(link).data.decode("utf-8-sig")), delimiter=";"))
            expected = ["1", "2"] if query.endswith("Alt+Ada") else ["1"]
            self.assertEqual(sorted(r["id"] for r in exported), expected)
            self.assertEqual(len(listing.xpath("//tbody/tr")), len(expected))

    def test_workspace_navigation_and_invite_filter_after_toggle(self):
        for path, current in (("/addresses", "Adressen"), ("/addresses/1/edit", "Adressen"),
                              ("/addresses/new", "Adressen"), ("/addresses/stats", "Statistik"),
                              ("/addresses/import", "CSV-Import")):
            with self.subTest(path=path):
                page = self.page(path)
                self.assertEqual(page.xpath("//nav[@aria-label='Adressbuchnavigation']/a[@aria-current]/text()"), [current])
                self.assertEqual(len(page.xpath("//h1")), 1)
        with self.con:
            self.con.execute("UPDATE addresses SET invite=1 WHERE id=1")
        def invite_count(label):
            return self.page("/addresses/stats").xpath("//a[span=$label]/strong/text()", label=label)
        self.assertEqual(invite_count("Einladbar ohne E-Mail"), ["1"])
        listing = self.page("/addresses?invite=an")
        form, = listing.xpath("//form[button[@aria-pressed='true']]")
        self.assertEqual(self.client.post(form.action, data=dict(form.fields)).location, "/addresses?invite=an")
        self.assertFalse(self.page("/addresses?invite=an").xpath("//tbody/tr"))
        self.assertEqual(invite_count("Nicht einladbar"), ["1"])
        off = self.page("/addresses?invite=aus")
        self.assertTrue(off.xpath("//button[@aria-pressed='false']"))

    def test_directory_pagination_exports_all_matching_pages(self):
        with self.con:
            self.con.executemany("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort) "
                                 "VALUES(1,'Verein',?,'Testort')", [(f"Person {n:02d}",) for n in range(26)])
        first = self.page("/addresses?q=Verein&per_page=25")
        self.assertEqual(len(first.xpath("//tbody/tr")), 25)
        second = self.page(first.xpath("//a[@aria-label='Nächste Seite']/@href")[0])
        self.assertEqual(len(second.xpath("//tbody/tr")), 1)
        self.assertEqual(second.xpath("//nav[@aria-label='Seiten der Trefferliste']//a[@aria-current]/text()"), ["2"])
        export = self.client.get(second.get_element_by_id("btnAddrExportFiltered").get("href"))
        self.assertEqual(len(list(csv.DictReader(StringIO(export.data.decode("utf-8-sig")), delimiter=";"))), 26)

    def test_header_only_invalid_rows_and_duplicate_ids_cannot_replace(self):
        before = self.snapshot()
        for source, part in [
            ("", "Pflichtspalten"), (HEADER+"\n", "keine Adresszeilen"),
            (HEADER+"\nGut;Ada;Ort\nKaputt;;Ort", "Zeile 3"),
            ("id;"+HEADER+"\n2;Gut;Ada;Ort\n2;Falsch;Ben;Ort", "Zeile 3"),
            (HEADER+";status\nGut;Ada;Ort;unbekannt", "Zeile 2"),
            (HEADER+";invite\nGut;Ada;Ort;ja", "Zeile 2"),
            (HEADER+";id\nGut;Ada;Ort;X", "Zeile 2"),
            (HEADER+"\nGut;Ada;Ort;extra", "Zeile 2"),
            (HEADER+'\n"offen;Ada;Ort', "Zeile 2"),
            (HEADER+";nachname\nGut;Ada;Ort;Gut", "Zeile 1"),
        ]:
            with self.subTest(source=source):
                preview = self.send(source).get_json()
                self.assertFalse(preview["token"])
                self.assertIn(part, " ".join(preview["errors"]))
                self.assertNotEqual(self.send(source, step="apply").location, "/addresses")
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.send(b'\xff').status_code, 400)
        mixed = self.send(HEADER+"\nGut;Ada;Ort\nKaputt;;Ort").get_json()
        self.assertEqual((mixed["valid"], mixed["invalid"]), (1, 1))

    def test_preview_then_explicit_replace_with_schema_defaults(self):
        before = self.snapshot()
        self.assertEqual(self.send(SAMPLE, step="apply").status_code, 400)
        preview = self.send(SAMPLE, as_json=False)
        page = html.fromstring(preview.get_data(as_text=True))
        token = page.xpath("//input[@name='preview_token']/@value")[0]
        self.assertTrue(token)
        self.assertEqual(self.snapshot(), before)
        result = self.send(b'\xef\xbb\xbf'+SAMPLE, step="apply", token=token, as_json=False)
        self.assertEqual(result.location, "/addresses")
        rows = self.con.execute("SELECT status,invite,participation_count,created_at,updated_at FROM addresses").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(tuple(r[:3]) == ("aktiv", 1, 0) and r[3] and r[4] for r in rows))
        self.assertEqual(before["addressbooks"], self.snapshot()["addressbooks"])

    def test_invite_csv_values_and_omitted_column(self):
        with self.con:
            self.con.execute("UPDATE addresses SET invite=0 WHERE id=1")
        rows = "id;" + HEADER + "\n1;Alt;Ada;Ort\n2;Neu;Ben;Ort"
        self.apply(rows)

        def flags():
            return dict(self.con.execute("SELECT id,invite FROM addresses"))

        self.assertEqual(flags(), {1: 0, 2: 1})
        self.apply("id;" + HEADER + ";invite\n1;Alt;Ada;Ort;1\n2;Neu;Ben;Ort;  \n3;Neu;Eva;Ort;0")
        self.assertEqual(flags(), {1: 1, 2: 0, 3: 0})
        self.apply("id;" + HEADER + "\n1;Alt;Ada;Ort\n2;Neu;Ben;Ort\n3;Neu;Eva;Ort")
        self.assertEqual(flags(), {1: 1, 2: 0, 3: 0})

    def test_legacy_invite_export_round_trip(self):
        with self.con:
            self.con.execute("ALTER TABLE addresses DROP COLUMN invite")
            self.con.execute("ALTER TABLE addresses ADD COLUMN invite INTEGER")
        export = self.client.get("/addresses/export").data.decode("utf-8-sig")
        records = list(csv.DictReader(StringIO(export), delimiter=";"))
        self.assertEqual([r["invite"] for r in records], ["0"])
        self.apply(export)
        self.assertEqual(self.con.execute("SELECT invite FROM addresses").fetchone()[0], 0)
        self.apply(HEADER + ";invite\nAlt;Ada;Ort;")
        self.assertEqual(self.con.execute("SELECT invite FROM addresses").fetchone()[0], 0)

    def test_other_books_shared_places_and_explicit_ids(self):
        with self.con:
            self.con.execute("INSERT INTO addressbooks(name,is_default) VALUES('Zweites',0)")
            self.con.execute("INSERT INTO addresses(id,addressbook_id,nachname,vorname,wohnort,plz,ort) "
                             "VALUES(77,2,'Zweiter','Ben','Gemeinsam','04000','Alt')")
            self.con.execute("INSERT INTO wohnorte(wohnort,plz,ort) VALUES('Gemeinsam','04000','Alt')")
            self.con.execute("INSERT INTO wohnorte(wohnort,plz,ort) VALUES('Separat','01000','Liste')")
        other = tuple(self.con.execute("SELECT * FROM addresses WHERE id=77").fetchone())
        before = self.snapshot()
        data = "id;"+HEADER+";plz;ort\n77;Neu;Ada;Gemeinsam;05555;Neu"
        preview = self.send(data).get_json()
        self.assertIn("ander", preview["errors"][0])
        self.assertEqual(self.snapshot(), before)
        self.apply(data.replace("77;Neu", "55;Neu")+"\n56;Neu;Ben;Dorf;01234;Dorfstadt")
        self.assertEqual(tuple(self.con.execute("SELECT * FROM addresses WHERE id=77").fetchone()), other)
        self.assertEqual(self.snapshot()["addressbooks"], before["addressbooks"])
        self.assertEqual(self.snapshot()["wohnorte"][:2], before["wohnorte"])
        self.assertEqual(self.con.execute("SELECT plz FROM wohnorte WHERE wohnort='Dorf'").fetchone()[0], "01234")

    def test_source_or_target_changes_after_preview_block_confirmation(self):
        token = self.send(SAMPLE).get_json()["token"]
        before = self.snapshot()
        self.assertEqual(self.send(SAMPLE+b"Neu;Ben;Dorf\n", "apply", token).status_code, 400)
        self.assertEqual(self.snapshot(), before)
        with self.con:
            self.con.execute("UPDATE addresses SET notizen='Korrektur' WHERE id=1")
        before = self.snapshot()
        self.assertEqual(self.send(SAMPLE, "apply", token).status_code, 400)
        self.assertEqual(self.snapshot(), before)
        token = self.send(SAMPLE).get_json()["token"]
        self.register()
        before = self.snapshot()
        self.assertEqual(self.send(SAMPLE, "apply", token).status_code, 400)
        self.assertEqual(self.snapshot(), before)
        p = self.send(SAMPLE).get_json()
        self.assertEqual((p["valid"], p["registered"], p["token"]), (2, 1, ""))

    def test_history_and_registration_protect_delete_and_permit_deactivation(self):
        for col, value in (("tournament_years", "1998"), ("last_tournament_at", "2025"), ("participation_count", 1)):
            with self.subTest(col=col):
                with self.con:
                    self.con.execute(f"UPDATE addresses SET {col}=? WHERE id=1", (value,))
                page = html.fromstring(self.client.get("/addresses/1/edit").get_data(as_text=True))
                self.assertTrue(page.xpath("//form[@action='/addresses/1/delete']//button[@disabled]"))
                before = self.snapshot()
                self.client.post("/addresses/1/delete")
                self.assertEqual(self.snapshot(), before)
                with self.con:
                    self.con.execute(f"UPDATE addresses SET {col}=? WHERE id=1", (0 if col=="participation_count" else None,))
        self.register()
        before = self.snapshot()
        self.client.post("/addresses/1/delete")
        self.assertEqual(self.snapshot(), before)
        self.client.post("/addresses/1/deactivate")
        self.assertEqual(self.con.execute("SELECT status FROM addresses WHERE id=1").fetchone()[0], "inaktiv")
        with self.con:
            self.con.execute("DELETE FROM tournament_participants")
        self.client.post("/addresses/1/delete")
        self.assertIsNone(self.con.execute("SELECT id FROM addresses WHERE id=1").fetchone())

    def test_export_round_trip_and_historical_addresses_without_live_registrations(self):
        with self.con:
            self.con.execute("UPDATE addresses SET notizen=?,participation_count=1,last_tournament_at='2001',"
                             "tournament_years='2001' WHERE id=1", ('Zeile 1; "A"\nZeile 2',))
        before = self.snapshot()
        for source in (SAMPLE, "id;"+HEADER+"\n1;Anders;Ada;Ort",
                       "id;"+HEADER+";tournament_years\n1;Alt;Ada;Ort;"):
            self.assertFalse(self.send(source).get_json()["token"])
        self.assertEqual(self.snapshot(), before)
        full = self.client.get("/addresses/export").data
        self.apply(full)
        self.assertEqual(self.snapshot(), before)
        self.apply("id;"+HEADER+"\n1;Alt;Ada;Neuer Ort")
        self.assertEqual(tuple(self.con.execute("SELECT participation_count,tournament_years,last_tournament_at FROM addresses").fetchone()), (1, "2001", "2001"))

    def test_database_failure_restores_original_rows_and_lookup(self):
        with self.con:
            self.con.execute("""CREATE TRIGGER reject_test BEFORE INSERT ON addresses
                                WHEN NEW.vorname='Ben' BEGIN SELECT RAISE(ABORT,'Test'); END""")
        before = self.snapshot()
        token = self.send(SAMPLE).get_json()["token"]
        result = self.send(SAMPLE, "apply", token)
        self.assertIn("Zeile 3", result.get_json()["error"])
        self.assertEqual(self.snapshot(), before)
        # Auch ein Aufrufer, der die Ausnahme in der Transaktion behandelt, darf keine Teiländerung sehen.
        with self.con:
            with self.assertRaises(ValueError):
                addressbook_io.import_addresses_replace_default_from_csv_text(con=self.con, csv_text=SAMPLE.decode())
            self.assertEqual(self.snapshot(), before)
