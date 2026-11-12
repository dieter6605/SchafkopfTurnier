# app/web.py
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    request,
    abort,
    render_template,
    url_for,
    g,
)
from werkzeug.routing import BuildError

from . import db
from .maintenance import guard_maintenance_forms, local_session_key, maintenance_csrf_token, maintenance_enabled
from .routes.home import bp as home_bp
from .routes.addresses import bp as addresses_bp
from .routes.tournaments import bp as tournaments_bp
from .routes.api import bp as api_bp
from .routes.help import bp as help_bp


def create_app(*, db_path: Path, backup_dir: Optional[Path] = None) -> Flask:
    app = Flask(__name__)
    app.config["SKT_DEV_MODE"] = (os.environ.get("SKT_DEV_MODE", "").strip() == "1")
    app.secret_key = local_session_key(Path(db_path).parent)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.before_request(guard_maintenance_forms)
    app.add_template_global(maintenance_csrf_token)

    db.init_db(db_path)

    app.config["SKT_DB_PATH"] = str(db_path)
    app.config["SKT_BACKUP_DIR"] = str(backup_dir) if backup_dir else ""

    # -------------------------------------------------------------------------
    # Branding
    # -------------------------------------------------------------------------
    app.config["SKT_HOME_IMAGE"] = "branding/image.png"
    app.config["SKT_PRINT_LOGO"] = "branding/logo.png"

    @app.context_processor
    def inject_branding():
        return {
            "home_image": app.config.get("SKT_HOME_IMAGE", ""),
            "print_logo": app.config.get("SKT_PRINT_LOGO", ""),
            "dev_mode": maintenance_enabled(),
            "skt_debug": bool(app.debug),
        }

    # -------------------------------------------------------------------------
    # Jinja helper filter
    # -------------------------------------------------------------------------
    @app.template_filter("combine")
    def _jinja_combine(a, b):
        a = a or {}
        b = b or {}
        try:
            return {**dict(a), **dict(b)}
        except Exception:
            out = {}
            try:
                out.update(a)
            except Exception:
                pass
            try:
                out.update(b)
            except Exception:
                pass
            return out

    # -------------------------------------------------------------------------
    # ✅ ZENTRALE SERVERSEITIGE ABSICHERUNG FÜR ABGESCHLOSSENE TURNIERE
    # -------------------------------------------------------------------------
    def _is_write_method() -> bool:
        return request.method in ("POST", "PUT", "PATCH", "DELETE")

    def _is_safe_tournament_endpoint(endpoint: str) -> bool:
        SAFE = (
            "tournaments.tournament_export_zip",
            "tournaments.tournaments_diagnostics",
            "tournaments.tournament_detail",
            "tournaments.tournament_round",
            "tournaments.tournament_standings",
            "tournaments.tournament_reopen",  # ✅ neu
        )
        return endpoint in SAFE

    def _tournament_is_closed(tournament_id: int) -> bool:
        """
        Robust gegen Alt-DBs ohne closed_at.
        Darf NIE crashen – Guard ist defensiv.
        """
        db_path_str = app.config.get("SKT_DB_PATH")
        if not db_path_str:
            return False

        con = sqlite3.connect(db_path_str)
        try:
            con.row_factory = sqlite3.Row

            cols = [r["name"] for r in con.execute("PRAGMA table_info(tournaments);").fetchall()]
            if "closed_at" not in cols:
                return False

            row = con.execute(
                "SELECT closed_at FROM tournaments WHERE id = ?",
                (tournament_id,),
            ).fetchone()

            if not row:
                return False

            val = row["closed_at"]
            if val is None:
                return False

            s = str(val).strip()
            if not s:
                return False

            # Defensive: falls irgendwo "None"/"null" als Text gelandet ist
            if s.lower() in ("none", "null"):
                return False

            return True

        except Exception:
            return False
        finally:
            con.close()

    # -------------------------------------------------------------------------
    # Globaler Turnier-Status für Layout/Navbar (Badge + JS Flag)
    # -------------------------------------------------------------------------
    @app.before_request
    def _inject_tournament_status_into_g():
        """
        Wenn wir auf einer tournaments.* Route sind und tournament_id haben,
        dann berechnen wir is_closed einmal zentral und legen es in g ab.
        """
        g.skt_tournament_id = None
        g.skt_tournament_closed = False

        ep = request.endpoint or ""
        if not ep.startswith("tournaments."):
            return

        tid = request.view_args.get("tournament_id") if request.view_args else None
        if not tid:
            return

        try:
            tid_int = int(tid)
        except Exception:
            return

        g.skt_tournament_id = tid_int
        g.skt_tournament_closed = bool(_tournament_is_closed(tid_int))

    @app.context_processor
    def inject_tournament_status():
        return {
            "skt_tournament_id": getattr(g, "skt_tournament_id", None),
            "skt_tournament_closed": getattr(g, "skt_tournament_closed", False),
        }

    @app.before_request
    def guard_closed_tournaments():
        ep = request.endpoint
        if not ep:
            return

        # Mini-Logzeile (nur Debug)
        if app.debug and ep.startswith("tournaments."):
            app.logger.debug(
                "guard_closed_tournaments: method=%s ep=%s path=%s view_args=%s",
                request.method,
                ep,
                request.path,
                dict(request.view_args or {}),
            )

        # Nur Turnier-Routen
        if not ep.startswith("tournaments."):
            return

        # Nur schreibende Methoden
        if not _is_write_method():
            return

        # Erlaubte Endpunkte
        if _is_safe_tournament_endpoint(ep):
            return

        # tournament_id aus URL
        tid = request.view_args.get("tournament_id") if request.view_args else None
        if not tid:
            return

        try:
            tid = int(tid)
        except Exception:
            abort(400, "Ungültige Turnier-ID")

        if _tournament_is_closed(tid):
            abort(
                409,
                description="Dieses Turnier ist abgeschlossen und darf nicht mehr geändert werden.",
            )

    # -------------------------------------------------------------------------
    # 🎨 UX: 409 (Turnier abgeschlossen) – Browser: Flash + Redirect, API: JSON
    # -------------------------------------------------------------------------
    @app.errorhandler(409)
    def handle_409(err):
        msg = getattr(err, "description", None) or "Konflikt – Aktion nicht möglich."

        # API / Fetch: immer JSON
        if request.accept_mimetypes.best == "application/json" or request.is_json:
            return {"error": msg}, 409

        # --- Browser UX: Flash + Redirect statt Fehlerseite ---
        from flask import flash, redirect

        # Sichere Home-URL-Auflösung
        def _safe_home_url() -> str:
            for ep in ("home.home", "home.index"):
                try:
                    return url_for(ep)
                except BuildError:
                    continue
            return "/"

        home_url = _safe_home_url()

        # Referer nur verwenden, wenn er "lokal" ist (kein externer Redirect)
        ref = request.headers.get("Referer") or ""
        if ref.startswith(request.host_url):
            back_url = ref
        else:
            back_url = ""

        flash(msg, "warning")

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            return redirect(back_url or home_url, code=303)

        return redirect(back_url or home_url, code=302)

    # -------------------------------------------------------------------------
    # 🎨 UX: hübsche Fehlerseiten
    # -------------------------------------------------------------------------
    def _safe_home_url() -> str:
        for ep in ("home.home", "home.index"):
            try:
                return url_for(ep)
            except BuildError:
                continue
        return "/"

    @app.errorhandler(403)
    def handle_403(err):
        msg = getattr(err, "description", None) or "Zugriff nicht erlaubt."
        if request.accept_mimetypes.best == "application/json" or request.is_json:
            return {"error": msg}, 403

        return (
            render_template(
                "errors/403.html",
                message=msg,
                path=request.path,
                home_url=_safe_home_url(),
                back_url=request.referrer if (request.referrer or "").startswith(request.host_url) else _safe_home_url(),
            ),
            403,
        )

    @app.errorhandler(404)
    def handle_404(err):
        msg = "Die angeforderte Seite wurde nicht gefunden."
        if request.accept_mimetypes.best == "application/json" or request.is_json:
            return {"error": msg}, 404

        return (
            render_template(
                "errors/404.html",
                message=msg,
                path=request.path,
                home_url=_safe_home_url(),
            ),
            404,
        )

    @app.errorhandler(500)
    def handle_500(err):
        msg = "Ein interner Fehler ist aufgetreten."
        if request.accept_mimetypes.best == "application/json" or request.is_json:
            return {"error": msg}, 500

        return (
            render_template(
                "errors/500.html",
                message=msg,
                home_url=_safe_home_url(),
            ),
            500,
        )

    # -------------------------------------------------------------------------
    # Blueprints
    # -------------------------------------------------------------------------
    app.register_blueprint(home_bp)
    app.register_blueprint(addresses_bp)
    app.register_blueprint(tournaments_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(help_bp)

    return app