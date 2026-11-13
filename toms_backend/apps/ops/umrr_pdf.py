"""The Unit Mission Risk Registry (UMRR) PDF for the hazard library.

The layout follows ``UMRR Sample.pdf`` on A4 paper (595 x 842 pt), Arial
throughout with 11 pt body text, the PAF vision line and unit hierarchy centred
at the top, then the registry table with four columns - Nr, Risk Identified,
Assessed Risk and Existing Mitigations/Treatments.

A registry is filed per activity code, never as one mixed list: every activity
code gets its own complete form (header, info row and table), and a selection of
several codes - including "all hazards" - is bound into a single PDF with each
form starting on a new page.

Only the *assessed* (before) risk is printed, exactly as on the unit's form;
residual risk stays in the MRM and is not part of this registry.

The sample's margins are reproduced from the document itself: 36.3 pt at the
left, 27.1 pt at the right (the header block measures 548.6 pt wide), 28.5 pt
from the top edge to the vision line and 36 pt at the foot.

The letterhead carries the mother unit's logo on the left and the current unit's
logo on the right, as the MRM does. In the hierarchy only the current unit is
bold; the parent commands above it are not. Every page ends with a one-inch
purple QR code reading ``202611091430[Air Operations][TOG10]``, followed by the
PAF footer image and ``Page X of Y``. Assessed-risk cells use the same
red/amber/green coding as the MRM.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import qrcode
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.core import timeutils
from apps.ops import mission_order_pdf
from apps.ops.pdf import (
    HEADER_TEXT,
    _risk_colour,
    _unit_logo_flowable,
    fitted_paragraph,
    unit_hierarchy_display,
)

PAGE_WIDTH, PAGE_HEIGHT = A4  # 595 x 842

#: The sample's side margins are kept; A4 is narrower, so the content column
#: follows from the page. The foot keeps room for the one-inch QR and the
#: footer strip.
LEFT_MARGIN = 36.3
RIGHT_MARGIN = 27.1
TOP_MARGIN = 28.5
BOTTOM_MARGIN = 82.0
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN

COLUMN_WIDTHS = [26.0, 178.0, 78.0, CONTENT_WIDTH - 26.0 - 178.0 - 78.0]
INFO_WIDTHS = [238.0, 172.0, CONTENT_WIDTH - 238.0 - 172.0]

LOGO_SIZE = 50.0
LOGO_COLUMN = 64.0

#: Footer strip: a one-inch purple QR code at the left edge, the PAF page
#: footer image beside it and the page number at the right edge. The image is
#: 711 x 76 px, so its height follows from the width.
QR_SIZE = 72.0  # exactly 1 x 1 inch
QR_X = LEFT_MARGIN
QR_Y = 1.0
QR_COLOR = colors.HexColor("#8f00ff")
FOOTER_IMAGE_X = QR_X + QR_SIZE + 8
FOOTER_IMAGE_Y = (QR_SIZE - 25.66) / 2  # centred against the QR code
FOOTER_IMAGE_WIDTH = 240.0
FOOTER_IMAGE_HEIGHT = FOOTER_IMAGE_WIDTH * 76 / 711


def _styles() -> dict[str, ParagraphStyle]:
    """Arial styles at the sample's sizes, registered on first use."""
    mission_order_pdf._ensure_arial()
    regular = mission_order_pdf.FONT_REGULAR
    bold = mission_order_pdf.FONT_BOLD
    italic = mission_order_pdf.FONT_ITALIC
    return {
        "vision": ParagraphStyle(
            "vision",
            fontName=italic,
            fontSize=11,
            leading=13.4,
            alignment=TA_CENTER,
        ),
        "hierarchy": ParagraphStyle(
            "hierarchy",
            fontName=regular,
            fontSize=12,
            leading=14,
            alignment=TA_CENTER,
        ),
        "unit": ParagraphStyle(
            "unit",
            fontName=bold,
            fontSize=12,
            leading=14,
            alignment=TA_CENTER,
        ),
        "location": ParagraphStyle(
            "location",
            fontName=regular,
            fontSize=11,
            leading=13,
            alignment=TA_CENTER,
        ),
        "title": ParagraphStyle(
            "title",
            fontName=bold,
            fontSize=16,
            leading=20,
            alignment=TA_CENTER,
        ),
        "label": ParagraphStyle("label", fontName=bold, fontSize=11, leading=13),
        "value": ParagraphStyle("value", fontName=regular, fontSize=11, leading=13),
        "head": ParagraphStyle("head", fontName=bold, fontSize=11, leading=13),
        "cell": ParagraphStyle(
            "cell",
            fontName=regular,
            fontSize=11,
            leading=13,
            alignment=TA_LEFT,
        ),
        "number": ParagraphStyle(
            "number",
            fontName=regular,
            fontSize=11,
            leading=13,
            alignment=TA_CENTER,
        ),
        "rating": ParagraphStyle(
            "rating",
            fontName=regular,
            fontSize=11,
            leading=13,
            alignment=TA_CENTER,
        ),
    }


def _safe(value) -> str:
    # Legacy rows can contain U+FFFD; a question mark keeps the document valid.
    return escape(str(value or "").replace("\ufffd", "?").strip())


def _rating(hazard) -> str:
    rating = getattr(hazard, "before_risk_rating", "") or (
        f"{hazard.before_likelihood}{hazard.before_severity}"
    )
    return _safe(rating)


def _mitigations(text) -> str:
    """One bullet per stored mitigation line, as the form shows them."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return "—"
    return "<br/>".join(f"• {_safe(line)}" for line in lines)


def _short_date(value) -> str:
    return value.strftime("%d %b %y") if value else ""


def qr_value(*, unit=None, code="", generated_at=None) -> str:
    """The footer QR payload: date and time, [activity code], [unit].

    Example: ``202611091430[Air Operations][TOG10]``. Each page of a form
    carries that form's activity code, so a scanned page identifies the unit and
    the exact form it belongs to. Unit acronyms are compacted (``TOG 10`` is
    written ``TOG10``) as in the scanning format.
    """
    stamp = (generated_at or timeutils.now()).strftime("%Y%m%d%H%M")
    label = getattr(unit, "acronym", "") or getattr(unit, "name", "") or "All Units"
    unit_text = "".join(str(label).split()) or "AllUnits"
    return f"{stamp}[{code or 'All Activities'}][{unit_text}]"


def _qr_matrix(data, border=2):
    """QR modules with a narrow quiet zone, so the code fills its square."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def _draw_qr(canvas, matrix, x, y, size, color) -> None:
    """Draw the QR as vector modules in ``color`` so it stays sharp in print."""
    module = size / len(matrix)
    canvas.saveState()
    canvas.setFillColor(color)
    for row, cells in enumerate(matrix):
        for col, dark in enumerate(cells):
            if dark:
                canvas.rect(
                    x + col * module,
                    y + (len(matrix) - row - 1) * module,
                    module,
                    module,
                    stroke=0,
                    fill=1,
                )
    canvas.restoreState()


def _groups(hazard_rows):
    """Split the rows into (activity code, rows), one form each.

    The queryset already sorts by activity code, so insertion order is stable;
    a row with no code is kept together as "Unassigned" and printed last.
    """
    grouped: dict[str, list] = {}
    for row in hazard_rows:
        code = (getattr(row, "activity_code", "") or "").strip()
        grouped.setdefault(code, []).append(row)
    return sorted(grouped.items(), key=lambda pair: (pair[0] == "", pair[0]))


def _header(hierarchy, styles) -> list:
    """The letterhead: vision line, both logos and the unit hierarchy."""
    text_width = CONTENT_WIDTH - 2 * LOGO_COLUMN
    text_lines = [
        fitted_paragraph(_safe(line), styles["hierarchy"], text_width)
        for line in hierarchy["hierarchy_lines"]
    ]
    text_lines.append(
        fitted_paragraph(_safe(hierarchy["current_unit"]), styles["unit"], text_width)
    )
    if hierarchy.get("location"):
        text_lines.append(
            fitted_paragraph(
                _safe(hierarchy["location"]), styles["location"], text_width
            )
        )

    text = Table(
        [[item] for item in text_lines], colWidths=[CONTENT_WIDTH - 2 * LOGO_COLUMN]
    )
    text.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    mother_logo = _unit_logo_flowable(hierarchy.get("mother_unit_id"), styles)
    if mother_logo is None:
        # The parent command is often the PAF itself; use the PAF seal when the
        # unit has no seal of its own, as the MRM header does.
        default_logo = Path(settings.LEGACY_STATIC_DIR) / "logo.png"
        if default_logo.is_file():
            try:
                mother_logo = Image(
                    str(default_logo), width=LOGO_SIZE, height=LOGO_SIZE
                )
            except Exception:
                mother_logo = None
    current_logo = _unit_logo_flowable(hierarchy.get("current_unit_id"), styles)
    logos = Table(
        [[mother_logo or "", text, current_logo or ""]],
        colWidths=[LOGO_COLUMN, CONTENT_WIDTH - 2 * LOGO_COLUMN, LOGO_COLUMN],
    )
    logos.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (-1, 0), (-1, 0), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [
        Paragraph(_safe(HEADER_TEXT), styles["vision"]),
        Spacer(1, 8),
        logos,
    ]


def _form(hierarchy, styles, code, hazard_rows, reviewed_by, generated_at) -> list:
    """One activity code's complete registry page."""
    elements = _header(hierarchy, styles)
    elements.extend(
        [
            Spacer(1, 12),
            Paragraph("MISSION RISK REGISTRY", styles["title"]),
            Spacer(1, 14),
        ]
    )

    info = Table(
        [
            [
                Paragraph("Mission/Activity:", styles["label"]),
                Paragraph("Reviewed by:", styles["label"]),
                Paragraph("Date Updated:", styles["label"]),
            ],
            [
                Paragraph(_safe(code) or "Unassigned", styles["value"]),
                Paragraph(_safe(reviewed_by) or "—", styles["value"]),
                Paragraph(
                    _safe(_short_date(generated_at or timeutils.now())),
                    styles["value"],
                ),
            ],
        ],
        colWidths=INFO_WIDTHS,
    )
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
            ]
        )
    )
    elements.extend([info, Spacer(1, 6)])

    rows = [
        [
            Paragraph("Nr", styles["head"]),
            Paragraph("Risk Identified", styles["head"]),
            Paragraph("Assessed<br/>Risk", styles["head"]),
            Paragraph("Existing Mitigations/Treatments", styles["head"]),
        ]
    ]
    for index, hazard in enumerate(hazard_rows, start=1):
        rows.append(
            [
                Paragraph(str(index), styles["number"]),
                Paragraph(_safe(hazard.hazard_description), styles["cell"]),
                Paragraph(_rating(hazard), styles["rating"]),
                Paragraph(_mitigations(hazard.mitigations), styles["cell"]),
            ]
        )

    table = Table(rows, colWidths=COLUMN_WIDTHS, repeatRows=1)
    style = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#8A8A8A")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDED")),
        ]
    )
    # The MRM's red / amber / green coding, applied to each assessed rating.
    for index, hazard in enumerate(hazard_rows, start=1):
        style.add(
            "BACKGROUND",
            (2, index),
            (2, index),
            _risk_colour(rating=_rating(hazard)),
        )
    table.setStyle(style)
    elements.append(table)
    return elements


def generate_umrr_pdf(
    hazard_rows,
    *,
    unit=None,
    reviewed_by: str = "",
    generated_at=None,
) -> bytes:
    """Render ``hazard_rows`` as the unit's UMRR and return the PDF bytes.

    The rows are split by activity code and every code is completed as its own
    form, so a registry never mixes activities inside one table. Selecting
    several codes (or all of them) binds the forms into a single PDF.

    Every page carries the purple authenticity QR code, the PAF footer image
    and ``Page X of Y``, as the Mission Order does.
    """
    styles = _styles()
    hierarchy = unit_hierarchy_display(unit)
    groups = _groups(hazard_rows)
    stamp = generated_at or timeutils.now()
    regular = mission_order_pdf.FONT_REGULAR
    footer_image = Path(settings.LEGACY_STATIC_DIR) / "Page_Footer.png"
    purple = QR_COLOR

    # Each form starts a new page; probing each one separately tells the footer
    # which activity code belongs to every page of the bound document.
    page_codes: list[str] = []
    for code, rows in groups:
        probe = SimpleDocTemplate(
            BytesIO(),
            pagesize=A4,
            leftMargin=LEFT_MARGIN,
            rightMargin=RIGHT_MARGIN,
            topMargin=TOP_MARGIN,
            bottomMargin=BOTTOM_MARGIN,
        )
        probe.build(_form(hierarchy, styles, code, rows, reviewed_by, stamp))
        page_codes.extend([code] * probe.page)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title="Unit Mission Risk Registry",
        author="TOWEMS",
    )

    elements = []
    for index, (code, rows) in enumerate(groups):
        if index:
            elements.append(PageBreak())
        elements.extend(
            _form(hierarchy, styles, code, rows, reviewed_by, stamp)
        )

    class _FooterCanvas(pdfcanvas.Canvas):
        """Stamps the purple QR, footer image and ``Page X of Y`` on every page."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._page_states = []

        def showPage(self):  # noqa: N802 - reportlab API
            self._page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._page_states)
            for state in self._page_states:
                self.__dict__.update(state)
                self._draw_footer(total)
                pdfcanvas.Canvas.showPage(self)
            pdfcanvas.Canvas.save(self)

        def _draw_footer(self, total):
            page = self.getPageNumber()
            code = page_codes[page - 1] if 0 < page <= len(page_codes) else ""
            try:
                _draw_qr(
                    self,
                    _qr_matrix(qr_value(unit=unit, code=code, generated_at=stamp)),
                    QR_X,
                    QR_Y,
                    QR_SIZE,
                    purple,
                )
            except Exception:  # pragma: no cover - a QR must never break the PDF
                pass

            if footer_image.is_file():
                self.drawImage(
                    str(footer_image),
                    FOOTER_IMAGE_X,
                    FOOTER_IMAGE_Y,
                    width=FOOTER_IMAGE_WIDTH,
                    height=FOOTER_IMAGE_HEIGHT,
                    preserveAspectRatio=True,
                    mask="auto",
                )

            self.saveState()
            self.setFillColor(colors.black)
            self.setFont(regular, 8)
            self.drawRightString(
                PAGE_WIDTH - RIGHT_MARGIN, 32, f"Page {page} of {total}"
            )
            self.restoreState()

    doc.build(elements, canvasmaker=_FooterCanvas)
    return buffer.getvalue()
