"""Die vereinsweit geltenden Spielregeln für Anzeige, Bearbeitung und PDF."""
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from .. import db

bp = Blueprint("rules", __name__, url_prefix="/hilfe/spielregeln")

DEFAULT_RULES = {
    "title": "Spielregeln der SF Bieswang",
    "closing": "Wir wünschen viel Spaß und ein gutes Blatt. Eure Sportfreunde Bieswang",
    "sections": [
        ["Runden und Karten",
         "Das Turnier umfasst zwei Runden mit je 40 Spielen. Gespielt wird mit der langen Karte. "
         "Es muss nachgemischt werden. Jeder Spieler erhält in der üblichen Reihenfolge zweimal vier Karten."],
        ["Spielwahl und Ansagen",
         "Zugelassen sind Rufspiel, Solo, Wenz, Tout und „Sie“. Solo hat Vorrang vor Wenz. "
         "Ein Herzsolo hat keinen Vorrang.\n"
         "Sagt niemand ein Spiel an, wird über Kreuz gespielt. Der Ausspieler ist Spieler.\n"
         "Untendurch ist erlaubt, wenn Ruf-Ass zu 4.\n"
         "Im 1. Spiel darf kein Tout angesagt werden. Ein Tout muss unter Aufsicht gespielt werden. Kontra ist ausgeschlossen."],
        ["Augen und Laufende",
         "Die Spielerpartei gewinnt ab 61 Augen und ist ab 31 Augen schneiderfrei. "
         "Die Gegenpartei gewinnt ab 60 Augen und ist ab 30 Augen schneiderfrei.\n"
         "Laufende zählen ab drei. Bei Rufspiel und Solo zählen bis zu acht Ober und Unter, beim Wenz nur die vier Unter. "
         "Bei einem Partnerspiel gewinnen oder verlieren beide Partner die Laufenden gemeinsam."],
        ["Punkte und Tischliste",
         "Einfaches Spiel · 1 Punkt, mit Schneider 2 Punkte, mit Schwarz 3 Punkte.\n"
         "Solo und Wenz · 4 Punkte, mit Schneider 5 Punkte, mit Schwarz 6 Punkte.\n"
         "Jeder zählende Laufende bringt 1 Punkt. Ein Tout zählt doppelt.\n"
         "Gewonnene Punkte mit Pluszeichen und verlorene Punkte mit Minuszeichen eintragen. "
         "Die Listen dürfen aufaddiert werden. Soli müssen eingekreist werden."],
        ["Spielfehler und Ausschluss",
         "Bei falschem Zugeben oder falschem Anspiel endet das Spiel. Wer den Fehler macht, erhält alle Minuspunkte. "
         "Ein bereits gewonnenes Spiel kann nicht mehr verloren werden. Maßgeblich ist der Spielstand beim Fehler. "
         "Bei einem Fehler an der ersten Karte wird das Spiel als „Schneider Schwarz“ gewertet.\n"
         "Wer absichtlich falsch spielt oder gegen die Spielregeln verstößt, wird ausgeschlossen. Das Startgeld wird nicht erstattet."],
        ["Gesamtwertung und Ehrenpreis",
         "Bei gleicher Punktzahl liegt vorn, wer mehr Soli gespielt hat. Sind auch die Soli gleich, teilen sich die Spieler den Platz.\n"
         "Den Ehrenpreis erhält, wer mit seiner Gesamtpunktzahl den geringsten Abstand zu null hat. "
         "Haben mehrere Teilnehmer denselben geringsten Abstand, entscheidet das Los. Einen Trostpreis gibt es nicht."],
    ],
}


def _read_rules(con):
    row = db.one(con, "SELECT v FROM meta WHERE k='spielregeln'")
    return json.loads(row["v"]) if row else DEFAULT_RULES


def _revision(con, rules):
    # Nach einer Wiederherstellung müssen auch bereits geöffnete Formulare neu geladen werden.
    state = json.dumps([db.wlan_generation(con), rules], ensure_ascii=False)
    return sha256(state.encode()).hexdigest()


@bp.get("")
def view():
    with closing(db.connect()) as con:
        rules = _read_rules(con)
    return render_template("help/rules.html", title=rules["title"], rules=rules, section="regeln")


@bp.get("/pdf")
def pdf():
    try:
        from weasyprint import HTML
    except (ImportError, OSError):
        abort(503, description="Für das Regelblatt werden WeasyPrint und seine Systembibliotheken benötigt.")
    with closing(db.connect()) as con:
        rules = _read_rules(con)
    page = render_template("help/rules_pdf.html", rules=rules)
    response = send_file(BytesIO(HTML(string=page).write_pdf()), mimetype="application/pdf",
                         as_attachment=True, download_name="Spielregeln.pdf")
    response.cache_control.no_store = True
    return response


@bp.route("/bearbeiten", methods=["GET", "POST"])
def edit():
    error = ""
    status = 200
    with closing(db.connect()) as con, con:
        con.execute("BEGIN IMMEDIATE" if request.method == "POST" else "BEGIN")
        rules = _read_rules(con)
        revision = _revision(con, rules)
        if request.method == "POST":
            titles = request.form.getlist("heading")
            texts = request.form.getlist("text")
            sections = [[heading.strip(), text.replace("\r\n", "\n").replace("\r", "\n").strip()]
                        for heading, text in zip(titles, texts) if heading.strip() or text.strip()]
            rules = {"title": request.form.get("title", "").strip(), "sections": sections,
                     "closing": request.form.get("closing", "").strip()}
            if request.form.get("revision") != revision:
                error = "Die gespeicherte Fassung hat sich geändert. Kopiere bei Bedarf deinen Entwurf und öffne die aktuelle Fassung."
                revision = request.form.get("revision", "")
                status = 409
            elif (len(titles) != len(texts) or not rules["title"] or not sections
                  or any(not heading or not text for heading, text in sections)):
                error = "Bitte gib einen Titel und mindestens einen Abschnitt an. Jeder Abschnitt braucht eine Überschrift und einen Text."
                status = 422
            else:
                con.execute("INSERT INTO meta(k,v) VALUES('spielregeln',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                            (json.dumps(rules, ensure_ascii=False),))
                flash("Spielregeln gespeichert. Das PDF enthält die neue Fassung.", "ok")
                return redirect(url_for("rules.view"), code=303)
    return render_template("help/rules_edit.html", title="Spielregeln bearbeiten", section="regeln",
                           rules=rules, revision=revision, error=error, conflict=status == 409), status
