import base64
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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

    def test_missing_card_preserves_html_and_pdf_redirects(self):
        for path in ("/tournaments/999/players/1", "/tournaments/1/players/999"):
            page = self.client.get(path)
            pdf = self.client.get(path + "/pdf")
            self.assertEqual((pdf.status_code, pdf.location), (302, page.location))
