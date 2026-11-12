import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from lxml import html

from app import db
from app.services import addressbook_io
from app.web import create_app


SAMPLE = (Path(__file__).parent / "fixtures" / "Adressprobe.csv").read_bytes()
HEADER = "nachname;vorname;wohnort"


class AddressbookTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        app = create_app(db_path=base / "test.sqlite3", backup_dir=base / "backups")
        app.testing = True
        self.client = app.test_client()
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
