"""Consolidated Mission Brief PDF.

Reviewers walk the mission detail page frame by frame: the header, the
ground/air detail, the personnel list, the vehicle movement, the MRM
assessment, the approval route and finally the Mission Order. Air missions
omit the approval route and Mission Order. This module renders the same
record as a short review document.

The layout follows the corrected house format: A4, half-inch side margins,
Arial throughout, navy section bars, blue table heads, black grid, grey label
cells, a green approval treatment, and no header, logo or footer furniture.
The signed Mission Order and the approved MRM remain the authoritative
records; this is a review copy.
"""

from __future__ import annotations

import datetime as dt
import re
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core import timeutils
from apps.ops import mission_order_pdf as order_pdf
from apps.ops.mission_order_pdf import ROLE_ORDER, _role_label, _text

PAGE_WIDTH, PAGE_HEIGHT = A4
# The format sits its tables 31.5 pt from each side and starts the first
# section bar 54 pt from the top edge, with no running header or footer.
LEFT_MARGIN = 31.5
RIGHT_MARGIN = 32.0
TOP_MARGIN = 54.0
BOTTOM_MARGIN = 36.0
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN

NAVY = colors.HexColor("#1f3864")
HEAD_BLUE = colors.HexColor("#4472c4")
CELL_GREY = colors.HexColor("#f4f6fa")
GREEN = colors.HexColor("#00b050")
LABEL = colors.HexColor("#596270")
INK = colors.HexColor("#1c2333")
LINK_BLUE = colors.HexColor("#0070c0")
GRID = colors.black


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------


def _stamp(value) -> str:
    """``10 Nov 2026, 1547H`` - the format's own timestamp, without a zone."""
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, dt.datetime):
        return f"{timeutils.format_date(value)}, {timeutils.format_time(value)}"
    if isinstance(value, dt.date):
        return timeutils.format_date(value)
    return str(value)


def _clock(value) -> str:
    """``1705H`` - an air leg's time without repeating its date."""
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, dt.datetime):
        return timeutils.format_time(value)
    return str(value)


MGRS_IN_TEXT = re.compile(
    r"\b\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z][A-HJ-NP-V]\d{2,10}\b",
    re.IGNORECASE,
)


def _mgrs(value) -> str:
    match = MGRS_IN_TEXT.search(str(value or ""))
    return match.group(0) if match else ""


def _short_location(value) -> str:
    """The place name only, without a street address or trailing grid."""
    text = str(value or "").strip()
    if not text:
        return ""
    first = text.split(",")[0].strip()
    # A location recorded as a grid reference has no place name to shorten to;
    # keep the grid so the reader still knows where the point is.
    if first.upper().startswith("MGRS"):
        grid = _mgrs(first)
        return f"MGRS {grid}" if grid else first
    first = re.sub(r"\s*\([^)]*\)\s*$", "", first).strip()
    without_grid = MGRS_IN_TEXT.sub("", first)
    without_grid = re.sub(r"[\s,;:–-]+$", "", without_grid).strip()
    if not without_grid:
        grid = _mgrs(first)
        return f"MGRS {grid}" if grid else first or text
    return without_grid


def _role_location(value, point_role: str = "") -> str:
    """A target or landing zone keeps its role and MGRS; every other point is
    only its short place name."""
    role = "TGT" if point_role == "target" else "LZ" if point_role == "landing_zone" else ""
    if role:
        grid = _mgrs(value)
        return f"{role} ({grid})" if grid else role
    return _short_location(value)


def _percent(value) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _mark(label: str, good: bool, size: int = 7) -> str:
    """Green tag for an approval; plain text when the step is not approved."""
    if not good:
        return _text(label)
    return _tag(label, size)


def _tag(label: str, size: int = 7) -> str:
    """An inline green approval tag, as the format shows it."""
    return (
        f'<font backColor="#00b050" color="#ffffff" size="{size}">'
        f"<b>&nbsp;{_text(label)}&nbsp;</b></font>"
    )


def _styles():
    """Build the brief's styles after Arial has been registered."""
    # Assigned to the module so ``_label`` can stay a one-argument helper; a
    # document is always built through ``generate_mission_brief_pdf``.
    order_pdf._ensure_arial()
    base = getSampleStyleSheet()["Normal"]
    regular = order_pdf.FONT_REGULAR
    bold = order_pdf.FONT_BOLD
    italic = order_pdf.FONT_ITALIC

    def make(name, **kwargs):
        return ParagraphStyle(name, parent=base, **kwargs)

    styles = {
        "bar": make("brief_bar", fontName=regular, fontSize=10, leading=12,
                    textColor=colors.white),
        "label": make("brief_label", fontName=bold, fontSize=10, leading=12,
                      textColor=LABEL),
        "value": make("brief_value", fontName=regular, fontSize=10, leading=12,
                      textColor=INK),
        "cell": make("brief_cell", fontName=regular, fontSize=10, leading=12,
                     textColor=INK),
        "cell_bold": make("brief_cell_bold", fontName=bold, fontSize=10,
                          leading=12, textColor=INK),
        "head": make("brief_head", fontName=bold, fontSize=10.2, leading=12,
                     textColor=colors.white, alignment=1),
        "note": make("brief_note", fontName=italic, fontSize=9, leading=11,
                     textColor=LABEL),
    }
    global _STYLES
    _STYLES = styles
    return styles


def _bar(styles, title: str, suffix: str = ""):
    """A full-width navy section bar, carried in the table's first row."""
    text = f"<b>{_text(title)}</b>"
    if suffix:
        text += f" {_text(suffix)}"
    table = Table([[Paragraph(text, styles["bar"])]], colWidths=[CONTENT_WIDTH],
                  hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _grid_table(rows, widths, *, head_row=False, label_cells=(), merged_rows=(),
                extra_commands=()):
    """A bordered table sharing the format's grid, padding and fills."""
    table = Table(rows, colWidths=widths, hAlign="LEFT")
    commands = [
        # Cells that hold a plain string (merged rows) would otherwise fall back
        # to the table default, Helvetica, and pull a second font into the file.
        ("FONTNAME", (0, 0), (-1, -1), order_pdf.FONT_REGULAR),
        ("GRID", (0, 0), (-1, -1), 0.6, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.0),
        ("TOPPADDING", (0, 0), (-1, -1), 2.7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.7),
    ]
    if head_row:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BLUE),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, 0), 3.6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3.6),
        ]
    for column, start_row in label_cells:
        commands.append(("BACKGROUND", (column, start_row), (column, -1), CELL_GREY))
    for row_index, start_col, end_col in merged_rows:
        commands.append(("SPAN", (start_col, row_index), (end_col, row_index)))
    commands += list(extra_commands)
    table.setStyle(TableStyle(commands))
    return table


_STYLES = None


def _label(text, styles=None):
    return Paragraph(_text(text).upper(), (styles or _STYLES)["label"])


# ---------------------------------------------------------------------------
# 1. Mission summary
# ---------------------------------------------------------------------------


def _air_element_mission(detail) -> str:
    """The mission text shown on the air mission page, without duplicates."""
    if detail is None:
        return "—"
    values = []
    for element in detail.elements.order_by("sequence"):
        text = (element.mission or "").strip()
        if text and text not in values:
            values.append(text)
    return "; ".join(values) or "—"


def _summary_table(mission, detail, styles):
    unit = mission.unit
    osp = (
        getattr(detail, "osp_section", "")
        or mission.office_section
        or "—"
    )
    notes = getattr(detail, "ground_risk_notes", "")
    mgrs = mission.mgrs or getattr(detail, "mgrs", "") or ""
    location = _text(mission.location_name or "—")
    if mgrs:
        location = (
            f"{location}<br/><b><font color=\"#0070c0\">{_text(mgrs)}</font></b>"
        )

    def value(text):
        return Paragraph(_text(text) if text else "—", styles["value"])

    summary_label = "MISSION" if mission.category == "air" else "PURPOSE"
    summary_value = (
        _air_element_mission(detail)
        if mission.category == "air"
        else (_text(mission.purpose) or "—")
    )
    rows = [
        [
            _label("MISSION NR"),
            value(mission.mission_number),
            _label("CATEGORY"),
            value(mission.get_category_display()),
        ],
        [
            _label("UNIT"),
            value(getattr(unit, "acronym", "") or getattr(unit, "name", "")),
            _label("O/SPR"),
            value(osp),
        ],
        [
            _label("STATUS"),
            value(mission.get_status_display()),
            _label("LOCATION/ MGRS" if mgrs else "LOCATION"),
            Paragraph(location, styles["value"]),
        ],
        [
            _label("START"),
            value(_stamp(mission.start_at)),
            _label("END"),
            value(_stamp(mission.end_at)),
        ],
        [
            _label(summary_label),
            Paragraph(summary_value, styles["value"]),
            "",
            "",
        ],
    ]
    merged_rows = [(4, 1, 3)]
    # Risk notes are a ground-operations field and are not part of the air
    # mission brief.
    if mission.category == "ground":
        rows.append(
            [
                _label("RISK NOTES"),
                Paragraph(_text(notes) or "—", styles["value"]),
                "",
                "",
            ]
        )
        merged_rows.append((len(rows) - 1, 1, 3))
    return _grid_table(
        rows,
        [76.6, 188.0, 70.0, CONTENT_WIDTH - 76.6 - 188.0 - 70.0],
        label_cells=[(0, 0), (2, 0)],
        merged_rows=merged_rows,
    )


# ---------------------------------------------------------------------------
# 2. Personnel involved
# ---------------------------------------------------------------------------


def _personnel_table(mission, styles):
    people = list(
        mission.personnel.filter(is_active=True)
        .select_related("user")
        .order_by("id")
    )
    people.sort(key=lambda row: (ROLE_ORDER.get(row.role, 50), row.id))

    rows = [[
        Paragraph("NR", styles["head"]),
        Paragraph("RANK / NAME / SERIAL/BOS", styles["head"]),
        Paragraph("ROLE", styles["head"]),
    ]]
    for number, row in enumerate(people, start=1):
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
        rows.append([
            Paragraph(str(number), styles["cell"]),
            Paragraph(_text(name), styles["cell"]),
            Paragraph(_text(_role_label(row)), styles["cell"]),
        ])
    if len(rows) == 1:
        rows.append([
            Paragraph("—", styles["cell"]),
            Paragraph("No active personnel recorded in this mission.", styles["cell"]),
            Paragraph("—", styles["cell"]),
        ])

    nr_width = 26.0
    role_width = 220.9
    return _grid_table(
        rows,
        [nr_width, CONTENT_WIDTH - nr_width - role_width, role_width],
        head_row=True,
    )


# ---------------------------------------------------------------------------
# 3. Vehicle trip
# ---------------------------------------------------------------------------


def _movement_times(trip):
    """Actual arrival/departure per stop, with the dispatch departure.

    The itinerary keeps planned times; a completed trip's real times live in
    its movement events. The format prints what happened, so those win.
    """
    from apps.ops.models import MovementEvent

    events = list(
        MovementEvent.objects.filter(run__trip=trip)
        .order_by("actual_at", "id")
    )
    departures = {}
    arrivals = {}
    origin_departure = None
    for event in events:
        if event.actual_at is None:
            continue
        if event.kind == "depart_origin":
            # The driver's confirmation is the departure; the automatic
            # dispatch stamp that precedes it is not.
            origin_departure = event.actual_at
        elif event.stop_id is not None and event.kind in {
            "arrive_stop",
            "arrive_destination",
        }:
            arrivals.setdefault(event.stop_id, event.actual_at)
        elif event.stop_id is not None and event.kind == "depart_stop":
            departures.setdefault(event.stop_id, event.actual_at)
    return origin_departure, arrivals, departures


def _transport_line(trip):
    labels = []
    for link in trip.vehicles.all():
        vehicle = link.vehicle
        label = vehicle.display_name or vehicle.registration_number or "Vehicle"
        if vehicle.registration_number:
            label += f" ({vehicle.registration_number})"
        if link.driver_id:
            label += f" · Driver: {link.driver.full_name}"
        labels.append(label)
    return "; ".join(labels)


def _route_line(trip):
    if trip.route_status != "calculated":
        return ""
    distance = (
        f"{trip.route_distance_m / 1000:.1f} km" if trip.route_distance_m else "—"
    )
    duration = (
        f"{trip.route_duration_s / 60:.0f} min" if trip.route_duration_s else "—"
    )
    parts = ["Planned road route:", distance, f"· {duration}"]
    if trip.route_provider:
        parts.append(f"· {_text(trip.route_provider)}")
    return " ".join(parts)


def _trip_table(trip, styles):
    stops = sorted(trip.itinerary.all(), key=lambda row: (row.sequence, row.id))
    origin_departure, arrivals, departures = _movement_times(trip)
    origin_id = stops[0].id if stops else None

    rows = [[
        Paragraph("NR", styles["head"]),
        Paragraph("STOP", styles["head"]),
        Paragraph("MGRS", styles["head"]),
        Paragraph("ARRIVAL", styles["head"]),
        Paragraph("DEPARTURE", styles["head"]),
    ]]
    for index, stop in enumerate(stops, start=1):
        arrival = arrivals.get(stop.id) or stop.planned_arrival
        departure = departures.get(stop.id) or stop.planned_departure
        if stop.id == origin_id and departure is None:
            departure = origin_departure
        rows.append([
            Paragraph(str(index), styles["cell"]),
            Paragraph(_text(stop.location_name) or "—", styles["cell"]),
            Paragraph(_text(stop.mgrs) or "—", styles["cell"]),
            Paragraph(_stamp(arrival), styles["cell"]),
            Paragraph(_stamp(departure), styles["cell"]),
        ])
    if len(rows) == 1:
        rows.append([
            Paragraph("—", styles["cell"]),
            Paragraph("No stops recorded for this trip.", styles["cell"]),
            Paragraph("—", styles["cell"]),
            Paragraph("—", styles["cell"]),
            Paragraph("—", styles["cell"]),
        ])

    transport = _transport_line(trip) or "No vehicle assigned."
    line = f"<b>Transport:</b> {_text(transport)}"
    route = _route_line(trip)
    if route:
        line += (
            f'<br/><i><font size="9" color="#596270">{_text(route)}</font></i>'
        )
    rows.append([Paragraph(line, styles["cell"]), "", "", "", ""])

    nr_width = 26.0
    stop_width = 245.0
    mgrs_width = 100.0
    arrival_width = 90.0
    departure_width = CONTENT_WIDTH - nr_width - stop_width - mgrs_width - arrival_width
    return _grid_table(
        rows,
        [nr_width, stop_width, mgrs_width, arrival_width, departure_width],
        head_row=True,
        merged_rows=[(len(rows) - 1, 0, 4)],
    )


# ---------------------------------------------------------------------------
# 4. Mission risk management
# ---------------------------------------------------------------------------


def _risk_table(mission, styles):
    from apps.ops import approvals

    form = approvals.mrm_form(mission)
    approved = bool(getattr(form, "authority_approved", False))
    creator_signed = bool(getattr(form, "creator_signed", False))
    evaluator_signed = bool(getattr(form, "evaluator_signed", False))

    creator = getattr(form, "creator_name", "") or ""
    evaluator = getattr(form, "reviewer_name", "") or ""
    if not creator or not evaluator:
        # Before an assessment exists the mission's MRM team already names the
        # people who owe the signatures, so the brief shows them as pending
        # instead of leaving the cells empty.
        team = list(mission.mrm_team.filter(is_active=True).select_related("user"))
        lead = next((row for row in team if row.role == "lead"), None)
        reviewer = next((row for row in team if row.role == "reviewer"), None)
        if not creator:
            creator = getattr(getattr(lead, "user", None), "full_name", "")
        if not evaluator:
            evaluator = getattr(getattr(reviewer, "user", None), "full_name", "")
    creator = creator or "Not assigned"
    evaluator = evaluator or "Not assigned"

    if approved:
        status = "Approved"
    elif form is None:
        status = "Not yet assessed"
    else:
        status = form.get_status_display() or "Pending"

    category = getattr(form, "risk_category", "") or "—"
    score = _percent(getattr(form, "total_percent", None))
    number = getattr(form, "mrm_number", "") or "Not recorded"

    def value(text):
        return Paragraph(_text(text), styles["cell"])

    approval_cell = Paragraph(
        "<b>AUTHORITY APPROVAL</b> "
        f'<font color="#596270">(Creator)</font><br/>{_mark(status, approved)}',
        styles["cell"],
    )
    creator_cell = Paragraph(
        f"{_text(creator)}<br/>"
        f"{_mark('Approved' if creator_signed else 'Pending', creator_signed)}",
        styles["cell"],
    )
    evaluator_cell = Paragraph(
        f"{_text(evaluator)}<br/>"
        f"{_mark('Approved' if evaluator_signed else 'Pending', evaluator_signed)}",
        styles["cell"],
    )

    rows = [
        [
            _label("MRM NR"),
            value(number),
            _label("ASSESSMENT STATUS"),
            approval_cell,
        ],
        [
            _label("RISK CATEGORY"),
            value(category),
            _label("RISK SCORE"),
            value(score),
        ],
        [
            _label("CREATOR"),
            creator_cell,
            _label("EVALUATOR"),
            evaluator_cell,
        ],
    ]
    extra = []
    if approved:
        extra = [
            ("BACKGROUND", (1, 1), (1, 1), GREEN),
            ("BACKGROUND", (3, 1), (3, 1), GREEN),
        ]
    return _grid_table(
        rows,
        [77.0, 168.0, 88.0, CONTENT_WIDTH - 77.0 - 168.0 - 88.0],
        label_cells=[(0, 0), (2, 0)],
        extra_commands=extra,
    )


# ---------------------------------------------------------------------------
# 5. Mission approval and routing history
# ---------------------------------------------------------------------------


def _approval_rows(mission):
    """The eight posts the format lists, in order, with what each decided."""
    from apps.ops import approvals

    plan = approvals.route_plan(mission)
    stages = {stage["stage"]: stage for stage in plan.get("stages", [])}
    routing = list(mission.routing.all().order_by("created_at", "id"))
    submitted = next(
        (row for row in routing if row.stage == "operations_center"), None
    )
    creator = mission.created_by
    unit = mission.unit
    created_by = "—"
    if creator is not None:
        created_by = getattr(creator, "full_name", "") or creator.username
        acronym = getattr(unit, "acronym", "") or getattr(unit, "name", "")
        if acronym:
            created_by = f"{created_by}, {acronym}"

    rows = [
        {
            "stage": "Created By",
            "assignee": created_by,
            "status": "Approved" if submitted is not None else "Pending",
            "decided_at": submitted.created_at if submitted is not None else None,
            "comment": "",
        }
    ]

    form = approvals.mrm_form(mission)
    creator_name = getattr(form, "creator_name", "") or ""
    evaluator_name = getattr(form, "reviewer_name", "") or ""
    if not creator_name or not evaluator_name:
        team = list(mission.mrm_team.filter(is_active=True).select_related("user"))
        lead = next((row for row in team if row.role == "lead"), None)
        reviewer = next((row for row in team if row.role == "reviewer"), None)
        if not creator_name:
            creator_name = getattr(getattr(lead, "user", None), "full_name", "")
        if not evaluator_name:
            evaluator_name = getattr(getattr(reviewer, "user", None), "full_name", "")

    creator_signed = bool(getattr(form, "creator_signed", False))
    evaluator_signed = bool(getattr(form, "evaluator_signed", False))
    authority_approved = bool(getattr(form, "authority_approved", False))
    rows += [
        {
            "stage": "MRM Creator",
            "assignee": creator_name,
            "status": "Approved" if creator_signed else "Pending",
            "decided_at": (
                getattr(form, "creator_signature_date", None) if creator_signed else None
            ),
            "comment": "",
        },
        {
            "stage": "MRM Evaluator",
            "assignee": evaluator_name,
            "status": "Approved" if evaluator_signed else "Pending",
            "decided_at": (
                getattr(form, "evaluator_signature_date", None) if evaluator_signed else None
            ),
            "comment": "",
        },
        {
            "stage": "MRM Authority",
            "assignee": getattr(form, "authority_name", "") or "",
            "status": "Approved" if authority_approved else "Pending",
            "decided_at": (
                getattr(form, "authority_approval_date", None) if authority_approved else None
            ),
            "comment": "",
        },
    ]

    for role in approvals.ROUTE_ROLE_ORDER:
        stage = stages.get(approvals.ROUTE_STAGE_BY_ROLE[role])
        if stage is None or stage.get("status") == "waiting":
            rows.append(
                {
                    "stage": approvals.ROUTE_ROLE_LABELS[role],
                    "assignee": "",
                    "status": "",
                    "decided_at": None,
                    "comment": "",
                }
            )
            continue
        status = (stage.get("status") or "").replace("_", " ").title()
        rows.append(
            {
                "stage": stage.get("label") or approvals.ROUTE_ROLE_LABELS[role],
                "assignee": stage.get("assignee_name") or "",
                "status": status,
                "decided_at": stage.get("decided_at"),
                "comment": stage.get("comment") or "",
            }
        )
    return rows


def _approval_table(mission, styles):
    rows = [[
        Paragraph("STAGE", styles["head"]),
        Paragraph("ASSIGNEE", styles["head"]),
        Paragraph("DECISION/ DECIDED", styles["head"]),
        Paragraph("COMMENT", styles["head"]),
    ]]
    for entry in _approval_rows(mission):
        decision = ""
        if entry["status"]:
            decided_at = entry["decided_at"]
            decision = _text(entry["status"])
            if decided_at:
                decision += (
                    f'<br/><font size="9">{_text(_stamp(decided_at))}</font>'
                )
        rows.append([
            Paragraph(_text(entry["stage"]), styles["cell_bold"]),
            Paragraph(_text(entry["assignee"]) or "", styles["cell"]),
            Paragraph(decision, styles["cell"]),
            Paragraph(_text(entry["comment"]) or "", styles["cell"]),
        ])

    stage_width = 86.0
    assignee_width = 190.0
    decision_width = 96.0
    return _grid_table(
        rows,
        [
            stage_width,
            assignee_width,
            decision_width,
            CONTENT_WIDTH - stage_width - assignee_width - decision_width,
        ],
        head_row=True,
    )


# ---------------------------------------------------------------------------
# Air missions
# ---------------------------------------------------------------------------


def _air_legs(detail):
    """The lead aircraft's shared itinerary, in sequence order."""
    element = detail.elements.order_by("sequence").first()
    if element is None:
        return []
    aircraft = element.aircraft.order_by("sequence").first()
    if aircraft is None:
        return []
    return list(aircraft.itineraries.order_by("sequence"))


def _origin_role(legs, origin) -> str:
    """A departure that is the previous target/LZ keeps that role's MGRS."""
    grid = _mgrs(origin)
    for leg in legs:
        if not leg.point_role:
            continue
        if grid and grid == _mgrs(leg.destination):
            return leg.point_role
        if (
            _short_location(origin).casefold()
            and _short_location(origin).casefold()
            == _short_location(leg.destination).casefold()
        ):
            return leg.point_role
    return ""


def _air_departure(detail, legs) -> str:
    value = detail.departure_location or (legs[0].origin if legs else "")
    if not value:
        return "—"
    return _role_location(value, _origin_role(legs, value)) or "—"


def _air_destination(detail, legs) -> str:
    value = detail.destination_location or (legs[-1].destination if legs else "")
    if not value:
        return "—"
    role = legs[-1].point_role if legs else ""
    return _role_location(value, role) or "—"


def _air_route(detail, legs) -> str:
    """The same short origin/target/landing-zone route shown by the air form."""
    if legs:
        labels = []
        origin = _role_location(legs[0].origin, _origin_role(legs, legs[0].origin))
        if origin:
            labels.append(origin)
        for leg in legs:
            destination = _role_location(leg.destination, leg.point_role)
            if destination:
                labels.append(destination)
            elif leg.overflight:
                labels.append("Over")
        return " - ".join(labels)
    return " - ".join(
        _short_location(line)
        for line in (detail.route or "").splitlines()
        if line.strip()
    )


def _air_pilots(detail) -> str:
    rows = []
    for element in detail.elements.order_by("sequence").prefetch_related("aircraft__crew"):
        for aircraft in element.aircraft.all():
            crew = {row.role: row.name for row in aircraft.crew.all() if row.name}
            pic = crew.get("PIC", "")
            copilot = crew.get("CP", "")
            if not pic and not copilot:
                continue
            label = " / ".join(
                part for part in (aircraft.aircraft_type, aircraft.tail_number) if part
            )
            details = [
                f"PIC: {pic}" if pic else "",
                f"CP: {copilot}" if copilot else "",
            ]
            rows.append(
                f"{label}: {', '.join(part for part in details if part)}"
                if label
                else ", ".join(part for part in details if part)
            )
    if not rows:
        for row in detail.assigned_aircraft.all():
            label = " / ".join(
                part for part in (row.aircraft_model, row.tail_number) if part
            )
            details = [
                f"PIC: {row.pilot_in_command}" if row.pilot_in_command else "",
                f"CP: {row.co_pilot}" if row.co_pilot else "",
            ]
            if any(details):
                rows.append(
                    f"{label}: {', '.join(part for part in details if part)}"
                    if label
                    else ", ".join(part for part in details if part)
                )
    return "; ".join(rows) or "—"


def _air_pairs(detail):
    legs = _air_legs(detail)
    aircraft = [
        " · ".join(
            part
            for part in (
                row.aircraft_model,
                row.tail_number,
                row.callsign,
                "LEAD" if row.is_lead else "",
            )
            if part
        )
        for row in detail.assigned_aircraft.all()
    ]
    return [
        ("DATE", _stamp(detail.operation_date)),
        ("CONFIG", detail.get_mission_configuration_display()),
        ("ACFT COUNT", str(detail.aircraft_count)),
        ("AIRCRAFT", "; ".join(aircraft) or detail.aircraft_type or "—"),
        ("FLIGHT ID", detail.flight_identification or "—"),
        ("DEPART", _air_departure(detail, legs)),
        ("DESTINATION", _air_destination(detail, legs)),
        ("ETD", _clock(detail.scheduled_departure)),
        ("ETA", _clock(detail.scheduled_arrival)),
    ]


def _air_table(detail, styles):
    pairs = _air_pairs(detail)
    legs = _air_legs(detail)
    rows = []
    for start in range(0, len(pairs), 2):
        chunk = pairs[start : start + 2]
        row = []
        for label, value in chunk:
            row.append(_label(label))
            row.append(Paragraph(_text(value) or "—", styles["value"]))
        while len(row) < 4:
            row += [_label(""), Paragraph("", styles["value"])]
        rows.append(row)
    label_rows = []
    pilots = _air_pilots(detail)
    if pilots and pilots != "—":
        rows.append([
            _label("PILOTS"),
            Paragraph(_text(pilots), styles["value"]),
            "",
            "",
        ])
        label_rows.append((len(rows) - 1, 1, 3))
    route = _air_route(detail, legs)
    if route:
        rows.append([_label("ROUTE"), Paragraph(_text(route), styles["value"]), "", ""])
        label_rows.append((len(rows) - 1, 1, 3))
    supporting = [
        f"{unit.acronym} — {unit.name}" if unit.acronym else unit.name
        for unit in detail.supporting_units.all()
    ]
    if supporting:
        rows.append([
            _label("SUPPORTED UNITS"),
            Paragraph(_text(", ".join(supporting)), styles["value"]),
            "",
            "",
        ])
        label_rows.append((len(rows) - 1, 1, 3))

    label_cols = [(0, 0)] if rows else []
    width = 100.0
    return _grid_table(
        rows,
        [width, CONTENT_WIDTH / 2 - width, 63.8, CONTENT_WIDTH / 2 - 63.8],
        label_cells=label_cols,
        merged_rows=label_rows,
    )


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


def generate_mission_brief_pdf(mission, *, prepared_by=None) -> bytes:
    """Render the consolidated reviewer brief for ``mission`` as PDF bytes."""
    styles = _styles()
    # The detail row is optional in legacy data, and the reverse OneToOne
    # raises instead of returning None when it is absent.
    detail = (
        getattr(mission, "ground_detail", None)
        if mission.category == "ground"
        else getattr(mission, "air_detail", None)
    )

    story = [
        _bar(styles, "1. MISSION SUMMARY"),
        _summary_table(mission, detail, styles),
        Spacer(1, 13.5),
    ]

    if mission.category == "ground":
        story.append(_bar(styles, "2. PERSONNEL INVOLVED"))
        story.append(_personnel_table(mission, styles))
        story.append(Spacer(1, 13.5))

        trips = list(
            mission.trips.exclude(status="cancelled")
            .prefetch_related("itinerary", "vehicles__vehicle", "vehicles__driver")
            .order_by("departure_at", "id")
        )
        if trips:
            for trip in trips:
                story.append(
                    _bar(styles, "3. VEHICLE TRIP", f"[{trip.trip_number}]")
                )
                story.append(_trip_table(trip, styles))
                story.append(Spacer(1, 13.5))
        else:
            story.append(_bar(styles, "3. VEHICLE TRIP"))
            story.append(
                Paragraph(
                    "No vehicle trip is recorded for this mission.", styles["note"]
                )
            )
            story.append(Spacer(1, 13.5))

        story.append(_bar(styles, "4. MISSION RISK MANAGEMENT (MRM)"))
        story.append(_risk_table(mission, styles))
        story.append(Spacer(1, 13.5))

        story.append(_bar(styles, "5. MISSION APPROVAL AND ROUTING HISTORY"))
        story.append(_approval_table(mission, styles))
    else:
        flight = (detail.flight_identification if detail is not None else "") or ""
        story.append(_bar(styles, "2. FLIGHT", f"[{flight}]" if flight else ""))
        if detail is not None:
            story.append(_air_table(detail, styles))
        else:
            story.append(Paragraph("No flight detail recorded.", styles["note"]))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title=f"Mission Brief {mission.mission_number}",
        author="Tactical Operations Wings Enterprise Management System",
        subject="Consolidated mission record for review",
    )
    doc.build(story)
    return buffer.getvalue()
