# SchafkopfTurnier

Webbasierte, lokal betriebene Turnierverwaltung für Schafkopf-Turniere.

## Kurzüberblick
- Teilnehmerverwaltung mit stabilen Startnummern
- Runden-Auslosung, Ergebniserfassung
- Runden- und Gesamtwertung
- Druckoptimierte Listen
- Umfangreiche Tastatursteuerung
- Vollständig lokal (SQLite)

## Voraussetzungen
- Python >= 3.11

## Installation
'''bash
pip install -r requirements.txt
'''

## Start
'''bash
python run.py
'''
Anschließend im Browser öffnen:
'''code
http://127.0.0.1:8000
'''

## Projektstruktur (Auszug)
'''text
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
'''

## Branding / Logos

Das Projekt unterstützt **getrennte Logos**:
- Anzeige-Logo
	- Datei: app/static/branding/image.png
	- Verwendung: Startseite, Bildschirmdarstellung
- Druck-Logo
	- Datei: app/static/branding/logo.png
	- Verwendung: Ausdrucke (Listen, Wertungen)

Die Zuordnung erfolgt zentral in:
'''python
# app/web.py
app.config["SKT_SITE_LOGO"] = "branding/image.png"
app.config["SKT_PRINT_LOGO"] = "branding/logo.png"
'''
(über context_processor in allen Templates verfügbar)

## Datenbank
- SQLite (.sqlite3)
- Automatische Initialisierung beim Start
- Schema-Versionierung vorbereitet
- Backup-Funktion direkt aus der Startseite

## Bedienkonzept
- Maus und Tastatur vollständig nutzbar
- Optimiert für Turnierleitung
- Schnelle Eingabe & Navigation auch unter Zeitdruck

## Lizenz / Nutzung

Private Nutzung für Vereins-, Dorf- und Freundschaftsturniere.
Keine Garantie, Nutzung auf eigene Verantwortung.

## Ergebnis

Du hast jetzt:

- ✔️ saubere **Dependency-Deklaration**
- ✔️ eine **projektreife README**
- ✔️ klare Trennung von **Anzeige- & Druck-Branding**
- ✔️ gute Basis für Version **v1.0**

