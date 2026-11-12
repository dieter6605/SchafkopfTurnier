# app/routes/tournaments/rounds.py
from __future__ import annotations

import random
from contextlib import closing

from flask import flash, redirect, render_template, request, url_for

from ... import db
from . import bp
from .draw import (
    DRAW_MODE_RANDOM,
    DRAW_MODE_SEEDING,
    _fisher_yates_shuffle,
    _history_pairs,
    _improve_tables,
    _seed_for_tournament_round,
    _tables_by_seeding,
)
from .helpers import _get_tournament, _guard_closed_redirect, _now_local_iso, _overall_standings_tp_ids


@bp.post("/tournaments/<int:tournament_id>/rounds/<int:round_no>/draw")
def tournament_round_draw(tournament_id: int, round_no: int):
    with closing(db.connect()) as con, con:
        # Die Ergebnisprüfung und der Wechsel des Sitzplans müssen zusammen erfolgen.
        con.execute("BEGIN IMMEDIATE")
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Auslosen",
            endpoint="tournaments.tournament_detail",
            endpoint_kwargs={"tournament_id": tournament_id},
        )
        if resp:
            return resp

        if round_no <= 0:
            flash("Ungültige Rundennummer.", "error")
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

        round_url = url_for("tournaments.tournament_round_view", tournament_id=tournament_id, round_no=round_no)
        prev = db.one(
            con,
            "SELECT id, draw_attempt FROM tournament_rounds WHERE tournament_id=? AND round_no=?",
            (tournament_id, round_no),
        )
        expected_id = request.form.get("expected_round_id")
        if expected_id is not None and expected_id != str(prev["id"] if prev else 0):
            flash(f"Die Auslosung für Runde {round_no} hat sich inzwischen geändert. Der aktuelle Sitzplan wird angezeigt.", "info")
            return redirect(round_url)
        has_results = db.one(
            con, "SELECT 1 FROM tournament_scores WHERE tournament_id=? AND round_no=? LIMIT 1",
            (tournament_id, round_no),
        )
        if has_results:
            flash(f"Für Runde {round_no} sind bereits Ergebnisse gespeichert. Der Sitzplan ist gesperrt.", "warning")
            return redirect(round_url)

        # --- gewünschtes Verfahren ---
        draw_mode = (request.form.get("draw_mode") or DRAW_MODE_RANDOM).strip().lower()
        if draw_mode not in (DRAW_MODE_RANDOM, DRAW_MODE_SEEDING):
            draw_mode = DRAW_MODE_RANDOM

        if draw_mode == DRAW_MODE_SEEDING and int(round_no) < 2:
            flash("Setzliste ist erst ab Runde 2 möglich.", "error")
            return redirect(url_for("tournaments.tournament_round_view", tournament_id=tournament_id, round_no=round_no))

        rounds = db.q(
            con,
            "SELECT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no ASC",
            (tournament_id,),
        )
        round_list = [int(r["round_no"]) for r in rounds]
        last_round_no = max(round_list) if round_list else 0
        has_round = int(round_no) in round_list

        if not has_round:
            if int(round_no) != 1:
                if int(round_no) - 1 not in round_list:
                    flash(
                        f"Runde {round_no} kann noch nicht ausgelost werden: "
                        f"Runde {round_no - 1} wurde noch nicht ausgelost.",
                        "error",
                    )
                    if last_round_no > 0:
                        return redirect(
                            url_for(
                                "tournaments.tournament_round_view",
                                tournament_id=tournament_id,
                                round_no=last_round_no,
                            )
                        )
                    return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

                if last_round_no > 0 and int(round_no) != (last_round_no + 1):
                    flash(
                        f"Runde {round_no} kann nicht übersprungen werden. "
                        f"Bitte zuerst Runde {last_round_no + 1} auslosen.",
                        "error",
                    )
                    return redirect(
                        url_for(
                            "tournaments.tournament_round_view",
                            tournament_id=tournament_id,
                            round_no=last_round_no + 1,
                        )
                    )

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

        if (n % 4) != 0:
            flash(
                f"Teilnehmerzahl ({n}) ist nicht durch 4 teilbar. "
                "Bitte erst auf 4er auffüllen, dann auslosen.",
                "error",
            )
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

        tp_ids = [tp["id"] for tp in tps]
        hist_pairs = _history_pairs(con, tournament_id, round_no)

        prev_attempt = int(prev["draw_attempt"]) if (prev and prev["draw_attempt"] is not None) else 0
        attempt = prev_attempt + 1 if prev_attempt >= 0 else 1
        if attempt < 1:
            attempt = 1

        # ✅ Seed jetzt abhängig von Attempt
        seed = _seed_for_tournament_round(tournament_id, round_no, attempt)
        rng = random.Random(seed)

        # Ein bestehender Sitzplan ohne Ergebnisse darf neu ausgelost werden.
        con.execute("DELETE FROM tournament_seats  WHERE tournament_id=? AND round_no=?", (tournament_id, round_no))
        con.execute("DELETE FROM tournament_rounds WHERE tournament_id=? AND round_no=?", (tournament_id, round_no))

        # Runde neu anlegen inkl. Metadaten
        con.execute(
            "INSERT INTO tournament_rounds(tournament_id, round_no, draw_seed, draw_attempt) VALUES (?,?,?,?)",
            (tournament_id, round_no, int(seed), int(attempt)),
        )

        # ✅ Auslosung wählen
        if draw_mode == DRAW_MODE_SEEDING:
            tp_ids_sorted = _overall_standings_tp_ids(con, tournament_id, upto_round=int(round_no) - 1)

            # Safety: falls irgendwas fehlt, fallback auf player_no Reihenfolge
            if not tp_ids_sorted or len(tp_ids_sorted) != len(tp_ids):
                tp_ids_sorted = tp_ids[:]

            tables = _tables_by_seeding(tp_ids_sorted, 4)
        else:
            tables = _improve_tables(
                tps,
                tp_ids,
                hist_pairs,
                tournament_id=tournament_id,
                round_no=round_no,
                attempt=attempt,
            )

        # ✅ Sitzverteilung am Tisch deterministisch
        seats = ["A", "B", "C", "D"]
        for table_no, ids in enumerate(tables, start=1):
            ids2 = ids[:]
            _fisher_yates_shuffle(ids2, rng)
            for seat, tp_id in zip(seats, ids2):
                con.execute(
                    """
                    INSERT INTO tournament_seats(tournament_id, round_no, table_no, seat, tp_id)
                    VALUES (?,?,?,?,?)
                    """,
                    (tournament_id, round_no, table_no, seat, int(tp_id)),
                )

        con.commit()

    mode_label = "Setzliste" if draw_mode == DRAW_MODE_SEEDING else "Zufall"
    flash(f"Runde {round_no} ausgelost ({n//4} Tische) · {mode_label}.", "ok")
    return redirect(url_for("tournaments.tournament_round_view", tournament_id=tournament_id, round_no=round_no))


@bp.get("/tournaments/<int:tournament_id>/rounds/<int:round_no>")
def tournament_round_view(tournament_id: int, round_no: int):
    with closing(db.connect()) as con, con:
        # Sitzplan, Ergebnisse und Formular-ID müssen denselben Stand zeigen.
        con.execute("BEGIN")
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        rounds = db.q(
            con,
            "SELECT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no ASC",
            (tournament_id,),
        )
        round_list = [int(r["round_no"]) for r in rounds]
        last_round_no = max(round_list) if round_list else 0

        prev_round_no: int | None = None
        next_round_no: int | None = None

        rn = int(round_no)

        if round_list:
            if rn in round_list:
                pos = round_list.index(rn)
                if pos > 0:
                    prev_round_no = round_list[pos - 1]
                if pos < len(round_list) - 1:
                    next_round_no = round_list[pos + 1]
            else:
                first_round_no = round_list[0]

                if rn > last_round_no:
                    prev_round_no = last_round_no if last_round_no > 0 else None
                    next_round_no = None
                elif rn < first_round_no:
                    prev_round_no = None
                    next_round_no = first_round_no
                else:
                    lower = [x for x in round_list if x < rn]
                    higher = [x for x in round_list if x > rn]
                    prev_round_no = max(lower) if lower else None
                    next_round_no = min(higher) if higher else None

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

        tr = db.one(
            con,
            "SELECT id, draw_seed, draw_attempt FROM tournament_rounds WHERE tournament_id=? AND round_no=?",
            (tournament_id, round_no),
        )
        draw_seed = int(tr["draw_seed"]) if (tr and tr["draw_seed"] is not None) else None
        draw_attempt = int(tr["draw_attempt"]) if (tr and tr["draw_attempt"] is not None) else 0

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

        seats_alpha = db.q(
            con,
            """
            SELECT s.table_no, s.seat, s.tp_id,
                   tp.player_no,
                   a.nachname, a.vorname, a.wohnort
            FROM tournament_seats s
            JOIN tournament_participants tp ON tp.id=s.tp_id
            JOIN addresses a ON a.id=tp.address_id
            WHERE s.tournament_id=? AND s.round_no=?
            ORDER BY a.nachname COLLATE NOCASE ASC,
                     a.vorname COLLATE NOCASE ASC,
                     tp.player_no ASC
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

        if not seats:
            flash(f"Für Runde {round_no} ist noch keine Auslosung vorhanden.", "info")

    return render_template(
        "tournament_round.html",
        t=t,
        round_no=round_no,
        seats=seats,
        seats_alpha=seats_alpha,
        reserve=reserve,
        last_round_no=last_round_no,
        prev_round_no=prev_round_no,
        next_round_no=next_round_no,
        done_tables=done_tables,
        has_results=bool(done_rows),
        round_id=tr["id"] if tr else 0,
        now=_now_local_iso(),
        draw_seed=draw_seed,
        draw_attempt=draw_attempt,
    )
