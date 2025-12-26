# 🂡 SchafkopfTurnier – Turnierverwaltung

Software zur Verwaltung von Schafkopf-Turnieren
(Teilnehmer, Auslosung, Ergebnisse, Runden- & Gesamtwertung, Drucklisten)

## 1. Zweck & Zielgruppe

*Diese Anwendung dient der einfachen und zuverlässigen Organisation von Schafkopf-Turnieren, insbesondere für:*
- Vereine
- Dorf- und Kirchweihturniere
- Wirtshaus- und Benefizturniere
- private Turniere mit vielen Teilnehmern

**Ziel:**
- möglichst wenig Technikaufwand am Turniertag
- klare Ausdrucke
- nachvollziehbare Nummerierung und Wertungen

## 2. Funktionsübersicht

### Teilnehmerverwaltung
- Teilnehmer aus Adressbuch übernehmen
- Quick-Add für spontane Anmeldungen
- feste Teilnehmernummern (Lücken erlaubt)
- manuelles Renummerieren (optional)

### Turnier & Runden
- Turniere anlegen und bearbeiten
- Runden vorbereiten oder direkt auslosen
- automatische Tisch- und Sitzverteilung (4er-Tische)
- Navigation per Maus oder Tastatur

### Ergebnisse & Wertung
- Ergebniserfassung pro Runde
- Rundenwertung (Punkte + Soli)
- Gesamtwertung über alle Runden
- automatische Platzierung

### Druck & Listen
- übersichtliche Drucklisten
- kompakte 2-Spalten-Layouts
- Druckkopf mit Vereins-/Turnierlogo
- getrennte Logos für Bildschirm & Ausdruck

## 3. Bedienung am Turniertag (Kurzfassung)

**Empfohlener Ablauf:**
1.	Turnier anlegen
2.	Teilnehmer erfassen
3.	Runde 1 auslosen
4.	Sitzplan ausdrucken
5.	Ergebnisse eingeben
6.	Nächste Runde auslosen
7.	Am Ende: Gesamtwertung drucken

***Alles läuft offline auf einem Laptop.***

## 4. Tastatur-Kurzbefehle (Auswahl)

**Teilnehmerliste**
- ↑ / ↓ → Auswahl
- Shift + Entf → Teilnehmer entfernen
- Alt + N → Quick-Add
- Alt + P → Nummern prüfen
- Alt + R → Renummerieren ab Nr.

**Runden**
- R → Runde auslosen
- Shift + R → nächste Runde auslosen
- N → nächste Runde vorbereiten
- 1 → Runde 1 anzeigen

## 5. Nummernlogik (wichtig für Vereine)
- Teilnehmer haben feste Nummern
- Beim Entfernen entsteht bewusst eine Lücke
- Keine automatische Verdichtung
	- Manuelle Funktionen:
	- „Prüfen“ (zeigt Lücken)
	- „Neu durchnummerieren (1..N)“
	- „Renummerieren ab Nr. X“

**Dadurch bleiben Ausdrucke und Aushänge stabil.**

## 6. Logos & Darstellung

**Verwendete Logos**

| Zweck								| Datei
| ----------------------------------|-----------------------------------------
| Startseite / Bildschirm			| app/static/branding/image.png
| Ausdrucke / Druckkopf				| app/static/branding/logo.png

Die Logos sind getrennt konfigurierbar.

## 7. Technische Basis (kurz & verständlich)
- Programmiersprache: Python
- Webframework: Flask
- Datenbank: SQLite
- Frontend: Bootstrap 5
- Betrieb: lokal (kein Internet nötig)

## 8. Installation (für technisch Interessierte)

'''bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
'''
*Starten (Beispiel):*
'''bash
python run.py
'''

## 9. Projektstruktur (vereinfacht)

'''text
app/
├─ routes/          # Seiten & Logik
├─ templates/       # HTML-Vorlagen
├─ static/
│  ├─ branding/     # Logos
│  └─ js/           # Tastatursteuerung
├─ db.py            # Datenbank
└─ web.py           # App-Start & Konfiguration
'''

## 10. Datensicherung
- Manuelles DB-Backup über Startseite
- SQLite-Datei kann jederzeit kopiert werden
- Empfohlen: Backup vor und nach dem Turnier

## 11. Hinweise für Helfer
- Keine Internetverbindung nötig
- Browser reicht (Chrome, Firefox, Safari)
- Druck über normalen Systemdrucker
- Bei Problemen: Seite neu laden (keine Daten verloren)

## 12. Lizenz & Nutzung

Interne Vereinssoftware.
Freie Nutzung im Vereins- und Privatbereich.

## 13. Ansprechpartner

> Organisation / Turnierleitung:
> [Sportfreunde Bieswang 1949 e.V.]
