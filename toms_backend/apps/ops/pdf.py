"""MRM PDF generation — Django port of the legacy ``services/pdf_service.py``.

The document layout, risk colouring, authority matrix, QR payload and header /
footer are reproduced from the Flask generator so that a PDF produced by TOWEMS
is equivalent to one produced by the legacy application.

Files (signatures, unit logos, page footer, risk matrix, previously approved
PDFs) are read from their original locations under the project root; Django
never moves or rewrites them.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)

HEADER_TEXT = (
    "Flight Plan Vision: A Credible and Agile Air Force Adaptable to Modern Warfare "
    "and Responsive to National and Regional Security and Development."
)


def _ph_time() -> datetime:
    """Naive Manila time, matching ``USE_TZ = False`` and the legacy rows."""
    from apps.core import timeutils

    return timeutils.now()


def _stamp() -> str:
    """The one user-facing timestamp format: ``29 Oct 2026, 1430H (PST)``."""
    from apps.core.timeutils import format_datetime

    return format_datetime(_ph_time())


def _pdf_date(value) -> str:
    from apps.core.timeutils import format_date

    return format_date(value)


def _pdf_datetime(value) -> str:
    from apps.core.timeutils import format_datetime

    return format_datetime(value)


# ---------------------------------------------------------------------------
# Unit header helpers
# ---------------------------------------------------------------------------


def unit_hierarchy_display(unit):
    """Hierarchical header lines for a unit (legacy ``get_unit_hierarchy_display``)."""
    if unit is None:
        return {
            "hierarchy_lines": ["PHILIPPINE AIR FORCE"],
            "current_unit": "PHILIPPINE AIR FORCE",
            "location": "",
            "mother_unit_id": None,
            "current_unit_id": None,
        }

    path = []
    current = unit
    while current is not None:
        path.insert(0, current)
        current = current.parent

    command_unit = wing_unit = group_center_unit = None
    for candidate in path:
        if candidate.category == "Command" and command_unit is None:
            command_unit = candidate
        elif candidate.category == "Wing" and wing_unit is None:
            wing_unit = candidate
        elif candidate.category in ("Group", "Center") and group_center_unit is None:
            group_center_unit = candidate

    hierarchy_lines = ["PHILIPPINE AIR FORCE"]

    def add_line(name):
        """Add a parent once; the PAF root is already the first line."""
        text = (name or "").strip().upper()
        if text and text not in hierarchy_lines:
            hierarchy_lines.append(text)

    if unit.category != "Command":
        if command_unit:
            add_line(command_unit.name)
        if unit.category in ("Group", "Center"):
            if wing_unit:
                add_line(wing_unit.name)
        else:
            # Ancestors only. A Wing is itself the current unit, so adding it
            # here would repeat it above "HEADQUARTERS ..." and leave the
            # header looking like it has a blank extra line.
            if wing_unit and wing_unit.id != unit.id:
                add_line(wing_unit.name)
            if group_center_unit and group_center_unit.id != unit.id:
                add_line(group_center_unit.name)

    return {
        "hierarchy_lines": hierarchy_lines,
        "current_unit": f"HEADQUARTERS {unit.name.upper()}",
        "location": (unit.location or "").upper(),
        # The mother unit is the immediate parent (the wing for a group, the
        # group for a squadron), not the PAF root.
        "mother_unit_id": unit.parent_id,
        "current_unit_id": unit.id,
    }


def _logo_directories():
    """Where a unit seal may live: the legacy folder and the new uploads."""
    return (
        Path(settings.LEGACY_LOGO_DIR),
        Path(settings.LEGACY_UPLOAD_DIR) / "unit_logos",
    )


def _logo_path(filename):
    """Resolve a stored logo filename in either logo folder."""
    name = os.path.basename(str(filename or ""))
    if not name:
        return None
    for directory in _logo_directories():
        path = directory / name
        if path.is_file():
            return path
    return None


def fitted_paragraph(text, style, max_width, min_size=8.0):
    """A centred header line that shrinks slightly rather than wrapping.

    Unit names such as "Headquarters Tactical Operations Wing Eastern
    Mindanao" are wider than the space between the two logos on portrait A4.
    Breaking the name across lines reads like a missing header line, so the
    line is kept intact and only the font size is reduced when necessary.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    size = style.fontSize
    width = stringWidth(text, style.fontName, size)
    if width > max_width:
        size = max(min_size, size * max_width / width)
        style = ParagraphStyle(
            f"{style.name}_fitted",
            parent=style,
            fontSize=size,
            leading=max(size + 2, size * 1.15),
        )
    return Paragraph(text, style)


def _unit_logo_flowable(unit_id, styles):
    """Logo image for a unit, wherever its seal was stored.

    Seals may come from the legacy ``unit_logos`` table (files under
    ``static/unit_logos``) or from the newer Operational Mapping upload
    (``command_unit_map_marker.logo_filename``, files under
    ``uploads/unit_logos``). Older rows also identify the unit only by name, and
    some drop-in files are simply named after the acronym, so all three paths
    are tried before the header goes without a seal.
    """
    if not unit_id:
        return None
    try:
        from apps.command.models import UnitMapMarker
        from apps.legacy.models import Unit, UnitLogo

        unit = Unit.objects.filter(pk=unit_id).first()
        names = {value for value in (unit.name, unit.acronym) if value} if unit else set()

        logo = UnitLogo.objects.filter(unit_id=unit_id).first()
        if logo is None and names:
            logo = UnitLogo.objects.filter(unit_name__in=names).first()
        candidate = _logo_path(logo.logo_filename) if logo else None

        if candidate is None:
            marker = UnitMapMarker.objects.filter(unit_id=unit_id).first()
            candidate = _logo_path(marker.logo_filename) if marker else None

        if candidate is None and unit is not None:
            for label in (unit.acronym, unit.name):
                for extension in ("png", "jpg", "jpeg", "webp"):
                    candidate = _logo_path(f"{label}.{extension}")
                    if candidate is not None:
                        break
                if candidate is not None:
                    break

        if candidate is not None:
            return Image(str(candidate), width=50, height=50)
    except Exception:  # pragma: no cover - logo must never break the document
        logger.exception("Unit logo lookup failed for unit %s", unit_id)
    return None


# ---------------------------------------------------------------------------
# Signatures + QR
# ---------------------------------------------------------------------------


def _qr_flowable(data: str, size: int = 60):
    try:
        import qrcode

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=2,
            border=1,
        )
        qr.add_data(data)
        qr.make(fit=True)
        image = qr.make_image(fill_color="#800080", back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return Image(buffer, width=size, height=size)
    except Exception:
        logger.exception("QR code generation failed")
        return None


def _signature_cell(user, signed, signature_date, role, mrm_number, small):
    """Signature image + verification QR, as in the legacy document."""
    try:
        if user and signed and user.signature_filename:
            path = Path(settings.LEGACY_SIGNATURE_DIR) / os.path.basename(
                user.signature_filename
            )
            if path.is_file():
                signature = Image(str(path), width=90, height=35)
                timestamp = (
                    signature_date.strftime("%y%m%d%H%M") if signature_date else "000000"
                )
                payload = (
                    f"{user.serial_number}|{user.full_name}|{timestamp}|"
                    f"{mrm_number}|{role}"
                )
                qr = _qr_flowable(payload, size=35)
                if qr is None:
                    return signature
                table = Table([[signature, Spacer(5, 1), qr]], colWidths=[90, 5, 35])
                table.setStyle(
                    TableStyle(
                        [
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 0), (-1, -1), 0),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                        ]
                    )
                )
                return table
    except Exception:
        logger.exception("Signature rendering failed for %s", role)
    return Paragraph("__________________", small)


def _signature_section(label, cell, user, signed, bold_left, centered_small, small):
    """Label + signature layered with the signer's name."""
    flowables = [Paragraph(f"<b>{label}:</b>", bold_left)]

    if user and signed and getattr(user, "signature_filename", None):
        layered = Table(
            [[cell], [Paragraph(user.full_name, centered_small)]], colWidths=[153]
        )
        layered.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.transparent),
                ]
            )
        )
        flowables.append(layered)
    else:
        flowables.append(Paragraph(user.full_name if user else "N/A", centered_small))
        flowables.append(Spacer(1, 20))

    section = Table([[item] for item in flowables], colWidths=[153])
    section.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return section


# ---------------------------------------------------------------------------
# Page template
# ---------------------------------------------------------------------------


class HeaderFooterDocTemplate(SimpleDocTemplate):
    """Reproduces the legacy page header (vision statement) and footer image."""

    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self.page_width, self.page_height = PAGE_WIDTH, PAGE_HEIGHT

    def handle_pageBegin(self):  # noqa: N802 - reportlab API
        self.canv.saveState()
        self.canv.setFont("Helvetica-Oblique", 9)
        self.canv.setFillColor(colors.black)

        words = HEADER_TEXT.split()
        lines: list[str] = []
        current: list[str] = []
        for word in words:
            test = " ".join(current + [word])
            if self.canv.stringWidth(test, "Helvetica-Oblique", 9) < self.page_width - 40:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))

        y = self.page_height - 25
        for line in reversed(lines):
            self.canv.drawCentredString(self.page_width / 2, y, line)
            y -= 12

        footer = Path(settings.LEGACY_STATIC_DIR) / "Page_Footer.png"
        if footer.is_file():
            try:
                Image(str(footer), width=360, height=40).drawOn(
                    self.canv, (self.page_width / 2) - 150, 15
                )
            except Exception:
                logger.exception("Footer image failed to render")

        self.canv.restoreState()
        SimpleDocTemplate.handle_pageBegin(self)


# ---------------------------------------------------------------------------
# Risk colouring (identical thresholds to the legacy generator)
# ---------------------------------------------------------------------------


def _risk_colour(rating=None):
    try:
        likelihood = severity = None
        if rating:
            text = str(rating).strip().upper()
            if len(text) == 2 and text[0].isdigit() and text[1].isalpha():
                likelihood, severity = text[0], text[1]
        score = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}.get(str(severity), 0) * int(
            likelihood or 0
        )
        if score >= 15:
            return colors.red
        if score >= 6:
            return colors.yellow
        return colors.green
    except Exception:
        return colors.white


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def generate_mrm_pdf(form) -> bytes:
    """Build the MRM document for ``form`` (an ``apps.legacy.models.MRMForm``)."""
    from apps.accounts.models import User
    from apps.legacy.models import MissionHazards, Unit

    creator = User.objects.filter(pk=form.created_by_id).first()
    reviewer = (
        User.objects.filter(pk=form.reviewed_by_id).first() if form.reviewed_by_id else None
    )
    authority_user = (
        User.objects.filter(pk=form.authority_assigned_to_id).first()
        if form.authority_assigned_to_id
        else None
    )
    hazards = list(
        MissionHazards.objects.filter(mrm_id=form.id, selected=True).select_related(
            "hazard"
        )
    )

    try:
        return _build(form, creator, reviewer, authority_user, hazards)
    except Exception:
        logger.exception("PDF generation failed for MRM %s", form.mrm_number)
        return _error_pdf(form)


def _build(form, creator, reviewer, authority_user, hazards) -> bytes:
    from apps.legacy.models import Unit

    buffer = BytesIO()
    doc = HeaderFooterDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=50,
        bottomMargin=60,
    )

    elements = []
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=11)
    bold_left = ParagraphStyle(
        "bold_left", parent=styles["Normal"], fontSize=9, leading=11, alignment=0
    )
    centered_small = ParagraphStyle(
        "centered_small", parent=styles["Normal"], fontSize=8, leading=10, alignment=1
    )
    hierarchy_line_style = ParagraphStyle(
        "hierarchy_line", parent=styles["Normal"], fontSize=10, leading=12, alignment=1
    )
    current_unit_style = ParagraphStyle(
        "current_unit",
        parent=styles["Normal"],
        fontSize=12,
        leading=14,
        alignment=1,
        fontName="Helvetica-Bold",
    )
    location_style = ParagraphStyle(
        "location", parent=styles["Normal"], fontSize=9, leading=11, alignment=1
    )
    mrm_title_style = ParagraphStyle(
        "mrm_title",
        parent=styles["Title"],
        fontSize=16,
        leading=20,
        alignment=1,
        fontName="Helvetica-Bold",
    )
    hazard_col_widths = [25, 140, 60, 60, 60, 200, 60, 60, 60]

    # ---- Unit hierarchy + logos ----
    # The document belongs to the unit the assessment was raised for, not to
    # whoever created it. An account transferred from TOG 10 to TOG 11 must
    # still produce TOG 10's MRM under TOG 10, so the record's unit snapshot
    # wins and the creator's *current* unit is only a fallback for rows that
    # predate the snapshot.
    unit = None
    if form.original_unit_id:
        unit = Unit.objects.filter(pk=form.original_unit_id).first()
    if unit is None and creator and creator.unit_id:
        unit = Unit.objects.filter(pk=creator.unit_id).first()
    hierarchy = unit_hierarchy_display(unit)

    static_dir = Path(settings.LEGACY_STATIC_DIR)
    left_logo = _unit_logo_flowable(hierarchy.get("mother_unit_id"), styles)
    if left_logo is None:
        default_logo = static_dir / "logo.png"
        if default_logo.is_file():
            try:
                left_logo = Image(str(default_logo), width=50, height=50)
            except Exception:
                left_logo = Paragraph("PAF", small)
        else:
            left_logo = Paragraph("PAF", small)

    right_logo = _unit_logo_flowable(hierarchy.get("current_unit_id"), styles)
    if right_logo is None:
        right_logo = Paragraph("", small)

    header_flowables = [
        Paragraph(line, hierarchy_line_style) for line in hierarchy["hierarchy_lines"]
    ]
    if hierarchy.get("current_unit"):
        header_flowables.append(Paragraph(hierarchy["current_unit"], current_unit_style))
    if hierarchy.get("location"):
        header_flowables.append(Paragraph(hierarchy["location"], location_style))
    header_flowables.append(Spacer(1, 6))
    header_flowables.append(Paragraph("MISSION RISK MANAGEMENT (MRM) FORM", mrm_title_style))

    header_text_table = Table([[item] for item in header_flowables], colWidths=[585])
    header_text_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    header1 = Table([[left_logo, header_text_table, right_logo]], colWidths=[70, 585, 70])
    header1.setStyle(
        TableStyle(
            [
                # Outer frame only: no vertical rules beside the logos.
                ("BOX", (0, 0), (-1, -1), 0.4, colors.black),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(header1)

    # ---- Mission / signatures / date ----
    creator_cell = _signature_cell(
        creator, form.creator_signed, form.creator_signature_date, "CREATOR",
        form.mrm_number, small,
    )
    reviewer_cell = _signature_cell(
        reviewer, form.evaluator_signed, form.evaluator_signature_date, "REVIEWER",
        form.mrm_number, small,
    )
    compiled = _signature_section(
        "Compiled by", creator_cell, creator, form.creator_signed,
        bold_left, centered_small, small,
    )
    reviewed = _signature_section(
        "Reviewed by", reviewer_cell, reviewer, form.evaluator_signed,
        bold_left, centered_small, small,
    )

    header2 = Table(
        [
            [
                Paragraph(
                    f"<b>Mission/Activity:</b><br/><br/>{form.activity_mission or ''}", small
                ),
                compiled,
                reviewed,
                Paragraph(
                    f"<b>Date:</b><br/><br/>{_pdf_date(form.date_created)}",
                    small,
                ),
            ]
        ],
        colWidths=[339, 153, 153, 80],
    )
    header2.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(header2)

    # ---- Document verification strip ----
    verify = (
        f"MRM: {form.mrm_number} | Created: "
        f"{_pdf_datetime(form.date_created) or 'N/A'}"
        f" | ID: {form.id}"
    )
    verify_table = Table(
        [[Paragraph(f"<font size=7><b>Document Verification:</b> {verify}</font>", small)]],
        colWidths=[725],
    )
    verify_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(verify_table)

    # ---- Objective / assumptions / environment / aircraft ----
    objective = Table(
        [[Paragraph(
            f"<b>Mission/Activity Objective:</b> {form.safety_objectives or ''}<br/><br/>",
            small,
        )]],
        colWidths=[725],
    )
    objective.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.black)]))
    elements.append(objective)

    assump_style = ParagraphStyle(
        "assump_center", parent=styles["Normal"], fontSize=10, alignment=1
    )
    assumptions = Table(
        [[Paragraph("<b>Assumptions / Nominal Conditions</b>", assump_style)]],
        colWidths=[725],
    )
    assumptions.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ]
        )
    )
    elements.append(assumptions)

    row4 = Table(
        [
            [
                Paragraph("<b>Crew/Personnel:</b> I.M.S.A.F.E.", small),
                Paragraph(f"<b>Environment:</b> {form.environment or ''}", small),
            ]
        ],
        colWidths=[362.5, 362.5],
    )
    row4.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(row4)

    row5 = Table(
        [
            [
                Paragraph(f"<b>Aircraft/Vehicle:</b> {form.aircraft_vehicle or ''}", small),
                Paragraph(f"<b>Mission:</b> {form.mission_statement or ''}", small),
            ]
        ],
        colWidths=[362.5, 362.5],
    )
    row5.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.black)]))
    elements.append(row5)

    # ---- Hazard table ----
    data = [
        [
            "NR", "Risk Identified (RI)", "Assessed Risk (AR)", "", "",
            "Mitigations / Treatments Needed", "Residual Risk (RR)", "", "",
        ],
        [
            "NR", "Hazard Description", "Likelihood", "Severity", "Risk Level",
            "Mitigations", "Likelihood", "Severity", "Risk Level",
        ],
    ]
    after_ratings = []
    for index, link in enumerate(hazards, start=1):
        hazard = link.hazard
        if link.override_likelihood is not None and link.override_severity:
            after_likelihood = link.override_likelihood
            after_severity = link.override_severity
            after_rating = f"{link.override_likelihood}{link.override_severity}"
        else:
            after_likelihood = hazard.after_likelihood
            after_severity = hazard.after_severity
            after_rating = hazard.after_risk_rating
        after_ratings.append(after_rating)

        extra = link.additional_mitigations or ""
        combined = (hazard.mitigations or "") + (f"\n{extra}" if extra else "")
        lines = [f"• {line.strip()}" for line in combined.splitlines() if line.strip()]
        mitigation = Paragraph("<br/>".join(lines) if lines else "N/A", small)

        data.append(
            [
                str(index),
                Paragraph(hazard.hazard_description or "N/A", small),
                hazard.before_likelihood or "",
                hazard.before_severity or "",
                hazard.before_risk_rating or "",
                mitigation,
                after_likelihood or "",
                after_severity or "",
                after_rating or "",
            ]
        )

    hazard_table = Table(data, colWidths=hazard_col_widths, repeatRows=2)
    style = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
            ("SPAN", (2, 0), (4, 0)), ("SPAN", (6, 0), (8, 0)),
            ("SPAN", (1, 0), (1, 1)), ("SPAN", (5, 0), (5, 1)), ("SPAN", (0, 0), (0, 1)),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (5, 2), (5, -1), "LEFT"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("BACKGROUND", (0, 1), (-1, 1), colors.lightgrey),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )
    for row_index, link in enumerate(hazards, start=2):
        style.add("BACKGROUND", (4, row_index), (4, row_index),
                  _risk_colour(rating=link.hazard.before_risk_rating))
        style.add("BACKGROUND", (8, row_index), (8, row_index),
                  _risk_colour(rating=after_ratings[row_index - 2]))
    hazard_table.setStyle(style)
    elements.append(hazard_table)

    # ---- Summary ----
    pct = form.total_percent or 0
    summary_style = ParagraphStyle(
        "summary_bold", parent=styles["Normal"], fontSize=10, leading=13, alignment=1
    )
    summary = Table(
        [
            [
                Paragraph(
                    f"<b>Total RR/Max Risk x 100 = Risk% __{pct:.0f}%__</b>", summary_style
                ),
                Paragraph("<b>MANAGEMENT</b>", summary_style),
            ]
        ],
        colWidths=[350, 375],
    )
    summary.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    elements.append(summary)

    # ---- Authority matrix ----
    authority_cell = _signature_cell(
        authority_user, form.authority_approved, form.authority_approval_date,
        "AUTHORITY", form.mrm_number, small,
    )
    if authority_user and form.authority_approved:
        name_date = f"{authority_user.full_name}<br/>{_pdf_datetime(form.authority_approval_date) or 'Not Signed'}"
    elif authority_user:
        name_date = f"{authority_user.full_name}<br/>Pending Approval"
    else:
        name_date = "Not Assigned<br/>Pending Approval"

    authority_rows = [
        ("≤50%", "Operator", 1),
        ("51–65%", "Supervisor", 2),
        ("66–75%", "Squadron Commander", 3),
        ("76–85%", "Director for Operations", 4),
        ("86–100%", "Commander", 5),
    ]
    authority_data = [
        ["Residual Risk /Max Risk (%)", "Mission Decision Authority",
         "Name with Date/Time", "Signature"]
    ]
    active_row = None
    for band, role, row_index in authority_rows:
        in_band = _pct_in_band(pct, band)
        authority_data.append(
            [
                band,
                role,
                Paragraph(name_date, centered_small) if in_band else "",
                authority_cell if in_band else "",
            ]
        )
        if in_band:
            active_row = row_index

    authority_table = Table(authority_data, colWidths=[150, 200, 200, 175])
    authority_style = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )
    if active_row:
        authority_style.add("BACKGROUND", (0, active_row), (3, active_row), colors.lightblue)
    authority_table.setStyle(authority_style)
    elements.append(authority_table)

    # ---- Verification footer ----
    footer_text = (
        f"Verified: {_stamp()} | MRM: {form.mrm_number}"
        f" | ID: {form.id}"
    )
    footer_table = Table(
        [[Paragraph(f"<font size=6>{footer_text}</font>", small)]], colWidths=[725]
    )
    footer_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    elements.append(footer_table)

    # ---- Risk matrix legend ----
    matrix = static_dir / "risk_matrix.png"
    if matrix.is_file():
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("<b>Risk Matrix Legend:</b>", small))
        elements.append(Image(str(matrix), width=360, height=180))

    doc.build(elements)
    return buffer.getvalue()


def _pct_in_band(pct: float, band: str) -> bool:
    """Whether ``pct`` falls in a display band such as ``51–65%``.

    The authority matrix labels carry percent signs, so strip them before
    converting the bounds. ``≤50%`` is a single upper bound; the remaining
    bands are inclusive ranges.
    """
    text = str(band or "").strip().replace("≤", "")
    text = text.replace("–", "-").replace("—", "-").replace("%", "").strip()
    if "-" in text:
        low, high = text.split("-", 1)
        return float(low.strip()) <= pct <= float(high.strip())
    return pct <= float(text)


def _error_pdf(form) -> bytes:
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=landscape(A4))
    page.setFont("Helvetica-Bold", 16)
    page.drawString(100, 500, f"Error Generating PDF for MRM {form.mrm_number}")
    page.setFont("Helvetica", 12)
    page.drawString(100, 470, "The system encountered an error while generating the PDF.")
    page.drawString(100, 450, "Please contact the system administrator.")
    page.drawString(100, 430, "The MRM record itself is unaffected.")
    page.save()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------


def stored_pdf_path(form) -> Path | None:
    """Absolute path of the approved PDF on disk, or ``None``.

    Guards against a traversal attempt if ``approved_pdf_filename`` ever
    contained separators: the resolved path must stay inside the storage
    directory.
    """
    if not form.approved_pdf_filename:
        return None
    root = Path(settings.LEGACY_PDF_DIR).resolve()
    candidate = (root / form.approved_pdf_filename).resolve()
    if candidate.parent != root or not candidate.is_file():
        return None
    return candidate


def can_view_pdf(user, form) -> bool:
    """Who may view an MRM PDF.

    The record must already be visible to the caller (the viewset applies unit
    scoping before this runs), and additionally the caller must be an
    administrator, the Unit Safety Officer, the creator, the assigned evaluator,
    the approval authority, or be looking at an authority-approved document -
    mirroring the legacy ``serve_approved_pdf`` check. The Unit Safety Officer
    holds oversight of their unit's register, so they may open any PDF already
    inside their unit scope.
    """
    from apps.core.permissions import ADMIN, UNIT_SAFETY, authority  # local import avoids a cycle

    if authority(user) in {ADMIN, UNIT_SAFETY}:
        return True
    if form.created_by_id == user.id or form.reviewed_by_id == user.id:
        return True
    if form.authority_assigned_to_id == user.id:
        return True
    return bool(form.authority_approved)
