import base64
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from lxml import html
from weasyprint import HTML

from app import db
from app.web import create_app


class PlayerCardTests(unittest.TestCase):
    card_path = "/tournaments/1/players/1"

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.app = create_app(db_path=base/"turnier.sqlite3", backup_dir=base/"backups")
        self.app.testing = True
        self.client = self.app.test_client()
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO tournaments(title,event_date,start_time) VALUES ('Kartenprobe','2026-11-12','18:00')")
            for i, first in enumerate(("Anna", "Ben", "Clara", "Dora"), 1):
                con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort) VALUES(1,'Müller',?,'Testort')", (first,))
                con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name) "
                            "VALUES(1,?,?,?)", (i, i, first + " Müller"))
            for rn in (1, 2, 3):
                con.execute("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,?)", (rn,))
                for i, seat in enumerate("ABCD", 1):
                    con.execute("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id) "
                                "VALUES(1,?,1,?,?)", (rn, seat, i))
                    if rn < 3:
                        points = (30 if i == 1 else -10) if rn == 1 else (-15 if i == 1 else 5)
                        con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id,points,soli) "
                                    "VALUES(1,?,1,?,?,?)", (rn, i, points, 1 if i == 1 else 0))

    def test_browser_card_uses_its_request_address_and_local_asset_routes(self):
        origin = "http://127.0.0.1:8051/verein/"
        response = self.client.get(self.card_path, base_url=origin)
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.get_data(as_text=True))
        self.assertIn(origin.rstrip("/") + self.card_path, page.text_content())
        qr = page.xpath("//img[@alt='QR-Code zur Spielerkarte']/@src")
        self.assertEqual(qr, ["/verein"+self.card_path+"/qr.png"])
        self.assertTrue(page.xpath("//link[@href='/verein/static/vendor/bootstrap/bootstrap.min.css']"))
        self.assertTrue(page.xpath("//img[@src='/verein/static/branding/logo.png']"))
        self.assertTrue(page.xpath("//a[@href='/verein"+self.card_path+"/pdf']"))
        self.assertIn("Müller, Anna", page.text_content())

    def test_card_returns_to_filtered_list_through_adjacent_cards(self):
        for section, label, filters in (
            ("standings", "Gesamtwertung", {"suche": "#1"}),
            ("participants", "Teilnehmer", {"suche": "Müller & A", "status": "active", "q": "Äußere Straße"}),
        ):
            with self.subTest(section=section):
                list_path = "/tournaments/1/" + section
                listing = html.fromstring(self.client.get(list_path + "?" + urlencode(filters)).data.decode())
                card_link, = listing.xpath("//a[@data-player-card and contains(@href,'/players/1?')]/@href")
                card = html.fromstring(self.client.get(card_link).data.decode())
                forward, = card.xpath("//a[normalize-space(.)='Nächster →']/@href")
                adjacent = html.fromstring(self.client.get(forward).data.decode())
                previous, = adjacent.xpath("//a[normalize-space(.)='← Vorheriger']/@href")
                self.assertEqual(urlsplit(previous).path, self.card_path)
                self.assertEqual(parse_qs(urlsplit(previous).query), parse_qs(urlsplit(card_link).query))
                for page in (card, adjacent):
                    active, = page.xpath("//nav[@aria-label='Turniernavigation']/a[@aria-current]")
                    self.assertEqual(active.text, label)
                    back, = page.xpath("//a[starts-with(normalize-space(.),'← Zur')]/@href")
                    self.assertEqual(active.get("href"), back)
                    self.assertEqual(urlsplit(back).path, list_path)
                    self.assertEqual(parse_qs(urlsplit(back).query), {k: [v] for k, v in filters.items()})
                    self.assertFalse(page.xpath("//a[contains(@href,'/pdf?')] | //img[contains(@src,'qr.png?')]"))
                    shared, = page.xpath("//a[starts-with(@href,'http')]/@href")
                    self.assertEqual(urlsplit(shared).query, "")
                returned = html.fromstring(self.client.get(back).data.decode())
                self.assertEqual(returned.xpath("//*[@data-card-filter='suche']/@value"), [filters["suche"]])
                if section == "participants":
                    self.assertEqual(returned.get_element_by_id("rosterStatus").value, "active")
                    self.assertEqual(returned.get_element_by_id("qInput").value, filters["q"])

    def test_direct_card_and_missing_player_return_to_local_lists(self):
        page = html.fromstring(self.client.get(self.card_path + "?von=fremd&status=active").data.decode())
        active, = page.xpath("//nav[@aria-label='Turniernavigation']/a[@aria-current]")
        self.assertEqual((active.text, active.get("href")), ("Gesamtwertung", "/tournaments/1/standings"))
        response = self.client.get("/tournaments/1/players/999?von=teilnehmer&status=withdrawn&suche=%23999")
        self.assertEqual(urlsplit(response.location).path, "/tournaments/1/participants")
        self.assertEqual(parse_qs(urlsplit(response.location).query), {"status": ["withdrawn"], "suche": ["#999"]})

    def test_pdf_uses_embedded_qr_and_local_files_with_network_unavailable(self):
        origin = "http://127.0.0.1:8051/verein/"
        expected_qr = self.client.get(self.card_path + "/qr.png", base_url=origin).data
        self.assertTrue(expected_qr.startswith(b"\x89PNG"))
        requests = []
        rendered = []
        html_init = HTML.__init__

        def capture(instance, *args, **kwargs):
            if "string" in kwargs:
                rendered.append((html.fromstring(kwargs["string"]), kwargs["base_url"]))
            html_init(instance, *args, **kwargs)

        def reject_network(*args, **kwargs):
            requests.append(True)
            raise AssertionError("Die Spielerkarte hat eine Netzwerkverbindung angefordert.")

        with patch.object(HTML, "__init__", capture), patch("socket.socket.connect", reject_network), \
             patch("socket.getaddrinfo", reject_network), self.assertNoLogs("weasyprint", level="ERROR"):
            response = self.client.get(self.card_path + "/pdf", base_url=origin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"%PDF-"))
        self.assertEqual(requests, [])
        [(page, base)] = rendered
        self.assertEqual(base, Path(self.app.static_folder).resolve().as_uri()+"/")
        qr = page.xpath("//img[@alt='QR-Code zur Spielerkarte']/@src")[0]
        prefix, data = qr.split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        self.assertEqual(base64.b64decode(data, validate=True), expected_qr)
        card_url = origin.rstrip("/") + self.card_path
        self.assertEqual(page.xpath("//a[text()=$target]/@href", target=card_url), [card_url])
        self.assertTrue(page.xpath("//link[@href='vendor/bootstrap/bootstrap.min.css']"))
        curves = page.xpath("//polyline[@fill='none']/@stroke")
        self.assertEqual(curves, ["#0d6efd", "#198754"])

    def test_closed_tournament_card_matches_standings_on_screen_phone_and_pdf(self):
        with closing(db.connect()) as con, con:
            con.execute("DELETE FROM tournament_seats WHERE round_no=3")
            con.execute("DELETE FROM tournament_rounds WHERE round_no=3")
            con.execute("UPDATE tournaments SET closed_at='2026-11-13'")
        valid = html.fromstring(self.client.get(self.card_path).get_data(as_text=True))
        self.assertEqual(valid.xpath("//span[@aria-label='Medaille']/text()"), ["🥇"])
        self.assertNotIn("Vorläufiger Platz", valid.text_content())

        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournament_scores SET points=31 WHERE round_no=1 AND tp_id=1")
        local = html.fromstring(self.client.get(self.card_path).get_data(as_text=True))
        issue = "Runde 1, Tisch 1: Die Punktesumme beträgt 1 und muss 0 sein."
        standings = self.client.get("/tournaments/1/standings").get_data(as_text=True)
        self.assertIn(issue, standings)

        self.app.config["WLAN_ORIGIN"] = "http://192.168.1.37:8051"
        admin = html.fromstring(self.client.get(self.card_path).get_data(as_text=True))
        card_link, = admin.xpath("//a[contains(@href,'/karte/')]/@href")
        phone_response = self.app.test_client().get(urlsplit(card_link).path, base_url=self.app.config["WLAN_ORIGIN"],
                                                    environ_overrides={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(phone_response.status_code, 200)
        phone = html.fromstring(phone_response.get_data(as_text=True))
        self.assertNotIn("Dora", phone.text_content())
        self.assertFalse(phone.xpath("//a[contains(@href,'/players/') or contains(@href,'/standings')]"))

        pdf_input = []
        original_init = HTML.__init__

        def capture(instance, *args, **kwargs):
            if "string" in kwargs:
                pdf_input.append(html.fromstring(kwargs["string"]))
            original_init(instance, *args, **kwargs)

        with patch.object(HTML, "__init__", capture):
            pdf = self.client.get(self.card_path + "/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF-"))
        self.assertEqual(len(pdf_input), 1)
        for page in (local, phone, pdf_input[0]):
            chips = [" ".join(span.text_content().split()) for span in page.xpath("//span[@class='sk-chip']")]
            self.assertIn("Vorläufiger Platz: 1", chips)
            self.assertIn("Punkte: 16", chips)
            self.assertIn(issue, page.text_content())
            self.assertFalse(page.xpath("//span[@aria-label='Medaille']"))

    def test_unreadable_points_and_soli_on_player_cards(self):
        cases = [
            ("unlesbar", 1, "–", "nicht berechenbar", "2"),
            (30, "unlesbar", "1", "15", "nicht berechenbar"),
            (10, "unlesbar", "–", "-5", "nicht berechenbar"),
        ]
        for points, soli, place, sum_points, sum_soli in cases:
            with self.subTest(points=points, soli=soli):
                with closing(db.connect()) as con, con:
                    con.execute("UPDATE tournament_scores SET points=?,soli=? WHERE round_no=1 AND tp_id=1",
                                (points, soli))
                response = self.client.get(self.card_path)
                self.assertEqual(response.status_code, 200)
                page = html.fromstring(response.get_data(as_text=True))
                chips = [" ".join(span.text_content().split()) for span in page.xpath("//span[@class='sk-chip']")]
                self.assertIn("Vorläufiger Platz: " + place, chips)
                self.assertIn("Punkte: " + sum_points, chips)
                self.assertIn("Soli: " + sum_soli, chips)
                self.assertIn("gespielt: 2/3", chips)
                self.assertIn("unlesbar", page.xpath("//div[@class='sk-rndbox']")[0].text_content())
                self.assertTrue(page.xpath("//div[@class='sk-rndbox'][3]//*[text()='kein Ergebnis']"))
                self.assertEqual(bool(page.xpath("//polyline")), isinstance(points, int))
                self.assertEqual(bool(page.xpath("//span[@title='Beste Runde']")), isinstance(points, int))
                self.assertTrue(self.client.get(self.card_path + "/pdf").data.startswith(b"%PDF-"))
                other = html.fromstring(self.client.get("/tournaments/1/players/2").get_data(as_text=True))
                other_place = "–" if points == 10 else ("2" if points == 30 else "1")
                other_chips = [" ".join(span.text_content().split()) for span in other.xpath("//span[@class='sk-chip']")]
                self.assertIn("Vorläufiger Platz: " + other_place, other_chips)
                self.assertFalse(other.xpath("//span[@aria-label='Medaille']"))

    def test_missing_card_preserves_html_and_pdf_redirects(self):
        for path in ("/tournaments/999/players/1", "/tournaments/1/players/999"):
            page = self.client.get(path)
            pdf = self.client.get(path + "/pdf")
            self.assertEqual((pdf.status_code, pdf.location), (302, page.location))
