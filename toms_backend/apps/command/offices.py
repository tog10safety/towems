"""The standard office/section catalogue and its seeding rules.

Every unit is organised around the same staff offices (A1-A10), a Safety Office
and an "Other" placeholder for a locally named office or section. The catalogue
lives here - not in the migration and not in the React app - so the backend,
the seed routine and the admin interface cannot drift apart.

Two rules shape the code below:

* Seeding is **idempotent**: it matches on ``(unit, code)`` and never touches a
  row it did not create, so an office renamed by the Admin Officer keeps its
  name and the routine can be re-run safely.
* Retiring the pre-catalogue entries **never deletes** anything. Rows are
  deactivated and their active personnel assignments are ended (history is
  retained), because assignments, missions and audit entries may reference
  them. The Operations Center office is never retired: it carries the unit's
  routing authority and is not part of the staff catalogue.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

#: ``(code, name, kind)``. The order is the order shown in the interface.
DEFAULT_OFFICE_CATALOGUE: tuple[tuple[str, str, str], ...] = (
    ("A1", "Personnel / Administration", "office"),
    ("A2", "Intelligence", "office"),
    ("A3", "Operations", "office"),
    ("A4", "Logistics", "office"),
    ("A5", "Plans", "office"),
    ("A6", "Communications, Electronics and Information Systems (CEIS)", "office"),
    ("A7", "Civil-Military Operations (CMO)", "office"),
    ("A8", "Training", "office"),
    ("A9", "Reservist Affairs", "office"),
    ("A10", "Finance", "office"),
    ("SAFETY", "Safety Office", "office"),
    # Selecting this entry asks the user to type the office/section name; the
    # stored row is then a custom entry with an ``X-`` code.
    ("OTHER", "Specify – Other Office/Section", "section"),
)

DEFAULT_OFFICE_CODES: tuple[str, ...] = tuple(code for code, _, _ in DEFAULT_OFFICE_CATALOGUE)
DEFAULT_OFFICE_NAMES: dict[str, str] = {code: name for code, name, _ in DEFAULT_OFFICE_CATALOGUE}

#: Placeholder code that means "type your own name".
OTHER_OFFICE_CODE = "OTHER"
#: Prefix for the code generated for a custom office/section.
CUSTOM_CODE_PREFIX = "X-"


def catalogue_payload() -> list[dict]:
    """The catalogue as the admin interface consumes it."""
    return [
        {
            "code": code,
            "name": name,
            "kind": kind,
            "custom": code == OTHER_OFFICE_CODE,
        }
        for code, name, kind in DEFAULT_OFFICE_CATALOGUE
    ]


def is_catalogue_code(code: str | None) -> bool:
    return (code or "").strip().upper() in DEFAULT_OFFICE_CODES


def next_custom_code(unit) -> str:
    """Next unused ``X-<n>`` code for ``unit`` (codes are unique per unit)."""
    from apps.command.models import OfficeSection

    used = set(
        OfficeSection.objects.filter(unit=unit, code__startswith=CUSTOM_CODE_PREFIX)
        .values_list("code", flat=True)
    )
    index = 1
    while f"{CUSTOM_CODE_PREFIX}{index:02d}" in used:
        index += 1
    return f"{CUSTOM_CODE_PREFIX}{index:02d}"


def seed_default_offices(unit, actor=None, *, retire_others: bool = False) -> dict:
    """Create the catalogue offices for ``unit``; optionally retire old ones.

    Returns ``{"created": [...], "retired": n, "assignments_ended": n}``.
    """
    from apps.command.models import OfficeSection, SectionAssignment

    created = []
    retired = 0
    assignments_ended = 0

    with transaction.atomic():
        for code, name, kind in DEFAULT_OFFICE_CATALOGUE:
            office, was_created = OfficeSection.objects.get_or_create(
                unit=unit,
                code=code,
                defaults={
                    "name": name,
                    "kind": kind,
                    "is_custom": False,
                    "created_by": actor,
                    "description": "",
                },
            )
            if was_created:
                created.append(office)

        if retire_others:
            stale = (
                OfficeSection.objects.filter(unit=unit, is_active=True)
                .exclude(code__in=DEFAULT_OFFICE_CODES)
                .exclude(function=OfficeSection.FUNCTION_OPERATIONS_CENTER)
            )
            stale_ids = list(stale.values_list("id", flat=True))
            if stale_ids:
                assignments_ended = SectionAssignment.objects.filter(
                    section_id__in=stale_ids, is_active=True
                ).update(is_active=False, ended_at=timezone.now(), ended_by=actor)
                retired = stale.update(is_active=False)

    return {"created": created, "retired": retired, "assignments_ended": assignments_ended}
