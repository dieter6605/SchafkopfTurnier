"""Freigaben für Helfer und persönliche Spielerkarten im Vereins-WLAN."""
from contextlib import closing
import hmac
from ipaddress import IPv4Address, IPv4Network
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for

from . import db
from .maintenance import local_request

bp = Blueprint("wlan", __name__)
PRIVATE_NETWORKS = tuple(IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
RESULT_ENDPOINTS = {
    "tournaments.tournament_round_results_overview",
    "tournaments.tournament_round_results_table",
    "tournaments.tournament_round_results_table_post",
}
PUBLIC_ASSETS = {
    "branding/favicon.ico", "branding/apple-touch-icon.png", "branding/logo.png", "branding/turnierlogo.png",
    "vendor/bootstrap/bootstrap.min.css", "vendor/bootstrap/bootstrap.bundle.min.js",
    "js/skt_ux.js",
}


def wlan_origin(address, port):
    if not address:
        return None
    try:
        ip = IPv4Address(address)
        if any(ip in net for net in PRIVATE_NETWORKS) and 1 <= port <= 65535:
            return f"http://{ip}:{port}" if port != 80 else f"http://{ip}"
    except ValueError:
        pass
    raise ValueError("Für SKT_WLAN_IP die private IPv4-Adresse des Turnierrechners und einen gültigen Port angeben.")


def helper_code(tournament_id, generation):
    # Ein eigener Code je Turnier und Serverstart. Der Code steht nur auf der lokalen Turnierseite.
    digest = hmac.digest(current_app.config["WLAN_KEY"], f"code:{generation}:{tournament_id}".encode(), "sha256").hex()
    return digest[:6] + "-" + digest[6:12]


def setup_wlan(app, address, port):
    app.config["WLAN_ORIGIN"] = wlan_origin(address, port)
    if app.config["WLAN_ORIGIN"]:
        app.config["WLAN_KEY"] = secrets.token_bytes(32)
        app.config["WLAN_SESSION_ID"] = secrets.token_urlsafe(24)
        app.register_blueprint(bp)
        app.add_template_global(helper_code)
    app.before_request(guard_access)

    @app.after_request
    def protect_private_responses(response):
        if app.config["WLAN_ORIGIN"] and request.endpoint != "static":
            response.cache_control.no_store = True
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
        return response


def guard_access():
    g.wlan_request = not local_request()
    if not g.wlan_request:
        return
    origin = current_app.config["WLAN_ORIGIN"]
    if not origin or request.host != urlsplit(origin).netloc:
        abort(403, description="Die Turnierverwaltung ist nur am Turnierrechner zugänglich.")
    endpoint = request.endpoint
    if endpoint == "static" and request.view_args["filename"] in PUBLIC_ASSETS:
        return
    if endpoint == "home.home":
        return render_template("wlan.html")
    if endpoint in {"wlan.helper", "wlan.logout", "tournaments.wlan_player_card"}:
        return
    if endpoint in RESULT_ENDPOINTS:
        tournament_id = request.view_args["tournament_id"]
        if helper_logged_in(tournament_id):
            return  # Die Runde prüft Turnierstatus und Datenstand in ihrer eigenen Transaktion.
        if request.method in {"GET", "HEAD"}:
            return redirect(url_for("wlan.helper", tournament_id=tournament_id))
    abort(403, description="Diese Funktion ist der Turnierleitung vorbehalten.")


def check_result_access(con, tournament):
    if g.wlan_request and (not tournament or (tournament["closed_at"] or "").strip()
                           or session.get("helper_generation") != db.wlan_generation(con)):
        abort(403, description="Die Helferfreigabe für diesen Datenstand ist beendet.")


def helper_logged_in(tournament_id):
    return (session.get("helper_tournament") == tournament_id
            and session.get("helper_session") == current_app.config["WLAN_SESSION_ID"])


@bp.route("/helfer/<int:tournament_id>", methods=["GET", "POST"])
def helper(tournament_id):
    with closing(db.connect()) as con:
        con.execute("BEGIN")
        generation = db.wlan_generation(con)
        if request.method == "POST":
            code = request.form.get("code", "").strip().lower()
            if not code.isascii() or not secrets.compare_digest(code, helper_code(tournament_id, generation)):
                return render_template("wlan.html", tournament_id=tournament_id,
                                       error="Der Helfercode stimmt nicht. Bitte den aktuellen Code eingeben."), 403
            session.clear()
            session["helper_tournament"] = tournament_id
            session["helper_session"] = current_app.config["WLAN_SESSION_ID"]
            session["helper_generation"] = generation
            return redirect(url_for("wlan.helper", tournament_id=tournament_id), code=303)
        if not helper_logged_in(tournament_id) or session.get("helper_generation") != generation:
            return render_template("wlan.html", tournament_id=tournament_id)
        tournament = db.one(con, "SELECT title FROM tournaments WHERE id=? AND trim(coalesce(closed_at,''))=''",
                            (tournament_id,))
        if not tournament:
            abort(403, description="Dieses Turnier ist für die Ergebniseingabe geschlossen.")
        rounds = db.q(con, "SELECT DISTINCT round_no FROM tournament_seats WHERE tournament_id=? ORDER BY round_no DESC",
                      (tournament_id,))
    return render_template("wlan.html", tournament_id=tournament_id, tournament=tournament, rounds=rounds)


@bp.post("/helfer/abmelden")
def logout():
    tournament_id = session.get("helper_tournament")
    session.clear()
    if tournament_id:
        return redirect(url_for("wlan.helper", tournament_id=tournament_id), code=303)
    return redirect("/", code=303)
