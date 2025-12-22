# app/routes/addresses.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db

bp = Blueprint("addresses", __name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _s(v: Any) -> str:
    return (v or "").strip()


def _display_name(r: Any) -> str:
    base = f"{r['nachname']}, {r['vorname']}"
    w = (r["wohnort"] or "").strip()
    return f"{base} · {w}" if w else base


def _is_used_in_any_tournament(con, address_id: int) -> bool:
    r = db.one(
        con,
        "SELECT 1 FROM tournament_participants WHERE address_id=? LIMIT 1",
        (address_id,),
    )
    return bool(r)


def _upsert_wohnort(con, wohnort: str, plz: str | None, ort: str | None) -> None:
    w = (wohnort or "").strip()
    p = (plz or "").strip() if plz is not None else ""
    o = (ort or "").strip() if ort is not None else ""
    if not w or not p or not o:
        return

    con.execute(
        """
        INSERT INTO wohnorte(wohnort, plz, ort)
        VALUES (?,?,?)
        ON CONFLICT(wohnort) DO UPDATE SET
            plz=excluded.plz,
            ort=excluded.ort
        """,
        (w, p, o),
    )


# -----------------------------------------------------------------------------
# Search DTO
# -----------------------------------------------------------------------------
@dataclass
class SearchHit:
    id: int
    nachname: str
    vorname: str
    wohnort: str
    plz: str
    ort: str
    strasse: str
    hausnummer: str
    telefon: str
    email: str
    status: str
    display: str


def _search(con, qtxt: str, limit: int = 80) -> list[SearchHit]:
    qtxt = (qtxt or "").strip()
    if not qtxt:
        return []

    like = f"%{qtxt}%"
    rows = db.q(
        con,
        """
        SELECT *
        FROM addresses
        WHERE
          nachname LIKE ? OR vorname LIKE ? OR wohnort LIKE ? OR ort LIKE ? OR
          plz LIKE ? OR email LIKE ? OR telefon LIKE ? OR
          strasse LIKE ? OR hausnummer LIKE ?
        ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE, id DESC
        LIMIT ?
        """,
        (like, like, like, like, like, like, like, like, like, int(limit)),
    )

    out: list[SearchHit] = []
    for r in rows:
        out.append(
            SearchHit(
                id=int(r["id"]),
                nachname=r["nachname"],
                vorname=r["vorname"],
                wohnort=r["wohnort"],
                plz=(r["plz"] or ""),
                ort=(r["ort"] or ""),
                strasse=(r["strasse"] or ""),
                hausnummer=(r["hausnummer"] or ""),
                telefon=(r["telefon"] or ""),
                email=(r["email"] or ""),
                status=(r["status"] or "aktiv"),
                display=_display_name(r),
            )
        )
    return out


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
@bp.get("/addresses")
def addresses_list():
    qtxt = (request.args.get("q") or "").strip()

    with db.connect() as con:
        dab = db.one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
        default_ab_id = int(dab["id"]) if dab else 1

        hits = _search(con, qtxt, limit=120) if qtxt else []

        # zuletzt bearbeitete oben, wenn keine Suche
        latest = []
        if not qtxt:
            latest = db.q(
                con,
                """
                SELECT *
                FROM addresses
                WHERE addressbook_id=?
                ORDER BY updated_at DESC, id DESC
                LIMIT 80
                """,
                (default_ab_id,),
            )

    return render_template(
        "addresses.html",
        q=qtxt,
        hits=hits,
        latest=latest,
    )


@bp.get("/addresses/new")
def address_new():
    defaults = {
        "nachname": "",
        "vorname": "",
        "wohnort": "",
        "plz": "",
        "ort": "",
        "strasse": "",
        "hausnummer": "",
        "telefon": "",
        "email": "",
        "status": "aktiv",
        "notizen": "",
    }
    return render_template("address_form.html", a=defaults, mode="new", used=False)


@bp.post("/addresses/new")
def address_create():
    f = request.form
    nachname = _s(f.get("nachname"))
    vorname = _s(f.get("vorname"))
    wohnort = _s(f.get("wohnort"))
    if not nachname or not vorname or not wohnort:
        flash("Pflichtfelder fehlen: Nachname, Vorname, Wohnort.", "error")
        return redirect(url_for("addresses.address_new"))

    plz = _s(f.get("plz")) or None
    ort = _s(f.get("ort")) or None

    with db.connect() as con:
        dab = db.one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
        default_ab_id = int(dab["id"]) if dab else 1

        con.execute(
            """
            INSERT INTO addresses(
              addressbook_id,
              nachname, vorname,
              wohnort, plz, ort,
              strasse, hausnummer,
              email, telefon,
              status, notizen,
              created_at, updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (
                default_ab_id,
                nachname,
                vorname,
                wohnort,
                plz,
                ort,
                _s(f.get("strasse")) or None,
                _s(f.get("hausnummer")) or None,
                _s(f.get("email")) or None,
                _s(f.get("telefon")) or None,
                _s(f.get("status")) or "aktiv",
                _s(f.get("notizen")) or None,
            ),
        )

        _upsert_wohnort(con, wohnort, plz, ort)

        con.commit()
        new_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    flash("Adresse angelegt.", "ok")
    return redirect(url_for("addresses.address_edit", address_id=new_id))


@bp.get("/addresses/<int:address_id>/edit")
def address_edit(address_id: int):
    with db.connect() as con:
        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(url_for("addresses.addresses_list"))

        used = _is_used_in_any_tournament(con, address_id)

    return render_template("address_form.html", a=a, mode="edit", used=used)


@bp.post("/addresses/<int:address_id>/edit")
def address_update(address_id: int):
    f = request.form
    nachname = _s(f.get("nachname"))
    vorname = _s(f.get("vorname"))
    wohnort = _s(f.get("wohnort"))
    if not nachname or not vorname or not wohnort:
        flash("Pflichtfelder fehlen: Nachname, Vorname, Wohnort.", "error")
        return redirect(url_for("addresses.address_edit", address_id=address_id))

    plz = _s(f.get("plz")) or None
    ort = _s(f.get("ort")) or None

    with db.connect() as con:
        a0 = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if not a0:
            flash("Adresse nicht gefunden.", "error")
            return redirect(url_for("addresses.addresses_list"))

        con.execute(
            """
            UPDATE addresses
            SET
              nachname=?,
              vorname=?,
              wohnort=?,
              plz=?,
              ort=?,
              strasse=?,
              hausnummer=?,
              telefon=?,
              email=?,
              status=?,
              notizen=?,
              updated_at=datetime('now')
            WHERE id=?
            """,
            (
                nachname,
                vorname,
                wohnort,
                plz,
                ort,
                _s(f.get("strasse")) or None,
                _s(f.get("hausnummer")) or None,
                _s(f.get("telefon")) or None,
                _s(f.get("email")) or None,
                _s(f.get("status")) or "aktiv",
                _s(f.get("notizen")) or None,
                address_id,
            ),
        )

        # Wohnort-Lookup pflegen
        _upsert_wohnort(con, wohnort, plz, ort)

        # Wichtig: Anzeige im Turnier aktualisieren (einseitig: Adresse -> Turnier)
        a1 = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if a1:
            con.execute(
                """
                UPDATE tournament_participants
                SET display_name=?, updated_at=datetime('now')
                WHERE address_id=?
                """,
                (_display_name(a1), address_id),
            )

        con.commit()

    flash("Adresse gespeichert (und Turnieranzeige aktualisiert).", "ok")
    return redirect(url_for("addresses.address_edit", address_id=address_id))


@bp.post("/addresses/<int:address_id>/delete")
def address_delete(address_id: int):
    """
    Löschen ist grundsätzlich nur erlaubt, wenn Adresse noch nie in einem Turnier war.
    Sonst: Status verwenden.
    """
    with db.connect() as con:
        a = db.one(con, "SELECT id FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(url_for("addresses.addresses_list"))

        if _is_used_in_any_tournament(con, address_id):
            flash("Löschen nicht erlaubt: Adresse war bereits Teilnehmer in einem Turnier. Bitte Status setzen.", "error")
            return redirect(url_for("addresses.address_edit", address_id=address_id))

        con.execute("DELETE FROM addresses WHERE id=?", (address_id,))
        con.commit()

    flash("Adresse gelöscht.", "ok")
    return redirect(url_for("addresses.addresses_list"))