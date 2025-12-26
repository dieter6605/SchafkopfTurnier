# app/db.py
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

_DB_PATH: Optional[Path] = None


# -----------------------------------------------------------------------------
# Connection handling
# -----------------------------------------------------------------------------
def set_db_path(path: Path) -> None:
    global _DB_PATH
    _DB_PATH = Path(path)


def connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("DB path not set. Call set_db_path(...) first.")
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def one(con: sqlite3.Connection, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    return con.execute(sql, params).fetchone()


def q(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(con.execute(sql, params))


# -----------------------------------------------------------------------------
# Schema helpers
# -----------------------------------------------------------------------------
def _has_table(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(r)


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    if not _has_table(con, table):
        return False
    rows = con.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == column for r in rows)


# -----------------------------------------------------------------------------
# Init / Migration
# -----------------------------------------------------------------------------
def init_db(db_path: Path) -> None:
    """
    Minimales, erweiterbares Basisschema:
    - meta (schema_version)
    - addressbooks, wohnorte, addresses
    - tournaments, tournament_participants
    - tournament_rounds, tournament_seats (Auslosung Sitzplan pro Runde)
    - tournament_scores (Ergebnisse)
    """
    set_db_path(db_path)

    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );
            INSERT OR IGNORE INTO meta(k,v) VALUES ('schema_version','1');

            -- Addressbooks
            CREATE TABLE IF NOT EXISTS addressbooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Wohnorte Lookup
            CREATE TABLE IF NOT EXISTS wohnorte (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wohnort TEXT NOT NULL UNIQUE,
                plz TEXT NOT NULL,
                ort TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_wohnorte_wohnort ON wohnorte(wohnort);

            -- Addresses (ohne adressen_id!)
            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                addressbook_id INTEGER NOT NULL,

                nachname TEXT NOT NULL,
                vorname TEXT NOT NULL,

                wohnort TEXT NOT NULL,
                plz TEXT,
                ort TEXT,

                strasse TEXT,
                hausnummer TEXT,

                email TEXT,
                telefon TEXT,

                invite INTEGER NOT NULL DEFAULT 1,

                status TEXT NOT NULL DEFAULT 'aktiv',  -- aktiv|inaktiv|verstorben|...
                notizen TEXT,

                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),

                participation_count INTEGER NOT NULL DEFAULT 0,
                last_tournament_at TEXT,
                tournament_years TEXT,

                FOREIGN KEY(addressbook_id) REFERENCES addressbooks(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_addresses_ab ON addresses(addressbook_id);
            CREATE INDEX IF NOT EXISTS idx_addresses_name ON addresses(nachname, vorname);
            CREATE INDEX IF NOT EXISTS idx_addresses_wohnort ON addresses(wohnort);
            CREATE INDEX IF NOT EXISTS idx_addresses_email ON addresses(email);

            -- Tournaments
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                description TEXT,
                location TEXT,
                organizer TEXT,
                min_participants INTEGER,
                max_participants INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_tournaments_event_date ON tournaments(event_date);

            -- Tournament participants
            CREATE TABLE IF NOT EXISTS tournament_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                player_no INTEGER NOT NULL,
                address_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,

                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),

                UNIQUE(tournament_id, player_no),
                UNIQUE(tournament_id, address_id),

                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(address_id) REFERENCES addresses(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_tp_tournament ON tournament_participants(tournament_id);
            CREATE INDEX IF NOT EXISTS idx_tp_address ON tournament_participants(address_id);

            -- -----------------------------------------------------------------
            -- Runden / Sitzplan
            -- -----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS tournament_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_no INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(tournament_id, round_no),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tr_tournament ON tournament_rounds(tournament_id);

            CREATE TABLE IF NOT EXISTS tournament_seats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_no INTEGER NOT NULL,
                table_no INTEGER NOT NULL,
                seat TEXT NOT NULL,          -- 'A'|'B'|'C'|'D'
                tp_id INTEGER NOT NULL,      -- tournament_participants.id
                created_at TEXT NOT NULL DEFAULT (datetime('now')),

                UNIQUE(tournament_id, round_no, table_no, seat),
                UNIQUE(tournament_id, round_no, tp_id),

                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(tp_id) REFERENCES tournament_participants(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_ts_round ON tournament_seats(tournament_id, round_no);
            CREATE INDEX IF NOT EXISTS idx_ts_tp ON tournament_seats(tp_id);

            -- -----------------------------------------------------------------
            -- Ergebnisse pro Runde / Spieler (Punkte + Soli)
            -- -----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS tournament_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_no INTEGER NOT NULL,
                table_no INTEGER NOT NULL,
                tp_id INTEGER NOT NULL,                -- tournament_participants.id

                points INTEGER NOT NULL DEFAULT 0,
                soli   INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),

                UNIQUE(tournament_id, round_no, tp_id),

                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY(tp_id) REFERENCES tournament_participants(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sc_round ON tournament_scores(tournament_id, round_no, table_no);
            CREATE INDEX IF NOT EXISTS idx_sc_tp    ON tournament_scores(tp_id);
            """
        )

        if _has_table(con, "addresses") and not _has_column(con, "addresses", "invite"):
            con.execute("ALTER TABLE addresses ADD COLUMN invite INTEGER;")
            con.execute("UPDATE addresses SET invite=1 WHERE invite IS NULL;")

        ab = one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
        if not ab:
            any_ab = one(con, "SELECT id FROM addressbooks LIMIT 1")
            if not any_ab:
                con.execute("INSERT INTO addressbooks(name, is_default) VALUES (?,1)", ("Standard",))
            else:
                con.execute("UPDATE addressbooks SET is_default=1 WHERE id=?", (int(any_ab["id"]),))

        con.commit()


# -----------------------------------------------------------------------------
# Backup / Restore
# -----------------------------------------------------------------------------
def backup_db(backup_dir: Path) -> Path:
    """
    Erstellt ein timestamped Backup der SQLite-Datei (copy).
    """
    if _DB_PATH is None:
        raise RuntimeError("DB path not set.")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"skt-backup-{ts}.sqlite3"
    shutil.copy2(_DB_PATH, target)
    return target


def restore_db(backup_file: Path) -> None:
    """
    Stellt ein Backup wieder her, indem es die aktuelle DB-Datei ersetzt.

    Hinweis:
    - Es darf dabei keine offene Verbindung auf die DB-Datei bestehen.
    - Danach ggf. die App neu starten/neu laden.
    """
    if _DB_PATH is None:
        raise RuntimeError("DB path not set.")

    backup_file = Path(backup_file)
    if not backup_file.exists() or not backup_file.is_file():
        raise FileNotFoundError(str(backup_file))

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Safety copy der aktuellen DB
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safety = _DB_PATH.with_name(f"{_DB_PATH.stem}.before-restore-{ts}{_DB_PATH.suffix}")
    if _DB_PATH.exists():
        shutil.copy2(_DB_PATH, safety)

    # Restore
    shutil.copy2(backup_file, _DB_PATH)