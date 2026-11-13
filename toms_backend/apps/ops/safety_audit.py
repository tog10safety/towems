"""Calculations behind the Unit Safety Officer's MRM audit.

The audit works from the same legacy ``mrm_forms`` queryset as the risk
register, so its figures always agree with the records the officer can open.
"Completed" means the decision authority approved the assessment; the date used
for a completion is the authority's approval date, with the review/raised dates
as fallbacks for older rows that predate the current workflow.

The module is deliberately free of request handling: the view enforces the
safety-officer role and unit scope, and the functions here only summarise the
rows it is given.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from collections import Counter
from io import BytesIO

from apps.ops import pdf as pdf_service

GRANULARITIES = ("week", "month", "quarter", "year")

RISK_KEYS = ("high", "medium", "low", "unrated")
RISK_LABELS = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "unrated": "Unrated",
}


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    return value


def completion_date(form):
    """The date the assessment was completed, or ``None``.

    The authority approval timestamp is authoritative. Legacy rows that carry
    ``authority_approved`` without a separate approval date fall back through
    the review and creation timestamps, which is the only date they have.
    """
    return _as_date(
        form.date_approved
        or form.authority_approval_date
        or form.date_reviewed
        or form.date_created
    )


def _start_of(value: dt.date, granularity: str) -> dt.date:
    if granularity == "week":
        return value - dt.timedelta(days=value.weekday())
    if granularity == "month":
        return value.replace(day=1)
    if granularity == "quarter":
        return value.replace(month=((value.month - 1) // 3) * 3 + 1, day=1)
    return value.replace(month=1, day=1)


def _next_start(value: dt.date, granularity: str) -> dt.date:
    if granularity == "week":
        return value + dt.timedelta(days=7)
    if granularity == "month":
        return (value.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    if granularity == "quarter":
        month = value.month + 3
        year = value.year + (month - 1) // 12
        return dt.date(year, (month - 1) % 12 + 1, 1)
    return dt.date(value.year + 1, 1, 1)


def _key(value: dt.date, granularity: str) -> str:
    if granularity == "week":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if granularity == "month":
        return f"{value.year}-{value.month:02d}"
    if granularity == "quarter":
        return f"{value.year}-Q{(value.month - 1) // 3 + 1}"
    return str(value.year)


def _label(value: dt.date, granularity: str) -> str:
    if granularity == "week":
        iso = value.isocalendar()
        return f"W{iso.week:02d} {iso.year}"
    if granularity == "month":
        return value.strftime("%b %Y")
    if granularity == "quarter":
        return f"Q{(value.month - 1) // 3 + 1} {value.year}"
    return str(value.year)


def _risk_key(form) -> str:
    category = (form.risk_category or "").strip().lower()
    return category if category in RISK_KEYS else "unrated"


def buckets(
    forms,
    granularity: str,
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
) -> list[dict]:
    """Completed MRMs per week/month/quarter/year, including empty periods.

    The bucket range follows the selected covered period when one is supplied,
    so a month with no completions still appears instead of the chart quietly
    ending at the last busy month. Without a period it spans the completions
    that exist.
    """
    completed_dates = [
        completion_date(form) for form in forms if form.authority_approved
    ]
    dates = [value for value in completed_dates if value is not None]
    if not dates and period_start is None and period_end is None:
        return []

    counts = Counter(_key(value, granularity) for value in dates)
    start = period_start or min(dates)
    end = period_end or max(dates)
    if end < start:
        end = start
    current = _start_of(start, granularity)
    last = _start_of(end, granularity)
    rows: list[dict] = []
    while current <= last and len(rows) < 240:
        key = _key(current, granularity)
        rows.append(
            {
                "key": key,
                "label": _label(current, granularity),
                "completed": counts.get(key, 0),
            }
        )
        current = _next_start(current, granularity)
    return rows


def summary(
    forms,
    granularity: str,
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
) -> dict:
    """Totals, risk distribution, per-unit counts and the chart buckets."""
    completed = [form for form in forms if form.authority_approved]
    risk_counts = {key: 0 for key in RISK_KEYS}
    for form in completed:
        risk_counts[_risk_key(form)] += 1

    percentages = [
        float(form.total_percent)
        for form in completed
        if form.total_percent is not None
    ]
    average = round(sum(percentages) / len(percentages), 1) if percentages else 0.0

    unit_rows: dict[int, dict] = {}
    for form in forms:
        unit = form.original_unit
        unit_id = unit.id if unit is not None else 0
        row = unit_rows.setdefault(
            unit_id,
            {
                "unit_id": unit_id or None,
                "acronym": (unit.acronym if unit is not None else "") or "",
                "name": (
                    (unit.name if unit is not None else "")
                    or form.original_unit_name
                    or "Unassigned"
                ),
                "total": 0,
                "completed": 0,
            },
        )
        row["total"] += 1
        if form.authority_approved:
            row["completed"] += 1

    return {
        "totals": {
            "mrms": len(forms),
            "completed": len(completed),
            "pending": len(forms) - len(completed),
            "completion_rate": round(len(completed) / len(forms) * 100, 1)
            if forms
            else 0.0,
            "average_residual": average,
            **risk_counts,
        },
        "by_risk": [
            {"key": key, "label": RISK_LABELS[key], "value": risk_counts[key]}
            for key in RISK_KEYS
        ],
        "by_unit": sorted(
            unit_rows.values(), key=lambda row: (row["acronym"], row["name"])
        ),
        "buckets": buckets(forms, granularity, period_start, period_end),
    }


def build_zip(forms) -> bytes:
    """Every supplied assessment as a PDF inside one ZIP archive."""
    buffer = BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for form in forms:
            stored = pdf_service.stored_pdf_path(form)
            content = stored.read_bytes() if stored is not None else pdf_service.generate_mrm_pdf(form)
            name = f"MRM_{form.mrm_number}.pdf"
            if name in used:
                name = f"MRM_{form.mrm_number}_{form.id}.pdf"
            used.add(name)
            archive.writestr(name, content)
    return buffer.getvalue()
