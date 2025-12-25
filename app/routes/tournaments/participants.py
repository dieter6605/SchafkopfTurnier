# app/routes/tournaments/participants.py
from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for

from ... import db
from ..addresses import _default_ab_id, _upsert_wohnort
from . import bp
from .helpers import (
    _cap_ok,
    _display_name,
    _find_gaps,
    _get_tournament,
    _next_free_player_no,
    _pop_session_gaps,
    _read_tournament_form,  # (nicht genutzt, aber ok falls du später erweiterst)
    _renumber_all,
    _renumber_from,
    _search_addresses,
    _session_gaps_key,
    _to_int,
    _tournament_counts,
)


@bp.get("/tournaments/<int:tournament_id>/participants")
def tournament_participants(tournament_id: int):
    qtxt = (request.args.get("q") or "").strip()
    show_gaps = (request.args.get("show_gaps") or "0") == "1"

    gaps: list[int] = _pop_session_gaps(tournament_id) if show_gaps else []

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)
        cap_ok = _cap_ok(t, counts["participants"])

        hits = _search_addresses(con, qtxt) if qtxt else []
        already = db.q(con, "SELECT address_id FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
        already_ids = {int(r["address_id"]) for r in already}
        hits = [h for h in hits if int(h["id"]) not in already_ids]

        participants = db.q(
            con,
            """
            SELECT tp.*,
                   a.nachname, a.vorname, a.wohnort,
                   a.telefon, a.email, a.status
            FROM tournament_participants tp
            JOIN addresses a ON a.id=tp.address_id
            WHERE tp.tournament_id=?
            ORDER BY tp.player_no
            """,
            (tournament_id,),
        )

    return render_template(
        "tournament_participants.html",
        t=t,
        counts=counts,
        q=qtxt,
        hits=hits,
        participants=participants,
        cap_ok=cap_ok,
        show_gaps=show_gaps,
        gaps=gaps,
    )


@bp.post("/tournaments/<int:tournament_id>/participants/add/<int:address_id>")
def tournament_participant_add(tournament_id: int, address_id: int):
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)
        if not _cap_ok(t, counts["participants"]):
            flash("Maximale Teilnehmerzahl erreicht – keine weitere Erfassung möglich.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        dup = db.one(
            con,
            "SELECT 1 FROM tournament_participants WHERE tournament_id=? AND address_id=?",
            (tournament_id, address_id),
        )
        if dup:
            flash("Teilnehmer bereits vorhanden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        pno = _next_free_player_no(con, tournament_id)

        con.execute(
            """
            INSERT INTO tournament_participants
              (tournament_id, player_no, address_id, display_name, created_at, updated_at)
            VALUES (?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (tournament_id, pno, address_id, _display_name(a)),
        )
        con.commit()

    flash(f"Teilnehmer übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/quickadd")
def tournament_participant_quickadd(tournament_id: int):
    f = request.form
    q = (f.get("q") or "").strip()

    nachname = (f.get("nachname") or "").strip()
    vorname = (f.get("vorname") or "").strip()
    wohnort = (f.get("wohnort") or "").strip()

    if not nachname or not vorname or not wohnort:
        flash("Pflichtfelder fehlen (Nachname, Vorname, Wohnort).", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

    plz = (f.get("plz") or "").strip() or None
    ort = (f.get("ort") or "").strip() or None

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)
        if not _cap_ok(t, counts["participants"]):
            flash("Maximale Teilnehmerzahl erreicht – keine weitere Erfassung möglich.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        _upsert_wohnort(con, wohnort, plz, ort)
        ab_id = _default_ab_id(con)

        cur = con.execute(
            """
            INSERT INTO addresses(
              addressbook_id, nachname, vorname, wohnort,
              plz, ort, strasse, hausnummer,
              telefon, email, status, notizen,
              created_at, updated_at
            )
            VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, datetime('now'), datetime('now'))
            """,
            (
                ab_id,
                nachname,
                vorname,
                wohnort,
                plz,
                ort,
                (f.get("strasse") or "").strip() or None,
                (f.get("hausnummer") or "").strip() or None,
                (f.get("telefon") or "").strip() or None,
                (f.get("email") or "").strip() or None,
                "aktiv",
                (f.get("notizen") or "").strip() or None,
            ),
        )

        address_id = int(cur.lastrowid)
        pno = _next_free_player_no(con, tournament_id)

        con.execute(
            """
            INSERT INTO tournament_participants
              (tournament_id, player_no, address_id, display_name, created_at, updated_at)
            VALUES (?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (tournament_id, pno, address_id, f"{nachname}, {vorname} · {wohnort}"),
        )
        con.commit()

    flash(f"Teilnehmer neu angelegt und übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/<int:tp_id>/remove")
def tournament_participant_remove(tournament_id: int, tp_id: int):
    # ✅ NEU: Standard ist "NICHT renummerieren" -> Nummernlücke bleibt bestehen.
    # Renummerierung passiert nur noch, wenn explizit renumber=1 gesendet wird
    # (z.B. falls du später wieder eine Option einbauen willst).
    renumber = _to_int(request.form.get("renumber"), 0)  # Default = 0
    q = (request.form.get("q") or request.args.get("q") or "").strip()

    with db.connect() as con:
        row = db.one(
            con,
            "SELECT id, player_no FROM tournament_participants WHERE id=? AND tournament_id=?",
            (tp_id, tournament_id),
        )
        if not row:
            flash("Teilnehmer nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        removed_no = int(row["player_no"] or 0)

        con.execute("DELETE FROM tournament_participants WHERE id=? AND tournament_id=?", (tp_id, tournament_id))

        if renumber and removed_no > 0:
            _renumber_from(con, tournament_id, removed_no)

        con.commit()

    flash("Teilnehmer entfernt.", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/renumber-from")
def tournament_participants_renumber_from(tournament_id: int):
    start_no = _to_int(request.form.get("start_no"), 0)
    q = (request.form.get("q") or request.args.get("q") or "").strip()

    if start_no <= 0:
        flash("Renummerieren: Startnummer fehlt/ungültig.", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

    with db.connect() as con:
        _renumber_from(con, tournament_id, start_no)
        con.commit()

    flash(f"Neu durchnummeriert ab Nr {start_no}.", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/check-numbers")
def tournament_participants_check_numbers(tournament_id: int):
    renumber = _to_int(request.form.get("renumber"), 0)  # 0/1
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        if renumber:
            _renumber_all(con, tournament_id)
            con.commit()
            flash("Teilnehmernummern wurden neu durchnummeriert (1..N).", "ok")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        gaps = _find_gaps(con, tournament_id)
        session[_session_gaps_key(tournament_id)] = gaps
        if gaps:
            flash(f"Prüfung: {len(gaps)} Lücke(n) gefunden.", "error")
        else:
            flash("Prüfung: keine Lücken gefunden.", "ok")

    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q, show_gaps="1"))


@bp.post("/tournaments/<int:tournament_id>/participants/swap")
def tournament_participant_swap(tournament_id: int):
    tp_id = _to_int(request.form.get("tp_id"), 0)
    new_address_id = _to_int(request.form.get("new_address_id"), 0)
    q = (request.form.get("q") or request.args.get("q") or "").strip()

    if tp_id <= 0 or new_address_id <= 0:
        flash("Swap: tp_id oder neue Address-ID fehlt.", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

    with db.connect() as con:
        tp = db.one(
            con,
            "SELECT id, address_id, player_no FROM tournament_participants WHERE id=? AND tournament_id=?",
            (tp_id, tournament_id),
        )
        if not tp:
            flash("Swap: Teilnehmer nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (new_address_id,))
        if not a:
            flash("Swap: Ziel-Adresse nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        dup = db.one(
            con,
            "SELECT 1 FROM tournament_participants WHERE tournament_id=? AND address_id=? LIMIT 1",
            (tournament_id, new_address_id),
        )
        if dup:
            flash("Swap: Diese Adresse ist bereits als Teilnehmer erfasst.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        con.execute(
            """
            UPDATE tournament_participants
            SET address_id=?,
                display_name=?,
                updated_at=datetime('now')
            WHERE id=? AND tournament_id=?
            """,
            (new_address_id, _display_name(a), tp_id, tournament_id),
        )
        con.commit()

    flash("Teilnehmer ersetzt (Nummer blieb gleich).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))