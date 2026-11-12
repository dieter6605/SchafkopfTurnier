from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime


ALLOWED_STATUS = {"aktiv", "inaktiv", "verzogen", "verstorben", "gesperrt"}
HISTORY_FIELDS = ("participation_count", "last_tournament_at", "tournament_years")
HISTORY_SQL = """
    COALESCE(a.participation_count, 0) > 0
    OR TRIM(COALESCE(a.last_tournament_at, '')) <> ''
    OR TRIM(COALESCE(a.tournament_years, '')) <> ''
"""
REGISTERED_SQL = "EXISTS (SELECT 1 FROM tournament_participants tp WHERE tp.address_id=a.id)"


def address_is_protected(con, address_id):
    return con.execute(
        f"SELECT 1 FROM addresses a WHERE a.id=? AND ({HISTORY_SQL} OR {REGISTERED_SQL})",
        (address_id,),
    ).fetchone() is not None


def _default_ab_id(con):
    row = con.execute("SELECT id FROM addressbooks WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise ValueError("Es gibt kein Standardadressbuch.")
    return row["id"]


def _addresses_columns(con):
    return [row["name"] for row in con.execute("PRAGMA table_info(addresses)")]


def export_addresses_csv(*, con: sqlite3.Connection, addressbook_id: int | None = None) -> tuple[str, str]:
    ab_id = addressbook_id if addressbook_id is not None else _default_ab_id(con)
    cols = _addresses_columns(con)
    rows = con.execute("SELECT * FROM addresses WHERE addressbook_id=? ORDER BY nachname, vorname, id", (ab_id,))
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(cols)
    for row in rows:
        values = dict(row)
        if "invite" in values and values["invite"] is None:
            values["invite"] = 0
        writer.writerow([values[c] for c in cols])
    return buf.getvalue(), f"addresses-export-{datetime.now():%Y%m%d-%H%M%S}.csv"


def book_status(con):
    book_id = _default_ab_id(con)
    counts = con.execute(f"""
        SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE {REGISTERED_SQL}) AS registered
        FROM addresses a WHERE a.addressbook_id=?
    """, (book_id,)).fetchone()
    return book_id, counts["total"], counts["registered"]


@dataclass
class ImportPreview:
    book_id: int
    existing: int
    registered: int
    rows: list[tuple[int, dict]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    invalid: int = 0
    token: str = ""

    @property
    def ready(self):
        return bool(self.rows) and not self.registered and not self.errors


def _parse_values(row, schema):
    values = {}
    for name in ("nachname", "vorname", "wohnort"):
        if not row[name].strip():
            raise ValueError(f"Pflichtfeld {name} ist leer.")
    for name, raw in row.items():
        value = raw.strip()
        if name == "addressbook_id":
            continue  # Exporte können aus einer anderen Installation stammen.
        if name == "id" and not value:
            raise ValueError("Die Spalte id ist vorhanden, die ID fehlt.")
        if name == "invite" and not value:
            values[name] = 0
        elif not value:
            if schema[name]["notnull"] or schema[name]["dflt_value"] is not None:
                continue  # SQLite übernimmt den Spaltenstandard.
            values[name] = None
        elif name in ("id", "invite", "participation_count"):
            if not value.isascii() or not value.isdecimal() or len(value.lstrip("0")) > 19:
                raise ValueError(f"{name} muss eine gültige nichtnegative ganze Zahl sein.")
            number = int(value.lstrip("0") or "0")
            if number >= 2**63 or (name == "id" and number == 0) or (name == "invite" and number not in (0, 1)):
                raise ValueError(f"Ungültiger Wert für {name}.")
            values[name] = number
        elif name == "status":
            if value.lower() not in ALLOWED_STATUS:
                raise ValueError("Unbekannter Adressstatus.")
            values[name] = value.lower()
        else:
            values[name] = value if name in ("nachname", "vorname", "wohnort") else raw
    return values


def preview_addresses(con, csv_text):
    book_id, existing, registered = book_status(con)
    preview = ImportPreview(book_id, existing, registered)
    schema = {r["name"]: r for r in con.execute("PRAGMA table_info(addresses)")}
    reader = csv.reader(io.StringIO(csv_text.removeprefix("\ufeff"), newline=""), delimiter=";", strict=True)
    try:
        headers = [v.strip() for v in next(reader, [])]
    except csv.Error:
        preview.errors.append("Zeile 1: CSV-Kopfzeile fehlerhaft.")
        return preview
    if not headers or not {"nachname", "vorname", "wohnort"}.issubset(headers):
        preview.errors.append("Zeile 1: Die Pflichtspalten nachname, vorname und wohnort werden benötigt.")
        return preview
    if len(set(headers)) != len(headers) or set(headers) - schema.keys():
        preview.errors.append("Zeile 1: Leere, unbekannte oder doppelte Spaltennamen.")
        return preview

    foreign_ids = {r[0] for r in con.execute("SELECT id FROM addresses WHERE addressbook_id<>?", (book_id,))}
    protected = {r["id"]: r for r in con.execute(
        f"SELECT * FROM addresses a WHERE a.addressbook_id=? AND ({HISTORY_SQL})", (book_id,),
    )}
    inherit_invite = "invite" in schema and "invite" not in headers
    existing_invites = {}
    if inherit_invite and "id" in headers:
        existing_invites = dict(con.execute(
            "SELECT id, COALESCE(invite, 0) FROM addresses WHERE addressbook_id=?", (book_id,),
        ))
    seen = set()
    while True:
        line = reader.line_num + 1
        try:
            fields = next(reader)
            if not fields:
                continue
            if len(fields) != len(headers):
                raise ValueError(f"{len(fields)} Felder statt {len(headers)}.")
            values = _parse_values(dict(zip(headers, fields)), schema)
            ident = values.get("id")
            if inherit_invite:
                values["invite"] = existing_invites.get(ident, 1)
            if ident is not None:
                if ident in seen:
                    raise ValueError(f"ID {ident} kommt mehrfach vor.")
                seen.add(ident)
                if ident in foreign_ids:
                    raise ValueError(f"ID {ident} gehört zu einem anderen Adressbuch.")
                if ident in protected:
                    old = protected[ident]
                    # Ein kompletter Export darf zurückgelesen werden. Ein Wechsel der
                    # Person oder das Entfernen ihrer Teilnahmehistorie braucht Einzelpflege.
                    if any(values[k].strip().casefold() != old[k].strip().casefold() for k in ("nachname", "vorname")):
                        raise ValueError(f"Die Person mit Teilnahmehistorie unter ID {ident} wurde geändert.")
                    for name in HISTORY_FIELDS:
                        empty = 0 if name == "participation_count" else ""
                        if name not in headers:
                            values[name] = old[name]
                        elif (values.get(name) or empty) != (old[name] or empty):
                            raise ValueError(f"Die Teilnahmehistorie von ID {ident} wurde geändert.")
            preview.rows.append((line, values))
        except StopIteration:
            break
        except (csv.Error, ValueError) as error:
            preview.invalid += 1
            preview.errors.append(f"Zeile {line}: {error}")
            if isinstance(error, csv.Error):
                break  # Nach einem fehlerhaften Anführungszeichen sind Folgezeilen nicht sicher zuordenbar.
    if not preview.rows and not preview.invalid:
        preview.errors.append("Die CSV enthält keine Adresszeilen.")
    missing = protected.keys() - seen
    if missing and preview.rows and not registered:
        preview.errors.append(f"{len(missing)} bestehende Adressen mit Teilnahmehistorie fehlen mit ihrer ID.")
    if preview.ready:
        digest = hashlib.sha256()
        digest.update(csv_text.encode("utf-8"))
        digest.update(f"\0{book_id}\0".encode())
        for row in con.execute("SELECT * FROM addresses WHERE addressbook_id=? ORDER BY id", (book_id,)):
            digest.update(json.dumps(tuple(row), ensure_ascii=True).encode())
        preview.token = digest.hexdigest()
    return preview


def _update_places(con, preview):
    """Ergänzt die gemeinsame Ortsliste und schützt Zuordnungen anderer Adressbücher."""
    imported = {}
    for _, row in preview.rows:
        postcode = (row.get("plz") or "").strip()
        town = (row.get("ort") or "").strip()
        if postcode and town:
            imported[row["wohnort"].strip()] = (postcode, town)
    if not imported:
        return

    shared = {r[0] for r in con.execute(
        "SELECT DISTINCT TRIM(wohnort) FROM addresses WHERE addressbook_id<>?", (preview.book_id,),
    )}
    existing = {r["wohnort"]: (r["plz"], r["ort"]) for r in con.execute("SELECT wohnort,plz,ort FROM wohnorte")}
    for place, (postcode, town) in imported.items():
        if place in shared:
            continue
        if place not in existing:
            con.execute("INSERT INTO wohnorte(wohnort,plz,ort) VALUES (?,?,?)", (place, postcode, town))
        elif existing[place] != (postcode, town):
            con.execute("UPDATE wohnorte SET plz=?,ort=? WHERE wohnort=?", (postcode, town, place))


def import_addresses_replace_default_from_csv_text(*, con, csv_text, expected_token=None):
    """Validiert im selben Schreibvorgang und ersetzt nur das Standardadressbuch."""
    started_transaction = not con.in_transaction
    if started_transaction:
        con.execute("BEGIN IMMEDIATE")
    con.execute("SAVEPOINT address_import")
    try:
        preview = preview_addresses(con, csv_text)
        if preview.registered:
            raise ValueError("Ersetzen gesperrt. Im Standardadressbuch sind Turnierteilnehmer registriert.")
        if not preview.ready:
            raise ValueError(" ".join(preview.errors))
        if expected_token is not None and expected_token != preview.token:
            raise ValueError("Datei oder Adressbestand haben sich geändert. Bitte die CSV erneut prüfen.")
        con.execute("DELETE FROM addresses WHERE addressbook_id=?", (preview.book_id,))
        for line, values in preview.rows:
            cols = ["addressbook_id", *values]
            names = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
            sql = f"INSERT INTO addresses({names}) VALUES ({', '.join('?' for _ in cols)})"
            try:
                con.execute(sql, (preview.book_id, *values.values()))
            except sqlite3.IntegrityError as error:
                raise ValueError(f"Zeile {line}: Diese Adresse verletzt eine Datenbankregel.") from error
        _update_places(con, preview)
    except BaseException:
        con.execute("ROLLBACK TO address_import")
        con.execute("RELEASE address_import")
        if started_transaction:
            con.rollback()
        raise
    con.execute("RELEASE address_import")
    return preview.book_id, len(preview.rows), 0
