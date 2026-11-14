from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from lxml import html

from app import db
from app.web import create_app
from tests.form_client import form_client


class RegistrationTests(unittest.TestCase):
    origin = "http://192.168.1.37:8051"
    phone_options = {"base_url": origin, "environ_overrides": {"REMOTE_ADDR": "192.168.1.43"}}
    login_path = "/helfer/1/anmeldung"
    desk = "/helfer/1/teilnehmer"

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = self.start()
        self.admin = form_client(self.app)
        self.phone = self.app.test_client()
        for title in ("Freitag", "Samstag"):
            self.admin.post("/tournaments/new", data={"title": title, "event_date": "2026-11-14", "start_time": "18:00"})
        with closing(db.connect()) as con, con:
            con.execute("""INSERT INTO addresses(addressbook_id,vorname,nachname,wohnort,email,telefon,invite)
                           VALUES(1,'Anna','Müller','Bieswang','privat@example.test','12345678',0)""")

    def start(self):
        return create_app(db_path=self.root/"test.sqlite3", backup_dir=self.root/"backups",
                          wlan_ip="192.168.1.37", port=8051)

    def credentials(self, area="Teilnehmererfassung im WLAN", tid=1):
        page = html.fromstring(self.admin.get(f"/tournaments/{tid}").get_data(as_text=True))
        section, = page.xpath("//section[@aria-label=$area]", area=area)
        link, = section.xpath(".//a/@href")
        code, = section.xpath(".//strong[@class='sk-mono']/text()")
        return link.removeprefix(self.origin), code

    def login(self, client=None, path=None, code=None):
        client = client or self.phone
        link, own_code = self.credentials()
        path = path or link
        page = html.fromstring(client.get(path, **self.phone_options).data)
        form = page.forms[0]
        return client.post(form.action, data=dict(form.fields, code=code or own_code), **self.phone_options)

    def form(self, action, query="", client=None):
        client = client or self.phone
        response = client.get(self.desk, query_string={"q": query}, **self.phone_options)
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.get_data(as_text=True))
        return dict(page.xpath("//form[input[@name='action' and @value=$action]]", action=action)[0].fields)

    def test_signup_uses_own_code_tournament_and_role(self):
        path, code = self.credentials()
        result_path, result_code = self.credentials("Helferzugang")
        self.assertEqual(path, self.login_path)
        self.assertNotEqual(code, result_code)
        self.assertEqual(self.login(code=result_code).status_code, 403)
        self.assertEqual(self.login(code=self.credentials(tid=2)[1]).status_code, 403)
        self.assertEqual(self.login(path=result_path, code=code).status_code, 403)
        self.assertEqual(self.phone.get(self.desk, **self.phone_options).location, path)
        self.login(path=result_path, code=result_code)
        self.assertEqual(self.phone.get(self.desk, **self.phone_options).location, path)
        self.assertEqual(self.login().location, self.desk)
        for url in ("/addresses", "/tournaments/1/participants", "/tournaments/1/participants/swap-search"):
            self.assertEqual(self.phone.get(url, **self.phone_options).status_code, 403)
        self.assertEqual(self.phone.get("/tournaments/1/rounds/1/results", **self.phone_options).location, result_path)
        self.assertEqual(self.phone.get("/helfer/2/teilnehmer", **self.phone_options).location, "/helfer/2/anmeldung")
        response = self.phone.get(self.desk, **self.phone_options)
        form, = html.fromstring(response.data).xpath("//form[contains(@action,'abmelden')]")
        self.assertEqual(self.phone.post(form.action, data=dict(form.fields), **self.phone_options).location, path)
        self.assertEqual(self.phone.get(self.desk, **self.phone_options).location, path)

    def test_existing_person_has_one_number_and_private_fields_stay_local(self):
        self.login()
        for private in ("privat@example.test", "12345678"):
            page = self.phone.get(self.desk, query_string={"q":private}, **self.phone_options)
            self.assertNotIn("Anna", html.fromstring(page.data).xpath("//main")[0].text_content())
        page = self.phone.get(self.desk, query_string={"q":"MÜLLER Anna"}, **self.phone_options)
        self.assertIn("Anna Müller",page.get_data(as_text=True))
        self.assertNotIn("privat@example.test",page.get_data(as_text=True))
        form = self.form("add", "bIESwang")
        response = self.phone.post(self.desk, data=form, **self.phone_options)
        self.assertEqual(response.status_code, 303)
        receipt = self.phone.get(response.location, **self.phone_options)
        self.assertIn("Anna Müller ist mit Startnummer 1 angemeldet.", receipt.get_data(as_text=True))
        self.assertEqual(self.phone.post(self.desk, data=form, **self.phone_options).status_code, 422)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_participants").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT invite FROM addresses").fetchone()[0], 0)
            self.assertIn("Anmeldehelfer", con.execute("SELECT note FROM audit_log").fetchone()[0])
        self.assertIn("Anna Müller", self.admin.get("/tournaments/1/participants").get_data(as_text=True))

    def test_new_person_parallel_desks_and_capacity(self):
        second = self.app.test_client()
        self.login()
        self.login(second)
        data = {"nachname":"Helfertest", "vorname":"Beate", "wohnort":"Dorf"}
        clients = [(c, dict(self.form("new", client=c), **data)) for c in (self.phone, second)]
        gate = Barrier(2)
        def book(arg):
            client, form = arg
            gate.wait(timeout=10)
            return client.post(self.desk, data=form, **self.phone_options).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(book, clients)), [303, 422])
        with closing(db.connect()) as con, con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM addresses WHERE nachname='Helfertest'").fetchone()[0], 1)
            self.assertEqual(tuple(con.execute("SELECT player_no,display_name FROM tournament_participants").fetchone()),
                             (1, "Helfertest, Beate · Dorf"))
            con.execute("UPDATE tournaments SET max_participants=1 WHERE id=1")
        form = dict(clients[0][1], nachname="Andere")
        self.assertEqual(self.phone.post(self.desk, data=form, **self.phone_options).status_code, 422)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM addresses").fetchone()[0], 2)

    def test_saved_form_requires_current_session_and_open_tournament(self):
        _, code = self.credentials()
        self.login()
        data = self.form("add", "Anna")
        self.assertEqual(self.phone.post(self.desk, data=dict(data,csrf_token=""), **self.phone_options).status_code, 403)
        self.admin.post("/backup")
        backup, = (self.root/"backups").glob("*.sqlite3")
        self.admin.post("/restore/" + backup.name)
        self.assertEqual(self.phone.post(self.desk, data=data, **self.phone_options).status_code, 403)
        self.assertEqual(self.login(code=code).status_code, 403)
        self.login()
        form = self.form("add", "Anna")
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET closed_at='2026-11-14' WHERE id=1")
        self.assertEqual(self.phone.post(self.desk, data=form, **self.phone_options).status_code, 403)
        self.assertEqual(self.phone.get(self.desk, **self.phone_options).status_code, 403)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_participants").fetchone()[0], 0)
