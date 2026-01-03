# app/routes/tournaments/export.py
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from typing import Any

from flask import Response, flash, redirect, url_for

from ... import db
from . import bp
from .helpers import _get_tournament


def _csv_bytes(rows: list[list[Any]], *, delimiter: str = ";") -> bytes:
    """
    rows: Liste von Zeilen (je Zeile = Liste von Spalten)
    Rückgabe: UTF-8 mit BOM (Excel-freundlich)
    """
    out = io.StringIO()
    w = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("utf-8-sig")


def _zip_add_csv(z: zipfile.ZipFile, path_in_zip: str, rows: list[list[Any]]) -> None:
    z.writestr(path_in_zip, _csv_bytes(rows))


def _zip_add_text(z: zipfile.ZipFile, path_in_zip: str, text: str) -> None:
    z.writestr(path_in_zip, (text or "").encode("utf-8"))


def _safe_filename(s: str) -> str:
    """
    Einfacher Dateiname-safe Helper für ZIP paths.
    (Wir halten es schlicht: nur problematische Zeichen weg)
    """
    s = (s or "").strip()
    for ch in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
        s = s.replace(ch, "_")
    return s


def _rank_places(rows: list[dict[str, Any]], *, key_fields: tuple[str, str]) -> dict[int, int]:
    """
    Platzierungen mit Ties:
      gleiche (points, soli) => gleicher Rang.
    Erwartet pro Row:
      - tp_id (int)
      - fields in key_fields (int)
    Rückgabe: tp_id -> place
    """
    places: dict[int, int] = {}
    last_key: tuple[int, int] | None = None
    place = 0
    shown = 0

    for r in rows:
        shown += 1
        key = (int(r[key_fields[0]]), int(r[key_fields[1]]))
        if key != last_key:
            place = shown
            last_key = key
        places[int(r["tp_id"])] = int(place)

    return places


@bp.get("/tournaments/<int:tournament_id>/export.csv")
def tournament_export_csv(tournament_id: int):
    """
    Kompatibilität: bestehender Export bleibt (nur Gesamtwertung).
    """
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        rows = db.q(
            con,
            """
            SELECT
                tp.player_no,
                a.nachname,
                a.vorname,
                a.wohnort,
                COALESCE(SUM(sc.points), 0) AS points,
                COALESCE(SUM(sc.soli), 0)   AS soli
            FROM tournament_participants tp
            JOIN addresses a ON a.id = tp.address_id
            LEFT JOIN tournament_scores sc
              ON sc.tournament_id = tp.tournament_id
             AND sc.tp_id = tp.id
            WHERE tp.tournament_id = ?
            GROUP BY tp.id
            ORDER BY
                points DESC,
                soli DESC,
                a.nachname COLLATE NOCASE ASC,
                a.vorname COLLATE NOCASE ASC,
                tp.player_no ASC
            """,
            (tournament_id,),
        )

    data_rows: list[list[Any]] = []
    data_rows.append(["turnier", "datum", "beginn", "nr", "nachname", "vorname", "wohnort", "punkte", "soli"])
    for r in rows:
        data_rows.append(
            [
                t["title"],
                t["event_date"],
                t["start_time"],
                int(r["player_no"]),
                r["nachname"],
                r["vorname"],
                r["wohnort"],
                int(r["points"]),
                int(r["soli"]),
            ]
        )

    data = _csv_bytes(data_rows)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"skt-export-{tournament_id}-{ts}.csv"

    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.get("/tournaments/<int:tournament_id>/export.zip")
def tournament_export_zip(tournament_id: int):
    """
    ZIP-Export:
      - 01_gesamtwertung.csv
      - 02_teilnehmer_anmeldung.csv
      - 03_gesamtuebersicht.csv
      - 04_scores_komplett.csv               (NEU: alle Einzelscores in einer Datei)
      - 05_pivot_vorlage_scores.csv          (NEU: Pivot-Vorlage/Long-Format für Excel)
      - README.txt
      - rundenwertung/Rxx.csv
      - sitzplan/Rxx.csv
      - tische/Rxx_Tyy.csv
    """
    with db.connect() as con:
        t_row = _get_tournament(con, tournament_id)
        if not t_row:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        # ✅ Fix für sqlite3.Row: wir brauchen .get(...) in README etc.
        t: dict[str, Any] = dict(t_row)

        # -------------------------
        # Meta: vorhandene Runden
        # -------------------------
        round_rows = db.q(
            con,
            "SELECT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no ASC",
            (tournament_id,),
        )
        round_nos = [int(r["round_no"]) for r in round_rows]

        # -------------------------
        # Teilnehmerbasis (für Overview + README)
        # -------------------------
        participants = db.q(
            con,
            """
            SELECT
              tp.id AS tp_id,
              tp.player_no,
              tp.address_id,
              tp.display_name,
              tp.created_at AS tp_created_at,
              tp.updated_at AS tp_updated_at,

              a.nachname, a.vorname,
              a.wohnort, a.plz, a.ort,
              a.strasse, a.hausnummer,
              a.telefon, a.email,
              a.status
            FROM tournament_participants tp
            JOIN addresses a ON a.id = tp.address_id
            WHERE tp.tournament_id=?
            ORDER BY tp.player_no ASC, tp.id ASC
            """,
            (tournament_id,),
        )

        participants_count = len(participants)
        rounds_count = len(round_nos)

        # -------------------------
        # 1) Gesamtwertung (Totals)
        # -------------------------
        overall_q = db.q(
            con,
            """
            SELECT
                tp.id AS tp_id,
                tp.player_no,
                a.nachname,
                a.vorname,
                a.wohnort,
                COALESCE(SUM(sc.points), 0) AS points,
                COALESCE(SUM(sc.soli), 0)   AS soli
            FROM tournament_participants tp
            JOIN addresses a ON a.id = tp.address_id
            LEFT JOIN tournament_scores sc
              ON sc.tournament_id = tp.tournament_id
             AND sc.tp_id = tp.id
            WHERE tp.tournament_id = ?
            GROUP BY tp.id
            ORDER BY
                points DESC,
                soli DESC,
                a.nachname COLLATE NOCASE ASC,
                a.vorname COLLATE NOCASE ASC,
                tp.player_no ASC
            """,
            (tournament_id,),
        )

        overall_rows: list[list[Any]] = []
        overall_rows.append(["turnier", "datum", "beginn", "nr", "nachname", "vorname", "wohnort", "punkte", "soli"])
        for r in overall_q:
            overall_rows.append(
                [
                    t.get("title", ""),
                    t.get("event_date", ""),
                    t.get("start_time", ""),
                    int(r["player_no"]),
                    r["nachname"],
                    r["vorname"],
                    r["wohnort"],
                    int(r["points"]),
                    int(r["soli"]),
                ]
            )

        # Gesamt-Rang (Ties nach points/soli)
        overall_for_rank: list[dict[str, Any]] = [
            {
                "tp_id": int(r["tp_id"]),
                "points": int(r["points"]),
                "soli": int(r["soli"]),
            }
            for r in overall_q
        ]
        overall_place_by_tp = _rank_places(overall_for_rank, key_fields=("points", "soli"))

        overall_points_by_tp = {int(r["tp_id"]): int(r["points"]) for r in overall_q}
        overall_soli_by_tp = {int(r["tp_id"]): int(r["soli"]) for r in overall_q}

        # -------------------------
        # 2) Teilnehmeranmeldung (breit)
        # -------------------------
        part_rows: list[list[Any]] = []
        part_rows.append(
            [
                "turnier",
                "nr",
                "nachname",
                "vorname",
                "wohnort",
                "plz",
                "ort",
                "strasse",
                "hausnummer",
                "telefon",
                "email",
                "status",
                "created_at",
                "updated_at",
            ]
        )
        for r in participants:
            part_rows.append(
                [
                    t.get("title", ""),
                    int(r["player_no"]) if r["player_no"] is not None else "",
                    r["nachname"],
                    r["vorname"],
                    r["wohnort"],
                    r["plz"],
                    r["ort"],
                    r["strasse"],
                    r["hausnummer"],
                    r["telefon"],
                    r["email"],
                    r["status"],
                    r["tp_created_at"],
                    r["tp_updated_at"],
                ]
            )

        # -------------------------
        # Per-Runde Maps für Übersicht
        #   round_maps[rn][tp_id] = dict(place, points, soli, table_no)
        # -------------------------
        round_maps: dict[int, dict[int, dict[str, Any]]] = {}

        for rn in round_nos:
            score_rows = db.q(
                con,
                """
                SELECT
                    sc.tp_id,
                    tp.player_no,
                    a.nachname, a.vorname, a.wohnort,
                    sc.points, sc.soli,
                    sc.table_no,
                    COALESCE(s.seat, '') AS seat
                FROM tournament_scores sc
                JOIN tournament_participants tp ON tp.id=sc.tp_id
                JOIN addresses a ON a.id=tp.address_id
                LEFT JOIN tournament_seats s
                  ON s.tournament_id=sc.tournament_id
                 AND s.round_no=sc.round_no
                 AND s.tp_id=sc.tp_id
                WHERE sc.tournament_id=? AND sc.round_no=?
                ORDER BY
                    sc.points DESC,
                    sc.soli DESC,
                    a.nachname COLLATE NOCASE ASC,
                    a.vorname COLLATE NOCASE ASC,
                    tp.player_no ASC
                """,
                (tournament_id, rn),
            )

            # rank
            to_rank = [{"tp_id": int(r["tp_id"]), "points": int(r["points"]), "soli": int(r["soli"])} for r in score_rows]
            place_map = _rank_places(to_rank, key_fields=("points", "soli"))

            rm: dict[int, dict[str, Any]] = {}
            for r in score_rows:
                tp_id = int(r["tp_id"])
                rm[tp_id] = {
                    "place": int(place_map.get(tp_id, 0) or 0),
                    "points": int(r["points"]),
                    "soli": int(r["soli"]),
                    "table_no": int(r["table_no"]),
                    "seat": (r["seat"] or ""),
                }
            round_maps[rn] = rm

        # -------------------------
        # 3) Gesamtübersicht (eine Zeile je Teilnehmer, breite Rundenspalten)
        # -------------------------
        overview_header: list[Any] = [
            "platz_gesamt",
            "punkte_gesamt",
            "soli_gesamt",
            "nr",
            "nachname",
            "vorname",
            "wohnort",
            "plz",
            "ort",
            "email",
        ]

        for rn in round_nos:
            overview_header.extend(
                [
                    f"platz_r{rn}",
                    f"punkte_r{rn}",
                    f"soli_r{rn}",
                    f"tisch_r{rn}",
                ]
            )

        overview_rows: list[list[Any]] = [overview_header]

        for p in participants:
            tp_id = int(p["tp_id"])
            row: list[Any] = [
                int(overall_place_by_tp.get(tp_id, 0) or 0),
                int(overall_points_by_tp.get(tp_id, 0) or 0),
                int(overall_soli_by_tp.get(tp_id, 0) or 0),
                int(p["player_no"]) if p["player_no"] is not None else "",
                p["nachname"],
                p["vorname"],
                p["wohnort"],
                p["plz"],
                p["ort"],
                p["email"],
            ]

            for rn in round_nos:
                rm = round_maps.get(rn, {})
                rr = rm.get(tp_id)
                if rr:
                    row.extend([int(rr["place"]), int(rr["points"]), int(rr["soli"]), int(rr["table_no"])])
                else:
                    row.extend(["", "", "", ""])
            overview_rows.append(row)

        # -------------------------
        # 04) Scores komplett (NEU)
        # -------------------------
        scores_all = db.q(
            con,
            """
            SELECT
              sc.round_no,
              sc.table_no,
              COALESCE(s.seat, '') AS seat,
              tp.player_no,
              tp.id AS tp_id,
              a.nachname, a.vorname,
              a.wohnort, a.plz, a.ort,
              sc.points, sc.soli,
              sc.created_at, sc.updated_at
            FROM tournament_scores sc
            JOIN tournament_participants tp ON tp.id=sc.tp_id
            JOIN addresses a ON a.id=tp.address_id
            LEFT JOIN tournament_seats s
              ON s.tournament_id=sc.tournament_id
             AND s.round_no=sc.round_no
             AND s.tp_id=sc.tp_id
            WHERE sc.tournament_id=?
            ORDER BY sc.round_no ASC, sc.table_no ASC,
                     CASE COALESCE(s.seat,'') WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END,
                     tp.player_no ASC
            """,
            (tournament_id,),
        )

        scores_all_rows: list[list[Any]] = []
        scores_all_rows.append(
            [
                "runde",
                "tisch",
                "sitz",
                "nr",
                "tp_id",
                "nachname",
                "vorname",
                "wohnort",
                "plz",
                "ort",
                "punkte",
                "soli",
                "created_at",
                "updated_at",
            ]
        )
        for r in scores_all:
            scores_all_rows.append(
                [
                    int(r["round_no"]),
                    int(r["table_no"]),
                    r["seat"] or "",
                    int(r["player_no"]) if r["player_no"] is not None else "",
                    int(r["tp_id"]),
                    r["nachname"],
                    r["vorname"],
                    r["wohnort"],
                    r["plz"],
                    r["ort"],
                    int(r["points"]),
                    int(r["soli"]),
                    r["created_at"],
                    r["updated_at"],
                ]
            )

        # -------------------------
        # 05) Pivot-Vorlage (NEU) - Long Format für Excel Pivot
        #     (jede Zeile = ein Score; perfekte Datenbasis für Pivot)
        # -------------------------
        pivot_rows: list[list[Any]] = []
        pivot_rows.append(
            [
                "turnier_id",
                "turnier_titel",
                "event_date",
                "start_time",
                "marker",
                "runde",
                "tisch",
                "sitz",
                "nr",
                "nachname",
                "vorname",
                "wohnort",
                "plz",
                "ort",
                "punkte",
                "soli",
            ]
        )
        for r in scores_all:
            pivot_rows.append(
                [
                    tournament_id,
                    t.get("title", ""),
                    t.get("event_date", ""),
                    t.get("start_time", ""),
                    t.get("marker", "") or "",
                    int(r["round_no"]),
                    int(r["table_no"]),
                    r["seat"] or "",
                    int(r["player_no"]) if r["player_no"] is not None else "",
                    r["nachname"],
                    r["vorname"],
                    r["wohnort"],
                    r["plz"],
                    r["ort"],
                    int(r["points"]),
                    int(r["soli"]),
                ]
            )

        # -------------------------
        # README.txt (Metadaten + Kurzauswertung)
        # -------------------------
        scores_count_row = db.one(con, "SELECT COUNT(*) AS c FROM tournament_scores WHERE tournament_id=?", (tournament_id,))
        scores_count = int(scores_count_row["c"] or 0) if scores_count_row else 0
        expected_scores = participants_count * rounds_count

        winner = None
        if overall_q:
            w = overall_q[0]
            winner = {
                "nr": int(w["player_no"]),
                "nachname": w["nachname"],
                "vorname": w["vorname"],
                "wohnort": w["wohnort"],
                "points": int(w["points"]),
                "soli": int(w["soli"]),
            }

        total_points = sum(int(r["points"]) for r in overall_q) if overall_q else 0
        total_soli = sum(int(r["soli"]) for r in overall_q) if overall_q else 0
        avg_points = (total_points / participants_count) if participants_count else 0.0
        avg_soli = (total_soli / participants_count) if participants_count else 0.0

        ts_export = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        readme_lines: list[str] = []
        readme_lines.append("Schafkopfturnier Export (ZIP)")
        readme_lines.append("=" * 30)
        readme_lines.append("")
        readme_lines.append("Metadaten")
        readme_lines.append("-" * 30)
        readme_lines.append(f"Turnier-ID:       {tournament_id}")
        readme_lines.append(f"Titel:            {t.get('title', '')}")
        readme_lines.append(f"Datum:            {t.get('event_date', '')}")
        readme_lines.append(f"Beginn:           {t.get('start_time', '')}")
        readme_lines.append(f"Ort/Location:     {t.get('location', '') or '-'}")
        readme_lines.append(f"Organisator:      {t.get('organizer', '') or '-'}")
        readme_lines.append(f"Marker:           {t.get('marker', '') or '-'}")
        if "created_at" in t:
            readme_lines.append(f"created_at:       {t.get('created_at') or '-'}")
        if "updated_at" in t:
            readme_lines.append(f"updated_at:       {t.get('updated_at') or '-'}")
        if "closed_at" in t:
            readme_lines.append(f"closed_at:        {t.get('closed_at') or '-'}")
        readme_lines.append(f"Export-Zeit:      {ts_export}")
        readme_lines.append("")
        readme_lines.append("Kurz-Auswertung")
        readme_lines.append("-" * 30)
        readme_lines.append(f"Teilnehmer:       {participants_count}")
        readme_lines.append(f"Runden:           {rounds_count} ({', '.join(str(x) for x in round_nos) if round_nos else '-'})")
        readme_lines.append(f"Scores:           {scores_count} / {expected_scores} (erwartet=Teilnehmer*Runden)")
        readme_lines.append(f"Summe Punkte:     {total_points} (Ø {avg_points:.2f} pro Teilnehmer)")
        readme_lines.append(f"Summe Soli:       {total_soli} (Ø {avg_soli:.2f} pro Teilnehmer)")
        if winner:
            readme_lines.append(
                "Sieger:           "
                f"#{winner['nr']} {winner['nachname']} {winner['vorname']} ({winner['wohnort']}) "
                f"– {winner['points']} Punkte, {winner['soli']} Soli"
            )
        else:
            readme_lines.append("Sieger:           - (keine Daten)")
        readme_lines.append("")
        readme_lines.append("Inhalt des ZIP")
        readme_lines.append("-" * 30)
        readme_lines.append("01_gesamtwertung.csv")
        readme_lines.append("02_teilnehmer_anmeldung.csv")
        readme_lines.append("03_gesamtuebersicht.csv           (breite Tabelle: Gesamt + je Runde Platz/Punkte/Soli/Tisch)")
        readme_lines.append("04_scores_komplett.csv            (alle Einzelscores)")
        readme_lines.append("05_pivot_vorlage_scores.csv       (Long-Format für Excel Pivot)")
        readme_lines.append("rundenwertung/Rxx.csv             (Rundenranglisten)")
        readme_lines.append("sitzplan/Rxx.csv                  (Sitz-/Spielplan je Runde)")
        readme_lines.append("tische/Rxx_Tyy.csv                (Einzel-Scores je Tisch)")
        readme_lines.append("")
        readme_text = "\n".join(readme_lines) + "\n"

        # -------------------------
        # ZIP bauen
        # -------------------------
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
            # README
            _zip_add_text(z, "README.txt", readme_text)

            # Basisdateien
            _zip_add_csv(z, "01_gesamtwertung.csv", overall_rows)
            _zip_add_csv(z, "02_teilnehmer_anmeldung.csv", part_rows)
            _zip_add_csv(z, "03_gesamtuebersicht.csv", overview_rows)

            # ✅ NEU
            _zip_add_csv(z, "04_scores_komplett.csv", scores_all_rows)
            _zip_add_csv(z, "05_pivot_vorlage_scores.csv", pivot_rows)

            # -------------------------
            # 4) Rundenwertung je Runde (mit Platzierung/Ties)
            # -------------------------
            for rn in round_nos:
                score_rows = db.q(
                    con,
                    """
                    SELECT
                        a.nachname, a.vorname, a.wohnort,
                        tp.player_no,
                        sc.tp_id,
                        sc.points, sc.soli,
                        sc.table_no,
                        COALESCE(s.seat, '') AS seat
                    FROM tournament_scores sc
                    JOIN tournament_participants tp ON tp.id=sc.tp_id
                    JOIN addresses a ON a.id=tp.address_id
                    LEFT JOIN tournament_seats s
                      ON s.tournament_id=sc.tournament_id
                     AND s.round_no=sc.round_no
                     AND s.tp_id=sc.tp_id
                    WHERE sc.tournament_id=? AND sc.round_no=?
                    ORDER BY
                        sc.points DESC,
                        sc.soli DESC,
                        a.nachname COLLATE NOCASE ASC,
                        a.vorname COLLATE NOCASE ASC,
                        tp.player_no ASC
                    """,
                    (tournament_id, rn),
                )

                to_rank = [{"tp_id": int(r["tp_id"]), "points": int(r["points"]), "soli": int(r["soli"])} for r in score_rows]
                place_map = _rank_places(to_rank, key_fields=("points", "soli"))

                out_rows: list[list[Any]] = []
                out_rows.append(["platz", "nr", "nachname", "vorname", "wohnort", "tisch", "sitz", "punkte", "soli"])

                for r in score_rows:
                    tp_id = int(r["tp_id"])
                    out_rows.append(
                        [
                            int(place_map.get(tp_id, 0) or 0),
                            int(r["player_no"]),
                            r["nachname"],
                            r["vorname"],
                            r["wohnort"],
                            int(r["table_no"]),
                            r["seat"] or "",
                            int(r["points"]),
                            int(r["soli"]),
                        ]
                    )

                _zip_add_csv(z, f"rundenwertung/R{rn:02d}.csv", out_rows)

            # -------------------------
            # 5) Sitz-/Spielplan je Runde
            # -------------------------
            for rn in round_nos:
                seats = db.q(
                    con,
                    """
                    SELECT
                        s.round_no,
                        s.table_no,
                        s.seat,
                        tp.player_no,
                        a.nachname, a.vorname, a.wohnort
                    FROM tournament_seats s
                    JOIN tournament_participants tp ON tp.id=s.tp_id
                    JOIN addresses a ON a.id=tp.address_id
                    WHERE s.tournament_id=? AND s.round_no=?
                    ORDER BY
                        s.table_no ASC,
                        CASE s.seat WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END,
                        tp.player_no ASC
                    """,
                    (tournament_id, rn),
                )
                sp_rows: list[list[Any]] = []
                sp_rows.append(["runde", "tisch", "sitz", "nr", "nachname", "vorname", "wohnort"])
                for r in seats:
                    sp_rows.append(
                        [
                            int(r["round_no"]),
                            int(r["table_no"]),
                            r["seat"],
                            int(r["player_no"]),
                            r["nachname"],
                            r["vorname"],
                            r["wohnort"],
                        ]
                    )
                _zip_add_csv(z, f"sitzplan/R{rn:02d}.csv", sp_rows)

            # -------------------------
            # 6) Einzel-Scores pro Tisch -> eine CSV pro Tisch
            # -------------------------
            table_pairs = db.q(
                con,
                """
                SELECT DISTINCT round_no, table_no
                FROM tournament_seats
                WHERE tournament_id=?
                ORDER BY round_no ASC, table_no ASC
                """,
                (tournament_id,),
            )

            for rtp in table_pairs:
                rn = int(rtp["round_no"])
                tn = int(rtp["table_no"])

                rows = db.q(
                    con,
                    """
                    SELECT
                        s.seat,
                        tp.player_no,
                        a.nachname, a.vorname, a.wohnort,
                        sc.points, sc.soli
                    FROM tournament_seats s
                    JOIN tournament_participants tp ON tp.id=s.tp_id
                    JOIN addresses a ON a.id=tp.address_id
                    LEFT JOIN tournament_scores sc
                      ON sc.tournament_id=s.tournament_id
                     AND sc.round_no=s.round_no
                     AND sc.tp_id=s.tp_id
                    WHERE s.tournament_id=? AND s.round_no=? AND s.table_no=?
                    ORDER BY CASE s.seat WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END
                    """,
                    (tournament_id, rn, tn),
                )

                t_rows: list[list[Any]] = []
                t_rows.append(["runde", "tisch", "sitz", "nr", "nachname", "vorname", "wohnort", "punkte", "soli"])

                points_sum = 0
                points_count = 0

                for rr in rows:
                    pts = rr["points"]
                    sol = rr["soli"]
                    if pts is not None:
                        try:
                            points_sum += int(pts)
                            points_count += 1
                        except Exception:
                            pass

                    t_rows.append(
                        [
                            rn,
                            tn,
                            rr["seat"],
                            int(rr["player_no"]),
                            rr["nachname"],
                            rr["vorname"],
                            rr["wohnort"],
                            "" if pts is None else int(pts),
                            "" if sol is None else int(sol),
                        ]
                    )

                # Prüfinfos als Footer
                t_rows.append([])
                t_rows.append(["check", "punkte_eingetragen", points_count, "summe_punkte", points_sum, "soll", 0])

                _zip_add_csv(z, f"tische/R{rn:02d}_T{tn:02d}.csv", t_rows)

        data = mem.getvalue()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    title_safe = _safe_filename(str(t.get("title") or "turnier"))
    filename = f"skt-export-{tournament_id}-{title_safe}-{ts}.zip"

    return Response(
        data,
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )