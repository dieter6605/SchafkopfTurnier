"""Teilnehmer am Anmeldetisch über das Vereins-WLAN erfassen."""
from contextlib import closing

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from .. import db
from ..wlan import helper_logged_in
from .addresses import _default_ab_id
from .tournaments.helpers import _cap_ok, _get_tournament, _is_closed, _tournament_counts
from .tournaments.participants import create_registration_address, register_participant

bp = Blueprint("registration", __name__)


def _matches(con, book_id, tournament_id, query):
    # Nur die zur Identifikation freigegebenen Felder durchsuchen und ausgeben.
    words = query[:100].casefold().split()
    if not words:
        return []
    condition = " AND ".join("instr(fold(a.vorname || ' ' || a.nachname || ' ' || coalesce(a.wohnort,'')), ?) > 0"
                             for _ in words)
    return db.q(con, f"""
        SELECT a.id, a.vorname, a.nachname, a.wohnort, tp.player_no
        FROM addresses a LEFT JOIN tournament_participants tp ON tp.address_id=a.id AND tp.tournament_id=?
        WHERE a.addressbook_id=? AND {condition}
        ORDER BY a.nachname COLLATE NOCASE, a.vorname COLLATE NOCASE, a.wohnort, a.id
        LIMIT 26
    """, (tournament_id, book_id, *words))


@bp.route("/helfer/<int:tournament_id>/teilnehmer", methods=["GET", "POST"])
def desk(tournament_id):
    query = ((request.form if request.method == "POST" else request.args).get("q") or "").strip()[:100]
    draft = {key: request.form.get(key, "").strip() for key in ("vorname", "nachname", "wohnort")}
    error = ""
    response_status = 200
    saving = request.method == "POST"
    with closing(db.connect()) as con, con:
        con.execute("BEGIN IMMEDIATE" if saving else "BEGIN")
        generation = db.wlan_generation(con)
        if not helper_logged_in(tournament_id, "registration") or session.get("helper_generation") != generation:
            if saving:
                abort(403, description="Die Anmeldefreigabe ist beendet. Bitte den aktuellen Anmeldecode eingeben.")
            return redirect(url_for("wlan.signup", tournament_id=tournament_id))
        tournament = _get_tournament(con, tournament_id)
        if not tournament or _is_closed(tournament):
            abort(403, description="Dieses Turnier ist für die Anmeldung geschlossen.")
        book_id = _default_ab_id(con)
        counts = _tournament_counts(con, tournament_id)
        space = _cap_ok(tournament, counts["active"])
        con.create_function("fold", 1, lambda text: (text or "").strip().casefold())
        matches = _matches(con, book_id, tournament_id, query)
        if saving:
            action = request.form.get("action")
            address = None
            if not space:
                error = "Die maximale Teilnehmerzahl ist erreicht."
            elif action == "add":
                aid = request.form.get("address_id", type=int)
                address = next((row for row in matches[:25] if row["id"] == aid), None)
                if address is None:
                    error = "Der Treffer ist nicht mehr verfügbar. Bitte suche die Person erneut."
                elif address["player_no"] is not None:
                    error = f'Diese Person ist bereits mit Startnummer {address["player_no"]} erfasst.'
            elif action == "new":
                if not all(draft.values()):
                    error = "Bitte Vorname, Nachname und Wohnort eintragen."
                elif db.one(con, """SELECT id FROM addresses WHERE addressbook_id=?
                                     AND fold(vorname)=? AND fold(nachname)=? AND fold(wohnort)=? LIMIT 1""",
                            (book_id, draft["vorname"].casefold(), draft["nachname"].casefold(), draft["wohnort"].casefold())):
                    error = "Zu diesen Angaben gibt es bereits eine Adresse. Prüfe den Treffer, bevor du die Person anmeldest."
                    query = " ".join((draft["nachname"], draft["vorname"], draft["wohnort"]))[:100]
                    matches = _matches(con, book_id, tournament_id, query)
                else:
                    aid = create_registration_address(con, draft["nachname"], draft["vorname"], draft["wohnort"])
                    address = dict(draft, id=aid)
            else:
                abort(400)
            if not error:
                number = register_participant(con, tournament_id, address, new=action == "new", helper=True)
                flash(f'{address["vorname"]} {address["nachname"]} ist mit Startnummer {number} angemeldet.', "ok")
                return redirect(url_for("registration.desk", tournament_id=tournament_id), code=303)
            response_status = 422
    return render_template("wlan_registration.html", tournament=tournament, query=query, matches=matches[:25],
                           more_hits=len(matches) > 25, space=space, draft=draft, error=error,
                           new_open=saving and request.form.get("action") == "new"), response_status
