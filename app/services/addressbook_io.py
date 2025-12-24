# app/services/addressbook_io.py
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Iterable

import sqlite3


def _now_sql() -> str:
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


def _addresses_columns(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("PRAGMA table_info(addresses);").fetchall()
    return [str(r["name"]) for r in rows]


def _default_ab_id(con: sqlite3.Connection) -> int:
    r = con.execute("SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1").fetchone()
    return int(r["id"]) if r else 1


def _set_default_addressbook(con: sqlite3.Connection, new_default_id: int) -> None:
    con.execute("UPDATE addressbooks SET is_default=0 WHERE is_default=1")
    con.execute("UPDATE addressbooks SET is_default=1 WHERE id=?", (int(new_default_id),))


def _rebuild_wohnorte_from_addresses(con: sqlite3.Connection, *, addressbook_id: int) -> None:
    """
    Baut die Lookup-Tabelle wohnorte aus addresses neu auf.

    WICHTIG: wohnorte.wohnort ist UNIQUE -> daher UPSERT, um Duplikate abzufangen.
    Nur vollständige Tripel (wohnort+plz+ort) werden übernommen.
    """
    # sauber neu aufbauen
    con.execute("DELETE FROM wohnorte")

    rows = con.execute(
        """
        SELECT wohnort, plz, ort
        FROM addresses
        WHERE addressbook_id=?
          AND TRIM(IFNULL(wohnort,'')) <> ''
          AND TRIM(IFNULL(plz,'')) <> ''
          AND TRIM(IFNULL(ort,'')) <> ''
        """,
        (int(addressbook_id),),
    ).fetchall()

    for r in rows:
        w = (r["wohnort"] or "").strip()
        p = (r["plz"] or "").strip()
        o = (r["ort"] or "").strip()
        if not w or not p or not o:
            continue

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


def export_addresses_csv(*, con: sqlite3.Connection, addressbook_id: int | None = None) -> tuple[str, str]:
    """
    Exportiert alle Adressen eines Addressbooks als CSV-Text (Semikolon, Header=DB-Spaltennamen).
    Rückgabe: (csv_text, filename)
    """
    ab_id = int(addressbook_id) if addressbook_id is not None else _default_ab_id(con)

    cols = _addresses_columns(con)
    # Export: wir geben exakt die DB-Spalten aus (inkl. id), weil du "DB-Feldnamen übernehmen" wolltest.
    rows = con.execute(
        f"SELECT {', '.join(cols)} FROM addresses WHERE addressbook_id=? ORDER BY nachname, vorname, id",
        (ab_id,),
    ).fetchall()

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";", lineterminator="\n")
    w.writeheader()
    for r in rows:
        d = {c: (r[c] if c in r.keys() else None) for c in cols}
        w.writerow(d)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"addresses-export-{ts}.csv"
    return buf.getvalue(), filename


def import_addresses_replace_default_from_csv_text(
    *,
    con: sqlite3.Connection,
    csv_text: str,
) -> tuple[int, int, int]:
    """
    Importiert CSV (Semikolon, UTF-8) und ersetzt das *aktive* Adressbuch
    ohne Historie zu zerstören:

    - Es wird ein NEUES Addressbook angelegt
    - Alle importierten addresses landen in diesem neuen Addressbook
    - Dieses neue Addressbook wird default (is_default=1)
    - wohnorte wird aus den neuen addresses neu aufgebaut

    Rückgabe: (new_ab_id, inserted, skipped)

    Erwartung:
    - Header enthält DB-Feldnamen
    - Pflichtfelder: nachname, vorname, wohnort
    - Import ist "Replace Default", aber nicht "Delete Old": alte Adressbücher bleiben erhalten.
    """
    if csv_text is None:
        raise ValueError("CSV ist leer.")

    buf = io.StringIO(csv_text)
    reader = csv.DictReader(buf, delimiter=";")

    if not reader.fieldnames:
        raise ValueError("CSV hat keinen Header (Spaltennamen fehlen).")

    # DB-Spalten
    db_cols = _addresses_columns(con)
    db_colset = set(db_cols)

    csv_cols = [c.strip() for c in reader.fieldnames if c and str(c).strip() != ""]
    csv_colset = set(csv_cols)

    required = {"nachname", "vorname", "wohnort"}
    missing_req = [x for x in sorted(required) if x not in csv_colset]
    if missing_req:
        raise ValueError(f"CSV fehlt Pflichtspalten: {', '.join(missing_req)}.")

    unknown = [c for c in csv_cols if c not in db_colset]
    if unknown:
        raise ValueError(f"CSV enthält unbekannte Spalten: {', '.join(unknown)}.")

    # Neues Addressbook anlegen
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = f"Import {ts}"
    con.execute("INSERT INTO addressbooks(name, is_default) VALUES (?,0)", (name,))
    new_ab_id = int(con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    # Insert vorbereiten:
    # - id NIE importieren (Autoincrement)
    # - addressbook_id IMMER new_ab_id
    insert_cols = [c for c in db_cols if c != "id"]
    placeholders = ",".join(["?"] * len(insert_cols))
    sql_ins = f"INSERT INTO addresses({', '.join(insert_cols)}) VALUES ({placeholders})"

    inserted = 0
    skipped = 0

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
                if c == "participation_count":
                    values.append(iv if iv is not None else 0)
                else:
                    values.append(iv if iv is not None else 1)
                continue

            if c in ("created_at", "updated_at"):
                vv = _norm_none(v)
                values.append(vv if vv is not None else _now_sql())
                continue

            values.append(_norm_none(v))

        con.execute(sql_ins, tuple(values))
        inserted += 1

    # Neues Addressbook als Default setzen
    _set_default_addressbook(con, new_ab_id)

    # Wohnorte neu aufbauen (UPSERT -> kein UNIQUE-Crash mehr)
    _rebuild_wohnorte_from_addresses(con, addressbook_id=new_ab_id)

    return new_ab_id, inserted, skipped