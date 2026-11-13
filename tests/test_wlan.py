from contextlib import closing
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from unittest.mock import patch

from lxml import html

from app import db
from app.web import create_app
from app.wlan import wlan_origin
from tests.form_client import form_client


class WlanTests(unittest.TestCase):
    origin = "http://192.168.1.37:8051"
    table = "/tournaments/1/rounds/1/results/1"
    helper = "/helfer/1"

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.db_path = self.base / "turnier.sqlite3"
        self.app = self.start()
        self.admin = form_client(self.app)
        self.phone = self.app.test_client()
        self.options = {"base_url": self.origin, "environ_overrides": {"REMOTE_ADDR": "192.168.1.20"}}
        for tid in (1, 2):
            self.admin.post("/tournaments/new", data={"title": f"Probe {tid}", "event_date": "2026-11-13",
                                                      "start_time": "18:00"})
        for name in ("Anna", "Berta", "Clara", "Dora"):
            self.admin.post("/tournaments/1/participants/quickadd",
                            data={"vorname": name, "nachname": "Probe", "wohnort": "Testort"})
        self.admin.post("/tournaments/1/rounds/1/draw", data={"expected_round_id": "0"})

    def start(self):
        return create_app(db_path=self.db_path, backup_dir=self.base / "backups", wlan_ip="192.168.1.37", port=8051)

    def access_code(self, tid=1):
        page = html.fromstring(self.admin.get(f"/tournaments/{tid}").data)
        section = page.xpath("//section[@aria-label='Helferzugang']")[0]
        self.assertEqual(section.xpath(".//a/@href"), [f"{self.origin}/helfer/{tid}"])
        return section.xpath(".//strong/text()")[-1]

    def login(self, code):
        page = self.phone.get(self.helper, **self.options)
        fields = dict(html.fromstring(page.data).forms[0].fields, code=code)
        return self.phone.post(self.helper, data=fields, **self.options)

    def card_path(self):
        page = html.fromstring(self.admin.get("/tournaments/1/players/1").data)
        link = page.xpath("//a[starts-with(@href,$prefix)]/@href", prefix=self.origin + "/karte/")[0]
        return urlsplit(link).path

    def test_helper_code_is_scoped_and_management_is_local(self):
        self.assertEqual(self.phone.get(self.table, **self.options).location, self.helper)
        self.assertEqual(self.login(self.access_code(2)).status_code, 403)
        self.assertEqual(self.login(self.access_code()).status_code, 303)
        portal = html.fromstring(self.phone.get(self.helper, **self.options).data)
        self.assertTrue(portal.xpath("//a[@href='/tournaments/1/rounds/1/results']"))
        table = html.fromstring(self.phone.get(self.table, **self.options).data)
        self.assertFalse(table.xpath("//a[contains(@href,'standings') or contains(@href,'addresses') or contains(@href,'docx')]"))
        for path in ("/addresses", "/tournaments/1", "/tournaments", "/tournaments/1/players/1",
                     "/tournaments/1/diagnostics", "/static/templates/unused.docx"):
            with self.subTest(path=path):
                self.assertEqual(self.phone.get(path, **self.options).status_code, 403)
        for path in ("/backup", "/restore-upload", "/tournaments/1/delete", "/tournaments/2/rounds/1/results/1"):
            self.assertEqual(self.phone.post(path, **self.options).status_code, 403)
        self.assertEqual(self.phone.get(self.table.replace("/1/rounds", "/2/rounds"), **self.options).location, "/helfer/2")
        form = portal.forms[0]
        self.assertEqual(self.phone.post(form.action, **self.options).status_code, 403)
        self.assertEqual(self.phone.post(form.action, data=dict(form.fields), **self.options).status_code, 303)
        self.assertEqual(self.phone.get(self.table, **self.options).location, self.helper)

    def test_helper_saves_checked_values_and_conflicts_are_visible(self):
        self.login(self.access_code())
        page = self.phone.get(self.table, **self.options)
        fields = dict(html.fromstring(page.data).get_element_by_id("skResultsForm").fields)
        for i, points in enumerate((30, -10, -10, -10), start=1):
            fields[f"points_{i}"] = str(points)
            fields[f"soli_{i}"] = "0"
        fields["soli_1"] = "41"
        self.assertEqual(self.phone.post(self.table, data=fields, **self.options).status_code, 422)
        fields["soli_1"] = "40"
        self.assertEqual(self.phone.post(self.table, data=fields, **self.options).status_code, 302)
        fields["soli_1"] = "2"
        self.assertEqual(self.phone.post(self.table, data=fields, **self.options).status_code, 409)
        with closing(db.connect()) as con, con:
            rows = con.execute("SELECT points,soli FROM tournament_scores ORDER BY tp_id").fetchall()
            self.assertEqual(list(map(tuple, rows)), [(30, 40), (-10, 0), (-10, 0), (-10, 0)])
            con.execute("UPDATE tournaments SET closed_at='2026-11-13' WHERE id=1")
        self.assertEqual(self.phone.get(self.table, **self.options).status_code, 403)
        self.assertEqual(self.phone.post(self.table, data=fields, **self.options).status_code, 403)
        self.assertEqual(self.phone.get(self.helper, **self.options).status_code, 403)

    def test_signed_card_shows_one_player_and_follows_registration_corrections(self):
        path = self.card_path()
        response = self.phone.get(path, **self.options)
        page = html.fromstring(response.data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Anna", page.text_content())
        self.assertNotIn("Berta", page.text_content())
        self.assertFalse(page.xpath("//a[contains(@href,'players') or contains(@href,'standings')]"))
        self.assertTrue(response.cache_control.no_store)
        with patch("app.routes.tournaments.player._qr_png", return_value=b"qr") as encoder:
            self.admin.get("/tournaments/1/players/1/qr.png")
            encoder.assert_called_once_with(self.origin + path)
        self.assertEqual(self.phone.get("/karte/ungueltig", **self.options).status_code, 404)
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort) VALUES(1,'Neu','Eva','Testort')")
            con.execute("UPDATE tournament_participants SET address_id=5 WHERE id=1")
        self.assertEqual(self.phone.get(path, **self.options).status_code, 404)
        self.assertIn("Eva", html.fromstring(self.phone.get(self.card_path(), **self.options).data).text_content())

    def test_restore_revokes_existing_links_sessions_and_forms(self):
        card = self.card_path()
        code = self.access_code()
        self.login(code)
        page = self.phone.get(self.table, **self.options)
        form = dict(html.fromstring(page.data).get_element_by_id("skResultsForm").fields)
        self.admin.post("/backup")
        [backup] = (self.base / "backups").glob("*.sqlite3")
        self.admin.post("/restore/" + backup.name)
        self.assertEqual(self.phone.get(card, **self.options).status_code, 404)
        self.assertEqual(self.phone.post(self.table, data=form, **self.options).status_code, 403)
        self.assertEqual(self.login(code).status_code, 403)
        self.assertEqual(self.login(self.access_code()).status_code, 303)
        self.assertEqual(self.phone.get(self.table, **self.options).status_code, 200)

    def test_restart_replaces_code_and_invalidates_helper_session(self):
        code = self.access_code()
        self.login(code)
        old_cookie = self.phone.get_cookie("session", domain="192.168.1.37").value
        new_app = self.start()
        self.admin = form_client(new_app)
        self.phone = new_app.test_client()
        self.phone.set_cookie("session", old_cookie, domain="192.168.1.37")
        self.assertEqual(self.phone.get(self.table, **self.options).location, self.helper)
        self.assertNotEqual(code, self.access_code())
        self.assertEqual(self.login(code).status_code, 403)

    def test_private_address_and_default_local_mode(self):
        for address in ("0.0.0.0", "localhost", "127.0.0.1", "8.8.8.8", "224.0.0.1"):
            with self.assertRaises(ValueError):
                wlan_origin(address, 8051)
        self.assertEqual(wlan_origin("10.10.0.12", 80), "http://10.10.0.12")
        self.assertEqual(self.phone.get(self.helper, base_url="http://192.168.1.38:8051").status_code, 403)
        local = create_app(db_path=self.db_path).test_client()
        self.assertEqual(local.get("/tournaments/1").status_code, 200)
        self.assertNotIn("Helfercode", local.get("/tournaments/1").get_data(as_text=True))
        for path in (self.helper, self.table, "/", "/addresses"):
            self.assertEqual(local.get(path, **self.options).status_code, 403)
