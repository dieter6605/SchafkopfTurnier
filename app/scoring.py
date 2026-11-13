"""Gemeinsame Prüfung der Tischwertungen für Spielbetrieb und Wiederherstellung."""
from __future__ import annotations

import sqlite3
from itertools import groupby
from operator import itemgetter

MAX_SOLI_PER_TABLE = 40


def overall_standings(con: sqlite3.Connection, tournament_id: int, *, sort_by_town: bool = False) -> list[dict]:
    # In Namensreihenfolge lesen, damit der stabile Sort bei Gleichstand diese Reihenfolge behält.
    players = {
        row["tp_id"]: dict(row, points=0, soli=0)
        for row in con.execute("""
            SELECT tp.id AS tp_id, tp.player_no, a.nachname, a.vorname, a.wohnort
            FROM tournament_participants tp JOIN addresses a ON a.id=tp.address_id
            WHERE tp.tournament_id=?
            ORDER BY a.nachname COLLATE NOCASE, a.vorname COLLATE NOCASE,
                     CASE WHEN ? THEN a.wohnort END COLLATE NOCASE, tp.player_no
        """, (tournament_id, sort_by_town))
    }
    for score in con.execute("""
        SELECT tp_id, points, soli FROM tournament_scores WHERE tournament_id=?
    """, (tournament_id,)):
        player = players[score["tp_id"]]
        # SQLite SUM würde Text als 0 zählen. Eine unlesbare Eingabe macht nur
        # diese Summe unbestimmt. Die andere Summe und die Einzelwerte bleiben lesbar.
        for field in ("points", "soli"):
            if not isinstance(score[field], int):
                player[field] = None
            elif player[field] is not None:
                player[field] += score[field]
    return sorted(players.values(), key=_standings_key, reverse=True)


def round_standings(con: sqlite3.Connection, tournament_id: int, round_no: int) -> list[dict]:
    rows = con.execute("""
        SELECT sc.tp_id, tp.player_no, a.nachname, a.vorname, a.wohnort,
               sc.points, sc.soli, sc.table_no, COALESCE(s.seat, '') AS seat
        FROM tournament_scores sc
        JOIN tournament_participants tp ON tp.id=sc.tp_id
        JOIN addresses a ON a.id=tp.address_id
        LEFT JOIN tournament_seats s ON s.tournament_id=sc.tournament_id AND s.round_no=sc.round_no
                                    AND s.tp_id=sc.tp_id
        WHERE sc.tournament_id=? AND sc.round_no=?
        ORDER BY a.nachname COLLATE NOCASE, a.vorname COLLATE NOCASE, tp.player_no
    """, (tournament_id, round_no))
    return sorted(map(dict, rows), key=_standings_key, reverse=True)


def _standings_key(row):
    if not isinstance(row["points"], int):
        return ()
    soli = row["soli"]
    return (row["points"], soli) if isinstance(soli, int) else (row["points"],)


def rank_places(rows) -> dict[int, int]:
    """Wettkampfränge nach Punkten. Nur bei Punktgleichheit entscheiden die Soli."""
    places = {}
    next_place = 1
    for points, group in groupby(rows, key=itemgetter("points")):
        if not isinstance(points, int):
            break  # Die sortierten Wertungen führen unlesbare Punktezahlen zuletzt.
        tied = list(group)
        first_place = next_place
        next_place += len(tied)
        if len(tied) > 1 and any(not isinstance(row["soli"], int) for row in tied):
            continue  # Die Reihenfolge dieser punktgleichen Spieler ist offen.
        place = first_place
        for offset, row in enumerate(tied):
            if offset and row["soli"] != tied[offset - 1]["soli"]:
                place = first_place + offset
            places[row["tp_id"]] = place
    return places


def soli_total_issue(total: int) -> str | None:
    if total > MAX_SOLI_PER_TABLE:
        return f"Die vier Spieler haben zusammen {total} Soli. Pro Tisch und Runde sind höchstens {MAX_SOLI_PER_TABLE} erlaubt."
    return None


def first_score_issue(con: sqlite3.Connection, tournament_id: int, *, through_round: int | None = None,
                      round_only: int | None = None) -> str | None:
    """Erste fehlerhafte Tischwertung, für Setzlisten bis einschließlich der Vorrunde."""
    params = (tournament_id, through_round, through_round, round_only, round_only)
    orphan_seat = con.execute("""
        SELECT s.round_no, s.table_no FROM tournament_seats s
        LEFT JOIN tournament_rounds r ON r.tournament_id=s.tournament_id AND r.round_no=s.round_no
        WHERE s.tournament_id=? AND (? IS NULL OR s.round_no <= ?) AND (? IS NULL OR s.round_no = ?)
          AND r.id IS NULL LIMIT 1
    """, params).fetchone()
    if orphan_seat:
        return f"Runde {orphan_seat['round_no']}, Tisch {orphan_seat['table_no']}: Der Sitzplan gehört zu keiner ausgelosten Runde."

    extra_score = con.execute("""
        SELECT sc.round_no, sc.table_no FROM tournament_scores sc
        LEFT JOIN tournament_seats s ON s.tournament_id=sc.tournament_id AND s.round_no=sc.round_no
                                    AND s.table_no=sc.table_no AND s.tp_id=sc.tp_id
        WHERE sc.tournament_id=? AND (? IS NULL OR sc.round_no <= ?) AND (? IS NULL OR sc.round_no = ?)
          AND s.id IS NULL LIMIT 1
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
        WHERE r.tournament_id=? AND (? IS NULL OR r.round_no <= ?) AND (? IS NULL OR r.round_no = ?)
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
