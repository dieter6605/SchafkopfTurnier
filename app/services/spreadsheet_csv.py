"""Semikolon-CSV für die Anzeige in Tabellenkalkulationen."""
import csv
import io
import unicodedata


def _cell(value):
    if not isinstance(value, str):
        return value
    # Nur den Feldanfang prüfen. Originaltext und numerische Daten bleiben unterscheidbar.
    for char in value:
        if char.isspace() or unicodedata.category(char) in ("Cc", "Cf"):
            continue
        if unicodedata.normalize("NFKC", char)[:1] in {"=", "+", "-", "@"}:
            return "Text: " + value
        break
    return value


def spreadsheet_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
    for row in rows:
        writer.writerow(_cell(value) for value in row)
    return stream.getvalue()
