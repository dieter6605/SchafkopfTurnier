import sqlite3
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from app import db
from app.web import create_app
from tests.form_client import form_client


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
