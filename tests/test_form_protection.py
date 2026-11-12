import re
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from lxml import html

from app import db
from app.web import create_app


class FormProtectionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.backups = base / "backups"
        self.app = create_app(db_path=base / "probe.sqlite3", backup_dir=self.backups)
        self.client = self.app.test_client()
        with closing(db.connect()) as con, con:
            for title, closed in (("Testturnier", None), ("Archiv", "2026-11-12 18:00")):
                con.execute("INSERT INTO tournaments(title,event_date,start_time,marker,closed_at)"
                            " VALUES(?,'2026-11-12','15:00','261112TEST',?)", (title, closed))
            con.execute("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,1)")
            for player_no in range(1, 6):
                con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort)"
                            " VALUES(1,'Probe',?,'Testort')", (str(player_no),))
                if player_no <= 4:
                    con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name)"
                                " VALUES(1,?,?,?)", (player_no, player_no, str(player_no)))
                    con.execute("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id)"
                                " VALUES(1,1,1,?,?)", ("ABCD"[player_no - 1], player_no))

    def page(self, path, client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200, path)
        return html.fromstring(response.get_data(as_text=True))

    def fields(self, action, client=None):
        form = self.page("/", client).xpath("//form[@action=$action]", action=action)[0]
        return dict(form.fields)

    def test_all_write_actions_are_represented_by_protected_controls(self):
        self.app.config["SKT_DEV_MODE"] = True
        token = self.fields("/backup")["csrf_token"]
        self.assertTrue(token)
        self.assertEqual(self.client.post("/backup", data={"csrf_token": token}).status_code, 302)
        router = self.app.url_map.bind("localhost")
        covered = set()
        for path in ("/", "/addresses?view=all", "/addresses/new", "/addresses/1/edit", "/addresses/import",
                     "/tournaments", "/tournaments/new", "/tournaments/1", "/tournaments/2",
                     "/tournaments/1/edit", "/tournaments/1/participants?q=Probe", "/tournaments/1/rounds/1",
                     "/tournaments/1/rounds/1/results/1"):
            page = self.page(path)
            for form in page.forms:
                fields = form.xpath(".//input[@type='hidden' and @name='csrf_token']/@value")
                if form.method.upper() == "GET":
                    self.assertEqual(fields, [], path)
                    continue
                self.assertEqual(fields, [token], (path, form.action))
                endpoint, _ = router.match(urlsplit(form.action or path).path, method="POST")
                covered.add(endpoint)
            for url in page.xpath("//*[@data-invite-toggle]/@data-url"):
                endpoint, _ = router.match(urlsplit(url).path, method="POST")
                covered.add(endpoint)
        expected = {rule.endpoint for rule in self.app.url_map.iter_rules() if "POST" in rule.methods}
        self.assertEqual(covered, expected)

    def test_backup_uses_its_own_session_and_multiple_open_forms_stay_valid(self):
        first = self.fields("/backup")
        second = self.fields("/backup")
        other = self.fields("/backup", self.app.test_client())
        self.assertNotEqual(first, other)
        for fields in ({}, other):
            response = self.client.post("/backup", data=fields)
            self.assertEqual(response.status_code, 403)
            self.assertIn("Seite neu laden", response.get_data(as_text=True))
            self.assertEqual(list(self.backups.glob("*.sqlite3")), [])
        for fields in (second, first):
            self.assertEqual(self.client.post("/backup", data=fields).status_code, 302)
        self.assertEqual(len(list(self.backups.glob("*.sqlite3"))), 2)

    def test_restore_upload_accepts_the_field_in_a_multipart_form(self):
        self.client.post("/backup", data=self.fields("/backup"))
        [saved] = self.backups.glob("*.sqlite3")
        address = self.page("/addresses/new").forms[0]
        form_data = dict(address.fields, nachname="Neu", vorname="Test", wohnort="Probeort")
        self.assertEqual(self.client.post(address.action, data=form_data).status_code, 302)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM addresses").fetchone()[0], 6)
        for action, count in (("/upload-only", 6), ("/restore-upload", 5)):
            fields = self.fields(action)
            fields["backup_file"] = (BytesIO(saved.read_bytes()), saved.name)
            with self.client.post(action, data=fields) as response:
                self.assertEqual(response.status_code, 302)
            with closing(db.connect()) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM addresses").fetchone()[0], count)

    def test_invite_toggle_uses_the_header_from_the_address_page(self):
        page = self.page("/addresses?view=all")
        url = page.xpath("//*[@data-invite-toggle and @data-address-id='1']/@data-url")[0]
        script = page.xpath("//script[contains(., 'function toggleInvite')]/text()")[0]
        token = re.search(r'"X-CSRF-Token":\s*"([^"]+)"', script).group(1)
        self.assertEqual(token, self.fields("/backup")["csrf_token"])
        headers = {"Accept": "application/json"}
        invalid = self.client.post(url, headers=headers)
        self.assertEqual(invalid.status_code, 403)
        self.assertIn("error", invalid.get_json())
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT invite FROM addresses WHERE id=1").fetchone()[0], 1)
        headers["X-CSRF-Token"] = token
        self.assertEqual(self.client.post(url, headers=headers).get_json()["invite"], 0)

    def test_confirmation_messages_survive_quotes_in_tournament_titles(self):
        title = 'Vereinsabend "A & B"'
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET title=? WHERE id=1", (title,))
        for path in ("/tournaments", "/tournaments/1"):
            form = self.page(path).xpath("//form[@action='/tournaments/1/delete']")[0]
            self.assertIn(title, form.get("data-confirm"))
            self.assertEqual(form.get("onsubmit"), "return window.confirm(this.dataset.confirm)")
        renumber = self.page("/tournaments/1/participants").xpath("//form[contains(@action,'/renumber-from')]")[0]
        self.assertEqual(renumber.get("onsubmit"), "return window.confirm(this.dataset.confirm)")
        self.assertEqual(renumber.get("data-confirm"), "Ab dieser Nummer neu durchnummerieren?")

    def test_existing_maintenance_session_preserves_its_form_token(self):
        with self.client.session_transaction() as session:
            session["maintenance_csrf"] = "token-from-the-previous-release"
        self.assertEqual(self.fields("/backup")["csrf_token"], "token-from-the-previous-release")
        with self.client.session_transaction() as session:
            self.assertNotIn("maintenance_csrf", session)
