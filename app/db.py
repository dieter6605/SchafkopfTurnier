# app/db.py
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
from uuid import uuid4

_DB_PATH: Optional[Path] = None
SCHEMA_VERSION = 8


_CREATE_SEATS = """
CREATE TABLE IF NOT EXISTS tournament_seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    table_no INTEGER NOT NULL,
    seat TEXT NOT NULL,
    tp_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(tournament_id, round_no, table_no, seat),
    UNIQUE(tournament_id, round_no, tp_id),
    UNIQUE(tournament_id, round_no, table_no, tp_id),

    FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY(tournament_id, tp_id) REFERENCES tournament_participants(tournament_id, id) ON DELETE CASCADE,
    FOREIGN KEY(tournament_id, round_no) REFERENCES tournament_rounds(tournament_id, round_no)
)
"""

_CREATE_SCORES = """
CREATE TABLE IF NOT EXISTS tournament_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    table_no INTEGER NOT NULL,
    tp_id INTEGER NOT NULL,

    points INTEGER NOT NULL DEFAULT 0,
    soli   INTEGER NOT NULL DEFAULT 0,

    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(tournament_id, round_no, tp_id),

    FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY(tp_id) REFERENCES tournament_participants(id) ON DELETE CASCADE,
    FOREIGN KEY(tournament_id, round_no, table_no, tp_id)
        REFERENCES tournament_seats(tournament_id, round_no, table_no, tp_id)
)
"""


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

    # Wichtig: SQLite erzwingt FKs nur, wenn diese PRAGMA pro Verbindung aktiv ist.
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


def _foreign_keys(con: sqlite3.Connection, table: str) -> set[tuple]:
    rows = con.execute("""
        SELECT id, "table", "from", "to", on_update, on_delete
        FROM pragma_foreign_key_list(?) ORDER BY id, seq
    """, (table,))
    return {tuple(row[1:] for row in columns) for _, columns in groupby(rows, key=lambda row: row[0])}


def _ensure_column(con: sqlite3.Connection, table: str, column: str, coldef: str) -> None:
    """
    Fügt eine Spalte per ALTER TABLE hinzu, falls sie in einer Bestands-DB fehlt.
    Wichtig: SQLite kann nur ADD COLUMN, keine Constraints nachträglich.
    """
    if not _has_table(con, table):
        return
    if _has_column(con, table, column):
        return
    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef};")


def _get_schema_version(con: sqlite3.Connection) -> int:
    if not _has_table(con, "meta"):
        return 0
    r = one(con, "SELECT v FROM meta WHERE k=?", ("schema_version",))
    if not r:
        return 0
    try:
        return int(str(r["v"]).strip())
    except Exception:
        return 0


def _set_schema_version(con: sqlite3.Connection, v: int) -> None:
    con.execute(
        "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        ("schema_version", str(int(v))),
    )


# -----------------------------------------------------------------------------
# Migration: years -> markers (JJMMTTssss)
# Altbestand enthält Jahreszahlen in:
# - addresses.tournament_years (CSV)
# - addresses.last_tournament_at (Jahr)
#
# Vorgabe:
# - MMTT = 1228
# - Suffix = "sfb1"
# => year 2024 -> "241228sfb1"
# -----------------------------------------------------------------------------
_MIG_MMTT = "1228"
_MIG_SUFFIX = "sfb1"


def _is_old_year_token(token: str) -> bool:
    t = (token or "").strip()
    return len(t) == 4 and t.isdigit() and 1900 <= int(t) <= 3000


def _is_marker_token(token: str) -> bool:
    t = (token or "").strip()
    if len(t) != 10:
        return False
    if not t.isalnum():
        return False
    # Erwartung: JJMMTT.... (erste 6 Stellen numerisch)
    if not t[:6].isdigit():
        return False
    return True


def _year_to_marker(year: int) -> str:
    yy = int(year) % 100
    # JJ + MMTT + suffix(4)
    return f"{yy:02d}{_MIG_MMTT}{_MIG_SUFFIX}"


def _migrate_years_to_markers_once(con: sqlite3.Connection) -> None:
    """
    Einmalige Migration der Alt-Daten:
    - addresses.tournament_years: Jahresliste -> Markerliste (JJ1228sfb1)
    - addresses.last_tournament_at: Jahr -> Marker (JJ1228sfb1)

    Wird über meta-Flag 'migr_years_to_markers' = '1' abgesichert.
    """
    if not _has_table(con, "meta"):
        return
    if not _has_table(con, "addresses"):
        return
    if not _has_column(con, "addresses", "tournament_years") or not _has_column(con, "addresses", "last_tournament_at"):
        return

    done = one(con, "SELECT v FROM meta WHERE k=?", ("migr_years_to_markers",))
    if done and (done["v"] or "") == "1":
        return

    rows = q(con, "SELECT id, tournament_years, last_tournament_at FROM addresses")

    changed = 0
    for r in rows:
        addr_id = int(r["id"])

        # --- tournament_years ---
        ty_raw = (r["tournament_years"] or "").strip()
        new_ty = ty_raw

        if ty_raw:
            parts = [p.strip() for p in ty_raw.split(",") if p.strip()]
            markers: list[str] = []
            seen: set[str] = set()

            for p in parts:
                if _is_old_year_token(p):
                    m = _year_to_marker(int(p))
                else:
                    # Unklare historische Einträge beim Wiederherstellen erhalten.
                    m = p

                if m not in seen:
                    seen.add(m)
                    markers.append(m)

            # sortieren nach Datum im Marker (JJMMTT....) – stabil/lesbar
            markers.sort()
            new_ty = ",".join(markers)

        # --- last_tournament_at ---
        lt_raw = (r["last_tournament_at"] or "").strip()
        new_lt = lt_raw
        if lt_raw:
            if _is_old_year_token(lt_raw):
                new_lt = _year_to_marker(int(lt_raw))
            elif _is_marker_token(lt_raw):
                new_lt = lt_raw
            else:
                # unbekanntes Format: nicht anfassen
                new_lt = lt_raw

        if new_ty != ty_raw or new_lt != lt_raw:
            con.execute(
                """
                UPDATE addresses
                SET tournament_years=?,
                    last_tournament_at=?,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (new_ty, new_lt, addr_id),
            )
            changed += 1

    con.execute(
        "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        ("migr_years_to_markers", "1"),
    )
    con.execute(
        "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        ("migr_years_to_markers_rows", str(changed)),
    )


# -----------------------------------------------------------------------------
# Migration v2: Anmeldungen und Runden mit FKs/UNIQUEs absichern
# -----------------------------------------------------------------------------
def _migrate_tournament_tables_v2(con: sqlite3.Connection) -> None:
    """Sitzplätze und Ergebnisse werden beim Umstieg auf v7 gemeinsam umgebaut."""
    # --- tournament_participants ---
    con.execute("ALTER TABLE tournament_participants RENAME TO tournament_participants_old;")
    con.execute(
        """
        CREATE TABLE tournament_participants (
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
        """
    )
    if _has_column(con, "tournament_participants_old", "updated_at"):
        con.execute(
            """
            INSERT INTO tournament_participants(id,tournament_id,player_no,address_id,display_name,created_at,updated_at)
            SELECT id,tournament_id,player_no,address_id,display_name,created_at,updated_at
            FROM tournament_participants_old;
            """
        )
    else:
        con.execute(
            """
            INSERT INTO tournament_participants(id,tournament_id,player_no,address_id,display_name,created_at,updated_at)
            SELECT id,tournament_id,player_no,address_id,display_name,created_at,datetime('now')
            FROM tournament_participants_old;
            """
        )
    con.execute("DROP TABLE tournament_participants_old;")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tp_tournament ON tournament_participants(tournament_id);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tp_address   ON tournament_participants(address_id);")

    # --- tournament_rounds ---
    con.execute("ALTER TABLE tournament_rounds RENAME TO tournament_rounds_old;")
    con.execute(
        """
        CREATE TABLE tournament_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_no INTEGER NOT NULL,

            -- ✅ NEU: deterministische Auslosungsmetadaten
            draw_seed INTEGER,
            draw_attempt INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL DEFAULT (datetime('now')),

            UNIQUE(tournament_id, round_no),
            FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        """
    )

    has_seed = _has_column(con, "tournament_rounds_old", "draw_seed")
    has_attempt = _has_column(con, "tournament_rounds_old", "draw_attempt")

    if has_seed and has_attempt:
        con.execute(
            """
            INSERT INTO tournament_rounds(id,tournament_id,round_no,draw_seed,draw_attempt,created_at)
            SELECT id,tournament_id,round_no,draw_seed,COALESCE(draw_attempt,0),created_at
            FROM tournament_rounds_old;
            """
        )
    elif has_seed and (not has_attempt):
        con.execute(
            """
            INSERT INTO tournament_rounds(id,tournament_id,round_no,draw_seed,draw_attempt,created_at)
            SELECT id,tournament_id,round_no,draw_seed,0,created_at
            FROM tournament_rounds_old;
            """
        )
    else:
        con.execute(
            """
            INSERT INTO tournament_rounds(id,tournament_id,round_no,draw_seed,draw_attempt,created_at)
            SELECT id,tournament_id,round_no,NULL,0,created_at
            FROM tournament_rounds_old;
            """
        )

    con.execute("DROP TABLE tournament_rounds_old;")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tr_tournament ON tournament_rounds(tournament_id);")


def _migrate_seat_assignments_v7(con: sqlite3.Connection) -> None:
    seat = con.execute("""
        SELECT s.id, s.tournament_id, s.round_no, s.table_no, s.tp_id
        FROM tournament_seats s
        LEFT JOIN tournament_participants p ON p.id=s.tp_id AND p.tournament_id=s.tournament_id
        LEFT JOIN tournament_rounds r ON r.round_no=s.round_no AND r.tournament_id=s.tournament_id
        WHERE p.id IS NULL OR r.id IS NULL LIMIT 1
    """).fetchone()
    if seat:
        raise ValueError(
            f"Sitzplatz {seat['id']} passt nicht zur Anmeldung oder Runde "
            f"(Turnier {seat['tournament_id']}, Runde {seat['round_no']}, Tisch {seat['table_no']}, "
            f"Teilnehmer-ID {seat['tp_id']})."
        )
    score = con.execute("""
        SELECT sc.id, sc.tournament_id, sc.round_no, sc.table_no, sc.tp_id
        FROM tournament_scores sc
        LEFT JOIN tournament_seats s ON s.tournament_id=sc.tournament_id AND s.round_no=sc.round_no
                                    AND s.table_no=sc.table_no AND s.tp_id=sc.tp_id
        WHERE s.id IS NULL LIMIT 1
    """).fetchone()
    if score:
        raise ValueError(
            f"Ergebnis {score['id']} hat keinen passenden Sitzplatz "
            f"(Turnier {score['tournament_id']}, Runde {score['round_no']}, Tisch {score['table_no']}, "
            f"Teilnehmer-ID {score['tp_id']})."
        )

    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tp_tournament_id ON tournament_participants(tournament_id, id)")
    # --- tournament_seats ---
    con.execute("ALTER TABLE tournament_seats RENAME TO tournament_seats_old;")
    con.execute(_CREATE_SEATS)
    con.execute(
        """
        INSERT INTO tournament_seats(id,tournament_id,round_no,table_no,seat,tp_id,created_at)
        SELECT id,tournament_id,round_no,table_no,seat,tp_id,created_at
        FROM tournament_seats_old;
        """
    )
    con.execute("DROP TABLE tournament_seats_old;")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts_round ON tournament_seats(tournament_id, round_no);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts_tp    ON tournament_seats(tp_id);")

    # --- tournament_scores ---
    con.execute("ALTER TABLE tournament_scores RENAME TO tournament_scores_old;")
    con.execute(_CREATE_SCORES)
    if _has_column(con, "tournament_scores_old", "updated_at"):
        con.execute(
            """
            INSERT INTO tournament_scores(id,tournament_id,round_no,table_no,tp_id,points,soli,created_at,updated_at)
            SELECT id,tournament_id,round_no,table_no,tp_id,points,COALESCE(soli,0),created_at,updated_at
            FROM tournament_scores_old;
            """
        )
    else:
        con.execute(
            """
            INSERT INTO tournament_scores(id,tournament_id,round_no,table_no,tp_id,points,soli,created_at,updated_at)
            SELECT id,tournament_id,round_no,table_no,tp_id,points,COALESCE(soli,0),created_at,datetime('now')
            FROM tournament_scores_old;
            """
        )
    con.execute("DROP TABLE tournament_scores_old;")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sc_round ON tournament_scores(tournament_id, round_no, table_no);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sc_tp    ON tournament_scores(tp_id);")


# -----------------------------------------------------------------------------
# Init / Migration
# -----------------------------------------------------------------------------
def init_db(db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        _initialize_schema(con)
    set_db_path(db_path)


def _initialize_schema(con: sqlite3.Connection) -> None:
    """Basisschema und Migrationen gemeinsam übernehmen oder zurückrollen."""
    con.row_factory = sqlite3.Row
    with con:
        # ---------------------------------------------------------------------
        # Basisschema (NEU-Installationen)
        # ---------------------------------------------------------------------
        con.executescript(
            f"""
            -- Für den Tabellenumbau müssen diese Optionen VOR der Transaktion gelten.
            PRAGMA foreign_keys=OFF;
            PRAGMA legacy_alter_table=ON;
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );

            -- initiale schema_version, falls noch nicht vorhanden
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

            -- Addresses
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

            -- Tournaments (inkl. marker)
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,

                marker TEXT,

                -- ✅ NEU: Turnier-Abschlusszeitpunkt (TEXT, i.d.R. datetime('now'))
                closed_at TEXT,

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

            -- Runden
            CREATE TABLE IF NOT EXISTS tournament_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_no INTEGER NOT NULL,

                -- ✅ NEU: deterministische Auslosungsmetadaten
                draw_seed INTEGER,
                draw_attempt INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(tournament_id, round_no),
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tr_tournament ON tournament_rounds(tournament_id);

            -- Sitzplan
            {_CREATE_SEATS};
            CREATE INDEX IF NOT EXISTS idx_ts_round ON tournament_seats(tournament_id, round_no);
            CREATE INDEX IF NOT EXISTS idx_ts_tp ON tournament_seats(tp_id);

            -- Ergebnisse
            {_CREATE_SCORES};
            CREATE INDEX IF NOT EXISTS idx_sc_round ON tournament_scores(tournament_id, round_no, table_no);
            CREATE INDEX IF NOT EXISTS idx_sc_tp    ON tournament_scores(tp_id);
            """
        )

        # ---------------------------------------------------------------------
        # Migrationen (Bestands-DBs)
        # ---------------------------------------------------------------------

        sv = _get_schema_version(con)
        if sv > SCHEMA_VERSION:
            raise ValueError(f"Diese Datenbank benötigt eine neuere App (Schema {sv}).")

        # 0) tournaments.closed_at (Altbestand)
        _ensure_column(con, "tournaments", "closed_at", "TEXT")

        # 1) addresses.invite (Altbestand)
        if _has_table(con, "addresses") and not _has_column(con, "addresses", "invite"):
            con.execute("ALTER TABLE addresses ADD COLUMN invite INTEGER NOT NULL DEFAULT 1")

        # 2) tournaments.marker (Altbestand)
        if _has_table(con, "tournaments") and not _has_column(con, "tournaments", "marker"):
            con.execute("ALTER TABLE tournaments ADD COLUMN marker TEXT;")

        # 3) Index auf tournaments.marker NUR wenn marker existiert
        if _has_table(con, "tournaments") and _has_column(con, "tournaments", "marker"):
            con.execute("CREATE INDEX IF NOT EXISTS idx_tournaments_marker ON tournaments(marker);")

        # 4) Einmalige Datenmigration: years -> markers
        _migrate_years_to_markers_once(con)

        if sv < 7:
            # Auch bereits gelöschte IDs bleiben nach dem Tabellenumbau verbraucht.
            sequences = dict(con.execute("""
                SELECT name, seq FROM sqlite_sequence
                WHERE name IN ('tournament_participants', 'tournament_rounds', 'tournament_seats', 'tournament_scores')
            """))

        # 5) Schema-Versionierte Migration (Tournament-Tabellen "hart" absichern)
        if sv < 2:
            _migrate_tournament_tables_v2(con)
            _set_schema_version(con, 2)

        # 6) ✅ NEU: draw_seed/draw_attempt in tournament_rounds (Altbestand)
        #    (für DBs, die bereits v2 haben, aber die Spalten noch nicht)
        if sv < 3:
            _ensure_column(con, "tournament_rounds", "draw_seed", "INTEGER")
            _ensure_column(con, "tournament_rounds", "draw_attempt", "INTEGER NOT NULL DEFAULT 0")
            # vorhandene NULLs glattziehen (SQLite-DEFAULT greift nicht rückwirkend)
            if _has_table(con, "tournament_rounds") and _has_column(con, "tournament_rounds", "draw_attempt"):
                con.execute("UPDATE tournament_rounds SET draw_attempt=0 WHERE draw_attempt IS NULL;")
            _set_schema_version(con, 3)

        if sv < 4:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    tournament_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    tp_id INTEGER, tp_id_2 INTEGER,
                    address_id_old INTEGER, address_id_new INTEGER,
                    address_id_old_2 INTEGER, address_id_new_2 INTEGER,
                    note TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_tournament_id_created_at "
                        "ON audit_log(tournament_id, created_at DESC)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")
            _set_schema_version(con, 4)

        if sv < 5:
            # Leere Kennzeichen zählten in der Einladungsliste bereits als ausgeschaltet.
            con.execute("UPDATE addresses SET invite=0 WHERE invite IS NULL")
            _set_schema_version(con, 5)

        if sv < 6:
            con.execute("""DELETE FROM audit_log WHERE NOT EXISTS (
                SELECT 1 FROM tournaments WHERE tournaments.id = audit_log.tournament_id
            )""")
            _set_schema_version(con, 6)

        con.execute("""
            CREATE TRIGGER IF NOT EXISTS delete_tournament_audit
            AFTER DELETE ON tournaments
            BEGIN
                DELETE FROM audit_log WHERE tournament_id = OLD.id;
            END
        """)

        if sv < 7:
            _migrate_seat_assignments_v7(con)
            for table, seq in sequences.items():
                con.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name=?", (seq, table))
            _set_schema_version(con, 7)

        if sv < 8:
            _ensure_column(con, "tournament_participants", "withdrawn_at", "TEXT")
            _set_schema_version(con, 8)

        # Default-Adressbuch sicherstellen
        ab = one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
        if not ab:
            any_ab = one(con, "SELECT id FROM addressbooks LIMIT 1")
            if not any_ab:
                con.execute("INSERT INTO addressbooks(name, is_default) VALUES (?,1)", ("Standard",))
            else:
                con.execute("UPDATE addressbooks SET is_default=1 WHERE id=?", (int(any_ab["id"]),))

        if sv < SCHEMA_VERSION and con.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Migration abgebrochen: Ungültige Datenbankverweise.")


# -----------------------------------------------------------------------------
# Backup / Restore
# -----------------------------------------------------------------------------
def _snapshot_name(prefix: str, suffix: str = ".sqlite3") -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}{stamp}-{uuid4().hex}{suffix}"


def _save_snapshot(source: sqlite3.Connection, target: Path) -> None:
    # Erst ein fertiges Backup unter dem sichtbaren Namen veröffentlichen.
    with TemporaryDirectory(dir=target.parent) as tmp:
        stage = Path(tmp) / "snapshot.sqlite3"
        with closing(sqlite3.connect(stage)) as out:
            source.backup(out)
        stage.replace(target)


def backup_db(backup_dir: Path) -> Path:
    if _DB_PATH is None:
        raise RuntimeError("DB path not set.")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / _snapshot_name("skt-backup-")
    with closing(sqlite3.connect(_DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        _save_snapshot(source, target)
    return target


def _prepare_restore(con: sqlite3.Connection) -> None:
    # Auch die ältesten Sicherungen enthalten diese Stammtabellen.
    base = ("meta", "addressbooks", "addresses", "wohnorte", "tournaments", "tournament_participants")
    if not all(_has_table(con, table) for table in base):
        raise ValueError("Die Datei enthält keine vollständige SchafkopfTurnier-Datenbank.")
    con.row_factory = sqlite3.Row
    round_tables = ("tournament_rounds", "tournament_seats", "tournament_scores")
    present = [_has_table(con, table) for table in round_tables]
    if (_get_schema_version(con) >= 2 or any(present)) and not all(present):
        raise ValueError("Die Rundendaten der Sicherung sind unvollständig.")
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Die Sicherung ist beschädigt.")
    _initialize_schema(con)

    # Mit dem tatsächlichen Installationsschema vergleichen, auch bei aktuellen Backups.
    with closing(sqlite3.connect(":memory:")) as reference:
        _initialize_schema(reference)
        tables = reference.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        for (table,) in tables:
            expected = {r["name"] for r in reference.execute("SELECT name FROM pragma_table_info(?)", (table,))}
            actual = {r["name"] for r in con.execute("SELECT name FROM pragma_table_info(?)", (table,))}
            if not expected.issubset(actual):
                raise ValueError(f"Unvollständige Sicherung: In {table} fehlen benötigte Spalten.")
            if table in ("tournament_seats", "tournament_scores"):
                required = _foreign_keys(reference, table)
                if not required.issubset(_foreign_keys(con, table)):
                    raise ValueError(f"Unvollständige Sicherung: In {table} fehlt die Verknüpfung zum Sitzplan.")
    if con.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Die Sicherung enthält ungültige Datenbankverweise.")


def wlan_generation(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT v FROM meta WHERE k='wlan_generation'").fetchone()
    return row[0] if row else ""


def restore_db(backup_file: Path) -> None:
    if _DB_PATH is None:
        raise RuntimeError("DB path not set.")
    backup_file = Path(backup_file)
    if not backup_file.is_file():
        raise FileNotFoundError(str(backup_file))

    live_path = _DB_PATH
    live_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=live_path.parent) as tmp:
        with closing(sqlite3.connect(Path(tmp) / "restore.sqlite3")) as stage:
            with closing(sqlite3.connect(backup_file.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                source.backup(stage)
            _prepare_restore(stage)
            # Eine eingespielte Sicherung kann dieselben IDs für andere Personen und Turniere enthalten.
            with stage:
                stage.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('wlan_generation',?)", (uuid4().hex,))

            with closing(sqlite3.connect(live_path)) as live:
                # SQLite hält diese Sperre bis zum Schließen. Zwischen Sicherung
                # und Aktivierung kann kein zweiter Schreiber Daten verändern.
                live.execute("PRAGMA locking_mode=EXCLUSIVE")
                live.execute("BEGIN EXCLUSIVE")
                live.commit()
                safety = live_path.with_name(_snapshot_name(
                    f"{live_path.stem}.before-restore-", live_path.suffix
                ))
                _save_snapshot(live, safety)
                stage.backup(live)
