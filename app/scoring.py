"""Gemeinsame Prüfung der Tischwertungen für Spielbetrieb und Wiederherstellung."""
from __future__ import annotations

import sqlite3
from itertools import groupby
from operator import itemgetter

MAX_SOLI_PER_TABLE = 40


def soli_total_issue(total: int) -> str | None:
    if total > MAX_SOLI_PER_TABLE:
        return f"Die vier Spieler haben zusammen {total} Soli. Pro Tisch und Runde sind höchstens {MAX_SOLI_PER_TABLE} erlaubt."
    return None


def first_score_issue(con: sqlite3.Connection, tournament_id: int, *, through_round: int | None = None) -> str | None:
    """Erste fehlerhafte Tischwertung, für Setzlisten bis einschließlich der Vorrunde."""
    params = (tournament_id, through_round, through_round)
    orphan_seat = con.execute("""
        SELECT s.round_no, s.table_no FROM tournament_seats s
        LEFT JOIN tournament_rounds r ON r.tournament_id=s.tournament_id AND r.round_no=s.round_no
        WHERE s.tournament_id=? AND (? IS NULL OR s.round_no <= ?) AND r.id IS NULL LIMIT 1
    """, params).fetchone()
    if orphan_seat:
        return f"Runde {orphan_seat['round_no']}, Tisch {orphan_seat['table_no']}: Der Sitzplan gehört zu keiner ausgelosten Runde."

    extra_score = con.execute("""
        SELECT sc.round_no, sc.table_no FROM tournament_scores sc
        LEFT JOIN tournament_seats s ON s.tournament_id=sc.tournament_id AND s.round_no=sc.round_no
                                    AND s.table_no=sc.table_no AND s.tp_id=sc.tp_id
        WHERE sc.tournament_id=? AND (? IS NULL OR sc.round_no <= ?) AND s.id IS NULL LIMIT 1
    """, params).fetchone()
    if extra_score:
        return f"Runde {extra_score['round_no']}, Tisch {extra_score['table_no']}: Ein Ergebnis hat keinen passenden Sitzplatz."

    rows = con.execute("""
        SELECT r.round_no, s.table_no, s.seat, tp.id AS participant_id, sc.id AS score_id, sc.points, sc.soli
        FROM tournament_rounds r
        LEFT JOIN tournament_seats s ON s.tournament_id=r.tournament_id AND s.round_no=r.round_no
        LEFT JOIN tournament_participants tp ON tp.id=s.tp_id AND tp.tournament_id=r.tournament_id
        LEFT JOIN tournament_scores sc ON sc.tournament_id=s.tournament_id AND sc.round_no=s.round_no
                                      AND sc.table_no=s.table_no AND sc.tp_id=s.tp_id
        WHERE r.tournament_id=? AND (? IS NULL OR r.round_no <= ?)
        ORDER BY r.round_no, s.table_no, s.seat
    """, params).fetchall()
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
        soli_issue = soli_total_issue(sum(r["soli"] for r in seats))
        if soli_issue:
            return f"{label}: {soli_issue}"
        if any(not isinstance(r["points"], int) for r in seats):
            return f"{label}: Die Punkte müssen ganze Zahlen sein."
        total = sum(r["points"] for r in seats)
        if total != 0:
            return f"{label}: Die Punktesumme beträgt {total} und muss 0 sein."
    if through_round is not None and last_seen < through_round:
        return f"Runde {last_seen + 1} wurde noch nicht ausgelost."
    return None
