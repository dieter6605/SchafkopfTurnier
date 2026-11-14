from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from lxml import html

from app import db
from app.web import create_app


class TournamentMarkerTests(unittest.TestCase):
    new = "/tournaments/new"

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = create_app(db_path=Path(tmp.name) / "probe.sqlite3")
        self.client = self.app.test_client()

    def fields(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        return dict(html.fromstring(response.get_data(as_text=True)).get_element_by_id("tForm").fields)

    def save(self, path, **changes):
        fields = self.fields(self.client, path)
        fields.update(changes)
        return self.client.post(path, data=fields)

    def create(self, title, marker):
        response = self.save(self.new, event_date="2026-11-12", title=title, marker=marker)
        self.assertEqual(response.status_code, 302)
        return response.location

    def rows(self):
        with closing(db.connect()) as con:
            return list(map(tuple, con.execute("SELECT title, marker FROM tournaments ORDER BY id")))

    def test_new_and_edit_have_named_controls_and_contextual_cancel(self):
        detail = self.create("Vereinsabend", "")
        for path, heading, back in ((self.new, "Neues Turnier", "/tournaments"),
                                    (detail + "/edit", "Turnier bearbeiten", detail)):
            with self.subTest(path=path):
                page = html.fromstring(self.client.get(path).get_data(as_text=True))
                self.assertEqual(page.xpath("//main//h1/text()"), [heading])
                form = page.get_element_by_id("tForm")
                for field in form.xpath(".//input[not(@type='hidden')] | .//textarea"):
                    self.assertEqual(len(form.xpath(".//label[@for=$id]", id=field.get("id"))), 1)
                self.assertEqual(form.xpath(".//*[@required]/@name"), ["title", "event_date", "start_time"])
                self.assertEqual(form.xpath(".//*[@autofocus]/@name"), ["title"])
                self.assertEqual(page.get_element_by_id("btnBack").get("href"), back)
                self.assertEqual(form.xpath('.//a[.="Abbrechen"]/@href'), [back])

    def test_invalid_marker_preserves_draft_on_create_and_edit_until_corrected(self):
        detail = self.create("Vorhandenes Turnier", "")
        for path, expected in ((self.new, "/tournaments/2"), (detail + "/edit", detail)):
            with self.subTest(path=path):
                drafts = dict(self.fields(self.client, path), title="Probe " + expected,
                              event_date="2026-11-14", start_time="18:30", marker="X",
                              location="Vereinssaal", organizer="Verein", description="Herbstturnier",
                              min_participants="4", max_participants="16")
                before = self.rows()
                response = self.client.post(path, data=drafts)
                self.assertEqual(response.status_code, 422)
                page = html.fromstring(response.get_data(as_text=True))
                form = page.get_element_by_id("tForm")
                self.assertEqual(dict(form.fields), drafts)
                self.assertEqual(form.xpath(".//*[@autofocus and @aria-invalid='true']/@name"), ["marker"])
                self.assertIn("marker-error", form.get_element_by_id("marker").get("aria-describedby").split())
                self.assertIn("10 Zeichen", form.get_element_by_id("marker-error").text_content())
                self.assertEqual(self.rows(), before)
                retry = dict(form.fields, marker="261114TEST" if path == self.new else "")
                self.assertEqual(self.client.post(path, data=retry).location, expected)
                saved = self.fields(self.client, expected + "/edit")
                for field in retry.keys() - {"csrf_token"}:
                    self.assertEqual(saved[field], retry[field])

    def test_missing_required_values_are_identified_on_the_returned_form(self):
        fields = dict(self.fields(self.client, self.new), title="Probe", event_date="2026-11-14", location="Saal")
        for key in ("title", "event_date", "start_time"):
            with self.subTest(field=key):
                response = self.client.post(self.new, data=dict(fields, **{key: ""}))
                self.assertEqual(response.status_code, 422)
                form = html.fromstring(response.get_data(as_text=True)).get_element_by_id("tForm")
                self.assertEqual(form.xpath(".//*[@autofocus and @aria-invalid='true']/@name"), [key])
                self.assertEqual(form.get_element_by_id(key).get("aria-describedby"), key + "-error")
                self.assertEqual(form.get_element_by_id(key + "-error").get("role"), "alert")
                self.assertEqual(form.fields["location"], "Saal")
        self.assertEqual(self.rows(), [])

    def test_duplicate_create_names_closed_owner_and_preserves_form_for_retry(self):
        owner_title = 'Herbst <Finale> & Freunde'
        owner = self.create(owner_title, "261112ABCD")
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET closed_at='2026-11-12 21:00' WHERE id=1")
        fields = dict(self.fields(self.client, self.new), title="Zweites Turnier", marker=" 261112 abcd ",
                      event_date="2026-11-12", start_time="17:45", location="Saal", organizer="Verein",
                      description="Zweite Serie", min_participants="4", max_participants="12")
        response = self.client.post(self.new, data=fields)
        self.assertEqual(response.status_code, 409)
        page = html.fromstring(response.get_data(as_text=True))
        link = page.get_element_by_id("marker-error").find("a")
        self.assertEqual(link.get("href"), owner)
        self.assertIn(owner_title, link.text)
        self.assertEqual(len(link), 0)
        self.assertEqual(self.rows(), [(owner_title, "261112ABCD")])
        retry = dict(page.get_element_by_id("tForm").fields)
        self.assertEqual(retry, dict(fields, marker="261112ABCD"))
        retry["marker"] = "261112ABCE"
        self.assertEqual(self.client.post(self.new, data=retry).location, "/tournaments/2")
        self.assertEqual(self.rows()[-1], ("Zweites Turnier", "261112ABCE"))

    def test_edit_keeps_own_marker_and_rejects_another_tournaments_marker(self):
        first = self.create("Erstes", "261112ABCD")
        second = self.create("Zweites", "2611120042")
        own = self.save(first + "/edit", title="Erstes neu", marker="261112 aBcD")
        self.assertEqual(own.location, first)
        conflict = self.save(first + "/edit", title="Entwurf", marker="2611120042")
        self.assertEqual(conflict.status_code, 409)
        page = html.fromstring(conflict.get_data(as_text=True))
        self.assertEqual(page.get_element_by_id("btnBack").get("href"), first)
        self.assertEqual(page.get_element_by_id("marker-error").find("a").get("href"), second)
        self.assertEqual(self.rows(), [("Erstes neu", "261112ABCD"), ("Zweites", "2611120042")])

    def test_existing_marker_identifies_owner_even_if_form_date_differs(self):
        owner = self.create("Frühere Veranstaltung", "261112ABCD")
        form = self.save(self.new, title="Anderer Tag", event_date="2026-11-13", marker="261112ABCD")
        self.assertEqual(form.status_code, 409)
        page = html.fromstring(form.get_data(as_text=True))
        self.assertEqual(page.get_element_by_id("marker-error").find("a").get("href"), owner)
        self.assertEqual(page.get_element_by_id("tForm").fields["event_date"], "2026-11-13")
        self.assertEqual(self.rows(), [("Frühere Veranstaltung", "261112ABCD")])

    def test_empty_markers_and_releasing_an_open_tournaments_marker(self):
        first = self.create("Erstes", "")
        second = self.create("Zweites", "   ")
        self.assertEqual(self.rows(), [("Erstes", None), ("Zweites", None)])
        self.assertEqual(self.save(first+"/edit", marker="261112ABCD").location, first)
        self.assertEqual(self.save(first+"/edit", marker="").location, first)
        self.assertEqual(self.save(second+"/edit", marker="261112ABCD").location, second)
        self.assertEqual(self.rows(), [("Erstes", None), ("Zweites", "261112ABCD")])

    def test_legacy_spelling_and_existing_duplicates_can_be_corrected_individually(self):
        first = self.create("Alt", "261112ABCD")
        second = self.create("Weiteres", "")
        legacy = "261112\u00a0aB cD"
        with closing(db.connect()) as con, con:
            con.execute("UPDATE tournaments SET marker=?", (legacy,))
        rejected = self.save(self.new, title="Neu", event_date="2026-11-12", marker="261112ABCD")
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.rows(), [("Alt", legacy), ("Weiteres", legacy)])
        self.assertEqual(self.save(second+"/edit", marker="261112EFGH").location, second)
        self.assertEqual(self.rows(), [("Alt", legacy), ("Weiteres", "261112EFGH")])
        self.assertEqual(self.save(first+"/edit", marker="261112ABCD").location, first)

    def test_only_one_parallel_save_gets_the_marker(self):
        first = self.create("Bestand A", "")
        second = self.create("Bestand B", "")

        def race(paths, marker):
            barrier = Barrier(2)
            def claim(index):
                client = self.app.test_client()
                path = paths[index]
                fields = dict(self.fields(client, path), event_date="2026-11-12",
                              title=f"Versuch {index}", marker=marker)
                barrier.wait(timeout=5)
                return client.post(path, data=fields)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(claim, (0, 1)))
            self.assertEqual(sorted(r.status_code for r in responses), [302, 409])
            winner = next(r.location for r in responses if r.status_code == 302)
            loser = next(r for r in responses if r.status_code == 409)
            page = html.fromstring(loser.get_data(as_text=True))
            self.assertEqual(page.get_element_by_id("marker-error").find("a").get("href"), winner)
            self.assertEqual(sum(code == marker for _, code in self.rows()), 1)

        race((self.new, self.new), "261112NEW1")
        race((first+"/edit", self.new), "261112MIX1")
        race((first+"/edit", second+"/edit"), "261112EDIT")
