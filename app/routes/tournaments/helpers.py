# app/routes/tournaments/helpers.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import request, session

from ... import db


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _now_local_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _display_name(a: Any) -> str:
    wohnort = (a["wohnort"] or "").strip()
    base = f"{a['nachname']}, {a['vorname']}"
    return f"{base} · {wohnort}" if wohnort else base


def _get_tournament(con, tournament_id: int):
    return db.one(con, "SELECT * FROM tournaments WHERE id=?", (tournament_id,))


def _cap_ok(t: Any, participant_count: int) -> bool:
    try:
        mx = int(t["max_participants"] or 0)
    except Exception:
        mx = 0
    return (mx <= 0) or (participant_count < mx)


def _next_free_player_no(con, tournament_id: int) -> int:
    rows = db.q(con, "SELECT player_no FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
    used = {int(r["player_no"]) for r in rows if r["player_no"] is not None}
    n = 1
    while n in used:
        n += 1
    return n


def _tournament_counts(con, tournament_id: int) -> dict[str, int]:
    c = db.one(con, "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
    n = int(c["c"] or 0) if c else 0
    return {"participants": n, "tables": n // 4, "rest": n % 4}


def _search_addresses(con, qtxt: str, limit: int = 60):
    qtxt = (qtxt or "").strip()
    if not qtxt:
        return []
    like = f"%{qtxt}%"
    return db.q(
        con,
        """
        SELECT *
        FROM addresses
        WHERE
          nachname LIKE ? OR vorname LIKE ? OR wohnort LIKE ? OR ort LIKE ?
          OR plz LIKE ? OR email LIKE ? OR telefon LIKE ?
          OR strasse LIKE ? OR hausnummer LIKE ?
        ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE, id DESC
        LIMIT ?
        """,
        (like, like, like, like, like, like, like, like, like, int(limit)),
    )


def _renumber_all(con, tournament_id: int) -> None:
    """Verdichtet alle Teilnehmer auf 1..N in aktueller Reihenfolge (player_no aufsteigend)."""
    rows = db.q(
        con,
        "SELECT id FROM tournament_participants WHERE tournament_id=? ORDER BY player_no ASC, id ASC",
        (tournament_id,),
    )
    n = 1
    for r in rows:
        con.execute(
            "UPDATE tournament_participants SET player_no=?, updated_at=datetime('now') WHERE id=?",
            (n, int(r["id"])),
        )
        n += 1


def _renumber_from(con, tournament_id: int, start_no: int) -> None:
    """
    Verdichtet ab start_no:
    - alle Teilnehmer mit player_no >= start_no werden neu fortlaufend nummeriert
    - Nummern < start_no bleiben unverändert
    """
    start_no = int(start_no)
    if start_no <= 0:
        return

    rows = db.q(
        con,
        """
        SELECT id
        FROM tournament_participants
        WHERE tournament_id=? AND player_no>=?
        ORDER BY player_no ASC, id ASC
        """,
        (tournament_id, start_no),
    )
    n = start_no
    for r in rows:
        con.execute(
            "UPDATE tournament_participants SET player_no=?, updated_at=datetime('now') WHERE id=?",
            (n, int(r["id"])),
        )
        n += 1


def _find_gaps(con, tournament_id: int) -> list[int]:
    """
    Ermittelt fehlende Nummern im Bereich 1..max(player_no).
    (Duplikate sind durch UNIQUE(tournament_id, player_no) ausgeschlossen.)
    """
    rows = db.q(
        con,
        "SELECT player_no FROM tournament_participants WHERE tournament_id=? ORDER BY player_no ASC",
        (tournament_id,),
    )
    nums: list[int] = []
    for r in rows:
        try:
            n = int(r["player_no"])
            if n > 0:
                nums.append(n)
        except Exception:
            pass

    if not nums:
        return []

    s = set(nums)
    m = max(nums)
    return [i for i in range(1, m + 1) if i not in s]


def _session_gaps_key(tournament_id: int) -> str:
    return f"skt_tp_gaps_{int(tournament_id)}"


def _read_tournament_form() -> dict[str, Any]:
    """Liest und normalisiert Turnier-Formularfelder aus request.form."""
    f = request.form
    return {
        "title": (f.get("title") or "").strip(),
        "event_date": (f.get("event_date") or "").strip(),
        "start_time": (f.get("start_time") or "").strip(),
        "location": (f.get("location") or "").strip() or None,
        "organizer": (f.get("organizer") or "").strip() or None,
        "description": (f.get("description") or "").strip() or None,
        "min_participants": _to_int(f.get("min_participants"), 0),
        "max_participants": _to_int(f.get("max_participants"), 0),
    }


def _validate_tournament_form(data: dict[str, Any]) -> str | None:
    """Gibt Fehlermeldung zurück oder None wenn ok."""
    if not data["title"]:
        return "Turniername fehlt."
    if not data["event_date"]:
        return "Datum ist Pflicht."
    if not data["start_time"]:
        return "Beginn ist Pflicht."
    return None


def _pop_session_gaps(tournament_id: int) -> list[int]:
    """
    Helfer für participants: liest die zuvor gespeicherten Lücken und entfernt sie aus der Session.
    """
    k = _session_gaps_key(tournament_id)
    gaps: list[int] = []
    try:
        raw = session.get(k) or []
        gaps = [int(x) for x in raw]
    except Exception:
        gaps = []
    session.pop(k, None)
    return gaps