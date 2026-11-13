import csv
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from zipfile import ZipFile

from lxml import etree, html

from app import db
from app.web import create_app
from tests.form_client import form_client


class ParticipantIntegrityTests(unittest.TestCase):
    tournament = "/tournaments/1"
    participants = "/tournaments/1/participants"
    table = "/tournaments/1/rounds/1/results/1"

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)
        app = create_app(db_path=path / "test.sqlite3", backup_dir=path / "backups")
        app.testing = True
        self.app = app
        self.client = form_client(app)
        self.client.post("/tournaments/new", data={
            "title": "Probe", "event_date": "2026-11-12", "start_time": "19:00", "marker": "261112TEST",
        })
        for name in ("Anna", "Berta", "Clara", "Dora"):
            self.add_player(name)

    def add_player(self, name):
        self.client.post(self.participants + "/quickadd", data={
            "vorname": name, "nachname": "Probe", "wohnort": "Testort",
        })

    def draw(self):
        self.client.post(self.tournament + "/rounds/1/draw", data={"expected_round_id": "0"})

    def form(self):
        page = self.client.get(self.table)
        self.assertEqual(page.status_code, 200)
        form = html.fromstring(page.get_data(as_text=True)).forms[0]
        values = dict(form.fields)
        points = form.xpath(".//input[@data-skt='points']/@name")
        self.assertEqual(len(points), 4)
        values.update(zip(points, ["30", "-10", "-10", "-10"]))
        return values

    def query(self, sql):
        with closing(db.connect()) as con:
            return list(map(tuple, con.execute(sql)))

    def snapshot(self):
        return [self.query("SELECT * FROM " + table + " ORDER BY id") for table in (
            "addresses", "tournament_participants", "tournament_rounds",
            "tournament_seats", "tournament_scores", "audit_log", "tournaments",
        )]

    def prepare_correction(self):
        self.draw()
        values = self.form()
        values.update(points_1="30", points_2="-10", points_3="-10", points_4="-10", soli_1="3")
        self.client.post(self.table, data=values)
        with closing(db.connect()) as con, con:
            con.execute("""INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort)
                           VALUES(1,'Huber','Berta','Testort')""")
        roster = html.fromstring(self.client.get(self.participants).get_data(as_text=True))
        link, = roster.get_element_by_id("tp1").xpath(".//a[normalize-space()='Anmeldung berichtigen']/@href")
        page = html.fromstring(self.client.get(link).get_data(as_text=True))
        form, = page.xpath("//form[.//textarea[@name='reason']]")
        fields = dict(form.fields)
        fields.update(new_address_id="5", reason="Tischblatt von Berta geprüft.")
        return link, fields

    def test_correct_identity_transfers_all_rounds_and_records_the_reason(self):
        path, fields = self.prepare_correction()
        second = self.tournament + "/rounds/2"
        self.client.post(second + "/draw", data={"draw_mode": "seeding"})
        values = dict(html.fromstring(self.client.get(second + "/results/1").get_data(as_text=True)).forms[0].fields)
        values.update(points_1="-15", points_2="5", points_3="5", points_4="5", soli_1="1")
        self.client.post(second + "/results/1", data=values)
        self.client.post(self.participants + "/1/withdraw", data={"expected_address_id": "1"})
        old_table = self.form()
        before = self.snapshot()
        self.client.post(path, data=fields)
        self.assertEqual(self.snapshot(), before)  # Stand vor Runde 2
        page = html.fromstring(self.client.get(path).get_data(as_text=True))
        fields["expected_state"] = page.xpath("//input[@name='expected_state']/@value")[0]
        self.assertEqual(self.client.post(path, data=fields).status_code, 302)
        after = self.snapshot()
        self.assertEqual(before[0], after[0])  # Adressbuch einschließlich Historie
        self.assertEqual(before[2:5], after[2:5])  # Runden, Sitze, Werte
        self.assertEqual(self.query("SELECT player_no,address_id,withdrawn_at IS NOT NULL FROM tournament_participants WHERE id=1"),
                         [(1, 5, 1)])
        row, = self.query("SELECT tp_id,address_id_old,address_id_new,note FROM audit_log WHERE action='correct_identity'")
        self.assertEqual(row[:3], (1, 1, 5))
        for part in ("Startnummer 1", "Probe, Anna", "Huber, Berta", "Punkte: 15", "Soli: 4", fields["reason"]):
            self.assertIn(part, row[3])
        self.client.post(path, data=fields)  # wiederholtes Speichern
        self.client.post(self.table, data=old_table)  # vor der Berichtigung geöffneter Tisch
        self.assertEqual(self.snapshot(), after)
        ranking = self.client.get(self.tournament + "/export.csv").data.decode("utf-8-sig")
        winner, = (r for r in csv.DictReader(StringIO(ranking), delimiter=";") if r["nr"] == "1")
        self.assertEqual((winner["nachname"], winner["vorname"], winner["punkte"], winner["soli"]), ("Huber", "Berta", "15", "4"))
        for url in (self.tournament + "/standings", self.tournament + "/players/1"):
            text = html.fromstring(self.client.get(url).get_data(as_text=True)).text_content()
            self.assertIn("Huber", text)
            self.assertNotIn("Anna", text)
        sheet = self.client.get(self.tournament + "/rounds/1/tablesheets-docx/1?ausgabe=helfer")
        with ZipFile(BytesIO(sheet.data)) as archive:
            xml = etree.fromstring(archive.read("word/document.xml"))
        texts = xml.xpath("//w:t/text()", namespaces={"w": xml.nsmap["w"]})
        self.assertIn("Huber, Berta · Testort", texts)
        self.assertNotIn("Probe, Anna · Testort", texts)
        self.client.post(self.tournament + "/close")
        self.assertEqual(self.query("SELECT id,participation_count FROM addresses WHERE id IN (1,5) ORDER BY id"),
                         [(1, 0), (5, 1)])
        closed = self.snapshot()
        self.client.post(path, data={**fields, "new_address_id":"1"})
        self.assertEqual(self.snapshot(), closed)

    def test_correction_requires_free_target_reason_current_form_and_audit(self):
        path, fields = self.prepare_correction()
        with closing(db.connect()) as con, con:
            con.execute("""INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort,status)
                           VALUES(1,'Gesperrt','Test','Ort','gesperrt')""")
        before = self.snapshot()
        for bad in ({"new_address_id": "3"}, {"new_address_id": "1"}, {"new_address_id": "6"},
                    {"new_address_id": "999"}, {"reason": "  "}, {"expected_state": ""}):
            with self.subTest(bad=bad):
                self.client.post(path, data={**fields, **bad})
                self.assertEqual(self.snapshot(), before)
        self.client.post("/tournaments/2/participants/1/correct", data=fields)
        self.assertEqual(self.snapshot(), before)
        with closing(db.connect()) as con, con:
            con.execute("""CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log
                           WHEN NEW.action='correct_identity' BEGIN SELECT RAISE(ABORT, 'Test'); END""")
        response = self.client.post(path, data=fields)
        self.assertIn("nicht gemeinsam gespeichert", response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_withdraw_after_scoring_keeps_ranking_and_excludes_from_all_draws(self):
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET max_participants=4 WHERE id=1")
        self.draw()
        values = self.form()
        values.update(points_1="30", points_2="-10", points_3="-10", points_4="-10", soli_1="3")
        self.client.post(self.table, data=values)
        previous = self.snapshot()
        roster = html.fromstring(self.client.get(self.participants).get_data(as_text=True))
        form, = roster.get_element_by_id("tp1").xpath(".//form[contains(@action,'/withdraw')]")
        for _ in range(2):
            self.client.post(form.action, data=dict(form.fields))
        roster = html.fromstring(self.client.get(self.participants).get_data(as_text=True))
        self.assertIn("Abgemeldet", roster.get_element_by_id("tp1").text_content())
        self.assertFalse(roster.get_element_by_id("tp1").xpath(".//form[contains(@action,'/withdraw')]"))
        self.assertEqual(self.query("SELECT tp_id FROM audit_log WHERE action='withdraw'"), [(1,)])
        second = self.tournament + "/rounds/2"
        blocked = self.client.post(second + "/draw", follow_redirects=True)
        self.assertIn("3 aktive Teilnehmer", blocked.get_data(as_text=True))
        self.assertEqual(self.query("SELECT round_no FROM tournament_rounds"), [(1,)])

        self.add_player("Ersatz")
        self.assertEqual(self.query("SELECT player_no FROM tournament_participants ORDER BY id"), [(1,), (2,), (3,), (4,), (5,)])
        for mode in ("random", "seeding"):
            with self.subTest(mode=mode):
                self.assertEqual(self.client.post(second + "/draw", data={"draw_mode": mode}).location, second)
                self.assertEqual(self.query("SELECT tp_id FROM tournament_seats WHERE round_no=2 ORDER BY tp_id"),
                                 [(2,), (3,), (4,), (5,)])
                self.assertNotIn("Reserve (nicht gesetzt)", self.client.get(second).get_data(as_text=True))
        self.assertEqual(self.query("SELECT draw_attempt FROM tournament_rounds WHERE round_no=2"), [(2,)])
        self.assertEqual(self.query("SELECT * FROM tournament_seats WHERE round_no=1 ORDER BY id"), previous[3])
        self.assertEqual(self.query("SELECT * FROM tournament_scores ORDER BY id"), previous[4])
        standings = html.fromstring(self.client.get(self.tournament + "/standings").get_data(as_text=True))
        row, = standings.xpath("//a[@href='/tournaments/1/players/1']/ancestor::tr")
        self.assertEqual(row.xpath(".//span[contains(@class,'sk-points')]/text()"), ["30"])
        self.assertIn("S 3", row.text_content())
        csv_data = self.client.get(self.tournament + "/export.csv").data.decode("utf-8-sig")
        player, = (r for r in csv.DictReader(StringIO(csv_data), delimiter=";") if r["nr"] == "1")
        self.assertEqual((player["vorname"], player["punkte"], player["soli"]), ("Anna", "30", "3"))

    def test_withdraw_preserves_drawn_table_and_rejects_stale_or_closed_changes(self):
        self.draw()
        old_table = self.form()
        before = self.snapshot()
        url = self.participants + "/1/withdraw"
        for target, fields in [(url, {}), (url, {"expected_address_id": "2"}),
                               (self.participants + "/999/withdraw", {"expected_address_id": "1"})]:
            self.client.post(target, data=fields)
        self.assertEqual(self.snapshot(), before)
        self.client.post(url, data={"expected_address_id": "1"})
        for source, target in [(1, 2), (2, 1)]:
            self.client.post(self.participants + "/swap", data={"tp_id": source, "new_address_id": target})
        hits = self.client.get(self.participants + "/swap-search?q=Probe").get_json()["items"]
        self.assertNotIn(1, [hit["id"] for hit in hits])
        self.assertEqual(self.query("SELECT address_id FROM tournament_participants ORDER BY id"), [(1,), (2,), (3,), (4,)])
        self.client.post(self.tournament + "/rounds/1/draw")
        self.assertEqual(self.snapshot()[2:5], before[2:5])
        self.client.post(self.table, data=old_table)
        self.assertEqual(self.query("SELECT COUNT(*) FROM tournament_scores"), [(4,)])
        self.client.post(self.tournament + "/close")
        self.assertEqual(self.query("SELECT closed_at IS NOT NULL FROM tournaments"), [(1,)])
        closed = self.snapshot()
        self.client.post(self.participants + "/2/withdraw", data={"expected_address_id": "2"})
        self.assertEqual(self.snapshot(), closed)

    def test_seeding_fills_two_tables_by_active_rank_and_withdrawal_is_local(self):
        for name in ("Eva", "Finn", "Gabi", "Hans"):
            self.add_player(name)
        self.client.post("/tournaments/new", data={
            "title": "Anderes Turnier", "event_date": "2026-11-13", "start_time": "18:00",
        })
        self.draw()
        for table in (1, 2):
            url = f"{self.tournament}/rounds/1/results/{table}"
            form = html.fromstring(self.client.get(url).get_data(as_text=True)).forms[0]
            data = dict(form.fields)
            fields = form.xpath(".//input[@data-skt='points']/@name")
            data.update(zip(fields, ("30", "10", "-10", "-30")))
            self.client.post(url, data=data)
        ranked = self.query("SELECT tp_id FROM tournament_scores ORDER BY points DESC, tp_id")
        winner = ranked[0][0]
        self.client.post(f"/tournaments/2/participants/add/{winner}")
        self.client.post(f"{self.participants}/{winner}/withdraw", data={"expected_address_id": str(winner)})
        second = self.tournament + "/rounds/2"
        blocked = self.client.post(second + "/draw", data={"draw_mode": "seeding"}, follow_redirects=True)
        self.assertIn("7 aktive Teilnehmer", blocked.get_data(as_text=True))
        self.assertEqual(self.query("SELECT COUNT(*) FROM tournament_rounds WHERE round_no=2"), [(0,)])
        self.add_player("Iris")
        replacement, = self.query("SELECT id FROM tournament_participants WHERE tournament_id=1 AND player_no=9")[0]
        self.client.post(second + "/draw", data={"draw_mode": "seeding"})
        # Nach zwei positiven Ergebnissen pro Tisch rückt die Ersatzperson mit null Punkten auf.
        expected_first = ({p for (p,) in ranked[:4]} - {winner}) | {replacement}
        self.assertEqual({p for (p,) in self.query("SELECT tp_id FROM tournament_seats WHERE round_no=2 AND table_no=1")},
                         expected_first)
        self.assertEqual({p for (p,) in self.query("SELECT tp_id FROM tournament_seats WHERE round_no=2 AND table_no=2")},
                         {p for (p,) in ranked[4:]})
        self.client.post(f"/tournaments/2/participants/{winner}/withdraw", data={"expected_address_id": str(winner)})
        self.assertEqual(self.query("SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=2 AND withdrawn_at IS NULL"), [(1,)])

    def test_reprints_and_standings_use_corrected_address_after_close(self):
        self.draw()
        self.client.post(self.table, data=self.form())
        self.client.post(self.tournament + "/close")
        self.assertEqual(self.query("SELECT closed_at IS NOT NULL FROM tournaments"), [(1,)])
        scores = self.query("SELECT * FROM tournament_scores ORDER BY id")
        edit = "/addresses/1/edit"
        fields = dict(html.fromstring(self.client.get(edit).get_data(as_text=True)).forms[0].fields)
        fields.update(nachname="Kurz", vorname="Mia", wohnort="Au")
        self.assertEqual(self.client.post(edit, data=fields).status_code, 302)
        standing = html.fromstring(self.client.get(self.tournament + "/standings").get_data(as_text=True))
        self.assertIn("Kurz", standing.text_content())
        self.assertNotIn("Anna", standing.text_content())
        export = self.client.get(self.tournament + "/export.csv").data.decode("utf-8-sig")
        person, = (row for row in csv.DictReader(StringIO(export), delimiter=";") if row["nr"] == "1")
        self.assertEqual((person["nachname"], person["vorname"], person["wohnort"]), ("Kurz", "Mia", "Au"))
        docx = self.client.get(self.tournament + "/rounds/1/tablesheets-docx/1")
        self.assertEqual(docx.status_code, 200)
        with ZipFile(BytesIO(docx.data)) as archive:
            xml = etree.fromstring(archive.read("word/document.xml"))
        labels = xml.xpath("//w:t/text()", namespaces={"w": xml.nsmap["w"]})
        self.assertIn("Kurz, Mia · Au", labels)
        self.assertNotIn("Probe, Anna · Testort", labels)
        self.assertEqual(self.query("SELECT * FROM tournament_scores ORDER BY id"), scores)

    def test_delete_removes_only_its_own_log_and_preserves_closed_tournament(self):
        self.client.post("/tournaments/new", data={
            "title": "Zweites", "event_date": "2026-11-13", "start_time": "19:00",
        })
        self.client.post("/tournaments/2/participants/quickadd", data={
            "nachname": "Zwei", "vorname": "Ada", "wohnort": "Ort",
        })
        self.draw()
        self.client.post(self.table, data=self.form())
        self.client.post(self.tournament + "/close")
        self.assertEqual(self.query("SELECT closed_at IS NOT NULL FROM tournaments WHERE id=1"), [(1,)])
        before = self.snapshot()
        self.client.post(self.tournament + "/delete")
        self.assertEqual(self.snapshot(), before)
        keep = self.query("SELECT * FROM audit_log WHERE tournament_id=1 ORDER BY id")
        self.assertEqual(len(keep), 4)
        self.assertEqual(self.query("SELECT COUNT(*) FROM audit_log WHERE tournament_id=2"), [(1,)])
        self.client.post("/tournaments/2/delete")
        self.assertEqual(self.query("SELECT id FROM tournaments"), [(1,)])
        self.assertEqual(self.query("SELECT * FROM audit_log ORDER BY id"), keep)
        self.assertEqual(self.query("SELECT * FROM addresses ORDER BY id"), before[0])

    def test_unused_signup_and_exchange_before_scoring(self):
        self.draw()
        before = self.snapshot()
        self.client.post(self.participants + "/1/remove", data={"renumber": "1"})
        self.assertEqual(self.snapshot(), before)
        # All four addresses are registered, so the exchange needs the temporary address.
        old_form = self.form()
        self.client.post(self.participants + "/swap", data={"tp_id": 1, "new_address_id": 2})
        self.assertEqual(self.query("SELECT address_id FROM tournament_participants ORDER BY id"), [(2,), (1,), (3,), (4,)])
        self.assertEqual(len(self.query("SELECT id FROM addresses")), 4)
        self.assertEqual(self.snapshot()[3], before[3])
        self.client.post(self.table, data=old_form)
        self.assertEqual(self.query("SELECT points FROM tournament_scores"), [])
        self.client.post(self.table, data=self.form())
        self.assertEqual(len(self.query("SELECT points FROM tournament_scores")), 4)
        self.add_player("Reserve")
        page = html.fromstring(self.client.get(self.participants).get_data(as_text=True))
        form = page.get_element_by_id("rmForm5")
        self.client.post(form.get("action"), data=dict(form.fields))
        self.assertEqual(self.query("SELECT COUNT(*) FROM tournament_participants"), [(4,)])

    def test_removal_exchange_and_replacement_preserve_scored_players(self):
        self.draw()
        self.client.post(self.table, data=self.form())
        self.add_player("Reserve")
        self.add_player("Ersatz")
        self.client.post(self.participants + "/6/remove")
        before = self.snapshot()
        self.client.post(self.participants + "/1/remove")
        for source, target in ((1, 6), (1, 2), (5, 1)):
            self.client.post(self.participants + "/swap", data={"tp_id": source, "new_address_id": target})
        self.assertEqual(self.snapshot(), before)
        page = html.fromstring(self.client.get(self.participants).get_data(as_text=True))
        self.assertEqual(len(page.xpath('//*[@id="tp1"]//button[@disabled]')), 2)
        self.assertEqual(len(page.xpath('//*[@id="tp5"]//button[@disabled]')), 0)
        hits = self.client.get(self.participants + "/swap-search?q=Probe").get_json()["items"]
        self.assertEqual({h["id"] for h in hits}, {5, 6})
        self.client.post(self.participants + "/swap", data={"tp_id": 5, "new_address_id": 6})
        self.assertEqual(self.query("SELECT address_id FROM tournament_participants WHERE id=5"), [(6,)])
        self.client.post(self.participants + "/5/remove", data={"expected_address_id": "5"})
        self.assertEqual(self.query("SELECT address_id FROM tournament_participants WHERE id=5"), [(6,)])

    def test_simultaneous_score_and_exchange_keep_the_right_players(self):
        self.draw()
        # Die Tischmaske vor dem Tausch in der Sitzung des erfassenden Spielers öffnen.
        values = self.form()
        start = Barrier(2)

        def send(path, data):
            client = self.client if path == self.table else form_client(self.app)
            start.wait(timeout=5)
            return client.post(path, data=data).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            score = pool.submit(send, self.table, values)
            swap = pool.submit(send, self.participants + "/swap", {"tp_id": 1, "new_address_id": 2})
            self.assertEqual((score.result(), swap.result()), (302, 302))
        people = self.query("SELECT address_id FROM tournament_participants ORDER BY id")
        scores = self.query("SELECT COUNT(*) FROM tournament_scores")[0][0]
        self.assertIn((people, scores), [
            ([(1,), (2,), (3,), (4,)], 4),
            ([(2,), (1,), (3,), (4,)], 0),
        ])

    def test_invalid_soli_reject_the_entire_table(self):
        self.draw()
        values = self.form()
        for invalid in ("-1", "1.5", "Text"):
            values["soli_1"] = invalid
            self.client.post(self.table, data=values)
            self.assertEqual(self.query("SELECT points FROM tournament_scores"), [])
        values["soli_1"] = "3"
        self.client.post(self.table, data=values)
        self.assertEqual(self.query("SELECT soli FROM tournament_scores ORDER BY tp_id"), [(3,), (0,), (0,), (0,)])

    def test_overview_and_close_reject_damaged_tables_without_changing_history(self):
        self.draw()
        self.client.post(self.table, data=self.form())
        cases = [
            ("DELETE FROM tournament_participants WHERE id=1", "vier Sitzplätze"),
            ("UPDATE tournament_seats SET seat='E' WHERE seat='D'", "vier Sitzplätze"),
            ("DELETE FROM tournament_scores WHERE tp_id=1", "3 von 4"),
            ("UPDATE tournament_scores SET points=points+1 WHERE tp_id=1", "Punktesumme beträgt 1"),
            ("UPDATE tournament_scores SET soli=-1 WHERE tp_id=1", "Soli müssen"),
            ("UPDATE tournament_scores SET soli='x' WHERE tp_id=1", "Soli müssen"),
            ("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,2)", "Sitzplan fehlt"),
        ]
        broken_links = [
            ("UPDATE tournament_scores SET table_no=2 WHERE tp_id=1", "keinen passenden Sitzplatz"),
            ("DELETE FROM tournament_rounds", "keiner ausgelosten Runde"),
        ]
        with closing(db.connect()) as con, closing(sqlite3.connect(":memory:")) as baseline:
            con.backup(baseline)
            for sql, reason in cases + broken_links:
                with self.subTest(sql=sql):
                    baseline.backup(con)
                    if (sql, reason) in broken_links:
                        # Beschädigten Altbestand ausschließlich in der Testverbindung nachstellen.
                        con.execute("PRAGMA foreign_keys=OFF")
                    with con:
                        con.execute(sql)
                    con.execute("PRAGMA foreign_keys=ON")
                    detail = self.client.get(self.tournament).get_data(as_text=True)
                    self.assertIn(reason, detail)
                    self.assertTrue(html.fromstring(detail).xpath("//form[@action='/tournaments/1/close']//button[@disabled]"))
                    before = self.snapshot()
                    self.client.post(self.tournament + "/close")
                    self.assertEqual(self.snapshot(), before)
            baseline.backup(con)
        detail = html.fromstring(self.client.get(self.tournament).get_data(as_text=True))
        self.assertTrue(detail.xpath("//form[@action='/tournaments/1/close']//button[not(@disabled)]"))
        self.client.post(self.tournament + "/close")
        self.assertEqual(self.query("SELECT closed_at IS NOT NULL FROM tournaments"), [(1,)])
        self.assertEqual(self.query("SELECT DISTINCT participation_count FROM addresses"), [(1,)])
        before = self.snapshot()
        self.client.post(self.tournament + "/close")
        self.assertEqual(self.snapshot(), before)
