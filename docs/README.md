# SchafkopfTurnier

**SchafkopfTurnier** ist eine **lokal betriebene, webbasierte Turnierverwaltung** für Schafkopf-Turniere.
Die Anwendung ist speziell für **Vereine, Dorfturniere und private Veranstaltungen** konzipiert und funktioniert vollständig **offline**.

Kein Cloud-Dienst, keine externen Abhängigkeiten – **alle Daten bleiben lokal**.

## Kurzüberblick
- Verwaltung von Turnieren und Teilnehmern
- **Stabile Startnummern** (bewusst ohne automatische Neunummerierung)
- Rundenweise **Auslosung von Tischen und Sitzplätzen**
- Erfassung von **Punkten und Soli**
- Automatische **Runden- und Gesamtwertung**
- **Druckoptimierte Listen** (Sitzpläne, Wertungen)
- Umfangreiche **Tastatursteuerung** für schnellen Turnierbetrieb
- Lokale SQLite-Datenbank mit **Backup & Restore**

## Zielgruppe
- Schafkopf-Vereine
- Feuerwehr-, Dorf- und Kirchweihturniere
- Turnierleiter, die **zügig, zuverlässig und ohne Internet** arbeiten möchten

## Technische Grundlagen
- Programmiersprache: **Python**
- Webframework: **Flask**
- Datenbank: **SQLite**
- Frontend: **Bootstrap 5**
- Betrieb: **lokaler Webserver**

## Voraussetzungen
- **Python ≥ 3.11**
- Unterstützte Systeme:
	- macOS
	- Windows
	- Linux

# Installation

## 1. Projekt herunterladen / entpacken

```bash
git clone <repository-url>
cd SchafkopfTurnier
```

oder ZIP-Datei entpacken und ins Verzeichnis wechseln.

## 2. Virtuelle Umgebung (empfohlen)

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\activate
```

## 3. Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```

# Start der Anwendung

```bash
python run.py
```

Danach im Browser öffnen:

```text
http://127.0.0.1:8000
```

Näheres in der Installationsanleitung.

# Projektstruktur (Auszug)

```text
app/
├─ routes/
│  ├─ home.py
│  ├─ addresses/
│  └─ tournaments/
├─ templates/
│  ├─ layout.html
│  ├─ home.html
│  ├─ tournament_*.html
├─ static/
│  ├─ js/
│  └─ branding/
│     ├─ image.png   # Anzeige / Startseite
│     └─ logo.png    # Druckausgaben
├─ db.py
└─ web.py
docs/
├─ README.md
├─ ANLEITUNG.md
└─ INSTALLATION.md
```

# Branding / Logos

Das Projekt unterstützt **getrennte Logos für Bildschirm und Druck**.

## Anzeige-Logo
- Datei:
app/static/branding/image.png
- Verwendung:
	- Startseite
	- Bildschirmdarstellung

## Druck-Logo
- Datei:
app/static/branding/logo.png
- Verwendung:
	- Ausdrucke (Sitzpläne, Wertungen)

# Konfiguration

```python
# app/web.py
app.config["SKT_HOME_IMAGE"] = "branding/image.png"
app.config["SKT_PRINT_LOGO"] = "branding/logo.png"
```
Die Logos stehen über einen context_processor automatisch in allen Templates zur Verfügung.


# Datenbank
- Dateibasierte **SQLite-Datenbank**
- Automatische Initialisierung beim Start
- Schema migrationsfähig vorbereitet
- **Backup-Funktion direkt in der Anwendung**
- Backup:
	- Erstellen
	- Download
	- Wiederherstellen
	- Löschen
	- Upload externer Backups

# Bedienkonzept
- Vollständig per **Maus und Tastatur** bedienbar
- Optimiert für den **Turniertag**
- Schnelle Navigation auch bei vielen Teilnehmern
- Klare Trennung:
	- Vorbereitung
	- Auslosung
	- Ergebniserfassung
	- Auswertung
- Zusätzliche Details siehe:
	- **ANLEITUNG.md** (Turnierablauf)
	- **INSTALLATION.md** (Inbetriebnahme)

# Hilfe in der Anwendung

Die Dokumentation ist direkt über das Menü **„Hilfe“** erreichbar:
- **Lies mich** → README.md
- **Anleitung** → ANLEITUNG.md
- **Installation** → INSTALLATION.md

# Lizenz / Nutzung
- Gedacht für **private, vereinsinterne und nicht-kommerzielle Nutzung**
- Keine Gewährleistung
- Nutzung auf eigene Verantwortung

# Status

**Stabiler Stand für reale Turniere.**
Geeignet als Basis für Version **v1.0**.