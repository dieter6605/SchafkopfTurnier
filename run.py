from __future__ import annotations

import os
from pathlib import Path

from app.web import create_app  # <-- HIER ist der Fix

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("SKT_DB_PATH", str(DATA_DIR / "skt.sqlite3")))
BACKUP_DIR = Path(os.environ.get("SKT_BACKUP_DIR", str(DATA_DIR / "backups")))

app = create_app(
    db_path=DB_PATH,
    backup_dir=BACKUP_DIR,
)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), debug=True)