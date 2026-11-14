
# 🃏 SchafkopfTurnier

Kurzanleitung für den Turniertag

Unter **Hilfe → Hilfeübersicht** findest du diese Anleitung, die Installation und den Tarifrechner. Mit **Kapitel wählen** springst du direkt zum gewünschten Arbeitsschritt. Der Tarifrechner zeigt den Betrag je Gewinner und Verlierer.

## 1. Vorbereitung vor Turnierbeginn

**Anwendung starten**
- Anwendung im Browser öffnen
(z. B. http://127.0.0.1:8000)
- Auf „Start“ das aktuelle Turnier und den nächsten Schritt prüfen. „Turnier öffnen“ führt zur Übersicht mit dem Stand der einzelnen Tische.
- Unter „Turniere“ nach Name oder Ort suchen und offene oder abgeschlossene Turniere auswählen. **Zur Trefferliste** führt von einem geöffneten Treffer zur gleichen Suche und Statusauswahl zurück. Wird **Neues Turnier** oder **Bearbeiten** direkt aus dieser Liste geöffnet, führt auch **Abbrechen** dorthin.

**Spielregeln lesen und ändern**

Unter **Hilfe → Spielregeln** steht das aktuelle Regelblatt für alle Turniere. **PDF herunterladen** speichert eine druckbare Fassung. Im Vereins-WLAN führt **Spielregeln lesen** auf der Helferseite zum gleichen Blatt.

Am Turnierrechner **Spielregeln bearbeiten** wählen. Titel, Überschriften und Regeltexte lassen sich direkt eintippen. Das leere Feldpaar am Ende fügt einen Abschnitt hinzu. Zum Entfernen eines Abschnitts beide Felder leeren. **Spielregeln speichern** aktualisiert die Anzeige und das PDF. **Abbrechen** verwirft den Entwurf. Eine Textänderung verändert die Berechnung der Turnierwertung oder die Grenzen der Ergebniseingabe nicht.

Die bearbeiteten Regeln gehören zur Datenbanksicherung. Beim Wiederherstellen gilt die Fassung aus der Sicherung. Bei einer älteren Sicherung ohne eigenen Regeltext erscheint das mitgelieferte Regelblatt.

**Sicherung erstellen**

Unter **Datensicherung** auf **Sicherung erstellen** klicken. Die Meldung nennt die neue Datei. Unter **Gespeicherte Sicherungen** steht die zuletzt gespeicherte Datei oben. Bei hochgeladenen Dateien bezeichnet das Datum den Speicherzeitpunkt auf diesem Rechner. Über **Herunterladen** lässt sich eine Kopie auf einem weiteren Datenträger ablegen.

Beim ersten Start mit einer älteren Datenbank prüft die Anwendung die Sitzplätze und Ergebnisse. Fehlt zu einem Ergebnis der passende Sitzplatz oder zu einem Sitzplatz die Anmeldung oder Runde, bricht die Umstellung ab. Die Meldung nennt Turnier, Runde, Tisch und Teilnehmer-ID. Diese Angaben mit dem ursprünglichen Sitzplan vergleichen. Die Prüfung gilt auch bei der Wiederherstellung einer älteren Sicherung.

**Sicherung wiederherstellen**

Unter **Gespeicherte Sicherungen** bei der gewünschten Datei **Verwalten → Wiederherstellen** wählen. Die Rückfrage nennt die Datei, die den aktiven Datenstand ersetzen soll. **Abbrechen** schließt die Rückfrage.

Für eine externe `.sqlite3`-Datei **Sicherungsdatei vom Rechner verwenden** öffnen. **Datei ablegen** speichert sie im Sicherungsordner. **Datei wiederherstellen** prüft sie und spielt sie nach der Bestätigung ein.

Enthält ein abgeschlossenes Turnier eine fehlerhafte Wertung, zeigt die Anwendung je betroffenem Turnier den ersten Befund mit Runde und Tisch, soweit vorhanden. „Abbrechen“ erhält den aktiven Bestand. Mit „Sicherung trotzdem einspielen“ übernimmt die Turnierleitung ausdrücklich die angezeigte Sicherung einschließlich der beanstandeten Werte. Vor dem Ersetzen legt die Anwendung im Datenordner eine Kopie des bisherigen Stands an. Offene Turniere lassen sich auch mit ausstehenden Tischergebnissen wiederherstellen.

**Datenbank prüfen**

Unter **Hilfe → DB-Diagnose** stehen die Prüfungen zur Datenbankstruktur und zu den Verknüpfungen aller Turniere. Im geöffneten Turnier zeigt **Hilfe → DB-Diagnose (dieses Turnier)** zusätzlich dessen Teilnehmer, Runden, Sitzplätze und Einzelergebnisse. Bei einem auffälligen Prüfstatus sind die **Technischen Details** bereits geöffnet.

**Adressen pflegen**

Im **Adressbuch** führen die Reiter **Adressen**, **Statistik** und **CSV-Import** zu den drei Ansichten des Standardadressbuchs. Die Adressliste startet mit den höchstens 80 zuletzt bearbeiteten Adressen. Für die alphabetische Liste **Alle Adressen** wählen und **Suchen** drücken.

- Über die Suche findest du Personen nach Name, Wohnort, Kontaktdaten oder Adressnummer mit vorangestelltem `#`. **Weitere Filter** öffnet unter anderem die Auswahl für fehlende Telefonnummern und E-Mail-Adressen.
- Den Namen anklicken, um eine Adresse zu bearbeiten. **Speichern** und **Abbrechen** führen zur vorherigen Liste oder Turnieransicht. **Einladung an/aus** lässt sich direkt in der Liste umschalten.
- **CSV herunterladen** bietet den Export aller Treffer der aktuellen Suche und den Export des gesamten Standardadressbuchs für Tabellen. In der **Statistik** öffnen die Zahlen unter **Einladungen** und **Datenpflege** die passenden Trefferlisten.

- Tischblätter, Wertungen und Turnierexporte verwenden Namen und Wohnort aus dem aktuellen Adressbuch. Eine Korrektur gilt auch für Nachdrucke abgeschlossener Turniere.
- Bei neuen Adressen ist „Einladung aktiv“ voreingestellt, auch bei der Schnellerfassung im Turnier. Im Adressbuch lässt sich das Kennzeichen ändern.
- In der CSV-Spalte `invite` bedeutet `1` „an“. `0` und ein leeres Feld bedeuten „aus“. Fehlt die ganze Spalte, behalten bekannte Adress-IDs ihr Kennzeichen. Zeilen ohne bekannte ID erhalten „an“.
- Bei einer Turnieranmeldung oder einer früheren Teilnahme ist „Löschen“ gesperrt. „Deaktivieren“ setzt eine aktive Adresse auf inaktiv und erhält ihre Teilnahmehistorie.
- Zum CSV-Import eine UTF-8-Datei mit Semikolon und den Spalten `nachname`, `vorname` und `wohnort` auswählen und „Datei prüfen“ anklicken. Die Vorschau zählt gültige und fehlerhafte Zeilen. Sie nennt die Zeile, wenn ein Eintrag korrigiert werden muss.
- „Standardadressbuch ersetzen“ übernimmt die geprüfte Datei. Eine reine Kopfzeile oder eine Datei mit fehlerhaften Zeilen wird abgewiesen. Bei vorhandenen Turnieranmeldungen im Standardadressbuch steht die Vorschau zur Verfügung, das Ersetzen ist gesperrt.
- Bei früheren Teilnahmen ohne aktuelle Anmeldung auf der Importseite „Adressbuch für Wiederimport (CSV)“ als Vorlage verwenden. Die Personen müssen unter ihrer bisherigen ID enthalten sein. Weggelassene Teilnahmespalten werden für diese Personen aus dem bestehenden Adressbuch übernommen.

## 2. Turnier öffnen
1.	Menü „Turniere“
2.	Gewünschtes Turnier anklicken
3.	Übersicht prüfen:
    - Titel
    - Datum / Uhrzeit
    - Anzahl Teilnehmer
    - Anzahl Tische

Über **Turniere → Neues Turnier** öffnest du das Formular. Unter **Veranstaltung** sind Turniername, Datum und Beginn erforderlich. Unter **Teilnehmerzahl und Marker** kannst du die Teilnehmergrenzen und den Turniermarker eintragen. Ein fehlerhafter Marker wird am Feld erklärt. Die übrigen Angaben bleiben zur Korrektur erhalten. Mit **Speichern** öffnest du die Turnierübersicht.

Beim Anlegen und Bearbeiten ist der Marker optional. Für den Abschluss braucht jedes Turnier einen eigenen Marker aus dem Veranstaltungsdatum im Format JJMMTT und vier weiteren Buchstaben oder Ziffern. Für den 12. November 2026 ist etwa `261112ABCD` gültig. Bei einem belegten Marker zeigt das Formular Nummer und Namen des zugehörigen Turniers.

Beim Löschen eines offenen Turniers werden seine Anmeldungen, Runden, Ergebnisse und das Teilnehmerprotokoll gemeinsam entfernt. Die Adressen bleiben im Adressbuch.

## 3. Teilnehmer erfassen

Im Turnier **Teilnehmer** öffnen. Oben stehen die Zahl aller Anmeldungen, die aktiven und abgemeldeten Spieler sowie die möglichen Vierertische.

- **Aus dem Adressbuch anmelden.** Unter „Person anmelden“ nach Name oder Wohnort suchen. Beim passenden Treffer „Anmelden“ wählen. Die Person erhält automatisch eine freie Startnummer.
- **Neue Person anlegen.** Unter der Adressbuchsuche „Neue Person anlegen“ öffnen und Nachname, Vorname und Wohnort eintragen. „Anlegen und anmelden“ speichert die Adresse und meldet die Person zum Turnier an.
- **Angemeldete Person finden.** Im Suchfeld der Teilnehmerliste nach Startnummer, Name oder Wohnort filtern. Die Auswahl „Abgemeldete“ zeigt die Spieler, die für weitere Auslosungen abgemeldet wurden. Unter „Aktionen“ stehen die für die Startnummer verfügbaren Funktionen und die Spielerkarte.
- **Änderungen nachvollziehen.** Unter der Liste das Teilnehmerprotokoll aufklappen. Dort lässt sich auch nach einer Begründung suchen.

## 4. Startnummern prüfen

Unter „Startnummern verwalten“ zeigt „Lücken prüfen“ die freien Nummern. „Alle neu nummerieren“ ordnet die Startnummern lückenlos. Für eine teilweise Neuvergabe die erste betroffene Nummer eingeben und „Ab dieser Nummer ordnen“ wählen.

## 5. Teilnehmer abmelden, entfernen oder tauschen

**Wenn ein Spieler das Turnier vorzeitig verlässt**

Unter „Teilnehmer“ in der Zeile des Spielers „Aktionen“ öffnen, „Für weitere Auslosungen abmelden“ wählen und bestätigen. Die Teilnehmerliste zeigt dann „Abgemeldet“. Die Startnummer, bereits ausgeloste Sitzplätze und alle erspielten Punkte und Soli bleiben erhalten. Für einen bereits ausgelosten Tisch können die Ergebnisse weiterhin eingetragen werden.

Jede spätere Auslosung berücksichtigt nur aktive Spieler. Das gilt für Zufall, Setzliste und die erneute Auslosung einer Runde ohne Ergebnisse. Fehlt ein Spieler für einen vollständigen Vierertisch, eine Ersatzperson als neuen Teilnehmer mit eigener Startnummer anmelden.

Teilnehmer entfernen
- Entfernen ist möglich, solange für die Anmeldung kein Sitzplatz und kein Ergebnis vorliegt.
- Button „Entfernen“
- Nummer bleibt frei (keine automatische Renummerierung)

Teilnehmer ersetzen oder tauschen
- Unter „Aktionen“ den Eintrag „Ersetzen oder tauschen“ öffnen und die Zielperson wählen. Bei einem Tausch behalten die Startnummern ihre Sitzplätze. Der neue Name gilt für alle bisherigen Sitzpläne dieser Anmeldung.
- Sobald eine der beteiligten Anmeldungen ein Ergebnis hat oder abgemeldet ist, ist der Tausch gesperrt.
- Vor dem Abschluss zeigt die Turnierübersicht den ersten Tisch mit fehlenden oder fehlerhaften Angaben. Jeder Tisch braucht die Sitze A bis D, vier passende Ergebnisse, eine Punktesumme von null und gültige Soli (ganze Zahlen ab null).

**Von der Turnierübersicht zum Tisch**

„Teilnehmer“, „Runden & Ergebnisse“, „Gesamtwertung“ und „Druck & Export“ öffnen die Bereiche des gewählten Turniers. In der Übersicht öffnet „Tisch … erfassen“ das Formular des ausstehenden Tisches. „Speichern“ führt zur Übersicht mit dem neuen Stand zurück. „Speichern & nächster Tisch“ folgt der Tischreihenfolge. Sind alle Tische gültig gewertet, führt „Runde … vorbereiten“ zum Sitzplan für die nächste Auslosung.

**Falsche Person nach der ersten Wertung berichtigen**

1. Unter „Teilnehmer“ bei der betroffenen Startnummer „Aktionen“ öffnen und **Anmeldung berichtigen** wählen. Startnummer, bisherige Person, Punkte und Soli mit dem Tischblatt vergleichen.
2. Die Person auswählen, die von Anfang an unter dieser Nummer gespielt hat. Sie braucht einen Adressbucheintrag und darf in diesem Turnier keine zweite Startnummer haben.
3. Den Grund eintragen und **Berichtigung speichern** wählen. Gesamtwertung, Exporte und Nachdrucke zeigen die richtige Person. Das Teilnehmerprotokoll hält die bisherigen und die neuen Personalien, die Startnummer, den Wertungsstand und die Begründung fest.

Bei einem bereits abgeschlossenen Turnier zuerst den unter „Abgeschlossenes Turnier korrigieren“ beschriebenen Weg zum Wiederöffnen nutzen. Nach der Berichtigung das Turnier erneut abschließen.

## 6. Runden und Sitzpläne

Im Turnier **Runden & Ergebnisse** öffnen. Die aktuelle Runde zeigt alle Tische mit ihrem Stand. Frühere Runden stehen darunter. Pro Runde gibt es drei Ansichten: **Sitzplan**, **Tischergebnisse** und **Rundenwertung**.

- Unter **Zur Vorbereitung** zeigt die Anwendung die aktiven Teilnehmer und die möglichen Vierertische. Bei einer unvollständigen Besetzung führt ein Link zur Teilnehmerliste.
- Für Runde 1 die Zufallsauslosung bestätigen. Ab Runde 2 lässt sich unter „Auslosung für Runde X“ auch die Setzliste wählen, sobald alle vorherigen Runden gültig gewertet sind. Bei einer gesperrten Setzliste nennt der Hinweis Runde und Tisch.
- Im **Sitzplan** öffnet „Sitze“ die Sitzliste A bis D. Der Tischname führt direkt zum Eingabeformular. Nach dem ersten gespeicherten Ergebnis ist der Sitzplan der Runde gesperrt.
- Unter **Tischblätter und Auslosungsdaten** findest du die Tischblätter für Helfer und Turnierleitung. „Sitzplan drucken“ erstellt den alphabetischen Aushang.
- Im Sitzplan öffnet Umschalt+R eine bereits ausgeloste Folgerunde. In der letzten ausgelosten Runde fragt die Anwendung, ob sie die nächste Runde auslosen soll.

## 7. Druck & Export

- **Tischblätter für Helfer** zeigt die jüngste ausgeloste Runde. Für einen Nachdruck eine frühere Runde auswählen und **Anzeigen** drücken. **Sammeldokument (DOCX)** enthält die vollständigen Tische in einem Word-Dokument. **Einzeldateien (ZIP)** enthält eine Word-Datei je Tisch. Unter **Einzelnes Tischblatt nachdrucken** den gewünschten Tisch anklicken.
- **Sitzplan für Runde … öffnen** und **Rundenwertung öffnen** führen zu den Druckansichten der gewählten Runde. Dort den jeweiligen Druckknopf verwenden.
- **Listen für den Aushang** bietet das **Helferpaket (ZIP)** mit Startliste, Gesamtwertung, Sitzplänen und Rundenwertungen als CSV. Zur Spielerkennung dienen Startnummer, Vorname, Nachname und Wohnort. Die Gesamtwertung lässt sich auch direkt als CSV herunterladen oder über die Druckansicht aushängen.
- **Turnierleitung · mit Kontaktdaten** aufklappen, um den vollständigen Turnierexport oder Tischblätter mit E-Mail-Adresse zu erhalten. Der vollständige Export umfasst alle Runden und erfordert eine Bestätigung. Für interne Tischblätter gilt dieselbe Rundenauswahl wie für die Helferblätter.
- In Tabellen-CSV erhalten Textfelder mit Formelanfang das sichtbare Präfix `Text: `. Negative Punkte bleiben Zahlen. Für den unveränderten Austausch von Adressdaten die CSV „Adressbuch für Wiederimport“ auf der Importseite verwenden.

Die Anwendung erstellt die Word-Tischblätter für Einzelblatt, Sammeldokument und ZIP in `app/routes/tournaments/round_export_docx_merged.py`. Änderungen am Drucklayout werden dort vorgenommen.

**Dateien auf dem Turnierrechner ablegen**

Wer einen Download im Projektordner aufbewahrt, kann dafür den Unterordner `exports/` anlegen. Git ignoriert seinen Inhalt auch nach dem Umbenennen der Dateien. Ein Software-ZIP für die Weitergabe enthält nur Programmdateien, Vereinsgrafiken und Anleitungen. Der vollständige Projektordner kann zusätzlich Adressen, Sicherungen, Exporte und ältere Git-Versionen enthalten.

## 8. Ergebnisse eingeben

1. Unter „Runden & Ergebnisse“ die Runde und „Tischergebnisse“ öffnen.
2. Einen Tisch auswählen.
3.	Für jeden der 4 Spieler:
    - Punkte
	- Soli

Plausibilitätsprüfung
- Summe der Punkte muss 0 ergeben
- Die vier Spieler dürfen pro Tisch und Runde zusammen höchstens 40 Soli haben. Ein leeres Solifeld zählt als 0. Bei mehr als 40 Soli die Eingabe mit dem Tischblatt vergleichen und vor dem Speichern korrigieren.

Ein grüner Haken in der Tischübersicht und im Sitzplan bedeutet, dass alle vier Ergebnisse vorliegen und die Tischwertung gültig ist. Fehlen nur Ergebnisse, zählt der Tisch als offen. Bei fehlerhaften Werten erscheint „Wertung prüfen“ mit dem Grund.

Ist ein Tisch in zwei Fenstern geöffnet, prüft die Anwendung beim Speichern die Punkte und Soli aller vier Spieler. Hat sich der Tisch inzwischen geändert, zeigt sie die gespeicherten Werte und daneben die Eingabe aus dem älteren Fenster. Die Turnierleitung kann beide mit dem Tischblatt vergleichen und eine Korrektur im aktuellen Formular speichern.

## 9. Wertungen anzeigen & drucken

Rundenwertung
- Punkte & Soli nur dieser Runde
- Sortierung nach Platzierung

**Gesamtwertung**

Im Turnier **Gesamtwertung** öffnen. Die Rangliste zeigt Startnummer, Name und Wohnort, Gesamtpunkte und Soli. Über das Suchfeld findest du einen Spieler nach Startnummer, Name oder Wohnort. **Rundenwerte** zeigt die Punkte und Soli der einzelnen Runden, auch auf dem Handy.

Bei gleicher Punktzahl entscheiden die Soli. Stimmen auch die Soli überein, teilen sich die Spieler den Platz. Bei einem geteilten ersten Platz führt die Zusammenfassung im Turnierleitungs-ZIP alle Erstplatzierten auf.

Bei einer fehlerhaften Tischwertung zeigen die Gesamtwertung und die Exporte die berechenbaren Plätze als vorläufig an. Der Hinweis nennt den ersten betroffenen Tisch und den Grund. Die Turnierleitung erhält im ZIP keine Siegerangabe.

Ist ein Punkte- oder Soliwert unlesbar, bleibt der gespeicherte Einzelwert in der Rundenwertung sichtbar. Für die betroffene Summe zeigt die Gesamtwertung „nicht berechenbar“, im CSV bleibt das Feld leer. Ein Platz setzt eine berechenbare Punktzahl voraus. Bei Punktgleichheit müssen zusätzlich die Soli aller punktgleichen Spieler lesbar sein.

Mit **Gesamtwertung drucken** erhältst du die vollständige Rangliste mit Gesamtpunkten und Soli, auch wenn die Suche gerade nur einzelne Spieler zeigt. Auf dem Ausdruck stehen der Stand der Wertung und gegebenenfalls der Prüfhinweis.

**Spielerkarte öffnen und zurückkehren**

In der Gesamtwertung den Spielernamen anklicken. In der Teilnehmerliste eines offenen Turniers **Aktionen → Spielerkarte öffnen** wählen, bei einem abgeschlossenen Turnier den Namen anklicken. Auf der Karte führt **Zur Gesamtwertung** oder **Zur Teilnehmerliste** zur vorherigen Auswahl. Der Suchbegriff bleibt erhalten, in der Teilnehmerliste auch die Auswahl **Aktive** oder **Abgemeldete**. Das gilt ebenfalls nach dem Wechsel zur nächsten oder vorherigen Spielerkarte.

**Spielerkarte als PDF**

In der Gesamtwertung den Spielernamen öffnen und „PDF“ wählen. Im Einzelplatzbetrieb öffnet der QR-Code die Karte am Turnierrechner. Im WLAN-Betrieb enthält eine neu angezeigte oder gedruckte Karte einen persönlichen Link für das Handy. Wer diesen Link hat, kann die betreffende Karte lesen. Die Turnierleitung gibt jede Karte an den zugehörigen Spieler. Nach der Berichtigung einer falschen Anmeldung eine neue Karte ausgeben.

Die Spielerkarte zeigt am Rechner, am Handy und im PDF denselben Platz wie die Gesamtwertung. Bei einer vorläufigen Platzierung nennt sie den Prüfhinweis und lässt die Medaille weg. In der Rundenübersicht bleiben auch beanstandete Einzelwerte sichtbar.

### Ergebnisse durch Helfer im Vereins-WLAN

Den WLAN-Start für Mac oder Windows beschreibt die Installationsanleitung. Auf dem Turnierrechner die lokale Turnierliste öffnen und das offene Turnier wählen. Im Abschnitt **Helferzugang für dieses Turnier** stehen die Adresse und der Helfercode. Beides nur den Helfern dieses Turniers geben.

Am Helfergerät dasselbe Vereins-WLAN verwenden, die Adresse öffnen, den Code eingeben und Runde und Tisch wählen. Für jeden Spieler Punkte und Soli eintragen und speichern. Ein bereits erfasster Tisch lässt sich korrigieren. Die Punktesumme muss null sein, die vier Spieler dürfen zusammen höchstens 40 Soli haben. Hat jemand den Tisch inzwischen geändert, die aktuellen Werte vergleichen und die Korrektur erneut bestätigen.

Über **Helferbereich → Abmelden** endet die Sitzung auf dem Gerät. Mit dem Beenden der Anwendung endet der WLAN-Zugriff. Beim nächsten WLAN-Start bekommt jedes Turnier einen neuen Helfercode. Nach dem Wiederherstellen einer Sicherung den neuen Helfercode und neue Spielerkarten ausgeben.

## 10. Tastatur-Shortcuts (wichtig!)

Allgemein
- Alt + N → „Neue Person anlegen“
- Esc → „Neue Person anlegen“ schließen

Teilnehmerliste
- ↑ / ↓ → Auswahl bewegen
- Shift + Entf → Teilnehmer entfernen

Turnierübersicht
- Tab → zur nächsten Schaltfläche oder zum nächsten Link
- Enter → Auswahl öffnen
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

Bei einem abgeschlossenen Turnier führt **Öffnen** in der Turnierliste zu den Wertungen und Druckunterlagen. **Bearbeiten** und **Löschen** stehen bei offenen Turnieren zur Verfügung. Für eine beabsichtigte Korrektur den lokalen Wartungsmodus wie in der Installationsanleitung starten. In der Turnieransicht **Turnier wieder öffnen** wählen und die Rückfrage bestätigen. Die erfassten Ergebnisse bleiben erhalten. Die Anwendung nimmt den Teilnahmemarker dieses Turniers bei den angemeldeten Personen zurück.

Für die Reparatur der Teilnahmehistorie ist **Nur Vorschau** vorbelegt. Nach der Bestätigung zeigt die Anwendung die Anzahl der betroffenen Adressen. Zum Speichern das Häkchen entfernen, den gewünschten Adressumfang wählen und bestätigen. Die auf ein einzelnes Turnier begrenzte Reparatur steht bei offenen Turnieren zur Verfügung.

**Viel Erfolg & Gut Blatt!** 🍀
