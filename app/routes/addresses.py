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
# Marker-Parsing (Marker statt Jahreslisten)
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
        if len(p) == 10 and p.isalnum():
            markers.add(p)
            continue
        # Legacy-Jahr ignorieren
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


def _qs_for_list(
    *,
    q: str,
    status: str,
    email: str,
    phone: str,
    plzort: str,
    street: str,
    wohnort: str,
    invite: str,
    mismatch: str,
    last_in_list: str,
    view: str,
    per_page: int,
    page: int,
) -> dict[str, Any]:
    """Helper: saubere Querystring-Parameter für url_for (None entfernt Flask automatisch)."""
    return {
        "q": q or None,
        "status": status if status else "alle",
        "email": email if email else "alle",
        "phone": phone if phone else "alle",
        "plzort": plzort if plzort else "alle",
        "street": street if street else "alle",
        "wohnort": wohnort or None,
        "invite": invite if invite else "alle",
        "mismatch": mismatch if mismatch else "0",
        "last_in_list": last_in_list if last_in_list else "0",
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
    phone_filter = (request.args.get("phone") or "alle").strip().lower()      # alle|vorhanden|fehlt
    plzort_filter = (request.args.get("plzort") or "alle").strip().lower()    # alle|voll|fehlt
    street_filter = (request.args.get("street") or "alle").strip().lower()    # alle|voll|fehlt
    wohnort_filter = (request.args.get("wohnort") or "").strip()
    invite_filter = (request.args.get("invite") or "alle").strip().lower()    # alle|an|aus

    # Inkonsistenzfilter (nur wenn Spalten vorhanden)
    mismatch = (request.args.get("mismatch") or "0").strip()                  # 1 => participation_count != Anzahl Marker
    last_in_list = (request.args.get("last_in_list") or "0").strip()          # 1 => last_tournament_at nicht in tournament_years enthalten

    # Pagination
    per_page = _clamp_per_page(request.args.get("per_page"))
    page = _clamp_page(request.args.get("page"))
    offset = (page - 1) * per_page

    # Legacy: show_inactive=0 hieß früher: nur aktiv (wenn status nicht gesetzt)
    if (request.args.get("show_inactive") or "") == "0" and (request.args.get("status") is None):
        status_filter = "aktiv"

    with db.connect() as con:
        default_ab_id = _default_ab_id(con)

        # Spalten prüfen (damit Filter robust sind)
        has_invite = _has_column(con, "addresses", "invite")
        has_pc = _has_column(con, "addresses", "participation_count")
        has_last = _has_column(con, "addresses", "last_tournament_at")
        has_years = _has_column(con, "addresses", "tournament_years")

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

        # --- SQL-Ausdruck: Marker-Anzahl aus CSV (kommabasiert)
        # NOTE: zählt Tokens als 0 wenn leer, sonst commas+1. Keine Dedupe-Logik.
        marker_count_expr = (
            "(CASE WHEN COALESCE(TRIM(tournament_years),'')='' THEN 0 "
            "ELSE (LENGTH(tournament_years) - LENGTH(REPLACE(tournament_years, ',', '')) + 1) END)"
        )

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

            # Telefon vorhanden/fehlt
            if phone_filter == "vorhanden":
                where.append("telefon IS NOT NULL AND TRIM(telefon)!=''")
            elif phone_filter == "fehlt":
                where.append("(telefon IS NULL OR TRIM(telefon)='')")

            # PLZ+Ort voll/fehlt
            if plzort_filter == "voll":
                where.append("plz IS NOT NULL AND TRIM(plz)!='' AND ort IS NOT NULL AND TRIM(ort)!=''")
            elif plzort_filter == "fehlt":
                where.append("((plz IS NULL OR TRIM(plz)='') OR (ort IS NULL OR TRIM(ort)=''))")

            # Straße+Hausnummer voll/fehlt
            if street_filter == "voll":
                where.append("strasse IS NOT NULL AND TRIM(strasse)!='' AND hausnummer IS NOT NULL AND TRIM(hausnummer)!=''")
            elif street_filter == "fehlt":
                where.append("((strasse IS NULL OR TRIM(strasse)='') OR (hausnummer IS NULL OR TRIM(hausnummer)=''))")

            # Wohnort
            if wohnort_filter:
                where.append("wohnort=?")
                params.append(wohnort_filter)

            # Einladung an/aus (nur wenn Spalte existiert)
            if has_invite:
                if invite_filter == "an":
                    where.append("COALESCE(invite,0)=1")
                elif invite_filter == "aus":
                    where.append("COALESCE(invite,0)=0")

            # Inkonsistenzen (nur wenn Felder da sind)
            if mismatch == "1" and has_pc and has_years:
                where.append(f"COALESCE(participation_count,0) != {marker_count_expr}")

            if last_in_list == "1" and has_last and has_years:
                # last_tournament_at ist Marker => muss in der CSV-Liste enthalten sein
                # (wenn last_tournament_at leer => ebenfalls "inkonsistent" im Sinne der Prüfung)
                where.append(
                    "(COALESCE(TRIM(last_tournament_at),'')='' "
                    "OR instr(',' || COALESCE(tournament_years,'') || ',', ',' || TRIM(last_tournament_at) || ',') = 0)"
                )

            return where, params

        any_filter = (
            bool(qtxt)
            or (status_filter != "alle")
            or (email_filter != "alle")
            or (phone_filter != "alle")
            or (plzort_filter != "alle")
            or (street_filter != "alle")
            or bool(wohnort_filter)
            or (invite_filter != "alle")
            or (mismatch == "1")
            or (last_in_list == "1")
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
        # Filter/Ansicht (bestehende)
        view=view,
        status_filter=status_filter,
        email_filter=email_filter,
        wohnort_filter=wohnort_filter,
        invite_filter=invite_filter,
        # Neue Filter (optional, templates die sie nicht kennen ignorieren sie)
        phone_filter=phone_filter,
        plzort_filter=plzort_filter,
        street_filter=street_filter,
        mismatch=mismatch,
        last_in_list=last_in_list,
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
# Statistik fürs Adressbuch (Marker statt Jahre) + Datenqualität + Top-Listen
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
        not_active = db.one(
            con, "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status!='aktiv'", (default_ab_id,)
        )

        total_i = int((total["c"] or 0) if total else 0)
        active_i = int((active["c"] or 0) if active else 0)
        not_active_i = int((not_active["c"] or 0) if not_active else 0)

        invite_yes = invite_no = None
        invite_yes_email = invite_yes_no_email = None
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

            r_ie = db.one(
                con,
                "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND invite=1 AND email IS NOT NULL AND TRIM(email)!=''",
                (default_ab_id,),
            )
            r_in = db.one(
                con,
                "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND invite=1 AND (email IS NULL OR TRIM(email)='')",
                (default_ab_id,),
            )
            invite_yes_email = int((r_ie["c"] or 0) if r_ie else 0)
            invite_yes_no_email = int((r_in["c"] or 0) if r_in else 0)

        # Datenqualität (SQL)
        q_missing_email = db.one(
            con,
            "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND (email IS NULL OR TRIM(email)='')",
            (default_ab_id,),
        )
        q_missing_phone = db.one(
            con,
            "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND (telefon IS NULL OR TRIM(telefon)='')",
            (default_ab_id,),
        )
        q_missing_plzort = db.one(
            con,
            "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND ((plz IS NULL OR TRIM(plz)='') OR (ort IS NULL OR TRIM(ort)=''))",
            (default_ab_id,),
        )
        q_missing_street = db.one(
            con,
            "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND ((strasse IS NULL OR TRIM(strasse)='') OR (hausnummer IS NULL OR TRIM(hausnummer)=''))",
            (default_ab_id,),
        )

        missing_email = int((q_missing_email["c"] or 0) if q_missing_email else 0)
        missing_phone = int((q_missing_phone["c"] or 0) if q_missing_phone else 0)
        missing_plzort = int((q_missing_plzort["c"] or 0) if q_missing_plzort else 0)
        missing_street = int((q_missing_street["c"] or 0) if q_missing_street else 0)

        # Dubletten-Kandidaten: gleiche (nachname, vorname, wohnort) mehrfach
        dup_rows = db.q(
            con,
            """
            SELECT nachname, vorname, wohnort, COUNT(*) AS c
            FROM addresses
            WHERE addressbook_id=?
              AND TRIM(COALESCE(nachname,''))!=''
              AND TRIM(COALESCE(vorname,''))!=''
              AND TRIM(COALESCE(wohnort,''))!=''
            GROUP BY nachname, vorname, wohnort
            HAVING COUNT(*) > 1
            ORDER BY c DESC, nachname COLLATE NOCASE, vorname COLLATE NOCASE
            LIMIT 20
            """,
            (default_ab_id,),
        )
        dup_candidates = [
            {"nachname": r["nachname"], "vorname": r["vorname"], "wohnort": r["wohnort"], "c": int(r["c"] or 0)}
            for r in dup_rows
        ]
        dup_total = sum(int(x["c"]) for x in dup_candidates) if dup_candidates else 0

        # Inkonsistenz: participation_count != Anzahl Marker (SQL zählt Tokens per Komma)
        mismatch_pc_markers = 0
        mismatch_last_not_in_list = 0
        if has_pc and has_years:
            r_mis = db.one(
                con,
                """
                SELECT COUNT(*) AS c
                FROM addresses
                WHERE addressbook_id=?
                  AND COALESCE(participation_count,0) !=
                      (CASE WHEN COALESCE(TRIM(tournament_years),'')='' THEN 0
                            ELSE (LENGTH(tournament_years) - LENGTH(REPLACE(tournament_years, ',', '')) + 1) END)
                """,
                (default_ab_id,),
            )
            mismatch_pc_markers = int((r_mis["c"] or 0) if r_mis else 0)

        if has_last and has_years:
            r_lmis = db.one(
                con,
                """
                SELECT COUNT(*) AS c
                FROM addresses
                WHERE addressbook_id=?
                  AND (COALESCE(TRIM(last_tournament_at),'')=''
                       OR instr(',' || COALESCE(tournament_years,'') || ',', ',' || TRIM(last_tournament_at) || ',') = 0)
                """,
                (default_ab_id,),
            )
            mismatch_last_not_in_list = int((r_lmis["c"] or 0) if r_lmis else 0)

        # Stats-Berechnung per Python (Buckets + Jahre + Top-Listen)
        cols = ["id", "nachname", "vorname", "wohnort", "status", "email", "telefon", "plz", "ort", "strasse", "hausnummer"]
        if has_invite:
            cols.append("invite")
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

        # Teilnahmen pro Jahr:
        # - persons_by_year: Anzahl Personen mit mind. 1 Marker in diesem Jahr
        # - markers_by_year: Anzahl Marker-Tokens in Summe (Häufigkeit) pro Jahr
        persons_by_year: dict[int, set[int]] = {}
        markers_by_year: dict[int, int] = {}

        # Top-Listen vorbereiten
        top_by_part: list[dict[str, Any]] = []
        top_recent: list[dict[str, Any]] = []
        top_old: list[dict[str, Any]] = []

        def _safe_str(x: Any) -> str:
            return ("" if x is None else str(x)).strip()

        for r in rows:
            aid = int(r["id"])

            # effektive Marker-Liste
            markers: list[str] = []
            if has_years:
                markers = _parse_markers(r["tournament_years"])

            # participation_count (effektiv)
            pc_eff = 0
            if has_pc:
                try:
                    pc_eff = int(r["participation_count"] or 0)
                except Exception:
                    pc_eff = 0
            else:
                pc_eff = len(markers)

            # last_tournament_year (effektiv)
            last_year: int | None = None
            last_dt: datetime | None = None

            if has_last:
                s = _safe_str(r["last_tournament_at"])
                if s:
                    if s.isdigit() and len(s) == 4:
                        last_year = int(s)
                        try:
                            last_dt = datetime(last_year, 1, 1)
                        except Exception:
                            last_dt = None
                    else:
                        dt = _marker_to_date(s)
                        if dt:
                            last_year = dt.year
                            last_dt = dt

            # Fallback: aus Markern den neuesten nehmen
            if last_dt is None and markers:
                best_dt = None
                for m in markers:
                    dt = _marker_to_date(m)
                    if dt and (best_dt is None or dt > best_dt):
                        best_dt = dt
                last_dt = best_dt
                last_year = best_dt.year if best_dt else None

            part_buckets[_bucket_participation(pc_eff)] = part_buckets.get(_bucket_participation(pc_eff), 0) + 1
            recency_buckets[_bucket_recency(last_year, now_year)] = recency_buckets.get(_bucket_recency(last_year, now_year), 0) + 1

            # Jahre zählen
            if markers:
                years_here: set[int] = set()
                for m in markers:
                    y = _year_from_marker(m)
                    if not y:
                        continue
                    years_here.add(y)
                    markers_by_year[y] = markers_by_year.get(y, 0) + 1
                for y in years_here:
                    persons_by_year.setdefault(y, set()).add(aid)

            # Top-Listen Datensätze
            nm = _safe_str(r["nachname"])
            vm = _safe_str(r["vorname"])
            wo = _safe_str(r["wohnort"])
            disp = f"{nm}, {vm}" + (f" · {wo}" if wo else "")

            top_by_part.append({"id": aid, "name": disp, "pc": pc_eff, "last": last_dt})
            if last_dt is not None:
                top_recent.append({"id": aid, "name": disp, "pc": pc_eff, "last": last_dt, "status": _safe_str(r["status"])})
                top_old.append({"id": aid, "name": disp, "pc": pc_eff, "last": last_dt, "status": _safe_str(r["status"])})

        # Top 10: nach participation_count
        top_by_part_sorted = sorted(top_by_part, key=lambda x: (int(x["pc"]), x["name"]), reverse=True)[:10]

        # Top 10: zuletzt teilgenommen (neueste last_dt)
        top_recent_sorted = sorted(top_recent, key=lambda x: (x["last"], x["name"]), reverse=True)[:10]

        # Top 10: lange nicht gesehen (älteste last_dt), aber nur status=aktiv (operativ)
        top_old_active = [x for x in top_old if (x.get("status") or "").lower() == "aktiv"]
        top_old_sorted = sorted(top_old_active, key=lambda x: (x["last"], x["name"]))[:10]

        # Years table
        years_sorted = sorted(((y, len(ids), markers_by_year.get(y, 0)) for y, ids in persons_by_year.items()), key=lambda t: t[0])

        has_participation = has_pc or has_years

    return render_template(
        "addresses_stats.html",
        total=total_i,
        active=active_i,
        inactive=not_active_i,
        has_invite=has_invite,
        invite_yes=invite_yes,
        invite_no=invite_no,
        invite_yes_email=invite_yes_email,
        invite_yes_no_email=invite_yes_no_email,
        has_participation=has_participation,
        part_buckets=part_buckets,
        recency_buckets=recency_buckets,
        years_sorted=years_sorted,  # (year, persons, markers)
        # Datenqualität
        missing_email=missing_email,
        missing_phone=missing_phone,
        missing_plzort=missing_plzort,
        missing_street=missing_street,
        dup_candidates=dup_candidates,
        dup_total=dup_total,
        mismatch_pc_markers=mismatch_pc_markers,
        mismatch_last_not_in_list=mismatch_last_not_in_list,
        # Top-Listen
        top_by_part=top_by_part_sorted,
        top_recent=top_recent_sorted,
        top_old=top_old_sorted,
        # Flags für Link-Logik
        has_years=has_years,
        has_pc=has_pc,
        has_last=has_last,
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
# Soft-Delete: Deaktivieren / Reaktivieren
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