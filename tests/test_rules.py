from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lxml import html
from weasyprint import HTML

from app import db
from app.web import create_app


class RulesTests(unittest.TestCase):
    path = "/hilfe/spielregeln"
    editor = path + "/bearbeiten"

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = self.start()
        self.client = self.app.test_client()

    def start(self):
        return create_app(db_path=self.root / "test.sqlite3", backup_dir=self.root / "backups",
                          wlan_ip="192.168.1.37", port=8051)

    def form(self):
        doc = html.fromstring(self.client.get(self.editor).get_data(as_text=True))
        fields = doc.forms[0].fields
        return {"csrf_token": fields["csrf_token"], "revision": fields["revision"],
                "title": "Regelprobe", "closing": "Gutes Blatt!", "heading": ["Spielwahl", ""], "text": ["Nur zur Probe.", ""]}

    def page_text(self):
        return html.fromstring(self.client.get(self.path).get_data(as_text=True)).xpath("//article")[0].text_content()

    def test_club_rules_and_confirmed_wenz_wording(self):
        text = self.page_text()
        for part in ("zwei Runden mit je 40 Spielen", "über Kreuz", "ab 61 Augen", "ab 31 Augen",
                     "ab 60 Augen", "ab 30 Augen", "Laufende zählen ab drei", "beim Wenz nur die vier Unter",
                     "Solo und Wenz · 4 Punkte, mit Schneider 5 Punkte, mit Schwarz 6 Punkte",
                     "Im 1. Spiel darf kein Tout angesagt werden", "Ruf-Ass zu 4", "Schneider Schwarz",
                     "Startgeld", "entscheidet das Los", "Eure Sportfreunde Bieswang"):
            self.assertIn(part, text)

    def test_browser_and_pdf_share_edited_text_and_escaped_markup(self):
        fields = self.form()
        fields.update(heading=["Augen", "Ehrenpreis", ""], text=["A < B & C", "Losentscheid.\r\nZweite Zeile.", ""])
        self.assertEqual(self.client.post(self.editor, data=fields).location, self.path)
        page = html.fromstring(self.client.get(self.path).get_data(as_text=True))
        self.assertEqual(page.xpath("//article//h2/text()"), ["Augen", "Ehrenpreis"])
        self.assertEqual(page.xpath("//article//p/text()"), ["A < B & C", "Losentscheid.", "Zweite Zeile.", "Gutes Blatt!"])
        self.assertEqual(page.xpath("//h1/text()"), ["Regelprobe"])
        url, = page.xpath('//a[normalize-space(.)="PDF herunterladen"]/@href')
        with patch("weasyprint.HTML", wraps=HTML) as render:
            response = self.client.get(url)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF-"))
        self.assertIn("attachment; filename=Spielregeln.pdf", response.headers["Content-Disposition"])
        pdf = html.fromstring(render.call_args.kwargs["string"])
        self.assertEqual(pdf.xpath("//h1/text() | //h2/text() | //p/text()"),
                         page.xpath("//h1/text() | //article//h2/text() | //article//p/text()"))

    def test_cancel_invalid_and_concurrent_edits_preserve_saved_rules(self):
        original = self.page_text()
        form = self.form()
        page = html.fromstring(self.client.get(self.editor).data)
        cancel, = page.xpath('//a[normalize-space(.)="Abbrechen"]/@href')
        self.assertEqual(cancel, self.path)
        self.client.get(cancel)
        self.assertEqual(self.page_text(), original)
        invalid = dict(form, text=["", ""])
        response = self.client.post(self.editor, data=invalid)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Spielwahl", response.get_data(as_text=True))
        self.assertEqual(self.page_text(), original)
        self.assertEqual(self.client.post(self.editor, data=form).status_code, 303)
        conflict = self.client.post(self.editor, data=dict(form, text=["Zweiter Entwurf", ""]))
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("Zweiter Entwurf", conflict.get_data(as_text=True))
        self.assertIn("Nur zur Probe.", self.page_text())

    def test_custom_rules_survive_restart_and_restore_including_older_backups(self):
        old = db.backup_db(self.root / "backups")
        self.client.post(self.editor, data=self.form())
        saved = db.backup_db(self.root / "backups")
        self.client = self.start().test_client()
        self.assertIn("Nur zur Probe.", self.page_text())
        stale = self.form()
        db.restore_db(old)
        self.assertIn("Ruf-Ass zu 4", self.page_text())
        db.restore_db(saved)
        self.assertIn("Nur zur Probe.", self.page_text())
        self.assertEqual(self.client.post(self.editor, data=stale).status_code, 409)

    def test_club_wifi_can_read_and_download_only(self):
        phone = self.app.test_client()
        options = {"base_url": "http://192.168.1.37:8051", "environ_overrides": {"REMOTE_ADDR": "192.168.1.44"}}
        start = html.fromstring(phone.get("/", **options).data)
        self.assertTrue(start.xpath("//a[@href=$path]", path=self.path))
        page = html.fromstring(phone.get(self.path, **options).data)
        self.assertFalse(page.xpath("//a[@href=$path]", path=self.editor))
        self.assertFalse(page.xpath("//nav[@aria-label='Hilfebereiche']"))
        self.assertEqual(phone.get(self.path + "/pdf", **options).mimetype, "application/pdf")
        self.assertEqual(phone.get(self.editor, **options).status_code, 403)
        self.assertEqual(phone.post(self.editor, data=self.form(), **options).status_code, 403)
        self.assertEqual(self.client.post(self.editor, data={}).status_code, 403)
