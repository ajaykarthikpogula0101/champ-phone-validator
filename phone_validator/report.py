"""
Build downloadable reports (PDF, Word, CSV) from a validated table.

Each report has the same three parts:
  1. Summary: total, Validated, Not Validated (and how many of those need a live carrier check)
  2. Why numbers were not validated: reason -> count
  3. Every row: input, international format, status, line type, country, carrier, reason
"""
from __future__ import annotations

import io
from collections import Counter
from datetime import datetime
from html import escape
from typing import Optional

from .validate import TableResult, ValidationResult, write_csv

APP_NAME = "Champ Phone Validator"
LAYER_NOTE = (
    "Checks run offline on the numbering plan (format, line type, country, original carrier, duplicates). "
    "'Validated' means a well-formed mobile number; it does not prove the SIM is active today. "
    "Rows marked 'needs live check' are the ones a paid carrier (HLR) lookup would settle."
)

GREEN = "#1F7A4D"
RED = "#B42318"
AMBER = "#B54708"
INK = "#1D2939"
MUTED = "#667085"
HEAD_BG = "#EEF2F6"
GREEN_BG = "#E7F6EE"
RED_BG = "#FDECEA"
AMBER_BG = "#FEF4E6"

DETAIL_HEADERS = ["#", "Input", "Number", "Status", "Line type", "Country", "Carrier", "Reason"]


# --------------------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------------------

def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.1f}%" if total else "0.0%"


def _international(r: ValidationResult) -> str:
    return r.to_public()["international"] if r.e164 else ""


def _status_label(r: ValidationResult) -> str:
    return r.status + (" (needs live check)" if r.needs_review else "")


def _line_type_label(r: ValidationResult) -> str:
    return (r.line_type or "").replace("_", " ")


def summary_rows(table: TableResult) -> list[tuple[str, int, str]]:
    s, total = table.summary, table.summary["total_rows"]
    return [
        ("Total numbers", total, "100%" if total else "0%"),
        ("Validated", s["validated"], _pct(s["validated"], total)),
        ("Not Validated", s["not_validated"], _pct(s["not_validated"], total)),
        ("  of which need a live carrier check", s["verdict_review"], _pct(s["verdict_review"], total)),
        ("Duplicates removed", s["duplicates"], _pct(s["duplicates"], total)),
    ]


def reason_rows(table: TableResult) -> list[tuple[str, int]]:
    c = Counter(
        ("Duplicate of an earlier row" if r.is_duplicate else r.reason_text)
        for r in table.results if r.verdict != "keep"
    )
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def line_type_rows(table: TableResult) -> list[tuple[str, int]]:
    c = Counter(_line_type_label(r) or "not a number" for r in table.results if not r.is_duplicate)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def detail_rows(table: TableResult) -> list[list[str]]:
    out = []
    for r in table.results:
        out.append([
            str(r.row), r.input, _international(r), _status_label(r), _line_type_label(r),
            r.country, r.carrier, r.reason_text,
        ])
    return out


def _meta(table: TableResult, source_name: str, generated: datetime) -> list[tuple[str, str]]:
    return [
        ("File", source_name or "(uploaded file)"),
        ("Generated", generated.strftime("%d %b %Y, %H:%M")),
        ("Phone column", table.phone_col),
        ("Country column", table.country_col or "none (default country used)"),
        ("Default country", table.default_region),
    ]


# --------------------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------------------

def build_csv(table: TableResult) -> bytes:
    buf = io.StringIO(newline="")
    write_csv(table, buf)
    # BOM so Excel opens UTF-8 correctly on double-click
    return ("﻿" + buf.getvalue()).encode("utf-8")


# --------------------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------------------

def _pdf_safe(text: str) -> str:
    """Built-in PDF fonts are Latin-1 only; replace anything else so nothing renders as a box."""
    text = (text or "").replace(" ", " ")
    text = text.encode("latin-1", errors="replace").decode("latin-1")
    return escape(text)


def build_pdf(table: TableResult, source_name: str = "", generated: Optional[datetime] = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    generated = generated or datetime.now()
    buf = io.BytesIO()
    page = landscape(A4)
    margin = 12 * mm
    doc = SimpleDocTemplate(
        buf, pagesize=page, leftMargin=margin, rightMargin=margin, topMargin=14 * mm, bottomMargin=14 * mm,
        title="Phone Number Validation Report", author=APP_NAME,
    )
    width = page[0] - 2 * margin

    title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.HexColor(INK))
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor(INK))
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor(MUTED))
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=7.5, leading=9, alignment=TA_LEFT,
                          textColor=colors.HexColor(INK))

    story = [Paragraph("Phone Number Validation Report", title), Spacer(1, 4)]

    meta = Table([[Paragraph(f"<b>{escape(k)}</b>", body), Paragraph(_pdf_safe(v), body)]
                  for k, v in _meta(table, source_name, generated)],
                 colWidths=[32 * mm, width - 32 * mm], hAlign="LEFT")
    meta.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 6)]

    # --- summary tiles
    s, total = table.summary, table.summary["total_rows"]
    tiles = [
        ("Total numbers", total, INK, HEAD_BG),
        ("Validated", s["validated"], GREEN, GREEN_BG),
        ("Not Validated", s["not_validated"], RED, RED_BG),
        ("Need live check", s["verdict_review"], AMBER, AMBER_BG),
    ]
    tile_style = ParagraphStyle("tile", fontName="Helvetica-Bold", fontSize=20, leading=24)
    tile_label = ParagraphStyle("tl", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor(MUTED))
    tile_cells = [[
        [Paragraph(f'<font color="{fg}">{n:,}</font>', tile_style),
         Paragraph(f"{escape(label)} &middot; {_pct(n, total)}", tile_label)]
        for label, n, fg, _ in tiles
    ]]
    tile_table = Table(tile_cells, colWidths=[width / 4] * 4, hAlign="LEFT")
    ts = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
          ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
    for i, (_, _, _, bg) in enumerate(tiles):
        ts.append(("BACKGROUND", (i, 0), (i, 0), colors.HexColor(bg)))
    tile_table.setStyle(TableStyle(ts))
    story += [tile_table, Spacer(1, 4), Paragraph(escape(LAYER_NOTE), body)]

    # --- reasons + line types side by side
    def small_table(rows, header):
        data = [[header, "Count"]] + [[Paragraph(_pdf_safe(k), cell), f"{v:,}"] for k, v in rows]
        if len(data) == 1:
            data.append([Paragraph("none", cell), "0"])
        t = Table(data, colWidths=[95 * mm, 20 * mm], hAlign="LEFT", repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONT", (1, 1), (1, -1), "Helvetica", 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(HEAD_BG)),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D5DD")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    pair = Table([[
        [Paragraph("Why numbers were not validated", h2), small_table(reason_rows(table), "Reason")],
        [Paragraph("Line types found", h2), small_table(line_type_rows(table), "Line type")],
    ]], colWidths=[width / 2, width / 2], hAlign="LEFT")
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [pair, Spacer(1, 6), Paragraph("Every number", h2)]

    # --- detail table
    col_mm = [11, 46, 38, 40, 26, 15, 30]
    widths = [c * mm for c in col_mm] + [width - sum(col_mm) * mm]
    data = [DETAIL_HEADERS]
    status_styles = []
    for i, (row, r) in enumerate(zip(detail_rows(table), table.results), start=1):
        data.append([
            row[0], Paragraph(_pdf_safe(row[1]), cell), row[2], row[3], row[4], row[5],
            Paragraph(_pdf_safe(row[6]), cell), Paragraph(_pdf_safe(row[7]), cell),
        ])
        fg = GREEN if r.verdict == "keep" else (AMBER if r.needs_review else RED)
        status_styles.append(("TEXTCOLOR", (3, i), (3, i), colors.HexColor(fg)))
        if i % 2 == 0:
            status_styles.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F9FAFB")))
    detail = LongTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    detail.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("FONT", (3, 1), (3, -1), "Helvetica-Bold", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(HEAD_BG)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#98A2B3")),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        *status_styles,
    ]))
    story.append(detail)

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawString(margin, 8 * mm, f"{APP_NAME}  |  offline checks (layers 1 and 2)")
        canvas.drawRightString(page[0] - margin, 8 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# --------------------------------------------------------------------------------------
# Word (.docx)
# --------------------------------------------------------------------------------------

def _shade(cell, hex_fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill.lstrip("#"))
    tc_pr.append(shd)


def _xml_safe(text: str) -> str:
    """Word rejects control characters; strip them."""
    return "".join(ch for ch in (text or "") if ch in "\t\n" or ord(ch) >= 32)


def build_docx(table: TableResult, source_name: str = "", generated: Optional[datetime] = None) -> bytes:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Mm, Pt, RGBColor

    generated = generated or datetime.now()
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(297), Mm(210)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Mm(12))

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(9)

    def rgb(h: str) -> RGBColor:
        return RGBColor.from_string(h.lstrip("#"))

    doc.add_heading("Phone Number Validation Report", level=0)
    for k, v in _meta(table, source_name, generated):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.add_run(f"{k}: ").bold = True
        p.add_run(_xml_safe(v))

    # summary
    doc.add_heading("Summary", level=1)
    st = doc.add_table(rows=1, cols=3)
    st.style = "Light Grid Accent 1"
    st.alignment = WD_TABLE_ALIGNMENT.LEFT
    for c, h in zip(st.rows[0].cells, ["Measure", "Count", "Share"]):
        c.text = h
    for label, n, pct in summary_rows(table):
        cells = st.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = label.strip(), f"{n:,}", pct
        color = {"Validated": GREEN, "Not Validated": RED}.get(label.strip())
        if "live carrier" in label:
            color = AMBER
        if color:
            for run in cells[0].paragraphs[0].runs + cells[1].paragraphs[0].runs:
                run.font.color.rgb = rgb(color)
                run.bold = True
    note = doc.add_paragraph(LAYER_NOTE)
    note.runs[0].font.size = Pt(8)
    note.runs[0].font.color.rgb = rgb(MUTED)

    def two_col(title, rows, header):
        doc.add_heading(title, level=1)
        t = doc.add_table(rows=1, cols=2)
        t.style = "Light Grid Accent 1"
        t.rows[0].cells[0].text, t.rows[0].cells[1].text = header, "Count"
        for k, v in rows or [("none", 0)]:
            cells = t.add_row().cells
            cells[0].text, cells[1].text = _xml_safe(k), f"{v:,}"

    two_col("Why numbers were not validated", reason_rows(table), "Reason")
    two_col("Line types found", line_type_rows(table), "Line type")

    # detail table: build all rows at once (add_row in a loop is quadratic-ish in python-docx)
    doc.add_heading("Every number", level=1)
    rows = detail_rows(table)
    dt = doc.add_table(rows=len(rows) + 1, cols=len(DETAIL_HEADERS))
    dt.style = "Light List Accent 1"
    widths = [Mm(w) for w in (10, 45, 38, 40, 25, 15, 30, 70)]
    all_rows = list(dt.rows)
    for c, h, w in zip(all_rows[0].cells, DETAIL_HEADERS, widths):
        c.text, c.width = h, w
    for tr, values, r in zip(all_rows[1:], rows, table.results):
        cells = tr.cells
        for c, v, w in zip(cells, values, widths):
            c.text, c.width = _xml_safe(v), w
        run = cells[3].paragraphs[0].runs[0] if cells[3].paragraphs[0].runs else None
        if run is not None:
            run.bold = True
            run.font.color.rgb = rgb(GREEN if r.verdict == "keep" else (AMBER if r.needs_review else RED))
        _shade(cells[3], GREEN_BG if r.verdict == "keep" else (AMBER_BG if r.needs_review else RED_BG))

    footer = sec.footer.paragraphs[0]
    footer.text = f"{APP_NAME}  |  offline checks (layers 1 and 2)"
    footer.runs[0].font.size = Pt(7.5)
    footer.runs[0].font.color.rgb = rgb(MUTED)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


REPORT_BUILDERS = {
    "pdf": ("application/pdf", ".pdf", build_pdf),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx", build_docx),
    "csv": ("text/csv; charset=utf-8", ".csv", lambda table, *_a, **_k: build_csv(table)),
}
