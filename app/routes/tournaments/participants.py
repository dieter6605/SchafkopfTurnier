# app/routes/tournaments/participants.py
from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, session, url_for

from ... import db
from ..addresses import _default_ab_id, _upsert_wohnort
from . import bp
from .helpers import (
    _cap_ok,
    _closed_at_str,
    _display_name,
    _event_date_to_marker_prefix,
    _find_gaps,
    _get_tournament,
    _guard_closed_redirect,
    _is_closed,
    _next_free_player_no,
    _pop_session_gaps,
    _renumber_all,
    _renumber_from,
    _search_addresses,
    _session_gaps_key,
    _to_int,
    _tournament_counts,
    _validate_marker_for_event_date,
)


def _upsert_wohnort_safe(con, wohnort: str, plz: str | None, ort: str | None) -> None:
    wohnort = (wohnort or "").strip()
    if not wohnort:
        return
    if (plz and str(plz).strip()) or (ort and str(ort).strip()):
        _upsert_wohnort(con, wohnort, plz, ort)


def _is_address_swap_blocked_status(status: str | None) -> bool:
    """
    Welche Adressen dürfen NICHT als Swap-Ziel gewählt werden?
    - mindestens: 'gesperrt'
    Optional (wenn du es strenger willst): alles außer 'aktiv'
    """
    s = (status or "").strip().lower()
    if s == "gesperrt":
        return True

    # OPTIONAL: strenger => nur aktiv zulassen
    # if s and s != "aktiv":
    #     return True

    return False


def _audit_log(
    con,
    *,
    tournament_id: int,
    action: str,
    tp_id: int | None = None,
    tp_id_2: int | None = None,
    address_id_old: int | None = None,
    address_id_new: int | None = None,
    address_id_old_2: int | None = None,
    address_id_new_2: int | None = None,
    note: str | None = None,
) -> None:
    """
    Audit-Log Eintrag schreiben (best effort).
    Wichtig: Fehler im Audit-Log dürfen niemals die Fachfunktion blockieren.
    Erwartet DB-Tabelle:
      audit_log(id, created_at, tournament_id, action, tp_id, tp_id_2,
                address_id_old, address_id_new, address_id_old_2, address_id_new_2, note)
    """
    try:
        con.execute(
            """
            INSERT INTO audit_log(
              tournament_id, action,
              tp_id, tp_id_2,
              address_id_old, address_id_new,
              address_id_old_2, address_id_new_2,
              note,
              created_at
            )
            VALUES (?,?,?,?, ?,?,?,?, ?, datetime('now'))
            """,
            (
                int(tournament_id),
                str(action or "").strip() or "unknown",
                (int(tp_id) if tp_id else None),
                (int(tp_id_2) if tp_id_2 else None),
                (int(address_id_old) if address_id_old else None),
                (int(address_id_new) if address_id_new else None),
                (int(address_id_old_2) if address_id_old_2 else None),
                (int(address_id_new_2) if address_id_new_2 else None),
                (str(note).strip() if note else None),
            ),
        )
    except Exception:
        pass


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
                   a.plz, a.ort, a.strasse, a.hausnummer,
                   a.telefon, a.email, a.status
            FROM tournament_participants tp
            JOIN addresses a ON a.id=tp.address_id
            WHERE tp.tournament_id=?
            ORDER BY tp.created_at DESC, tp.id DESC
            """,
            (tournament_id,),
        )
        
        audit = db.q(
            con,
            """
            SELECT *
            FROM audit_log
            WHERE tournament_id=?
            ORDER BY id DESC
            LIMIT 200
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
        audit=audit,
    )


# -------------------------------------------------------------------------
# ✅ Swap-Suche (Modal) – liefert JSON Treffer aus dem Adressbuch
# -------------------------------------------------------------------------
@bp.get("/tournaments/<int:tournament_id>/participants/swap-search")
def tournament_participants_swap_search(tournament_id: int):
    qtxt = (request.args.get("q") or "").strip()
    limit = _to_int(request.args.get("limit"), 25)
    if limit <= 0:
        limit = 25
    if limit > 50:
        limit = 50

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            return jsonify({"ok": False, "error": "Turnier nicht gefunden."}), 404
        if _is_closed(t):
            return jsonify({"ok": False, "error": "Turnier ist abgeschlossen."}), 409

        if len(qtxt) < 2:
            return jsonify({"ok": True, "items": []})

        hits = _search_addresses(con, qtxt)

        rows = db.q(
            con,
            "SELECT id, address_id, player_no FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,),
        )
        by_aid = {int(r["address_id"]): {"tp_id": int(r["id"]), "player_no": int(r["player_no"] or 0)} for r in rows}

        out = []
        for h in hits:
            aid = int(h["id"])

            status = (h.get("status") if hasattr(h, "get") else h["status"]) if "status" in h.keys() else None
            if _is_address_swap_blocked_status(status):
                continue

            info = by_aid.get(aid)
            out.append(
                {
                    "id": aid,
                    "nachname": str(h["nachname"] or ""),
                    "vorname": str(h["vorname"] or ""),
                    "wohnort": str(h["wohnort"] or ""),
                    "plz": str(h["plz"] or "") if "plz" in h.keys() else "",
                    "ort": str(h["ort"] or "") if "ort" in h.keys() else "",
                    "email": str(h["email"] or "") if "email" in h.keys() else "",
                    "status": str(status or ""),
                    "in_tournament": bool(info),
                    "player_no": (info["player_no"] if info else None),
                    "tp_id": (info["tp_id"] if info else None),
                }
            )
            if len(out) >= limit:
                break

    return jsonify({"ok": True, "items": out})


@bp.post("/tournaments/<int:tournament_id>/participants/add/<int:address_id>")
def tournament_participant_add(tournament_id: int, address_id: int):
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Teilnehmer-Erfassung",
            endpoint="tournaments.tournament_participants",
            endpoint_kwargs={"tournament_id": tournament_id, "q": q},
        )
        if resp:
            return resp

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

        cur = con.execute(
            """
            INSERT INTO tournament_participants
              (tournament_id, player_no, address_id, display_name, created_at, updated_at)
            VALUES (?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (tournament_id, pno, address_id, _display_name(a)),
        )
        tp_id = int(cur.lastrowid or 0) if cur else 0

        _audit_log(
            con,
            tournament_id=tournament_id,
            action="add",
            tp_id=(tp_id if tp_id > 0 else None),
            address_id_new=address_id,
            note=f"Teilnehmer übernommen (Nr {pno}).",
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

        resp = _guard_closed_redirect(
            t,
            action="Teilnehmer-Erfassung",
            endpoint="tournaments.tournament_participants",
            endpoint_kwargs={"tournament_id": tournament_id, "q": q},
        )
        if resp:
            return resp

        counts = _tournament_counts(con, tournament_id)
        if not _cap_ok(t, counts["participants"]):
            flash("Maximale Teilnehmerzahl erreicht – keine weitere Erfassung möglich.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        _upsert_wohnort_safe(con, wohnort, plz, ort)
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

        cur2 = con.execute(
            """
            INSERT INTO tournament_participants
              (tournament_id, player_no, address_id, display_name, created_at, updated_at)
            VALUES (?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (tournament_id, pno, address_id, f"{nachname}, {vorname} · {wohnort}"),
        )
        tp_id = int(cur2.lastrowid or 0) if cur2 else 0

        _audit_log(
            con,
            tournament_id=tournament_id,
            action="quickadd",
            tp_id=(tp_id if tp_id > 0 else None),
            address_id_new=address_id,
            note=f"Adresse neu angelegt + übernommen (Nr {pno}).",
        )

        con.commit()

    flash(f"Teilnehmer neu angelegt und übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/close")
def tournament_close_participations(tournament_id: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        if _is_closed(t):
            ca = _closed_at_str(t)
            flash(f"Dieses Turnier ist bereits abgeschlossen (seit {ca}).", "info")
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

        event_date = str(t["event_date"] or "").strip()
        marker = (t["marker"] or "").strip()

        if not marker:
            pref = _event_date_to_marker_prefix(event_date) or "JJMMTT"
            flash(
                f"Marker fehlt. Bitte im Turnier den 10-stelligen Marker setzen (z. B. {pref}ABCD) und erneut abschließen.",
                "error",
            )
            return redirect(url_for("tournaments.tournament_edit", tournament_id=tournament_id))

        msg = _validate_marker_for_event_date(marker, event_date)
        if msg:
            flash(f"Marker ungültig: {msg}", "error")
            return redirect(url_for("tournaments.tournament_edit", tournament_id=tournament_id))

        affected_row = db.one(
            con,
            "SELECT COUNT(DISTINCT address_id) AS c FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,),
        )
        affected = int(affected_row["c"] or 0) if affected_row else 0
        if affected <= 0:
            flash("Keine Teilnehmer vorhanden – nichts zu aktualisieren.", "info")
            return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))

        con.execute(
            """
            UPDATE addresses
            SET
              tournament_years =
                CASE
                  WHEN COALESCE(tournament_years,'') = '' THEN ?
                  WHEN instr(',' || tournament_years || ',', ',' || ? || ',') > 0 THEN tournament_years
                  ELSE tournament_years || ',' || ?
                END,

              participation_count =
                CASE
                  WHEN instr(',' || COALESCE(tournament_years,'') || ',', ',' || ? || ',') > 0 THEN COALESCE(participation_count,0)
                  ELSE COALESCE(participation_count,0) + 1
                END,

              last_tournament_at = ?,
              updated_at = datetime('now')
            WHERE id IN (
              SELECT DISTINCT tp.address_id
              FROM tournament_participants tp
              WHERE tp.tournament_id = ?
            )
            """,
            (marker, marker, marker, marker, marker, tournament_id),
        )

        try:
            con.execute(
                """
                UPDATE tournaments
                SET closed_at = COALESCE(closed_at, datetime('now')),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (tournament_id,),
            )
        except Exception:
            pass

        con.commit()

    flash(f"Turnier abgeschlossen: Marker {marker} gepflegt ({affected} Teilnehmer).", "ok")
    return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))


@bp.post("/tournaments/<int:tournament_id>/participants/<int:tp_id>/remove")
def tournament_participant_remove(tournament_id: int, tp_id: int):
    renumber = _to_int(request.form.get("renumber"), 0)
    q = (request.form.get("q") or request.args.get("q") or "").strip()

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Entfernen",
            endpoint="tournaments.tournament_participants",
            endpoint_kwargs={"tournament_id": tournament_id, "q": q},
        )
        if resp:
            return resp

        row = db.one(
            con,
            "SELECT id, player_no, address_id FROM tournament_participants WHERE id=? AND tournament_id=?",
            (tp_id, tournament_id),
        )
        if not row:
            flash("Teilnehmer nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        removed_no = int(row["player_no"] or 0)
        old_address_id = int(row["address_id"] or 0)

        con.execute("DELETE FROM tournament_participants WHERE id=? AND tournament_id=?", (tp_id, tournament_id))

        _audit_log(
            con,
            tournament_id=tournament_id,
            action="remove",
            tp_id=tp_id,
            address_id_old=(old_address_id if old_address_id > 0 else None),
            note=f"Teilnehmer entfernt (Nr {removed_no}).",
        )

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
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Renummerieren",
            endpoint="tournaments.tournament_participants",
            endpoint_kwargs={"tournament_id": tournament_id, "q": q},
        )
        if resp:
            return resp

        _renumber_from(con, tournament_id, start_no)
        con.commit()

    flash(f"Neu durchnummeriert ab Nr {start_no}.", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


@bp.post("/tournaments/<int:tournament_id>/participants/check-numbers")
def tournament_participants_check_numbers(tournament_id: int):
    renumber = _to_int(request.form.get("renumber"), 0)
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Änderungen",
            endpoint="tournaments.tournament_participants",
            endpoint_kwargs={"tournament_id": tournament_id, "q": q},
        )
        if resp:
            return resp

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
        flash("Swap: Teilnehmer oder Zieladresse fehlt.", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Swap",
            endpoint="tournaments.tournament_participants",
            endpoint_kwargs={"tournament_id": tournament_id, "q": q},
        )
        if resp:
            return resp

        tp = db.one(
            con,
            """
            SELECT id, address_id, player_no, display_name
            FROM tournament_participants
            WHERE id=? AND tournament_id=?
            """,
            (tp_id, tournament_id),
        )
        if not tp:
            flash("Swap: Teilnehmer nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        old_address_id = int(tp["address_id"] or 0)
        if old_address_id <= 0:
            flash("Swap: Aktuelle Adresse ungültig.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        a_new = db.one(con, "SELECT * FROM addresses WHERE id=?", (new_address_id,))
        if not a_new:
            flash("Swap: Ziel-Adresse nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        if _is_address_swap_blocked_status(a_new["status"] if "status" in a_new.keys() else None):
            flash("Swap: Ziel-Adresse ist gesperrt und darf nicht gewählt werden.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        if int(new_address_id) == int(old_address_id):
            flash("Swap: Ziel ist bereits die aktuelle Person.", "info")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        other = db.one(
            con,
            """
            SELECT id, address_id, player_no, display_name
            FROM tournament_participants
            WHERE tournament_id=? AND address_id=?
            LIMIT 1
            """,
            (tournament_id, new_address_id),
        )

        # ----------------------------
        # ✅ TAUSCH (Swap innerhalb Turnier)
        # SQLite UNIQUE-safe via temp address_id
        # ----------------------------
        if other and int(other["id"]) != int(tp_id):
            other_tp_id = int(other["id"])
            other_player_no = int(other["player_no"] or 0)
            this_player_no = int(tp["player_no"] or 0)

            # Displaynames sauber bauen
            a_old = db.one(con, "SELECT * FROM addresses WHERE id=?", (old_address_id,))
            old_display = _display_name(a_old) if a_old else str(tp.get("display_name") or "")
            new_display = _display_name(a_new)

            # 1) finde eine existierende Adresse, die NICHT Teilnehmer ist (FK ok) und nicht gesperrt
            tmp = db.one(
                con,
                """
                SELECT a.id
                FROM addresses a
                WHERE a.id NOT IN (
                  SELECT tp.address_id
                  FROM tournament_participants tp
                  WHERE tp.tournament_id=?
                )
                  AND a.id NOT IN (?, ?)
                  AND COALESCE(LOWER(a.status),'') != 'gesperrt'
                ORDER BY a.id ASC
                LIMIT 1
                """,
                (tournament_id, old_address_id, new_address_id),
            )

            created_tmp = False
            tmp_id = int(tmp["id"]) if tmp else 0

            # 2) wenn keine freie Adresse existiert: Dummy-Adresse anlegen und danach wieder löschen
            if tmp_id <= 0:
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
                        "__TEMP_SWAP__",
                        "__TEMP__",
                        "__TEMP__",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        "gesperrt",
                        "Technischer Zwischeneintrag für Teilnehmer-Tausch (wird sofort wieder gelöscht).",
                    ),
                )
                tmp_id = int(cur.lastrowid)
                created_tmp = True

            try:
                # 3) Swap in 3 Schritten über tmp_id (keine UNIQUE-Kollision möglich)
                #    Wichtig: innerhalb einer Transaktion
                con.execute("BEGIN IMMEDIATE")

                # tp -> tmp
                con.execute(
                    """
                    UPDATE tournament_participants
                    SET address_id=?, display_name=?, updated_at=datetime('now')
                    WHERE id=? AND tournament_id=?
                    """,
                    (tmp_id, "__TEMP_SWAP__", tp_id, tournament_id),
                )

                # other -> old
                con.execute(
                    """
                    UPDATE tournament_participants
                    SET address_id=?, display_name=?, updated_at=datetime('now')
                    WHERE id=? AND tournament_id=?
                    """,
                    (old_address_id, old_display, other_tp_id, tournament_id),
                )

                # tp -> new
                con.execute(
                    """
                    UPDATE tournament_participants
                    SET address_id=?, display_name=?, updated_at=datetime('now')
                    WHERE id=? AND tournament_id=?
                    """,
                    (new_address_id, new_display, tp_id, tournament_id),
                )

                # Dummy-Adresse wieder entfernen, falls wir sie erzeugt haben
                if created_tmp and tmp_id > 0:
                    con.execute("DELETE FROM addresses WHERE id=?", (tmp_id,))

                _audit_log(
                    con,
                    tournament_id=tournament_id,
                    action="swap_exchange",
                    tp_id=tp_id,
                    tp_id_2=other_tp_id,
                    address_id_old=old_address_id,
                    address_id_new=new_address_id,
                    address_id_old_2=new_address_id,
                    address_id_new_2=old_address_id,
                    note=f"Teilnehmer getauscht (Nr {this_player_no} ↔ Nr {other_player_no}).",
                )

                con.commit()

            except Exception as e:
                try:
                    con.rollback()
                except Exception:
                    pass
                flash(f"Swap: Tauschen fehlgeschlagen ({e}).", "error")
                return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

            flash(f"Teilnehmer getauscht (Nr {this_player_no} ↔ Nr {other_player_no}).", "ok")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))

        # ----------------------------
        # ✅ ERSETZEN (Ziel nicht im Turnier)
        # ----------------------------
        con.execute(
            """
            UPDATE tournament_participants
            SET address_id=?,
                display_name=?,
                updated_at=datetime('now')
            WHERE id=? AND tournament_id=?
            """,
            (new_address_id, _display_name(a_new), tp_id, tournament_id),
        )

        _audit_log(
            con,
            tournament_id=tournament_id,
            action="swap_replace",
            tp_id=tp_id,
            address_id_old=old_address_id,
            address_id_new=new_address_id,
            note=f"Teilnehmer ersetzt (Nr {int(tp['player_no'] or 0)}).",
        )

        con.commit()

    flash("Teilnehmer ersetzt (Nummer blieb gleich).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))