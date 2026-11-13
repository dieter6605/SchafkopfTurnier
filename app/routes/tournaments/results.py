# app/routes/tournaments/results.py
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from typing import Any

from flask import flash, redirect, render_template, request, url_for

from ... import db
from . import bp
from .helpers import MAX_SOLI_PER_TABLE, _get_tournament, _guard_closed_redirect, _now_local_iso, _soli_total_issue


def _to_int(v: Any, *, default: int | None = None) -> int | None:
    if v is None:
        return default
    s = str(v).strip()
    if s == "":
        return default
    try:
        return int(s)
    except Exception:
        return default


@bp.get("/tournaments/<int:tournament_id>/rounds/<int:round_no>/results")
def tournament_round_results_overview(tournament_id: int, round_no: int):
    with closing(db.connect()) as con, con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        tables = db.q(
            con,
            """
            SELECT DISTINCT table_no
            FROM tournament_seats
            WHERE tournament_id=? AND round_no=?
            ORDER BY table_no ASC
            """,
            (tournament_id, round_no),
        )
        table_nos = [int(r["table_no"]) for r in tables]
        total_tables = len(table_nos)

        done_rows = db.q(
            con,
            """
            SELECT table_no, COUNT(*) AS c
            FROM tournament_scores
            WHERE tournament_id=? AND round_no=?
            GROUP BY table_no
            """,
            (tournament_id, round_no),
        )
        done_map = {int(r["table_no"]): int(r["c"]) for r in done_rows}
        done_tables = {k for k, c in done_map.items() if c >= 4}

        done_count = len(done_tables)
        open_count = max(0, total_tables - done_count)

        scores_count_row = db.one(
            con,
            """
            SELECT COUNT(*) AS c
            FROM tournament_scores
            WHERE tournament_id=? AND round_no=?
            """,
            (tournament_id, round_no),
        )
        scores_count = int(scores_count_row["c"] or 0) if scores_count_row else 0
        expected_scores = total_tables * 4

    return render_template(
        "tournament_results_overview.html",
        t=t,
        round_no=round_no,
        table_nos=table_nos,
        done_tables=done_tables,
        done_count=done_count,
        open_count=open_count,
        total_tables=total_tables,
        scores_count=scores_count,
        expected_scores=expected_scores,
        now=_now_local_iso(),
    )


@bp.get("/tournaments/<int:tournament_id>/rounds/<int:round_no>/results/standings")
def tournament_round_results_standings(tournament_id: int, round_no: int):
    with closing(db.connect()) as con, con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        total_tables_row = db.one(
            con,
            """
            SELECT COUNT(DISTINCT table_no) AS c
            FROM tournament_seats
            WHERE tournament_id=? AND round_no=?
            """,
            (tournament_id, round_no),
        )
        total_tables = int(total_tables_row["c"] or 0) if total_tables_row else 0
        expected_scores = total_tables * 4

        scores_count_row = db.one(
            con,
            """
            SELECT COUNT(*) AS c
            FROM tournament_scores
            WHERE tournament_id=? AND round_no=?
            """,
            (tournament_id, round_no),
        )
        scores_count = int(scores_count_row["c"] or 0) if scores_count_row else 0

        rows = db.q(
            con,
            """
            SELECT
                a.nachname, a.vorname, a.wohnort,
                tp.player_no,
                sc.points, sc.soli,
                sc.table_no,
                s.seat
            FROM tournament_scores sc
            JOIN tournament_participants tp ON tp.id=sc.tp_id
            JOIN addresses a ON a.id=tp.address_id
            LEFT JOIN tournament_seats s
              ON s.tournament_id=sc.tournament_id
             AND s.round_no=sc.round_no
             AND s.tp_id=sc.tp_id
            WHERE sc.tournament_id=? AND sc.round_no=?
            ORDER BY
                sc.points DESC,
                sc.soli DESC,
                a.nachname COLLATE NOCASE ASC,
                a.vorname COLLATE NOCASE ASC,
                tp.player_no ASC
            """,
            (tournament_id, round_no),
        )

        ranked: list[dict[str, Any]] = []
        last_key: tuple[int, int] | None = None
        place = 0
        shown = 0

        for r in rows:
            shown += 1
            key = (int(r["points"]), int(r["soli"]))
            if key != last_key:
                place = shown
                last_key = key

            ranked.append(
                {
                    "place": place,
                    "nachname": r["nachname"],
                    "vorname": r["vorname"],
                    "wohnort": r["wohnort"],
                    "player_no": int(r["player_no"]),
                    "points": int(r["points"]),
                    "soli": int(r["soli"]),
                    "table_no": int(r["table_no"]),
                    "seat": (r["seat"] or ""),
                }
            )

    return render_template(
        "tournament_round_standings.html",
        t=t,
        round_no=round_no,
        rows=ranked,
        total_tables=total_tables,
        scores_count=scores_count,
        expected_scores=expected_scores,
        now=_now_local_iso(),
    )


def _table_players(con, tournament_id, round_no, table_no):
    return db.q(con, """
        SELECT s.seat, s.tp_id, s.table_no, tr.id AS round_id, tp.address_id, tp.player_no,
               a.nachname, a.vorname, a.wohnort, sc.id AS score_id, sc.table_no AS score_table,
               sc.points, sc.soli
        FROM tournament_seats s
        LEFT JOIN tournament_rounds tr ON tr.tournament_id=s.tournament_id AND tr.round_no=s.round_no
        JOIN tournament_participants tp ON tp.id=s.tp_id
        JOIN addresses a ON a.id=tp.address_id
        LEFT JOIN tournament_scores sc
          ON sc.tournament_id=s.tournament_id AND sc.round_no=s.round_no AND sc.tp_id=s.tp_id
        WHERE s.tournament_id=? AND s.round_no=? AND s.table_no=?
        ORDER BY s.seat, s.tp_id
    """, (tournament_id, round_no, table_no))


def _score_state(players):
    # Der Zeitstempel hat nur Sekundengenauigkeit. Verglichen werden die vier
    # angezeigten Zuordnungen und Werte, einschließlich bislang leerer Plätze.
    fields = ("round_id", "table_no", "seat", "tp_id", "address_id", "score_id", "score_table", "points", "soli")
    state = [[player[field] for field in fields] for player in players]
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()


def _next_table(con, tournament_id, round_no, table_no):
    return con.execute("""
        SELECT MIN(table_no) FROM tournament_seats
        WHERE tournament_id=? AND round_no=? AND table_no>?
    """, (tournament_id, round_no, table_no)).fetchone()[0]


def _table_form(t, round_no, table_no, players, next_table, conflict_message=None, *, validation_message=None):
    return render_template(
        "tournament_results_table.html", t=t, round_no=round_no, table_no=table_no,
        players=players, round_id=players[0]["round_id"] or 0, next_table=next_table,
        score_state=_score_state(players), conflict_message=conflict_message, now=_now_local_iso(),
        max_soli=MAX_SOLI_PER_TABLE, validation_message=validation_message,
    )


@bp.get("/tournaments/<int:tournament_id>/rounds/<int:round_no>/results/<int:table_no>")
def tournament_round_results_table(tournament_id: int, round_no: int, table_no: int):
    with closing(db.connect()) as con:
        con.execute("BEGIN")
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))
        players = _table_players(con, tournament_id, round_no, table_no)
        if len(players) != 4:
            flash("Tisch nicht gefunden oder unvollständig (nicht genau 4 Spieler).", "error")
            return redirect(url_for("tournaments.tournament_round_view", tournament_id=tournament_id, round_no=round_no))
        next_table = _next_table(con, tournament_id, round_no, table_no)
    return _table_form(t, round_no, table_no, players, next_table)


@bp.post("/tournaments/<int:tournament_id>/rounds/<int:round_no>/results/<int:table_no>")
def tournament_round_results_table_post(tournament_id: int, round_no: int, table_no: int):
    f = request.form

    with closing(db.connect()) as con, con:
        con.execute("BEGIN IMMEDIATE")
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Ergebnis-Eingabe",
            endpoint="tournaments.tournament_round_results_overview",
            endpoint_kwargs={"tournament_id": tournament_id, "round_no": round_no},
        )
        if resp:
            return resp

        seats = _table_players(con, tournament_id, round_no, table_no)
        if len(seats) != 4:
            flash("Tisch nicht gefunden oder unvollständig.", "error")
            return redirect(url_for("tournaments.tournament_round_results_overview", tournament_id=tournament_id, round_no=round_no))

        expected_id = f.get("expected_round_id")
        if expected_id is not None and expected_id != str(seats[0]["round_id"] or 0):
            flash("Der Sitzplan hat sich seit dem Öffnen geändert. Bitte den Tisch erneut prüfen.", "warning")
            return redirect(url_for("tournaments.tournament_round_results_table", tournament_id=tournament_id,
                                    round_no=round_no, table_no=table_no))

        if expected_id is not None and any(
            f.get(f"address_{seat['tp_id']}") != str(seat["address_id"]) for seat in seats
        ):
            flash("Die Teilnehmerzuordnung muss erneut geprüft werden. Bitte den aktuellen Tisch kontrollieren.", "warning")
            return redirect(url_for("tournaments.tournament_round_results_table", tournament_id=tournament_id,
                                    round_no=round_no, table_no=table_no))

        expected_state = f.get("expected_score_state")
        conflict = None
        if expected_state is None:
            # Alte Eingabemasken dürfen einen leeren Tisch erstmals erfassen.
            if any(player["score_id"] is not None for player in seats):
                conflict = "Dieses Formular enthält keinen Vergleichsstand."
        elif expected_state != _score_state(seats):
            conflict = "Dieser Tisch wurde inzwischen geändert."
        if conflict:
            next_table = _next_table(con, tournament_id, round_no, table_no)
            con.rollback()
            return _table_form(t, round_no, table_no, seats, next_table, conflict), 409

        points_map: dict[int, int] = {}
        soli_map: dict[int, int] = {}

        for r in seats:
            tp_id = int(r["tp_id"])

            p = _to_int(f.get(f"points_{tp_id}"), default=None)
            if p is None or not -(2**63) <= p < 2**63:
                flash("Bitte für alle 4 Spieler gültige ganze Punkte eingeben.", "error")
                return redirect(
                    url_for(
                        "tournaments.tournament_round_results_table",
                        tournament_id=tournament_id,
                        round_no=round_no,
                        table_no=table_no,
                    )
                )

            raw_soli = (f.get(f"soli_{tp_id}") or "").strip() or "0"
            s = _to_int(raw_soli)
            if not raw_soli.isascii() or not raw_soli.isdecimal() or s is None or s >= 2**63:
                flash(f"Bitte für Nr. {r['player_no']} eine gültige Anzahl Soli ab 0 eingeben.", "error")
                return redirect(url_for("tournaments.tournament_round_results_table", tournament_id=tournament_id,
                                        round_no=round_no, table_no=table_no))

            points_map[tp_id] = int(p)
            soli_map[tp_id] = int(s)

        soli_issue = _soli_total_issue(sum(soli_map.values()))
        if soli_issue:
            next_table = _next_table(con, tournament_id, round_no, table_no)
            con.rollback()
            return _table_form(t, round_no, table_no, seats, next_table, validation_message=soli_issue), 422

        total = sum(points_map.values())
        if total != 0:
            flash(f"Fehler: Punktesumme am Tisch {table_no} ist {total} (muss 0 sein).", "error")
            return redirect(
                url_for(
                    "tournaments.tournament_round_results_table",
                    tournament_id=tournament_id,
                    round_no=round_no,
                    table_no=table_no,
                )
            )

        for tp_id in points_map:
            con.execute(
                """
                INSERT INTO tournament_scores(tournament_id, round_no, table_no, tp_id, points, soli, created_at, updated_at)
                VALUES (?,?,?,?,?,?, datetime('now'), datetime('now'))
                ON CONFLICT(tournament_id, round_no, tp_id) DO UPDATE SET
                    table_no=excluded.table_no,
                    points=excluded.points,
                    soli=excluded.soli,
                    updated_at=datetime('now')
                """,
                (tournament_id, round_no, table_no, tp_id, points_map[tp_id], soli_map[tp_id]),
            )

        con.commit()

        go_next = (f.get("go_next") or "") == "1"
        if go_next:
            next_table = _next_table(con, tournament_id, round_no, table_no)
            if next_table is not None:
                flash(f"Tisch {table_no} gespeichert. Weiter zu Tisch {next_table}.", "ok")
                return redirect(
                    url_for(
                        "tournaments.tournament_round_results_table",
                        tournament_id=tournament_id,
                        round_no=round_no,
                        table_no=next_table,
                    )
                )

        flash(f"Tisch {table_no} gespeichert.", "ok")
        return redirect(url_for("tournaments.tournament_round_results_overview", tournament_id=tournament_id, round_no=round_no))
