# app/routes/tournaments/draw.py
from __future__ import annotations

import random

from ... import db


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _history_pairs(con, tournament_id: int, round_lt: int) -> set[tuple[int, int]]:
    """
    Alle Paare (tp_id,tp_id), die vor round_lt schon mal am selben Tisch saßen.
    """
    rows = db.q(
        con,
        """
        SELECT round_no, table_no, tp_id
        FROM tournament_seats
        WHERE tournament_id=? AND round_no < ?
        ORDER BY round_no, table_no
        """,
        (tournament_id, round_lt),
    )
    by_rt: dict[tuple[int, int], list[int]] = {}
    for r in rows:
        key = (int(r["round_no"]), int(r["table_no"]))
        by_rt.setdefault(key, []).append(int(r["tp_id"]))

    pairs: set[tuple[int, int]] = set()
    for ids in by_rt.values():
        ids = list(dict.fromkeys(ids))
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.add(_pair(ids[i], ids[j]))
    return pairs


def _score_plan(tps: list[dict[str, int]], tables: list[list[int]], hist_pairs: set[tuple[int, int]]) -> int:
    """
    Kostenfunktion:
    - direkt benachbarte player_no (d==1) am selben Tisch: sehr harte Strafe
    - Wiedersehen (Paar schon mal gemeinsam am Tisch): harte Strafe
    - optional: d==2 am selben Tisch: kleine Strafe
    """
    pno = {tp["id"]: tp["player_no"] for tp in tps}
    score = 0

    for tab in tables:
        for i in range(len(tab)):
            for j in range(i + 1, len(tab)):
                a, b = tab[i], tab[j]
                d = abs(pno[a] - pno[b])
                if d == 1:
                    score += 10_000
                elif d == 2:
                    score += 500

                if _pair(a, b) in hist_pairs:
                    score += 2_000

    return score


def _random_tables(tp_ids: list[int], table_size: int = 4) -> list[list[int]]:
    return [tp_ids[i : i + table_size] for i in range(0, len(tp_ids), table_size)]


def _improve_tables(tps: list[dict[str, int]], tp_ids: list[int], hist_pairs: set[tuple[int, int]]) -> list[list[int]]:
    best_tables: list[list[int]] | None = None
    best_score = 10**18

    # Mehrere Random-Restarts
    for _ in range(40):
        ids = tp_ids[:]
        random.shuffle(ids)
        tables = _random_tables(ids, 4)
        cur = _score_plan(tps, tables, hist_pairs)

        # Lokale Verbesserung per Random-Swaps
        for _iter in range(4000):
            t1 = random.randrange(len(tables))
            t2 = random.randrange(len(tables))
            i1 = random.randrange(4)
            i2 = random.randrange(4)
            if t1 == t2 and i1 == i2:
                continue

            tables[t1][i1], tables[t2][i2] = tables[t2][i2], tables[t1][i1]
            nxt = _score_plan(tps, tables, hist_pairs)

            if nxt <= cur:
                cur = nxt
                if cur == 0:
                    break
            else:
                tables[t1][i1], tables[t2][i2] = tables[t2][i2], tables[t1][i1]

        if cur < best_score:
            best_score = cur
            best_tables = [t[:] for t in tables]

        if best_score == 0:
            break

    return best_tables or _random_tables(tp_ids, 4)