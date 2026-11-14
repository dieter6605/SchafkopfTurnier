import unittest
from contextlib import closing
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from zipfile import ZipFile

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

    def tournament(self, title="Herbstturnier", days=0, marker_suffix=""):
        event_date = date.today() + timedelta(days=days)
        response = self.client.post("/tournaments/new", data={
            "title": title, "event_date": event_date.isoformat(),
            "marker": event_date.strftime("%y%m%d") + marker_suffix if marker_suffix else "",
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

    def test_rounds_workspace_from_preparation_to_results_and_history(self):
        hub = "/tournaments/1?bereich=runden"
        self.tournament()
        self.register(4)
        start = self.page(hub)
        self.assertEqual(start.xpath("//h1/text()"), ["Runden & Ergebnisse"])
        preparation = self.page(self.button(start, "Zur Vorbereitung"))
        self.assertFalse(preparation.xpath("//nav[@aria-label='Ansichten für Runde 1']"))
        form = preparation.get_element_by_id("formDraw")
        self.client.post(form.action, data=dict(form.fields))
        plan = self.page(self.button(self.page(hub), "Sitzplan"))
        tabs, = plan.xpath("//nav[@aria-label='Ansichten für Runde 1']")
        self.assertEqual(tabs.xpath("a/text()"), ["Sitzplan", "Tischergebnisse", "Rundenwertung"])
        results = self.page(tabs.xpath("a/@href")[1])
        table = self.button(results, "Tisch 1")
        self.assertEqual(self.save_table(table).location, tabs.xpath("a/@href")[1])
        self.assertIn("1 fertig · 0 offen", self.page(hub).text_content())
        self.assertEqual(self.page(tabs.xpath("a/@href")[2]).xpath("//tbody/tr[1]/td[4]/text()"), ["30"])
        self.draw(2)
        previous, = self.page(hub).xpath("//section[.//h2='Frühere Runden']")
        self.assertEqual(previous.xpath(".//a/@href"), tabs.xpath("a/@href"))
        self.assertIn("1 fertig · 0 offen", previous.text_content())

    def test_rounds_workspace_reflects_draw_eligibility_and_closure(self):
        self.tournament()
        self.register(3)
        hub = "/tournaments/1?bereich=runden"
        page = self.page(hub)
        self.assertEqual(self.button(page, "Teilnehmer öffnen"), "/tournaments/1/participants")
        self.assertFalse(page.xpath("//form"))
        self.assertIn("disabled", self.page("/tournaments/1/rounds/1").get_element_by_id("btnRedraw").attrib)
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET closed_at='2026-11-13 20:00:00'")
        closed = self.page(hub)
        self.assertFalse(closed.xpath("//form"))
        self.assertEqual(self.button(closed, "Gesamtwertung öffnen"), "/tournaments/1/standings")

    def test_overall_standings_tracks_missing_and_saved_round_scores(self):
        self.tournament()
        base = "/tournaments/1"
        path = base + "/standings"
        self.assertIn("noch keine Teilnehmer angemeldet", self.page(path).text_content())
        self.register(4)
        initial = self.page(path)
        self.assertIn("Bisher wurden keine Ergebnisse erfasst.", initial.text_content())
        self.assertFalse(initial.xpath("//details"))

        self.draw(1)
        table = base + "/rounds/1/results/1"
        form = self.page(table).get_element_by_id("skResultsForm")
        values = dict(form.fields)
        for player in range(1, 5):
            values[f"points_{player}"] = values[f"soli_{player}"] = "0"
        self.client.post(table, data=values)
        self.draw(2)
        standings = self.page(path)
        self.assertEqual(standings.xpath("//nav[@aria-label='Turniernavigation']/a[@aria-current]/text()"), ["Gesamtwertung"])
        self.assertIn("Runde 2, Tisch 1", standings.text_content())
        self.assertIn("Zwischenstand", standings.text_content())
        player, = standings.xpath("//tr[@data-number='1']")
        self.assertEqual(player.xpath(".//details//li/span/text()"), ["0 Punkte · 0 Soli", "Kein Ergebnis"])
        self.assertEqual(player.xpath(".//a/@href"), [base + "/players/1"])

    def test_closed_overall_standings_shows_shared_places_and_keeps_player_links(self):
        self.tournament(marker_suffix="PROB")
        self.register(4)
        self.draw(1)
        table = "/tournaments/1/rounds/1/results/1"
        form = self.page(table).get_element_by_id("skResultsForm")
        values = dict(form.fields)
        for player, points, soli in ((1, 10, 2), (2, 10, 2), (3, -10, 1), (4, -10, 0)):
            values[f"points_{player}"] = str(points)
            values[f"soli_{player}"] = str(soli)
        self.client.post(table, data=values)
        self.client.post("/tournaments/1/close")

        page = self.page("/tournaments/1/standings")
        self.assertTrue(page.xpath("//span[text()='Endstand']"))
        rows = page.xpath("//tbody/tr")
        self.assertEqual([r.xpath("td[1]/span/text()")[0] for r in rows], ["#1", "#1", "#3", "#4"])
        self.assertEqual([r.get("data-number") for r in rows if r.xpath("td[1]/small[text()='geteilt']")], ["1", "2"])
        self.assertEqual([(r[3].text_content(), r[4].text_content()) for r in rows],
                         [("10", "2"), ("10", "2"), ("-10", "1"), ("-10", "0")])
        self.assertEqual([r.xpath(".//a/@href")[0] for r in rows],
                         [f"/tournaments/1/players/{n}" for n in range(1, 5)])

    def test_print_workspace_uses_selected_round_and_separates_audiences(self):
        self.tournament()
        self.register(8)
        self.draw(1)
        self.draw(2)
        path = "/tournaments/1?bereich=druck"
        current = self.page(path)
        form, = current.xpath("//form[.//select[@name='runde']]")
        self.assertEqual(dict(form.fields), {"bereich": "druck", "runde": "2"})
        self.assertEqual(form.xpath(".//option/@value"), ["2", "1"])
        previous = self.page(form.action + "?bereich=druck&runde=1")
        sheets, = previous.xpath("//section[.//h2='Tischblätter für Helfer']")
        internal, = previous.xpath("//details[summary='Turnierleitung · mit Kontaktdaten']")
        self.assertNotIn("open", internal.attrib)
        self.assertEqual(self.button(sheets, "Sitzplan für Runde 1"), "/tournaments/1/rounds/1")
        self.assertEqual(self.button(sheets, "Rundenwertung öffnen"), "/tournaments/1/rounds/1/results/standings")
        self.assertEqual(self.button(previous, "Druckansicht öffnen"), "/tournaments/1/standings")
        self.assertEqual(self.button(previous, "CSV herunterladen"), "/tournaments/1/export.csv")
        self.assertEqual(self.button(previous, "Helferpaket"), "/tournaments/1/helfer.zip")
        self.assertTrue(internal.xpath(".//a[@href='/tournaments/1/export.zip' and @data-confirm and @onclick]"))
        for scope, audience, prefix in ((sheets, "helfer", "Helfer"), (internal, "leitung", "Turnierleitung")):
            links = scope.xpath(".//a[contains(@href, 'docx')]/@href")
            self.assertEqual(links, [
                f"/tournaments/1/rounds/1/{part}?ausgabe={audience}"
                for part in ("tablesheets-docx-merged", "tablesheets-docx-zip", "tablesheets-docx/1", "tablesheets-docx/2")
            ])
            download = self.client.get(links[1])
            self.assertEqual(download.status_code, 200)
            self.assertIn(prefix, download.headers["Content-Disposition"])
            with ZipFile(BytesIO(download.data)) as archive:
                self.assertEqual(sorted(archive.namelist()), [
                    f"{prefix}_Herbstturnier_R01_T01.docx", f"{prefix}_Herbstturnier_R01_T02.docx",
                ])
        self.assertFalse(previous.xpath("//section//a[contains(@href,'ausgabe=leitung') or contains(@href,'/export.zip')]"))
        self.assertEqual(self.page(path + "&runde=99").xpath("//option[@selected]/@value"), ["2"])

    def test_print_workspace_before_draw_and_with_incomplete_seats(self):
        self.tournament()
        path = "/tournaments/1?bereich=druck"
        before = self.page(path)
        self.assertFalse(before.xpath("//select | //a[contains(@href,'docx')]"))
        self.assertIn("Nach der ersten Auslosung", before.text_content())
        self.assertEqual(self.button(before, "Helferpaket"), "/tournaments/1/helfer.zip")
        self.register(8)
        self.draw(1)
        with closing(db.connect()) as con, con:
            con.execute("DELETE FROM tournament_seats WHERE table_no=1 AND seat='D'")
            con.execute("UPDATE tournaments SET closed_at='2026-11-13'")
        partial = self.page(path)
        self.assertIn("Turnier abgeschlossen", partial.text_content())
        self.assertIn("Vorläufige Platzierungen.", partial.text_content())
        self.assertIn("Runde 1, Tisch 1", partial.text_content())
        self.assertIn("1 vollständiger Tisch", partial.text_content())
        self.assertFalse(partial.xpath("//a[contains(@href,'tablesheets-docx/1?')]"))
        self.assertTrue(partial.xpath("//a[contains(@href,'tablesheets-docx/2?ausgabe=helfer')]"))
        with closing(db.connect()) as con, con:
            con.execute("DELETE FROM tournament_seats WHERE table_no=2")
        missing = self.page(path)
        self.assertFalse(missing.xpath("//a[contains(@href,'docx')]"))
        self.assertIn("kein vollständiger Vierertisch", missing.text_content())
        self.assertEqual(self.button(missing, "Sitzplan für Runde 1"), "/tournaments/1/rounds/1")

    def test_filtered_list_returns_from_detail_and_forms_with_both_filters(self):
        title = "Grün & Weiß"
        self.tournament(title)
        self.tournament("Herbstturnier")
        self.tournament(title)
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET closed_at='2026-11-14' WHERE id=3")

        unfiltered = self.page("/tournaments").xpath("//tr[@data-open-url='/tournaments/1']")[0]
        plain_edit, = unfiltered.xpath(".//a[normalize-space(.)='Bearbeiten']/@href")
        self.assertEqual(self.button(self.page(plain_edit), "Abbrechen"), "/tournaments/1")
        initial = "/tournaments?" + urlencode({"status": "offen", "q": title})
        listing = self.page(initial)
        row, = listing.xpath("//tr[@data-open-url]")
        view, = row.xpath(".//a[normalize-space(.)='Öffnen']/@href")
        edit, = row.xpath(".//a[normalize-space(.)='Bearbeiten']/@href")
        self.assertEqual(row.get("data-open-url"), view)
        self.assertEqual(row.get("data-edit-url"), edit)
        overview = self.page(view)
        return_link = overview.get_element_by_id("btnBackToList").get("href")
        returned = self.page(return_link)
        self.assertEqual(returned.xpath("//input[@name='q']/@value"), [title])
        self.assertEqual(returned.xpath("//nav[@aria-label='Turniere filtern']/a[@aria-current]/text()"), ["Offen"])
        self.assertEqual(returned.xpath("//tr[@data-open-url]/td[2]/strong/text()"), [title])

        new = listing.get_element_by_id("btnNewTournament").get("href")
        for path in (new, edit):
            page = self.page(path)
            self.assertEqual(page.get_element_by_id("btnBack").get("href"), return_link)
            self.assertEqual(self.button(page, "Abbrechen"), return_link)
            fields = dict(page.get_element_by_id("tForm").fields, title=title, event_date="2026-11-14", marker="X")
            invalid = self.client.post(path, data=fields)
            self.assertEqual(invalid.status_code, 422)
            retry = html.fromstring(invalid.get_data(as_text=True))
            self.assertEqual(self.button(retry, "Abbrechen"), return_link)
            saved = self.client.post(path, data=dict(retry.get_element_by_id("tForm").fields, marker=""))
            self.assertEqual(saved.status_code, 302)
            self.assertEqual(self.page(saved.location).get_element_by_id("btnBackToList").get("href"), return_link)

        details = self.page(self.button(overview, "Turnierdetails"))
        edit_from_detail = self.page(details.get_element_by_id("btnEditTournament").get("href"))
        self.assertEqual(self.button(edit_from_detail, "Abbrechen"), view)
        delete = row.xpath(".//form")[0]
        self.assertEqual(self.client.post(delete.get("action"), data=dict(delete.fields)).location, return_link)

    def test_lists_in_two_tabs_keep_independent_filters_even_with_no_matches(self):
        views = []
        for status, term in (("abgeschlossen", "Sommer"), ("offen", "Winter")):
            page = self.page("/tournaments?" + urlencode({"status": status, "q": term}))
            self.assertIn("Keine passenden Turniere gefunden.", page.text_content())
            views.append((page.get_element_by_id("btnNewTournament").get("href"), status, term))
        for new, status, term in views:
            returned = self.page(self.button(self.page(new), "Abbrechen"))
            self.assertEqual(returned.xpath("//input[@name='q']/@value"), [term])
            self.assertEqual(returned.xpath("//input[@name='status']/@value"), [status])
        self.assertEqual(self.button(self.page("/tournaments/new"), "Abbrechen"), "/tournaments")

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
        self.assertEqual(self.page(nav[-1].get("href")).xpath("//h1/text()"), ["Druck & Export"])
