"""Bestehende Lesezeichen auf die aktuelle deutschsprachige Hilfe führen."""
from flask import Blueprint, redirect, url_for

bp = Blueprint("help", __name__, url_prefix="/help")


@bp.get("/readme")
def readme():
    return redirect(url_for("home.help_docs", doc="readme"))


@bp.get("/anleitung")
def anleitung():
    return redirect(url_for("home.help_docs", doc="anleitung"))
