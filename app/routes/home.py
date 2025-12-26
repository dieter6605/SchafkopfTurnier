# app/routes/home.py
from __future__ import annotations

from pathlib import Path
from datetime import datetime, date
from typing import Iterable

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
# Dashboard-Helpers
# -----------------------------------------------------------------------------
def _default_ab_id(con) -> int:
    dab = db.one(con, "SELECT id FROM addressbooks WHERE is_default=1 LIMIT 1")
    return int(dab["id"]) if dab else 1


def _count(con, sql: str, params: tuple = ()) -> int:
    r = db.one(con, sql, params)
    try:
        return int((r["c"] if r else 0) or 0)
    except Exception:
        return 0


def _parse_iso_date(d: str | None) -> date | None:
    if not d:
        return None
    s = str(d).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _day_label(iso_date: str | None) -> str:
    d = _parse_iso_date(iso_date)
    if not d:
        return ""
    today = date.today()
    delta = (d - today).days
    if delta == 0:
        return "Heute"
    if delta == 1:
        return "Morgen"
    if delta == -1:
        return "Gestern"
    if delta > 1:
        return f"in {delta} Tagen"
    return f"vor {abs(delta)} Tagen"


def _get_next_tournament(con) -> dict | None:
    today = date.today().isoformat()
    return db.one(
        con,
        """
        SELECT *
        FROM tournaments
        WHERE event_date >= ?
        ORDER BY event_date ASC, start_time ASC, id ASC
        LIMIT 1
        """,
        (today,),
    )


def _get_last_tournament(con) -> dict | None:
    today = date.today().isoformat()
    return db.one(
        con,
        """
        SELECT *
        FROM tournaments
        WHERE event_date < ?
        ORDER BY event_date DESC, start_time DESC, id DESC
        LIMIT 1
        """,
        (today,),
    )


def _list_upcoming(con, limit: int = 5) -> list[dict]:
    today = date.today().isoformat()
    return db.q(
        con,
        """
        SELECT *
        FROM tournaments
        WHERE event_date >= ?
        ORDER BY event_date ASC, start_time ASC, id ASC
        LIMIT ?
        """,
        (today, int(limit)),
    )


def _list_recent(con, limit: int = 5) -> list[dict]:
    return db.q(
        con,
        """
        SELECT *
        FROM tournaments
        ORDER BY event_date DESC, start_time DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def _participants_counts_for(con, tournament_ids: Iterable[int]) -> dict[int, int]:
    ids = [int(x) for x in tournament_ids if int(x) > 0]
    if not ids:
        return {}

    placeholders = ",".join(["?"] * len(ids))
    rows = db.q(
        con,
        f"""
        SELECT tournament_id, COUNT(*) AS c
        FROM tournament_participants
        WHERE tournament_id IN ({placeholders})
        GROUP BY tournament_id
        """,
        tuple(ids),
    )
    out: dict[int, int] = {int(r["tournament_id"]): int(r["c"] or 0) for r in rows}
    return out


# -----------------------------------------------------------------------------
# Backups
# -----------------------------------------------------------------------------
def _is_allowed_backup_name(name: str) -> bool:
    safe = Path(name).name
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


# -----------------------------------------------------------------------------
# Startseite
# -----------------------------------------------------------------------------
@bp.get("/")
def home():
    # Backups
    bdir = (current_app.config.get("SKT_BACKUP_DIR") or "").strip()
    backups: list[dict] = []
    if bdir:
        backups = _list_backups(Path(bdir))

    dash = {
        "tournaments_total": 0,
        "addresses_total": 0,
        "addresses_active": 0,
        "next_tournament": None,
        "last_tournament": None,
        "next_participants": 0,
        "last_participants": 0,
        "upcoming": [],
        "recent": [],
        "counts_by_tid": {},
    }

    try:
        with db.connect() as con:
            dash["tournaments_total"] = _count(con, "SELECT COUNT(*) AS c FROM tournaments")

            ab_id = _default_ab_id(con)
            dash["addresses_total"] = _count(
                con,
                "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=?",
                (ab_id,),
            )
            dash["addresses_active"] = _count(
                con,
                "SELECT COUNT(*) AS c FROM addresses WHERE addressbook_id=? AND status='aktiv'",
                (ab_id,),
            )

            nt = _get_next_tournament(con)
            lt = _get_last_tournament(con)

            dash["next_tournament"] = nt
            dash["last_tournament"] = lt

            upcoming = _list_upcoming(con, 5)
            recent = _list_recent(con, 5)

            dash["upcoming"] = upcoming
            dash["recent"] = recent

            all_ids = []
            if nt:
                all_ids.append(int(nt["id"]))
            if lt:
                all_ids.append(int(lt["id"]))
            all_ids.extend([int(x["id"]) for x in upcoming])
            all_ids.extend([int(x["id"]) for x in recent])

            counts_by_tid = _participants_counts_for(con, all_ids)
            dash["counts_by_tid"] = counts_by_tid

            if nt:
                dash["next_participants"] = int(counts_by_tid.get(int(nt["id"]), 0))
            if lt:
                dash["last_participants"] = int(counts_by_tid.get(int(lt["id"]), 0))
    except Exception:
        pass

    return render_template(
        "home.html",
        backups=backups,
        backup_dir=bdir,
        dash=dash,
        day_label=_day_label,  # Jinja kann die Funktion direkt nutzen
    )


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
# Backups: create / download / delete / restore / upload
# -----------------------------------------------------------------------------
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

    safe_name = Path(filename).name
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