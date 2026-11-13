# app/routes/tournaments/standings.py
from __future__ import annotations

from contextlib import closing
from flask import flash, redirect, render_template, url_for

from ... import db
from ...scoring import first_score_issue, overall_standings, rank_places
from . import bp
from .helpers import _scores_expected_count, _get_tournament, _now_local_iso


@bp.get("/tournaments/<int:tournament_id>/standings")
def tournament_standings_overall(tournament_id: int):
    """
    Gesamtwertung über alle Runden:
    - Sum(points) je Spieler
    - Sum(soli) je Spieler
    - zusätzlich: Anzeige der Punkte je Runde
    """
    with closing(db.connect()) as con, con:
        con.execute("BEGIN")
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        pc_row = db.one(
            con,
            "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,),
        )
        participants_count = int(pc_row["c"] or 0) if pc_row else 0

        rounds = db.q(
            con,
            "SELECT DISTINCT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no",
            (tournament_id,),
        )
        round_numbers = [int(r["round_no"]) for r in rounds]
        rounds_count = len(round_numbers)

        sc_row = db.one(con, "SELECT COUNT(*) AS c FROM tournament_scores WHERE tournament_id=?", (tournament_id,))
        scores_count = int(sc_row["c"] or 0) if sc_row else 0

        expected_scores = _scores_expected_count(con, tournament_id)
        score_issue = first_score_issue(con, tournament_id)

        rows = overall_standings(con, tournament_id, sort_by_town=True)
        places = rank_places(rows)
        per_round = db.q(
            con,
            "SELECT tp_id, round_no, points, soli FROM tournament_scores WHERE tournament_id=?",
            (tournament_id,),
        )
        rounds_by_tp: dict[int, dict[int, dict]] = {}
        for r in per_round:
            tp_id = int(r["tp_id"])
            rn = int(r["round_no"])
            rounds_by_tp.setdefault(tp_id, {})[rn] = {"points": r["points"], "soli": r["soli"]}

        for row in rows:
            row["place"] = places.get(row["tp_id"])
            row["rounds"] = rounds_by_tp.get(row["tp_id"], {})

    return render_template(
        "tournament_standings.html",
        t=t,
        rows=rows,
        round_numbers=round_numbers,
        participants_count=participants_count,
        rounds_count=rounds_count,
        scores_count=scores_count,
        expected_scores=expected_scores,
        score_issue=score_issue,
        now=_now_local_iso(),
    )
