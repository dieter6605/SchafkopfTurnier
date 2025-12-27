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
    - Platzierung nach points DESC, soli DESC, dann Name

    Phase 1:
    - UI: zusätzlich pro Runde Punkte/Soli anzeigen (unter dem Gesamtwert)
    - Druck: unverändert (weiterhin kompakt)
    """
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        participants_count_row = db.one(
            con,
            "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,),
        )
        participants_count = int(participants_count_row["c"] or 0) if participants_count_row else 0

        rounds_count_row = db.one(
            con,
            "SELECT COUNT(DISTINCT round_no) AS c FROM tournament_rounds WHERE tournament_id=?",
            (tournament_id,),
        )
        rounds_count = int(rounds_count_row["c"] or 0) if rounds_count_row else 0

        scores_count_row = db.one(
            con,
            "SELECT COUNT(*) AS c FROM tournament_scores WHERE tournament_id=?",
            (tournament_id,),
        )
        scores_count = int(scores_count_row["c"] or 0) if scores_count_row else 0

        expected_scores = participants_count * rounds_count

        # ✅ Rundenliste (für UI-Anzeige pro Runde)
        rn_rows = db.q(
            con,
            """
            SELECT DISTINCT round_no
            FROM tournament_rounds
            WHERE tournament_id=?
            ORDER BY round_no ASC
            """,
            (tournament_id,),
        )
        round_numbers = [int(r["round_no"]) for r in rn_rows] if rn_rows else []

        # ✅ Gesamtwertung (wie bisher)
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
                a.nachname COLLATE NOCASE ASC,
                a.vorname  COLLATE NOCASE ASC,
                a.wohnort  COLLATE NOCASE ASC,
                tp.player_no ASC
            """,
            (tournament_id,),
        )

        # ✅ Rundendetails je Teilnehmer holen (points/soli pro Runde)
        # Erwartung: tournament_scores hat Spalte round_no (wie bei Rundenwertung üblich)
        detail = db.q(
            con,
            """
            SELECT tp_id, round_no,
                   COALESCE(points, 0) AS points,
                   COALESCE(soli, 0)   AS soli
            FROM tournament_scores
            WHERE tournament_id=?
            """,
            (tournament_id,),
        )

        by_tp: dict[int, dict[int, dict[str, int]]] = {}
        for d in detail or []:
            try:
                tp_id = int(d["tp_id"])
                rn = int(d["round_no"])
                p = int(d["points"] or 0)
                s = int(d["soli"] or 0)
            except Exception:
                continue
            by_tp.setdefault(tp_id, {})[rn] = {"points": p, "soli": s}

        # Platzierung vergeben (gleiches Punkte+Soli => gleicher Platz)
        out: list[dict] = []
        last_key: tuple[int, int] | None = None
        place = 0
        idx = 0

        for r in rows:
            idx += 1
            tp_id = int(r["tp_id"])
            p = int(r["points"] or 0)
            s = int(r["soli"] or 0)
            key = (p, s)
            if last_key is None or key != last_key:
                place = idx
                last_key = key

            out.append(
                {
                    "place": place,
                    "player_no": int(r["player_no"]),
                    "nachname": r["nachname"],
                    "vorname": r["vorname"],
                    "wohnort": r["wohnort"],
                    "points": p,
                    "soli": s,
                    # ✅ neu: je Runde Werte (kann leer sein)
                    "rounds": by_tp.get(tp_id, {}),
                }
            )

    return render_template(
        "tournament_standings.html",
        t=t,
        rows=out,
        participants_count=participants_count,
        rounds_count=rounds_count,
        scores_count=scores_count,
        expected_scores=expected_scores,
        round_numbers=round_numbers,  # ✅ neu
        now=_now_local_iso(),
    )