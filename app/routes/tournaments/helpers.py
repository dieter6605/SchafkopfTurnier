# app/routes/tournaments/helpers.py
from __future__ import annotations

from datetime import datetime
from itertools import groupby
from operator import itemgetter
from typing import Any

from flask import flash, redirect, request, session, url_for

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


# -----------------------------
# closed_at helper
# -----------------------------
def _is_closed(t: Any) -> bool:
    """
    True, wenn Turnier abgeschlossen (closed_at gesetzt).
    Funktioniert für sqlite RowMapping (dict-like) und dict.
    """
    try:
        if hasattr(t, "get"):
            return bool((t.get("closed_at") or "").strip())
        return bool((t["closed_at"] or "").strip())
    except Exception:
        return False


def _closed_at_str(t: Any) -> str:
    """closed_at als String (oder leer)."""
    try:
        if hasattr(t, "get"):
            return str(t.get("closed_at") or "")
        return str(t["closed_at"] or "")
    except Exception:
        return ""


def _guard_closed_redirect(
    t: Any,
    *,
    action: str,
    endpoint: str,
    endpoint_kwargs: dict[str, Any] | None = None,
    category: str = "error",
):
    """
    Zentrale serverseitige Sperre:
    - Wenn Turnier geschlossen => Flash + Redirect
    - Sonst => None
    """
    if not _is_closed(t):
        return None

    ca = _closed_at_str(t) or "unbekannt"
    flash(f"Turnier ist abgeschlossen (seit {ca}). {action} ist gesperrt.", category)
    return redirect(url_for(endpoint, **(endpoint_kwargs or {})))


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
    total, withdrawn = con.execute(
        "SELECT COUNT(*), COUNT(withdrawn_at) FROM tournament_participants WHERE tournament_id=?",
        (tournament_id,),
    ).fetchone()
    active = total - withdrawn
    return {"participants": total, "active": active, "withdrawn": withdrawn, "tables": active // 4, "rest": active % 4}


def _scores_expected_count(con, tournament_id: int) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM tournament_seats WHERE tournament_id=?", (tournament_id,),
    ).fetchone()[0]


def _first_score_issue(con, tournament_id: int, *, through_round: int | None = None) -> str | None:
    """Erste fehlerhafte Tischwertung, für Setzlisten bis einschließlich der Vorrunde."""
    params = (tournament_id, through_round, through_round)
    orphan_seat = db.one(con, """
        SELECT s.round_no, s.table_no FROM tournament_seats s
        LEFT JOIN tournament_rounds r ON r.tournament_id=s.tournament_id AND r.round_no=s.round_no
        WHERE s.tournament_id=? AND (? IS NULL OR s.round_no <= ?) AND r.id IS NULL LIMIT 1
    """, params)
    if orphan_seat:
        return f"Runde {orphan_seat['round_no']}, Tisch {orphan_seat['table_no']}: Der Sitzplan gehört zu keiner ausgelosten Runde."

    extra_score = db.one(con, """
        SELECT sc.round_no, sc.table_no FROM tournament_scores sc
        LEFT JOIN tournament_seats s ON s.tournament_id=sc.tournament_id AND s.round_no=sc.round_no
                                    AND s.table_no=sc.table_no AND s.tp_id=sc.tp_id
        WHERE sc.tournament_id=? AND (? IS NULL OR sc.round_no <= ?) AND s.id IS NULL LIMIT 1
    """, params)
    if extra_score:
        return f"Runde {extra_score['round_no']}, Tisch {extra_score['table_no']}: Ein Ergebnis hat keinen passenden Sitzplatz."

    rows = db.q(con, """
        SELECT r.round_no, s.table_no, s.seat, tp.id AS participant_id, sc.id AS score_id, sc.points, sc.soli
        FROM tournament_rounds r
        LEFT JOIN tournament_seats s ON s.tournament_id=r.tournament_id AND s.round_no=r.round_no
        LEFT JOIN tournament_participants tp ON tp.id=s.tp_id AND tp.tournament_id=r.tournament_id
        LEFT JOIN tournament_scores sc ON sc.tournament_id=s.tournament_id AND sc.round_no=s.round_no
                                      AND sc.table_no=s.table_no AND sc.tp_id=s.tp_id
        WHERE r.tournament_id=? AND (? IS NULL OR r.round_no <= ?)
        ORDER BY r.round_no, s.table_no, s.seat
    """, params)
    if not rows and through_round is None:
        return "Es wurde noch keine Runde ausgelost."
    last_seen = 0
    for (round_no, table_no), group in groupby(rows, key=itemgetter("round_no", "table_no")):
        if through_round is not None and round_no > last_seen + 1:
            return f"Runde {last_seen + 1} wurde noch nicht ausgelost."
        last_seen = round_no
        if table_no is None:
            return f"Runde {round_no}: Der Sitzplan fehlt."
        seats = list(group)
        label = f"Runde {round_no}, Tisch {table_no}"
        if len(seats) != 4 or {r["seat"] for r in seats} != {"A", "B", "C", "D"}:
            return f"{label}: Die vier Sitzplätze A bis D sind unvollständig oder fehlerhaft."
        players = {r["participant_id"] for r in seats}
        if len(players) != 4 or None in players:
            return f"{label}: Vier verschiedene Teilnehmer dieses Turniers müssen am Tisch sitzen."
        scored = sum(r["score_id"] is not None for r in seats)
        if scored != 4:
            return f"{label}: {scored} von 4 Ergebnissen erfasst."
        if any(not isinstance(r["soli"], int) or r["soli"] < 0 for r in seats):
            return f"{label}: Soli müssen nichtnegative ganze Zahlen sein."
        if any(not isinstance(r["points"], int) for r in seats):
            return f"{label}: Die Punkte müssen ganze Zahlen sein."
        total = sum(r["points"] for r in seats)
        if total != 0:
            return f"{label}: Die Punktesumme beträgt {total} und muss 0 sein."
    if through_round is not None and last_seen < through_round:
        return f"Runde {last_seen + 1} wurde noch nicht ausgelost."
    return None


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


def _normalize_marker(raw: str) -> str | None:
    """
    Normalisierung:
    - trim
    - Großbuchstaben
    - Whitespaces entfernen
    Ergebnis None, wenn leer.
    """
    s = (raw or "").strip().upper()
    s = "".join(s.split())
    return s or None


def _event_date_to_marker_prefix(event_date: str) -> str | None:
    """
    event_date erwartet YYYY-MM-DD.
    Gibt JJMMTT als 6-stelligen Prefix zurück, sonst None.
    """
    s = (event_date or "").strip()
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return dt.strftime("%y%m%d")
    except Exception:
        return None


def _validate_marker_for_event_date(marker: str, event_date: str) -> str | None:
    """
    Marker-Regeln:
    - exakt 10 Zeichen
    - A-Z/0-9
    - beginnt mit JJMMTT aus event_date
    Rückgabe: Fehlermeldung oder None.
    """
    m = (marker or "").strip().upper()
    if len(m) != 10:
        return "Marker muss exakt 10 Zeichen lang sein (JJMMTTxxxx)."
    if not m.isalnum():
        return "Marker darf nur Buchstaben/Ziffern enthalten (ohne Leerzeichen)."
    pref = _event_date_to_marker_prefix(event_date or "")
    if not pref:
        return "Marker-Prüfung nicht möglich: ungültiges Veranstaltungsdatum."
    if not m.startswith(pref):
        return f"Marker muss mit dem Datum beginnen: {pref}xxxx (JJMMTTxxxx)."
    return None


def _read_tournament_form() -> dict[str, Any]:
    """Liest und normalisiert Turnier-Formularfelder aus request.form."""
    f = request.form
    return {
        "title": (f.get("title") or "").strip(),
        "event_date": (f.get("event_date") or "").strip(),
        "start_time": (f.get("start_time") or "").strip(),
        "marker": _normalize_marker(f.get("marker") or ""),
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

    marker = data.get("marker")
    if marker:
        msg = _validate_marker_for_event_date(marker, data.get("event_date") or "")
        if msg:
            return msg

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


# =============================================================================
# ✅ DEV: CSV / Recalc helpers (Quelle der Wahrheit: tournament_years)
# =============================================================================

def _csv_tokens_norm(raw: str | None) -> list[str]:
    """
    CSV -> Tokens, normalisiert:
    - trim
    - upper
    - whitespaces entfernen
    - keine leeren Tokens
    - Duplikate entfernen (Reihenfolge bleibt)
    """
    s = (raw or "").strip()
    if not s:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in s.split(","):
        m = _normalize_marker(p or "")
        if not m:
            continue
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def _csv_join_norm(tokens: list[str]) -> str:
    """Join normalisierte Tokens als CSV ohne Spaces."""
    toks = [_normalize_marker(t or "") for t in (tokens or [])]
    toks2 = [t for t in toks if t]
    return ",".join(toks2)


def _remove_marker_from_tokens(tokens: list[str], marker: str) -> list[str]:
    """Entfernt marker aus Tokenliste (normalisiert), alle Vorkommen."""
    m = _normalize_marker(marker or "")
    if not m:
        return tokens
    out: list[str] = []
    for t in tokens:
        tt = _normalize_marker(t or "")
        if not tt:
            continue
        if tt == m:
            continue
        out.append(tt)
    return out


def _recalc_from_tournament_years(ty_raw: str | None) -> tuple[str | None, str | None, int]:
    """
    Quelle der Wahrheit: tournament_years (CSV)
    Rückgabe:
      (tournament_years_db, last_tournament_at_db, participation_count_int)
    """
    toks = _csv_tokens_norm(ty_raw)
    ty_db = _csv_join_norm(toks).strip() or None
    last_db = toks[-1] if toks else None
    pc = len(toks)
    return ty_db, last_db, pc


# =============================================================================
# ✅ DEV: Turnier wieder öffnen + Address-Marker/Counts zurückdrehen
# =============================================================================

def _reopen_tournament_and_fix_addresses(con, tournament_id: int) -> int:
    """
    Öffnet ein abgeschlossenes Turnier wieder (closed_at=NULL) und
    dreht die beim Abschluss geschriebenen Address-Marker zurück.

    participation_count wird NICHT +/-1 gerechnet,
    sondern IMMER aus tournament_years abgeleitet:
      participation_count == Anzahl der Marker in tournament_years

    - Entferne Turnier-Marker aus addresses.tournament_years
    - last_tournament_at = letzter (rechter) Marker aus tournament_years (nach Entfernung), sonst NULL
    - participation_count = len(tournament_years tokens)

    Rückgabe: Anzahl korrigierter address rows.
    """
    t = _get_tournament(con, tournament_id)
    if not t:
        raise RuntimeError("Turnier nicht gefunden")

    marker = _normalize_marker((t["marker"] or ""))
    if not marker:
        raise RuntimeError("Turnier hat keinen Marker – Wiederöffnen kann Marker nicht rückgängig machen.")

    addr_rows = db.q(
        con,
        """
        SELECT DISTINCT a.id, a.participation_count, a.last_tournament_at, a.tournament_years
        FROM tournament_participants tp
        JOIN addresses a ON a.id = tp.address_id
        WHERE tp.tournament_id = ?
        ORDER BY a.id ASC
        """,
        (tournament_id,),
    )

    changed = 0
    for a in addr_rows:
        aid = int(a["id"])

        pc_old = int(a["participation_count"] or 0)
        lt_old = (a["last_tournament_at"] or "").strip()
        ty_old = (a["tournament_years"] or "").strip()

        toks = _csv_tokens_norm(ty_old)
        toks2 = _remove_marker_from_tokens(toks, marker)

        ty_new, lt_new, pc_new = _recalc_from_tournament_years(_csv_join_norm(toks2))

        if (ty_new or "") != ty_old or (lt_new or "") != lt_old or pc_new != pc_old:
            con.execute(
                """
                UPDATE addresses
                SET tournament_years = ?,
                    last_tournament_at = ?,
                    participation_count = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (ty_new, lt_new, pc_new, aid),
            )
            changed += 1

    con.execute(
        "UPDATE tournaments SET closed_at = NULL, updated_at = datetime('now') WHERE id = ?",
        (tournament_id,),
    )

    return changed


# =============================================================================
# ✅ DEV: Repair addresses aus tournament_years (Count + last_tournament_at)
# =============================================================================

def _repair_addresses_from_tournament_years(
    con,
    *,
    only_active: bool = True,
    tournament_id: int | None = None,
) -> tuple[int, int]:
    """
    Repariert addresses anhand der CSV-Felder (Quelle: tournament_years):
    - tournament_years: normalisieren (trim/upper/whitespace raus, Duplikate raus)
    - participation_count: = Anzahl Tokens in tournament_years
    - last_tournament_at: = rechter CSV-Wert aus tournament_years oder NULL

    Optional:
    - tournament_id: wenn gesetzt, nur Adressen der Teilnehmer dieses Turniers.
    - only_active: wenn True, nur addresses.status='aktiv'.

    Rückgabe: (changed_rows, scanned_rows)
    """
    where_active = ""
    params: list[object] = []

    if only_active:
        where_active = " AND COALESCE(a.status,'aktiv')='aktiv' "

    if tournament_id is None:
        sql = (
            "SELECT a.id, a.tournament_years, a.last_tournament_at, a.participation_count "
            "FROM addresses a WHERE 1=1 " + where_active + " ORDER BY a.id ASC"
        )
    else:
        sql = (
            "SELECT DISTINCT a.id, a.tournament_years, a.last_tournament_at, a.participation_count "
            "FROM tournament_participants tp "
            "JOIN addresses a ON a.id = tp.address_id "
            "WHERE tp.tournament_id = ? " + where_active + " ORDER BY a.id ASC"
        )
        params.append(int(tournament_id))

    rows = db.q(con, sql, tuple(params))
    scanned = len(rows)
    changed = 0

    for r in rows:
        aid = int(r["id"])
        ty_old = (r["tournament_years"] or "").strip()
        lt_old = (r["last_tournament_at"] or "").strip()
        pc_old = int(r["participation_count"] or 0)

        ty_new, lt_new, pc_new = _recalc_from_tournament_years(ty_old)

        if (ty_new or "") != ty_old or (lt_new or "") != lt_old or pc_new != pc_old:
            con.execute(
                """
                UPDATE addresses
                SET tournament_years=?,
                    last_tournament_at=?,
                    participation_count=?,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (ty_new, lt_new, pc_new, aid),
            )
            changed += 1

    return changed, scanned


# =============================================================================
# ✅ Ranking helper: tp_ids nach Gesamtwertung (für Setzliste / Spielerkarte)
# =============================================================================

def _seeding_tp_ids(con, tournament_id: int, *, upto_round: int) -> list[int]:
    """
    Aktive Teilnehmer nach Gesamtwertung bis einschließlich `upto_round`.

    Sortierung:
    - SUM(points) DESC
    - SUM(soli)   DESC
    - Name + player_no als Tie-Breaker
    """
    rows = db.q(
        con,
        """
        SELECT
            tp.id AS tp_id,
            COALESCE(SUM(sc.points), 0) AS points,
            COALESCE(SUM(sc.soli), 0)   AS soli,
            a.nachname, a.vorname, a.wohnort,
            tp.player_no
        FROM tournament_participants tp
        JOIN addresses a ON a.id = tp.address_id
        LEFT JOIN tournament_scores sc
          ON sc.tournament_id = tp.tournament_id
         AND sc.tp_id = tp.id
         AND sc.round_no <= ?
        WHERE tp.tournament_id = ? AND tp.withdrawn_at IS NULL
        GROUP BY tp.id, a.nachname, a.vorname, a.wohnort, tp.player_no
        ORDER BY
            points DESC,
            soli   DESC,
            a.nachname COLLATE NOCASE,
            a.vorname  COLLATE NOCASE,
            a.wohnort  COLLATE NOCASE,
            tp.player_no
        """,
        (int(upto_round), int(tournament_id)),
    )
    return [int(r["tp_id"]) for r in rows]
