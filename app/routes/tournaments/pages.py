# app/routes/tournaments/pages.py
from __future__ import annotations

from flask import flash, redirect, render_template, url_for

from ... import db
from . import bp
from .helpers import (
    _get_tournament,
    _now_local_iso,
    _read_tournament_form,
    _tournament_counts,
    _validate_tournament_form,
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

        rounds = db.q(
            con,
            "SELECT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no ASC",
            (tournament_id,),
        )
        round_list = [int(r["round_no"]) for r in rounds]
        last_round_no = max(round_list) if round_list else 0
        next_round_no = (last_round_no + 1) if last_round_no > 0 else 1

    return render_template(
        "tournament_detail.html",
        t=t,
        counts=counts,
        now=_now_local_iso(),
        last_round_no=last_round_no,
        round_list=round_list,
        next_round_no=next_round_no,
    )


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