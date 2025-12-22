# app/routes/api.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import db

bp = Blueprint("api", __name__)


@bp.get("/api/wohnorte")
def api_wohnorte():
    """
    JSON endpoint für Wohnort-Autocomplete.
    Erwartet: /api/wohnorte?q=...
    Antwort: [{wohnort, plz, ort}, ...]
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])

    like = f"%{q}%"

    with db.connect() as con:
        rows = db.q(
            con,
            """
            SELECT wohnort, plz, ort
            FROM wohnorte
            WHERE wohnort LIKE ?
            ORDER BY wohnort COLLATE NOCASE
            LIMIT 30
            """,
            (like,),
        )

    out = [{"wohnort": r["wohnort"], "plz": r["plz"], "ort": r["ort"]} for r in rows]
    return jsonify(out)