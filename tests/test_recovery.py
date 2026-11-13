import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
from zipfile import ZipFile

from lxml import html

from app import db
from app.web import create_app
from tests.form_client import form_client
from tests.test_helper_exports import records


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.live = self.directory / "turnier.sqlite3"
        self.backups = self.directory / "backups"
        self.app = create_app(db_path=self.live, backup_dir=self.backups)
        self.app.testing = True
        self.client = form_client(self.app)

    def test_fresh_database_records_participants_and_restarts(self):
        self.client.post("/tournaments/new", data={
            "title": "Vereinsturnier", "event_date": "2026-11-12", "start_time": "18:00",
        })
        self.client.post("/tournaments/1/participants/quickadd", data={
            "vorname": "Test", "nachname": "Person", "wohnort": "Testort",
        })
        db.init_db(self.live)
        self.assertEqual(self.client.get("/tournaments/1/participants").status_code, 200)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT action FROM audit_log").fetchone()[0], "quickadd")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_participants").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT invite FROM addresses").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def closed_score_backup(self):
        self.client.post("/tournaments/new", data={
            "title": "Freitagsturnier", "event_date": "2026-11-13", "start_time": "18:00", "marker": "261113TEST",
        })
        for name in ("Anna", "Berta", "Clara", "Dora"):
            self.client.post("/tournaments/1/participants/quickadd", data={
                "vorname": name, "nachname": "Probe", "wohnort": "Testort",
            })
        self.client.post("/tournaments/1/rounds/1/draw", data={"expected_round_id": "0"})
        table = "/tournaments/1/rounds/1/results/1"
        form = html.fromstring(self.client.get(table).data).get_element_by_id("skResultsForm")
        values = dict(form.fields)
        values.update(points_1="30", points_2="-10", points_3="-10", points_4="-10")
        self.client.post(table, data=values)
        self.client.post("/tournaments/1/close")
        with closing(db.connect()) as con:
            self.assertTrue(con.execute("SELECT closed_at FROM tournaments").fetchone()[0])
        saved = db.backup_db(self.backups)
        with closing(sqlite3.connect(saved)) as con, con:
            con.execute("UPDATE tournament_scores SET points=points+1 WHERE tp_id=1")
        return saved

    def warning_form(self, response, expected="Die Punktesumme beträgt 1 und muss 0 sein."):
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.get_data(as_text=True))
        self.assertIn("Turnier 1 · Freitagsturnier: Runde 1, Tisch 1", page.text_content())
        self.assertIn(expected, page.text_content())
        form, = page.xpath("//form[.//input[@name='confirmation']]")
        return form.action, dict(form.fields)

    def test_restore_warns_until_confirmed_and_keeps_previous_valid_scores(self):
        saved = self.closed_score_backup()
        original = self.live.read_bytes()
        target, fields = self.warning_form(self.client.post("/restore/" + saved.name))
        self.assertEqual(self.live.read_bytes(), original)
        self.assertFalse(list(self.directory.glob("*.before-restore-*")))
        self.client.post(target, data={**fields, "decision": "cancel"})
        self.client.post(target, data={**fields, "decision": "restore"})
        self.assertEqual(self.live.read_bytes(), original)

        target, fields = self.warning_form(self.client.post("/restore/" + saved.name))
        self.assertEqual(self.client.post(target, data={**fields, "decision": "restore"}).status_code, 302)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT SUM(points) FROM tournament_scores").fetchone()[0], 1)
            self.assertTrue(con.execute("SELECT closed_at FROM tournaments").fetchone()[0])
        self.client.post(target, data={**fields, "decision": "restore"})
        safety, = self.directory.glob("*.before-restore-*")
        with closing(sqlite3.connect(safety)) as con:
            self.assertEqual(con.execute("SELECT SUM(points) FROM tournament_scores").fetchone()[0], 0)

    def test_restored_invalid_table_remains_locked_and_visible_in_both_round_views(self):
        saved = self.closed_score_backup()
        target, fields = self.warning_form(self.client.post("/restore/" + saved.name))
        self.assertEqual(self.client.post(target, data={**fields, "decision": "restore"}).status_code, 302)
        round_url = "/tournaments/1/rounds/1"
        results = round_url + "/results"
        for path in (round_url, results):
            page = html.fromstring(self.client.get(path).get_data(as_text=True))
            link, = page.xpath("//a[@href=$url]", url=results + "/1")
            self.assertIn("Wertung prüfen", link.text_content())
            self.assertNotIn("✓", link.text_content())
            self.assertIn("Die Punktesumme beträgt 1 und muss 0 sein.", page.text_content())
        table = html.fromstring(self.client.get(results + "/1").get_data(as_text=True))
        self.assertEqual(len(table.xpath("//input[@data-skt='points' and @disabled]")), 4)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT points FROM tournament_scores WHERE tp_id=1").fetchone()[0], 31)

    def test_confirmed_invalid_total_is_flagged_in_standings_and_export(self):
        saved = self.closed_score_backup()
        standings = "/tournaments/1/standings"
        archive = "/tournaments/1/export.zip"
        valid = html.fromstring(self.client.get(standings).get_data(as_text=True))
        self.assertTrue(valid.xpath("//span[normalize-space()='Vollständig']"))
        with ZipFile(BytesIO(self.client.get(archive).data)) as result:
            self.assertIn("Sieger:", result.read("README.txt").decode())
            self.assertNotIn("wertungsstatus", records(result.read("01_gesamtwertung.csv"))[0])

        target, fields = self.warning_form(self.client.post("/restore/" + saved.name))
        self.client.post(target, data={**fields, "decision": "restore"})
        issue = "Runde 1, Tisch 1: Die Punktesumme beträgt 1 und muss 0 sein."
        page = html.fromstring(self.client.get(standings).get_data(as_text=True))
        self.assertIn(issue, page.text_content())
        self.assertFalse(page.xpath("//span[normalize-space()='Vollständig']"))
        self.assertIn("Vorläufiger Endstand", page.text_content())
        self.assertIn("Vorläufige Rangliste", page.text_content())
        self.assertFalse(page.xpath("//span[contains(@class, 'sk-first-place')]"))
        self.assertEqual(page.xpath("//tbody/tr/td[1]/span/text()"), ["#1", "#2", "#2", "#2"])
        csv_rows = records(self.client.get("/tournaments/1/export.csv").data)
        self.assertEqual([row["platz"] for row in csv_rows], ["1", "2", "2", "2"])
        self.assertTrue(all((row["wertungsstatus"], row["pruefhinweis"]) == ("vorläufig", issue) for row in csv_rows))
        with ZipFile(BytesIO(self.client.get(archive).data)) as result:
            info = result.read("README.txt").decode()
            self.assertIn(issue, info)
            self.assertNotIn("Sieger:", info)
            self.assertEqual(records(result.read("01_gesamtwertung.csv")), csv_rows)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT points FROM tournament_scores WHERE tp_id=1").fetchone()[0], 31)

    def test_unreadable_values_after_restore_keep_other_totals_and_rounds_usable(self):
        saved = self.closed_score_backup()
        with closing(sqlite3.connect(saved)) as con, con:
            con.execute("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,2)")
            con.execute("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id)"
                        " SELECT tournament_id,2,table_no,seat,tp_id FROM tournament_seats WHERE round_no=1")
            con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id,points,soli)"
                        " SELECT tournament_id,2,table_no,tp_id,0,1 FROM tournament_scores WHERE round_no=1")
        base = "/tournaments/1"
        cases = [
            ("unlesbar", 2, "punkte", "Die Punkte müssen ganze Zahlen sein.", ("", "3"), ["1", "1", "1", ""], ""),
            (30, "unlesbar", "soli", "Soli müssen nichtnegative ganze Zahlen sein.", ("30", ""), ["1", "2", "2", "2"], "1"),
            (-10, "unlesbar", "soli", "Soli müssen nichtnegative ganze Zahlen sein.", ("-10", ""), ["", "", "", ""], ""),
        ]
        for points, soli, column, reason, totals, places, anna_place in cases:
            with self.subTest(points=points, soli=soli):
                with closing(sqlite3.connect(saved)) as con, con:
                    con.execute("UPDATE tournament_scores SET points=?, soli=? WHERE round_no=1 AND tp_id=1",
                                (points, soli))
                target, fields = self.warning_form(self.client.post("/restore/" + saved.name), reason)
                self.assertEqual(self.client.post(target, data={**fields, "decision": "restore"}).status_code, 302)
                issue = "Runde 1, Tisch 1: " + reason
                view = html.fromstring(self.client.get(base + "/standings").get_data(as_text=True))
                self.assertIn(issue, view.text_content())
                cells = view.xpath("//tr[td//a[@href='/tournaments/1/players/1']]/td")
                self.assertEqual(cells[0].text_content().strip(), "#" + anna_place if anna_place else "–")
                self.assertEqual([cell.text_content().strip() for cell in cells[3:5]],
                                 [value or "nicht berechenbar" for value in totals])
                for rn in (1, 2):
                    page = self.client.get(f"{base}/rounds/{rn}/results/standings").get_data(as_text=True)
                    if rn == 1:
                        self.assertIn(issue, page)
                        self.assertIn("unlesbar", page)
                    else:
                        self.assertNotIn("Vorläufige Platzierungen", page)

                direct = records(self.client.get(base + "/export.csv").data)
                for endpoint in ("/export.zip", "/helfer.zip"):
                    with ZipFile(BytesIO(self.client.get(base + endpoint).data)) as archive:
                        overall = records(archive.read("01_gesamtwertung.csv"))
                        self.assertEqual([r["platz"] for r in overall], places)
                        self.assertTrue(all(r["pruefhinweis"] == issue and r["wertungsstatus"] == "vorläufig"
                                            for r in overall))
                        anna = next(r for r in overall if r["vorname"] == "Anna")
                        self.assertEqual((anna["punkte"], anna["soli"]), totals)
                        first = records(archive.read("rundenwertung/R01.csv"))
                        anna_first = next(r for r in first if r["vorname"] == "Anna")
                        self.assertEqual((anna_first[column], anna_first["platz"], anna_first["pruefhinweis"]),
                                         ("unlesbar", anna_place, issue))
                        second = records(archive.read("rundenwertung/R02.csv"))
                        self.assertTrue(all(r["platz"] == "1" and "wertungsstatus" not in r for r in second))
                        if endpoint == "/export.zip":
                            self.assertEqual(direct, overall)
                            overview = records(archive.read("03_gesamtuebersicht.csv"))[0]
                            self.assertEqual((overview["platz_gesamt"], overview[column + "_gesamt"],
                                              overview[column + "_r1"], overview["pruefhinweis"]),
                                             (anna_place, "", "unlesbar", issue))
                            for entry in ("04_scores_komplett.csv", "05_pivot_vorlage_scores.csv", "tische/R01_T01.csv"):
                                raw = next(r for r in records(archive.read(entry)) if r["nr"] == "1" and r["runde"] == "1")
                                self.assertEqual(raw[column], "unlesbar")
                            summary = archive.read("README.txt").decode()
                            self.assertIn(issue, summary)
                            self.assertNotIn("Sieger:", summary)
                            self.assertIn("Summe " + column.capitalize() + ": nicht berechenbar", summary)
                with closing(db.connect()) as con:
                    actual = con.execute("SELECT points,soli FROM tournament_scores WHERE round_no=1 AND tp_id=1")
                    self.assertEqual(tuple(actual.fetchone()), (points, soli))

    def test_uploaded_warning_is_bound_to_session_file_and_its_checked_contents(self):
        saved = self.closed_score_backup()
        source = saved.read_bytes()
        original = self.live.read_bytes()
        target, fields = self.warning_form(self.client.post("/restore-upload", data={
            "backup_file": (BytesIO(source), "fremde-sicherung.sqlite3"),
        }))
        self.assertIn("/restore/skt-upload-", target)
        confirm = {**fields, "decision": "restore"}
        self.client.post("/restore/" + saved.name, data=confirm)
        other = form_client(self.app)
        other.post(target, data={k: v for k, v in confirm.items() if k != "csrf_token"})
        self.assertEqual(self.live.read_bytes(), original)

        uploaded, = self.backups.glob("skt-upload-*.sqlite3")
        with closing(sqlite3.connect(uploaded)) as con, con:
            con.execute("UPDATE tournament_scores SET points=points-1 WHERE tp_id=1")
        response = self.client.post(target, data=confirm, follow_redirects=True)
        self.assertIn("seit der Warnung geändert", response.get_data(as_text=True))
        self.assertEqual(self.live.read_bytes(), original)
        self.assertFalse(list(self.directory.glob("*.before-restore-*")))
        self.assertEqual(saved.read_bytes(), source)

        target, fields = self.warning_form(self.client.post("/restore-upload", data={
            "backup_file": (BytesIO(source), "archiv.sqlite3"),
        }))
        self.client.post(target, data={**fields, "decision": "restore"})
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT SUM(points) FROM tournament_scores").fetchone()[0], 1)

    def test_confirmation_expires_after_restore_in_another_session(self):
        saved = self.closed_score_backup()
        target, fields = self.warning_form(self.client.post("/restore/" + saved.name))
        other = form_client(self.app)
        good = db.backup_db(self.backups)
        self.assertEqual(other.post("/restore/" + good.name).status_code, 302)
        previous = self.live.read_bytes()
        result = self.client.post(target, data={**fields, "decision": "restore"}, follow_redirects=True)
        self.assertIn("Inzwischen wurde eine andere Wiederherstellung durchgeführt", result.get_data(as_text=True))
        self.assertEqual(self.live.read_bytes(), previous)
        self.assertEqual(len(list(self.directory.glob("*.before-restore-*"))), 1)

    def test_open_tournament_with_missing_results_restores_directly(self):
        saved = self.closed_score_backup()
        with closing(sqlite3.connect(saved)) as con, con:
            con.execute("UPDATE tournaments SET closed_at=NULL")
            con.execute("DELETE FROM tournament_scores WHERE tp_id=1")
        self.assertEqual(self.client.post("/restore/" + saved.name).status_code, 302)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournament_scores").fetchone()[0], 3)
            self.assertIsNone(con.execute("SELECT closed_at FROM tournaments").fetchone()[0])

    def test_warning_lists_every_affected_closed_tournament(self):
        saved = self.closed_score_backup()
        with closing(sqlite3.connect(saved)) as con, con:
            con.execute("UPDATE tournament_scores SET points=points-1, soli=41 WHERE tp_id=1")
            con.execute("INSERT INTO tournaments(title,event_date,start_time,closed_at)"
                        " VALUES('Früheres Turnier','2025-11-14','18:00','2025-11-14')")
        original = self.live.read_bytes()
        response = self.client.post("/restore/" + saved.name)
        page = html.fromstring(response.get_data(as_text=True))
        notices = page.xpath("//div[contains(@class,'alert-warning')]//li/text()")
        self.assertEqual(len(notices), 2)
        self.assertIn("Turnier 1 · Freitagsturnier: Runde 1, Tisch 1", notices[0])
        self.assertIn("41 Soli", notices[0])
        self.assertIn("Turnier 2 · Früheres Turnier: Es wurde noch keine Runde ausgelost.", notices[1])
        self.assertEqual(self.live.read_bytes(), original)

    def test_v7_signup_is_active_and_withdrawal_survives_backup_and_restart(self):
        self.client.post("/tournaments/new", data={
            "title": "Vereinsturnier", "event_date": "2026-11-12", "start_time": "18:00",
        })
        self.client.post("/tournaments/1/participants/quickadd", data={
            "vorname": "Anna", "nachname": "Probe", "wohnort": "Testort",
        })
        old = db.backup_db(self.backups)
        with closing(sqlite3.connect(old)) as con, con:
            con.execute("ALTER TABLE tournament_participants DROP COLUMN withdrawn_at")
            con.execute("UPDATE meta SET v='7' WHERE k='schema_version'")
        db.restore_db(old)
        with closing(db.connect()) as con:
            self.assertIsNone(con.execute("SELECT withdrawn_at FROM tournament_participants").fetchone()[0])
        self.client.post("/tournaments/1/participants/1/withdraw", data={"expected_address_id": "1"})
        saved = db.backup_db(self.backups)
        db.restore_db(old)
        db.restore_db(saved)
        db.init_db(self.live)
        with closing(db.connect()) as con:
            self.assertEqual(tuple(con.execute("SELECT player_no, withdrawn_at IS NOT NULL FROM tournament_participants").fetchone()), (1, 1))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM audit_log WHERE action='withdraw'").fetchone()[0], 1)

    def test_legacy_invites_keep_selection_after_upgrade_quickadd_and_restart(self):
        with closing(db.connect()) as con, con:
            con.execute("ALTER TABLE addresses DROP COLUMN invite")
            con.execute("ALTER TABLE addresses ADD COLUMN invite INTEGER")
            con.execute("UPDATE meta SET v='4' WHERE k='schema_version'")
            con.executemany("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort,invite) "
                            "VALUES(1,?,'Test','Ort',?)", [("Leer", None), ("Aus", 0), ("An", 1)])
        db.init_db(self.live)
        self.client.post("/tournaments/new", data={"title": "Probe", "event_date": "2026-11-12", "start_time": "18:00"})
        result = self.client.post("/tournaments/1/participants/quickadd", data={
            "nachname": "Schnellerfassung", "vorname": "Test", "wohnort": "Ort",
        })
        self.assertEqual(result.status_code, 302)
        db.init_db(self.live)
        with closing(db.connect()) as con:
            self.assertEqual(dict(con.execute("SELECT nachname,invite FROM addresses")),
                             {"Leer": 0, "Aus": 0, "An": 1, "Schnellerfassung": 1})

    def test_database_without_invite_column_keeps_previous_default(self):
        with closing(db.connect()) as con, con:
            con.execute("ALTER TABLE addresses DROP COLUMN invite")
            con.execute("UPDATE meta SET v='4' WHERE k='schema_version'")
            con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort) VALUES(1,'Alt','Ada','Ort')")
        db.init_db(self.live)
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort) VALUES(1,'Neu','Ben','Ort')")
            self.assertEqual(dict(con.execute("SELECT nachname,invite FROM addresses")), {"Alt": 1, "Neu": 1})

    def test_restore_migrates_before_activation_and_keeps_safety_copy(self):
        legacy = db.backup_db(self.backups)
        with closing(sqlite3.connect(legacy)) as con, con:
            con.execute("UPDATE meta SET v='1' WHERE k='schema_version'")
            con.execute("DELETE FROM meta WHERE k LIKE 'migr_years_to_markers%'")
            con.execute("DROP TABLE audit_log")
            con.execute("INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort,tournament_years)"
                        " VALUES (1,'Alt','Test','Testort','1999,123')")
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO tournaments(title,event_date,start_time) VALUES ('Aktuell','2026-11-12','18:00')")
        before = legacy.read_bytes()
        result = self.client.post("/restore-upload", data={"backup_file": (BytesIO(before), "archiv.sqlite3")})
        self.assertEqual(result.status_code, 302)
        with closing(db.connect()) as con:
            self.assertEqual(set(con.execute("SELECT tournament_years FROM addresses").fetchone()[0].split(",")),
                             {"991228sfb1", "123"})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0], 0)
        safety, = self.directory.glob("turnier.before-restore-*.sqlite3")
        with closing(sqlite3.connect(safety)) as con:
            self.assertEqual(con.execute("SELECT title FROM tournaments").fetchone()[0], "Aktuell")
        self.assertEqual(before, legacy.read_bytes())

    def test_restore_waits_for_writer_and_copies_its_entire_commit(self):
        saved = db.backup_db(self.backups)
        reached_lock = Event()
        connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            con = connect(*args, **kwargs)

            def signal_lock(sql):
                if sql.strip().upper() == "BEGIN EXCLUSIVE":
                    reached_lock.set()

            con.set_trace_callback(signal_lock)
            return con

        with closing(db.connect()) as writer, ThreadPoolExecutor(max_workers=1) as pool:
            writer.execute("PRAGMA busy_timeout=1000")
            writer.execute("BEGIN IMMEDIATE")
            insert = "INSERT INTO tournaments(title,event_date,start_time) VALUES(?,'2026-11-13','18:00')"
            writer.execute(insert, ("Erste Buchung",))
            with patch.object(sqlite3, "connect", traced_connect):
                restore = pool.submit(db.restore_db, saved)
                try:
                    self.assertTrue(reached_lock.wait(3))
                    writer.execute(insert, ("Zweite Buchung",))
                    writer.commit()
                finally:
                    writer.rollback()
                restore.result(timeout=8)

        with closing(db.connect()) as active:
            self.assertEqual(active.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0], 0)
        safety, = self.directory.glob("*.before-restore-*.sqlite3")
        with closing(sqlite3.connect(safety)) as previous:
            self.assertEqual(previous.execute("SELECT title FROM tournaments ORDER BY id").fetchall(),
                             [("Erste Buchung",), ("Zweite Buchung",)])

    def test_restore_reports_a_persistent_lock_then_allows_retry(self):
        saved = db.backup_db(self.backups)
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO tournaments(title,event_date,start_time) VALUES('Aktuell','2026-11-13','18:00')")
        before = self.live.read_bytes()
        with closing(db.connect()) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with patch.object(db, "monotonic", side_effect=[0, 6]):
                response = self.client.post("/restore/" + saved.name, follow_redirects=True)
            self.assertIn("Die Datenbank ist noch in Benutzung.", response.get_data(as_text=True))
            self.assertFalse(list(self.directory.glob("*.before-restore-*.sqlite3")))
            self.assertEqual(self.live.read_bytes(), before)
        self.assertEqual(self.client.post("/restore/" + saved.name).status_code, 302)
        self.assertEqual(len(list(self.directory.glob("*.before-restore-*.sqlite3"))), 1)
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0], 0)

    def test_restore_removes_only_logs_for_missing_tournaments(self):
        legacy = db.backup_db(self.backups)
        with closing(sqlite3.connect(legacy)) as con, con:
            con.execute("DROP TRIGGER delete_tournament_audit")
            con.execute("UPDATE meta SET v='5' WHERE k='schema_version'")
            con.execute("INSERT INTO tournaments(id,title,event_date,start_time) VALUES(1,'Probe','2026-11-12','18:00')")
            con.executemany("INSERT INTO audit_log(tournament_id, action) VALUES(?,'add')", [(1,), (12,), (12,)])
        db.restore_db(legacy)
        db.init_db(self.live)
        with closing(db.connect()) as con, con:
            self.assertEqual(list(map(tuple, con.execute("SELECT id,tournament_id FROM audit_log"))), [(1, 1)])
            con.execute("DELETE FROM tournaments WHERE id=1")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)
        with closing(sqlite3.connect(legacy)) as original:
            self.assertEqual(original.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 3)

    def test_invalid_backups_preserve_active_database(self):
        invalid = self.directory / "fremd.sqlite3"
        with closing(sqlite3.connect(invalid)) as con:
            con.execute("CREATE TABLE other(id INTEGER)")
        future = db.backup_db(self.backups)
        with closing(sqlite3.connect(future)) as con, con:
            con.execute("UPDATE meta SET v='99' WHERE k='schema_version'")
        incomplete = db.backup_db(self.backups)
        with closing(sqlite3.connect(incomplete)) as con, con:
            con.execute("DROP TABLE audit_log")
        missing_scores = db.backup_db(self.backups)
        with closing(sqlite3.connect(missing_scores)) as con, con:
            con.execute("DROP TABLE tournament_scores")
        before = self.live.read_bytes()
        for path in (invalid, future, incomplete, missing_scores):
            with self.subTest(path=path.name):
                with self.assertRaises(ValueError):
                    db.restore_db(path)
                self.assertEqual(self.live.read_bytes(), before)
        self.assertFalse(list(self.directory.glob("*.before-restore-*.sqlite3")))

    def test_failed_migration_rolls_back_the_entire_upgrade(self):
        old = db.backup_db(self.backups)
        with closing(sqlite3.connect(old)) as con, con:
            con.execute("DROP TABLE audit_log")
            con.execute("UPDATE meta SET v='1' WHERE k='schema_version'")
            con.execute("DELETE FROM meta WHERE k LIKE 'migr_years_to_markers%'")
            con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name)"
                        " VALUES (999,1,999,'Fehlender Verweis')")
        with self.assertRaises(ValueError):
            db.init_db(old)
        with closing(sqlite3.connect(old)) as con:
            self.assertEqual(con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0], "1")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM meta WHERE k='migr_years_to_markers'").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='audit_log'").fetchone()[0], 0)
        with closing(db.connect()) as con:
            active = Path(con.execute("PRAGMA database_list").fetchone()["file"])
            self.assertTrue(active.samefile(self.live))

    def test_backups_include_committed_wal_rows_only(self):
        with closing(db.connect()) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            insert = "INSERT INTO tournaments(title,event_date,start_time) VALUES (?,'2026-11-12','18:00')"
            writer.execute(insert, ("Bestätigt",))
            writer.commit()
            writer.execute(insert, ("In Bearbeitung",))
            first = db.backup_db(self.backups)
            writer.commit()
            second = db.backup_db(self.backups)
        self.assertNotEqual(first, second)
        for file, expected in ((first, 1), (second, 2)):
            with closing(sqlite3.connect(file)) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0], expected)
