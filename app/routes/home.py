# app/routes/home.py
from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from datetime import datetime, date
from tempfile import TemporaryDirectory
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    abort,
    send_file,
)
import markdown
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .. import db
from .tournaments.overview import tournament_overview

bp = Blueprint("home", __name__)

# -----------------------------------------------------------------------------
# Hilfe-Dokumente aus /docs/
# -----------------------------------------------------------------------------
_DOCS_MAP = {
    "readme": {"file": "README.md", "title": "Lies mich"},
    "anleitung": {"file": "ANLEITUNG.md", "title": "Anleitung"},
    "installation": {"file": "INSTALLATION.md", "title": "Installation"},
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _docs_dir() -> Path:
    return _project_root() / "docs"


def _render_markdown_file(md_path: Path) -> str:
    md_text = md_path.read_text(encoding="utf-8")
    return markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc", "sane_lists"],
    )


# -----------------------------------------------------------------------------
# Backups
# -----------------------------------------------------------------------------
def _is_allowed_backup_name(name: str) -> bool:
    safe = Path(name).name
    if not safe.lower().endswith(".sqlite3"):
        return False
    return safe.startswith(("skt-backup-", "skt_backup_", "skt-upload-"))


def _format_bytes(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    u = 0
    while size >= 1024.0 and u < len(units) - 1:
        size /= 1024.0
        u += 1
    if u == 0:
        return f"{int(size)} {units[u]}"
    return f"{size:.1f} {units[u]}"


def _list_backups(backup_dir: Path) -> list[dict]:
    if not backup_dir.exists():
        return []

    files = sorted(
        (p for p in backup_dir.iterdir() if p.is_file() and _is_allowed_backup_name(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )

    out: list[dict] = []
    for p in files:
        try:
            st = p.stat()
            out.append(
                {
                    "name": p.name,
                    "size": st.st_size,
                    "size_h": _format_bytes(st.st_size),
                    "mtime": st.st_mtime,
                    "mtime_h": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M:%S"),
                }
            )
        except OSError:
            continue
    return out


# -----------------------------------------------------------------------------
# Startseite
# -----------------------------------------------------------------------------
@bp.app_template_filter("turnierdatum")
def turnierdatum(value):
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return value


@bp.get("/datensicherung")
def backups_page():
    bdir = current_app.config.get("SKT_BACKUP_DIR")
    return render_template("backups.html", backup_dir=bdir, backups=_list_backups(Path(bdir)) if bdir else [])


@bp.get("/")
def home():
    bdir = current_app.config.get("SKT_BACKUP_DIR")
    backups = _list_backups(Path(bdir)) if bdir else []
    latest_backup = max(backups, key=lambda backup: backup["mtime"], default=None)
    today = date.today()
    with closing(db.connect()) as con, con:
        con.execute("BEGIN")
        events = db.q(con, """
            SELECT t.*, EXISTS(SELECT 1 FROM tournament_rounds r WHERE r.tournament_id=t.id) AS drawn,
                   (SELECT COUNT(*) FROM tournament_participants p WHERE p.tournament_id=t.id) AS signups
            FROM tournaments t ORDER BY t.event_date, t.start_time, t.id
        """)
        open_events = [event for event in events if not (event["closed_at"] or "").strip()]
        archive = [event for event in reversed(events) if (event["closed_at"] or "").strip()]
        upcoming = [event for event in open_events if event["event_date"] >= today.isoformat()]
        # Ein bereits gespieltes offenes Turnier geht einem zukünftigen Termin vor.
        active = next((event for event in reversed(open_events)
                       if event["drawn"] and event["event_date"] <= today.isoformat()), None)
        if active is None:
            active = next(iter(upcoming or open_events[::-1] or archive), None)
        overview = tournament_overview(con, active) if active else None
        addresses = db.one(con, """
            SELECT COUNT(*) AS total, COUNT(CASE WHEN status='aktiv' THEN 1 END) AS active
            FROM addresses WHERE addressbook_id=(SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1)
        """)
    return render_template("home.html", today=today.strftime("%d.%m.%Y"), t=active, overview=overview,
                           upcoming=[e for e in upcoming if e != active][:5],
                           archive=[e for e in archive if e != active][:5],
                           addresses=addresses, backup_dir=bdir, backups=backups, latest_backup=latest_backup)


# -----------------------------------------------------------------------------
# Hilfe /docs/*.md
# -----------------------------------------------------------------------------
@bp.get("/hilfe")
def help_readme():
    return redirect(url_for("home.help_docs", doc="readme"))


@bp.get("/hilfe/<doc>")
def help_docs(doc: str):
    cfg = _DOCS_MAP.get(doc)
    if not cfg:
        abort(404)

    md_path = _docs_dir() / cfg["file"]
    if not md_path.exists():
        abort(404)

    html = _render_markdown_file(md_path)

    return render_template("help_readme.html", title=cfg["title"], content=html)


# -----------------------------------------------------------------------------
# Hilfe: Tarifrechner
# -----------------------------------------------------------------------------
@bp.get("/hilfe/tarifrechner")
def help_tarif():
    return render_template("help/tarifrechner.html")


# -----------------------------------------------------------------------------
# Backups: create / download / delete / restore / upload
# -----------------------------------------------------------------------------
@bp.post("/backup")
def backup():
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.backups_page"))

    try:
        target = db.backup_db(Path(bdir))
    except (OSError, sqlite3.Error) as e:
        flash(f"Sicherung fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.backups_page"))
    flash(f"Backup erstellt: {target.name}", "ok")
    return redirect(url_for("home.backups_page"))


@bp.get("/backup/download/<path:filename>")
def download_backup(filename: str):
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        abort(404)

    safe_name = Path(filename).name
    if not _is_allowed_backup_name(safe_name):
        abort(404)

    backup_path = Path(bdir) / safe_name
    if not backup_path.exists():
        abort(404)

    return send_file(backup_path, as_attachment=True, download_name=safe_name)


@bp.post("/backup/delete/<path:filename>")
def delete_backup(filename: str):
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.backups_page"))

    safe_name = Path(filename).name
    if not _is_allowed_backup_name(safe_name):
        flash("Ungültiger Backup-Dateiname.", "error")
        return redirect(url_for("home.backups_page"))

    backup_path = Path(bdir) / safe_name
    try:
        backup_path.unlink()
    except FileNotFoundError:
        flash("Backup-Datei nicht gefunden.", "error")
        return redirect(url_for("home.backups_page"))
    except Exception as e:
        flash(f"Löschen fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.backups_page"))

    flash(f"Backup gelöscht: {safe_name}", "ok")
    return redirect(url_for("home.backups_page"))


@bp.post("/restore/<path:filename>")
def restore(filename: str):
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.backups_page"))

    safe_name = Path(filename).name
    if not _is_allowed_backup_name(safe_name):
        flash("Ungültiger Backup-Dateiname.", "error")
        return redirect(url_for("home.backups_page"))

    return _restore_backup(Path(bdir) / safe_name)


def _restore_backup(backup_path: Path, *, uploaded: bool = False):
    pending = session.get("restore_warning", {})
    generation = digest = None
    if "confirmation" in request.form:
        if (pending.get("filename") != backup_path.name
                or pending.get("token") != request.form["confirmation"]):
            flash("Diese Bestätigung ist nicht mehr gültig. Bitte die Sicherung erneut auswählen.", "error")
            return redirect(url_for("home.backups_page"))
        session.pop("restore_warning")
        if request.form.get("decision") != "restore":
            flash("Wiederherstellung abgebrochen.", "ok")
            return redirect(url_for("home.backups_page"))
        digest = pending["digest"]
        generation = pending["generation"]

    try:
        db.restore_db(backup_path, confirmed_digest=digest, expected_generation=generation)
    except db.RestoreScoreWarning as warning:
        with closing(db.connect()) as con:
            generation = db.wlan_generation(con)
        token = uuid4().hex
        session["restore_warning"] = {
            "filename": backup_path.name, "digest": warning.digest, "generation": generation, "token": token,
        }
        return render_template("restore_warning.html", filename=backup_path.name, issues=warning.issues, token=token)
    except FileNotFoundError:
        flash("Backup-Datei nicht gefunden.", "error")
        return redirect(url_for("home.backups_page"))
    except Exception as e:
        flash(f"Wiederherstellen fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.backups_page"))

    session.pop("restore_warning", None)
    label = "Upload" if uploaded else "Backup"
    flash(f"{label} wiederhergestellt: {backup_path.name}.", "ok")
    return redirect(url_for("home.backups_page"))


def _store_uploaded_backup(backup_dir: Path, upload: FileStorage) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"skt-upload-{stamp}-{uuid4().hex}.sqlite3"
    with TemporaryDirectory(dir=backup_dir) as tmp:
        stage = Path(tmp) / "upload.sqlite3"
        upload.save(stage)
        stage.replace(target)
    return target


@bp.post("/upload-only")
def upload_only():
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.backups_page"))

    backup_dir = Path(bdir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("home.backups_page"))

    original = secure_filename(f.filename)
    if not original.lower().endswith(".sqlite3"):
        flash("Ungültige Datei. Erwartet wird eine *.sqlite3-Datei.", "error")
        return redirect(url_for("home.backups_page"))

    try:
        target_path = _store_uploaded_backup(backup_dir, f)
    except Exception as e:
        flash(f"Upload fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.backups_page"))

    flash(f"Backup hochgeladen: {target_path.name}", "ok")
    return redirect(url_for("home.backups_page"))


@bp.post("/restore-upload")
def restore_upload():
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.backups_page"))

    backup_dir = Path(bdir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("home.backups_page"))

    original = secure_filename(f.filename)
    if not original.lower().endswith(".sqlite3"):
        flash("Ungültige Datei. Erwartet wird eine *.sqlite3-Datei.", "error")
        return redirect(url_for("home.backups_page"))

    try:
        target_path = _store_uploaded_backup(backup_dir, f)
    except Exception as e:
        flash(f"Upload fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.backups_page"))

    return _restore_backup(target_path, uploaded=True)
