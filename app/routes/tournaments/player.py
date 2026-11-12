# app/routes/tournaments/player.py
from __future__ import annotations

from base64 import b64encode
from contextlib import closing
from io import BytesIO
from pathlib import Path

from flask import (
    abort,
    flash,
    redirect,
    render_template,
    url_for,
    send_file,
    current_app,
)

from ... import db
from . import bp
from .helpers import _get_tournament, _now_local_iso


def _sparkline_points(values: list[int | None], *, width: int = 520, height: int = 64, pad: int = 6) -> str:
    """
    Erzeugt eine SVG-Points-String-Liste "x,y x,y ..." für eine Sparkline.
    - None-Werte werden ausgelassen (Linie wird über Lücken verbunden; bewusst schlicht).
    - Wenn <2 Werte vorhanden -> leer.
    """
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""

    vmin = min(vals)
    vmax = max(vals)
    if vmin == vmax:
        # flache Linie mittig
        y = pad + (height - 2 * pad) / 2
        xs = [pad + i * ((width - 2 * pad) / (len(values) - 1)) for i in range(len(values))]
        pts = []
        for i, v in enumerate(values):
            if v is None:
                continue
            pts.append(f"{xs[i]:.2f},{y:.2f}")
        return " ".join(pts)

    def y_of(v: int) -> float:
        # oben = max, unten = min
        return pad + (vmax - v) * (height - 2 * pad) / (vmax - vmin)

    xs = [pad + i * ((width - 2 * pad) / (len(values) - 1)) for i in range(len(values))]
    pts = []
    for i, v in enumerate(values):
        if v is None:
            continue
        pts.append(f"{xs[i]:.2f},{y_of(v):.2f}")
    return " ".join(pts)


def _medal_for_place(place: int | None) -> str:
    if place == 1:
        return "🥇"
    if place == 2:
        return "🥈"
    if place == 3:
        return "🥉"
    return ""


def _qr_png(target: str) -> bytes:
    try:
        import qrcode
        from qrcode.image.pil import PilImage
    except ImportError:
        abort(503, description="Für den QR-Code werden qrcode und Pillow benötigt.")

    stream = BytesIO()
    qrcode.make(target, image_factory=PilImage).save(stream, format="PNG")
    return stream.getvalue()


def _render_player_card(tournament_id: int, tp_id: int, *, pdf: bool):
    with closing(db.connect()) as con:
        con.execute("BEGIN")
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        # Spieler + Adresse
        p = db.one(
            con,
            """
            SELECT
                tp.id AS tp_id,
                tp.player_no,
                tp.display_name,
                a.nachname, a.vorname, a.wohnort
            FROM tournament_participants tp
            JOIN addresses a ON a.id = tp.address_id
            WHERE tp.tournament_id=? AND tp.id=?
            """,
            (int(tournament_id), int(tp_id)),
        )
        if not p:
            flash("Spieler im Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournament_standings_overall", tournament_id=tournament_id))

        # Rundenliste
        rounds = db.q(
            con,
            "SELECT DISTINCT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no ASC",
            (int(tournament_id),),
        )
        round_numbers = [int(r["round_no"]) for r in rounds]

        # Ergebnisse pro Runde
        scores = db.q(
            con,
            """
            SELECT round_no, table_no, points, soli
            FROM tournament_scores
            WHERE tournament_id=? AND tp_id=?
            ORDER BY round_no ASC
            """,
            (int(tournament_id), int(tp_id)),
        )
        by_round = {int(r["round_no"]): r for r in scores}

        # Sitzplatz pro Runde
        seats = db.q(
            con,
            """
            SELECT round_no, table_no, seat
            FROM tournament_seats
            WHERE tournament_id=? AND tp_id=?
            ORDER BY round_no ASC
            """,
            (int(tournament_id), int(tp_id)),
        )
        seat_by_round = {int(r["round_no"]): {"table_no": int(r["table_no"]), "seat": r["seat"]} for r in seats}

        # Verlauf + Summen
        timeline = []
        sum_points = 0
        sum_soli = 0
        best = None
        worst = None

        points_series: list[int | None] = []
        cum_series: list[int] = []

        cum = 0
        played = 0

        for rn in round_numbers:
            sc = by_round.get(rn)
            pts = int(sc["points"]) if sc else None
            sol = int(sc["soli"]) if sc else None

            seatinfo = seat_by_round.get(rn)
            tab_no = seatinfo["table_no"] if seatinfo else (int(sc["table_no"]) if sc else None)
            seat = seatinfo["seat"] if seatinfo else None

            if pts is not None:
                played += 1
                sum_points += pts
                sum_soli += (sol or 0)
                cum += pts

                if best is None or pts > best["points"]:
                    best = {"round_no": rn, "points": pts, "soli": sol or 0}
                if worst is None or pts < worst["points"]:
                    worst = {"round_no": rn, "points": pts, "soli": sol or 0}

            cum_series.append(cum)  # bleibt bei None stabil
            points_series.append(pts)

            timeline.append(
                {
                    "round_no": rn,
                    "table_no": tab_no,
                    "seat": seat,
                    "points": pts,
                    "soli": sol,
                    "has_score": (pts is not None),
                }
            )

        # Ranking (wie Gesamtwertung)
        ranking_rows = db.q(
            con,
            """
            SELECT
                tp.id AS tp_id,
                COALESCE(SUM(sc.points), 0) AS points,
                COALESCE(SUM(sc.soli), 0)   AS soli,
                a.nachname, a.vorname, a.wohnort,
                tp.player_no
            FROM tournament_participants tp
            JOIN addresses a ON a.id = tp.address_id
            LEFT JOIN tournament_scores sc
              ON sc.tournament_id = tp.tournament_id
             AND sc.tp_id = tp.id
            WHERE tp.tournament_id = ?
            GROUP BY tp.id, a.nachname, a.vorname, a.wohnort, tp.player_no
            ORDER BY
                points DESC,
                soli   DESC,
                a.nachname COLLATE NOCASE,
                a.vorname  COLLATE NOCASE,
                a.wohnort  COLLATE NOCASE,
                tp.player_no
            """,
            (int(tournament_id),),
        )

        # Platzlogik + Nachbarspieler
        ordered_tp_ids: list[int] = []
        place: int | None = None

        last_key = None
        cur_place = 0
        idx = 0

        for r in ranking_rows:
            idx += 1
            key = (int(r["points"]), int(r["soli"]))
            if key != last_key:
                cur_place = idx
                last_key = key

            rid = int(r["tp_id"])
            ordered_tp_ids.append(rid)
            if rid == int(tp_id):
                place = cur_place

        prev_tp_id = None
        next_tp_id = None
        if int(tp_id) in ordered_tp_ids:
            pos = ordered_tp_ids.index(int(tp_id))
            if pos > 0:
                prev_tp_id = ordered_tp_ids[pos - 1]
            if pos < len(ordered_tp_ids) - 1:
                next_tp_id = ordered_tp_ids[pos + 1]

        # Sparkline-Daten
        spark = {
            "width": 520,
            "height": 64,
            "pad": 6,
            "points_pts": _sparkline_points(points_series, width=520, height=64, pad=6),
            "points_cum": _sparkline_points([int(x) for x in cum_series], width=520, height=64, pad=6),
            "has_pts": (len([v for v in points_series if v is not None]) >= 2),
            "has_cum": (len(cum_series) >= 2),
        }

        medal = _medal_for_place(place)

    player_url = url_for(
        "tournaments.tournament_player_view", tournament_id=tournament_id, tp_id=tp_id, _external=True,
    )
    if pdf:
        qr_url = "data:image/png;base64," + b64encode(_qr_png(player_url)).decode("ascii")
    else:
        qr_url = url_for("tournaments.tournament_player_qr_png", tournament_id=tournament_id, tp_id=tp_id)

    return render_template(
        "tournament_player.html", t=t, p=p, place=place, medal=medal,
        sum_points=sum_points, sum_soli=sum_soli, best=best, worst=worst,
        timeline=timeline, now=_now_local_iso(), prev_tp_id=prev_tp_id, next_tp_id=next_tp_id,
        spark=spark, played=played, rounds_total=len(round_numbers),
        player_url=player_url, qr_url=qr_url, pdf_mode=pdf,
        pdf_url=url_for("tournaments.tournament_player_pdf", tournament_id=tournament_id, tp_id=tp_id),
    )


@bp.get("/tournaments/<int:tournament_id>/players/<int:tp_id>")
def tournament_player_view(tournament_id: int, tp_id: int):
    return _render_player_card(tournament_id, tp_id, pdf=False)


@bp.get("/tournaments/<int:tournament_id>/players/<int:tp_id>/qr.png")
def tournament_player_qr_png(tournament_id: int, tp_id: int):
    target = url_for("tournaments.tournament_player_view", tournament_id=tournament_id, tp_id=tp_id, _external=True)
    return send_file(BytesIO(_qr_png(target)), mimetype="image/png", max_age=3600)


@bp.get("/tournaments/<int:tournament_id>/players/<int:tp_id>/pdf")
def tournament_player_pdf(tournament_id: int, tp_id: int):
    try:
        from weasyprint import HTML
    except (ImportError, OSError):
        abort(503, description="Für den PDF-Export werden WeasyPrint und seine Systembibliotheken benötigt.")

    card = _render_player_card(tournament_id, tp_id, pdf=True)
    if not isinstance(card, str):
        return card  # Weiterleitung für ein fehlendes Turnier oder eine fehlende Anmeldung.

    # Relative Stylesheets und Logos kommen aus dem lokalen static-Verzeichnis.
    static_root = Path(current_app.static_folder).resolve().as_uri() + "/"
    pdf_bytes = HTML(string=card, base_url=static_root).write_pdf()
    return send_file(
        BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
        download_name=f"spielerkarte-T{tournament_id}-P{tp_id}.pdf",
    )
