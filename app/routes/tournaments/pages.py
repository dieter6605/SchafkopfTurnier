# app/routes/tournaments/pages.py
from __future__ import annotations

from contextlib import closing

from flask import flash, redirect, render_template, request, url_for

from ... import db
from . import bp
from .helpers import (
    _get_tournament,
    _guard_close_requires_complete_scores,
    _validate_marker_for_event_date,
    _guard_closed_redirect,
    _now_local_iso,
    _read_tournament_form,
    _scores_expected_count,
    _tournament_counts,
    _validate_tournament_form,
)


@bp.get("/tournaments")
def tournaments_list():
    with closing(db.connect()) as con, con:
        con.execute("BEGIN")
        rows = db.q(
            con,
            """
            SELECT
              t.*,

              -- Teilnehmer
              (SELECT COUNT(*) FROM tournament_participants tp WHERE tp.tournament_id=t.id) AS participant_count,

              -- Runden
              (SELECT COUNT(*) FROM tournament_rounds tr WHERE tr.tournament_id=t.id) AS rounds_count,

              -- Scores (alle Einträge)
              (SELECT COUNT(*) FROM tournament_scores sc WHERE sc.tournament_id=t.id) AS scores_count,

              (SELECT COUNT(*) FROM tournament_seats s WHERE s.tournament_id=t.id) AS expected_scores,

              -- Status-Felder als 0/1
              CASE WHEN COALESCE(TRIM(t.marker),'') <> '' THEN 1 ELSE 0 END AS marker_ok,
              CASE WHEN COALESCE(TRIM(t.closed_at),'') <> '' THEN 1 ELSE 0 END AS is_closed,

              -- "zuletzt geändert" (für Liste)
              COALESCE(t.updated_at, t.created_at) AS last_update

            FROM tournaments t
            ORDER BY t.event_date DESC, t.start_time DESC, t.id DESC
            """,
        )
        tournaments = []
        for row in rows:
            tournament = dict(row)
            tournament["scores_complete"] = (
                row["expected_scores"] > 0 and row["scores_count"] == row["expected_scores"]
                and _guard_close_requires_complete_scores(con, row["id"]) is None
            )
            tournaments.append(tournament)
    return render_template("tournaments.html", tournaments=tournaments, now=_now_local_iso())


@bp.get("/tournaments/new")
def tournament_new():
    defaults = {
        "title": "",
        "event_date": "",
        "start_time": "19:00",
        "marker": "",
        "location": "",
        "organizer": "",
        "description": "",
        "min_participants": 0,
        "max_participants": 0,
    }
    return render_template(
        "tournament_form.html",
        t=defaults,
        mode="new",
        back_url=url_for("tournaments.tournaments_list"),
    )


@bp.post("/tournaments/new")
def tournament_create():
    data = _read_tournament_form()
    err = _validate_tournament_form(data)
    if err:
        flash(err, "error")
        return redirect(url_for("tournaments.tournament_new"))

    with closing(db.connect()) as con, con:
        con.execute(
            """
            INSERT INTO tournaments(
              title, event_date, start_time,
              marker,
              location, organizer, description,
              min_participants, max_participants,
              created_at, updated_at
            )
            VALUES (?,?,?,?, ?,?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (
                data["title"],
                data["event_date"],
                data["start_time"],
                data["marker"],
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
    with closing(db.connect()) as con, con:
        con.execute("BEGIN")
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

        fk_on = int(con.execute("PRAGMA foreign_keys;").fetchone()[0] or 0)
        integrity = str(con.execute("PRAGMA integrity_check;").fetchone()[0] or "")
        fk_issues = db.q(con, "PRAGMA foreign_key_check;")
        fk_issues_count = len(fk_issues)

        scores_count = con.execute(
            "SELECT COUNT(*) FROM tournament_scores WHERE tournament_id=?", (tournament_id,),
        ).fetchone()[0]
        expected_scores = _scores_expected_count(con, tournament_id)
        score_issue = _guard_close_requires_complete_scores(con, tournament_id)
        marker_issue = _validate_marker_for_event_date(t["marker"] or "", t["event_date"] or "")

        safe = {
            "fk_on": fk_on,
            "integrity": integrity,
            "fk_issues_count": fk_issues_count,
            "scores": scores_count,
            "expected_scores": expected_scores,
            "marker_ok": marker_issue is None,
            "marker_issue": marker_issue,
            "score_issue": score_issue,
        }
        safe["ready"] = (
            safe["fk_on"] == 1
            and safe["integrity"].lower() == "ok"
            and safe["fk_issues_count"] == 0
            and safe["marker_ok"]
            and score_issue is None
        )

    return render_template(
        "tournament_detail.html",
        t=t,
        counts=counts,
        now=_now_local_iso(),
        last_round_no=last_round_no,
        round_list=round_list,
        next_round_no=next_round_no,
        safe=safe,
    )


@bp.get("/tournaments/<int:tournament_id>/edit")
def tournament_edit(tournament_id: int):
    with closing(db.connect()) as con, con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Bearbeiten",
            endpoint="tournaments.tournament_detail",
            endpoint_kwargs={"tournament_id": tournament_id},
        )
        if resp:
            return resp

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

    with closing(db.connect()) as con, con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Speichern",
            endpoint="tournaments.tournament_detail",
            endpoint_kwargs={"tournament_id": tournament_id},
        )
        if resp:
            return resp

        con.execute(
            """
            UPDATE tournaments
            SET title=?,
                event_date=?,
                start_time=?,
                marker=?,
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
                data["marker"],
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


@bp.get("/tournaments/diagnostics")
@bp.get("/tournaments/<int:tournament_id>/diagnostics")
def tournaments_diagnostics(tournament_id: int | None = None):
    with closing(db.connect()) as con:
        con.execute("BEGIN")
        fk_on = con.execute("PRAGMA foreign_keys").fetchone()[0]
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        issues = db.q(con, "PRAGMA foreign_key_check")

        tournament = _get_tournament(con, tournament_id) if tournament_id is not None else None
        counts = None
        if tournament is not None:
            counts = con.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=:tid) AS participants,
                    (SELECT COUNT(*) FROM tournament_rounds WHERE tournament_id=:tid) AS rounds,
                    (SELECT COUNT(*) FROM tournament_seats WHERE tournament_id=:tid) AS seats,
                    (SELECT COUNT(*) FROM tournament_scores WHERE tournament_id=:tid) AS scores
                """,
                {"tid": tournament_id},
            ).fetchone()

    return render_template(
        "tournament_diagnostics.html", tournament_id=tournament_id, tournament=tournament,
        counts=counts, fk_on=fk_on, integrity=integrity, issues=issues,
    )


@bp.post("/tournaments/<int:tournament_id>/delete")
def tournament_delete(tournament_id: int):
    """
    Löscht das Turnier. Alle abhängigen Datensätze (participants/rounds/seats/scores)
    werden via ON DELETE CASCADE automatisch entfernt.
    """
    with closing(db.connect()) as con, con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Löschen",
            endpoint="tournaments.tournament_detail",
            endpoint_kwargs={"tournament_id": tournament_id},
        )
        if resp:
            return resp

        con.execute("DELETE FROM tournaments WHERE id=?", (tournament_id,))
        con.commit()

    flash("Turnier gelöscht.", "ok")
    return redirect(url_for("tournaments.tournaments_list"))