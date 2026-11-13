"""Stand und nächste Aufgabe für Startseite und Turnierübersicht."""
from dataclasses import dataclass

from flask import url_for

from ...scoring import TableStatus, round_table_statuses
from .helpers import _is_closed, _tournament_counts


@dataclass
class RoundOverview:
    number: int
    tables: dict[int, TableStatus]

    @property
    def done(self):
        return sum(table.complete for table in self.tables.values())

    @property
    def review(self):
        return sum(table.issue is not None for table in self.tables.values())

    @property
    def open_count(self):
        return len(self.tables) - self.done - self.review


@dataclass
class NextAction:
    title: str
    label: str
    url: str
    hint: str


def _next_action(t, counts, rounds):
    tid = t["id"]
    if _is_closed(t):
        return NextAction("Endstand ansehen", "Gesamtwertung öffnen",
                          url_for("tournaments.tournament_standings_overall", tournament_id=tid),
                          "Die Gesamtwertung und die Turnierausgaben stehen bereit.")

    first_open = None
    for rnd in reversed(rounds):
        if not rnd.tables:
            return NextAction("Sitzplan prüfen", f"Runde {rnd.number} prüfen",
                              url_for("tournaments.tournament_round_view", tournament_id=tid, round_no=rnd.number),
                              f"Für Runde {rnd.number} fehlt der Sitzplan.")
        for number, table in sorted(rnd.tables.items()):
            if table.issue:
                path = (url_for("tournaments.tournament_round_results_table", tournament_id=tid,
                                round_no=rnd.number, table_no=number, source="overview") if table.seats == 4 else
                        url_for("tournaments.tournament_round_view", tournament_id=tid, round_no=rnd.number))
                return NextAction(f"Wertung in Runde {rnd.number} prüfen", f"Tisch {number} prüfen",
                                  path, f"Tisch {number} · {table.issue}")
            if first_open is None and not table.complete:
                first_open = (rnd.number, number, table.scores)
    if first_open:
        rn, number, scores = first_open
        return NextAction(f"Runde {rn} vervollständigen", f"Tisch {number} erfassen",
                          url_for("tournaments.tournament_round_results_table", tournament_id=tid,
                                  round_no=rn, table_no=number, source="overview"),
                          f"An Tisch {number} sind {scores} von 4 Ergebnissen erfasst.")

    if counts["active"] < 4 or counts["rest"]:
        missing = 4 - counts["rest"]
        hint = ("Für vollständige Vierertische fehlt noch eine Person." if missing == 1 else
                f"Für vollständige Vierertische fehlen noch {missing} Teilnehmer.")
        return NextAction("Teilnehmerliste ergänzen", "Teilnehmer erfassen",
                          url_for("tournaments.tournament_participants", tournament_id=tid), hint)
    number = rounds[-1].number + 1 if rounds else 1
    title = f"Runde {rounds[-1].number} ist vollständig" if rounds else "Erste Runde vorbereiten"
    tables = counts["tables"]
    hint = ("4 aktive Teilnehmer ergeben einen vollständigen Vierertisch." if tables == 1 else
            f"{counts['active']} aktive Teilnehmer ergeben {tables} vollständige Vierertische.")
    return NextAction(title, f"Runde {number} vorbereiten",
                      url_for("tournaments.tournament_round_view", tournament_id=tid, round_no=number), hint)


def tournament_overview(con, t):
    tid = t["id"]
    counts = _tournament_counts(con, tid)
    numbers = con.execute("SELECT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no", (tid,))
    rounds = [RoundOverview(number, round_table_statuses(con, tid, number)) for (number,) in numbers]
    return dict(counts=counts, rounds=rounds, current=rounds[-1] if rounds else None,
                action=_next_action(t, counts, rounds))
