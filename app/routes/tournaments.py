# app/routes/tournaments.py
from __future__ import annotations

from datetime import datetime
import random
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .. import db
from .addresses import _default_ab_id, _upsert_wohnort

bp = Blueprint("tournaments", __name__)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# Auslosung: Historie + Optimierung
# -----------------------------------------------------------------------------
def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _history_pairs(con, tournament_id: int, round_lt: int) -> set[tuple[int, int]]:
    """
    Alle Paare (tp_id,tp_id), die vor round_lt schon mal am selben Tisch saßen.
    """
    rows = db.q(
        con,
        """
        SELECT round_no, table_no, tp_id
        FROM tournament_seats
        WHERE tournament_id=? AND round_no < ?
        ORDER BY round_no, table_no
        """,
        (tournament_id, round_lt),
    )
    by_rt: dict[tuple[int, int], list[int]] = {}
    for r in rows:
        key = (int(r["round_no"]), int(r["table_no"]))
        by_rt.setdefault(key, []).append(int(r["tp_id"]))

    pairs: set[tuple[int, int]] = set()
    for ids in by_rt.values():
        ids = list(dict.fromkeys(ids))
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.add(_pair(ids[i], ids[j]))
    return pairs


def _score_plan(tps: list[dict[str, int]], tables: list[list[int]], hist_pairs: set[tuple[int, int]]) -> int:
    """
    Kostenfunktion:
    - direkt benachbarte player_no (d==1) am selben Tisch: sehr harte Strafe
    - Wiedersehen (Paar schon mal gemeinsam am Tisch): harte Strafe
    - optional: d==2 am selben Tisch: kleine Strafe
    """
    pno = {tp["id"]: tp["player_no"] for tp in tps}
    score = 0

    for tab in tables:
        for i in range(len(tab)):
            for j in range(i + 1, len(tab)):
                a, b = tab[i], tab[j]
                d = abs(pno[a] - pno[b])
                if d == 1:
                    score += 10_000
                elif d == 2:
                    score += 500

                if _pair(a, b) in hist_pairs:
                    score += 2_000

    return score


def _random_tables(tp_ids: list[int], table_size: int = 4) -> list[list[int]]:
    return [tp_ids[i : i + table_size] for i in range(0, len(tp_ids), table_size)]


def _improve_tables(tps: list[dict[str, int]], tp_ids: list[int], hist_pairs: set[tuple[int, int]]) -> list[list[int]]:
    best_tables: list[list[int]] | None = None
    best_score = 10**18

    # Mehrere Random-Restarts
    for _ in range(40):
        ids = tp_ids[:]
        random.shuffle(ids)
        tables = _random_tables(ids, 4)
        cur = _score_plan(tps, tables, hist_pairs)

        # Lokale Verbesserung per Random-Swaps
        for _iter in range(4000):
            t1 = random.randrange(len(tables))
            t2 = random.randrange(len(tables))
            i1 = random.randrange(4)
            i2 = random.randrange(4)
            if t1 == t2 and i1 == i2:
                continue

            tables[t1][i1], tables[t2][i2] = tables[t2][i2], tables[t1][i1]
            nxt = _score_plan(tps, tables, hist_pairs)

            if nxt <= cur:
                cur = nxt
                if cur == 0:
                    break
            else:
                tables[t1][i1], tables[t2][i2] = tables[t2][i2], tables[t1][i1]

        if cur < best_score:
            best_score = cur
            best_tables = [t[:] for t in tables]

        if best_score == 0:
            break

    return best_tables or _random_tables(tp_ids, 4)


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
@bp.get("/tournaments")
def tournaments_list():
    with db.connect() as con:
        rows = db.q(
            con,
            """
            SELECT t.*,
              (SELECT COUNT(*) FROM tournament_participants tp WHERE tp.tournament_id=t.id) AS participant_count
            FROM tournaments t
            ORDER BY t.event_date DESC, t.start_time DESC, t.id DESC
            """,
        )
    return render_template("tournaments.html", tournaments=rows, now=_now_local_iso())


@bp.get("/tournaments/new")
def tournament_new():
    defaults = {
        "title": "",
        "event_date": "",
        "start_time": "19:00",
        "location": "",
        "organizer": "",
        "description": "",
        "min_participants": 0,
        "max_participants": 0,
    }
    return render_template("tournament_form.html", t=defaults, mode="new", back_url=url_for("tournaments.tournaments_list"))


@bp.post("/tournaments/new")
def tournament_create():
    data = _read_tournament_form()
    err = _validate_tournament_form(data)
    if err:
        flash(err, "error")
        return redirect(url_for("tournaments.tournament_new"))

    with db.connect() as con:
        con.execute(
            """
            INSERT INTO tournaments(
              title, event_date, start_time, location, organizer, description,
              min_participants, max_participants,
              created_at, updated_at
            )
            VALUES (?,?,?,?,?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (
                data["title"],
                data["event_date"],
                data["start_time"],
                data["location"],
                data["organizer"],
                data["description"],
                data["min_participants"],
                data["max_participants"],
            ),
        )
        con.commit()
        tid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    flash("Turnier angelegt.", "ok")
    return redirect(url_for("tournaments.tournament_detail", tournament_id=tid))


@bp.get("/tournaments/<int:tournament_id>")
def tournament_detail(tournament_id: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)
        last_round = db.one(
            con,
            "SELECT MAX(round_no) AS mx FROM tournament_rounds WHERE tournament_id=?",
            (tournament_id,),
        )
        last_round_no = int(last_round["mx"] or 0) if last_round else 0

    return render_template(
        "tournament_detail.html",
        t=t,
        counts=counts,
        now=_now_local_iso(),
        last_round_no=last_round_no,
    )


# -----------------------------------------------------------------------------
# Turnier bearbeiten
# -----------------------------------------------------------------------------
@bp.get("/tournaments/<int:tournament_id>/edit")
def tournament_edit(tournament_id: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

    return render_template(
        "tournament_form.html",
        t=t,
        mode="edit",
        back_url=url_for("tournaments.tournament_detail", tournament_id=tournament_id),
    )


@bp.post("/tournaments/<int:tournament_id>/edit")
def tournament_update(tournament_id: int):
    data = _read_tournament_form()
    err = _validate_tournament_form(data)
    if err:
        flash(err, "error")
        return redirect(url_for("tournaments.tournament_edit", tournament_id=tournament_id))

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        con.execute(
            """
            UPDATE tournaments
            SET title=?,
                event_date=?,
                start_time=?,
                location=?,
                organizer=?,
                description=?,
                min_participants=?,
                max_participants=?,
                updated_at=datetime('now')
            WHERE id=?
            """,
            (
                data["title"],
                data["event_date"],
                data["start_time"],
                data["location"],
                data["organizer"],
                data["description"],
                data["min_participants"],
                data["max_participants"],
                tournament_id,
            ),
        )
        con.commit()

    flash("Turnier gespeichert.", "ok")
    return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))


# -----------------------------------------------------------------------------
# Turnier löschen (inkl. Teilnehmer)
# -----------------------------------------------------------------------------
@bp.post("/tournaments/<int:tournament_id>/delete")
def tournament_delete(tournament_id: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        con.execute("DELETE FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
        con.execute("DELETE FROM tournaments WHERE id=?", (tournament_id,))
        con.commit()

    flash("Turnier gelöscht.", "ok")
    return redirect(url_for("tournaments.tournaments_list"))


# -----------------------------------------------------------------------------
# Runden: Auslosen + Anzeigen
# -----------------------------------------------------------------------------
@bp.post("/tournaments/<int:tournament_id>/rounds/<int:round_no>/draw")
def tournament_round_draw(tournament_id: int, round_no: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        if round_no <= 0:
            flash("Ungültige Rundennummer.", "error")
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

        rows = db.q(
            con,
            """
            SELECT id, player_no
            FROM tournament_participants
            WHERE tournament_id=?
            ORDER BY player_no ASC
            """,
            (tournament_id,),
        )
        tps = [{"id": int(r["id"]), "player_no": int(r["player_no"])} for r in rows]
        n = len(tps)
        if n < 4:
            flash("Zu wenige Teilnehmer zum Auslosen.", "error")
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

        full_n = (n // 4) * 4
        rest = n - full_n
        tp_ids = [tp["id"] for tp in tps[:full_n]]

        hist_pairs = _history_pairs(con, tournament_id, round_no)

        # Überschreiben erlaubt: vorhandene Auslosung dieser Runde löschen
        con.execute("DELETE FROM tournament_seats WHERE tournament_id=? AND round_no=?", (tournament_id, round_no))
        con.execute("DELETE FROM tournament_rounds WHERE tournament_id=? AND round_no=?", (tournament_id, round_no))
        con.execute("INSERT INTO tournament_rounds(tournament_id, round_no) VALUES (?,?)", (tournament_id, round_no))

        tables = _improve_tables(tps, tp_ids, hist_pairs)

        seats = ["A", "B", "C", "D"]
        for table_no, ids in enumerate(tables, start=1):
            ids2 = ids[:]
            random.shuffle(ids2)  # Sitzplätze A-D zufällig
            for seat, tp_id in zip(seats, ids2):
                con.execute(
                    """
                    INSERT INTO tournament_seats(tournament_id, round_no, table_no, seat, tp_id)
                    VALUES (?,?,?,?,?)
                    """,
                    (tournament_id, round_no, table_no, seat, int(tp_id)),
                )

        con.commit()

    msg = f"Runde {round_no} ausgelost ({full_n//4} Tische)."
    if rest:
        msg += f" {rest} Reserve (nicht gesetzt)."
    flash(msg, "ok")
    return redirect(url_for("tournaments.tournament_round_view", tournament_id=tournament_id, round_no=round_no))


@bp.get("/tournaments/<int:tournament_id>/rounds/<int:round_no>")
def tournament_round_view(tournament_id: int, round_no: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        seats = db.q(
            con,
            """
            SELECT s.table_no, s.seat, s.tp_id,
                   tp.player_no,
                   a.nachname, a.vorname, a.wohnort
            FROM tournament_seats s
            JOIN tournament_participants tp ON tp.id=s.tp_id
            JOIN addresses a ON a.id=tp.address_id
            WHERE s.tournament_id=? AND s.round_no=?
            ORDER BY s.table_no ASC,
                     CASE s.seat WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END
            """,
            (tournament_id, round_no),
        )

        seated_tp = {int(r["tp_id"]) for r in seats}
        reserve = db.q(
            con,
            """
            SELECT tp.id AS tp_id, tp.player_no, a.nachname, a.vorname, a.wohnort
            FROM tournament_participants tp
            JOIN addresses a ON a.id=tp.address_id
            WHERE tp.tournament_id=?
            ORDER BY tp.player_no ASC
            """,
            (tournament_id,),
        )
        reserve = [r for r in reserve if int(r["tp_id"]) not in seated_tp]

        rounds = db.q(
            con,
            "SELECT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no ASC",
            (tournament_id,),
        )
        round_list = [int(r["round_no"]) for r in rounds]
        last_round_no = max(round_list) if round_list else 0

        prev_round_no = None
        next_round_no = None
        if round_list:
            try:
                pos = round_list.index(int(round_no))
            except ValueError:
                pos = -1
            if pos != -1:
                if pos > 0:
                    prev_round_no = round_list[pos - 1]
                if pos < len(round_list) - 1:
                    next_round_no = round_list[pos + 1]

    return render_template(
        "tournament_round.html",
        t=t,
        round_no=round_no,
        seats=seats,
        reserve=reserve,
        last_round_no=last_round_no,
        prev_round_no=prev_round_no,
        next_round_no=next_round_no,
        now=_now_local_iso(),
    )


@bp.get("/tournaments/<int:tournament_id>/participants")
def tournament_participants(tournament_id: int):
    qtxt = (request.args.get("q") or "").strip()
    show_gaps = (request.args.get("show_gaps") or "0") == "1"

    gaps: list[int] = []
    if show_gaps:
        k = _session_gaps_key(tournament_id)
        try:
            raw = session.get(k) or []
            gaps = [int(x) for x in raw]
        except Exception:
            gaps = []
        session.pop(k, None)

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)
        cap_ok = _cap_ok(t, counts["participants"])

        hits = _search_addresses(con, qtxt) if qtxt else []
        already = db.q(con, "SELECT address_id FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
        already_ids = {int(r["address_id"]) for r in already}
        hits = [h for h in hits if int(h["id"]) not in already_ids]

        participants = db.q(
            con,
            """
            SELECT tp.*,
                   a.nachname, a.vorname, a.wohnort,
                   a.telefon, a.email, a.status
            FROM tournament_participants tp
            JOIN addresses a ON a.id=tp.address_id
            WHERE tp.tournament_id=?
            ORDER BY tp.player_no
            """,
            (tournament_id,),
        )

    return render_template(
        "tournament_participants.html",
        t=t,
        counts=counts,
        q=qtxt,
        hits=hits,
        participants=participants,
        cap_ok=cap_ok,
        show_gaps=show_gaps,
        gaps=gaps,
    )


@bp.post("/tournaments/<int:tournament_id>/participants/add/<int:address_id>")
def tournament_participant_add(tournament_id: int, address_id: int):
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)
        if not _cap_ok(t, counts["participants"]):
            flash("Maximale Teilnehmerzahl erreicht – keine weitere Erfassung möglich.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        dup = db.one(
            con,
            "SELECT 1 FROM tournament_participants WHERE tournament_id=? AND address_id=?",
            (tournament_id, address_id),
        )
        if dup:
            flash("Teilnehmer bereits vorhanden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        pno = _next_free_player_no(con, tournament_id)

        con.execute(
            """
            INSERT INTO tournament_participants
              (tournament_id, player_no, address_id, display_name, created_at, updated_at)
            VALUES (?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (tournament_id, pno, address_id, _display_name(a)),
        )
        con.commit()

    flash(f"Teilnehmer übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/quickadd")
def tournament_participant_quickadd(tournament_id: int):
    f = request.form
    q = (f.get("q") or "").strip()

    nachname = (f.get("nachname") or "").strip()
    vorname = (f.get("vorname") or "").strip()
    wohnort = (f.get("wohnort") or "").strip()

    if not nachname or not vorname or not wohnort:
        flash("Pflichtfelder fehlen (Nachname, Vorname, Wohnort).", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

    plz = (f.get("plz") or "").strip() or None
    ort = (f.get("ort") or "").strip() or None

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)
        if not _cap_ok(t, counts["participants"]):
            flash("Maximale Teilnehmerzahl erreicht – keine weitere Erfassung möglich.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        _upsert_wohnort(con, wohnort, plz, ort)
        ab_id = _default_ab_id(con)

        cur = con.execute(
            """
            INSERT INTO addresses(
              addressbook_id, nachname, vorname, wohnort,
              plz, ort, strasse, hausnummer,
              telefon, email, status, notizen,
              created_at, updated_at
            )
            VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, datetime('now'), datetime('now'))
            """,
            (
                ab_id,
                nachname,
                vorname,
                wohnort,
                plz,
                ort,
                (f.get("strasse") or "").strip() or None,
                (f.get("hausnummer") or "").strip() or None,
                (f.get("telefon") or "").strip() or None,
                (f.get("email") or "").strip() or None,
                "aktiv",
                (f.get("notizen") or "").strip() or None,
            ),
        )

        address_id = int(cur.lastrowid)
        pno = _next_free_player_no(con, tournament_id)

        con.execute(
            """
            INSERT INTO tournament_participants
              (tournament_id, player_no, address_id, display_name, created_at, updated_at)
            VALUES (?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (tournament_id, pno, address_id, f"{nachname}, {vorname} · {wohnort}"),
        )
        con.commit()

    flash(f"Teilnehmer neu angelegt und übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/<int:tp_id>/remove")
def tournament_participant_remove(tournament_id: int, tp_id: int):
    renumber = _to_int(request.form.get("renumber"), 1)  # Default = 1
    q = (request.form.get("q") or request.args.get("q") or "").strip()

    with db.connect() as con:
        row = db.one(
            con,
            "SELECT id, player_no FROM tournament_participants WHERE id=? AND tournament_id=?",
            (tp_id, tournament_id),
        )
        if not row:
            flash("Teilnehmer nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        removed_no = int(row["player_no"] or 0)

        con.execute(
            "DELETE FROM tournament_participants WHERE id=? AND tournament_id=?",
            (tp_id, tournament_id),
        )

        if renumber and removed_no > 0:
            _renumber_from(con, tournament_id, removed_no)

        con.commit()

    flash("Teilnehmer entfernt.", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/renumber-from")
def tournament_participants_renumber_from(tournament_id: int):
    start_no = _to_int(request.form.get("start_no"), 0)
    q = (request.form.get("q") or request.args.get("q") or "").strip()

    if start_no <= 0:
        flash("Renummerieren: Startnummer fehlt/ungültig.", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

    with db.connect() as con:
        _renumber_from(con, tournament_id, start_no)
        con.commit()

    flash(f"Neu durchnummeriert ab Nr {start_no}.", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/check-numbers")
def tournament_participants_check_numbers(tournament_id: int):
    renumber = _to_int(request.form.get("renumber"), 0)  # 0/1
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        if renumber:
            _renumber_all(con, tournament_id)
            con.commit()
            flash("Teilnehmernummern wurden neu durchnummeriert (1..N).", "ok")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        gaps = _find_gaps(con, tournament_id)
        session[_session_gaps_key(tournament_id)] = gaps
        if gaps:
            flash(f"Prüfung: {len(gaps)} Lücke(n) gefunden.", "error")
        else:
            flash("Prüfung: keine Lücken gefunden.", "ok")

    return redirect(
        url_for(
            "tournaments.tournament_participants",
            tournament_id=tournament_id,
            q=q,
            show_gaps="1",
        )
    )


@bp.post("/tournaments/<int:tournament_id>/participants/swap")
def tournament_participant_swap(tournament_id: int):
    tp_id = _to_int(request.form.get("tp_id"), 0)
    new_address_id = _to_int(request.form.get("new_address_id"), 0)
    q = (request.form.get("q") or request.args.get("q") or "").strip()

    if tp_id <= 0 or new_address_id <= 0:
        flash("Swap: tp_id oder neue Address-ID fehlt.", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

    with db.connect() as con:
        tp = db.one(
            con,
            "SELECT id, address_id, player_no FROM tournament_participants WHERE id=? AND tournament_id=?",
            (tp_id, tournament_id),
        )
        if not tp:
            flash("Swap: Teilnehmer nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (new_address_id,))
        if not a:
            flash("Swap: Ziel-Adresse nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        dup = db.one(
            con,
            "SELECT 1 FROM tournament_participants WHERE tournament_id=? AND address_id=? LIMIT 1",
            (tournament_id, new_address_id),
        )
        if dup:
            flash("Swap: Diese Adresse ist bereits als Teilnehmer erfasst.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        con.execute(
            """
            UPDATE tournament_participants
            SET address_id=?,
                display_name=?,
                updated_at=datetime('now')
            WHERE id=? AND tournament_id=?
            """,
            (new_address_id, _display_name(a), tp_id, tournament_id),
        )
        con.commit()

    flash("Teilnehmer ersetzt (Nummer blieb gleich).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))