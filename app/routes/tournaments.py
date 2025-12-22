# app/routes/tournaments.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db
from .addresses import _default_ab_id, _upsert_wohnort

bp = Blueprint("tournaments", __name__)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _now_local_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _display_name(a: Any) -> str:
    wohnort = (a["wohnort"] or "").strip()
    base = f"{a['nachname']}, {a['vorname']}"
    return f"{base} · {wohnort}" if wohnort else base


def _next_free_player_no(con, tournament_id: int) -> int:
    rows = db.q(con, "SELECT player_no FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
    used = {int(r["player_no"]) for r in rows if r["player_no"] is not None}
    n = 1
    while n in used:
        n += 1
    return n


def _tournament_counts(con, tournament_id: int) -> dict[str, int]:
    c = db.one(con, "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
    n = int(c["c"] or 0) if c else 0
    return {"participants": n, "tables": n // 4, "rest": n % 4}


def _search_addresses(con, qtxt: str, limit: int = 60):
    if not qtxt:
        return []
    like = f"%{qtxt.strip()}%"
    return db.q(
        con,
        """
        SELECT *
        FROM addresses
        WHERE
          nachname LIKE ? OR vorname LIKE ? OR wohnort LIKE ? OR ort LIKE ?
          OR plz LIKE ? OR email LIKE ? OR telefon LIKE ?
          OR strasse LIKE ? OR hausnummer LIKE ?
        ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE, id DESC
        LIMIT ?
        """,
        (like, like, like, like, like, like, like, like, like, int(limit)),
    )


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
@bp.get("/tournaments")
def tournaments_list():
    with db.connect() as con:
        rows = db.q(
            con,
            """
            SELECT t.*,
              (SELECT COUNT(*) FROM tournament_participants tp WHERE tp.tournament_id=t.id) AS participant_count
            FROM tournaments t
            ORDER BY t.event_date DESC, t.start_time DESC, t.id DESC
            """,
        )
    return render_template("tournaments.html", tournaments=rows, now=_now_local_iso())


@bp.get("/tournaments/<int:tournament_id>/participants")
def tournament_participants(tournament_id: int):
    qtxt = (request.args.get("q") or "").strip()

    with db.connect() as con:
        t = db.one(con, "SELECT * FROM tournaments WHERE id=?", (tournament_id,))
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)

        hits = _search_addresses(con, qtxt)
        already = db.q(con, "SELECT address_id FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
        already_ids = {int(r["address_id"]) for r in already if r["address_id"] is not None}
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
    )


# -----------------------------------------------------------------------------
# Teilnehmer übernehmen (aus Adressbuch)
# -----------------------------------------------------------------------------
@bp.post("/tournaments/<int:tournament_id>/participants/add/<int:address_id>")
def tournament_participant_add(tournament_id: int, address_id: int):
    q = (request.args.get("q") or request.form.get("q") or "").strip()

    with db.connect() as con:
        t = db.one(con, "SELECT id FROM tournaments WHERE id=?", (tournament_id,))
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

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
              (tournament_id, player_no, address_id, display_name)
            VALUES (?,?,?,?)
            """,
            (tournament_id, pno, address_id, _display_name(a)),
        )
        con.commit()

    flash(f"Teilnehmer übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id, q=q))


# -----------------------------------------------------------------------------
# 🆕 Quick-Add: neue Adresse + sofortiger Turnierteilnehmer
# -----------------------------------------------------------------------------
@bp.post("/tournaments/<int:tournament_id>/participants/quickadd")
def tournament_participant_quickadd(tournament_id: int):
    f = request.form

    nachname = (f.get("nachname") or "").strip()
    vorname = (f.get("vorname") or "").strip()
    wohnort = (f.get("wohnort") or "").strip()

    if not nachname or not vorname or not wohnort:
        flash("Pflichtfelder fehlen (Nachname, Vorname, Wohnort).", "error")
        return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id))

    plz = (f.get("plz") or "").strip() or None
    ort = (f.get("ort") or "").strip() or None
    strasse = (f.get("strasse") or "").strip() or None
    hausnummer = (f.get("hausnummer") or "").strip() or None
    telefon = (f.get("telefon") or "").strip() or None
    email = (f.get("email") or "").strip() or None
    notizen = (f.get("notizen") or "").strip() or None

    with db.connect() as con:
        t = db.one(con, "SELECT id FROM tournaments WHERE id=?", (tournament_id,))
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        # Wohnort-Lookup upsert (nur wenn plz/ort sinnvoll vorhanden)
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
                strasse,
                hausnummer,
                telefon,
                email,
                "aktiv",
                notizen,
            ),
        )
        address_id = int(cur.lastrowid)

        # Falls (theoretisch) schon Teilnehmer: abbrechen
        dup = db.one(
            con,
            "SELECT 1 FROM tournament_participants WHERE tournament_id=? AND address_id=?",
            (tournament_id, address_id),
        )
        if dup:
            con.rollback()
            flash("Diese Person ist bereits als Teilnehmer erfasst.", "error")
            return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id))

        # Teilnehmer anlegen
        pno = _next_free_player_no(con, tournament_id)

        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        disp = _display_name(a) if a else f"{nachname}, {vorname} · {wohnort}"

        con.execute(
            """
            INSERT INTO tournament_participants
              (tournament_id, player_no, address_id, display_name)
            VALUES (?,?,?,?)
            """,
            (tournament_id, pno, address_id, disp),
        )

        con.commit()

    flash(f"Teilnehmer neu angelegt und übernommen (Nr {pno}).", "ok")
    return redirect(url_for("tournaments.tournament_participants", tournament_id=tournament_id))