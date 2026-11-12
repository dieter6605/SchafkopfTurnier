from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

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
        tournament = page.xpath("//h3[.='Turnier']/following-sibling::p[1]")[0]
        name = tournament.find("b")
        self.assertEqual(name.text, title)
        self.assertEqual(len(name), 0)
        self.assertEqual(tournament.find("span").text, f"(marker: {marker})")
        self.assertIn("2026-11-12 18:00", tournament.text_content())
        self.assertEqual(page.xpath("//h3[.='Turnier']/following-sibling::ul[1]/li/b/text()"), ["4","1","4","4"])
        self.assertEqual(page.xpath("//a/@href"), ["/tournaments/1"])

    def test_general_and_missing_tournament_keep_the_database_status(self):
        for path in ("/tournaments/diagnostics", "/tournaments/999/diagnostics"):
            with self.subTest(path=path):
                page = self.page(path)
                self.assertEqual(page.xpath("//h3[.='Prüfstatus']/following-sibling::ul[1]/li/b/text()"),
                                 ["1","ok","0"])
                self.assertIn("Keine FK-Probleme gefunden.", page.text_content())
                self.assertEqual(page.xpath("//a/@href"), ["/tournaments"])
                self.assertEqual("Turnier nicht gefunden." in page.text_content(), "999" in path)

    def test_diagnostics_show_foreign_key_details_for_an_inconsistent_test_database(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name)"
                        " VALUES(10,1,20,'Testperson')")
            expected = sorted(tuple(map(str, row)) for row in con.execute("PRAGMA foreign_key_check"))
        self.assertEqual(len(expected), 2)
        page = self.page("/tournaments/diagnostics")
        self.assertEqual(page.xpath("//li/b[@class='bad']/text()"), ["2"])
        details = sorted(tuple(row.xpath("td/text()")) for row in page.xpath("//tbody/tr"))
        self.assertEqual(details, expected)
