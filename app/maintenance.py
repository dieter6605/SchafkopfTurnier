"""Sitzungsschlüssel und Formularschutz für die Turnierverwaltung."""
from ipaddress import ip_address
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from flask import abort, current_app, request, session


MAINTENANCE_ENDPOINTS = {
    "tournaments.tournament_reopen",
    "tournaments.tournaments_repair_addresses",
    "tournaments.tournament_repair_addresses",
}


def local_session_key(data_dir: Path) -> bytes:
    key_dir = data_dir / ".skt-session"
    path = key_dir / "key"
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Das vollständige, nicht leere Verzeichnis atomar veröffentlichen.
        # Ein parallel erzeugtes Schlüsselverzeichnis wird nie überschrieben.
        with TemporaryDirectory(dir=data_dir) as tmp:
            pending = Path(tmp) / "session"
            pending.mkdir(mode=0o700)
            key_file = pending / "key"
            key_file.touch(mode=0o600)
            key_file.write_bytes(secrets.token_bytes(32))
            try:
                pending.rename(key_dir)
            except OSError:
                if not path.is_file():
                    raise
        key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError(f"Ungültiger Sitzungsschlüssel in {path}.")
    return key


def _is_loopback(address: str | None) -> bool:
    try:
        ip = ip_address(address or "")
    except ValueError:
        return False
    return ip.is_loopback


def local_request() -> bool:
    if not _is_loopback(request.remote_addr):
        return False
    host = urlsplit(request.host_url).hostname
    return host == "localhost" or _is_loopback(host)


def maintenance_enabled() -> bool:
    return bool(current_app.config["SKT_DEV_MODE"] and local_request())


def csrf_token() -> str:
    if "csrf_token" not in session:
        # Bereits geöffnete Wartungsformulare bleiben beim Update gültig.
        session["csrf_token"] = session.pop("maintenance_csrf", None) or secrets.token_urlsafe(32)
    return session["csrf_token"]


def guard_write_requests():
    if request.endpoint is None or request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    if request.endpoint in MAINTENANCE_ENDPOINTS and not maintenance_enabled():
        abort(403, description="Diese Aktion ist nur im lokalen Wartungsmodus freigegeben.")

    expected = session.get("csrf_token") or session.get("maintenance_csrf")
    if expected:
        submitted = request.form.get("csrf_token", request.headers.get("X-CSRF-Token", ""))
        if submitted.isascii() and secrets.compare_digest(expected, submitted):
            return
    abort(403, description="Das Formular gehört nicht zur aktuellen Sitzung. Bitte die Seite neu laden und erneut versuchen.")
