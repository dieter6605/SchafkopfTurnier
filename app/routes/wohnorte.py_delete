# app/routes/wohnorte.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import db

bp = Blueprint("wohnorte", __name__)


@bp.get("/api/wohnorte")
def api_wohnorte():
    """
    Rückgabe: [{ wohnort, plz, ort }]
    Query: ?q=...
    """
    qtxt = (request.args.get("q") or "").strip()
    limit = 30

    if not qtxt:
        return jsonify([])

    like = f"%{qtxt}%"

    with db.connect() as con:
        rows = db.q(
            con,
            """
            SELECT wohnort, plz, ort
            FROM wohnorte
            WHERE wohnort LIKE ?
            ORDER BY wohnort COLLATE NOCASE
            LIMIT ?
            """,
            (like, limit),
        )

    return jsonify([{"wohnort": r["wohnort"], "plz": r["plz"], "ort": r["ort"]} for r in rows])