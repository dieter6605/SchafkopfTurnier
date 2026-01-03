# app/routes/api.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import db

# ✅ NEU: wir nutzen vorhandene Turnier-/Adressbuch-Helfer
from .tournaments.helpers import _get_tournament, _is_closed, _search_addresses, _to_int, _display_name

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


# -----------------------------------------------------------------------------
# ✅ NEU: Swap-Suche fürs Modal (ohne vorherige Adressbuchsuche)
# URL: /api/tournaments/<tournament_id>/swap-search?q=...&limit=30
# Antwort: {ok:true, items:[{id,label,in_tournament,player_no,tp_id,...}, ...]}
# -----------------------------------------------------------------------------
def _is_address_swap_blocked_status(status: str | None) -> bool:
    s = (status or "").strip().lower()
    return s == "gesperrt"


@bp.get("/api/tournaments/<int:tournament_id>/swap-search")
def api_swap_search(tournament_id: int):
    qtxt = (request.args.get("q") or "").strip()
    limit = _to_int(request.args.get("limit"), 25)
    if limit <= 0:
        limit = 25
    if limit > 50:
        limit = 50

    # UI macht eh min. 2 Zeichen – hier defensiv genauso:
    if len(qtxt) < 2:
        return jsonify({"ok": True, "items": []})

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            return jsonify({"ok": False, "error": "Turnier nicht gefunden."}), 404
        if _is_closed(t):
            return jsonify({"ok": False, "error": "Turnier ist abgeschlossen."}), 409

        hits = _search_addresses(con, qtxt)

        # Teilnehmer-Mapping: address_id -> (tp_id, player_no)
        rows = db.q(
            con,
            "SELECT id, address_id, player_no FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,),
        )
        by_aid = {
            int(r["address_id"]): {"tp_id": int(r["id"]), "player_no": int(r["player_no"] or 0)}
            for r in rows
        }

        out = []
        for h in hits:
            aid = int(h["id"])
            status = (h["status"] if "status" in h.keys() else None)

            if _is_address_swap_blocked_status(status):
                continue

            info = by_aid.get(aid)

            # ✅ JS erwartet "label"
            label = _display_name(h)

            out.append(
                {
                    "id": aid,
                    "label": label,
                    "in_tournament": bool(info),
                    "player_no": (info["player_no"] if info else None),
                    "tp_id": (info["tp_id"] if info else None),

                    # optional – kann später nützlich sein
                    "status": str(status or ""),
                    "wohnort": str(h["wohnort"] or ""),
                    "nachname": str(h["nachname"] or ""),
                    "vorname": str(h["vorname"] or ""),
                }
            )

            if len(out) >= limit:
                break

    return jsonify({"ok": True, "items": out})