# app/routes/tournaments/admin.py
from __future__ import annotations

from flask import flash, redirect, request, url_for

from ... import db
from . import bp
from .helpers import (
    _get_tournament,
    _is_closed,
    _reopen_tournament_and_fix_addresses,
    _repair_addresses_from_tournament_years,
)


@bp.post("/tournaments/<int:tournament_id>/reopen")
def tournament_reopen(tournament_id: int):
    """
    DEV-Route: Turnier wieder öffnen (closed_at löschen)
    und Teilnahme-Marker/Counts in addresses zurückdrehen.
    """
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        if not _is_closed(t):
            flash("Turnier ist bereits offen.", "warning")
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

        try:
            affected = _reopen_tournament_and_fix_addresses(con, tournament_id)
            con.commit()
        except Exception as e:
            con.rollback()
            flash(f"Wiederöffnen fehlgeschlagen: {e}", "error")
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

    flash(f"Turnier wieder geöffnet. {affected} Adresse(n) korrigiert.", "ok")
    return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))


@bp.post("/tournaments/repair-addresses")
def tournaments_repair_addresses():
    """
    DEV-Route: Recalc addresses.* aus tournament_years (global)
    - participation_count = Anzahl Marker
    - last_tournament_at = rechter Marker
    Optional:
      only_active=1
      dry_run=1
    """
    only_active = (request.form.get("only_active") or "").strip() == "1"
    dry_run = (request.form.get("dry_run") or "").strip() == "1"

    with db.connect() as con:
        try:
            changed, scanned = _repair_addresses_from_tournament_years(con, only_active=only_active, tournament_id=None)
            if dry_run:
                con.rollback()
                flash(f"DRY-RUN: {changed}/{scanned} Adresse(n) würden geändert.", "warning")
            else:
                con.commit()
                flash(f"Repair abgeschlossen: {changed}/{scanned} Adresse(n) geändert.", "ok")
        except Exception as e:
            con.rollback()
            flash(f"Repair fehlgeschlagen: {e}", "error")

    return redirect(url_for("tournaments.tournaments_list"))


@bp.post("/tournaments/<int:tournament_id>/repair-addresses")
def tournament_repair_addresses(tournament_id: int):
    """
    DEV-Route: Recalc addresses.* aus tournament_years (nur Teilnehmer dieses Turniers)
    Optional:
      only_active=1
      dry_run=1
    """
    only_active = (request.form.get("only_active") or "").strip() == "1"
    dry_run = (request.form.get("dry_run") or "").strip() == "1"

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        try:
            changed, scanned = _repair_addresses_from_tournament_years(con, only_active=only_active, tournament_id=tournament_id)
            if dry_run:
                con.rollback()
                flash(f"DRY-RUN (Turnier): {changed}/{scanned} Adresse(n) würden geändert.", "warning")
            else:
                con.commit()
                flash(f"Repair (Turnier) abgeschlossen: {changed}/{scanned} Adresse(n) geändert.", "ok")
        except Exception as e:
            con.rollback()
            flash(f"Repair (Turnier) fehlgeschlagen: {e}", "error")

    return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))