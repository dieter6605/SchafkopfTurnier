from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lxml import html

from app.web import create_app


class HelpTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = create_app(db_path=Path(folder.name) / "test.sqlite3")
        app.testing = True
        self.client = app.test_client()

    def page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.get_data(as_text=True))

    def test_help_menu_and_overview_reach_every_section(self):
        sections = {"/hilfe/spielregeln", "/hilfe/anleitung", "/hilfe/installation", "/hilfe/readme", "/hilfe/tarifrechner"}
        overview = self.page("/hilfe")
        self.assertEqual(set(overview.xpath("//main//a/@href")), sections)
        menu = self.page("/").xpath('//button[normalize-space(.)="Hilfe"]/following-sibling::ul')[0]
        self.assertTrue(sections.union({"/hilfe", "/tournaments/diagnostics"}) <= set(menu.xpath(".//a/@href")))
        for section in sections:
            with self.subTest(section=section):
                page = self.page(section)
                self.assertEqual(len(page.xpath("//main//h1")), 1)
                nav = page.xpath('//nav[@aria-label="Hilfebereiche"]')[0]
                self.assertEqual(nav.xpath('.//a[@aria-current="page"]/@href'), [section])
                self.assertEqual(set(nav.xpath(".//a/@href")), sections.union({"/hilfe"}))
                for asset in page.xpath("//script[@src]/@src | //link[@rel='stylesheet']/@href"):
                    self.assertTrue(asset.startswith("/static/"))
                    self.assertEqual(self.client.get(asset, buffered=True).status_code, 200)

    def test_chapters_match_visible_document_headings_and_old_bookmarks(self):
        for doc in ("anleitung", "installation", "readme"):
            with self.subTest(doc=doc):
                page = self.page(f"/hilfe/{doc}")
                chapters = page.xpath('//nav[@aria-label="Kapitel"]//a')
                headings = page.xpath("//article//*[self::h2 or self::h3 or self::h4]")
                self.assertGreater(len(chapters), 5)
                self.assertEqual([c.get("href") for c in chapters], ["#" + h.get("id") for h in headings[1:]])
                self.assertEqual([c.text_content() for c in chapters], [h.text_content() for h in headings[1:]])
        for doc in ("readme", "anleitung"):
            response = self.client.get(f"/help/{doc}", follow_redirects=True)
            self.assertEqual(response.request.path, f"/hilfe/{doc}")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/hilfe/unbekannt").status_code, 404)

    def test_document_links_open_installation_and_current_tournament_list(self):
        readme = self.page("/hilfe/readme")
        target, = readme.xpath('//article//a[normalize-space(.)="Installationsanleitung"]/@href')
        install = self.page(target)
        self.assertIn("Turnier-starten.command", install.text_content())
        self.assertIn("Turnier-starten.cmd", install.text_content())
        tournaments, = set(install.xpath('//article//a[normalize-space(.)="die Turnierliste öffnen"]/@href'))
        self.assertEqual(tournaments, "/tournaments")
        self.page(tournaments)

    def test_tariff_defaults_and_accessible_result(self):
        page = self.page("/hilfe/tarifrechner")
        form = page.get_element_by_id("tarif-form")
        for field, value in (("rateSchneider", "0.10"), ("rateSauspiel", "0.20"), ("rateSolo", "0.50")):
            self.assertEqual(form.get_element_by_id(field).get("value"), value)
        for control in form.xpath(".//input | .//select"):
            self.assertTrue(form.xpath(".//label[@for=$id]", id=control.get("id")))
        self.assertEqual(form.xpath(".//button[@type='submit']/@id"), ["calcBtn"])
        self.assertEqual(page.get_element_by_id("output").get("aria-live"), "polite")
        self.assertTrue(page.get_element_by_id("breakdown").xpath("ancestor::details/summary"))
