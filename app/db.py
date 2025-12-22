# app/db.py
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_DB_PATH: Optional[Path] = None


def set_db_path(path: Path) -> None:
    global _DB_PATH
    _DB_PATH = Path(path)


def connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("DB path not set. Call init_db(...) or set_db_path(...) first.")
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def one(con: sqlite3.Connection, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    return con.execute(sql, params).fetchone()


def q(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(con.execute(sql, params))


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)).fetchone()
    return bool(r)


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    if not _has_table(con, table):
        return False
    rows = con.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == column for r in rows)


# -----------------------------------------------------------------------------
# Schema (v1) – ohne adressen_id (komplett raus)
# -----------------------------------------------------------------------------
def init_db(db_path: Path) -> None:
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

            -- Wohnorte (Lookup)
            CREATE TABLE IF NOT EXISTS wohnorte (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wohnort TEXT NOT NULL UNIQUE,
                plz TEXT NOT NULL,
                ort TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_wohnorte_wohnort ON wohnorte(wohnort);

            -- Addresses (Adressbuch)
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

                status TEXT NOT NULL DEFAULT 'aktiv',
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

            -- Tournament participants (müssen IMMER im Adressbuch existieren)
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
            """
        )

        # Default Addressbook
        ab = one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
        if not ab:
            con.execute("INSERT OR IGNORE INTO addressbooks(name, is_default) VALUES (?,1)", ("Standard",))

        con.commit()


# -----------------------------------------------------------------------------
# Backup (SQLite-Datei kopieren, offline-safe)
# -----------------------------------------------------------------------------
def backup_db(backup_dir: Path) -> Path:
    if _DB_PATH is None:
        raise RuntimeError("DB path not set.")
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"skt_backup_{ts}.sqlite3"

    # saubere Kopie: kurz exclusive lock vermeiden, indem wir vorher checken
    # (Für lokale Einzeluser reicht shutil.copy2 typischerweise.)
    shutil.copy2(_DB_PATH, target)
    return target


def row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        return default