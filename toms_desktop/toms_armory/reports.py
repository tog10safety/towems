"""Printable PDF receipts/reports and Excel exports.

Receipts deliberately repeat the four identity fields required by the armory
standing orders: Firearm ID, Butt Number, Firearm Type and Serial Number.  The
PDFs are generated locally and never depend on the central server, so an offline
armorer can print immediately after a transaction.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import config
from .models import Ammunition, Assignment, Firearm, Personnel, Transaction

INK = colors.HexColor("#1b2419")
BRASS = colors.HexColor("#8a6d2f")
PALE = colors.HexColor("#eef1e8")
LINE = colors.HexColor("#b8c2b0")
MUTED = colors.HexColor("#5b6756")


def _safe_name(value: str, fallback: str = "report") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return text[:100] or fallback


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ArmoryTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            textColor=INK,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "ArmorySubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=10,
        ),
        "heading": ParagraphStyle(
            "ArmoryHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=BRASS,
            spaceBefore=10,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "ArmoryBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "ArmorySmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=9,
            textColor=INK,
        ),
        "cell": ParagraphStyle(
            "ArmoryCell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.1,
            leading=8.6,
            textColor=INK,
        ),
        "right": ParagraphStyle(
            "ArmoryRight",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            alignment=2,
            textColor=INK,
        ),
    }


def _footer(canvas, document, organisation: str):
    canvas.saveState()
    width, height = document.pagesize
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.4)
    canvas.line(16 * mm, 14 * mm, width - 16 * mm, 14 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(16 * mm, 9 * mm, f"{organisation} · TOWEMS Armory Management")
    canvas.drawRightString(
        width - 16 * mm,
        9 * mm,
        f"Generated {_stamp()} · Page {document.page}",
    )
    canvas.restoreState()


def _header_flow(styles, title: str, subtitle: str) -> list:
    return [
        Paragraph("TOWEMS ARMORY MANAGEMENT SYSTEM", styles["subtitle"]),
        Paragraph(title, styles["title"]),
        Paragraph(subtitle, styles["subtitle"]),
    ]


def _info_table(rows: Sequence[Sequence[str]], styles, columns: int = 4) -> Table:
    data = []
    for index in range(0, len(rows), 2):
        pair = rows[index : index + 2]
        line = []
        for item in pair:
            line.extend(
                [
                    Paragraph(f"<b>{item[0]}</b>", styles["cell"]),
                    Paragraph(str(item[1] or ""), styles["cell"]),
                ]
            )
        while len(line) < columns:
            line.extend(["", ""])
        data.append(line[:columns])
    table = Table(data, colWidths=[29 * mm, 57 * mm, 29 * mm, 57 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("BACKGROUND", (0, 0), (0, -1), PALE),
                ("BACKGROUND", (2, 0), (2, -1), PALE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _data_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    styles,
    widths: Sequence[float] | None = None,
    font_size: float = 7.1,
) -> Table:
    body = [[Paragraph(f"<b>{header}</b>", styles["cell"]) for header in headers]]
    for row in rows:
        body.append([Paragraph(str(value or ""), styles["cell"]) for value in row])
    if len(body) == 1:
        body.append([Paragraph("No records.", styles["cell"])] + [""] * (len(headers) - 1))
    table = Table(body, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, LINE),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#24311f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f8f3")]),
            ]
        )
    )
    # The header is white text on dark fill; Paragraph style above is ink, so
    # use a white-headed copy.
    for cell in table._cellvalues[0]:
        if isinstance(cell, Paragraph):
            cell.style = ParagraphStyle(
                "HeaderCell",
                parent=styles["cell"],
                textColor=colors.white,
                fontName="Helvetica-Bold",
            )
    return table


def _firearm_rows(transaction: Transaction) -> list[list[str]]:
    return [
        [
            line.firearm_id_snapshot,
            line.butt_number_snapshot,
            line.firearm_type.title(),
            line.serial_number_snapshot,
            line.condition_status.title(),
            line.inspection_remarks,
        ]
        for line in transaction.lines
        if line.firearm_id
    ]


def _ammo_rows(transaction: Transaction) -> list[list[str]]:
    return [
        [
            line.ammunition_name,
            line.ammunition_caliber,
            line.lot_number,
            str(line.quantity),
            line.unit_of_measure,
            line.condition_status.title(),
            line.inspection_remarks,
        ]
        for line in transaction.lines
        if line.ammunition_id
    ]


def write_receipt(transaction: Transaction, path: Path | str | None = None) -> Path:
    if path is None:
        filename = (
            f"{_safe_name(transaction.reference_number, 'receipt')}"
            f"-{datetime.now():%Y%m%d-%H%M%S}.pdf"
        )
        path = config.receipts_dir() / filename
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    label = {
        "issuance": "FIREARM AND AMMUNITION ISSUANCE RECEIPT",
        "return": "FIREARM AND AMMUNITION DEPOSIT / RETURN RECEIPT",
        "adjustment": "AMMUNITION STOCK ADJUSTMENT VOUCHER",
    }.get(transaction.transaction_type, "ARMORY TRANSACTION RECEIPT")
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=20 * mm,
        title=f"{transaction.reference_number} — {label.title()}",
        author=config.ORGANISATION,
    )
    story = _header_flow(
        styles,
        label,
        f"Reference {transaction.reference_number}"
        + (f"  ·  TOWEMS {transaction.remote_reference}" if transaction.remote_reference else ""),
    )
    mission = transaction.mission_order_reference or "Standalone / No Mission Order"
    info = [
        ("Transaction date", transaction.transaction_at.strftime("%Y-%m-%d %H:%M")),
        ("Status", transaction.status.upper()),
        ("Personnel", transaction.personnel_name),
        ("Rank", transaction.rank),
        ("Unit", transaction.unit_name),
        ("Mission Order", mission),
        ("Purpose / reason", transaction.purpose or "Mission-based issue"),
        ("Releasing armorer", transaction.releasing_armorer),
        ("Receiving person", transaction.receiving_personnel),
        ("Acknowledged by", transaction.acknowledgement_name),
        (
            "Acknowledged at",
            transaction.acknowledged_at.strftime("%Y-%m-%d %H:%M")
            if transaction.acknowledged_at
            else "",
        ),
        ("Supporting documents", transaction.supporting_documents),
    ]
    story.append(_info_table(info, styles))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Firearms", styles["heading"]))
    story.append(
        _data_table(
            ["Firearm ID", "Butt No.", "Type", "Serial No.", "Condition", "Inspection remarks"],
            _firearm_rows(transaction),
            styles,
            widths=[34 * mm, 24 * mm, 18 * mm, 38 * mm, 24 * mm, 34 * mm],
        )
    )
    ammo = _ammo_rows(transaction)
    if ammo:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Ammunition accountability", styles["heading"]))
        story.append(
            _data_table(
                ["Nomenclature", "Caliber", "Lot", "Qty", "UoM", "Condition", "Remarks"],
                ammo,
                styles,
                widths=[42 * mm, 20 * mm, 26 * mm, 13 * mm, 15 * mm, 26 * mm, 30 * mm],
            )
        )
    if transaction.discrepancy_notes:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Discrepancies / notes", styles["heading"]))
        story.append(Paragraph(transaction.discrepancy_notes, styles["body"]))
    if transaction.inspection_remarks:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Inspection remarks", styles["heading"]))
        story.append(Paragraph(transaction.inspection_remarks, styles["body"]))
    story.append(Spacer(1, 12 * mm))
    signatures = Table(
        [
            [
                Paragraph("Releasing armorer", styles["small"]),
                Paragraph("Receiving person", styles["small"]),
                Paragraph("Authorised witness / commander", styles["small"]),
            ],
            ["", "", ""],
            [
                Paragraph("Signature / date", styles["small"]),
                Paragraph("Signature / date", styles["small"]),
                Paragraph("Signature / date", styles["small"]),
            ],
        ],
        colWidths=[58 * mm, 58 * mm, 58 * mm],
        rowHeights=[6 * mm, 15 * mm, 5 * mm],
    )
    signatures.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, LINE),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, INK),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(signatures)
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "This receipt is generated from the local armory ledger. The transaction "
            "remains on the local record after synchronization and is traceable by its "
            "unique reference and UUID.",
            styles["small"],
        )
    )
    doc.build(
        story,
        onFirstPage=lambda canvas, doc_: _footer(canvas, doc_, config.ORGANISATION),
        onLaterPages=lambda canvas, doc_: _footer(canvas, doc_, config.ORGANISATION),
    )
    return path


def write_report(
    *,
    title: str,
    subtitle: str,
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    path: Path | str,
    landscape_mode: bool = True,
    generated_by: str = "",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    page = landscape(A4) if landscape_mode else A4
    doc = SimpleDocTemplate(
        str(path),
        pagesize=page,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=18 * mm,
        title=title,
        author=config.ORGANISATION,
    )
    story = _header_flow(styles, title, subtitle)
    usable = page[0] - 28 * mm
    count = max(1, len(headers))
    widths = [usable / count for _ in headers]
    story.append(_data_table(headers, rows, styles, widths=widths))
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            f"Prepared by: {generated_by or 'Armory user'}  ·  {_stamp()}",
            styles["small"],
        )
    )
    doc.build(
        story,
        onFirstPage=lambda canvas, doc_: _footer(canvas, doc_, config.ORGANISATION),
        onLaterPages=lambda canvas, doc_: _footer(canvas, doc_, config.ORGANISATION),
    )
    return path


def write_excel(path: Path | str, sheets: dict[str, tuple[Sequence[str], Iterable[Sequence]]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.remove(workbook.active)
    header_fill = PatternFill("solid", fgColor="24311F")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    for name, (headers, rows) in sheets.items():
        sheet = workbook.create_sheet(title=str(name)[:31] or "Sheet")
        sheet.append(list(headers))
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(vertical="center")
        for row in rows:
            sheet.append(
                [
                    value.isoformat() if isinstance(value, (date, datetime)) else value
                    for value in row
                ]
            )
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for index, header in enumerate(headers, start=1):
            width = max(10, min(42, len(str(header)) + 4))
            for row in list(sheet.iter_rows(min_col=index, max_col=index))[:400]:
                width = max(width, min(52, len(str(row[0].value or "")) + 2))
            sheet.column_dimensions[get_column_letter(index)].width = width
    workbook.save(path)
    return path


# ---------------------------------------------------------------------------
# Report data builders
# ---------------------------------------------------------------------------


def firearm_export_rows(firearms: Sequence[Firearm]) -> list[list]:
    return [
        [
            item.firearm_id,
            item.butt_number,
            item.firearm_type.title(),
            item.serial_number,
            item.manufacturer,
            item.model,
            item.caliber,
            item.condition_status.title(),
            item.custody_status.title(),
            item.assigned_personnel.full_name if item.assigned_personnel else "",
            item.assigned_unit.acronym if item.assigned_unit else "",
            item.storage_location,
            _date(item.date_last_inspected),
            item.remarks,
        ]
        for item in firearms
    ]


FIREARM_REPORT_HEADERS = [
    "Firearm ID",
    "Butt No.",
    "Type",
    "Serial No.",
    "Manufacturer",
    "Model",
    "Caliber",
    "Condition",
    "Custody",
    "Designated person",
    "Unit",
    "Storage",
    "Last inspected",
    "Remarks",
]


def ammunition_export_rows(lots: Sequence[Ammunition]) -> list[list]:
    return [
        [
            item.ammunition_id,
            item.nomenclature,
            item.caliber,
            item.ammunition_type,
            item.lot_number,
            item.quantity_received,
            item.quantity_issued,
            item.stock_balance,
            item.unit_of_measure,
            item.serviceability.title(),
            item.owning_unit.acronym if item.owning_unit else "",
            item.storage_location,
            _date(item.date_received),
            _date(item.expiration_date),
            item.remarks,
        ]
        for item in lots
    ]


AMMUNITION_REPORT_HEADERS = [
    "Ammunition ID",
    "Nomenclature",
    "Caliber",
    "Type",
    "Lot",
    "Received",
    "Issued",
    "Balance",
    "UoM",
    "Serviceability",
    "Unit",
    "Storage",
    "Received date",
    "Expiry",
    "Remarks",
]


def assignment_export_rows(rows: Sequence[Assignment]) -> list[list]:
    return [
        [
            item.assigned_at.strftime("%Y-%m-%d %H:%M"),
            item.personnel.display_name,
            item.personnel.serial_number,
            item.assignment_type.title(),
            item.firearm.firearm_id,
            item.firearm.butt_number,
            item.firearm.firearm_type.title(),
            item.firearm.serial_number,
            "Active" if item.is_active else "Closed",
            item.assigned_by,
            item.released_at.strftime("%Y-%m-%d %H:%M") if item.released_at else "",
            item.notes,
        ]
        for item in rows
    ]


ASSIGNMENT_REPORT_HEADERS = [
    "Assigned at",
    "Personnel",
    "Service No.",
    "Type",
    "Firearm ID",
    "Butt No.",
    "Firearm type",
    "Serial No.",
    "State",
    "Assigned by",
    "Released at",
    "Notes",
]


def transaction_export_rows(rows: Sequence[Transaction]) -> list[list]:
    output = []
    for transaction in rows:
        firearms = [
            f"{line.firearm_id_snapshot} / butt {line.butt_number_snapshot} / "
            f"{line.firearm_type.title()} / serial {line.serial_number_snapshot}"
            for line in transaction.lines
            if line.firearm_id
        ]
        ammo = [
            f"{line.ammunition_name} lot {line.lot_number}: {line.quantity} "
            f"{line.unit_of_measure}"
            for line in transaction.lines
            if line.ammunition_id
        ]
        output.append(
            [
                transaction.transaction_at.strftime("%Y-%m-%d %H:%M"),
                transaction.reference_number,
                transaction.transaction_type.title(),
                transaction.status.title(),
                transaction.personnel_name,
                transaction.rank,
                transaction.mission_order_reference,
                transaction.purpose,
                transaction.releasing_armorer,
                transaction.acknowledgement_name,
                "; ".join(firearms),
                "; ".join(ammo),
                transaction.discrepancy_notes,
                transaction.remote_reference,
            ]
        )
    return output


TRANSACTION_REPORT_HEADERS = [
    "Date",
    "Reference",
    "Type",
    "Status",
    "Personnel",
    "Rank",
    "Mission Order",
    "Purpose",
    "Releasing armorer",
    "Acknowledged by",
    "Firearms",
    "Ammunition",
    "Discrepancies",
    "TOWEMS reference",
]
