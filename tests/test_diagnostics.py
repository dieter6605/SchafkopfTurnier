from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from flask import render_template
from lxml import html

from app import db
from app.web import create_app


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "turnier.sqlite3"
        self.client = create_app(db_path=self.path).test_client()

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        return html.fromstring(response.get_data(as_text=True))

    def test_tournament_text_and_counts_match_the_stored_values(self):
        title = 'Vereinsabend <Finale> & "Freunde"'
        marker = "261112<Ort>"
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO tournaments(title,event_date,start_time,marker) VALUES(?,'2026-11-12','18:00',?)",
                        (title, marker))
            con.execute("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,1)")
            for number, seat in enumerate("ABCD", 1):
                con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort) VALUES(1,'Muster',?,'Testort')",
                            (str(number),))
                con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name)"
                            " VALUES(1,?,?,?)", (number, number, str(number)))
                con.execute("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id) VALUES(1,1,1,?,?)",
                            (seat, number))
                con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id,points,soli)"
                            " VALUES(1,1,1,?,?,0)", (number, 30 if number == 1 else -10))
        page = self.page("/tournaments/1/diagnostics")
        tournament = page.xpath('//section[@aria-labelledby="turnierbestand"]')[0]
        name = tournament.find(".//h2")
        self.assertEqual(name.text, title)
        self.assertEqual(len(name), 0)
        self.assertEqual(tournament.find(".//code").text, marker)
        self.assertIn("12.11.2026 · 18:00 Uhr", tournament.text_content())
        self.assertEqual(self.values(tournament.find("dl")), {
            "Teilnehmer": "4", "Runden": "1", "Sitzplätze": "4", "Einzelergebnisse": "4",
        })
        self.assertEqual(page.xpath('//a[normalize-space(.)="← Zur Turnierübersicht"]/@href'), ["/tournaments/1"])
        self.assertTrue(page.xpath('//nav[@aria-label="Turniernavigation"]'))
        self.assertEqual(page.xpath('//header//a[@aria-current="page"]/@href'), ["/tournaments/1/diagnostics"])

    @staticmethod
    def values(dl):
        return {row.find("dt").text_content(): row.find("dd").text_content() for row in dl.findall("div")}

    def test_general_and_missing_tournament_keep_the_database_status(self):
        for path in ("/tournaments/diagnostics", "/tournaments/999/diagnostics"):
            with self.subTest(path=path):
                page = self.page(path)
                status = page.xpath('//section[@aria-labelledby="datenbankpruefung"]')[0]
                self.assertIn("Ohne Befund", status.text_content())
                self.assertEqual(self.values(status.find("dl")), {
                    "Datenbankstruktur": "In Ordnung", "Schutz vor ungültigen Verknüpfungen": "Aktiv",
                    "Ungültige Verknüpfungen": "0",
                })
                self.assertFalse(page.xpath("//details[@open]"))
                self.assertEqual(self.values(page.xpath("//details//dl")[0]), {
                    "PRAGMA integrity_check": "ok", "PRAGMA foreign_keys": "1", "PRAGMA foreign_key_check": "0 Befunde",
                })
                self.assertEqual(page.xpath('//a[normalize-space(.)="← Zur Turnierliste"]/@href'), ["/tournaments"])
                self.assertFalse(page.xpath('//nav[@aria-label="Turniernavigation"] | //main//form'))
                self.assertFalse(page.xpath('//header//a[@href="/tournaments/999/diagnostics"]'))
                self.assertEqual("Turnier Nr. 999 wurde nicht gefunden." in page.text_content(), "999" in path)
                if "999" not in path:
                    self.assertEqual(page.xpath('//header//a[@aria-current="page"]/@href'), [path])

    def test_diagnostics_show_foreign_key_details_for_an_inconsistent_test_database(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name)"
                        " VALUES(10,1,20,'Testperson')")
            expected = sorted(tuple(map(str, row)) for row in con.execute("PRAGMA foreign_key_check"))
        self.assertEqual(len(expected), 2)
        page = self.page("/tournaments/diagnostics")
        status = page.xpath('//section[@aria-labelledby="datenbankpruefung"]')[0]
        self.assertIn("Prüfung auffällig", status.text_content())
        self.assertEqual(self.values(status.find("dl"))["Ungültige Verknüpfungen"], "2")
        self.assertTrue(page.xpath("//details[@open]//table"))
        details = sorted(tuple(row.xpath("td/text()")) for row in page.xpath("//tbody/tr"))
        self.assertEqual(details, expected)


    def test_failed_structure_or_disabled_constraints_alone_open_the_details(self):
        for fk_on, integrity in ((1, "Fehler in der Datenbankstruktur"), (0, "ok")):
            with self.subTest(fk_on=fk_on), self.client.application.test_request_context("/tournaments/diagnostics"):
                page = html.fromstring(render_template("tournament_diagnostics.html", tournament_id=None,
                    tournament=None, counts=None, fk_on=fk_on, integrity=integrity, issues=[]))
                status = page.xpath('//section[@aria-labelledby="datenbankpruefung"]')[0]
                self.assertIn("Prüfung auffällig", status.text_content())
                self.assertEqual(self.values(status.find("dl"))["Datenbankstruktur"],
                                 "In Ordnung" if integrity == "ok" else "Fehler gemeldet")
                details = page.xpath("//details[@open]//dl")[0]
                self.assertEqual(self.values(details)["PRAGMA integrity_check"], integrity)
                self.assertEqual(self.values(details)["PRAGMA foreign_keys"], str(fk_on))
