import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from lxml import html

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
            ("UPDATE tournament_scores SET table_no=2 WHERE tp_id=1", "keinen passenden Sitzplatz"),
            ("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,2)", "Sitzplan fehlt"),
            ("DELETE FROM tournament_rounds", "keiner ausgelosten Runde"),
        ]
        with closing(db.connect()) as con, closing(sqlite3.connect(":memory:")) as baseline:
            con.backup(baseline)
            for sql, reason in cases:
                with self.subTest(sql=sql):
                    baseline.backup(con)
                    with con:
                        con.execute(sql)
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
