# app/routes/home.py
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, url_for

from .. import db

bp = Blueprint("home", __name__)


@bp.get("/")
def home():
    return render_template("home.html")


@bp.post("/backup")
def backup():
    bdir = current_app.config.get("SKT_BACKUP_DIR") or ""
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.home"))

    target = db.backup_db(Path(bdir))
    flash(f"Backup erstellt: {target.name}", "ok")
    return redirect(url_for("home.home"))