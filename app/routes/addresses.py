# app/routes/addresses.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for

from .. import db
from ..services import addressbook_io

bp = Blueprint("addresses", __name__)

# -----------------------------------------------------------------------------
# Konfiguration / Konstanten
# -----------------------------------------------------------------------------
ALLOWED_STATUS = {"aktiv", "inaktiv", "verzogen", "verstorben", "gesperrt"}


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


def _norm_status(raw: Any) -> str:
    s = (str(raw or "").strip() or "aktiv").lower()
    return s if s in ALLOWED_STATUS else "aktiv"


def _is_used_in_any_tournament(con, address_id: int) -> bool:
    r = db.one(con, "SELECT 1 FROM tournament_participants WHERE address_id=? LIMIT 1", (address_id,))
    return bool(r)


def _default_ab_id(con) -> int:
    dab = db.one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
    return int(dab["id"]) if dab else 1


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


# -----------------------------------------------------------------------------
# Marker-Parsing (NEU: statt Jahreslisten)
# -----------------------------------------------------------------------------
def _parse_markers(s: Any) -> list[str]:
    """
    Erwartet z.B. "251228ABCD,250101WXYZ" (kommasepariert) ODER Legacy "2019,2024".
    Gibt sortierte, eindeutige Marker zurück (10 Zeichen, A-Z/0-9).
    Legacy-Jahre werden ignoriert (für Stats zählt dann ggf. last_tournament_at/participation_count).
    """
    if s is None:
        return []
    raw = str(s).strip()
    if not raw:
        return []

    markers: set[str] = set()
    for part in raw.split(","):
        p = part.strip().upper()
        if not p:
            continue
        # Marker: exakt 10, alnum
        if len(p) == 10 and p.isalnum():
            markers.add(p)
            continue
        # Legacy (Jahreszahl) -> ignorieren in Marker-Statistik
        # (Stats bleiben trotzdem robust über last_tournament_at)
    return sorted(markers)


def _marker_to_date(m: str) -> datetime | None:
    """
    Marker beginnt mit JJMMTT (6-stellig). Wir mappen JJ -> 2000+JJ (2000..2099).
    Rückgabe datetime oder None.
    """
    if not m:
        return None
    s = str(m).strip().upper()
    if len(s) != 10 or not s.isalnum():
        return None
    pref = s[:6]
    if not pref.isdigit():
        return None
    yy = int(pref[0:2])
    mm = int(pref[2:4])
    dd = int(pref[4:6])
    year = 2000 + yy
    try:
        return datetime(year, mm, dd)
    except Exception:
        return None


def _year_from_marker(m: str) -> int | None:
    dt = _marker_to_date(m)
    return dt.year if dt else None


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
    # Wir bucketen weiterhin nach Jahresdifferenz (wie bisher), nur dass last_year nun aus Marker stammt.
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


def _clamp_per_page(v: Any) -> int:
    n = _to_int(v, 50)
    if n <= 0:
        n = 50
    if n not in (25, 50, 100, 200):
        n = 50
    return n


def _clamp_page(v: Any) -> int:
    n = _to_int(v, 1)
    return n if n >= 1 else 1


def _qs_for_list(*, q: str, status: str, email: str, wohnort: str, invite: str, view: str, per_page: int, page: int) -> dict[str, Any]:
    """Helper: saubere Querystring-Parameter für url_for (None entfernt Flask automatisch)."""
    return {
        "q": q or None,
        "status": status if status else "alle",
        "email": email if email else "alle",
        "wohnort": wohnort or None,
        "invite": invite if invite else "alle",
        "view": view if view else "latest",
        "per_page": per_page,
        "page": page,
    }


# -----------------------------------------------------------------------------
# Pages: Liste / Suche
# -----------------------------------------------------------------------------
@bp.get("/addresses")
def addresses_list():
    qtxt = (request.args.get("q") or "").strip()
    like = f"%{qtxt}%"

    # Ansicht: latest|all
    view = (request.args.get("view") or "latest").strip().lower()
    if view not in ("latest", "all"):
        view = "latest"

    # Filter
    status_filter = (request.args.get("status") or "alle").strip().lower()
    email_filter = (request.args.get("email") or "alle").strip().lower()      # alle|vorhanden|fehlt
    wohnort_filter = (request.args.get("wohnort") or "").strip()
    invite_filter = (request.args.get("invite") or "alle").strip().lower()    # alle|an|aus

    # Pagination
    per_page = _clamp_per_page(request.args.get("per_page"))
    page = _clamp_page(request.args.get("page"))
    offset = (page - 1) * per_page

    # Legacy: show_inactive=0 hieß früher: nur aktiv (wenn status nicht gesetzt)
    if (request.args.get("show_inactive") or "") == "0" and (request.args.get("status") is None):
        status_filter = "aktiv"

    with db.connect() as con:
        default_ab_id = _default_ab_id(con)

        cnt_all = db.one(con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=?", (default_ab_id,))
        cnt_not_active = db.one(
            con,
            "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status!='aktiv'",
            (default_ab_id,),
        )
        cnt_all_i = int((cnt_all["c"] or 0) if cnt_all else 0)
        cnt_not_active_i = int((cnt_not_active["c"] or 0) if cnt_not_active else 0)

        wohnorte_rows = db.q(
            con,
            """
            SELECT DISTINCT wohnort
            FROM addresses
            WHERE addressbook_id=? AND wohnort IS NOT NULL AND TRIM(wohnort)!=''
            ORDER BY wohnort COLLATE NOCASE ASC
            """,
            (default_ab_id,),
        )
        wohnorte = [str(r["wohnort"]) for r in wohnorte_rows]

        hits: list[Any] = []
        latest: list[Any] = []
        total_hits = 0

        def build_where_and_params() -> tuple[list[str], list[Any]]:
            where = ["addressbook_id=?"]
            params: list[Any] = [default_ab_id]

            # Status
            if status_filter and status_filter != "alle":
                if status_filter in ALLOWED_STATUS:
                    where.append("status=?")
                    params.append(status_filter)

            # E-Mail vorhanden/fehlt
            if email_filter == "vorhanden":
                where.append("email IS NOT NULL AND TRIM(email)!=''")
            elif email_filter == "fehlt":
                where.append("(email IS NULL OR TRIM(email)='')")

            # Wohnort
            if wohnort_filter:
                where.append("wohnort=?")
                params.append(wohnort_filter)

            # Einladung an/aus
            if invite_filter == "an":
                where.append("COALESCE(invite,0)=1")
            elif invite_filter == "aus":
                where.append("COALESCE(invite,0)=0")

            return where, params

        any_filter = (
            bool(qtxt)
            or (status_filter != "alle")
            or (email_filter != "alle")
            or bool(wohnort_filter)
            or (invite_filter != "alle")
        )

        # --- LISTENMODUS: (a) Suche/Filter aktiv ODER (b) view=all -> paginierte Liste
        if any_filter or view == "all":
            where, params = build_where_and_params()

            if qtxt:
                where.append(
                    "("
                    "nachname LIKE ? OR vorname LIKE ? OR wohnort LIKE ? OR ort LIKE ? OR "
                    "plz LIKE ? OR email LIKE ? OR telefon LIKE ? OR "
                    "strasse LIKE ? OR hausnummer LIKE ?"
                    ")"
                )
                params.extend([like, like, like, like, like, like, like, like, like])

            # Count
            sql_count = f"SELECT COUNT(*) AS c FROM addresses WHERE {' AND '.join(where)}"
            r = db.one(con, sql_count, tuple(params))
            total_hits = int((r["c"] or 0) if r else 0)

            # Data
            sql_data = f"""
                SELECT *
                FROM addresses
                WHERE {' AND '.join(where)}
                ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE, id DESC
                LIMIT ? OFFSET ?
            """
            hits = db.q(con, sql_data, tuple(params + [per_page, offset]))

            # Wenn page zu groß (z.B. nach Filterwechsel), zurück auf Seite 1
            if total_hits > 0 and offset >= total_hits:
                page = 1
                offset = 0
                hits = db.q(con, sql_data, tuple(params + [per_page, offset]))

        # --- DEFAULT: zuletzt bearbeitet (ohne Pagination)
        else:
            sql_latest = """
                SELECT *
                FROM addresses
                WHERE addressbook_id=?
                ORDER BY updated_at DESC, id DESC
                LIMIT 80
            """
            latest = db.q(con, sql_latest, (default_ab_id,))

    # Pagination Infos fürs Template
    total_pages = (total_hits + per_page - 1) // per_page if total_hits else 0
    show_hits = (any_filter or view == "all")

    return render_template(
        "addresses.html",
        # Daten
        hits=hits,
        latest=latest,
        show_hits=show_hits,
        total_hits=total_hits,
        # Meta
        q=qtxt,
        cnt_all=cnt_all_i,
        cnt_not_active=cnt_not_active_i,
        # Filter/Ansicht
        view=view,
        status_filter=status_filter,
        email_filter=email_filter,
        wohnort_filter=wohnort_filter,
        invite_filter=invite_filter,
        wohnorte=wohnorte,
        allowed_status=sorted(ALLOWED_STATUS),
        # Pagination
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


# -----------------------------------------------------------------------------
# INVITE Toggle (Badge klickbar)
# -----------------------------------------------------------------------------
@bp.post("/addresses/<int:address_id>/invite-toggle")
def address_invite_toggle(address_id: int):
    nxt = (request.form.get("next") or request.args.get("next") or "").strip()
    if not nxt:
        nxt = request.referrer or url_for("addresses.addresses_list")

    wants_json = "application/json" in (request.headers.get("Accept") or "")

    with db.connect() as con:
        if not _has_column(con, "addresses", "invite"):
            msg = "Spalte 'invite' existiert nicht in addresses."
            if wants_json:
                return jsonify({"ok": False, "error": msg}), 400
            flash(msg, "error")
            return redirect(nxt)

        a = db.one(con, "SELECT id, invite FROM addresses WHERE id=?", (address_id,))
        if not a:
            msg = "Adresse nicht gefunden."
            if wants_json:
                return jsonify({"ok": False, "error": msg}), 404
            flash(msg, "error")
            return redirect(nxt)

        cur = 1 if int(a["invite"] or 0) == 1 else 0
        newv = 0 if cur == 1 else 1

        con.execute(
            "UPDATE addresses SET invite=?, updated_at=datetime('now') WHERE id=?",
            (newv, address_id),
        )
        con.commit()

    if wants_json:
        return jsonify({"ok": True, "address_id": address_id, "invite": newv})

    flash("Einladung umgestellt.", "ok")
    return redirect(nxt)


# -----------------------------------------------------------------------------
# NEU: Statistik fürs Adressbuch (UMGESTELLT: Marker statt Jahre)
# -----------------------------------------------------------------------------
@bp.get("/addresses/stats")
def addresses_stats():
    with db.connect() as con:
        default_ab_id = _default_ab_id(con)

        has_invite = _has_column(con, "addresses", "invite")
        has_pc = _has_column(con, "addresses", "participation_count")
        has_last = _has_column(con, "addresses", "last_tournament_at")   # enthält nun idealerweise Marker (10-stellig)
        has_years = _has_column(con, "addresses", "tournament_years")    # enthält nun idealerweise Marker-Liste (CSV)

        total = db.one(con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=?", (default_ab_id,))
        active = db.one(
            con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status='aktiv'", (default_ab_id,)
        )
        not_active = db.one(
            con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status!='aktiv'", (default_ab_id,)
        )

        total_i = int((total["c"] or 0) if total else 0)
        active_i = int((active["c"] or 0) if active else 0)
        not_active_i = int((not_active["c"] or 0) if not_active else 0)

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
            # participation_count: wenn vorhanden, nehmen; sonst aus Marker-Liste ableiten
            pc = 0
            if has_pc:
                try:
                    pc = int(r["participation_count"] or 0)
                except Exception:
                    pc = 0

            markers: list[str] = []
            if has_years:
                markers = _parse_markers(r["tournament_years"])
                if not has_pc:
                    pc = len(markers)

                # Year-Counts aus Marker-Datum
                for m in markers:
                    y = _year_from_marker(m)
                    if y:
                        year_counts[y] = year_counts.get(y, 0) + 1

            # last_tournament_at: kann Legacy-Jahr sein oder Marker
            last_year: int | None = None
            if has_last:
                try:
                    v = r["last_tournament_at"]
                    s = ("" if v is None else str(v)).strip()
                    if s:
                        # Legacy: Jahr
                        if s.isdigit() and len(s) == 4:
                            last_year = int(s)
                        else:
                            # Marker
                            y = _year_from_marker(s)
                            if y:
                                last_year = y
                except Exception:
                    last_year = None

            # Fallback: falls last_tournament_at nicht gesetzt ist, aber Marker-Liste existiert -> max(Marker-Datum)
            if last_year is None and markers:
                # robust: sortiere nach Datum, nehme max
                best_year = None
                best_dt = None
                for m in markers:
                    dt = _marker_to_date(m)
                    if dt and (best_dt is None or dt > best_dt):
                        best_dt = dt
                        best_year = dt.year
                last_year = best_year

            part_buckets[_bucket_participation(pc)] = part_buckets.get(_bucket_participation(pc), 0) + 1
            recency_buckets[_bucket_recency(last_year, now_year)] = recency_buckets.get(
                _bucket_recency(last_year, now_year), 0
            ) + 1

        years_sorted = sorted(year_counts.items(), key=lambda t: t[0])

    return render_template(
        "addresses_stats.html",
        total=total_i,
        active=active_i,
        inactive=not_active_i,
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
    with db.connect() as con:
        default_ab_id = _default_ab_id(con)
        text, filename = addressbook_io.export_addresses_csv(con=con, addressbook_id=default_ab_id)
    return _csv_text_response(filename, text)


@bp.get("/addresses/import")
def addresses_import():
    return render_template("address_import.html")


@bp.post("/addresses/import")
def addresses_import_post():
    file = request.files.get("file")
    if not file:
        flash("Bitte eine CSV-Datei auswählen.", "error")
        return redirect(url_for("addresses.addresses_import"))

    raw = file.read()
    try:
        text = raw.decode("utf-8-sig")
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
    nxt = (request.args.get("next") or "").strip()
    if not nxt:
        nxt = url_for("addresses.addresses_list")
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
        "invite": 1,
        "participation_count": 0,
        "last_tournament_at": "",
        "tournament_years": "",
    }
    return render_template("address_form.html", a=defaults, mode="new", used=False, next=nxt)


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
    status = _norm_status(f.get("status"))
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
    nxt = (request.args.get("next") or "").strip() or (request.referrer or url_for("addresses.addresses_list"))

    with db.connect() as con:
        a = db.one(con, "SELECT * FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(nxt)

        used = _is_used_in_any_tournament(con, address_id)

    return render_template("address_form.html", a=a, mode="edit", used=used, next=nxt)


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
            _norm_status(f.get("status")),
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
# Soft-Delete: Deaktivieren / Reaktivieren (optional, falls später wieder Buttons)
# -----------------------------------------------------------------------------
@bp.post("/addresses/<int:address_id>/deactivate")
def address_deactivate(address_id: int):
    nxt = (request.form.get("next") or "").strip() or (request.referrer or url_for("addresses.addresses_list"))

    with db.connect() as con:
        a = db.one(con, "SELECT id, status FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(nxt)

        if (a["status"] or "aktiv") != "aktiv":
            flash("Adresse ist nicht aktiv (kann nicht per 'Deaktivieren' umgestellt werden).", "ok")
            return redirect(nxt)

        con.execute(
            "UPDATE addresses SET status='inaktiv', updated_at=datetime('now') WHERE id=?",
            (address_id,),
        )
        con.commit()

    flash("Adresse auf 'inaktiv' gesetzt.", "ok")
    return redirect(nxt)


@bp.post("/addresses/<int:address_id>/reactivate")
def address_reactivate(address_id: int):
    nxt = (request.form.get("next") or "").strip() or (request.referrer or url_for("addresses.addresses_list"))

    with db.connect() as con:
        a = db.one(con, "SELECT id FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(nxt)

        con.execute(
            "UPDATE addresses SET status='aktiv', updated_at=datetime('now') WHERE id=?",
            (address_id,),
        )
        con.commit()

    flash("Adresse auf 'aktiv' gesetzt.", "ok")
    return redirect(nxt)


# -----------------------------------------------------------------------------
# Optional: Physisches Löschen (stark eingeschränkt)
# -----------------------------------------------------------------------------
@bp.post("/addresses/<int:address_id>/delete")
def address_delete(address_id: int):
    nxt = (request.form.get("next") or "").strip() or (request.referrer or url_for("addresses.addresses_list"))

    with db.connect() as con:
        a = db.one(con, "SELECT id FROM addresses WHERE id=?", (address_id,))
        if not a:
            flash("Adresse nicht gefunden.", "error")
            return redirect(nxt)

        if _is_used_in_any_tournament(con, address_id):
            flash("Löschen nicht möglich: Adresse war bereits in einem Turnier. Bitte Status verwenden.", "error")
            return redirect(nxt)

        con.execute("DELETE FROM addresses WHERE id=?", (address_id,))
        con.commit()

    flash("Adresse gelöscht (war nie Turnierteilnehmer).", "ok")
    return redirect(nxt)