import csv
import unittest
from contextlib import closing
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from lxml import etree, html

from app import db
from app.services.spreadsheet_csv import spreadsheet_csv
from app.web import create_app


def records(data):
    return list(csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""), delimiter=";"))


def document_text(data):
    with ZipFile(BytesIO(data)) as docx:
        root = etree.fromstring(docx.read("word/document.xml"))
    return " ".join(root.xpath("//w:t/text()", namespaces={"w": root.nsmap["w"]}))


class HelperExportTests(unittest.TestCase):
    base = "/tournaments/1"
    round = base + "/rounds/1"
    contact = "probe@example.invalid"
    family = '=Muster;"West"'

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        self.client = create_app(db_path=path / "probe.sqlite3", backup_dir=path / "backups").test_client()
        with closing(db.connect()) as con, con:
            con.execute("INSERT INTO tournaments(title,event_date,start_time,organizer) VALUES('Spiel','2026-11-13','18:00','Intern-Organisation')")
            con.execute("INSERT INTO tournament_rounds(tournament_id,round_no) VALUES(1,1)")
            for nr, (first, seat, points) in enumerate(zip(("Ada","Bea","Carl","Dora"), "ABCD", (30,-10,-10,-10)), 1):
                con.execute("""INSERT INTO addresses(addressbook_id,nachname,vorname,wohnort,plz,ort,strasse,hausnummer,telefon,email,notizen)
                               VALUES(1,?,?,'Oberdorf','12345','Privatort','Musterweg','4B','+49 123',?,'Vertraulich')""",
                            (self.family, first, self.contact))
                con.execute("INSERT INTO tournament_participants(tournament_id,player_no,address_id,display_name) VALUES(1,?,?,?)", (nr,nr,first))
                con.execute("INSERT INTO tournament_seats(tournament_id,round_no,table_no,seat,tp_id) VALUES(1,1,1,?,?)", (seat,nr))
                con.execute("INSERT INTO tournament_scores(tournament_id,round_no,table_no,tp_id,points) VALUES(1,1,1,?,?)", (nr,points))

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response

    def test_helper_archive_limits_columns_and_preserves_scores_and_ranks(self):
        response = self.get(self.base + "/helfer.zip")
        self.assertIn("Helfer", response.headers["Content-Disposition"])
        player = {"nr","nachname","vorname","wohnort"}
        allowed = {
            "02_startliste.csv": player, "01_gesamtwertung.csv": player | {"platz","punkte","soli"},
            "sitzplan/R01.csv": player | {"runde","tisch","sitz"},
            "rundenwertung/R01.csv": player | {"platz","punkte","soli","tisch","sitz"},
        }
        with ZipFile(BytesIO(response.data)) as z:
            self.assertEqual(set(z.namelist()), set(allowed))
            for filename, fields in allowed.items():
                payload = z.read(filename)
                self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
                parsed = records(payload)
                self.assertEqual(len(parsed), 4)
                self.assertEqual(set(parsed[0]), fields)
                self.assertEqual({r["nachname"] for r in parsed}, {"Text: " + self.family})
                self.assertEqual({r["wohnort"] for r in parsed}, {"Oberdorf"})
                if "platz" in fields:
                    self.assertEqual([(r["platz"], r["punkte"]) for r in parsed], [("1","30"), ("2","-10"), ("2","-10"), ("2","-10")])
                for private in (self.contact, "Privatort", "Musterweg", "4B", "+49 123", "Intern-Organisation", "Vertraulich"):
                    self.assertNotIn(private, payload.decode("utf-8-sig"))

    def test_internal_zip_retains_contacts_and_spreadsheet_text_protection(self):
        response = self.get(self.base + "/export.zip")
        self.assertIn("Turnierleitung", response.headers["Content-Disposition"])
        with ZipFile(BytesIO(response.data)) as z:
            signup = records(z.read("02_teilnehmer_anmeldung.csv"))[0]
            self.assertEqual((signup["email"], signup["telefon"], signup["strasse"]), (self.contact, "Text: +49 123", "Musterweg"))
            for filename in ("01_gesamtwertung.csv","04_scores_komplett.csv","05_pivot_vorlage_scores.csv"):
                rows = records(z.read(filename))
                self.assertTrue(all(r["nachname"]=="Text: "+self.family for r in rows))
                self.assertIn("-10",[r["punkte"] for r in rows])
        self.assertEqual(records(self.get(self.base + "/export.csv").data)[0]["nachname"], "Text: "+self.family)

    def test_email_only_in_explicit_internal_docx_for_every_download(self):
        for tail in ("tablesheets-docx/1", "tablesheets-docx-merged", "tablesheets-docx-zip"):
            path = self.round + "/" + tail
            for profile in ("", "?ausgabe=helfer", "?ausgabe=leitung"):
                with self.subTest(tail=tail, profile=profile):
                    internal = profile.endswith("leitung")
                    response = self.get(path + profile)
                    prefix = "Turnierleitung" if internal else "Helfer"
                    self.assertIn(prefix, response.headers["Content-Disposition"])
                    docx = response.data
                    if tail.endswith("-zip"):
                        with ZipFile(BytesIO(docx)) as archive:
                            name, = archive.namelist()
                            self.assertTrue(name.startswith(prefix))
                            docx = archive.read(name)
                    text = document_text(docx)
                    self.assertIn("Oberdorf", text)
                    self.assertIn("Teiln.-Nr. 1", text)
                    self.assertEqual(self.contact in text, internal)
                    self.assertEqual("E-Mail:" in text, internal)
            for invalid in ("?ausgabe=andere", "?ausgabe=helfer&ausgabe=leitung"):
                self.assertEqual(self.client.get(path + invalid).status_code, 400)

    def test_soli_fields_follow_total_on_second_page(self):
        response = self.get(self.round + "/tablesheets-docx/1")
        with ZipFile(BytesIO(response.data)) as archive:
            document = etree.fromstring(archive.read("word/document.xml"))
        ns = {"w": document.nsmap["w"]}
        first, second = document.xpath("//w:body/w:tbl", namespaces=ns)
        label = "Anzahl der Soli je Spieler"
        self.assertNotIn(label, first.xpath(".//w:t/text()", namespaces=ns))
        total, soli = second.xpath("./w:tr", namespaces=ns)[-2:]
        self.assertEqual(total.xpath("./w:tc[1]//w:t/text()", namespaces=ns), ["Gesamt"])
        self.assertEqual(soli.xpath(".//w:t/text()", namespaces=ns), [label])
        fields = soli.xpath("./w:tc[position() > 1]", namespaces=ns)
        self.assertEqual(len(fields), 4)
        for field in fields:
            self.assertEqual(field.xpath("./w:tcPr/w:gridSpan/@w:val", namespaces=ns), ["2"])

    def test_download_choices_and_address_interchange(self):
        page = html.fromstring(self.get(self.base).get_data(as_text=True))
        for tail in ("/helfer.zip","/export.zip"):
            self.assertTrue(page.xpath("//a[@href=$url]",url=self.base+tail))
        for path, number in ((self.round,2),(self.round+"/results/1",1)):
            page = html.fromstring(self.get(path).get_data(as_text=True))
            self.assertEqual(len(page.xpath("//a[contains(@href,'ausgabe=helfer')]")), number)
            self.assertEqual(len(page.xpath("//details//a[contains(@href,'ausgabe=leitung')]")), number)
        for path in ("/addresses/export-filtered?view=all","/addresses/export-invites","/addresses/export-quality"):
            row = records(self.get(path).data)[0]
            self.assertEqual((row["nachname"],row["telefon"]), ("Text: "+self.family, "Text: +49 123"))
        raw = records(self.get("/addresses/export").data)[0]
        self.assertEqual((raw["nachname"],raw["telefon"]), (self.family, "+49 123"))

    def test_csv_keeps_multiline_text_in_one_cell_and_negative_integers_numeric(self):
        cases = ["=abc","+Text","-Text","@Name","\t =ABC","\ufeff＋ABC","＝123"]
        other = 'Name;"Ort"\rmit\nUmbruch'
        text = spreadsheet_csv([cases+[other,-10,0,None]])
        parsed, = csv.reader(StringIO(text, newline=""), delimiter=";")
        self.assertEqual(parsed, ["Text: "+v for v in cases]+[other,"-10","0",""])
