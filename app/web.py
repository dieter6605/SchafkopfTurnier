# app/web.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from flask import Flask

from . import db
from .routes.home import bp as home_bp
from .routes.addresses import bp as addresses_bp
from .routes.tournaments import bp as tournaments_bp
from .routes.api import bp as api_bp


def create_app(*, db_path: Path, backup_dir: Optional[Path] = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = "dev-secret-change-me"

    db.init_db(db_path)

    app.config["SKT_DB_PATH"] = str(db_path)
    app.config["SKT_BACKUP_DIR"] = str(backup_dir) if backup_dir else ""

    # -------------------------------------------------------------------------
    # Globales Standard-Logo (liegt unter app/static/branding/)
    # - Einmal hier konfigurieren, dann in allen Templates verfügbar.
    # -------------------------------------------------------------------------
    app.config["SKT_SITE_LOGO"] = "branding/logo.png"  # z.B. "branding/sfb-wappen.jpeg"

    @app.context_processor
    def inject_branding():
        return {"site_logo": app.config.get("SKT_SITE_LOGO", "")}

    app.register_blueprint(home_bp)
    app.register_blueprint(addresses_bp)
    app.register_blueprint(tournaments_bp)
    app.register_blueprint(api_bp)

    return app