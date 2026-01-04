# app/routes/tournaments/pages.py
from __future__ import annotations

from flask import Response, flash, redirect, render_template, request, url_for

from ... import db
from . import bp
from .helpers import (
    _get_tournament,
    _guard_closed_redirect,
    _now_local_iso,
    _read_tournament_form,
    _tournament_counts,
    _validate_tournament_form,
)


@bp.get("/tournaments")
def tournaments_list():
    with db.connect() as con:
        rows = db.q(
            con,
            """
            SELECT
              t.*,

              -- Teilnehmer
              (SELECT COUNT(*) FROM tournament_participants tp WHERE tp.tournament_id=t.id) AS participant_count,

              -- Runden
              (SELECT COUNT(*) FROM tournament_rounds tr WHERE tr.tournament_id=t.id) AS rounds_count,

              -- Scores (alle Einträge)
              (SELECT COUNT(*) FROM tournament_scores sc WHERE sc.tournament_id=t.id) AS scores_count,

              -- expected_scores = participants * rounds
              (
                (SELECT COUNT(*) FROM tournament_participants tp WHERE tp.tournament_id=t.id)
                *
                (SELECT COUNT(*) FROM tournament_rounds tr WHERE tr.tournament_id=t.id)
              ) AS expected_scores,

              -- Status-Felder als 0/1
              CASE WHEN COALESCE(TRIM(t.marker),'') <> '' THEN 1 ELSE 0 END AS marker_ok,
              CASE WHEN COALESCE(TRIM(t.closed_at),'') <> '' THEN 1 ELSE 0 END AS is_closed,

              -- scores_complete: nur wahr, wenn es überhaupt Runden gibt und expected == scores
              CASE
                WHEN (
                  (SELECT COUNT(*) FROM tournament_rounds tr WHERE tr.tournament_id=t.id)
                ) <= 0 THEN 0
                WHEN (
                  (SELECT COUNT(*) FROM tournament_scores sc WHERE sc.tournament_id=t.id)
                ) = (
                  (SELECT COUNT(*) FROM tournament_participants tp WHERE tp.tournament_id=t.id)
                  *
                  (SELECT COUNT(*) FROM tournament_rounds tr WHERE tr.tournament_id=t.id)
                )
                THEN 1 ELSE 0
              END AS scores_complete,

              -- "zuletzt geändert" (für Liste)
              COALESCE(t.updated_at, t.created_at) AS last_update

            FROM tournaments t
            ORDER BY t.event_date DESC, t.start_time DESC, t.id DESC
            """,
        )
    return render_template("tournaments.html", tournaments=rows, now=_now_local_iso())


@bp.get("/tournaments/new")
def tournament_new():
    defaults = {
        "title": "",
        "event_date": "",
        "start_time": "19:00",
        "marker": "",
        "location": "",
        "organizer": "",
        "description": "",
        "min_participants": 0,
        "max_participants": 0,
    }
    return render_template(
        "tournament_form.html",
        t=defaults,
        mode="new",
        back_url=url_for("tournaments.tournaments_list"),
    )


@bp.post("/tournaments/new")
def tournament_create():
    data = _read_tournament_form()
    err = _validate_tournament_form(data)
    if err:
        flash(err, "error")
        return redirect(url_for("tournaments.tournament_new"))

    with db.connect() as con:
        con.execute(
            """
            INSERT INTO tournaments(
              title, event_date, start_time,
              marker,
              location, organizer, description,
              min_participants, max_participants,
              created_at, updated_at
            )
            VALUES (?,?,?,?, ?,?,?,?, ?, datetime('now'), datetime('now'))
            """,
            (
                data["title"],
                data["event_date"],
                data["start_time"],
                data["marker"],
                data["location"],
                data["organizer"],
                data["description"],
                data["min_participants"],
                data["max_participants"],
            ),
        )
        con.commit()
        tid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    flash("Turnier angelegt.", "ok")
    return redirect(url_for("tournaments.tournament_detail", tournament_id=tid))


@bp.get("/tournaments/<int:tournament_id>")
def tournament_detail(tournament_id: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        counts = _tournament_counts(con, tournament_id)

        rounds = db.q(
            con,
            "SELECT round_no FROM tournament_rounds WHERE tournament_id=? ORDER BY round_no ASC",
            (tournament_id,),
        )
        round_list = [int(r["round_no"]) for r in rounds]
        last_round_no = max(round_list) if round_list else 0
        next_round_no = (last_round_no + 1) if last_round_no > 0 else 1

        fk_on = int(con.execute("PRAGMA foreign_keys;").fetchone()[0] or 0)
        integrity = str(con.execute("PRAGMA integrity_check;").fetchone()[0] or "")
        fk_issues = db.q(con, "PRAGMA foreign_key_check;")
        fk_issues_count = len(fk_issues)

        participants_count = int(
            (db.one(con, "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id=?", (tournament_id,)) or {"c": 0})[
                "c"
            ]
            or 0
        )
        rounds_count = int(
            (db.one(con, "SELECT COUNT(*) AS c FROM tournament_rounds WHERE tournament_id=?", (tournament_id,)) or {"c": 0})[
                "c"
            ]
            or 0
        )
        scores_count = int(
            (db.one(con, "SELECT COUNT(*) AS c FROM tournament_scores WHERE tournament_id=?", (tournament_id,)) or {"c": 0})["c"]
            or 0
        )
        expected_scores = participants_count * rounds_count

        divisible_by_4 = (participants_count > 0) and (participants_count % 4 == 0)
        marker_ok = bool((t["marker"] or "").strip())

        safe = {
            "fk_on": fk_on,
            "integrity": integrity,
            "fk_issues_count": fk_issues_count,
            "participants": participants_count,
            "rounds": rounds_count,
            "scores": scores_count,
            "expected_scores": expected_scores,
            "divisible_by_4": divisible_by_4,
            "marker_ok": marker_ok,
            "scores_complete": (expected_scores == scores_count) if rounds_count > 0 else False,
        }
        safe["ready"] = (
            safe["fk_on"] == 1
            and safe["integrity"].lower() == "ok"
            and safe["fk_issues_count"] == 0
            and safe["rounds"] > 0
            and safe["divisible_by_4"]
            and safe["scores_complete"]
        )

    return render_template(
        "tournament_detail.html",
        t=t,
        counts=counts,
        now=_now_local_iso(),
        last_round_no=last_round_no,
        round_list=round_list,
        next_round_no=next_round_no,
        safe=safe,
    )


@bp.get("/tournaments/<int:tournament_id>/edit")
def tournament_edit(tournament_id: int):
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Bearbeiten",
            endpoint="tournaments.tournament_detail",
            endpoint_kwargs={"tournament_id": tournament_id},
        )
        if resp:
            return resp

    return render_template(
        "tournament_form.html",
        t=t,
        mode="edit",
        back_url=url_for("tournaments.tournament_detail", tournament_id=tournament_id),
    )


@bp.post("/tournaments/<int:tournament_id>/edit")
def tournament_update(tournament_id: int):
    data = _read_tournament_form()
    err = _validate_tournament_form(data)
    if err:
        flash(err, "error")
        return redirect(url_for("tournaments.tournament_edit", tournament_id=tournament_id))

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Speichern",
            endpoint="tournaments.tournament_detail",
            endpoint_kwargs={"tournament_id": tournament_id},
        )
        if resp:
            return resp

        con.execute(
            """
            UPDATE tournaments
            SET title=?,
                event_date=?,
                start_time=?,
                marker=?,
                location=?,
                organizer=?,
                description=?,
                min_participants=?,
                max_participants=?,
                updated_at=datetime('now')
            WHERE id=?
            """,
            (
                data["title"],
                data["event_date"],
                data["start_time"],
                data["marker"],
                data["location"],
                data["organizer"],
                data["description"],
                data["min_participants"],
                data["max_participants"],
                tournament_id,
            ),
        )
        con.commit()

    flash("Turnier gespeichert.", "ok")
    return redirect(url_for("tournaments.tournament_detail", tournament_id=tournament_id))


@bp.get("/tournaments/diagnostics")
@bp.get("/tournaments/<int:tournament_id>/diagnostics")
def tournaments_diagnostics(tournament_id: int | None = None):
    with db.connect() as con:
        fk_on = int(con.execute("PRAGMA foreign_keys;").fetchone()[0] or 0)
        integrity = str(con.execute("PRAGMA integrity_check;").fetchone()[0] or "")

        fk_issues = db.q(con, "PRAGMA foreign_key_check;")
        issues = []
        for r in fk_issues:
            issues.append({"table": r["table"], "rowid": r["rowid"], "parent": r["parent"], "fkid": r["fkid"]})

        t = None
        counts = None
        extras = {}
        if tournament_id is not None:
            t = _get_tournament(con, int(tournament_id))
            if t:
                counts = _tournament_counts(con, int(tournament_id))
                extras["rounds"] = int(
                    (db.one(con, "SELECT COUNT(*) AS c FROM tournament_rounds WHERE tournament_id=?", (tournament_id,)) or {"c": 0})[
                        "c"
                    ]
                    or 0
                )
                extras["seats"] = int(
                    (db.one(con, "SELECT COUNT(*) AS c FROM tournament_seats WHERE tournament_id=?", (tournament_id,)) or {"c": 0})[
                        "c"
                    ]
                    or 0
                )
                extras["scores"] = int(
                    (db.one(con, "SELECT COUNT(*) AS c FROM tournament_scores WHERE tournament_id=?", (tournament_id,)) or {"c": 0})[
                        "c"
                    ]
                    or 0
                )

    title = "DB-Diagnose – Turniere" if tournament_id is None else f"DB-Diagnose – Turnier #{tournament_id}"
    back = url_for("tournaments.tournaments_list") if tournament_id is None else url_for("tournaments.tournament_detail", tournament_id=tournament_id)

    html = []
    html.append("<!doctype html><html><head><meta charset='utf-8'>")
    html.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    html.append("<title>DB-Diagnose</title>")
    html.append(
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;padding:16px}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:6px}"
        "th{text-align:left;background:#f6f6f6}.ok{color:#0a7}.bad{color:#c00}.muted{color:#666}</style>"
    )
    html.append("</head><body>")
    html.append(f"<h2>{title}</h2>")
    html.append(f"<p><a href='{back}'>← Zurück</a></p>")

    html.append("<h3>Prüfstatus</h3>")
    html.append("<ul>")
    html.append(f"<li>PRAGMA foreign_keys: <b class='{ 'ok' if fk_on==1 else 'bad' }'>{fk_on}</b></li>")
    html.append(f"<li>PRAGMA integrity_check: <b class='{ 'ok' if integrity.lower()=='ok' else 'bad' }'>{integrity}</b></li>")
    html.append(f"<li>foreign_key_check Probleme: <b class='{ 'ok' if len(issues)==0 else 'bad' }'>{len(issues)}</b></li>")
    html.append("</ul>")

    if tournament_id is not None:
        html.append("<h3>Turnier</h3>")
        if not t:
            html.append("<p class='bad'>Turnier nicht gefunden.</p>")
        else:
            html.append(
                f"<p><b>{t['title']}</b> · {t['event_date']} {t['start_time']} "
                f"<span class='muted'>(marker: {t['marker'] or '-'})</span></p>"
            )
            html.append("<ul>")
            html.append(f"<li>Teilnehmer: <b>{counts['participants'] if counts else 0}</b></li>")
            html.append(f"<li>Runden: <b>{extras.get('rounds',0)}</b></li>")
            html.append(f"<li>Sitzplan-Zeilen: <b>{extras.get('seats',0)}</b></li>")
            html.append(f"<li>Ergebnis-Zeilen: <b>{extras.get('scores',0)}</b></li>")
            html.append("</ul>")

    if issues:
        html.append("<h3>foreign_key_check – Details</h3>")
        html.append("<table><thead><tr><th>Tabelle</th><th>rowid</th><th>Parent</th><th>fkid</th></tr></thead><tbody>")
        for it in issues:
            html.append(f"<tr><td>{it['table']}</td><td>{it['rowid']}</td><td>{it['parent']}</td><td>{it['fkid']}</td></tr>")
        html.append("</tbody></table>")
    else:
        html.append("<p class='ok'><b>Keine FK-Probleme gefunden.</b></p>")

    html.append("</body></html>")
    return Response("\n".join(html), mimetype="text/html")


@bp.post("/tournaments/<int:tournament_id>/delete")
def tournament_delete(tournament_id: int):
    """
    Löscht das Turnier. Alle abhängigen Datensätze (participants/rounds/seats/scores)
    werden via ON DELETE CASCADE automatisch entfernt.
    """
    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        resp = _guard_closed_redirect(
            t,
            action="Löschen",
            endpoint="tournaments.tournament_detail",
            endpoint_kwargs={"tournament_id": tournament_id},
        )
        if resp:
            return resp

        con.execute("DELETE FROM tournaments WHERE id=?", (tournament_id,))
        con.commit()

    flash("Turnier gelöscht.", "ok")
    return redirect(url_for("tournaments.tournaments_list"))