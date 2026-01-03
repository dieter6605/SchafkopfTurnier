# app/routes/tournaments/standings.py
from __future__ import annotations

from flask import flash, redirect, render_template, url_for

from ... import db
from . import bp
from .helpers import _get_tournament, _now_local_iso


@bp.get("/tournaments/<int:tournament_id>/standings")
def tournament_standings_overall(tournament_id: int):
    """
    Gesamtwertung über alle Runden:
    - Sum(points) je Spieler
    - Sum(soli) je Spieler
    - zusätzlich: Anzeige der Punkte/Soli je Runde (Phase 1, nur UI)
    """
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        pc_row = db.one(con, "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
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

        expected_scores = participants_count * rounds_count

        rows = db.q(
            con,
            """
            SELECT
                tp.id AS tp_id,
                tp.player_no,
                a.nachname, a.vorname, a.wohnort,
                COALESCE(SUM(sc.points), 0) AS points,
                COALESCE(SUM(sc.soli), 0)   AS soli
            FROM tournament_participants tp
            JOIN addresses a ON a.id = tp.address_id
            LEFT JOIN tournament_scores sc
              ON sc.tournament_id = tp.tournament_id
             AND sc.tp_id = tp.id
            WHERE tp.tournament_id = ?
            GROUP BY tp.id, tp.player_no, a.nachname, a.vorname, a.wohnort
            ORDER BY
                points DESC,
                soli   DESC,
                a.nachname COLLATE NOCASE,
                a.vorname  COLLATE NOCASE,
                a.wohnort  COLLATE NOCASE,
                tp.player_no
            """,
            (tournament_id,),
        )

        per_round = db.q(
            con,
            """
            SELECT tp_id, round_no, points, soli
            FROM tournament_scores
            WHERE tournament_id=?
            """,
            (tournament_id,),
        )

        rounds_by_tp: dict[int, dict[int, dict]] = {}
        for r in per_round:
            tp_id = int(r["tp_id"])
            rn = int(r["round_no"])
            rounds_by_tp.setdefault(tp_id, {})[rn] = {"points": int(r["points"]), "soli": int(r["soli"])}

        out = []
        last_key = None
        place = 0
        idx = 0

        for r in rows:
            idx += 1
            key = (int(r["points"]), int(r["soli"]))
            if key != last_key:
                place = idx
                last_key = key

            out.append(
                {
                    "place": place,
                    "player_no": int(r["player_no"]),
                    "nachname": r["nachname"],
                    "vorname": r["vorname"],
                    "wohnort": r["wohnort"],
                    "points": int(r["points"]),
                    "soli": int(r["soli"]),
                    "rounds": rounds_by_tp.get(int(r["tp_id"]), {}),
                }
            )

    return render_template(
        "tournament_standings.html",
        t=t,
        rows=out,
        round_numbers=round_numbers,
        participants_count=participants_count,
        rounds_count=rounds_count,
        scores_count=scores_count,
        expected_scores=expected_scores,
        now=_now_local_iso(),
    )