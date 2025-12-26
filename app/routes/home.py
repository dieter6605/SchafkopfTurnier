# app/routes/home.py
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
    abort,
    send_file,
)
import markdown
from werkzeug.utils import secure_filename

from .. import db

bp = Blueprint("home", __name__)


def _is_allowed_backup_name(name: str) -> bool:
    safe = Path(name).name  # keine Pfade zulassen
    if not safe.lower().endswith(".sqlite3"):
        return False
    return safe.startswith("skt-backup-") or safe.startswith("skt-upload-")


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

    files = []
    files.extend(list(backup_dir.glob("skt-backup-*.sqlite3")))
    files.extend(list(backup_dir.glob("skt-upload-*.sqlite3")))

    files = sorted(set(files), key=lambda p: p.name, reverse=True)

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


@bp.get("/")
def home():
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    backups: list[dict] = []
    if bdir:
        backups = _list_backups(Path(bdir))
    return render_template("home.html", backups=backups, backup_dir=bdir)


@bp.get("/hilfe")
def help_readme():
    """
    Zeigt die README.md als HTML-Seite an.
    (Aktueller Stand: README.md im Projektroot)
    """
    project_root = Path(__file__).resolve().parents[2]
    readme_path = project_root / "README.md"

    if not readme_path.exists():
        abort(404)

    md_text = readme_path.read_text(encoding="utf-8")

    html = markdown.markdown(
        md_text,
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            "sane_lists",
        ],
    )

    return render_template(
        "help_readme.html",
        title="Hilfe / README",
        content=html,
    )


@bp.post("/backup")
def backup():
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.home"))

    target = db.backup_db(Path(bdir))
    flash(f"Backup erstellt: {target.name}", "ok")
    return redirect(url_for("home.home"))


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
        return redirect(url_for("home.home"))

    safe_name = Path(filename).name
    if not _is_allowed_backup_name(safe_name):
        flash("Ungültiger Backup-Dateiname.", "error")
        return redirect(url_for("home.home"))

    backup_path = Path(bdir) / safe_name
    try:
        backup_path.unlink()
    except FileNotFoundError:
        flash("Backup-Datei nicht gefunden.", "error")
        return redirect(url_for("home.home"))
    except Exception as e:
        flash(f"Löschen fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.home"))

    flash(f"Backup gelöscht: {safe_name}", "ok")
    return redirect(url_for("home.home"))


@bp.post("/restore/<path:filename>")
def restore(filename: str):
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.home"))

    safe_name = Path(filename).name  # keine Pfade zulassen
    if not _is_allowed_backup_name(safe_name):
        flash("Ungültiger Backup-Dateiname.", "error")
        return redirect(url_for("home.home"))

    backup_path = Path(bdir) / safe_name

    try:
        db.restore_db(backup_path)
    except FileNotFoundError:
        flash("Backup-Datei nicht gefunden.", "error")
        return redirect(url_for("home.home"))
    except Exception as e:
        flash(f"Wiederherstellen fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.home"))

    flash(f"Backup wiederhergestellt: {safe_name}. (Tipp: App ggf. neu starten)", "ok")
    return redirect(url_for("home.home"))


@bp.post("/upload-only")
def upload_only():
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.home"))

    backup_dir = Path(bdir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("home.home"))

    original = secure_filename(f.filename)
    if not original.lower().endswith(".sqlite3"):
        flash("Ungültige Datei. Erwartet wird eine *.sqlite3-Datei.", "error")
        return redirect(url_for("home.home"))

    target_name = f"skt-upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    target_path = backup_dir / target_name

    try:
        f.save(target_path)
    except Exception as e:
        flash(f"Upload fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.home"))

    flash(f"Backup hochgeladen: {target_name}", "ok")
    return redirect(url_for("home.home"))


@bp.post("/restore-upload")
def restore_upload():
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    if not bdir:
        flash("Backup-Verzeichnis ist nicht gesetzt.", "error")
        return redirect(url_for("home.home"))

    backup_dir = Path(bdir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("home.home"))

    original = secure_filename(f.filename)
    if not original.lower().endswith(".sqlite3"):
        flash("Ungültige Datei. Erwartet wird eine *.sqlite3-Datei.", "error")
        return redirect(url_for("home.home"))

    target_name = f"skt-upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    target_path = backup_dir / target_name

    try:
        f.save(target_path)
    except Exception as e:
        flash(f"Upload fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.home"))

    try:
        db.restore_db(target_path)
    except Exception as e:
        flash(f"Wiederherstellen fehlgeschlagen: {e}", "error")
        return redirect(url_for("home.home"))

    flash(f"Upload wiederhergestellt: {target_name}. (Tipp: App ggf. neu starten)", "ok")
    return redirect(url_for("home.home"))