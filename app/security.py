# app/security.py
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Optional

from flask import abort, request


def tournament_is_closed(t: Any) -> bool:
    """
    Robust: akzeptiert dict, sqlite-row, dataclass/objekt.
    closed_at gilt als gesetzt, wenn truthy und nicht leer.
    """
    if t is None:
        return False

    closed_at: Optional[Any] = None

    # dict-like
    try:
        if isinstance(t, dict):
            closed_at = t.get("closed_at")
        else:
            # sqlite Row unterstützt i.d.R. __getitem__
            try:
                closed_at = t["closed_at"]  # type: ignore[index]
            except Exception:
                closed_at = getattr(t, "closed_at", None)
    except Exception:
        closed_at = getattr(t, "closed_at", None)

    # Strings mit Leerzeichen nicht als "offen" werten
    if isinstance(closed_at, str):
        return bool(closed_at.strip())

    return bool(closed_at)


def require_open_tournament(t: Any, *, message: str = "Turnier ist abgeschlossen – Änderung nicht erlaubt.") -> None:
    """
    Harte Sperre für Schreibzugriffe.
    """
    if tournament_is_closed(t):
        abort(403, description=message)


def guard_tournament_not_closed(load_tournament_fn: Callable[..., Any]):
    """
    Decorator:
    - lädt Turnier über load_tournament_fn(**kwargs)
    - sperrt alle Nicht-GET Requests, wenn closed_at gesetzt ist

    Beispiel:
        def _load_tournament(tournament_id: int, **_):
            return db.tournament_get(tournament_id)

        @bp.route("/t/<int:tournament_id>/round/<int:round_no>/draw", methods=["POST"])
        @guard_tournament_not_closed(_load_tournament)
        def tournament_round_draw(tournament_id, round_no):
            ...
    """

    def deco(view: Callable[..., Any]):
        @wraps(view)
        def wrapper(*args, **kwargs):
            t = load_tournament_fn(**kwargs)

            # Nur Lesen erlaubt, sobald geschlossen.
            if request.method != "GET":
                require_open_tournament(t)

            # Optional: Turnier an View durchreichen (falls du das willst)
            # kwargs["_tournament"] = t

            return view(*args, **kwargs)

        return wrapper

    return deco