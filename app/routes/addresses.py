# app/routes/addresses.py
from __future__ import annotations

from typing import Any
from datetime import datetime

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from .. import db
from ..services import addressbook_io

bp = Blueprint("addresses", __name__)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _to_int_none(v: Any) -> int | None:
    try:
        s = str(v).strip()
        if s == "":
            return None
        return int(s)
    except Exception:
        return None


def _is_used_in_any_tournament(con, address_id: int) -> bool:
    r = db.one(con, "SELECT 1 FROM tournament_participants WHERE address_id=? LIMIT 1", (address_id,))
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


def _csv_text_response(filename: str, text: str) -> Response:
    bom = "\ufeff"  # UTF-8 BOM (Excel)
    data = (bom + text).encode("utf-8")
    resp = Response(data, mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _parse_years(s: Any) -> list[int]:
    """
    Erwartet z.B. "2018,2019,2024" (kommasepariert).
    Gibt sortierte, eindeutige Jahreszahlen zurück.
    """
    if s is None:
        return []
    raw = str(s).strip()
    if not raw:
        return []
    years: set[int] = set()
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if not p.isdigit():
            continue
        y = int(p)
        if 1900 <= y <= 3000:
            years.add(y)
    return sorted(years)


def _bucket_participation(n: int) -> str:
    if n <= 0:
        return "0"
    if n <= 2:
        return "1–2"
    if n <= 5:
        return "3–5"
    if n <= 10:
        return "6–10"
    return ">10"


def _bucket_recency(last_year: int | None, now_year: int) -> str:
    if not last_year:
        return "nie"
    d = now_year - int(last_year)
    if d <= 0:
        return "dieses Jahr"
    if d == 1:
        return "letztes Jahr"
    if d <= 3:
        return "vor 2–3 Jahren"
    if d <= 5:
        return "vor 4–5 Jahren"
    return "vor >5 Jahren"


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
# NEU: Statistik fürs Adressbuch
# -----------------------------------------------------------------------------
@bp.get("/addresses/stats")
def addresses_stats():
    with db.connect() as con:
        default_ab_id = _default_ab_id(con)

        has_invite = _has_column(con, "addresses", "invite")
        has_pc = _has_column(con, "addresses", "participation_count")
        has_last = _has_column(con, "addresses", "last_tournament_at")
        has_years = _has_column(con, "addresses", "tournament_years")

        total = db.one(con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=?", (default_ab_id,))
        active = db.one(
            con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status='aktiv'", (default_ab_id,)
        )
        inactive = db.one(
            con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status!='aktiv'", (default_ab_id,)
        )

        total_i = int((total["c"] or 0) if total else 0)
        active_i = int((active["c"] or 0) if active else 0)
        inactive_i = int((inactive["c"] or 0) if inactive else 0)

        invite_yes = invite_no = None
        if has_invite:
            r1 = db.one(
                con,
                "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND invite=1",
                (default_ab_id,),
            )
            r0 = db.one(
                con,
                "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND (invite=0 OR invite IS NULL)",
                (default_ab_id,),
            )
            invite_yes = int((r1["c"] or 0) if r1 else 0)
            invite_no = int((r0["c"] or 0) if r0 else 0)

        cols = ["status"]
        if has_pc:
            cols.append("participation_count")
        if has_last:
            cols.append("last_tournament_at")
        if has_years:
            cols.append("tournament_years")

        rows = con.execute(
            f"SELECT {', '.join(cols)} FROM addresses WHERE addressbook_id=?",
            (default_ab_id,),
        ).fetchall()

        now_year = datetime.now().year

        part_buckets: dict[str, int] = {"0": 0, "1–2": 0, "3–5": 0, "6–10": 0, ">10": 0}
        recency_buckets: dict[str, int] = {
            "nie": 0,
            "dieses Jahr": 0,
            "letztes Jahr": 0,
            "vor 2–3 Jahren": 0,
            "vor 4–5 Jahren": 0,
            "vor >5 Jahren": 0,
        }
        year_counts: dict[int, int] = {}

        for r in rows:
            pc = 0
            if has_pc:
                try:
                    pc = int(r["participation_count"] or 0)
                except Exception:
                    pc = 0

            years_list: list[int] = []
            if has_years:
                years_list = _parse_years(r["tournament_years"])
                if not has_pc:
                    pc = len(years_list)

                for y in years_list:
                    year_counts[y] = year_counts.get(y, 0) + 1

            last_year: int | None = None
            if has_last:
                try:
                    v = r["last_tournament_at"]
                    if v is not None and str(v).strip() != "":
                        last_year = int(str(v).strip())
                except Exception:
                    last_year = None
            if last_year is None and years_list:
                last_year = max(years_list)

            part_buckets[_bucket_participation(pc)] = part_buckets.get(_bucket_participation(pc), 0) + 1
            recency_buckets[_bucket_recency(last_year, now_year)] = recency_buckets.get(
                _bucket_recency(last_year, now_year), 0
            ) + 1

        years_sorted = sorted(year_counts.items(), key=lambda t: t[0])

    return render_template(
        "addresses_stats.html",
        total=total_i,
        active=active_i,
        inactive=inactive_i,
        has_invite=has_invite,
        invite_yes=invite_yes,
        invite_no=invite_no,
        has_participation=has_pc or has_years,
        part_buckets=part_buckets,
        recency_buckets=recency_buckets,
        years_sorted=years_sorted,
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
        text, filename = addressbook_io.export_addresses_csv(con=con, addressbook_id=default_ab_id)
    return _csv_text_response(filename, text)


@bp.get("/addresses/import")
def addresses_import():
    return render_template("address_import.html")


@bp.post("/addresses/import")
def addresses_import_post():
    """
    Importiert CSV (Semikolon, UTF-8) und ersetzt das Standard-Adressbuch
    als HARD-REPLACE (nur erlaubt, wenn KEINE Turnierdaten existieren).
    """
    file = request.files.get("file")
    if not file:
        flash("Bitte eine CSV-Datei auswählen.", "error")
        return redirect(url_for("addresses.addresses_import"))

    raw = file.read()
    try:
        text = raw.decode("utf-8-sig")  # UTF-8 mit/ohne BOM
    except Exception:
        flash("CSV konnte nicht als UTF-8 gelesen werden.", "error")
        return redirect(url_for("addresses.addresses_import"))

    with db.connect() as con:
        try:
            new_ab_id, inserted, skipped = addressbook_io.import_addresses_replace_default_from_csv_text(
                con=con,
                csv_text=text,
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("addresses.addresses_import"))

        con.commit()

    flash(
        f"Import abgeschlossen: {inserted} Adressen importiert, {skipped} Zeilen übersprungen. "
        f"(Adressbuch #{new_ab_id} ist jetzt Standard.)",
        "ok",
    )
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
        # Turnier-/Einladungsfelder
        "invite": 1,
        "participation_count": 0,
        "last_tournament_at": "",
        "tournament_years": "",
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

    invite_val = 1 if (f.get("invite") == "1") else 0
    participation_count = _to_int(f.get("participation_count"), 0)
    last_tournament_at = (f.get("last_tournament_at") or "").strip() or None
    tournament_years = (f.get("tournament_years") or "").strip() or None

    with db.connect() as con:
        default_ab_id = _default_ab_id(con)

        has_invite = _has_column(con, "addresses", "invite")
        has_pc = _has_column(con, "addresses", "participation_count")
        has_last = _has_column(con, "addresses", "last_tournament_at")
        has_years = _has_column(con, "addresses", "tournament_years")

        cols = [
            "addressbook_id", "nachname", "vorname", "wohnort",
            "plz", "ort", "strasse", "hausnummer",
        ]
        vals: list[Any] = [
            default_ab_id, nachname, vorname, wohnort,
            plz, ort, strasse, hausnummer,
        ]

        if has_invite:
            cols.append("invite")
            vals.append(invite_val)

        cols.extend(["email", "telefon", "status", "notizen"])
        vals.extend([email, telefon, status, notizen])

        if has_pc:
            cols.append("participation_count")
            vals.append(participation_count)

        if has_last:
            cols.append("last_tournament_at")
            vals.append(last_tournament_at)

        if has_years:
            cols.append("tournament_years")
            vals.append(tournament_years)

        cols.extend(["created_at", "updated_at"])
        placeholders = ", ".join(["?"] * (len(cols) - 2)) + ", datetime('now'), datetime('now')"
        sql = f"""
            INSERT INTO addresses({', '.join(cols)})
            VALUES ({placeholders})
        """

        con.execute(sql, tuple(vals))

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

    invite_val = 1 if (f.get("invite") == "1") else 0
    participation_count = _to_int(f.get("participation_count"), 0)
    last_tournament_at = (f.get("last_tournament_at") or "").strip() or None
    tournament_years = (f.get("tournament_years") or "").strip() or None

    with db.connect() as con:
        a = db.one(con, "SELECT id FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(nxt or url_for("addresses.addresses_list"))

        has_invite = _has_column(con, "addresses", "invite")
        has_pc = _has_column(con, "addresses", "participation_count")
        has_last = _has_column(con, "addresses", "last_tournament_at")
        has_years = _has_column(con, "addresses", "tournament_years")

        sets = [
            "nachname=?",
            "vorname=?",
            "wohnort=?",
            "plz=?",
            "ort=?",
            "strasse=?",
            "hausnummer=?",
            "telefon=?",
            "email=?",
            "status=?",
            "notizen=?",
        ]
        params: list[Any] = [
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
        ]

        if has_invite:
            sets.append("invite=?")
            params.append(invite_val)

        if has_pc:
            sets.append("participation_count=?")
            params.append(participation_count)

        if has_last:
            sets.append("last_tournament_at=?")
            params.append(last_tournament_at)

        if has_years:
            sets.append("tournament_years=?")
            params.append(tournament_years)

        sets.append("updated_at=datetime('now')")

        sql = f"""
            UPDATE addresses
            SET {', '.join(sets)}
            WHERE id=?
        """
        params.append(address_id)

        con.execute(sql, tuple(params))

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