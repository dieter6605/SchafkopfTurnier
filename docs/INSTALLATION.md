# Inbetriebnahme (lokal)

Für einen Mac mit Apple-Chip gibt es ein vollständiges Offline-Paket. Das kleinere Software-ZIP und der Projektordner lassen sich auf macOS und Windows mit einer eigenen Python-Umgebung einrichten.

## Offline-ZIP für Macs mit Apple-Chip

1. `SchafkopfTurnier-Offline-Mac-arm64.zip` in einen eigenen Ordner entpacken. Für die erste Probe den Ordner getrennt von der laufenden Turnierverwaltung ablegen.
2. Im Finder `Turnier-starten.command` öffnen. Beim ersten Start richtet das Programm Python und die Druckbibliotheken aus dem ZIP ein. Falls du das Terminal verwendest, im entpackten Ordner `/bin/zsh ./Turnier-starten.command` aufrufen.
3. Im Browser [die Turnierliste öffnen](http://127.0.0.1:8051/tournaments). Beim ersten Start ist sie leer. Mit Ctrl+C im Terminal beenden. Beim nächsten Start dasselbe Startprogramm öffnen.

Das Paket bringt Python, Pango, die Schriftbibliotheken und sämtliche Python-Pakete mit. Zur Einrichtung genügen die macOS-Systemwerkzeuge. Die Datenbank und ihre Sicherungen liegen im Unterordner `data`. Zum Übernehmen vorhandener Daten die Wiederherstellung einer Sicherung in der Anwendung verwenden. Jedes neue Offline-ZIP in einem eigenen Ordner entpacken.

Die geprüften Python-Versionen und die Prüfsummen der conda-forge-Pakete stehen im ZIP unter `offline/` und im Projekt unter `offline-mac/`. Das Projekt enthält auch die Vorlage für das Startprogramm.

## Installation aus dem Software-ZIP oder Projektordner

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

Das Software-ZIP entpacken und im Terminal in den entpackten Ordner `SchafkopfTurnier` wechseln. Dort liegen `requirements.txt` und `run_waitress.py`.

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
