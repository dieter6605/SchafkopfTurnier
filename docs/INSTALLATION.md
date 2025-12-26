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

Typische Inhalte von requirements.txt:

```text
Flask>=3.0,<4.0
markdown
```

## 5. Lokalen Webserver starten

**Beispiel: Startskript run.py**

```bash
python run.py
```

Oder direkt mit Flask:

```bash
export FLASK_APP=app.web:create_app
export FLASK_ENV=development
flask run
```

**Windows (PowerShell):**

```powershell
$env:FLASK_APP="app.web:create_app"
$env:FLASK_ENV="development"
flask run
```

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

❌ Port 8000 belegt

```bash
flask run --port 8050
```

❌ Python-Version zu alt

➡ Python ≥ 3.11 installieren
