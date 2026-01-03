# app/tournament_marker.py
from __future__ import annotations

from datetime import datetime
from typing import Optional


def make_marker(event_date: str, tournament_id: int) -> str:
    """
    Marker: JJMMTTXXXX
    JJMMTT = YYMMDD aus event_date
    XXXX   = tournament_id, 4-stellig
    """
    # event_date kommt in deinem Projekt als 'YYYY-MM-DD'
    dt = datetime.strptime(event_date, "%Y-%m-%d")
    yymmdd = dt.strftime("%y%m%d")
    return f"{yymmdd}{int(tournament_id):04d}"


def ensure_tournament_marker_column(con) -> None:
    """
    Fügt Spalte 'marker' hinzu, wenn sie fehlt. (SQLite-robust, offline-friendly)
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(tournaments)").fetchall()]
    if "marker" not in cols:
        con.execute("ALTER TABLE tournaments ADD COLUMN marker TEXT")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tournaments_marker ON tournaments(marker)")


def ensure_tournament_markers(con) -> int:
    """
    Sorgt dafür, dass jedes Turnier einen Marker hat.
    Gibt die Anzahl der nachgezogenen Marker zurück.
    """
    ensure_tournament_marker_column(con)

    rows = con.execute(
        "SELECT id, event_date FROM tournaments WHERE marker IS NULL OR marker = ''"
    ).fetchall()

    updated = 0
    for tid, event_date in rows:
        if not event_date:
            # ohne Datum kein Marker – sollte aber bei dir required sein
            continue
        marker = make_marker(event_date, tid)
        con.execute("UPDATE tournaments SET marker=? WHERE id=?", (marker, tid))
        updated += 1

    return updated