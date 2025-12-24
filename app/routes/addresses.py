# app/routes/addresses.py
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

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


def _is_used_in_any_tournament(con, address_id: int) -> bool:
    r = db.one(con, "SELECT 1 FROM tournament_participants WHERE address_id=? LIMIT 1", (address_id,))
    return bool(r)


def _has_any_tournament_refs(con) -> bool:
    """
    Import-Replace ist nur sicher, wenn keine Turnier-Referenzen existieren.
    Sonst würden FK-Restriktionen und/oder Historienbrüche auftreten.
    """
    r = db.one(con, "SELECT 1 FROM tournament_participants LIMIT 1")
    return bool(r)


def _default_ab_id(con) -> int:
    dab = db.one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
    return int(dab["id"]) if dab else 1


def _build_next_url(qtxt: str, show_inactive: bool) -> str:
    return url_for(
        "addresses.addresses_list",
        q=qtxt or None,
        show_inactive="1" if show_inactive else "0",
    )


def _has_column(con, table: str, column: str) -> bool:
    """
    Lokale Schema-Prüfung (ohne Abhängigkeit von db._has_column, die evtl. nicht exportiert ist).
    """
    try:
        rows = con.execute(f"PRAGMA table_info({table});").fetchall()
        return any((r["name"] == column) for r in rows)
    except Exception:
        return False


def _upsert_wohnort(con, wohnort: str, plz: str | None, ort: str | None) -> None:
    """
    Pflegt Wohnort/PLZ/Ort in die Lookup-Tabelle 'wohnorte'.
    Upsert über UNIQUE(wohnort). Nur wenn alle drei Werte vorhanden sind.
    """
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


def _addresses_columns(con) -> list[str]:
    """
    Liefert alle Spaltennamen der Tabelle 'addresses' in DB-Reihenfolge.
    """
    rows = con.execute("PRAGMA table_info(addresses);").fetchall()
    return [str(r["name"]) for r in rows]


def _norm_none(v: Any) -> Any:
    """
    CSV liefert Strings. Wir wandeln leere Strings in None um (für nullable Felder).
    """
    if v is None:
        return None
    s = str(v)
    if s.strip() == "":
        return None
    return s


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return int(s)
    except Exception:
        return None


def _csv_text_response(filename: str, text: str) -> Response:
    """
    Liefert CSV als Download. Optional BOM für Excel-Freundlichkeit.
    """
    bom = "\ufeff"  # UTF-8 BOM (Excel)
    data = (bom + text).encode("utf-8")
    resp = Response(data, mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# -----------------------------------------------------------------------------
# Pages: Liste / Suche
# -----------------------------------------------------------------------------
@bp.get("/addresses")
def addresses_list():
    qtxt = (request.args.get("q") or "").strip()
    show_inactive = (request.args.get("show_inactive") or "0") == "1"
    like = f"%{qtxt}%"

    with db.connect() as con:
        default_ab_id = _default_ab_id(con)

        # Zähler für UI (immer über gesamtes Adressbuch)
        cnt_all = db.one(con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=?", (default_ab_id,))
        cnt_inactive = db.one(
            con,
            "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status!='aktiv'",
            (default_ab_id,),
        )
        cnt_all_i = int((cnt_all["c"] or 0) if cnt_all else 0)
        cnt_inactive_i = int((cnt_inactive["c"] or 0) if cnt_inactive else 0)

        hits = []
        latest = []

        if qtxt:
            where = ["addressbook_id=?"]
            params: list[Any] = [default_ab_id]

            where.append(
                "("
                "nachname LIKE ? OR vorname LIKE ? OR wohnort LIKE ? OR ort LIKE ? OR "
                "plz LIKE ? OR email LIKE ? OR telefon LIKE ? OR "
                "strasse LIKE ? OR hausnummer LIKE ?"
                ")"
            )
            params.extend([like, like, like, like, like, like, like, like, like])

            if not show_inactive:
                where.append("status='aktiv'")

            sql = f"""
                SELECT *
                FROM addresses
                WHERE {' AND '.join(where)}
                ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE, id DESC
                LIMIT 500
            """
            hits = db.q(con, sql, tuple(params))

        else:
            where = ["addressbook_id=?"]
            params2: list[Any] = [default_ab_id]
            if not show_inactive:
                where.append("status='aktiv'")

            sql2 = f"""
                SELECT *
                FROM addresses
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, id DESC
                LIMIT 80
            """
            latest = db.q(con, sql2, tuple(params2))

    return render_template(
        "addresses.html",
        q=qtxt,
        show_inactive=show_inactive,
        cnt_all=cnt_all_i,
        cnt_inactive=cnt_inactive_i,
        hits=hits,
        latest=latest,
    )


# -----------------------------------------------------------------------------
# Import/Export (CSV)
# -----------------------------------------------------------------------------
@bp.get("/addresses/export")
def addresses_export():
    """
    Exportiert alle Adressen des Default-Adressbuchs als CSV.
    Trennzeichen ';', Header = DB-Spaltennamen, UTF-8 (+BOM).
    """
    with db.connect() as con:
        default_ab_id = _default_ab_id(con)
        cols = _addresses_columns(con)

        rows = db.q(
            con,
            f"SELECT {', '.join(cols)} FROM addresses WHERE addressbook_id=? ORDER BY nachname, vorname, id",
            (default_ab_id,),
        )

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";", lineterminator="\n")
    w.writeheader()
    for r in rows:
        d = {c: (r[c] if c in r.keys() else None) for c in cols}
        w.writerow(d)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"addresses-export-{ts}.csv"
    return _csv_text_response(filename, buf.getvalue())


@bp.get("/addresses/import")
def addresses_import():
    """
    Seite für CSV-Import (Replace All).
    """
    return render_template("address_import.html")


@bp.post("/addresses/import")
def addresses_import_post():
    """
    Importiert CSV (Semikolon, UTF-8) und ersetzt *komplett* alle Adressen
    im Default-Adressbuch. Niemals Merge/Overwrite.
    """
    file = request.files.get("file")
    if not file:
        flash("Bitte eine CSV-Datei auswählen.", "error")
        return redirect(url_for("addresses.addresses_import"))

    # CSV einlesen (UTF-8; BOM optional)
    raw = file.read()
    try:
        text = raw.decode("utf-8-sig")  # akzeptiert UTF-8 mit/ohne BOM
    except Exception:
        flash("CSV konnte nicht als UTF-8 gelesen werden.", "error")
        return redirect(url_for("addresses.addresses_import"))

    buf = io.StringIO(text)
    reader = csv.DictReader(buf, delimiter=";")

    if not reader.fieldnames:
        flash("CSV hat keinen Header (Spaltennamen fehlen).", "error")
        return redirect(url_for("addresses.addresses_import"))

    with db.connect() as con:
        # Sicherheitsbremse: keine Import-Ersetzung, wenn Turnier-Referenzen existieren
        if _has_any_tournament_refs(con):
            flash(
                "Import ist gesperrt: Es existieren bereits Turnier-Teilnehmerdaten. "
                "Ein 'Replace All' würde FK-Restriktionen verletzen bzw. Historie zerstören.",
                "error",
            )
            return redirect(url_for("addresses.addresses_import"))

        default_ab_id = _default_ab_id(con)
        db_cols = _addresses_columns(con)

        csv_cols = [c.strip() for c in reader.fieldnames if c and str(c).strip() != ""]

        # Muss mindestens die Pflichtfelder liefern
        required = {"nachname", "vorname", "wohnort"}
        missing_req = [x for x in sorted(required) if x not in set(csv_cols)]
        if missing_req:
            flash(f"CSV fehlt Pflichtspalten: {', '.join(missing_req)}.", "error")
            return redirect(url_for("addresses.addresses_import"))

        # Wir akzeptieren nur DB-Spalten; unbekannte Spalten werden abgewiesen (Planungssicherheit)
        unknown = [c for c in csv_cols if c not in set(db_cols)]
        if unknown:
            flash(f"CSV enthält unbekannte Spalten: {', '.join(unknown)}.", "error")
            return redirect(url_for("addresses.addresses_import"))

        # Replace All: alle Adressen des Default-Adressbuchs löschen
        con.execute("DELETE FROM addresses WHERE addressbook_id=?", (default_ab_id,))
        # wohnorte neu aufbauen (sauber)
        con.execute("DELETE FROM wohnorte")

        inserted = 0
        skipped = 0

        # Insert vorbereiten:
        # - wir setzen addressbook_id IMMER auf default_ab_id
        # - id wird NICHT importiert (damit keine Kollisionen/Autoincrement-Probleme)
        insert_cols = [c for c in db_cols if c not in ("id",)]
        placeholders = ",".join(["?"] * len(insert_cols))
        sql_ins = f"INSERT INTO addresses({', '.join(insert_cols)}) VALUES ({placeholders})"

        for row in reader:
            # Pflichtfelder
            nachname = (row.get("nachname") or "").strip()
            vorname = (row.get("vorname") or "").strip()
            wohnort = (row.get("wohnort") or "").strip()
            if not nachname or not vorname or not wohnort:
                skipped += 1
                continue

            # Zeile -> Insert-Werte
            values: list[Any] = []
            for c in insert_cols:
                if c == "addressbook_id":
                    values.append(default_ab_id)
                    continue

                v = row.get(c)

                # Integer-Felder (stabil & explizit)
                if c in ("invite", "participation_count"):
                    values.append(_int_or_none(v) if _int_or_none(v) is not None else 0 if c == "participation_count" else 1)
                    continue
                if c in ("created_at", "updated_at"):
                    # wenn leer → now
                    vv = _norm_none(v)
                    values.append(vv if vv is not None else datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    continue

                # Alles andere als Text/Nullable
                values.append(_norm_none(v))

            con.execute(sql_ins, tuple(values))
            inserted += 1

            # wohnorte pflegen (nur wenn wohnort+plz+ort vollständig)
            _upsert_wohnort(con, wohnort, _norm_none(row.get("plz")), _norm_none(row.get("ort")))

        con.commit()

    flash(f"Import abgeschlossen: {inserted} Adressen importiert, {skipped} Zeilen übersprungen.", "ok")
    return redirect(url_for("addresses.addresses_list"))


# -----------------------------------------------------------------------------
# Neu anlegen
# -----------------------------------------------------------------------------
@bp.get("/addresses/new")
def address_new():
    qtxt = (request.args.get("q") or "").strip()
    show_inactive = (request.args.get("show_inactive") or "0") == "1"
    next_url = (request.args.get("next") or "").strip() or _build_next_url(qtxt, show_inactive)

    defaults = {
        "id": 0,
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
    return render_template("address_form.html", a=defaults, mode="new", used=False, next=next_url)


@bp.post("/addresses/new")
def address_create():
    f = request.form
    nxt = (f.get("next") or "").strip()

    nachname = (f.get("nachname") or "").strip()
    vorname = (f.get("vorname") or "").strip()
    wohnort = (f.get("wohnort") or "").strip()
    if not nachname or not vorname or not wohnort:
        flash("Pflichtfelder fehlen: Nachname, Vorname, Wohnort.", "error")
        return redirect(url_for("addresses.address_new", next=nxt))

    plz = (f.get("plz") or "").strip() or None
    ort = (f.get("ort") or "").strip() or None

    strasse = (f.get("strasse") or "").strip() or None
    hausnummer = (f.get("hausnummer") or "").strip() or None
    email = (f.get("email") or "").strip() or None
    telefon = (f.get("telefon") or "").strip() or None
    status = (f.get("status") or "aktiv").strip() or "aktiv"
    notizen = (f.get("notizen") or "").strip() or None

    with db.connect() as con:
        default_ab_id = _default_ab_id(con)
        has_invite = _has_column(con, "addresses", "invite")

        if has_invite:
            con.execute(
                """
                INSERT INTO addresses(
                  addressbook_id, nachname, vorname, wohnort,
                  plz, ort, strasse, hausnummer,
                  invite,
                  email, telefon, status, notizen,
                  created_at, updated_at
                )
                VALUES (?,?,?,?, ?,?,?,?, ?, ?,?,?,?, datetime('now'), datetime('now'))
                """,
                (
                    default_ab_id,
                    nachname,
                    vorname,
                    wohnort,
                    plz,
                    ort,
                    strasse,
                    hausnummer,
                    1,
                    email,
                    telefon,
                    status,
                    notizen,
                ),
            )
        else:
            # Fallback für DBs ohne invite-Spalte
            con.execute(
                """
                INSERT INTO addresses(
                  addressbook_id, nachname, vorname, wohnort,
                  plz, ort, strasse, hausnummer,
                  email, telefon, status, notizen,
                  created_at, updated_at
                )
                VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, datetime('now'), datetime('now'))
                """,
                (
                    default_ab_id,
                    nachname,
                    vorname,
                    wohnort,
                    plz,
                    ort,
                    strasse,
                    hausnummer,
                    email,
                    telefon,
                    status,
                    notizen,
                ),
            )

        # ✅ Wohnort-Lookup pflegen (nur wenn wohnort+plz+ort vollständig)
        _upsert_wohnort(con, wohnort, plz, ort)

        con.commit()

    flash("Adresse angelegt.", "ok")
    return redirect(nxt or url_for("addresses.addresses_list"))


# -----------------------------------------------------------------------------
# Bearbeiten
# -----------------------------------------------------------------------------
@bp.get("/addresses/<int:address_id>/edit")
def address_edit(address_id: int):
    qtxt = (request.args.get("q") or "").strip()
    show_inactive = (request.args.get("show_inactive") or "0") == "1"
    next_url = (request.args.get("next") or "").strip() or _build_next_url(qtxt, show_inactive)

    with db.connect() as con:
        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(next_url)

        used = _is_used_in_any_tournament(con, address_id)

    return render_template("address_form.html", a=a, mode="edit", used=used, next=next_url)


@bp.post("/addresses/<int:address_id>/edit")
def address_update(address_id: int):
    f = request.form
    nxt = (f.get("next") or "").strip()

    nachname = (f.get("nachname") or "").strip()
    vorname = (f.get("vorname") or "").strip()
    wohnort = (f.get("wohnort") or "").strip()
    if not nachname or not vorname or not wohnort:
        flash("Pflichtfelder fehlen: Nachname, Vorname, Wohnort.", "error")
        return redirect(url_for("addresses.address_edit", address_id=address_id, next=nxt))

    plz = (f.get("plz") or "").strip() or None
    ort = (f.get("ort") or "").strip() or None

    with db.connect() as con:
        a = db.one(con, "SELECT id FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(nxt or url_for("addresses.addresses_list"))

        con.execute(
            """
            UPDATE addresses
            SET nachname=?,
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
                (f.get("strasse") or "").strip() or None,
                (f.get("hausnummer") or "").strip() or None,
                (f.get("telefon") or "").strip() or None,
                (f.get("email") or "").strip() or None,
                (f.get("status") or "aktiv").strip() or "aktiv",
                (f.get("notizen") or "").strip() or None,
                address_id,
            ),
        )

        # ✅ Wohnort-Lookup pflegen (nur wenn wohnort+plz+ort vollständig)
        _upsert_wohnort(con, wohnort, plz, ort)

        con.commit()

    flash("Adresse gespeichert.", "ok")
    return redirect(nxt or url_for("addresses.addresses_list"))


# -----------------------------------------------------------------------------
# Soft-Delete: Deaktivieren / Reaktivieren
# -----------------------------------------------------------------------------
@bp.post("/addresses/<int:address_id>/deactivate")
def address_deactivate(address_id: int):
    qtxt = (request.args.get("q") or request.form.get("q") or "").strip()
    show_inactive = ((request.args.get("show_inactive") or request.form.get("show_inactive") or "0") == "1")
    next_url = (request.form.get("next") or "").strip() or _build_next_url(qtxt, show_inactive)

    with db.connect() as con:
        a = db.one(con, "SELECT id, status FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(next_url)

        if (a["status"] or "aktiv") != "aktiv":
            flash("Adresse ist bereits inaktiv.", "ok")
            return redirect(next_url)

        con.execute(
            "UPDATE addresses SET status='inaktiv', updated_at=datetime('now') WHERE id=?",
            (address_id,),
        )
        con.commit()

    flash("Adresse deaktiviert (wird standardmäßig nicht mehr angezeigt).", "ok")
    return redirect(next_url)


@bp.post("/addresses/<int:address_id>/reactivate")
def address_reactivate(address_id: int):
    qtxt = (request.args.get("q") or request.form.get("q") or "").strip()
    show_inactive = ((request.args.get("show_inactive") or request.form.get("show_inactive") or "0") == "1")
    next_url = (request.form.get("next") or "").strip() or _build_next_url(qtxt, show_inactive)

    with db.connect() as con:
        a = db.one(con, "SELECT id FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(next_url)

        con.execute(
            "UPDATE addresses SET status='aktiv', updated_at=datetime('now') WHERE id=?",
            (address_id,),
        )
        con.commit()

    flash("Adresse reaktiviert.", "ok")
    return redirect(next_url)


# -----------------------------------------------------------------------------
# Optional: Physisches Löschen (stark eingeschränkt)
# -----------------------------------------------------------------------------
@bp.post("/addresses/<int:address_id>/delete")
def address_delete(address_id: int):
    qtxt = (request.args.get("q") or request.form.get("q") or "").strip()
    show_inactive = ((request.args.get("show_inactive") or request.form.get("show_inactive") or "0") == "1")
    next_url = (request.form.get("next") or "").strip() or _build_next_url(qtxt, show_inactive)

    with db.connect() as con:
        a = db.one(con, "SELECT id FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(next_url)

        if _is_used_in_any_tournament(con, address_id):
            flash("Löschen nicht möglich: Adresse war bereits in einem Turnier. Bitte deaktivieren.", "error")
            return redirect(next_url)

        con.execute("DELETE FROM addresses WHERE id=?", (address_id,))
        con.commit()

    flash("Adresse gelöscht (war nie Turnierteilnehmer).", "ok")
    return redirect(next_url)