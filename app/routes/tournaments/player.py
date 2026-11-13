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

from itsdangerous import BadData, URLSafeSerializer

from ... import db
from ...scoring import first_score_issue, overall_standings, rank_places
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


def _card_signer():
    return URLSafeSerializer(current_app.secret_key, salt="wlan-spielerkarte-v1")


def _card_url(tournament_id, tp_id, address_id, generation):
    origin = current_app.config["WLAN_ORIGIN"]
    if origin:
        token = _card_signer().dumps([tournament_id, tp_id, address_id, generation])
        return origin + url_for("tournaments.wlan_player_card", token=token)
    return url_for("tournaments.tournament_player_view", tournament_id=tournament_id, tp_id=tp_id, _external=True)


@bp.get("/karte/<token>")
def wlan_player_card(token):
    if not current_app.config["WLAN_ORIGIN"]:
        abort(404)
    try:
        tournament_id, tp_id, address_id, generation = _card_signer().loads(token)
    except BadData:
        abort(404)
    return _render_player_card(tournament_id, tp_id, pdf=False, identity=(address_id, generation))


def _render_player_card(tournament_id: int, tp_id: int, *, pdf: bool, identity: tuple[int, str] | None = None):
    with closing(db.connect()) as con:
        con.execute("BEGIN")
        t = _get_tournament(con, tournament_id)
        if not t:
            if identity is not None:
                abort(404)
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        # Spieler + Adresse
        p = db.one(
            con,
            """
            SELECT
                tp.id AS tp_id,
                tp.player_no,
                tp.display_name, tp.address_id,
                a.nachname, a.vorname, a.wohnort
            FROM tournament_participants tp
            JOIN addresses a ON a.id = tp.address_id
            WHERE tp.tournament_id=? AND tp.id=?
            """,
            (int(tournament_id), int(tp_id)),
        )
        generation = db.wlan_generation(con) if current_app.config["WLAN_ORIGIN"] else None
        if identity is not None and (not p or (p["address_id"], generation) != identity):
            abort(404)
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

        standings = overall_standings(con, tournament_id, sort_by_town=True)
        places = rank_places(standings)
        position = next(i for i, row in enumerate(standings) if row["tp_id"] == tp_id)
        current = standings[position]
        place = places.get(tp_id)
        score_issue = first_score_issue(con, tournament_id)
        medal = _medal_for_place(place) if not score_issue else ""
        prev_tp_id = standings[position - 1]["tp_id"] if position > 0 else None
        next_tp_id = standings[position + 1]["tp_id"] if position + 1 < len(standings) else None

        timeline = []
        for rn in round_numbers:
            score = by_round.get(rn)
            seat = seat_by_round.get(rn)
            timeline.append({
                "round_no": rn,
                "table_no": seat["table_no"] if seat else (score["table_no"] if score else None),
                "seat": seat["seat"] if seat else None,
                "points": score["points"] if score else None,
                "soli": score["soli"] if score else None,
                "has_score": score is not None,
            })

        best = worst = None
        points_curve = cumulative_curve = ""
        if current["points"] is not None:
            played_rounds = [entry for entry in timeline if entry["has_score"]]
            if played_rounds:
                best = max(played_rounds, key=lambda entry: entry["points"])
                worst = min(played_rounds, key=lambda entry: entry["points"])
            point_series = [entry["points"] for entry in timeline]
            cumulative = []
            total = 0
            for points in point_series:
                if points is not None:
                    total += points
                cumulative.append(total)
            points_curve = _sparkline_points(point_series)
            cumulative_curve = _sparkline_points(cumulative)

    player_url = _card_url(tournament_id, tp_id, p["address_id"], generation)
    if pdf or identity is not None:
        qr_url = "data:image/png;base64," + b64encode(_qr_png(player_url)).decode("ascii")
    else:
        qr_url = url_for("tournaments.tournament_player_qr_png", tournament_id=tournament_id, tp_id=tp_id)

    return render_template(
        "tournament_player.html", t=t, p=p, place=place, medal=medal,
        score_issue=score_issue, sum_points=current["points"], sum_soli=current["soli"], best=best, worst=worst,
        timeline=timeline, now=_now_local_iso(), prev_tp_id=prev_tp_id, next_tp_id=next_tp_id,
        points_curve=points_curve, cumulative_curve=cumulative_curve, played=len(scores), rounds_total=len(round_numbers),
        player_url=player_url, qr_url=qr_url, pdf_mode=pdf, personal_card=identity is not None,
        pdf_url=url_for("tournaments.tournament_player_pdf", tournament_id=tournament_id, tp_id=tp_id),
    )


@bp.get("/tournaments/<int:tournament_id>/players/<int:tp_id>")
def tournament_player_view(tournament_id: int, tp_id: int):
    return _render_player_card(tournament_id, tp_id, pdf=False)


@bp.get("/tournaments/<int:tournament_id>/players/<int:tp_id>/qr.png")
def tournament_player_qr_png(tournament_id: int, tp_id: int):
    with closing(db.connect()) as con:
        con.execute("BEGIN")
        generation = db.wlan_generation(con) if current_app.config["WLAN_ORIGIN"] else None
        p = db.one(con, "SELECT address_id FROM tournament_participants WHERE tournament_id=? AND id=?",
                   (tournament_id, tp_id))
    if not p:
        abort(404)
    target = _card_url(tournament_id, tp_id, p["address_id"], generation)
    return send_file(BytesIO(_qr_png(target)), mimetype="image/png", max_age=0)


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
