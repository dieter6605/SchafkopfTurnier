# app/routes/tournaments.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from .. import db

bp = Blueprint("tournaments", __name__)


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


def _next_free_player_no(con: db.sqlite3.Connection, tournament_id: int) -> int:
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


def _search_addresses(con, qtxt: str, limit: int = 60) -> list[db.sqlite3.Row]:
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
          OR plz LIKE ? OR email LIKE ? OR telefon LIKE ? OR strasse LIKE ? OR hausnummer LIKE ?
        ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE, id DESC
        LIMIT ?
        """,
        (like, like, like, like, like, like, like, like, like, int(limit)),
    )


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
    return render_template("tournament_form.html", t=defaults, mode="new")


@bp.post("/tournaments/new")
def tournament_create():
    f = request.form
    title = (f.get("title") or "").strip()
    event_date = (f.get("event_date") or "").strip()
    start_time = (f.get("start_time") or "").strip()

    if not title:
        flash("Turniername fehlt.", "error")
        return redirect(url_for("tournaments.tournament_new"))
    if not event_date:
        flash("Datum ist Pflicht.", "error")
        return redirect(url_for("tournaments.tournament_new"))
    if not start_time:
        flash("Beginn ist Pflicht.", "error")
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
                title,
                event_date,
                start_time,
                (f.get("location") or "").strip() or None,
                (f.get("organizer") or "").strip() or None,
                (f.get("description") or "").strip() or None,
                _to_int(f.get("min_participants"), 0),
                _to_int(f.get("max_participants"), 0),
            ),
        )
        con.commit()
        tid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    flash("Turnier angelegt.", "ok")
    return redirect(url_for("tournaments.tournament_detail", tournament_id=tid))


@bp.get("/tournaments/<int:tournament_id>")
def tournament_detail(tournament_id: int):
    with db.connect() as con:
        t = db.one(con, "SELECT * FROM tournaments WHERE id=?", (tournament_id,))
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)

    return render_template("tournament_detail.html", t=t, counts=counts, now=_now_local_iso())


@bp.get("/tournaments/<int:tournament_id>/participants")
def tournament_participants(tournament_id: int):
    qtxt = (request.args.get("q") or "").strip()

    with db.connect() as con:
        t = db.one(con, "SELECT * FROM tournaments WHERE id=?", (tournament_id,))
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)

        hits = _search_addresses(con, qtxt, limit=60) if qtxt else []
        already = db.q(con, "SELECT address_id FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
        already_ids = {int(r["address_id"]) for r in already}
        hits2 = [h for h in hits if int(h["id"]) not in already_ids]

        plist = db.q(
            con,
            """
            SELECT tp.*,
                   a.nachname, a.vorname, a.wohnort, a.plz, a.ort, a.strasse, a.hausnummer,
                   a.telefon, a.email, a.status, a.notizen
            FROM tournament_participants tp
            JOIN addresses a ON a.id=tp.address_id
            WHERE tp.tournament_id=?
            ORDER BY tp.player_no DESC
            """,
            (tournament_id,),
        )

    return render_template(
        "tournament_participants.html",
        t=t,
        counts=counts,
        q=qtxt,
        hits=hits2,
        participants=plist,
        now=_now_local_iso(),
    )


@bp.post("/tournaments/<int:tournament_id>/participants/add/<int:address_id>")
def tournament_participant_add(tournament_id: int, address_id: int):
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        t = db.one(con, "SELECT * FROM tournaments WHERE id=?", (tournament_id,))
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        dup = db.one(
            con,
            "SELECT id FROM tournament_participants WHERE tournament_id=? AND address_id=?",
            (tournament_id, address_id),
        )
        if dup:
            flash("Diese Person ist bereits als Teilnehmer erfasst.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        pno = _next_free_player_no(con, tournament_id)

        con.execute(
            """
            INSERT INTO tournament_participants(tournament_id, player_no, address_id, display_name)
            VALUES (?,?,?,?)
            """,
            (tournament_id, pno, address_id, _display_name(a)),
        )
        con.commit()

    flash(f"Teilnehmer übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/admin/backup")
def admin_backup():
    from pathlib import Path

    backup_dir = Path(request.form.get("backup_dir") or "")
    if not str(backup_dir).strip():
        flash("Backup-Pfad fehlt.", "error")
        return redirect(url_for("tournaments.tournaments_list"))

    try:
        p = db.backup_db(backup_dir)
        flash(f"Backup erstellt: {p.name}", "ok")
    except Exception as e:
        flash(f"Backup fehlgeschlagen: {e}", "error")

    return redirect(url_for("tournaments.tournaments_list"))