# app/web.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from flask import Flask

from . import db
from .routes.home import bp as home_bp


def create_app(*, db_path: Path, backup_dir: Optional[Path] = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = "dev-secret-change-me"  # später via ENV

    # DB init
    db.init_db(db_path)

    # Pfade in app.config
    app.config["SKT_DB_PATH"] = str(db_path)
    app.config["SKT_BACKUP_DIR"] = str(backup_dir) if backup_dir else ""

    # Blueprints
    app.register_blueprint(home_bp)

    return app