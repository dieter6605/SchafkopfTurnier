import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from lxml import html

from app import db
from app.web import create_app
from tests.form_client import form_client


class OverviewTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        app = create_app(db_path=base / "test.sqlite3", backup_dir=base / "backups")
        app.testing = True
        self.client = form_client(app)

    def tournament(self, title="Herbstturnier", days=0):
        response = self.client.post("/tournaments/new", data={
            "title": title, "event_date": (date.today() + timedelta(days=days)).isoformat(),
            "start_time": "19:00", "location": "Vereinsheim",
        })
        self.assertEqual(response.status_code, 302)

    def register(self, count):
        for number in range(1, count + 1):
            self.client.post("/tournaments/1/participants/quickadd", data={
                "vorname": f"Probe {number}", "nachname": "Spieler", "wohnort": "Testort",
            })

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.get_data(as_text=True))

    def draw(self, rn):
        response = self.client.post(f"/tournaments/1/rounds/{rn}/draw", data={"expected_round_id": "0"})
        self.assertEqual(response.status_code, 302)

    def save_table(self, path, *, go_next=False):
        form = self.page(path).get_element_by_id("skResultsForm")
        data = dict(form.fields)
        # Ein normaler Klick auf Speichern sendet den zweiten Submit-Button nicht mit.
        data.pop("go_next", None)
        for field, values in (("points", [30, -10, -10, -10]), ("soli", [2, 0, 1, 0])):
            names = form.xpath(".//input[@data-skt=$field]/@name", field=field)
            self.assertEqual(len(names), 4)
            data.update(zip(names, map(str, values)))
        if go_next:
            data["go_next"] = "1"
        return self.client.post(form.action, data=data)

    def button(self, page, label):
        links = page.xpath("//a[starts-with(normalize-space(.), $label)]/@href", label=label)
        self.assertTrue(links, label)
        return links[0]

    def test_last_open_table_returns_to_live_overview(self):
        self.tournament()
        self.register(16)
        self.draw(1)
        for tn in (1, 2, 3, 4):
            self.save_table(f"/tournaments/1/rounds/1/results/{tn}")
        self.draw(2)
        for tn in (1, 2, 4):
            self.save_table(f"/tournaments/1/rounds/2/results/{tn}")
        detail = self.page("/tournaments/1")
        self.assertIn("3 fertig · 1 offen", detail.text_content())
        path = self.button(detail, "Tisch 3 erfassen")
        self.assertEqual(path, self.button(self.page("/"), "Tisch 3 erfassen"))
        response = self.save_table(path)
        self.assertEqual(response.location, "/tournaments/1")
        detail = self.page(response.location)
        self.assertIn("4 fertig · 0 offen", detail.text_content())
        self.assertIn("Runde 2 ist vollständig", detail.text_content())
        next_round = self.button(detail, "Runde 3 vorbereiten")
        self.assertEqual(next_round, self.button(self.page("/"), "Runde 3 vorbereiten"))
        self.page(next_round)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_rounds").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_scores WHERE round_no=2").fetchone()[0], 16)

    def test_invalid_table_has_priority_and_closed_tournament_shows_provisional_standings(self):
        self.tournament()
        self.register(8)
        self.draw(1)
        dest = self.save_table("/tournaments/1/rounds/1/results/1?source=overview", go_next=True)
        self.assertEqual(dest.location, "/tournaments/1/rounds/1/results/2")
        self.draw(2)
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournament_scores SET points=31 WHERE points=30")
        page = self.page("/tournaments/1")
        self.assertIn("Wertung in Runde 1 prüfen", page.text_content())
        self.assertIn("0 fertig · 1 offen · 1 zu prüfen", page.text_content())
        self.assertIn("rounds/1/results/1?", self.button(page, "Tisch 1 prüfen"))
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET closed_at='2026-11-13 21:00'")
        page = self.page("/tournaments/1")
        self.assertIn("Die Gesamtwertung ist vorläufig.", page.text_content())
        self.assertIn("Runde 1, Tisch 1", page.text_content())
        self.page(self.button(page, "Gesamtwertung öffnen"))

    def test_start_selection_empty_and_filtered_lists_and_navigation(self):
        self.assertIn("Erstes Turnier anlegen", self.page("/").text_content())
        self.tournament(days=-1)
        self.register(4)
        self.draw(1)
        self.tournament("Adventsturnier", days=10)
        self.tournament("Sommerturnier", days=-90)
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET closed_at='2026-08-01' WHERE id=3")
        home = self.page("/")
        self.assertEqual(home.xpath("//section[contains(@class,'sk-feature')]/h2/text()"), ["Herbstturnier"])
        self.assertEqual(home.xpath("//a[contains(@class,'sk-event')]/span/strong/text()"),
                         ["Adventsturnier", "Sommerturnier"])
        for query, expected in (("status=offen&q=ADVENT", ["Adventsturnier"]),
                                ("status=abgeschlossen", ["Sommerturnier"]), ("q=kein-treffer", [])):
            self.assertEqual(self.page("/tournaments?" + query).xpath("//tr[@data-open-url]/td[2]/strong/text()"), expected)
        backup = self.page(self.button(home, "Datensicherung öffnen"))
        self.assertTrue(backup.xpath("//form[@action='/backup']"))
        nav = self.page("/tournaments/1").xpath("//nav[@aria-label='Turniernavigation']/a")
        self.assertEqual([a.text for a in nav], ["Übersicht", "Teilnehmer", "Runden & Ergebnisse", "Gesamtwertung", "Druck & Export"])
        for a in nav:
            page = self.page(a.get("href"))
            self.assertEqual(page.xpath("//nav[@aria-label='Turniernavigation']/a[@aria-current]/text()"), [a.text])
        self.assertTrue(self.page(nav[-1].get("href")).xpath("//details[@open]//a[contains(@href,'docx')]"))
