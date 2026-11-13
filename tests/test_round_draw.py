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


class RoundDrawTests(unittest.TestCase):
    round_url = "/tournaments/1/rounds/1"

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.app = create_app(db_path=root / "test.sqlite3", backup_dir=root / "backups")
        self.app.testing = True
        self.client = form_client(self.app)
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

    def test_table_link_and_seat_toggle_work_independently(self):
        result_url = self.round_url + "/results/1"
        for scored in (False, True):
            with self.subTest(scored=scored):
                if scored:
                    self.client.post(result_url, data=self.result_form())
                page = self.page(self.round_url)
                card = page.get_element_by_id("accTable_1_1")
                link, = card.xpath(".//a[@href=$url]", url=result_url)
                toggle, = card.xpath(".//button[@data-bs-toggle='collapse']")
                self.assertFalse(link.xpath("ancestor::button | descendant::button"))
                self.assertIsNone(link.get("data-bs-toggle"))
                panel_id = toggle.get("aria-controls")
                self.assertEqual(toggle.get("data-bs-target"), "#" + panel_id)
                self.assertEqual(toggle.get("aria-expanded"), "false" if scored else "true")
                self.assertEqual("show" in card.get_element_by_id(panel_id).classes, not scored)
                self.assertIn("Tisch 1", toggle.get("aria-label", ""))
                self.assertIsNotNone(self.page(link.get("href")).get_element_by_id("skResultsForm"))

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

    def test_partial_previous_round_blocks_seeding_from_both_views(self):
        second = "/tournaments/1/rounds/2"
        with closing(db.connect()) as con, con:
            con.execute("""INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id)
                           SELECT tournament_id,round_no,table_no,tp_id FROM tournament_seats LIMIT 1""")
        for url, form_id in [(self.round_url, "formDrawNext"), (second, "formDraw")]:
            page = self.page(url)
            select = page.get_element_by_id("drawMode2")
            self.assertEqual(select.get("form"), form_id)
            self.assertTrue(select.xpath("option[@value='seeding' and @disabled]"))
            self.assertTrue(select.xpath("option[@value='random' and not(@disabled)]"))
            self.assertIn("Runde 1, Tisch 1: 1 von 4", page.get_element_by_id("seedingStatus2").text_content())
        before = self.round_state()
        result = self.client.post(second + "/draw", data={"draw_mode": "seeding"}, follow_redirects=True)
        self.assertIn("Setzliste für Runde 2 gesperrt.", result.get_data(as_text=True))
        self.assertEqual(self.round_state(), before)
        with closing(db.connect()) as con:
            self.assertIsNone(con.execute("SELECT id FROM tournament_rounds WHERE round_no=2").fetchone())
        self.client.post(second + "/draw", data={"draw_mode": "random"})
        self.assertTrue(self.page(second).xpath("//a[@id='btnPrevRound']"))
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_seats WHERE round_no=2").fetchone()[0], 4)

    def test_stale_seeding_form_cannot_replace_an_existing_plan(self):
        second = "/tournaments/1/rounds/2"
        self.client.post(self.round_url + "/results/1", data=self.result_form())
        self.client.post(second + "/draw", data={"draw_mode": "seeding"})
        page = self.page(second)
        self.assertTrue(page.xpath("//select[@form='formDraw']/option[@value='seeding' and not(@disabled)]"))
        self.assertTrue(page.xpath("//select[@form='formDrawNext']/option[@value='seeding' and @disabled]"))
        form = page.get_element_by_id("formDraw")
        values = dict(form.fields, draw_mode="seeding")
        with closing(db.connect()) as con, con:
            before = list(con.execute("SELECT * FROM tournament_seats WHERE round_no=2 ORDER BY id"))
            con.execute("UPDATE tournament_scores SET points=points+1 WHERE tp_id=1")
        result = self.client.post(form.get("action"), data=values, follow_redirects=True)
        self.assertIn("Punktesumme beträgt 1", result.get_data(as_text=True))
        with closing(db.connect()) as con:
            after = list(con.execute("SELECT * FROM tournament_seats WHERE round_no=2 ORDER BY id"))
            self.assertEqual(after, before)
            self.assertEqual(str(con.execute("SELECT id FROM tournament_rounds WHERE round_no=2").fetchone()[0]),
                             values["expected_round_id"])

    def test_seeding_uses_every_round_in_the_overall_standings(self):
        second = "/tournaments/1/rounds/2"
        third = "/tournaments/1/rounds/3"
        self.client.post(second + "/draw", data={"draw_mode": "random"})
        with closing(db.connect()) as con, con:
            con.execute("""INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id)
                           SELECT tournament_id,round_no,table_no,tp_id FROM tournament_seats WHERE round_no=2""")
        self.assertIn("Runde 1, Tisch 1", self.page(second).get_element_by_id("seedingStatus3").text_content())
        self.client.post(third + "/draw", data={"draw_mode": "seeding"})
        with closing(db.connect()) as con:
            self.assertIsNone(con.execute("SELECT id FROM tournament_rounds WHERE round_no=3").fetchone())
        self.client.post(self.round_url + "/results/1", data=self.result_form())
        self.assertIn("Runden 1 bis 2 vollständig gewertet", self.page(second).get_element_by_id("seedingStatus3").text_content())
        self.client.post(third + "/draw", data={"draw_mode": "seeding"})
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_seats WHERE round_no=3").fetchone()[0], 4)

    def test_late_signups_do_not_add_missing_scores_to_a_finished_round(self):
        self.client.post(self.round_url + "/results/1", data=self.result_form())
        for first in ("Eva", "Finn", "Gabi", "Hans"):
            self.client.post("/tournaments/1/participants/quickadd", data={
                "vorname": first, "nachname": "Reserve", "wohnort": "Testort",
            })
        select = self.page(self.round_url).get_element_by_id("drawMode2")
        self.assertTrue(select.xpath("option[@value='seeding' and not(@disabled)]"))
        self.client.post("/tournaments/1/rounds/2/draw", data={"draw_mode": "seeding"})
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_seats WHERE round_no=2").fetchone()[0], 8)
            # Die drei Spieler mit negativen Punkten folgen den Nachmeldungen mit null Punkten.
            table_one = set(r[0] for r in con.execute("SELECT tp_id FROM tournament_seats WHERE round_no=2 AND table_no=1"))
            winner = con.execute("SELECT tp_id FROM tournament_scores WHERE points=30").fetchone()[0]
            self.assertEqual(table_one, {winner, 5, 6, 7})

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
        self.client.post(self.round_url + "/results/1", data=self.result_form())
        barrier = Barrier(2)
        path = "/tournaments/1/rounds/2"

        def request_draw():
            client = form_client(self.app)
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
