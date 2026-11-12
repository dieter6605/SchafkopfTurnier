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


class ResultConflictTests(unittest.TestCase):
    first_table = "/tournaments/1/rounds/1/results/1"
    second_table = "/tournaments/1/rounds/1/results/2"

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.app = create_app(db_path=base / "test.sqlite3", backup_dir=base / "backups")
        self.app.testing = True
        self.first = form_client(self.app)
        self.second = form_client(self.app)
        self.first.post("/tournaments/new", data={
            "title": "Tischprobe", "event_date": "2026-11-12", "start_time": "18:00",
        })
        for name in ("Anna", "Berta", "Clara", "Dora", "Else", "Fritz", "Georg", "Hans"):
            self.first.post("/tournaments/1/participants/quickadd", data={
                "vorname": name, "nachname": "Test", "wohnort": "Testort",
            })
        self.first.post("/tournaments/1/rounds/1/draw", data={"expected_round_id": "0"})

    @staticmethod
    def form(response):
        page = html.fromstring(response.get_data(as_text=True))
        return page.get_element_by_id("skResultsForm")

    @staticmethod
    def values(form, points=(30, -10, -10, -10), soli=(0, 0, 0, 0)):
        values = dict(form.fields)
        for field, numbers in (("points", points), ("soli", soli)):
            names = form.xpath(f".//input[@data-skt='{field}']/@name")
            if len(names) != 4:
                raise AssertionError("Vom Tischformular werden vier Sitze erwartet.")
            values.update(zip(names, map(str, numbers)))
        return values

    def stored(self):
        with closing(db.connect()) as con:
            return list(map(tuple, con.execute("""
                SELECT sc.points, sc.soli FROM tournament_seats s
                JOIN tournament_scores sc ON sc.tournament_id=s.tournament_id
                    AND sc.round_no=s.round_no AND sc.tp_id=s.tp_id
                WHERE s.tournament_id=1 AND s.round_no=1 AND s.table_no=1 ORDER BY s.seat
            """)))

    def test_first_entry_conflict_shows_both_values_and_allows_reviewed_correction(self):
        form_a = self.form(self.first.get(self.first_table))
        form_b = self.form(self.second.get(self.first_table))
        self.first.post(self.first_table, data=self.values(form_a))
        alternate = (60, -20, -20, -20)
        stale = self.values(form_b, alternate, (1, 0, 0, 0))
        response = self.second.post(self.first_table, data={**stale, "go_next": "1"})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(response.location)
        self.assertEqual(self.stored(), [(30, 0), (-10, 0), (-10, 0), (-10, 0)])
        review = self.form(response)
        self.assertEqual(review.xpath(".//input[@data-skt='points']/@value"), ["30", "-10", "-10", "-10"])
        self.assertEqual(review.xpath(".//tbody/tr/td[last()]/text()"), ["60 / 1", "-20 / 0", "-20 / 0", "-20 / 0"])
        self.assertNotEqual(review.fields["expected_score_state"], stale["expected_score_state"])
        confirmed = self.values(review, alternate, (1, 0, 0, 0))
        response = self.second.post(self.first_table, data={**confirmed, "go_next": "1"})
        self.assertEqual(response.location, self.second_table)
        self.assertEqual(self.stored(), [(60, 1), (-20, 0), (-20, 0), (-20, 0)])

    def test_soli_change_is_detected_and_other_tables_are_independent(self):
        initial = self.values(self.form(self.first.get(self.first_table)))
        initial.pop("expected_score_state")  # Erstmalige Erfassung mit einem älteren Formular.
        self.assertEqual(self.first.post(self.first_table, data=initial).status_code, 302)
        old = self.form(self.second.get(self.first_table))
        form_b = self.form(self.second.get(self.second_table))
        correction = self.form(self.first.get(self.first_table))
        self.first.post(self.first_table, data=self.values(correction, soli=(1, 0, 0, 0)))
        self.assertEqual(self.second.post(self.first_table, data=self.values(old)).status_code, 409)
        self.assertEqual(self.second.post(self.second_table, data=self.values(form_b)).status_code, 302)
        self.assertEqual(self.stored(), [(30, 1), (-10, 0), (-10, 0), (-10, 0)])

    def test_legacy_partial_zero_result_cannot_be_lost(self):
        form = self.form(self.first.get(self.first_table))
        with closing(db.connect()) as con, con:
            con.execute("""INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id,points,soli)
                SELECT tournament_id,round_no,table_no,tp_id,0,0 FROM tournament_seats
                WHERE tournament_id=1 AND round_no=1 AND table_no=1 AND seat='A'""")
        stale = self.values(form)
        self.assertEqual(self.first.post(self.first_table, data=stale).status_code, 409)
        stale.pop("expected_score_state")
        self.assertEqual(self.first.post(self.first_table, data=stale).status_code, 409)
        self.assertEqual(self.stored(), [(0, 0)])
        current = self.form(self.first.get(self.first_table))
        self.assertEqual(self.first.post(self.first_table, data=self.values(current)).status_code, 302)
        self.assertEqual(len(self.stored()), 4)

    def test_simultaneous_corrections_only_commit_one_whole_table(self):
        self.first.post(self.first_table, data=self.values(self.form(self.first.get(self.first_table))))
        barrier = Barrier(2)
        corrections = [((60, -20, -20, -20), (1, 2, 3, 4)), ((-15, 5, 5, 5), (4, 3, 2, 1))]

        def save(points, soli):
            client = form_client(self.app)
            form = self.form(client.get(self.first_table))
            barrier.wait(timeout=5)
            return client.post(self.first_table, data=self.values(form, points, soli)).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(save, *change) for change in corrections]
            statuses = [attempt.result() for attempt in attempts]
        self.assertEqual(sorted(statuses), [302, 409])
        self.assertEqual(self.stored(), list(zip(*corrections[statuses.index(302)])))
