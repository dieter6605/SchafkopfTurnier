# Inbetriebnahme (lokal)

Diese Anleitung beschreibt die lokale Installation und den Start der Anwendung SchafkopfTurnier auf macOS und Windows mithilfe einer virtuellen Python-Umgebung.

Voraussetzungen
- Python 3.11 oder neuer
- Internetzugang (für Python-Pakete)
- Schreibrechte im Projektverzeichnis

Python prüfen

```bash
python --version
```

oder

```bash
python3 --version
```

## 1. Projekt vorbereiten

Repository klonen oder entpacken

```bash
cd <Zielverzeichnis>
git clone <REPOSITORY-URL>
cd SchafkopfTurnier
```

Alternativ: ZIP entpacken und in das Projektverzeichnis wechseln.

## 2. Virtuelle Umgebung erstellen

**macOS / Linux**

```bash
python3 -m venv .venv
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
```

## 3. Virtuelle Umgebung aktivieren

**macOS / Linux**

```bash
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
.venv\Scripts\Activate.ps1
```

Falls die Ausführung blockiert ist:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Nach Aktivierung erscheint:

```text
(.venv)
```

## 4. Abhängigkeiten installieren

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Die Spielerkarte verwendet WeasyPrint mit den nativen Pango-Bibliotheken für PDF und `qrcode[pil]` für das QR-Bild. Vor dem Turniertag lässt sich die Druckumgebung in der aktivierten Python-Umgebung prüfen:

```bash
python -m weasyprint --info
python -m pip check
```

Für den laufenden PDF-Export werden das lokale Stylesheet, die Vereinslogos und ein eingebettetes QR-Bild verwendet.

## 5. Lokalen Webserver starten

Für den Turnierbetrieb in der aktivierten Python-Umgebung starten.

```bash
python run_waitress.py
```

Waitress verwendet standardmäßig `127.0.0.1` und Port `8000`. Beim ersten Start der aktualisierten Anwendung entsteht neben der Datenbank die private Schlüsseldatei `.skt-session/key`. Sie signiert die Browsersitzungen und wird bei weiteren Starts wiederverwendet. Bereits vor der Umstellung geöffnete Wartungsformulare und Importvorschauen bitte neu laden.

Für eine Wartung den laufenden Server mit Ctrl+C beenden. Unter macOS oder Linux gilt der folgende Aufruf für diesen einen Start.

```bash
SKT_DEV_MODE=1 HOST=127.0.0.1 python run_waitress.py
```

Unter Windows in PowerShell:

```powershell
$env:SKT_DEV_MODE="1"
$env:HOST="127.0.0.1"
try { python run_waitress.py } finally { Remove-Item Env:SKT_DEV_MODE }
```

Am Turnierrechner `http://127.0.0.1:8000` öffnen. Die Kopfzeile zeigt im Wartungsmodus **Wartung**. Nach der Wartung mit Ctrl+C beenden und wieder regulär starten. Für die Entwicklung gibt es weiterhin `python run.py`. Der Flask-Debugger lässt sich dort gezielt mit `FLASK_DEBUG=1` aktivieren. Die Wartungsfreigabe erfolgt über `SKT_DEV_MODE`.

## 6. Anwendung im Browser öffnen

```text
http://127.0.0.1:8000
```

## 7. Beenden der Anwendung

```bash
CTRL + C
```

Virtuelle Umgebung verlassen:

```bash
deactivate
```

# Typische Probleme & Lösungen

❌ ModuleNotFoundError

➡ Virtuelle Umgebung aktivieren und pip install -r requirements.txt erneut ausführen

❌ Port 8000 belegt (macOS/Linux)

```bash
PORT=8050 python run_waitress.py
```

❌ Python-Version zu alt

➡ Python ≥ 3.11 installieren
