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
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)

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
