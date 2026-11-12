import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from lxml import html

from app import db
from app.web import create_app


class RoundDrawTests(unittest.TestCase):
    round_url = "/tournaments/1/rounds/1"

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.app = create_app(db_path=root / "test.sqlite3", backup_dir=root / "backups")
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.post("/tournaments/new", data={
            "title": "Rundenprobe", "event_date": "2026-11-12", "start_time": "18:00",
        })
        for name in ("Anton", "Berta", "Chris", "Doris"):
            self.client.post("/tournaments/1/participants/quickadd", data={
                "vorname": name, "nachname": "Test", "wohnort": "Testort",
            })
        self.draw("0")

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.get_data(as_text=True))

    def draw(self, expected_id):
        response = self.client.post(self.round_url + "/draw", data={"expected_round_id": expected_id})
        self.assertEqual(response.location, self.round_url)

    def result_form(self):
        form = self.page(self.round_url + "/results/1").forms[0]
        payload = dict(form.fields)
        points = form.xpath(".//input[@data-skt='points']/@name")
        self.assertEqual(len(points), 4)
        payload.update(zip(points, ("30", "-10", "-10", "-10")))
        return payload

    def round_state(self):
        with closing(db.connect()) as con:
            return {
                table: [tuple(row) for row in con.execute(
                    f"SELECT * FROM {table} WHERE tournament_id=1 AND round_no=1 ORDER BY id"
                )]
                for table in ("tournament_rounds", "tournament_seats", "tournament_scores")
            }

    def test_scored_round_stays_intact_when_drawing_next(self):
        payload = self.result_form()
        self.client.post(self.round_url + "/results/1", data=payload)
        before = self.round_state()
        self.assertEqual(len(before["tournament_scores"]), 4)
        page = self.page(self.round_url)
        self.assertIn("disabled", page.get_element_by_id("btnRedraw").attrib)
        self.assertNotIn("disabled", page.get_element_by_id("btnDrawNext").attrib)
        self.draw(payload["expected_round_id"])
        form = page.get_element_by_id("formDrawNext")
        self.assertEqual(form.get("data-confirm"), "Runde 2 auslosen?")
        data = dict(form.fields)
        self.assertEqual(data["expected_round_id"], "0")
        data["draw_mode"] = "seeding"
        response = self.client.post(form.get("action"), data=data)
        self.assertEqual(response.location, "/tournaments/1/rounds/2")
        self.assertEqual(self.page(self.round_url).get_element_by_id("btnNextRound").get("href"), response.location)
        self.assertEqual(self.round_state(), before)

    def test_single_zero_score_blocks_even_an_unversioned_redraw(self):
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO tournament_scores(tournament_id, round_no, table_no, tp_id, points, soli) "
                        "SELECT tournament_id, round_no, table_no, tp_id, 0, 0 FROM tournament_seats LIMIT 1")
        before = self.round_state()
        self.client.post(self.round_url + "/draw")
        self.assertEqual(self.round_state(), before)
        self.assertIn("disabled", self.page(self.round_url).get_element_by_id("btnRedraw").attrib)

    def test_superseded_forms_cannot_replace_a_draw_or_save_scores(self):
        old = self.result_form()
        self.draw(old["expected_round_id"])
        current = self.result_form()
        self.assertNotEqual(current["expected_round_id"], old["expected_round_id"])
        before = self.round_state()
        self.draw(old["expected_round_id"])
        self.draw("0")
        self.client.post(self.round_url + "/results/1", data=old)
        self.assertEqual(self.round_state(), before)
        self.client.post(self.round_url + "/results/1", data=current)
        self.assertEqual(len(self.round_state()["tournament_scores"]), 4)

    def test_two_simultaneous_requests_only_create_one_draw(self):
        barrier = Barrier(2)
        path = "/tournaments/1/rounds/2"

        def request_draw():
            client = self.app.test_client()
            barrier.wait(timeout=5)
            return client.post(path + "/draw", data={"expected_round_id": "0", "draw_mode": "seeding"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(request_draw) for _ in range(2)]
            for attempt in attempts:
                self.assertEqual(attempt.result().location, path)
        with closing(db.connect()) as con:
            attempts = con.execute("SELECT draw_attempt FROM tournament_rounds WHERE round_no=2").fetchall()
            self.assertEqual([row[0] for row in attempts], [1])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_seats WHERE round_no=2").fetchone()[0], 4)
