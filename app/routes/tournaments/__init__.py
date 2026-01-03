# app/routes/tournaments/__init__.py
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("tournaments", __name__)

# Side-effect imports: registrieren die @bp.get/@bp.post Routen
from . import pages  # noqa: E402,F401
from . import rounds  # noqa: E402,F401
from . import participants  # noqa: E402,F401
from . import results  # noqa: E402,F401
from . import standings  # noqa: E402,F401
from . import export  # noqa: E402,F401