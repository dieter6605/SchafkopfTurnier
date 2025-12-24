# app/services/addressbook_io.py
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Iterable

from .. import db


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm_none(v: Any) -> Any:
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


def _addresses_columns(con) -> list[str]:
    rows = con.execute("PRAGMA table_info(addresses);").fetchall()
    return [str(r["name"]) for r in rows]


def _rebuild_wohnorte_from_addresses(con) -> None:
    """
    Wohnorte-Lookup vollständig aus addresses neu aufbauen.
    (Damit passt es immer zum aktuellen Datenbestand – auch nach Import.)
    """
    con.execute("DELETE FROM wohnorte")
    con.execute(
        """
        INSERT INTO wohnorte(wohnort, plz, ort)
        SELECT wohnort, plz, ort
        FROM addresses
        WHERE TRIM(COALESCE(wohnort,''))!=''
          AND TRIM(COALESCE(plz,''))!=''
          AND TRIM(COALESCE(ort,''))!=''
        GROUP BY wohnort, plz, ort
        """
    )


def export_addresses_csv(*, con, addressbook_id: int) -> tuple[str, str]:
    """
    Export: CSV-Text + Filename.
    Trennzeichen ';', Header = DB-Spaltennamen, UTF-8 (+BOM im Response kommt im Route).
    """
    cols = _addresses_columns(con)

    rows = db.q(
        con,
        f"""
        SELECT {', '.join(cols)}
        FROM addresses
        WHERE addressbook_id=?
        ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE, id
        """,
        (int(addressbook_id),),
    )

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";", lineterminator="\n")
    w.writeheader()
    for r in rows:
        d = {c: (r[c] if c in r.keys() else None) for c in cols}
        w.writerow(d)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"addresses-export-{ts}.csv"
    return buf.getvalue(), filename


def import_addresses_replace_default_from_csv_text(*, con, csv_text: str) -> tuple[int, int, int]:
    """
    Import: CSV (Semikolon, UTF-8) -> erzeugt neues Addressbook und setzt es als Default.
    Keine Deletes an historischen Adressen nötig, daher turnierfest.

    Rückgabe: (new_addressbook_id, inserted, skipped)
    """
    buf = io.StringIO(csv_text)
    reader = csv.DictReader(buf, delimiter=";")

    if not reader.fieldnames:
        raise ValueError("CSV hat keinen Header (Spaltennamen fehlen).")

    db_cols = _addresses_columns(con)
    csv_cols = [c.strip() for c in reader.fieldnames if c and str(c).strip() != ""]

    required = {"nachname", "vorname", "wohnort"}
    missing_req = [x for x in sorted(required) if x not in set(csv_cols)]
    if missing_req:
        raise ValueError(f"CSV fehlt Pflichtspalten: {', '.join(missing_req)}.")

    unknown = [c for c in csv_cols if c not in set(db_cols)]
    if unknown:
        raise ValueError(f"CSV enthält unbekannte Spalten: {', '.join(unknown)}.")

    # Neues Addressbook anlegen + als Default setzen
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = f"Import {ts}"
    cur = con.execute("INSERT INTO addressbooks(name, is_default) VALUES (?, 0)", (name,))
    new_ab_id = int(cur.lastrowid)

    con.execute("UPDATE addressbooks SET is_default=0")
    con.execute("UPDATE addressbooks SET is_default=1 WHERE id=?", (new_ab_id,))

    inserted = 0
    skipped = 0

    # Wir importieren alle addresses-Spalten außer:
    # - id (immer neu)
    # - addressbook_id (immer new_ab_id)
    insert_cols = [c for c in db_cols if c not in ("id",)]
    placeholders = ",".join(["?"] * len(insert_cols))
    sql_ins = f"INSERT INTO addresses({', '.join(insert_cols)}) VALUES ({placeholders})"

    for row in reader:
        nachname = (row.get("nachname") or "").strip()
        vorname = (row.get("vorname") or "").strip()
        wohnort = (row.get("wohnort") or "").strip()
        if not nachname or not vorname or not wohnort:
            skipped += 1
            continue

        values: list[Any] = []
        for c in insert_cols:
            if c == "addressbook_id":
                values.append(new_ab_id)
                continue

            v = row.get(c)

            if c in ("invite", "participation_count"):
                iv = _int_or_none(v)
                if c == "invite":
                    values.append(1 if iv is None else iv)
                else:
                    values.append(0 if iv is None else iv)
                continue

            if c in ("created_at", "updated_at"):
                vv = _norm_none(v)
                values.append(vv if vv is not None else _now_iso())
                continue

            values.append(_norm_none(v))

        con.execute(sql_ins, tuple(values))
        inserted += 1

    # wohnorte neu aufbauen (global aus addresses)
    _rebuild_wohnorte_from_addresses(con)

    return new_ab_id, inserted, skipped