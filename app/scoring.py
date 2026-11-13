"""Gemeinsame Prüfung der Tischwertungen für Spielbetrieb und Wiederherstellung."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class TableStatus:
    seats: int
    scores: int
    issue: str | None

    @property
    def complete(self):
        return self.scores == 4 and self.issue is None


def _table_status(seats):
    scored = [row for row in seats if row["score_id"] is not None]
    issue = None
    if len(seats) != 4 or {row["seat"] for row in seats} != {"A", "B", "C", "D"}:
        issue = "Die vier Sitzplätze A bis D sind unvollständig oder fehlerhaft."
    elif len({row["participant_id"] for row in seats}) != 4 or any(row["participant_id"] is None for row in seats):
        issue = "Vier verschiedene Teilnehmer dieses Turniers müssen am Tisch sitzen."
    elif any(not isinstance(row["soli"], int) or row["soli"] < 0 for row in scored):
        issue = "Soli müssen nichtnegative ganze Zahlen sein."
    elif (soli_issue := soli_total_issue(sum(row["soli"] for row in scored))):
        issue = soli_issue
    elif any(not isinstance(row["points"], int) for row in scored):
        issue = "Die Punkte müssen ganze Zahlen sein."
    elif len(scored) == 4:
        total = sum(row["points"] for row in scored)
        if total != 0:
            issue = f"Die Punktesumme beträgt {total} und muss 0 sein."
    return TableStatus(len(seats), len(scored), issue)


def _assignment_issues(con, params):
    for row in con.execute("""
        SELECT DISTINCT s.round_no, s.table_no FROM tournament_seats s
        LEFT JOIN tournament_rounds r ON r.tournament_id=s.tournament_id AND r.round_no=s.round_no
        WHERE s.tournament_id=? AND (? IS NULL OR s.round_no <= ?) AND (? IS NULL OR s.round_no = ?)
          AND r.id IS NULL ORDER BY s.round_no, s.table_no
    """, params):
        yield row["round_no"], row["table_no"], "Der Sitzplan gehört zu keiner ausgelosten Runde."

    for row in con.execute("""
        SELECT DISTINCT sc.round_no, sc.table_no FROM tournament_scores sc
        LEFT JOIN tournament_seats s ON s.tournament_id=sc.tournament_id AND s.round_no=sc.round_no
                                    AND s.table_no=sc.table_no AND s.tp_id=sc.tp_id
        WHERE sc.tournament_id=? AND (? IS NULL OR sc.round_no <= ?) AND (? IS NULL OR sc.round_no = ?)
          AND s.id IS NULL ORDER BY sc.round_no, sc.table_no
    """, params):
        yield row["round_no"], row["table_no"], "Ein Ergebnis hat keinen passenden Sitzplatz."


def _seat_scores(con, params):
    return con.execute("""
        SELECT r.round_no, s.table_no, s.seat, tp.id AS participant_id, sc.id AS score_id, sc.points, sc.soli
        FROM tournament_rounds r
        LEFT JOIN tournament_seats s ON s.tournament_id=r.tournament_id AND s.round_no=r.round_no
        LEFT JOIN tournament_participants tp ON tp.id=s.tp_id AND tp.tournament_id=r.tournament_id
        LEFT JOIN tournament_scores sc ON sc.tournament_id=s.tournament_id AND sc.round_no=s.round_no
                                      AND sc.table_no=s.table_no AND sc.tp_id=s.tp_id
        WHERE r.tournament_id=? AND (? IS NULL OR r.round_no <= ?) AND (? IS NULL OR r.round_no = ?)
        ORDER BY r.round_no, s.table_no, s.seat
    """, params).fetchall()


def round_table_statuses(con, tournament_id: int, round_no: int) -> dict[int, TableStatus]:
    params = (tournament_id, None, None, round_no, round_no)
    statuses = {number: _table_status(list(rows))
                for number, rows in groupby(_seat_scores(con, params), key=itemgetter("table_no"))
                if number is not None}
    for _, number, reason in _assignment_issues(con, params):
        status = statuses.get(number, TableStatus(0, 0, None))
        statuses[number] = replace(status, issue=reason)
    return statuses


def first_score_issue(con: sqlite3.Connection, tournament_id: int, *, through_round: int | None = None,
                      round_only: int | None = None) -> str | None:
    """Erste fehlerhafte Tischwertung, für Setzlisten bis einschließlich der Vorrunde."""
    params = (tournament_id, through_round, through_round, round_only, round_only)
    for rn, tn, issue in _assignment_issues(con, params):
        return f"Runde {rn}, Tisch {tn}: {issue}"

    rows = _seat_scores(con, params)
    if not rows and through_round is None:
        return "Es wurde noch keine Runde ausgelost."
    last_seen = 0
    for (round_no, table_no), group in groupby(rows, key=itemgetter("round_no", "table_no")):
        if through_round is not None and round_no > last_seen + 1:
            return f"Runde {last_seen + 1} wurde noch nicht ausgelost."
        last_seen = round_no
        if table_no is None:
            return f"Runde {round_no}: Der Sitzplan fehlt."
        status = _table_status(list(group))
        label = f"Runde {round_no}, Tisch {table_no}"
        if status.issue:
            return f"{label}: {status.issue}"
        if not status.complete:
            return f"{label}: {status.scores} von 4 Ergebnissen erfasst."
    if through_round is not None and last_seen < through_round:
        return f"Runde {last_seen + 1} wurde noch nicht ausgelost."
    return None
