"""Generate the formal Mission Order PDF for a TOWEMS mission.

The layout follows the unit's existing Mission Order form: the PAF vision line,
the unit hierarchy with the higher (mother) unit's seal on the left and the
current unit's seal on the right, the left-aligned order number and date, then
the numbered directive paragraphs and the signer block. All text uses Arial at
12 pt on A4 paper. Content comes from the mission record, its active personnel,
and the first planned vehicle itinerary.
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import qrcode
from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.rl_config import paraFontSizeHeightOffset

from apps.ops.pdf import (
    HEADER_TEXT,
    _unit_logo_flowable,
    fitted_paragraph,
    unit_hierarchy_display,
)

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MARGIN = 1.25 * inch
RIGHT_MARGIN = 0.75 * inch
TOP_MARGIN = 0.75 * inch
BOTTOM_MARGIN = 1.0 * inch
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
FRAME_PADDING = 6
TAB_WIDTH = 0.5 * inch
SIGNER_INDENT = 6 * TAB_WIDTH
OPERATIONS_AS_IS_CODE = "03"
VERIFICATION_SALT = "mission-order-verification"
QR_SIZE = 52
QR_X = LEFT_MARGIN
QR_Y = 15
BARCODE_COLOR = colors.HexColor("#8f00ff")
BARCODE_TEXT_COLOR = colors.HexColor("#575656")
BARCODE_WIDTH = 67.1
BARCODE_SYMBOL_WIDTH = 67.1
BARCODE_HEIGHT = 38.0
BARCODE_LOGO_SIZE = 15

FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"
FONT_BOLD_ITALIC = "Helvetica-BoldOblique"
_ARIAL_READY = False

FONT_FILES = {
    "Arial": ("arial.ttf", "Arial.ttf"),
    "Arial-Bold": ("arialbd.ttf", "Arial Bold.ttf"),
    "Arial-Italic": ("ariali.ttf", "Arial Italic.ttf"),
    "Arial-BoldItalic": ("arialbi.ttf", "Arial Bold Italic.ttf"),
}


def _ensure_arial():
    """Register Arial once; fall back only when the host lacks the font files."""
    global FONT_REGULAR, FONT_BOLD, FONT_ITALIC, FONT_BOLD_ITALIC, _ARIAL_READY
    if _ARIAL_READY:
        return

    roots = [
        Path(
            os.environ.get("WINDIR")
            or os.environ.get("SystemRoot")
            or r"C:\Windows"
        )
        / "Fonts",
        Path("/usr/share/fonts/truetype/msttcorefonts"),
        Path("/usr/share/fonts/truetype/microsoft"),
        Path(settings.LEGACY_STATIC_DIR) / "fonts",
    ]
    found = {}
    for family, filenames in FONT_FILES.items():
        for root in roots:
            for filename in filenames:
                path = root / filename
                if path.is_file():
                    found[family] = path
                    break
            if family in found:
                break

    if len(found) == len(FONT_FILES):
        try:
            for family, path in found.items():
                pdfmetrics.registerFont(TTFont(family, str(path)))
            pdfmetrics.registerFontFamily(
                "Arial",
                "Arial",
                "Arial-Bold",
                "Arial-Italic",
                "Arial-BoldItalic",
            )
            FONT_REGULAR = "Arial"
            FONT_BOLD = "Arial-Bold"
            FONT_ITALIC = "Arial-Italic"
            FONT_BOLD_ITALIC = "Arial-BoldItalic"
        except Exception:
            # A missing or corrupt font file must not take down the document.
            pass

    _ARIAL_READY = True


#: Keep the order table in the same order the mission form presents roles.
ROLE_ORDER = {
    "Officer-In-Charge": 0,
    "Officer In Charge": 0,
    "Team Leader": 1,
    "Assistant Team Leader": 2,
    "Driver": 3,
    "Security": 4,
    "Member": 5,
    "Crew": 6,
    "Medic": 7,
    "Specify": 8,
    "Other": 9,
}

COMMANDER_TITLES = {
    "Command": "COMMANDER",
    "Wing": "WING COMMANDER",
    "Group": "GROUP COMMANDER",
    "Center": "CENTER COMMANDER",
    "Squadron": "SQUADRON COMMANDER",
}

#: The same posts as they are written under a signature. A commander is titled
#: by the level of the unit the order is issued for, never by a free-text field.
COMMANDER_DESIGNATIONS = {
    "Command": "Commander",
    "Wing": "Wing Commander",
    "Group": "Group Commander",
    "Center": "Center Commander",
    "Squadron": "Squadron Commander",
}


def _text(value) -> str:
    # Legacy rows can contain U+FFFD where a name was imported with a bad
    # encoding. A visible question mark is honest and keeps ReportLab's
    # standard fonts from failing the whole document.
    text = str(value or "").replace("\ufffd", "?").strip()
    return escape(text)


def _long_date(value) -> str:
    if value is None:
        return ""
    return value.strftime("%d %B %Y")


def _order_number(mission) -> str:
    """Return the ``03-05-2026`` operations/order/year number.

    The form has no separate order-series field, so the annual sequence follows
    the mission's position among the missions recorded for the same year.
    """
    issued = mission.created_at or timezone.now()
    sequence = 1
    if mission.pk:
        from apps.ops.models import Mission

        sequence = (
            Mission.objects.filter(
                created_at__year=issued.year,
                created_at__lte=issued,
            ).count()
            or 1
        )
    return f"{OPERATIONS_AS_IS_CODE}-{sequence:02d}-{issued.year}"


def verification_token(mission, *, order_number=None, issued=None) -> str:
    """Signed, compact proof that this Mission Order came from TOWEMS."""
    issued_at = issued or mission.created_at or timezone.now()
    payload = {
        "mission_id": mission.pk,
        "mission_number": mission.mission_number or "",
        "order_number": order_number or _order_number(mission),
        "issued_at": issued_at.isoformat(),
    }
    return signing.dumps(payload, salt=VERIFICATION_SALT, compress=True)


def signature_barcode_value(mission, signer) -> str:
    """``YYYYMMDD`` plus the signatory's serial using digits only.

    The barcode is issued on the signature date (falling back to the mission's
    creation date while the order is unsigned). Serial numbers in the legacy
    personnel table can contain a leading letter and punctuation; those are
    deliberately removed so the payload is numeric as required by the form.
    """
    if signer is None:
        return ""
    issued = mission.mission_order_signed_at or mission.created_at or timezone.now()
    serial = "".join(
        char
        for char in str(getattr(signer, "serial_number", "") or "")
        if char in "0123456789"
    )
    if not serial:
        serial = "".join(
            char
            for char in str(getattr(signer, "username", "") or "")
            if char in "0123456789"
        )
    return f"{issued:%Y%m%d}{serial}"


def load_verification_token(token):
    """Validate a QR token; raises ``signing.BadSignature`` when tampered."""
    return signing.loads(token, salt=VERIFICATION_SALT)


def _default_verification_url(mission) -> str:
    token = verification_token(mission)
    base_url = os.environ.get(
        "TOMS_PUBLIC_BASE_URL", "http://127.0.0.1:8000"
    ).rstrip("/")
    path = reverse("mission-order-verify", kwargs={"token": token})
    return f"{base_url}{path}"


def _person_name(user) -> str:
    if user is None:
        return ""
    return " ".join(
        part for part in (user.first_name, user.middle_name, user.last_name) if part
    ).strip()


def _role_label(row) -> str:
    if row.role == "Specify" and row.custom_role:
        return row.custom_role
    return row.role or "Member"


def _signer_for(mission):
    # The designated Mission Order signatory is the person whose name and rank
    # belong in the signature block. Older records without one keep the
    # approving authority / approver / creator fallback.
    return (
        getattr(mission, "mission_order_signatory", None)
        or mission.approving_authority
        or mission.approved_by
        or mission.created_by
    )


def _signing_as_commander(mission, signer) -> bool:
    """Whether the signer signs in their own right as the unit's commander.

    That is the commander's post at the unit the order is issued for, or the
    commander the mission names as its approving authority when no appointment
    row was ever entered. Holding command at a *higher* headquarters is not the
    same thing: a wing commander signing a group's order still signs it for the
    group commander.
    """
    if signer is None or mission.unit_id is None:
        return False

    from apps.command.models import UnitAppointment
    from apps.core.roles import ROLE_COMMANDER
    from apps.ops.models import Mission

    if UnitAppointment.objects.filter(
        is_active=True,
        role=ROLE_COMMANDER,
        unit_id=mission.unit_id,
        user_id=getattr(signer, "pk", None),
    ).exists():
        return True
    return bool(
        mission.approving_authority_id == getattr(signer, "pk", None)
        and mission.approving_authority_role == Mission.AUTHORITY_COMMANDER
        and getattr(signer, "unit_id", None) == mission.unit_id
    )


def _commander_designation(unit) -> str:
    """The commander's designation at the level of ``unit``."""
    return COMMANDER_DESIGNATIONS.get(getattr(unit, "category", ""), "Commander")


def _signer_designation(mission, signer, *, as_commander=None):
    """Return the post shown under the signer's rank.

    A commander signs in their own right, so the designation is the post of
    their level - Commander, Wing Commander, Group Commander, Squadron
    Commander. Anybody else keeps the mission's authority role when they are
    that authority (a deputy signing "for" the commander), and their own
    profile designation otherwise.

    The designated signatory can be the approving authority stored as a
    separate model instance, so compare primary keys rather than object
    identity.
    """
    if signer is None:
        return ""
    if as_commander is None:
        as_commander = _signing_as_commander(mission, signer)
    if as_commander:
        return _commander_designation(mission.unit)
    if getattr(signer, "pk", None) == mission.approving_authority_id:
        role = mission.get_approving_authority_role_display()
        if role:
            return role
    return (signer.designation or "").strip()


def signature_block(mission, signer) -> dict:
    """The signature block as it is printed on the order.

    A signed order is a record of the day it was signed, so every part of its
    block - name, rank, branch and post - is served from what was recorded
    then. Before signature it follows the signer's current record.
    """
    if _has_recorded_signature_block(mission):
        return {
            "name": mission.mission_order_signer_name,
            "rank": mission.mission_order_signer_rank,
            "branch": mission.mission_order_signer_branch,
            "designation": mission.mission_order_signer_designation,
            "as_commander": bool(mission.mission_order_signer_as_commander),
        }
    as_commander = _signing_as_commander(mission, signer)
    return {
        "name": _person_name(signer) if signer is not None else "",
        "rank": (getattr(signer, "rank", "") or "").strip(),
        "branch": (getattr(signer, "branch_of_service", "") or "").strip(),
        "designation": _signer_designation(
            mission, signer, as_commander=as_commander
        ),
        "as_commander": as_commander,
    }


def _has_recorded_signature_block(mission) -> bool:
    """Whether this order carries the block recorded when it was signed."""
    return bool(
        mission.mission_order_signed_at is not None
        and (
            mission.mission_order_signer_designation
            or mission.mission_order_signer_as_commander
            or mission.mission_order_signer_name
        )
    )


def _signature_path(user):
    if user is None or not getattr(user, "signature_filename", ""):
        return None
    path = Path(settings.LEGACY_SIGNATURE_DIR) / os.path.basename(
        user.signature_filename
    )
    return path if path.is_file() else None


def _signature_flowable(user):
    path = _signature_path(user)
    if path is None:
        return None
    try:
        return Image(str(path), width=110, height=42)
    except Exception:
        return None


def _personnel_rows(mission, trips):
    """Active mission personnel, plus any assigned driver not already listed."""
    rows = list(
        mission.personnel.filter(is_active=True)
        .select_related("user")
        .order_by("id")
    )
    listed = {row.user_id for row in rows}

    for trip in trips:
        for link in trip.vehicles.all():
            driver = link.driver
            if driver is not None and driver.id not in listed:
                rows.append(
                    type("DriverRow", (), {
                        "id": driver.id,
                        "user": driver,
                        "user_id": driver.id,
                        "role": "Driver",
                        "custom_role": "",
                    })()
                )
                listed.add(driver.id)

    rows.sort(key=lambda row: (ROLE_ORDER.get(row.role, 50), row.id))
    table_rows = [[
        Paragraph("<b>NR</b>", _style("table_head")),
        Paragraph("<b>RANK/NAME/BOS</b>", _style("table_head")),
        Paragraph("<b>REMARKS</b>", _style("table_head")),
    ]]

    for number, row in enumerate(rows, start=1):
        user = row.user
        name = " ".join(
            part
            for part in (
                user.full_name,
                user.serial_number,
                user.branch_of_service,
            )
            if part
        )
        table_rows.append([
            Paragraph(str(number), _style("table_cell")),
            Paragraph(_text(name), _style("table_cell")),
            Paragraph(_text(_role_label(row)), _style("table_cell")),
        ])

    if len(table_rows) == 1:
        table_rows.append([
            Paragraph("—", _style("table_cell")),
            Paragraph("No personnel recorded in this mission.", _style("table_cell")),
            Paragraph("—", _style("table_cell")),
        ])

    table = Table(
        table_rows,
        colWidths=[30, 276, CONTENT_WIDTH - 306],
        repeatRows=1,
    )
    table.setStyle(
        _table_style(
            [
                ("GRID", (0, 0), (-1, -1), 0.45, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    return table, len(rows)


def _route_text(mission, trips):
    """The first planned route, falling back to the mission header."""
    trip = next((row for row in trips if list(row.itinerary.all())), None)
    stops = list(trip.itinerary.all()) if trip else []
    if stops:
        origin = stops[0].location_name
        last = stops[-1]
        is_return = (
            last.location_name.strip().lower().endswith("(return)")
            or (last.remarks or "").strip().lower() == "origin"
        )
        destination_index = len(stops) - 2 if is_return and len(stops) > 1 else len(stops) - 1
        destination = stops[destination_index].location_name
        via = [
            stop.location_name
            for stop in stops[1:destination_index]
            if stop.location_name
        ]
        route = f"from {origin} to {destination}"
        if via:
            route += " via " + ", ".join(via)
        return route + " and vice versa"

    origin = (
        (mission.unit.location or "").strip()
        or (mission.unit.name or "").strip()
        or "this station"
    )
    destination = (
        (mission.location_name or "").strip()
        or getattr(getattr(mission, "ground_detail", None), "operational_area", "")
        or "the mission area"
    )
    return f"from {origin} to {destination} and vice versa"


def _itinerary_text(mission, trips):
    """The planned stop sequence, with the origin repeated for a round trip."""
    trip = next((row for row in trips if list(row.itinerary.all())), None)
    stops = list(trip.itinerary.all()) if trip else []
    names = [
        stop.location_name.strip()
        .removesuffix(" (return)")
        .removesuffix(" (Return)")
        .strip()
        for stop in stops
        if stop.location_name and stop.location_name.strip()
    ]
    if names:
        if len(names) > 1 and names[0].casefold() != names[-1].casefold():
            names.append(names[0])
        return " - ".join(names)
    route = _route_text(mission, trips)
    return route.removeprefix("from ").replace(" and vice versa", "")


def _duration_text(mission):
    start = mission.start_at
    end = mission.end_at
    if start is None:
        return "Until completion of mission."
    if end is not None and end.date() != start.date():
        return f"{_long_date(start)} to {_long_date(end)}."
    return f"{_long_date(start)} until completion of mission."


def _transportation_text(trips):
    labels: list[str] = []
    for trip in trips:
        for link in trip.vehicles.all():
            vehicle = link.vehicle
            make = (
                vehicle.make_model
                or vehicle.make
                or vehicle.vehicle_type
                or "Vehicle"
            ).strip()
            if vehicle.is_pov:
                owner = vehicle.owner.full_name if vehicle.owner_id else "POV"
                label = f"{make} (POV — {owner})"
            else:
                label = f"{make} # {vehicle.registration_number}"
            if label not in labels:
                labels.append(label)
    return ", ".join(labels) + "." if labels else "Not specified in the mission plan."


def _style(name):
    styles = getSampleStyleSheet()
    if name == "vision":
        return ParagraphStyle(
            "mission_order_vision",
            parent=styles["Normal"],
            fontName=FONT_ITALIC,
            fontSize=12,
            leading=14,
            alignment=1,
        )
    if name == "hierarchy":
        return ParagraphStyle(
            "mission_order_hierarchy",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
            alignment=1,
        )
    if name == "current":
        return ParagraphStyle(
            "mission_order_current",
            parent=styles["Normal"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=14,
            alignment=1,
        )
    if name == "address":
        return ParagraphStyle(
            "mission_order_address",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
            alignment=1,
        )
    if name == "right":
        return ParagraphStyle(
            "mission_order_right",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
            alignment=2,
        )
    if name == "title":
        return ParagraphStyle(
            "mission_order_title",
            parent=styles["Normal"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=14,
            alignment=0,
            spaceBefore=0,
            spaceAfter=0,
        )
    if name == "number":
        return ParagraphStyle(
            "mission_order_number",
            parent=styles["Normal"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=14,
            alignment=0,
            spaceBefore=0,
            spaceAfter=0,
        )
    if name == "body":
        return ParagraphStyle(
            "mission_order_body",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
            alignment=4,
            firstLineIndent=2 * TAB_WIDTH,
            spaceAfter=14,
        )
    if name == "table_head":
        return ParagraphStyle(
            "mission_order_table_head",
            parent=styles["Normal"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=14,
        )
    if name == "table_cell":
        return ParagraphStyle(
            "mission_order_table_cell",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
        )
    if name == "signature":
        return ParagraphStyle(
            "mission_order_signature",
            parent=styles["Normal"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=14,
            alignment=0,
        )
    if name == "signature_regular":
        return ParagraphStyle(
            "mission_order_signature_regular",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
            alignment=0,
        )
    if name == "signature_label":
        return ParagraphStyle(
            "mission_order_signature_label",
            parent=styles["Normal"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=14,
            alignment=0,
        )
    if name == "signature_rank":
        return ParagraphStyle(
            "mission_order_signature_rank",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
            alignment=0,
        )
    if name == "signature_branch":
        return ParagraphStyle(
            "mission_order_signature_branch",
            parent=styles["Normal"],
            fontName=FONT_REGULAR,
            fontSize=12,
            leading=14,
            alignment=2,
        )
    return ParagraphStyle(
        "mission_order_default",
        parent=styles["Normal"],
        fontName=FONT_REGULAR,
        fontSize=12,
        leading=14,
    )


def _table_style(commands):
    return TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
            ("FONTSIZE", (0, 0), (-1, -1), 12),
            *commands,
        ]
    )


class _NumberedParagraph(Flowable):
    """A justified paragraph whose number sits one tab before the text."""

    def __init__(self, number, text, style, space_after=None):
        super().__init__()
        self.number = number
        self.paragraph = Paragraph(text, style)
        self.spaceAfter = style.spaceAfter if space_after is None else space_after

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        self.paragraph.width = availWidth
        self.height = self.paragraph.wrap(availWidth, availHeight)[1]
        return self.width, self.height

    def draw(self):
        self.paragraph.drawOn(self.canv, 0, 0)
        if paraFontSizeHeightOffset:
            baseline = self.height - self.paragraph.style.fontSize
        else:
            baseline = self.height - self.paragraph.blPara.ascent
        self.canv.saveState()
        self.canv.setFillColor(colors.black)
        self.canv.setFont(FONT_REGULAR, self.paragraph.style.fontSize)
        self.canv.drawString(TAB_WIDTH, baseline, self.number)
        self.canv.restoreState()


def _barcode_flowable(value):
    if not value:
        return None
    try:
        drawing = createBarcodeDrawing(
            "Code128",
            value=value,
            width=BARCODE_SYMBOL_WIDTH,
            height=11,
            quiet=False,
            humanReadable=False,
        )
        if drawing.contents:
            drawing.contents[0].barFillColor = BARCODE_COLOR
        return drawing
    except Exception:
        return None


class _SignatureOverlay(Flowable):
    """Layered signature image, barcode and typed signer name.

    The signature is drawn first so a scan with an opaque white background
    cannot hide the typed name. The image is centred over the name and the
    barcode sits to its right; the name is painted last, on top of both.
    """

    def __init__(self, *, width, name, image_path=None, barcode=None):
        super().__init__()
        self.width = width
        self.name = name
        self.image_path = image_path
        self.barcode = barcode
        self.image_width = 110
        self.image_height = 42
        self.name_width = pdfmetrics.stringWidth(name, FONT_BOLD, 12)
        barcode_height = getattr(barcode, "height", 0)
        self.height = max(self.image_height, barcode_height) + 12

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        barcode_width = getattr(self.barcode, "width", 0) if self.barcode else 0
        max_image_x = max(self.width - barcode_width - self.image_width - 6, 0)
        image_x = min(max((self.name_width - self.image_width) / 2, 0), max_image_x)
        barcode_x = min(
            image_x + self.image_width + 6,
            max(self.width - barcode_width, 0),
        )
        image_y = 4
        if self.image_path is not None:
            try:
                canvas.drawImage(
                    str(self.image_path),
                    image_x,
                    image_y,
                    width=self.image_width,
                    height=self.image_height,
                    mask="auto",
                )
            except Exception:
                pass
        if self.barcode is not None:
            try:
                self.barcode.drawOn(
                    canvas,
                    barcode_x,
                    max(self.height - getattr(self.barcode, "height", 0), image_y),
                )
            except Exception:
                pass
        canvas.setFillColor(colors.black)
        canvas.setFont(FONT_BOLD, 12)
        canvas.drawString(0, 1, self.name)
        canvas.restoreState()


class _AuthenticityBarcode(Flowable):
    """PAF-branded barcode block matching the reference signature block.

    The supplied PAF seal and two-line unit label sit above purple Code 128
    bars, with the numeric barcode value underneath. The block is 67.1 x 38
    points so it prints at the same size as the reference document. Code 128
    uses fewer, better-separated bars than the reference's Code 39 and remains
    readable at ordinary print resolution.
    """

    def __init__(self, value):
        super().__init__()
        self.value = value
        self.width = BARCODE_WIDTH
        self.height = BARCODE_HEIGHT
        self.barcode = _barcode_flowable(value)

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        logo_path = Path(settings.LEGACY_STATIC_DIR) / "logo.png"
        if logo_path.is_file():
            try:
                canvas.drawImage(
                    str(logo_path),
                    0,
                    23,
                    width=BARCODE_LOGO_SIZE,
                    height=BARCODE_LOGO_SIZE,
                    mask="auto",
                )
            except Exception:
                pass
        canvas.setFillColor(BARCODE_TEXT_COLOR)
        canvas.setFont(FONT_REGULAR, 8)
        canvas.drawString(22, 30, "PHILIPPINE")
        canvas.drawString(22, 22, "AIR FORCE")
        if self.barcode is not None:
            try:
                self.barcode.drawOn(canvas, 0, 9)
            except Exception:
                pass
        canvas.setFillColor(BARCODE_TEXT_COLOR)
        canvas.setFont(FONT_REGULAR, 8.5)
        canvas.drawString(0, 1, self.value)
        canvas.restoreState()


def _unit_logo(unit_id, fallback=True):
    if unit_id:
        logo = _unit_logo_flowable(unit_id, getSampleStyleSheet())
        if logo is not None:
            logo.drawWidth = 48
            logo.drawHeight = 48
            return logo
    if not fallback:
        return ""
    default = Path(settings.LEGACY_STATIC_DIR) / "logo.png"
    if default.is_file():
        try:
            return Image(str(default), width=48, height=48)
        except Exception:
            pass
    return ""


def _draw_page_header(canvas):
    """Draw the PAF vision in the page header, outside the body frame."""
    canvas.saveState()
    canvas.setFont(FONT_ITALIC, 12)
    canvas.setFillColor(colors.black)
    lines = simpleSplit(HEADER_TEXT, FONT_ITALIC, 12, CONTENT_WIDTH)
    x = LEFT_MARGIN + CONTENT_WIDTH / 2
    y = PAGE_HEIGHT - 18
    for line in lines:
        canvas.drawCentredString(x, y, line)
        y -= 14
    canvas.restoreState()


def _qr_matrix(data):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def _draw_qr(canvas, matrix, x, y, size):
    """Draw the QR as vector modules so it stays sharp at any print size."""
    module = size / len(matrix)
    canvas.saveState()
    canvas.setFillColor(colors.black)
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


def generate_mission_order_pdf(mission, *, verification_url=None) -> bytes:
    """Render ``mission`` as a Mission Order and return the PDF bytes."""
    _ensure_arial()
    unit = mission.unit
    hierarchy = unit_hierarchy_display(unit)
    trips = list(
        mission.trips.exclude(status="cancelled")
        .prefetch_related("itinerary", "vehicles__vehicle", "vehicles__driver")
        .order_by("departure_at", "id")
    )
    personnel, personnel_count = _personnel_rows(mission, trips)
    route = _route_text(mission, trips)
    itinerary = _itinerary_text(mission, trips)
    purpose = (mission.purpose or mission.title or "").strip() or "Not specified."
    signer = _signer_for(mission)
    commander_title = COMMANDER_TITLES.get(unit.category, "COMMANDER")
    # A commander signs in their own right; the order is not signed "for" them.
    # Once signed, the block is the one recorded at signature, not today's.
    block = signature_block(mission, signer)
    designation = block["designation"]
    signing_as_commander = block["as_commander"]
    issued = mission.created_at or timezone.now()
    order_number = _order_number(mission)
    if not verification_url:
        verification_url = _default_verification_url(mission)
    qr_matrix = _qr_matrix(verification_url)

    header_width = CONTENT_WIDTH - 116
    header_text = [
        fitted_paragraph(_text(line), _style("hierarchy"), header_width)
        for line in hierarchy["hierarchy_lines"]
    ]
    header_text.append(
        fitted_paragraph(
            _text(hierarchy["current_unit"]), _style("current"), header_width
        )
    )
    if hierarchy.get("location"):
        header_text.append(
            fitted_paragraph(_text(unit.location), _style("address"), header_width)
        )

    text_block = Table([[item] for item in header_text], colWidths=[CONTENT_WIDTH - 116])
    text_block.setStyle(
        _table_style(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    # The higher (mother) unit's seal is on the left; the current unit's seal is
    # on the right. "Mother unit" is the immediate parent (the wing for a group,
    # the group for a squadron), not the PAF root.
    mother_unit_id = unit.parent_id if unit is not None else None
    header = Table(
        [[
            _unit_logo(mother_unit_id, fallback=False),
            text_block,
            _unit_logo(hierarchy.get("current_unit_id"), fallback=True),
        ]],
        colWidths=[58, CONTENT_WIDTH - 116, 58],
    )
    header.setStyle(
        _table_style(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    date_line = Table(
        [[
            Paragraph("Opns", _style("table_cell")),
            Paragraph(_text(_long_date(issued)), _style("right")),
        ]],
        colWidths=[CONTENT_WIDTH / 2, CONTENT_WIDTH / 2],
    )
    date_line.setStyle(
        _table_style(
            [
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    signature_label = Table(
        [[Paragraph(f"FOR THE {commander_title}:", _style("signature_label"))]],
        colWidths=[CONTENT_WIDTH],
    )
    signature_label.setStyle(
        _table_style(
            [
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2 * TAB_WIDTH),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    if signing_as_commander:
        # The commander's own signature carries no "for the commander" line.
        signature_label = None

    elements = [
        header,
        Spacer(1, 8),
        date_line,
        Spacer(1, 14),
        Paragraph("MISSION ORDER", _style("title")),
        Paragraph(f"NUMBER <u>{_text(order_number)}</u>", _style("number")),
        Spacer(1, 28),
        _NumberedParagraph(
            "1.",
            f"You are hereby directed to proceed {_text(route)}.",
            _style("body"),
            space_after=14,
        ),
        personnel,
        Spacer(1, 14),
        _NumberedParagraph("2.", f"<b>Purpose:</b> {_text(purpose)}", _style("body")),
        _NumberedParagraph(
            "3.",
            f"<b>Duration:</b> {_text(_duration_text(mission))}",
            _style("body"),
        ),
        _NumberedParagraph(
            "4.",
            f"<b>Transportation:</b> {_text(_transportation_text(trips))}",
            _style("body"),
        ),
        _NumberedParagraph(
            "5.",
            f"<b>Itinerary:</b> {_text(itinerary)}.",
            _style("body"),
        ),
        _NumberedParagraph(
            "6.",
            "May wear military/civilian uniform and authorized to carry "
            "government-issued firearms while in the performance of their mission.",
            _style("body"),
        ),
        _NumberedParagraph(
            "7.",
            "Above-mentioned personnel will return to this station upon completion "
            "of the said mission.",
            _style("body"),
        ),
        _NumberedParagraph("8.", "Be guided accordingly.", _style("body")),
    ]

    signer_rows = []
    if signer is not None:
        name_text = block["name"].replace("\ufffd", "?").upper()
        name_width = max(pdfmetrics.stringWidth(name_text, FONT_BOLD, 12), 1)
        rank = (block["rank"] or "").strip()
        branch = (block["branch"] or "").strip()
        signer_rows.append(
            [
                _SignatureOverlay(
                    width=CONTENT_WIDTH - SIGNER_INDENT,
                    name=name_text,
                    image_path=_signature_path(signer),
                    barcode=_AuthenticityBarcode(
                        signature_barcode_value(mission, signer)
                    ),
                )
            ]
        )
        if rank or branch:
            rank_width = pdfmetrics.stringWidth(rank, FONT_REGULAR, 12)
            branch_width = pdfmetrics.stringWidth(branch, FONT_REGULAR, 12)
            rank_row = Table(
                [[
                    Paragraph(_text(rank), _style("signature_rank")),
                    "",
                    Paragraph(_text(branch), _style("signature_branch")),
                ]],
                colWidths=[
                    rank_width,
                    max(name_width - rank_width - branch_width, 0),
                    branch_width,
                ],
            )
            rank_row.setStyle(
                _table_style(
                    [
                        ("ALIGN", (0, 0), (0, 0), "LEFT"),
                        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            rank_row.hAlign = "LEFT"
            signer_rows.append([rank_row])

        if designation:
            signer_rows.append(
                [Paragraph(_text(designation), _style("signature_regular"))]
            )
    elif unit.commanding_officer:
        signer_rows = [
            [Spacer(1, 56)],
            [
                Paragraph(
                    _text(unit.commanding_officer).upper(),
                    _style("signature"),
                )
            ],
        ]

    if signer_rows:
        signer_width = CONTENT_WIDTH - SIGNER_INDENT
        signer_table = Table(signer_rows, colWidths=[signer_width])
        signer_table.setStyle(
            _table_style(
                [
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        signer_block = Table(
            [["", signer_table]],
            colWidths=[SIGNER_INDENT, signer_width],
        )
        signer_block.setStyle(
            _table_style(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        if signature_label is None:
            elements.append(signer_block)
        else:
            elements.append(KeepTogether([signature_label, signer_block]))
    elif signature_label is not None:
        elements.append(signature_label)

    if personnel_count == 0:
        elements.append(Spacer(1, 8))
        elements.append(
            Paragraph(
                "Note: no active personnel are recorded in this mission yet.",
                ParagraphStyle(
                    "mission_order_note",
                    parent=_style("signature_regular"),
                    fontSize=12,
                    leading=14,
                    textColor=colors.HexColor("#8b0000"),
                ),
            )
        )

    buffer = BytesIO()

    def footer(canvas, doc):
        _draw_page_header(canvas)
        _draw_qr(canvas, qr_matrix, QR_X, QR_Y, QR_SIZE)
        canvas.saveState()
        footer_path = Path(settings.LEGACY_STATIC_DIR) / "Page_Footer.png"
        if footer_path.is_file():
            try:
                Image(str(footer_path), width=360, height=40).drawOn(
                    canvas,
                    (PAGE_WIDTH / 2) - 150,
                    15,
                )
            except Exception:
                pass
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=LEFT_MARGIN - FRAME_PADDING,
        rightMargin=RIGHT_MARGIN - FRAME_PADDING,
        topMargin=TOP_MARGIN - FRAME_PADDING,
        bottomMargin=BOTTOM_MARGIN - FRAME_PADDING,
        initialFontName=FONT_REGULAR,
        initialFontSize=12,
        initialLeading=14,
        title="Mission Order",
        author="Tactical Operations Wings Enterprise Management System",
    )
    doc.build(elements, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
