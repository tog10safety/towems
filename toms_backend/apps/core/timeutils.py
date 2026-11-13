"""Philippine Standard Time and the 24-hour military format (spec §7).

Why not ``USE_TZ = True``?
==========================
The legacy Flask tables store naive Asia/Manila wall-clock timestamps. Turning
on Django's timezone support would make Django read every existing naive value
as UTC and render it eight hours later - a silent shift of the entire audit and
operational history, which the data-preservation rules forbid. The database is
therefore kept exactly as it is (``USE_TZ = False``, ``TIME_ZONE =
"Asia/Manila"``) and this module owns the convention instead:

* **naive == Philippine wall clock**. That is what is stored, what APIs return
  and what the frontend prints (``utils/format.js``).
* :func:`now` is computed from ``Asia/Manila`` explicitly, so a server whose
  operating system is set to UTC still writes Philippine time. ``settings`` also
  normalises the process time zone so Django's own ``auto_now_add`` columns and
  ``django.utils.timezone.now()`` agree with it.
* :func:`to_manila` is the one place an aware datetime is converted, so no view
  performs its own arithmetic.

Presentation is a single format: ``29 Oct 2026, 1430H (PST)``.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

MANILA = ZoneInfo("Asia/Manila")
#: Label appended to user-facing timestamps. Not "PST" for Pacific: this is
#: Philippine Standard Time, UTC+08:00, with no daylight-saving transitions.
PST_LABEL = "PST"

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def now() -> datetime:
    """Current Philippine time as a naive datetime (the storage convention)."""
    return datetime.now(MANILA).replace(tzinfo=None)


def today() -> date:
    return now().date()


def to_manila(value: datetime | None) -> datetime | None:
    """Normalise ``value`` to naive Philippine wall-clock time.

    A naive value is already Philippine time and is returned unchanged; an
    aware value (UTC from a job, an external report, a test) is converted once,
    here, rather than in each caller.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(MANILA).replace(tzinfo=None)


def to_aware(value: datetime | None) -> datetime | None:
    """Attach the Philippine offset - for integrations that require one."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(MANILA)
    return value.replace(tzinfo=MANILA)


def format_date(value: datetime | date | None) -> str:
    """``29 Oct 2026`` (empty string for no value)."""
    value = to_manila(value) if isinstance(value, datetime) else value
    if value is None:
        return ""
    return f"{value.day:02d} {_MONTHS[value.month - 1]} {value.year}"


def format_time(value: datetime | None) -> str:
    """``1430H`` - 24-hour, no AM/PM."""
    value = to_manila(value)
    if value is None:
        return ""
    return f"{value.hour:02d}{value.minute:02d}H"


def format_datetime(value: datetime | None) -> str:
    """``29 Oct 2026, 1430H (PST)`` - the only timestamp format we emit."""
    value = to_manila(value)
    if value is None:
        return ""
    return f"{format_date(value)}, {format_time(value)} ({PST_LABEL})"
