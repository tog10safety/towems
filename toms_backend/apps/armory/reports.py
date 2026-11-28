"""PDF and Excel outputs for the Armory Management System.

Every report runs against the same unit-scoped querysets as the register, so a
report can never disclose a firearm or transaction the caller cannot open on
screen.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from io import BytesIO

from django.db.models import Q, Sum
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.core import timeutils
from apps.ops.models import Mission

from . import services
from .models import (
    Ammunition,
    ArmoryTransaction,
    ArmoryTransactionLine,
    Firearm,
    FirearmInspection,
    PersonnelFirearmAssignment,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

REPORT_SPECS = {
    "firearms": (
        "Complete Firearm Inventory",
        "Every registered firearm, including Firearm ID, Butt Number and Serial Number.",
    ),
    "ammunition": (
        "Ammunition Inventory",
        "Ammunition lots, receipts, issues and current stock balances.",
    ),
    "assignments": (
        "Personnel Firearm Assignment",
        "Active and historical designated rifle and pistol assignments.",
    ),
    "issuances": (
        "Firearm Issuance Report",
        "Mission-authorised releases, receipt references and approving authorities.",
    ),
    "returns": (
        "Firearm Deposit / Return Report",
        "Deposits linked to their original issuance, including inspection results.",
    ),
    "mission-accountability": (
        "Mission-Based Armory Accountability",
        "Issued and returned firearms and ammunition, grouped by Mission Order where linked.",
    ),
    "ammunition-reconciliation": (
        "Ammunition Consumption and Reconciliation",
        "Issued, returned, consumed and on-hand quantities by ammunition lot.",
    ),
    "inspections": (
        "Armory Inspection Report",
        "Firearm condition and inspection history.",
    ),
    "monthly-summary": (
        "Monthly Inventory Summary",
        "Armory holdings, movements and inspections for one calendar month.",
    ),
}


def _fmt_date(value) -> str:
    if not value:
        return ""
    return timeutils.format_date(value)


def _fmt_dt(value) -> str:
    if not value:
        return ""
    return timeutils.format_datetime(value)


def _bool(value) -> str:
    return "Yes" if value else "No"


def _person(user) -> str:
    return user.get_full_name() if user else ""


def _firearm_filter(queryset, params):
    value = (params.get("type") or "").strip()
    if value in {Firearm.TYPE_RIFLE, Firearm.TYPE_PISTOL}:
        queryset = queryset.filter(firearm_type=value)
    status_value = (params.get("status") or "").strip()
    if status_value:
        queryset = queryset.filter(custody_status=status_value)
    return queryset


def _txn_range(queryset, params):
    start = params.get("start") or ""
    end = params.get("end") or ""
    if start:
        queryset = queryset.filter(transaction_at__date__gte=start)
    if end:
        queryset = queryset.filter(transaction_at__date__lte=end)
    return queryset


def _table_data(report_slug, user, params):
    """Return ``(title, subtitle, headers, rows)`` for one report."""
    title, subtitle = REPORT_SPECS[report_slug]

    if report_slug == "firearms":
        rows = []
        queryset = _firearm_filter(services.scoped_firearms(user), params)
        for row in queryset:
            rows.append(
                [
                    row.firearm_id,
                    row.butt_number,
                    row.serial_number,
                    row.get_firearm_type_display(),
                    row.manufacturer,
                    row.model,
                    row.caliber,
                    row.get_condition_status_display(),
                    row.get_custody_status_display(),
                    _person(row.assigned_personnel),
                    row.assigned_unit.acronym if row.assigned_unit_id else "",
                    row.storage_location,
                    _fmt_date(row.date_last_inspected),
                    row.remarks,
                ]
            )
        return (
            title,
            subtitle,
            [
                "Firearm ID",
                "Butt No.",
                "Serial No.",
                "Type",
                "Make",
                "Model",
                "Caliber",
                "Condition",
                "Custody",
                "Assigned Personnel",
                "Unit",
                "Storage",
                "Last Inspected",
                "Remarks",
            ],
            rows,
        )

    if report_slug == "ammunition":
        rows = []
        queryset = services.scoped_ammunition(user)
        caliber = (params.get("caliber") or "").strip()
        if caliber:
            queryset = queryset.filter(caliber__iexact=caliber)
        for row in queryset:
            rows.append(
                [
                    row.ammunition_id,
                    row.nomenclature,
                    row.caliber,
                    row.ammunition_type,
                    row.lot_number,
                    row.unit_of_measure,
                    row.quantity_received,
                    row.quantity_issued,
                    row.stock_balance,
                    row.get_serviceability_display(),
                    row.storage_location,
                    _fmt_date(row.date_received),
                    _fmt_date(row.expiration_date),
                    _person(row.responsible_custodian),
                    row.owning_unit.acronym if row.owning_unit_id else "",
                    row.remarks,
                ]
            )
        return (
            title,
            subtitle,
            [
                "Ammunition ID",
                "Nomenclature",
                "Caliber",
                "Type",
                "Lot / Batch",
                "UOM",
                "Received",
                "Issued",
                "Balance",
                "Serviceability",
                "Storage",
                "Received Date",
                "Expiry",
                "Custodian",
                "Unit",
                "Remarks",
            ],
            rows,
        )

    if report_slug == "assignments":
        rows = []
        queryset = services.scoped_assignments(user)
        if (params.get("active") or "") != "all":
            queryset = queryset.filter(is_active=True)
        for row in queryset:
            firearm = row.firearm
            rows.append(
                [
                    _person(row.user),
                    row.user.rank or "",
                    row.user.serial_number or "",
                    row.user.branch_of_service or "",
                    row.user.unit.acronym if row.user.unit_id else "",
                    row.get_assignment_type_display(),
                    firearm.firearm_id,
                    firearm.butt_number,
                    firearm.serial_number,
                    firearm.manufacturer,
                    firearm.model,
                    firearm.caliber,
                    _bool(row.is_active),
                    _fmt_dt(row.assigned_at),
                    _fmt_dt(row.released_at),
                    _person(row.assigned_by),
                    row.notes,
                ]
            )
        return (
            title,
            subtitle,
            [
                "Personnel",
                "Rank",
                "Service No.",
                "BOS",
                "Unit",
                "Designation",
                "Firearm ID",
                "Butt No.",
                "Serial No.",
                "Make",
                "Model",
                "Caliber",
                "Active",
                "Assigned",
                "Released",
                "Assigned By",
                "Notes",
            ],
            rows,
        )

    if report_slug in {"issuances", "returns"}:
        is_return = report_slug == "returns"
        txn_type = (
            ArmoryTransaction.TYPE_RETURN
            if is_return
            else ArmoryTransaction.TYPE_ISSUANCE
        )
        queryset = _txn_range(
            services.scoped_transactions(user).filter(transaction_type=txn_type), params
        )
        rows = []
        for txn in queryset:
            for line in txn.lines.all():
                rows.append(
                    [
                        txn.reference_number,
                        _fmt_dt(txn.transaction_at),
                        txn.mission_order_reference or txn.mission.mission_number if txn.mission_id else txn.mission_order_reference,
                        txn.personnel_name,
                        txn.rank,
                        txn.branch_of_service,
                        txn.unit_name,
                        line.firearm_id_snapshot,
                        line.butt_number_snapshot,
                        line.serial_number_snapshot,
                        line.ammunition_name,
                        line.lot_number,
                        line.quantity if line.ammunition_id else "",
                        line.unit_of_measure,
                        line.condition_status,
                        _person(txn.releasing_armorer),
                        _person(txn.approving_authority),
                        txn.return_of.reference_number if txn.return_of_id else "",
                        txn.inspection_remarks,
                    ]
                )
            if not txn.lines.exists():
                rows.append(
                    [
                        txn.reference_number,
                        _fmt_dt(txn.transaction_at),
                        txn.mission_order_reference,
                        txn.personnel_name,
                        txn.rank,
                        txn.branch_of_service,
                        txn.unit_name,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        _person(txn.releasing_armorer),
                        _person(txn.approving_authority),
                        txn.return_of.reference_number if txn.return_of_id else "",
                        txn.inspection_remarks,
                    ]
                )
        return (
            title,
            subtitle,
            [
                "Reference",
                "Date / Time",
                "Mission Order",
                "Personnel",
                "Rank",
                "BOS",
                "Unit",
                "Firearm ID",
                "Butt No.",
                "Serial No.",
                "Ammunition",
                "Lot",
                "Qty",
                "UOM",
                "Condition",
                "Armorer",
                "Approving Authority",
                "Original Issue",
                "Remarks",
            ],
            rows,
        )

    if report_slug == "mission-accountability":
        queryset = _txn_range(
            services.scoped_transactions(user).filter(
                transaction_type=ArmoryTransaction.TYPE_ISSUANCE
            ),
            params,
        )
        rows = []
        for txn in queryset:
            returns = txn.returns.all()
            return_refs = ", ".join(row.reference_number for row in returns)
            rows.append(
                [
                    txn.mission.mission_number if txn.mission_id else "",
                    txn.mission.title if txn.mission_id else "",
                    txn.mission_order_reference,
                    txn.personnel_name,
                    txn.rank,
                    txn.branch_of_service,
                    txn.unit_name,
                    _fmt_dt(txn.transaction_at),
                    ", ".join(
                        line.firearm_id_snapshot
                        for line in txn.lines.all()
                        if line.firearm_id_snapshot
                    ),
                    sum(
                        line.quantity
                        for line in txn.lines.all()
                        if line.ammunition_id
                    ),
                    return_refs or "OUTSTANDING",
                    txn.get_status_display(),
                    _person(txn.releasing_armorer),
                    _person(txn.approving_authority),
                    txn.discrepancy_notes,
                ]
            )
        return (
            title,
            subtitle,
            [
                "Mission",
                "Mission Title",
                "Mission Order",
                "Personnel",
                "Rank",
                "BOS",
                "Unit",
                "Issued At",
                "Firearms Issued",
                "Ammunition Issued",
                "Return Reference",
                "Status",
                "Armorer",
                "Approving Authority",
                "Discrepancies",
            ],
            rows,
        )

    if report_slug == "ammunition-reconciliation":
        ammunition_rows = list(services.scoped_ammunition(user))
        ammo_ids = [row.id for row in ammunition_rows]
        issuance_lines = list(
            ArmoryTransactionLine.objects.filter(
                ammunition_id__in=ammo_ids,
                transaction__transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
            ).prefetch_related("return_lines")
        )
        returns_by_line = defaultdict(int)
        for line in issuance_lines:
            returns_by_line[line.id] = sum(
                int(row.quantity or 0) for row in line.return_lines.all()
            )
        by_ammo = defaultdict(lambda: {"issued": 0, "returned": 0, "consumed": 0})
        for line in issuance_lines:
            returned = returns_by_line.get(line.id, 0)
            by_ammo[line.ammunition_id]["issued"] += int(line.quantity or 0)
            by_ammo[line.ammunition_id]["returned"] += returned
            by_ammo[line.ammunition_id]["consumed"] += max(
                0, int(line.quantity or 0) - returned
            )
        rows = []
        for row in ammunition_rows:
            stats = by_ammo[row.id]
            rows.append(
                [
                    row.ammunition_id,
                    row.nomenclature,
                    row.caliber,
                    row.ammunition_type,
                    row.lot_number,
                    row.unit_of_measure,
                    row.quantity_received,
                    stats["issued"],
                    stats["returned"],
                    stats["consumed"],
                    row.quantity_issued,
                    row.stock_balance,
                    row.get_serviceability_display(),
                    _fmt_date(row.expiration_date),
                ]
            )
        return (
            title,
            subtitle,
            [
                "Ammunition ID",
                "Nomenclature",
                "Caliber",
                "Type",
                "Lot / Batch",
                "UOM",
                "Received",
                "Total Issued",
                "Returned",
                "Consumed",
                "Outstanding",
                "On Hand",
                "Serviceability",
                "Expiry",
            ],
            rows,
        )

    if report_slug == "inspections":
        ids = services.user_unit_ids(user)
        queryset = FirearmInspection.objects.select_related(
            "firearm", "inspected_by"
        ).filter(firearm__assigned_unit_id__in=ids)
        start = params.get("start") or ""
        end = params.get("end") or ""
        if start:
            queryset = queryset.filter(inspected_at__date__gte=start)
        if end:
            queryset = queryset.filter(inspected_at__date__lte=end)
        rows = []
        for row in queryset:
            firearm = row.firearm
            rows.append(
                [
                    _fmt_dt(row.inspected_at),
                    firearm.firearm_id,
                    firearm.butt_number,
                    firearm.serial_number,
                    firearm.get_firearm_type_display(),
                    row.get_condition_status_display(),
                    _person(row.inspected_by),
                    row.remarks,
                    _fmt_date(row.next_inspection_date),
                    firearm.storage_location,
                ]
            )
        return (
            title,
            subtitle,
            [
                "Inspected",
                "Firearm ID",
                "Butt No.",
                "Serial No.",
                "Type",
                "Condition",
                "Inspector",
                "Remarks",
                "Next Due",
                "Storage",
            ],
            rows,
        )

    if report_slug == "monthly-summary":
        month_text = params.get("start") or timeutils.today().isoformat()[:7]
        try:
            month_start = dt.date.fromisoformat(
                month_text if len(month_text) == 10 else f"{month_text}-01"
            )
        except ValueError:
            month_start = timeutils.today().replace(day=1)
        if month_start.day != 1:
            month_start = month_start.replace(day=1)
        next_month = (month_start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        month_end = next_month - dt.timedelta(days=1)
        firearms = services.scoped_firearms(user)
        ammunition = services.scoped_ammunition(user)
        transactions = services.scoped_transactions(user).filter(
            transaction_at__date__gte=month_start,
            transaction_at__date__lte=month_end,
        )
        rows = [
            ["Reporting period", f"{_fmt_date(month_start)} to {_fmt_date(month_end)}"],
            ["Total registered firearms", firearms.count()],
            ["Rifles", firearms.filter(firearm_type=Firearm.TYPE_RIFLE).count()],
            ["Pistols", firearms.filter(firearm_type=Firearm.TYPE_PISTOL).count()],
            ["Firearms stored", firearms.filter(custody_status=Firearm.CUSTODY_STORED).count()],
            ["Firearms issued", firearms.filter(custody_status=Firearm.CUSTODY_ISSUED).count()],
            ["Unserviceable firearms", firearms.exclude(condition_status=Firearm.CONDITION_SERVICEABLE).count()],
            ["Ammunition lots", ammunition.count()],
            ["Ammunition on hand", ammunition.aggregate(total=Sum("stock_balance"))["total"] or 0],
            ["Issuances", transactions.filter(transaction_type=ArmoryTransaction.TYPE_ISSUANCE).count()],
            ["Returns", transactions.filter(transaction_type=ArmoryTransaction.TYPE_RETURN).count()],
            ["Transfers", transactions.filter(transaction_type=ArmoryTransaction.TYPE_TRANSFER).count()],
            ["Adjustments", transactions.filter(transaction_type=ArmoryTransaction.TYPE_ADJUSTMENT).count()],
            ["Open returns", transactions.filter(transaction_type=ArmoryTransaction.TYPE_ISSUANCE, status=ArmoryTransaction.STATUS_OPEN).count()],
            ["Inspections", FirearmInspection.objects.filter(firearm__assigned_unit_id__in=services.user_unit_ids(user), inspected_at__date__gte=month_start, inspected_at__date__lte=month_end).count()],
        ]
        return (title, subtitle, ["Metric", "Value"], rows)

    raise KeyError(report_slug)


def _pdf_response(title, subtitle, headers, rows, filename, generated_by):
    buffer = BytesIO()
    page = landscape(letter)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page,
        rightMargin=0.35 * inch,
        leftMargin=0.35 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.4 * inch,
        title=title,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ArmoryTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        "ArmorySub",
        parent=styles["Normal"],
        fontSize=8.5,
        textColor=colors.HexColor("#4b5563"),
        spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        "ArmoryCell",
        parent=styles["Normal"],
        fontSize=6.5,
        leading=8,
    )
    head_style = ParagraphStyle(
        "ArmoryHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=6.5,
        leading=8,
        textColor=colors.white,
    )
    story = [
        Paragraph("TOWEMS · ARMORY MANAGEMENT SYSTEM", title_style),
        Paragraph(title, ParagraphStyle("Report", parent=title_style, fontSize=11, leading=14)),
        Paragraph(
            f"{subtitle} &nbsp;&nbsp; Generated {_fmt_dt(timeutils.now())} by {generated_by}.",
            sub_style,
        ),
    ]
    if not rows:
        story.append(Paragraph("No records match this report.", styles["Normal"]))
    else:
        wrapped_headers = [Paragraph(str(value), head_style) for value in headers]
        wrapped_rows = [
            [Paragraph("" if value is None else str(value), cell_style) for value in row]
            for row in rows
        ]
        available = page[0] - 0.7 * inch
        col_width = max(0.42 * inch, available / max(1, len(headers)))
        table = Table(
            [wrapped_headers] + wrapped_rows,
            colWidths=[col_width] * len(headers),
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(table)
    doc.build(story)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


def _xlsx_response(title, headers, rows, filename):
    workbook = Workbook()
    sheet = workbook.active
    # Excel forbids : \ / ? * [ ] in a worksheet name, and a few report titles
    # contain a slash. Keep the visible name recognisable while making it legal.
    safe_title = "".join("-" if character in ":\\/?*[]" else character for character in title)
    sheet.title = safe_title[:31].strip() or "Armory report"
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="1F3A5F")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        sheet.append(["" if value is None else value for value in row])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, header in enumerate(headers, start=1):
        width = max(10, min(32, len(str(header)) + 3))
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    buffer = BytesIO()
    workbook.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX_MIME)
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return response


def build_report_response(report_slug, *, user, params, export):
    title, subtitle, headers, rows = _table_data(report_slug, user, params)
    stamp = timeutils.now().strftime("%Y%m%d-%H%M")
    filename = f"TOWEMS-armory-{report_slug}-{stamp}"
    if export == "xlsx":
        return _xlsx_response(title, headers, rows, filename)
    return _pdf_response(
        title,
        subtitle,
        headers,
        rows,
        filename,
        generated_by=_person(user) or getattr(user, "username", ""),
    )


def receipt_pdf_response(transaction: ArmoryTransaction, user):
    """Electronic issuance or deposit receipt for one transaction."""
    is_return = transaction.transaction_type == ArmoryTransaction.TYPE_RETURN
    title = "FIREARM DEPOSIT RECEIPT" if is_return else "FIREARM ISSUANCE RECEIPT"
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=title,
    )
    styles = getSampleStyleSheet()
    brand = ParagraphStyle(
        "Brand", parent=styles["Title"], fontSize=13, leading=16, spaceAfter=2
    )
    subtitle = ParagraphStyle(
        "ReceiptSub", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#4b5563")
    )
    section = ParagraphStyle(
        "Section", parent=styles["Heading3"], fontSize=9.5, spaceBefore=10, spaceAfter=4
    )
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=8.5, leading=11)
    small = ParagraphStyle("Small", parent=body, fontSize=7.5, leading=9.5)
    story = [
        Paragraph("TOWEMS · ARMORY MANAGEMENT SYSTEM", brand),
        Paragraph(title, brand),
        Paragraph(
            f"Reference {transaction.reference_number} · {_fmt_dt(transaction.transaction_at)}",
            subtitle,
        ),
        Spacer(1, 8),
    ]

    identity = [
        ["Mission Order", transaction.mission_order_reference or "—"],
        ["Mission", transaction.mission.mission_number + " · " + transaction.mission.title if transaction.mission_id else "—"],
        ["Personnel", transaction.personnel_name or "—"],
        ["Rank / BOS", f"{transaction.rank or '—'} / {transaction.branch_of_service or '—'}"],
        ["Unit", transaction.unit_name or "—"],
        ["Releasing Armorer", _person(transaction.releasing_armorer) or "—"],
        ["Approving Authority", _person(transaction.approving_authority) or "—"],
        ["Acknowledged By", transaction.acknowledgement_name or "—"],
        ["Acknowledged At", _fmt_dt(transaction.acknowledged_at) or "—"],
    ]
    if transaction.return_of_id:
        identity.insert(1, ["Original Issuance", transaction.return_of.reference_number])
    identity_table = Table(identity, colWidths=[1.45 * inch, 5.45 * inch])
    identity_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
            ]
        )
    )
    story += [identity_table, Paragraph("Accounted Items", section)]
    headers = ["Firearm ID", "Butt No.", "Serial No.", "Type", "Ammunition / Lot", "Qty", "Condition"]
    rows = []
    for line in transaction.lines.all():
        rows.append(
            [
                line.firearm_id_snapshot or "—",
                line.butt_number_snapshot or "—",
                line.serial_number_snapshot or "—",
                line.firearm_type.title() if line.firearm_type else "—",
                (
                    f"{line.ammunition_name} / {line.lot_number}" if line.ammunition_id else "—"
                ),
                line.quantity if line.ammunition_id else "1 firearm",
                line.condition_status or "—",
            ]
        )
    item_table = Table([headers] + rows, colWidths=[1.2 * inch, 0.8 * inch, 1.2 * inch, 0.65 * inch, 2.1 * inch, 0.55 * inch, 0.9 * inch], repeatRows=1)
    item_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [item_table]

    notes = []
    if transaction.inspection_remarks:
        notes.append(f"Inspection: {transaction.inspection_remarks}")
    if transaction.discrepancy_notes:
        notes.append(f"Discrepancies: {transaction.discrepancy_notes}")
    if transaction.supporting_documents:
        notes.append(f"Supporting documents: {transaction.supporting_documents}")
    if notes:
        story += [Paragraph("Remarks", section), Paragraph("<br/>".join(notes), small)]

    story += [
        Spacer(1, 22),
        Table(
            [
                ["____________________________", "____________________________"],
                ["Releasing Armorer", "Receiving Personnel"],
            ],
            colWidths=[3.4 * inch, 3.5 * inch],
            style=TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                ]
            ),
        ),
        Spacer(1, 10),
        Paragraph(
            f"Generated by TOWEMS for {_person(user) or getattr(user, 'username', '')} on "
            f"{_fmt_dt(timeutils.now())}. This receipt is linked to the transaction record "
            "and cannot be altered without an audit entry.",
            small,
        ),
    ]
    doc.build(story)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{transaction.reference_number}-receipt.pdf"'
    )
    return response
