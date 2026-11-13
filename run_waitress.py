from __future__ import annotations

import os
from pathlib import Path

from waitress import serve

from app.web import create_app

DATA_DIR = Path(__file__).resolve().parent / "data"
port = int(os.environ.get("PORT", "8000"))
wlan_ip = os.environ.get("SKT_WLAN_IP", "").strip()
app = create_app(
    db_path=Path(os.environ.get("SKT_DB_PATH", DATA_DIR / "skt.sqlite3")),
    backup_dir=Path(os.environ.get("SKT_BACKUP_DIR", DATA_DIR / "backups")),
    wlan_ip=wlan_ip,
    port=port,
)

if __name__ == "__main__":
    if wlan_ip:
        print(f"Turnierleitung: http://127.0.0.1:{port}/tournaments", flush=True)
        print("WLAN: Helferlink und Helfercode stehen im geöffneten Turnier.", flush=True)
        serve(app, listen=f"127.0.0.1:{port} {wlan_ip}:{port}", threads=8)
    else:
        serve(app, host=os.environ.get("HOST", "127.0.0.1"), port=port, threads=8)
