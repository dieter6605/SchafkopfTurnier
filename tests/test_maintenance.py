import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lxml import html

from app import db
from app.maintenance import local_session_key
from app.web import create_app


class MaintenanceTests(unittest.TestCase):
    detail = "/tournaments/1"
    reopen = detail + "/reopen"
    global_repair = "/tournaments/repair-addresses"
    local_repair = detail + "/repair-addresses"

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        with patch.dict(os.environ, {"SKT_DEV_MODE": "0"}):
            self.app = create_app(db_path=self.folder/"test.sqlite3")
        self.client = self.app.test_client()
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO tournaments(title,event_date,start_time,marker,closed_at) "
                        "VALUES('Vereinsprobe','2026-11-12','18:00','261112TEST','2026-11-12 20:00:00')")
            con.execute("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,1)")
            for n, seat in enumerate("ABCD",1):
                con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort,"
                            "tournament_years,last_tournament_at,participation_count) "
                            "VALUES(1,'Muster',?,'Probeort','251112ALT,261112TEST','261112TEST',2)", (str(n),))
                con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name)"
                            " VALUES(1,?,?,?)",(n,n,'Probe '+str(n)))
                con.execute("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id) VALUES(1,1,1,?,?)", (seat,n))
                con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id,points,soli)"
                            " VALUES(1,1,1,?,?,0)",(n,30 if n==1 else -10))
            con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort,tournament_years,"
                        "participation_count) VALUES(1,'Weitere','Person','Probeort','251112ALT',5)")

    def page(self, client=None, **kwargs):
        response = (client or self.client).get(self.detail, **kwargs)
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.get_data(as_text=True))

    def fields(self, action, client=None):
        form = self.page(client).xpath('//form[@action=$action]', action=action)[0]
        return dict(form.fields)

    def contents(self):
        with closing(db.connect()) as con:
            return [list(map(tuple,con.execute("SELECT * FROM "+table+" ORDER BY id"))) for table in
                    ("tournaments","addresses","tournament_participants","tournament_scores","tournament_seats")]

    def test_regular_start_and_debug_only_keep_maintenance_disabled(self):
        before = self.contents()
        for debug in (False, True):
            self.app.debug = debug
            page = self.page()
            for action in (self.reopen, self.global_repair, self.local_repair):
                self.assertFalse(page.xpath('//form[@action=$action]', action=action))
                response = self.client.post(action)
                self.assertEqual(response.status_code, 403)
                self.assertIn('Wartungsmodus',response.get_data(as_text=True))
        self.assertEqual(self.contents(), before)

    def test_maintenance_requires_a_local_connection_and_local_hostname(self):
        self.app.config["SKT_DEV_MODE"] = True
        before = self.contents()
        for origin, peer, allowed in (
            ("http://localhost", "127.0.0.1", True),
            ("http://127.0.0.1", "127.0.0.1", True),
            ("http://[::1]", "::1", True),
            ("http://verein.example", "127.0.0.1", False),
            ("http://localhost", "192.0.2.10", False),
        ):
            options = {"base_url":origin, "environ_overrides":{"REMOTE_ADDR":peer}}
            page = self.page(**options)
            forms = page.xpath('//form[@action=$action]',action=self.reopen)
            self.assertEqual(bool(forms), allowed)
            if not allowed:
                for action in (self.reopen,self.global_repair,self.local_repair):
                    self.assertEqual(self.client.post(action,**options).status_code,403)
        self.assertEqual(self.contents(), before)

    def test_admin_forms_reject_missing_invalid_and_other_sessions_tokens(self):
        self.app.config["SKT_DEV_MODE"] = True
        other_client = self.app.test_client()
        before = self.contents()
        for action in (self.reopen,self.global_repair,self.local_repair):
            fields = self.fields(action)
            other_token = self.fields(action, other_client)["csrf_token"]
            self.assertNotEqual(other_token, fields["csrf_token"])
            for token in (None,"",other_token,"ä"):
                data = dict(fields)
                if token is None:
                    del data["csrf_token"]
                else:
                    data["csrf_token"] = token
                self.assertEqual(self.client.post(action,data=data).status_code,403)
        self.assertEqual(self.contents(), before)

    def test_reopen_uses_current_closure_and_preserves_results(self):
        self.app.config["SKT_DEV_MODE"] = True
        form = self.fields(self.reopen)
        before = self.contents()
        self.client.post(self.reopen, data=dict(form,expected_closed_at="früher"))
        self.assertEqual(self.contents(), before)
        response = self.client.post(self.reopen, data=form)
        self.assertEqual((response.status_code,response.location),(302,self.detail))
        with closing(db.connect()) as con:
            self.assertIsNone(con.execute("SELECT closed_at FROM tournaments").fetchone()[0])
            rows = list(map(tuple,con.execute("SELECT participation_count,tournament_years,last_tournament_at "
                                              "FROM addresses WHERE id<=4")))
            self.assertEqual(rows,[(1,"251112ALT","251112ALT")]*4)
        after = self.contents()
        self.assertEqual(after[2:], before[2:])
        self.assertEqual(after[1][-1], before[1][-1])
        self.client.post(self.reopen, data=form)
        self.assertEqual(self.contents(), after)

    def test_repair_preview_scope_and_explicit_save(self):
        self.app.config["SKT_DEV_MODE"] = True
        page = self.page()
        self.assertTrue(page.xpath('//input[@id="repairDryRun" and @checked]'))
        self.assertTrue(page.xpath('//form[@action=$action]//button[@disabled]',action=self.local_repair))
        fields = self.fields(self.local_repair)
        before = self.contents()
        response = self.client.post(self.local_repair,data=dict(fields,dry_run="0"),headers={"Accept":"application/json"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.contents(),before)
        self.client.post(self.reopen, data=self.fields(self.reopen))
        with closing(db.connect()) as con, con:
            con.execute("UPDATE addresses SET participation_count=9 WHERE id=1")
        before = self.contents()
        for action in (self.global_repair,self.local_repair):
            fields = self.fields(action)
            self.assertEqual(fields["dry_run"],"1")
            for flag in ("1",None):
                data=dict(fields)
                if flag is None:
                    del data["dry_run"]
                self.assertEqual(self.client.post(action,data=data).status_code,302)
                self.assertEqual(self.contents(),before)
        for action,expected in ((self.local_repair,(1,5)),(self.global_repair,(1,1))):
            self.client.post(action,data=dict(self.fields(action),dry_run="0"))
            with closing(db.connect()) as con:
                counts = tuple(r[0] for r in con.execute("SELECT participation_count FROM addresses WHERE id IN (1,5) ORDER BY id"))
            self.assertEqual(counts,expected)
        self.assertEqual(self.contents()[2:],before[2:])

    def test_session_key_survives_restart_and_simultaneous_first_use(self):
        key_dir = self.folder/"parallel"
        with ThreadPoolExecutor(max_workers=4) as pool:
            keys = list(pool.map(local_session_key,[key_dir]*8))
        self.assertEqual(len(set(keys)),1)
        self.assertEqual(len(keys[0]),32)
        self.assertNotEqual(self.app.secret_key,keys[0])
        restarted = create_app(db_path=self.folder/"test.sqlite3")
        self.assertEqual(restarted.secret_key,self.app.secret_key)
        if os.name == "posix":
            self.assertEqual((key_dir/".skt-session"/"key").stat().st_mode & 0o077,0)
