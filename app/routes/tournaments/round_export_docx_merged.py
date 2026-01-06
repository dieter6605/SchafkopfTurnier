# app/routes/tournaments/round_export_docx_merged.py
from __future__ import annotations

from dataclasses import dataclass
import io

from flask import flash, redirect, send_file, url_for

from ... import db
from . import bp
from .helpers import _get_tournament


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class SeatInfo:
    seat: str            # "A" | "B" | "C" | "D"
    player_no: int       # Teilnehmernummer
    display_name: str    # Anzeigename
    email: str | None    # E-Mail (kann fehlen)


@dataclass(frozen=True)
class TableInfo:
    table_no: int
    seats: dict[str, SeatInfo]  # keys: "A","B","C","D"


# -----------------------------------------------------------------------------
# Helpers: fetch table data
# -----------------------------------------------------------------------------
def _fetch_round_tables(con, tournament_id: int, round_no: int) -> list[TableInfo]:
    rows = db.q(
        con,
        """
        SELECT
          s.table_no,
          s.seat,
          tp.player_no,
          tp.display_name,
          a.nachname,
          a.vorname,
          a.wohnort,
          a.email
        FROM tournament_seats s
        JOIN tournament_participants tp ON tp.id = s.tp_id
        JOIN addresses a ON a.id = tp.address_id
        WHERE s.tournament_id = ? AND s.round_no = ?
        ORDER BY
          s.table_no ASC,
          CASE s.seat
            WHEN 'A' THEN 1
            WHEN 'B' THEN 2
            WHEN 'C' THEN 3
            WHEN 'D' THEN 4
            ELSE 9
          END
        """,
        (int(tournament_id), int(round_no)),
    )

    by_table: dict[int, dict[str, SeatInfo]] = {}
    for r in rows:
        tno = int(r["table_no"])
        seat = str(r["seat"] or "").strip().upper()

        disp = (r["display_name"] or "").strip()
        if not disp:
            nn = (r["nachname"] or "").strip()
            vn = (r["vorname"] or "").strip()
            wo = (r["wohnort"] or "").strip()
            disp = f"{nn}, {vn}"
            if wo:
                disp += f" · {wo}"

        si = SeatInfo(
            seat=seat,
            player_no=int(r["player_no"] or 0),
            display_name=disp,
            email=(str(r["email"]).strip() if r["email"] else None),
        )
        by_table.setdefault(tno, {})
        by_table[tno][seat] = si

    out: list[TableInfo] = []
    for tno in sorted(by_table.keys()):
        seats = by_table[tno]
        if not all(k in seats for k in ("A", "B", "C", "D")):
            continue
        out.append(TableInfo(table_no=tno, seats=seats))
    return out


# -----------------------------------------------------------------------------
# DOCX builder (no template)
# -----------------------------------------------------------------------------
def _build_merged_docx(*, tournament_title: str, round_no: int, tables: list[TableInfo]) -> bytes:
    from docx import Document
    from docx.shared import Pt, Cm, Mm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()

    # --- Page setup ---
    sec = doc.sections[0]
    sec.top_margin = Mm(12)
    sec.bottom_margin = Mm(12)
    sec.left_margin = Mm(10)
    sec.right_margin = Mm(10)

    # header/footer distances + clear content
    try:
        sec.header_distance = Mm(0)
        sec.footer_distance = Mm(0)
    except Exception:
        pass
    try:
        sec.different_first_page_header_footer = False
    except Exception:
        pass
    for p in sec.header.paragraphs:
        p.text = ""
    for p in sec.footer.paragraphs:
        p.text = ""

    # --- Global default font: Source Sans Pro, 12pt ---
    style = doc.styles["Normal"]
    style.font.name = "Source Sans Pro"
    style.font.size = Pt(12)
    try:
        pf = style.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.0
    except Exception:
        pass

    # Column widths (9 columns)
    w0 = Cm(1.6)
    w = Cm(2.2)
    col_widths = [w0] + [w] * 8

    # Shades
    SHADE_HEADER = "F2F2F2"
    SHADE_LIGHT = "FAFAFA"
    SHADE_SUM = "F7F7F7"
    SHADE_BOTTOM = "F2F2F2"
    SHADE_WHITE = "FFFFFF"

    # -------- low-level XML helpers --------
    def _rgb(color: str | None) -> RGBColor | None:
        if not color:
            return None
        c = str(color).strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) != 6:
            return None
        try:
            return RGBColor.from_string(c.upper())
        except Exception:
            return None

    def _set_cell_shading(cell, *, fill: str) -> None:
        c = str(fill or "").strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) != 6:
            return
        tcPr = cell._tc.get_or_add_tcPr()
        shd = tcPr.find(qn("w:shd"))
        if shd is None:
            shd = OxmlElement("w:shd")
            tcPr.append(shd)
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), c.upper())

    def _set_table_cell_margins(tbl, *, top_tw: int = 60, bottom_tw: int = 60, left_tw: int = 90, right_tw: int = 90) -> None:
        tblPr = tbl._tbl.tblPr
        mar = tblPr.find(qn("w:tblCellMar"))
        if mar is None:
            mar = OxmlElement("w:tblCellMar")
            tblPr.append(mar)

        def _set(tag: str, val: int):
            el = mar.find(qn(f"w:{tag}"))
            if el is None:
                el = OxmlElement(f"w:{tag}")
                mar.append(el)
            el.set(qn("w:w"), str(int(val)))
            el.set(qn("w:type"), "dxa")

        _set("top", top_tw)
        _set("bottom", bottom_tw)
        _set("left", left_tw)
        _set("right", right_tw)

    def _set_table_borders(tbl, *, outer_pt: float = 2.0, inner_pt: float = 1.0) -> None:
        outer_sz = str(int(round(outer_pt * 8)))  # 2pt -> 16
        inner_sz = str(int(round(inner_pt * 8)))  # 1pt -> 8

        tblPr = tbl._tbl.tblPr
        borders = tblPr.find(qn("w:tblBorders"))
        if borders is None:
            borders = OxmlElement("w:tblBorders")
            tblPr.append(borders)

        def _border(tag: str, sz: str):
            el = borders.find(qn(f"w:{tag}"))
            if el is None:
                el = OxmlElement(f"w:{tag}")
                borders.append(el)
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), sz)
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), "000000")

        _border("top", outer_sz)
        _border("left", outer_sz)
        _border("bottom", outer_sz)
        _border("right", outer_sz)
        _border("insideH", inner_sz)
        _border("insideV", inner_sz)

    def _set_cell_borders(cell, *, left=None, right=None, top=None, bottom=None) -> None:
        """
        Each side may be a dict: {"sz": int_twips8, "val": "dashed"/"single", "color": "000000"}
        sz is in eighths of a point (w:sz).
        """
        tcPr = cell._tc.get_or_add_tcPr()
        tcBorders = tcPr.find(qn("w:tcBorders"))
        if tcBorders is None:
            tcBorders = OxmlElement("w:tcBorders")
            tcPr.append(tcBorders)

        def _apply(side: str, spec):
            if spec is None:
                return
            el = tcBorders.find(qn(f"w:{side}"))
            if el is None:
                el = OxmlElement(f"w:{side}")
                tcBorders.append(el)
            el.set(qn("w:val"), spec.get("val", "single"))
            el.set(qn("w:sz"), str(int(spec.get("sz", 8))))
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), spec.get("color", "000000"))

        _apply("left", left)
        _apply("right", right)
        _apply("top", top)
        _apply("bottom", bottom)

    # -------- text helpers --------
    def _fit_text_lines(text: str, *, max_chars_per_line: int, max_lines: int) -> list[str]:
        t = " ".join((text or "").split())
        if not t:
            return [""]
        words = t.split(" ")
        lines: list[str] = []
        cur = ""
        for w in words:
            cand = w if not cur else (cur + " " + w)
            if len(cand) <= max_chars_per_line:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = w
                if len(lines) >= max_lines:
                    break
        if len(lines) < max_lines and cur:
            lines.append(cur)

        joined = " ".join(lines)
        if len(joined) < len(t):
            last = lines[-1]
            if len(last) >= max_chars_per_line:
                last = last[: max(0, max_chars_per_line - 1)]
            lines[-1] = last.rstrip(".") + "…"
        return lines[:max_lines]

    def _auto_header_sizes(name: str, email: str) -> tuple[int, int]:
        n = len((name or "").strip())
        e = len((email or "").strip())
        name_pt = 11
        email_pt = 9
        if n > 42:
            name_pt = 10
        if n > 60:
            name_pt = 9
        if e > 32:
            email_pt = 8
        if e > 45:
            email_pt = 7
        return name_pt, email_pt

    def _set_cell_paragraph(
        cell,
        text: str,
        *,
        align,
        pt: int = 12,
        bold: bool = False,
        italic: bool = False,
        color: str = "",
    ):
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = align
        try:
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
        except Exception:
            pass
        run = p.add_run(text)
        run.font.name = "Source Sans Pro"
        run.font.size = Pt(pt)
        run.bold = bool(bold)
        run.italic = bool(italic)
        rgb = _rgb(color)
        if rgb is not None:
            run.font.color.rgb = rgb

    def _clear_cell(cell) -> None:
        cell.text = ""
        # ensure at least one paragraph exists
        if not cell.paragraphs:
            cell.add_paragraph()

    def _set_header_cell(
        cell,
        *,
        seat: str,
        player_no: int,
        name: str,
        email: str,
    ) -> None:
        """
        Header cell content:
          'Platz X — Nr' in one line, then Name, then 'E-Mail: xxx' left-aligned.
        All vertically TOP aligned.
        """
        _clear_cell(cell)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP

        name_pt, email_pt = _auto_header_sizes(name, email)
        name_lines = _fit_text_lines(name, max_chars_per_line=28, max_lines=2)
        email_lines = _fit_text_lines(email, max_chars_per_line=26, max_lines=2)

        # line 1: Platz X — <Nr>
        p1 = cell.paragraphs[0]
        p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            p1.paragraph_format.space_before = Pt(0)
            p1.paragraph_format.space_after = Pt(0)
            p1.paragraph_format.line_spacing = 1.0
        except Exception:
            pass

        r1 = p1.add_run(f"Platz {seat} — {int(player_no)}")
        r1.bold = True
        r1.font.name = "Source Sans Pro"
        r1.font.size = Pt(12)

        # line 2: Name (center)
        p2 = cell.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            p2.paragraph_format.space_before = Pt(0)
            p2.paragraph_format.space_after = Pt(0)
            p2.paragraph_format.line_spacing = 1.0
        except Exception:
            pass
        r2 = p2.add_run("\n".join(name_lines).strip())
        r2.font.name = "Source Sans Pro"
        r2.font.size = Pt(name_pt)

        # line 3: E-Mail: <...> (left, smaller)
        p3 = cell.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.LEFT
        try:
            p3.paragraph_format.space_before = Pt(0)
            p3.paragraph_format.space_after = Pt(0)
            p3.paragraph_format.line_spacing = 1.0
        except Exception:
            pass

        r3a = p3.add_run("E-Mail: ")
        r3a.font.name = "Source Sans Pro"
        r3a.font.size = Pt(email_pt)
        rgb = _rgb("666666")
        if rgb is not None:
            r3a.font.color.rgb = rgb

        r3b = p3.add_run("\n".join(email_lines).strip())
        r3b.font.name = "Source Sans Pro"
        r3b.font.size = Pt(email_pt)
        rgb2 = _rgb("444444")
        if rgb2 is not None:
            r3b.font.color.rgb = rgb2

    def _shade_row(row, *, fill: str) -> None:
        for c in row.cells:
            _set_cell_shading(c, fill=fill)

    def _merge_pairs(row):
        row.cells[1].merge(row.cells[2])
        row.cells[3].merge(row.cells[4])
        row.cells[5].merge(row.cells[6])
        row.cells[7].merge(row.cells[8])

    def _apply_dashed_internal_separators(tbl) -> None:
        """
        Set vertical dashed 0.5pt separators between:
          (2|3), (4|5), (6|7), (8|9)  => 0-based boundaries: between 1|2, 3|4, 5|6, 7|8
        Use cell borders: right border on left cell and left border on right cell.
        """
        dashed = {"val": "dashed", "sz": 4, "color": "000000"}  # 0.5pt -> 4
        boundaries = [(1, 2), (3, 4), (5, 6), (7, 8)]
        for row in tbl.rows:
            for a, b in boundaries:
                try:
                    _set_cell_borders(row.cells[a], right=dashed)
                    _set_cell_borders(row.cells[b], left=dashed)
                except Exception:
                    # some merged rows might not have expected cells; ignore
                    pass

    def _apply_table_layout(tbl, *, page_no: int):
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.style = "Table Grid"

        for row in tbl.rows:
            for ci, cell in enumerate(row.cells):
                cell.width = col_widths[ci]
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        _set_table_cell_margins(tbl, top_tw=60, bottom_tw=60, left_tw=90, right_tw=90)

        # Heights (EXACT) based on previous tuned values, with requested reductions
        # Base values from previous version:
        header_h = 22.0 * 1.2                # first row +20%
        plusminus_h = 6.5                    # already reduced before
        carry_h_page2 = 10.5                 # was +50%, now needs -20%
        game_h = 8.5
        sum_h = 9.0 * 1.5                    # currently +50%, then -10% (last two rows)
        bottom_h = 9.0 * 1.5                 # same, then -10%

        # Apply requested deltas:
        # - Plus rows 20% lower
        plusminus_h *= 0.8                   # -20%
        # - "Übertrag" row on page 2 (row index 1) 20% lower
        carry_h_page2 *= 0.8                 # -20%
        # - page1 row2 (plus row) and page2 row3 (plus row) already addressed by plusminus_h
        # - last two rows each table 10% lower
        sum_h *= 0.9
        bottom_h *= 0.9
        # - email line can be a bit tighter: achieved by font sizes and TOP alignment

        heights_mm: dict[int, float] = {}

        if page_no == 1:
            # 24 rows: [0 header], [1 plus], [2..21 games], [22 sum], [23 bottom]
            heights_mm[0] = header_h
            heights_mm[1] = plusminus_h
            for rix in range(2, 22):
                heights_mm[rix] = game_h
            heights_mm[22] = sum_h
            heights_mm[23] = bottom_h
        else:
            # 25 rows: [0 header], [1 carry], [2 plus], [3..22 games], [23 sum], [24 bottom]
            heights_mm[0] = header_h
            heights_mm[1] = carry_h_page2
            heights_mm[2] = plusminus_h
            for rix in range(3, 23):
                heights_mm[rix] = game_h
            heights_mm[23] = sum_h
            heights_mm[24] = bottom_h

        for rix, row in enumerate(tbl.rows):
            row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
            row.height = Mm(heights_mm.get(rix, game_h))

        # Make sure font + spacing are compact everywhere
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    try:
                        p.paragraph_format.space_before = Pt(0)
                        p.paragraph_format.space_after = Pt(0)
                        p.paragraph_format.line_spacing = 1.0
                    except Exception:
                        pass
                    for run in p.runs:
                        run.font.name = "Source Sans Pro"

        # borders: outer 2pt, inner 1pt
        _set_table_borders(tbl, outer_pt=2.0, inner_pt=1.0)

        # dashed separators 0.5pt on specified vertical boundaries
        _apply_dashed_internal_separators(tbl)

    # -------- row fillers --------
    def _fill_header_row(tbl, table: TableInfo):
        _merge_pairs(tbl.rows[0])
        _shade_row(tbl.rows[0], fill=SHADE_HEADER)

        seat_order = ["A", "B", "C", "D"]
        start_cells = [tbl.rows[0].cells[1], tbl.rows[0].cells[3], tbl.rows[0].cells[5], tbl.rows[0].cells[7]]

        for seat, cell in zip(seat_order, start_cells):
            s = table.seats[seat]
            _set_header_cell(
                cell,
                seat=seat,
                player_no=int(s.player_no),
                name=str(s.display_name or ""),
                email=str(s.email or ""),
            )

    def _fill_carry_in_row(tbl, *, row_index: int):
        row = tbl.rows[row_index]
        _merge_pairs(row)
        _shade_row(row, fill=SHADE_LIGHT)

        # smaller text, left aligned, TOP aligned
        c0 = row.cells[0]
        c0.vertical_alignment = WD_ALIGN_VERTICAL.TOP
        _set_cell_paragraph(c0, "Übertrag", align=WD_ALIGN_PARAGRAPH.LEFT, pt=10, color="444444")
        for c in [row.cells[1], row.cells[3], row.cells[5], row.cells[7]]:
            c.text = ""
            c.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _fill_plusminus_row(tbl, *, row_index: int):
        row = tbl.rows[row_index]
        _merge_pairs(row)
        _shade_row(row, fill=SHADE_LIGHT)

        cells = [row.cells[1], row.cells[3], row.cells[5], row.cells[7]]
        for c in cells:
            c.text = ""
            c.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
            except Exception:
                pass
            run = p.add_run("Plus+ / -Minus")
            run.italic = True
            run.font.name = "Source Sans Pro"
            run.font.size = Pt(10)
            rgb = _rgb("666666")
            if rgb is not None:
                run.font.color.rgb = rgb

    def _fill_games_and_totals(
        tbl,
        *,
        start_row: int,
        start_game_no: int,
        sum_row: int,
        bottom_row: int,
        bottom_label: str,
        bottom_label_pt: int,
    ):
        for i in range(20):
            rix = start_row + i
            game_no = start_game_no + i

            # zebra
            _shade_row(tbl.rows[rix], fill=("FCFCFC" if i % 2 == 1 else SHADE_WHITE))

            # left numbering
            tbl.rows[rix].cells[0].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            _set_cell_paragraph(tbl.rows[rix].cells[0], str(game_no), align=WD_ALIGN_PARAGRAPH.RIGHT, pt=12)

            for ci in range(1, 9):
                c = tbl.rows[rix].cells[ci]
                c.text = ""
                p = c.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Summe
        sumr = tbl.rows[sum_row]
        _shade_row(sumr, fill=SHADE_SUM)
        _set_cell_paragraph(sumr.cells[0], "Summe", align=WD_ALIGN_PARAGRAPH.RIGHT, pt=12, bold=True)
        for ci in range(1, 9):
            sumr.cells[ci].text = ""
            sumr.cells[ci].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Bottom row
        bot = tbl.rows[bottom_row]
        _shade_row(bot, fill=SHADE_BOTTOM)
        _set_cell_paragraph(
            bot.cells[0],
            bottom_label,
            align=WD_ALIGN_PARAGRAPH.RIGHT,
            pt=bottom_label_pt,
            bold=True if bottom_label == "Gesamt" else False,
            color="444444" if bottom_label != "Gesamt" else "",
        )
        _merge_pairs(bot)
        for c in [bot.cells[1], bot.cells[3], bot.cells[5], bot.cells[7]]:
            c.text = ""
            c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _add_tablesheet_page(table: TableInfo, *, page_no: int):
        # Title line (bigger)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        try:
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.0
        except Exception:
            pass

        title_text = f"{tournament_title}  Runde {int(round_no)}  Tisch {int(table.table_no)}  ·  Seite {page_no}/2"
        r = p.add_run(title_text)
        r.font.name = "Source Sans Pro"
        r.font.size = Pt(16)
        r.bold = True

        if page_no == 1:
            tbl = doc.add_table(rows=24, cols=9)
            _apply_table_layout(tbl, page_no=1)
            _fill_header_row(tbl, table)
            _fill_plusminus_row(tbl, row_index=1)
            _fill_games_and_totals(
                tbl,
                start_row=2,
                start_game_no=1,
                sum_row=22,
                bottom_row=23,
                bottom_label="Übertrag",
                bottom_label_pt=10,
            )
        else:
            tbl = doc.add_table(rows=25, cols=9)
            _apply_table_layout(tbl, page_no=2)
            _fill_header_row(tbl, table)
            _fill_carry_in_row(tbl, row_index=1)
            _fill_plusminus_row(tbl, row_index=2)
            _fill_games_and_totals(
                tbl,
                start_row=3,
                start_game_no=21,
                sum_row=23,
                bottom_row=24,
                bottom_label="Gesamt",
                bottom_label_pt=12,
            )

    # Build document
    for idx, table in enumerate(tables):
        _add_tablesheet_page(table, page_no=1)
        doc.add_page_break()
        _add_tablesheet_page(table, page_no=2)
        if idx < len(tables) - 1:
            doc.add_page_break()

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _safe_filename(s: str) -> str:
    s2 = "".join(ch for ch in (s or "") if ch.isalnum() or ch in (" ", "-", "_")).strip()
    s2 = s2.replace(" ", "_")
    return s2 or "Turnier"


# -----------------------------------------------------------------------------
# Route: merged tablesheets DOCX (no template)
# -----------------------------------------------------------------------------
@bp.get("/tournaments/<int:tournament_id>/rounds/<int:round_no>/tablesheets-docx-merged")
def tournament_round_tablesheets_docx_merged(tournament_id: int, round_no: int):
    try:
        import docx  # noqa: F401
    except ModuleNotFoundError:
        flash("DOCX-Export nicht verfügbar: Paket 'python-docx' ist nicht installiert.", "error")
        return redirect(url_for("tournaments.tournament_round_view", tournament_id=tournament_id, round_no=round_no))

    with db.connect() as con:
        t = _get_tournament(con, tournament_id)
        if not t:
            flash("Turnier nicht gefunden.", "error")
            return redirect(url_for("tournaments.tournaments_list"))

        tables = _fetch_round_tables(con, tournament_id, round_no)
        if not tables:
            flash(f"Keine vollständigen 4er-Tische für Runde {round_no} gefunden (Auslosung fehlt?).", "error")
            return redirect(url_for("tournaments.tournament_round_view", tournament_id=tournament_id, round_no=round_no))

        title = str(t["title"] or "").strip() or "Turnier"

    payload = _build_merged_docx(tournament_title=title, round_no=int(round_no), tables=tables)

    fn = f"{_safe_filename(title)}_R{int(round_no):02d}_Tische.docx"
    return send_file(
        io.BytesIO(payload),
        as_attachment=True,
        download_name=fn,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        max_age=0,
    )