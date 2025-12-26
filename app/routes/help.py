# app/routes/help.py
from __future__ import annotations

from pathlib import Path

import markdown
from flask import Blueprint, current_app, flash, redirect, render_template, url_for

bp = Blueprint("help", __name__, url_prefix="/help")


def _docs_dir() -> Path:
    # current_app.root_path -> .../app
    # Projektwurzel -> parent
    return Path(current_app.root_path).parent / "docs"


def _render_md(filename: str, title: str):
    path = _docs_dir() / filename
    if not path.exists():
        flash(f"Dokument nicht gefunden: docs/{filename}", "error")
        return redirect(url_for("home.home"))

    text = path.read_text(encoding="utf-8")

    html = markdown.markdown(
        text,
        extensions=[
            "extra",
            "tables",
            "fenced_code",
            "toc",
        ],
    )

    return render_template(
        "help_readme.html",
        title=title,
        content=html,
    )


@bp.get("/readme")
def readme():
    return _render_md("README.md", "Lies mich")


@bp.get("/anleitung")
def anleitung():
    return _render_md("ANLEITUNG.md", "Anleitung")