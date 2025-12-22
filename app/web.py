# app/web.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from flask import Flask

from . import db
from .routes.tournaments import bp as tournaments_bp


def create_app(*, db_path: Path, backup_dir: Optional[Path] = None) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev-local-secret"  # offline lokal; später ggf. per ENV setzen

    # Pfade
    db.set_db_path(db_path)
    app.config["SKT_DB_PATH"] = str(db_path)
    if backup_dir is not None:
        app.config["SKT_BACKUP_DIR"] = str(backup_dir)

    # DB initialisieren (Schema erzeugen / migrieren)
    db.init_db(db_path)

    # Blueprints
    app.register_blueprint(tournaments_bp)

    # Startseite
    @app.get("/")
    def index():
        from flask import render_template

        return render_template("index.html")

    return app