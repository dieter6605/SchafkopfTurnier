
# 🃏 SchafkopfTurnier

Kurzanleitung für den Turniertag

## 1. Vorbereitung vor Turnierbeginn

**Anwendung starten**
- Anwendung im Browser öffnen
(z. B. http://127.0.0.1:8000)
- Startseite prüfen (keine Fehlermeldungen)

**Backup erstellen (empfohlen)**
- Startseite → „DB-Backup jetzt erstellen“
- Meldung „Backup erstellt“ abwarten

Beim ersten Start mit einer älteren Datenbank prüft die Anwendung die Sitzplätze und Ergebnisse. Fehlt zu einem Ergebnis der passende Sitzplatz oder zu einem Sitzplatz die Anmeldung oder Runde, bricht die Umstellung ab. Die Meldung nennt Turnier, Runde, Tisch und Teilnehmer-ID. Diese Angaben mit dem ursprünglichen Sitzplan vergleichen. Die Prüfung gilt auch bei der Wiederherstellung einer älteren Sicherung.

**Adressen pflegen**

- Tischblätter, Wertungen und Turnierexporte verwenden Namen und Wohnort aus dem aktuellen Adressbuch. Eine Korrektur gilt auch für Nachdrucke abgeschlossener Turniere.
- Bei neuen Adressen ist „Einladung aktiv“ voreingestellt, auch bei der Schnellerfassung im Turnier. Im Adressbuch lässt sich das Kennzeichen ändern.
- In der CSV-Spalte `invite` bedeutet `1` „an“. `0` und ein leeres Feld bedeuten „aus“. Fehlt die ganze Spalte, behalten bekannte Adress-IDs ihr Kennzeichen. Zeilen ohne bekannte ID erhalten „an“.
- Bei einer Turnieranmeldung oder einer früheren Teilnahme ist „Löschen“ gesperrt. „Deaktivieren“ setzt eine aktive Adresse auf inaktiv und erhält ihre Teilnahmehistorie.
- Zum CSV-Import eine UTF-8-Datei mit Semikolon und den Spalten `nachname`, `vorname` und `wohnort` auswählen und „Datei prüfen“ anklicken. Die Vorschau zählt gültige und fehlerhafte Zeilen. Sie nennt die Zeile, wenn ein Eintrag korrigiert werden muss.
- „Standardadressbuch ersetzen“ übernimmt die geprüfte Datei. Eine reine Kopfzeile oder eine Datei mit fehlerhaften Zeilen wird abgewiesen. Bei vorhandenen Turnieranmeldungen im Standardadressbuch steht die Vorschau zur Verfügung, das Ersetzen ist gesperrt.
- Bei früheren Teilnahmen ohne aktuelle Anmeldung den vollständigen Adress-Export als Vorlage verwenden. Die Personen müssen unter ihrer bisherigen ID enthalten sein. Weggelassene Teilnahmespalten werden für diese Personen aus dem bestehenden Adressbuch übernommen.

## 2. Turnier öffnen
1.	Menü „Turniere“
2.	Gewünschtes Turnier anklicken
3.	Übersicht prüfen:
    - Titel
    - Datum / Uhrzeit
    - Anzahl Teilnehmer
    - Anzahl Tische

Beim Anlegen und Bearbeiten ist der Marker optional. Für den Abschluss braucht jedes Turnier einen eigenen Marker aus dem Veranstaltungsdatum im Format JJMMTT und vier weiteren Buchstaben oder Ziffern. Für den 12. November 2026 ist etwa `261112ABCD` gültig. Bei einem belegten Marker zeigt das Formular Nummer und Namen des zugehörigen Turniers. Die übrigen Eingaben bleiben zur Korrektur im Formular.

Beim Löschen eines offenen Turniers werden seine Anmeldungen, Runden, Ergebnisse und das Teilnehmerprotokoll gemeinsam entfernt. Die Adressen bleiben im Adressbuch.

## 3. Teilnehmer erfassen

Teilnehmer hinzufügen
- Button „Teilnehmer erfassen“
- Im Adressbuch suchen (Name / Wohnort)
- Übernehmen klicken
    → Teilnehmer erhält automatisch eine Nummer

**Quick-Add (neue Teilnehmer)**
- Alt + N oder Button „Quick-Add“
- Pflichtfelder:
    - Nachname
	- Vorname
	- Wohnort
	- „Anlegen + übernehmen“

## 4. Teilnehmernummern prüfen

Im Bereich „Teilnehmernummern“:
- Prüfen
    → zeigt, ob Nummern fehlen (Lücken)
- Neu durchnummerieren (1..N)
    → alle Teilnehmer neu sortiert
- Renummerieren ab Nr. X
    → Nummern darunter bleiben unverändert

***Hinweis:***
Beim Entfernen eines Teilnehmers bleibt standardmäßig eine Nummernlücke bestehen.

## 5. Teilnehmer entfernen oder tauschen

Teilnehmer entfernen
- Entfernen ist möglich, solange für die Anmeldung kein Sitzplatz und kein Ergebnis vorliegt.
- Button „Entfernen“
- Nummer bleibt frei (keine automatische Renummerierung)

Teilnehmer ersetzen (Swap)
- „Swap…“ öffnen und die Zielperson wählen. Bei einem Tausch behalten die Startnummern ihre Sitzplätze. Der neue Name gilt für alle bisherigen Sitzpläne dieser Anmeldung.
- Sobald eine der beteiligten Anmeldungen ein Ergebnis hat, ist der Tausch gesperrt.
- Vor dem Abschluss zeigt die Turnierübersicht den ersten Tisch mit fehlenden oder fehlerhaften Angaben. Jeder Tisch braucht die Sitze A bis D, vier passende Ergebnisse, eine Punktesumme von null und gültige Soli (ganze Zahlen ab null).

## 6. Runde auslosen

Im Turnier:

Runde 1
- „Runde 1 auslosen“
- Solange die Runde keine Ergebnisse enthält, kann die Turnierleitung einen neuen Sitzplan bestätigen. Mit dem ersten gespeicherten Ergebnis wird der Sitzplan gesperrt.

Weitere Runden
- „Nächste Runde vorbereiten“
- „Runde X auslosen“
- Im Sitzplan öffnet Umschalt+R eine vorhandene Folgerunde. Aus der letzten ausgelosten Runde heraus fragt die Anwendung, ob sie die nächste Runde auslosen soll.

Anzeige
- „Runde anzeigen“ zeigt Sitzplan (Tische & Plätze)

## 7. Sitzpläne drucken / aushängen
- Runde anzeigen
- Browser → Drucken
- Druckkopf mit Logo erscheint automatisch
- Für Aushang geeignet

8. Ergebnisse eingeben

Vorgehen
1.	Runde auswählen
2.	Tisch auswählen
3.	Für jeden der 4 Spieler:
    - Punkte
	- Soli

Plausibilitätsprüfung
- Summe der Punkte muss 0 ergeben
- Sonst Fehlermeldung

Ist ein Tisch in zwei Fenstern geöffnet, prüft die Anwendung beim Speichern die Punkte und Soli aller vier Spieler. Hat sich der Tisch inzwischen geändert, zeigt sie die gespeicherten Werte und daneben die Eingabe aus dem älteren Fenster. Die Turnierleitung kann beide mit dem Tischblatt vergleichen und eine Korrektur im aktuellen Formular speichern.

## 9. Wertungen anzeigen & drucken

Rundenwertung
- Punkte & Soli nur dieser Runde
- Sortierung nach Platzierung

Gesamtwertung
- Summe aller Runden
- Sortierung:
	1.	Punkte
	2.	Soli
	3.	Name

Druckausgabe
- Zwei Spalten (A4)
- Format:
```text
Platz | Name | Punkte (Soli)
```

**Spielerkarte als PDF**

In der Gesamtwertung den Spielernamen öffnen und „PDF“ wählen. Der QR-Code und der anklickbare Link auf der Karte führen zur Spieleransicht unter der Adresse, mit der die Turnierverwaltung geöffnet wurde. Bei `127.0.0.1` oder `localhost` lässt sich die Ansicht auf dem Turnier-Mac öffnen, solange die Anwendung läuft. Ein Handy kann den Code lesen. Für den Aufruf auf dem Handy ist eine vom Handy erreichbare Adresse erforderlich.

## 10. Tastatur-Shortcuts (wichtig!)

Allgemein
- Alt + N → Quick-Add
- Esc → Quick-Add schließen

Teilnehmerliste
- ↑ / ↓ → Auswahl bewegen
- Shift + Entf → Teilnehmer entfernen

Turnierübersicht
- E → Teilnehmer erfassen
- L → Turnierliste
- B → Bearbeiten
- 1 → Runde 1 anzeigen
- N → nächste Runde vorbereiten
- R → Runde auslosen
- Umschalt + R → Auslosung der nächsten Runde bestätigen

## 11. Typische Hinweise aus der Praxis

- Vor jeder Auslosung: Teilnehmerliste prüfen
- Nach dem Auslosen: Sitzpläne sofort drucken
- Ergebnisse zeitnah eingeben (nicht sammeln)
- Bei Problemen: Seite neu laden – Daten bleiben erhalten
- Zwischendurch Backups erstellen

Beim Speichern prüft die Anwendung, ob das Formular zur aktuellen Browsersitzung gehört. Erscheint beim Speichern die Meldung zur Sitzung, die eingegebenen Werte kurz notieren, die Seite neu laden und die Eingabe erneut absenden.

## 12. Ende des Turniers
- Gesamtwertung anzeigen
- Gesamtwertung drucken
- Letztes Backup erstellen

### Abgeschlossenes Turnier korrigieren

Im regulären Turnierbetrieb ist ein abgeschlossenes Turnier gegen Änderungen gesperrt. Für eine beabsichtigte Korrektur den lokalen Wartungsmodus wie in der Installationsanleitung starten. In der Turnieransicht **Turnier wieder öffnen** wählen und die Rückfrage bestätigen. Die erfassten Ergebnisse bleiben erhalten. Die Anwendung nimmt den Teilnahmemarker dieses Turniers bei den angemeldeten Personen zurück.

Für die Reparatur der Teilnahmehistorie ist **Nur Vorschau** vorbelegt. Nach der Bestätigung zeigt die Anwendung die Anzahl der betroffenen Adressen. Zum Speichern das Häkchen entfernen, den gewünschten Adressumfang wählen und bestätigen. Die auf ein einzelnes Turnier begrenzte Reparatur steht bei offenen Turnieren zur Verfügung.

**Viel Erfolg & Gut Blatt!** 🍀
