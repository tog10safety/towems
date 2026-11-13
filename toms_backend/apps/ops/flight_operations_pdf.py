"""Daily Flight Operations sheet.

The planning PDF is a schedule: it prints ETD/ETA. Once the day is flown, the
same sheet is printed as an actual record: ATD/ATA. Planned and actual times
are stored separately, so exporting an actual sheet never rewrites the plan.
"""

from __future__ import annotations

from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core import timeutils

PAGE_SIZE = landscape(A4)
LEFT_MARGIN = 14 * mm
RIGHT_MARGIN = 14 * mm
TOP_MARGIN = 12 * mm
BOTTOM_MARGIN = 12 * mm
CONTENT_WIDTH = PAGE_SIZE[0] - LEFT_MARGIN - RIGHT_MARGIN

NAVY = colors.HexColor("#1f3864")
HEAD_BLUE = colors.HexColor("#4472c4")
GRID = colors.black
INK = colors.HexColor("#1c2333")


def _styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="FlightTitle",
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            alignment=1,
            textColor=INK,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FlightSub",
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            alignment=1,
            textColor=INK,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FlightCell",
            fontName="Helvetica",
            fontSize=7.2,
            leading=9,
            textColor=INK,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FlightHead",
            fontName="Helvetica-Bold",
            fontSize=7.4,
            leading=9,
            textColor=colors.white,
        )
    )
    return styles


def _text(value) -> str:
    return escape(str(value or "")).replace("\n", "<br/>")


def _time(value, *, actual: bool) -> str:
    if value is None:
        return "—" if actual else "TBD"
    if hasattr(value, "strftime"):
        return value.strftime("%H%M")
    return _text(value)


def _element_rows(element):
    """Return (aircraft label, crew label, legs) rows for one element."""
    aircraft = list(element.aircraft.all())
    if not aircraft:
        return [("", "", [])]
    return [
        (
            " · ".join(
                part
                for part in (
                    plane.aircraft_type,
                    f"# {plane.tail_number}" if plane.tail_number else "",
                    plane.callsign,
                    "LEAD" if plane.is_lead and len(aircraft) > 1 else "",
                )
                if part
            ),
            "\n".join(
                f"[{row.role}] {row.name}" for row in plane.crew.all()
            ),
            list(plane.itineraries.all()),
        )
        for plane in aircraft
    ]


def _leg_text(leg, element):
    route = leg.route_label
    mission = leg.mission or element.mission or ""
    if leg.overflight and route:
        route = f"{route} (overflight)"
    if leg.return_to_origin and route and not leg.overflight:
        route = f"{route} (return)"
    return route, mission


def generate_flight_operations_pdf(missions, *, operation_date, mode="schedule") -> bytes:
    styles = _styles()
    actual = mode == "actual"
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title=f"Flight Operations {operation_date}",
        author="Tactical Operations Wing Eastern Mindanao",
    )

    story = [
        Paragraph("PHILIPPINE AIR FORCE", styles["FlightSub"]),
        Paragraph("TACTICAL OPERATIONS WING EASTERN MINDANAO", styles["FlightSub"]),
        Paragraph("HEADQUARTERS TACTICAL OPERATIONS GROUP 10", styles["FlightSub"]),
        Paragraph("Brgy Bancasi, Butuan City", styles["FlightSub"]),
        Spacer(1, 5),
        Paragraph("FLIGHT OPERATIONS", styles["FlightTitle"]),
        Paragraph(
            f"dated {timeutils.format_date(operation_date)}"
            + (" · ACTUAL" if actual else " · SCHEDULE"),
            styles["FlightSub"],
        ),
        Spacer(1, 7),
    ]

    time_headers = ["ATD", "ATA"] if actual else ["ETD"]
    headers = [
        "NR",
        "ACFT TYPE",
        "PILOTS",
        *time_headers,
        "ITINERARIES",
        "MISSIONS",
    ]
    data = [[Paragraph(header, styles["FlightHead"]) for header in headers]]
    spans = []
    row_index = 1

    for mission in missions:
        detail = getattr(mission, "air_detail", None)
        if detail is None:
            continue
        elements = list(detail.elements.all())
        if not elements:
            continue
        for element in elements:
            element_start = row_index
            aircraft_rows = _element_rows(element)
            for aircraft_label, crew_label, legs in aircraft_rows:
                aircraft_start = row_index
                if not legs:
                    time_cells = [
                        Paragraph(_time(None, actual=actual), styles["FlightCell"])
                        for _ in time_headers
                    ]
                    data.append(
                        [
                            Paragraph(_text(element.sequence), styles["FlightCell"]),
                            Paragraph(_text(aircraft_label), styles["FlightCell"]),
                            Paragraph(_text(crew_label), styles["FlightCell"]),
                            *time_cells,
                            Paragraph("", styles["FlightCell"]),
                            Paragraph(_text(element.mission), styles["FlightCell"]),
                        ]
                    )
                    row_index += 1
                for leg in legs:
                    route, mission_text = _leg_text(leg, element)
                    data.append(
                        [
                            Paragraph(_text(element.sequence), styles["FlightCell"]),
                            Paragraph(_text(aircraft_label), styles["FlightCell"]),
                            Paragraph(_text(crew_label), styles["FlightCell"]),
                            *[
                                Paragraph(
                                    _time(
                                        getattr(leg, field),
                                        actual=actual,
                                    ),
                                    styles["FlightCell"],
                                )
                                for field in (
                                    ("atd", "ata") if actual else ("etd",)
                                )
                            ],
                            Paragraph(_text(route), styles["FlightCell"]),
                            Paragraph(_text(mission_text), styles["FlightCell"]),
                        ]
                    )
                    row_index += 1
                if row_index - aircraft_start > 1:
                    spans.append(
                        ("SPAN", (1, aircraft_start), (1, row_index - 1))
                    )
                    spans.append(
                        ("SPAN", (2, aircraft_start), (2, row_index - 1))
                    )
            if row_index - element_start > 1:
                spans.append(("SPAN", (0, element_start), (0, row_index - 1)))

    if len(data) == 1:
        data.append(
            [
                Paragraph("No Flight Operations were recorded for this date.", styles["FlightCell"]),
                *["" for _ in range(len(headers) - 1)],
            ]
        )

    time_widths = [65, 55] if actual else [65]
    fixed_widths = [24, 110, 125, *time_widths, 215]
    widths = [
        *fixed_widths,
        CONTENT_WIDTH - sum(fixed_widths),
    ]
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.6, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    style.extend(spans)
    table.setStyle(TableStyle(style))

    story.append(table)
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "Actual times are recorded from the flown legs. "
            "TBD is used when no planned time was entered; — means the actual time is not recorded.",
            styles["FlightSub"],
        )
    )
    doc.build(story)
    return buffer.getvalue()
