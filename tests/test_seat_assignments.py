import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from app import db


class SeatAssignmentTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "turnier.sqlite3"
        db.init_db(self.path)
        self.con = db.connect()
        self.addCleanup(self.con.close)
        with self.con:
            for number in (1, 2):
                self.con.execute("INSERT INTO tournaments(id,title,event_date,start_time) VALUES(?,'Probe','2026-11-12','18:00')", (number,))
            for player, tournament in ((1, 1), (2, 1), (3, 1), (4, 2)):
                self.con.execute("INSERT INTO addresses(id,addressbook_id,nachname,vorname,wohnort) VALUES(?,1,'Probe','Ada','Ort')", (player,))
                self.con.execute("INSERT INTO tournament_participants(id,tournament_id,player_no,address_id,display_name) VALUES(?,?,?,?,'Probe')",
                                 (player, tournament, player, player))
            self.con.executemany("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(?,?)",
                                 [(1, 1), (1, 2), (2, 1)])
            self.con.executemany("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id) VALUES(?,?,?,'A',?)",
                                 [(1, 1, 1, 1), (1, 1, 2, 2), (1, 2, 2, 2), (2, 1, 1, 4)])

    def test_score_requires_its_own_tournament_round_table_and_player_on_insert_and_update(self):
        insert = "INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id) VALUES(?,?,?,?)"
        update = "UPDATE tournament_scores SET tournament_id=?,round_no=?,table_no=?,tp_id=? WHERE id=1"
        invalid = [(2, 1, 1, 1), (1, 2, 1, 1), (1, 1, 2, 1), (1, 1, 1, 2), (1, 1, 1, 3)]
        for key in invalid:
            with self.subTest(insert=key), self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"), self.con:
                self.con.execute(insert, key)
        with self.con:
            self.con.execute(insert, (1, 1, 1, 1))
        for key in invalid:
            with self.subTest(update=key), self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"), self.con:
                self.con.execute(update, key)
        self.assertEqual(tuple(self.con.execute("SELECT tournament_id,round_no,table_no,tp_id FROM tournament_scores").fetchone()),
                         (1, 1, 1, 1))

    def test_seat_needs_matching_round_and_signup_and_cannot_leave_an_existing_score(self):
        insert = "INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id) VALUES(1,?,1,'B',?)"
        with self.con:
            self.con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id) VALUES(1,1,1,1)")
        for sql, args in [
            (insert, (1, 4)), (insert, (9, 3)),
            ("UPDATE tournament_seats SET tp_id=4 WHERE tp_id=2 AND round_no=1", ()),
            ("UPDATE tournament_seats SET table_no=3 WHERE tp_id=1", ()),
            ("DELETE FROM tournament_seats WHERE tp_id=1", ()),
            ("DELETE FROM tournament_rounds WHERE tournament_id=1 AND round_no=1", ()),
        ]:
            with self.subTest(sql=sql, args=args), self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"), self.con:
                self.con.execute(sql, args)
        with self.con:
            self.con.execute("UPDATE tournament_scores SET points=-10 WHERE id=1")
            self.con.execute("DELETE FROM tournaments WHERE id=1")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tournament_scores").fetchone()[0], 0)
        self.assertEqual(list(map(tuple, self.con.execute("SELECT tournament_id,tp_id FROM tournament_seats"))), [(2, 4)])

    def test_upgrade_keeps_records_and_previously_used_ids(self):
        with self.con:
            self.con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id,points,soli) VALUES(1,1,1,1,-10,2)")
            self.con.execute("UPDATE sqlite_sequence SET seq=50 WHERE name IN ('tournament_seats','tournament_scores')")
            self.con.execute("UPDATE meta SET v='6' WHERE k='schema_version'")
        tables = ("tournament_seats", "tournament_scores")
        before = {t: self.con.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in tables}
        for _ in range(2):
            db.init_db(self.path)
            for table in tables:
                self.assertEqual(self.con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall(), before[table])
        with self.con:
            seat = self.con.execute("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id) VALUES(1,1,1,'B',3)")
            score = self.con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id) VALUES(1,1,1,3)")
            self.assertEqual((seat.lastrowid, score.lastrowid), (51, 51))

    def test_invalid_legacy_data_blocks_upgrade_and_restore(self):
        for sql, message in [
            ("UPDATE tournament_seats SET tp_id=4 WHERE tp_id=1", "Sitzplatz"),
            ("UPDATE tournament_seats SET round_no=9 WHERE tp_id=1", "Sitzplatz"),
            ("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id) VALUES(1,1,2,1)", "Ergebnis"),
        ]:
            with self.subTest(sql=sql):
                backup = db.backup_db(self.path.parent / "backups")
                with closing(sqlite3.connect(backup)) as old, old:
                    old.execute("PRAGMA foreign_keys=OFF")
                    old.execute("UPDATE meta SET v='6' WHERE k='schema_version'")
                    old.execute(sql)
                before = backup.read_bytes()
                active = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, message):
                    db.init_db(backup)
                with self.assertRaisesRegex(ValueError, message):
                    db.restore_db(backup)
                self.assertEqual(backup.read_bytes(), before)
                self.assertEqual(self.path.read_bytes(), active)

    def test_restore_rejects_current_version_with_missing_score_reference(self):
        backup = db.backup_db(self.path.parent / "backups")
        with closing(sqlite3.connect(backup)) as old, old:
            old.execute("ALTER TABLE tournament_scores RENAME TO old_scores")
            old.execute("CREATE TABLE tournament_scores AS SELECT * FROM old_scores")
            old.execute("DROP TABLE old_scores")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Verknüpfung"):
            db.restore_db(backup)
        self.assertEqual(self.path.read_bytes(), before)
