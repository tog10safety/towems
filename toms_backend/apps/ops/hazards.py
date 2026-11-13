"""The unit's institutional hazard library (legacy ``hazard_registry``).

The table is unmanaged and preserved exactly as it is: a row carries the legacy
``unit`` name *string* beside the ``unit_id`` link, and its risk rating is the
concatenation of likelihood and severity (``"4B"``), which is what 1,923
historical rows and every MRM PDF already depend on. Nothing here rewrites a
rating differently.

Who may do what
---------------
==================================  ==========================================
Caller                              Hazard library
==================================  ==========================================
Administrator                       every unit: read, create, edit, delete,
                                    import, export
Unit Safety Officer                 own unit: read, create, edit, delete,
                                    import, export; own unit and
                                    subordinates: read
Commander, Deputy Commander,        own unit and subordinates: read only
Director for Operations,
Operations Officer
Creator                             own unit only: read (no subordinates)
Evaluator                           own unit only: read (no subordinates)
Everyone else                       no access at all (hidden)
==================================  ==========================================

Export is a maintainer's tool, not a reader's: it produces the workbook that
is edited and re-imported, so only the two roles that may import it may
download it. Command and operations posts read the library on screen.

Deleting is refused while an MRM still selects the hazard: the assessment, its
PDF and the mission's risk record would otherwise point at a row that no longer
exists. The refusal says how many assessments use it, so the officer can decide
whether to edit the hazard instead.
"""

from __future__ import annotations

import re
from io import BytesIO

from django.db import transaction
from django.db.models import Count, Q
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from apps.core import roles, timeutils
from apps.core.scoping import accessible_unit_ids
from apps.legacy.models import HazardRegistry, MissionHazards, Unit

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 2000

LIKELIHOODS = (1, 2, 3, 4, 5)
SEVERITIES = ("A", "B", "C", "D", "E")

#: (field, header). The template and the export share this order.
COLUMNS = [
    ("unit", "Unit"),
    ("activity_code", "Activity Code"),
    ("hazard_description", "Hazard Description"),
    ("before_likelihood", "Before Likelihood"),
    ("before_severity", "Before Severity"),
    ("mitigations", "Mitigations"),
    ("after_likelihood", "After Likelihood"),
    ("after_severity", "After Severity"),
]

HEADER_ALIASES = {
    "unit": "unit",
    "unit name": "unit",
    "activity code": "activity_code",
    "activity": "activity_code",
    "code": "activity_code",
    "hazard description": "hazard_description",
    "hazard": "hazard_description",
    "description": "hazard_description",
    "before likelihood": "before_likelihood",
    "likelihood before": "before_likelihood",
    "initial likelihood": "before_likelihood",
    "before severity": "before_severity",
    "severity before": "before_severity",
    "initial severity": "before_severity",
    "mitigations": "mitigations",
    "mitigation": "mitigations",
    "control measures": "mitigations",
    "after likelihood": "after_likelihood",
    "residual likelihood": "after_likelihood",
    "likelihood after": "after_likelihood",
    "after severity": "after_severity",
    "residual severity": "after_severity",
    "severity after": "after_severity",
}

REQUIRED_FIELDS = (
    "activity_code",
    "hazard_description",
    "before_likelihood",
    "before_severity",
    "mitigations",
    "after_likelihood",
    "after_severity",
)


class HazardError(Exception):
    """A hazard operation is not permitted or not valid."""


class HazardImportError(Exception):
    """The workbook cannot be imported; ``errors`` explains every bad row."""

    def __init__(self, message: str, errors: dict | None = None):
        super().__init__(message)
        self.errors = errors or {}


# ---------------------------------------------------------------------------
# Permissions and scope
# ---------------------------------------------------------------------------


def _authenticated(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def can_view(user) -> bool:
    return _authenticated(user) and roles.has_capability(user, roles.CAP_VIEW_HAZARDS)


def can_manage(user) -> bool:
    return _authenticated(user) and roles.has_capability(user, roles.CAP_MANAGE_HAZARDS)


def is_administrator(user) -> bool:
    return _authenticated(user) and getattr(user, "authority", None) == roles.BASE_ADMIN


def _creator_only(user) -> bool:
    """True when the account holds nothing beyond the Creator role.

    A Creator reads the hazard library for their own unit and not for its
    subordinates: the rows describe the unit's own activities, and the Creator
    has no command over the units below it. Any appointment or office
    membership takes the account out of this narrow rule.
    """
    if getattr(user, "authority", None) != roles.BASE_CREATOR:
        return False
    return not (roles.effective_roles(user) - {roles.ROLE_CREATOR})


def read_unit_ids(user) -> set[int]:
    """Units whose hazards this caller may read. Empty set means none."""
    if not can_view(user):
        return set()
    # An Evaluator reads the assessed unit's library as reference, like a
    # Creator: their own unit only, never its subordinates.
    if getattr(user, "authority", None) == roles.BASE_EVALUATOR:
        unit_id = getattr(user, "unit_id", None)
        return {unit_id} if unit_id else set()
    if _creator_only(user):
        unit_id = getattr(user, "unit_id", None)
        return {unit_id} if unit_id else set()
    return accessible_unit_ids(user)


def managed_unit_ids(user) -> set[int]:
    """Units this caller may change. The Safety Officer keeps to their own."""
    if not can_manage(user):
        return set()
    if is_administrator(user):
        return set(Unit.objects.filter(is_active=True).values_list("id", flat=True))
    unit_id = getattr(user, "unit_id", None)
    return {unit_id} if unit_id else set()


def _writable_unit(user, unit_id) -> Unit:
    """Resolve and authorise the unit a hazard belongs to."""
    allowed = managed_unit_ids(user)
    if unit_id in (None, ""):
        own = getattr(user, "unit_id", None)
        if own is None:
            raise HazardError(
                "Your account has no unit; the Administrator must assign one before "
                "you can maintain a hazard library."
            )
        unit_id = own
    try:
        unit_id = int(unit_id)
    except (TypeError, ValueError):
        raise HazardError("The unit must be an existing unit.") from None
    if unit_id not in allowed:
        raise HazardError(
            "You may only maintain the hazard library of your own unit."
            if not is_administrator(user)
            else "Choose an active unit."
        )
    unit = Unit.objects.filter(pk=unit_id, is_active=True).first()
    if unit is None:
        raise HazardError("That unit does not exist or is inactive.")
    return unit


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------


def registry_queryset(user, *, unit=None, activity_code="", q="", include_usage=True):
    """Rows this caller may read, filtered as the page's controls do."""
    if not can_view(user):
        raise HazardError("You do not have access to the hazard library.")

    queryset = HazardRegistry.objects.all()
    if not is_administrator(user):
        ids = read_unit_ids(user)
        names = list(
            Unit.objects.filter(id__in=ids).values_list("name", flat=True)
        ) if ids else []
        # Legacy rows before the unit link existed carry only the name string.
        scope = Q(unit_id__in=ids)
        if names:
            scope |= Q(unit_id__isnull=True, unit__in=names)
        queryset = queryset.filter(scope)
    if unit not in (None, ""):
        try:
            unit_id = int(unit)
        except (TypeError, ValueError):
            raise HazardError("The unit filter must be a unit id.") from None
        queryset = queryset.filter(unit_id=unit_id)
    if activity_code:
        queryset = queryset.filter(activity_code=activity_code)
    if q:
        queryset = queryset.filter(hazard_description__icontains=q)
    if include_usage:
        queryset = queryset.annotate(uses=Count("mission_links", distinct=True))
    return queryset.order_by("activity_code", "hazard_description", "id")


def hazard_payload(row) -> dict:
    before = row.before_risk_rating or HazardRegistry.calculate_risk_rating(
        row.before_likelihood, row.before_severity
    )
    after = row.after_risk_rating or HazardRegistry.calculate_risk_rating(
        row.after_likelihood, row.after_severity
    )
    unit = row.unit_id if row.unit_id_id else None
    return {
        "id": row.id,
        "unit_id": row.unit_id_id,
        "unit_name": getattr(unit, "name", "") or row.unit or "",
        "unit_acronym": getattr(unit, "acronym", "") or "",
        "activity_code": row.activity_code,
        "hazard_description": row.hazard_description,
        "before_likelihood": row.before_likelihood,
        "before_severity": row.before_severity,
        "before_rating": before,
        "before_tone": HazardRegistry.risk_color(before),
        "mitigations": row.mitigations,
        "after_likelihood": row.after_likelihood,
        "after_severity": row.after_severity,
        "after_rating": after,
        "after_tone": HazardRegistry.risk_color(after),
        "uses": getattr(row, "uses", None),
        "date_updated": row.date_updated,
        "created_by_name": getattr(row.created_by, "full_name", "") if row.created_by_id else "",
    }


def activity_codes(user, unit=None) -> list[dict]:
    """The codes this caller's scope actually contains, for the filter menus."""
    queryset = registry_queryset(user, unit=unit, include_usage=False)
    rows = (
        queryset.values("activity_code")
        .annotate(hazard_count=Count("id"))
        .order_by("activity_code")
    )
    return [
        {"activity_code": row["activity_code"], "hazard_count": row["hazard_count"]}
        for row in rows
        if row["activity_code"]
    ]


def summary(user) -> dict:
    """Counts for the page header, over everything the caller may read."""
    queryset = registry_queryset(user, include_usage=False)
    rows = list(queryset.values_list("before_risk_rating", "after_risk_rating"))
    before_red = sum(1 for before, _ in rows if before in {"5A", "5B", "5C", "4A", "4B", "3A"})
    after_red = sum(1 for _, after in rows if after in {"5A", "5B", "5C", "4A", "4B", "3A"})
    return {
        "hazards": len(rows),
        "activity_codes": len({code for code in queryset.values_list("activity_code", flat=True) if code}),
        "before_unacceptable": before_red,
        "after_unacceptable": after_red,
    }


# ---------------------------------------------------------------------------
# Validation and writes
# ---------------------------------------------------------------------------


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _likelihood(value, label: str, errors: dict) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        errors[label] = "Likelihood must be a whole number from 1 to 5."
        return None
    if number not in LIKELIHOODS:
        errors[label] = "Likelihood must be between 1 and 5."
        return None
    return number


def _severity(value, label: str, errors: dict) -> str | None:
    text = _text(value).upper()
    if text not in SEVERITIES:
        errors[label] = "Severity must be one of A, B, C, D or E."
        return None
    return text


def clean_fields(data: dict) -> tuple[dict, dict]:
    """Validate the eight hazard fields, returning (values, field errors)."""
    errors: dict[str, str] = {}
    values: dict = {}

    activity_code = _text(data.get("activity_code"))
    if not activity_code:
        errors["activity_code"] = "The activity code is required."
    elif len(activity_code) > 100:
        errors["activity_code"] = "The activity code must be 100 characters or fewer."
    else:
        values["activity_code"] = activity_code

    description = _text(data.get("hazard_description"))
    if not description:
        errors["hazard_description"] = "The hazard description is required."
    else:
        values["hazard_description"] = description

    mitigations = _text(data.get("mitigations"))
    if not mitigations:
        errors["mitigations"] = "At least one mitigation is required."
    else:
        values["mitigations"] = mitigations

    before_l = _likelihood(data.get("before_likelihood"), "before_likelihood", errors)
    before_s = _severity(data.get("before_severity"), "before_severity", errors)
    after_l = _likelihood(data.get("after_likelihood"), "after_likelihood", errors)
    after_s = _severity(data.get("after_severity"), "after_severity", errors)

    if before_l is not None:
        values["before_likelihood"] = before_l
    if before_s is not None:
        values["before_severity"] = before_s
    if after_l is not None:
        values["after_likelihood"] = after_l
    if after_s is not None:
        values["after_severity"] = after_s

    return values, errors


@transaction.atomic
def create_hazard(user, data: dict) -> HazardRegistry:
    if not can_manage(user):
        raise HazardError("Only the Unit Safety Officer or Administrator may add a hazard.")
    values, errors = clean_fields(data)
    if errors:
        raise HazardImportError("The hazard could not be saved.", errors)
    unit = _writable_unit(user, data.get("unit") or data.get("unit_id"))

    row = HazardRegistry.objects.create(
        unit=unit.name,
        unit_id=unit,
        activity_code=values["activity_code"],
        hazard_description=values["hazard_description"],
        before_likelihood=values["before_likelihood"],
        before_severity=values["before_severity"],
        before_risk_rating=f"{values['before_likelihood']}{values['before_severity']}",
        mitigations=values["mitigations"],
        after_likelihood=values["after_likelihood"],
        after_severity=values["after_severity"],
        after_risk_rating=f"{values['after_likelihood']}{values['after_severity']}",
        date_updated=timeutils.now(),
        created_by_id=user.id,
    )
    _audit(user, "hazard_create", row, after=hazard_payload(row))
    return row


def _load_for_write(user, hazard_id: int) -> HazardRegistry:
    row = HazardRegistry.objects.filter(pk=hazard_id).first()
    if row is None:
        raise HazardError("That hazard does not exist.")
    if not can_manage(user):
        raise HazardError("Only the Unit Safety Officer or Administrator may change a hazard.")
    if row.unit_id_id and row.unit_id_id not in managed_unit_ids(user):
        raise HazardError("That hazard belongs to a unit you do not maintain.")
    if not row.unit_id_id and not is_administrator(user) and row.unit != getattr(
        getattr(user, "unit", None), "name", None
    ):
        raise HazardError("That hazard belongs to a unit you do not maintain.")
    return row


@transaction.atomic
def update_hazard(user, hazard_id: int, data: dict, *, partial=True) -> HazardRegistry:
    row = _load_for_write(user, hazard_id)
    before = hazard_payload(row)
    merged = {
        "activity_code": row.activity_code,
        "hazard_description": row.hazard_description,
        "before_likelihood": row.before_likelihood,
        "before_severity": row.before_severity,
        "mitigations": row.mitigations,
        "after_likelihood": row.after_likelihood,
        "after_severity": row.after_severity,
    }
    if not partial:
        merged = {}
    for key in list(merged):
        if key in data:
            merged[key] = data[key]
    values, errors = clean_fields(merged)
    if errors:
        raise HazardImportError("The hazard could not be saved.", errors)

    if data.get("unit") not in (None, "") or data.get("unit_id") not in (None, ""):
        unit = _writable_unit(user, data.get("unit") or data.get("unit_id"))
        row.unit = unit.name
        row.unit_id = unit

    row.activity_code = values["activity_code"]
    row.hazard_description = values["hazard_description"]
    row.before_likelihood = values["before_likelihood"]
    row.before_severity = values["before_severity"]
    row.before_risk_rating = f"{values['before_likelihood']}{values['before_severity']}"
    row.mitigations = values["mitigations"]
    row.after_likelihood = values["after_likelihood"]
    row.after_severity = values["after_severity"]
    row.after_risk_rating = f"{values['after_likelihood']}{values['after_severity']}"
    row.date_updated = timeutils.now()
    row.save()
    _audit(user, "hazard_update", row, before=before, after=hazard_payload(row))
    return row


@transaction.atomic
def delete_hazard(user, hazard_id: int, *, force: bool = False) -> dict:
    """Remove a hazard that no assessment uses.

    The legacy ``mission_hazards`` rows are the historical record of what was
    assessed, so a hazard they reference is never deleted; the officer is told
    how many assessments use it instead. Map pins that only georeference the
    registry row go with it.

    ``force`` is the Super Administrator's override. The assessment links are
    removed with the hazard, and the audit entry keeps the full row and the
    number of links that went with it, so the deletion is still explainable.
    """
    row = _load_for_write(user, hazard_id)
    used = MissionHazards.objects.filter(hazard_id=row.id).count()
    if used and not force:
        raise HazardError(
            f"This hazard is used by {used} MRM assessment"
            f"{'' if used == 1 else 's'} and cannot be deleted. Edit it instead."
        )
    before = hazard_payload(row)
    from apps.command.models import HazardLocation

    HazardLocation.objects.filter(hazard_id=row.id).delete()
    if force:
        before["assessments_unlinked"] = used
        MissionHazards.objects.filter(hazard_id=row.id).delete()
    row.delete()
    _audit(user, "hazard_delete", None, before=before)
    return {"deleted": True, "id": hazard_id}


def _audit(actor, action, row, *, before=None, after=None, note="", request=None):
    from apps.core.audit import log_action

    entity_id = getattr(row, "id", None)
    if entity_id is None:
        entity_id = (after or before or {}).get("id")
    log_action(
        actor=actor,
        action=action,
        entity_type="hazard_registry",
        entity_id=entity_id,
        before=before,
        after=after,
        note=note,
        request=request,
    )


# ---------------------------------------------------------------------------
# Excel: template, export, import
# ---------------------------------------------------------------------------


def _style_header(sheet, columns) -> None:
    for index, (_, header) in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F6F43")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = max(18, len(header) + 6)
    sheet.freeze_panes = "A2"


def template_units(user) -> list[str]:
    """The unit names a template offers in its Unit dropdown.

    The dropdown is the point of the list: an encoder can only choose a unit
    that exists in TOWEMS, so the import never receives a mistyped unit and no
    row is silently attached to a unit nobody recorded. The Safety Officer sees
    their own unit, the Administrator every active unit.
    """
    if is_administrator(user):
        units = Unit.objects.filter(is_active=True).order_by("name")
    else:
        units = Unit.objects.filter(
            pk=getattr(user, "unit_id", None), is_active=True
        )
    return [unit.name for unit in units]


def _add_unit_picker(workbook, sheet, units: list[str], *, allow_blank: bool) -> None:
    """Point the Unit column at a dropdown of real units.

    The names live on their own sheet and the validation refers to that range,
    because Excel rejects an inline list longer than 255 characters - a command
    with many subordinates would silently lose its dropdown.
    """
    if not units:
        return
    choices = workbook.create_sheet("Units")
    choices.column_dimensions["A"].width = 46
    title = choices.cell(row=1, column=1, value="Active units - choose one on the Hazard Registry sheet")
    title.font = Font(bold=True)
    for index, name in enumerate(units, start=2):
        choices.cell(row=index, column=1, value=name)

    picker = DataValidation(
        type="list",
        formula1=f"Units!$A$2:$A${len(units) + 1}",
        allow_blank=allow_blank,
        showErrorMessage=True,
        showInputMessage=True,
    )
    picker.errorTitle = "Unknown unit"
    picker.error = (
        "Choose a unit from the list. A unit that is not in TOWEMS cannot be "
        "imported."
    )
    picker.promptTitle = "Unit"
    picker.prompt = (
        "Pick the unit this hazard belongs to from the dropdown."
        if not allow_blank
        else "Pick a unit from the dropdown, or leave it blank for your own unit."
    )
    sheet.add_data_validation(picker)
    for row in range(2, 1002):
        picker.add(f"A{row}")


def build_template(user=None) -> bytes:
    """The import workbook: headers, pickers and instructions."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hazard Registry"
    _style_header(sheet, COLUMNS)
    limits = 1000
    units = template_units(user) if user is not None else []
    _add_unit_picker(workbook, sheet, units, allow_blank=not is_administrator(user))

    likelihood = DataValidation(
        type="whole", operator="between", formula1="1", formula2="5", allow_blank=False
    )
    likelihood.error = "Likelihood must be a whole number from 1 to 5."
    likelihood.errorTitle = "Invalid likelihood"
    severity = DataValidation(type="list", formula1='"A,B,C,D,E"', allow_blank=False)
    severity.error = "Severity must be A, B, C, D or E."
    severity.errorTitle = "Invalid severity"
    sheet.add_data_validation(likelihood)
    sheet.add_data_validation(severity)
    for row in range(2, limits + 2):
        likelihood.add(f"D{row}")
        severity.add(f"E{row}")
        likelihood.add(f"G{row}")
        severity.add(f"H{row}")

    for column, width in {
        "A": 30,
        "B": 26,
        "C": 60,
        "D": 18,
        "E": 16,
        "F": 70,
        "G": 16,
        "H": 15,
    }.items():
        sheet.column_dimensions[column].width = width

    notes = workbook.create_sheet("Instructions")
    notes.column_dimensions["A"].width = 118
    lines = [
        ("TOWEMS hazard registry import", True),
        ("", False),
        ("1. Fill in the 'Hazard Registry' sheet. Do not rename or reorder the columns.", False),
        ("2. Choose the Unit from the dropdown. Only units recorded in TOWEMS can be"
         " imported, so a unit cannot be typed by hand."
         + ("" if is_administrator(user) else
            " Leave it blank to import into your own unit."), False),
        ("3. Likelihood is 1 (extremely improbable) to 5 (frequent).", False),
        ("4. Severity is A (catastrophic) to E (negligible).", False),
        ("5. Before/After ratings are calculated by TOWEMS from likelihood and severity;"
         " they are never typed by hand.", False),
        ("6. Every row is validated before anything is saved. A workbook with any invalid row"
         " imports nothing, so you can fix the reported rows and upload it again.", False),
        ("7. Mitigations are one per line inside the cell.", False),
    ]
    for index, (text, bold) in enumerate(lines, start=1):
        cell = notes.cell(row=index, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = Font(bold=True, size=13)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_workbook(rows, user=None) -> bytes:
    """The caller's current selection as a re-importable workbook."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hazard Registry"
    headers = list(COLUMNS) + [("before_rating", "Before Rating"), ("after_rating", "After Rating")]
    _style_header(sheet, headers)
    # An export round-trips through the import, so it carries the same Unit
    # dropdown rather than free text.
    if user is not None:
        _add_unit_picker(
            workbook, sheet, template_units(user), allow_blank=not is_administrator(user)
        )
    for index, row in enumerate(rows, start=2):
        values = [
            row.unit or "",
            row.activity_code,
            row.hazard_description,
            row.before_likelihood,
            row.before_severity,
            row.mitigations,
            row.after_likelihood,
            row.after_severity,
            row.before_risk_rating,
            row.after_risk_rating,
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=index, column=column, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for column, width in {"A": 30, "B": 26, "C": 60, "F": 70}.items():
        sheet.column_dimensions[column].width = width
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _normalise_header(value) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return HEADER_ALIASES.get(text, "")


def import_workbook(user, upload) -> dict:
    """Import every row of a template workbook, or none of them."""
    if not can_manage(user):
        raise HazardError("Only the Unit Safety Officer or Administrator may import hazards.")
    name = getattr(upload, "name", "") or ""
    if not name.lower().endswith(".xlsx"):
        raise HazardImportError("Upload an .xlsx workbook produced from the TOWEMS template.")
    if getattr(upload, "size", 0) > MAX_IMPORT_BYTES:
        raise HazardImportError("The workbook is larger than 5 MB.")

    try:
        workbook = load_workbook(filename=BytesIO(upload.read()), data_only=True)
    except Exception:  # openpyxl raises several unrelated errors
        raise HazardImportError("That file is not a readable .xlsx workbook.") from None

    sheet = workbook["Hazard Registry"] if "Hazard Registry" in workbook.sheetnames else workbook.active
    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    mapping = {}
    for index, value in enumerate(header_row):
        field = _normalise_header(value)
        if field and field not in mapping:
            mapping[field] = index

    missing = [field for field in REQUIRED_FIELDS if field not in mapping]
    if missing:
        labels = dict(COLUMNS)
        raise HazardImportError(
            "The workbook is missing required columns: "
            + ", ".join(labels.get(field, field) for field in missing)
            + ". Download the template and use its headers."
        )

    prepared: list[dict] = []
    errors: dict[str, list[str]] = {}
    rows = 0
    for offset, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if all(value in (None, "") for value in values):
            continue
        rows += 1
        if rows > MAX_IMPORT_ROWS:
            raise HazardImportError(f"Import at most {MAX_IMPORT_ROWS} hazards per workbook.")
        raw = {
            field: values[index] if index < len(values) else None
            for field, index in mapping.items()
            if index < len(values)
        }
        cleaned, row_errors = clean_fields(raw)
        unit = _resolve_import_unit(user, raw.get("unit"), row_errors)
        if unit is not None:
            cleaned["unit"] = unit
        if row_errors:
            errors[f"row {offset}"] = [
                f"{dict(COLUMNS).get(field, field)}: {message}"
                for field, message in row_errors.items()
            ]
            continue
        prepared.append(cleaned)

    if not prepared and not errors:
        raise HazardImportError("The workbook has no hazard rows.")
    if errors:
        raise HazardImportError(
            f"{len(errors)} row(s) need attention; nothing was imported.", errors
        )

    now = timeutils.now()
    with transaction.atomic():
        created = [
            HazardRegistry(
                unit=values["unit"].name,
                unit_id=values["unit"],
                activity_code=values["activity_code"],
                hazard_description=values["hazard_description"],
                before_likelihood=values["before_likelihood"],
                before_severity=values["before_severity"],
                before_risk_rating=f"{values['before_likelihood']}{values['before_severity']}",
                mitigations=values["mitigations"],
                after_likelihood=values["after_likelihood"],
                after_severity=values["after_severity"],
                after_risk_rating=f"{values['after_likelihood']}{values['after_severity']}",
                date_updated=now,
                created_by_id=user.id,
            )
            for values in prepared
        ]
        HazardRegistry.objects.bulk_create(created)

    _audit(
        user,
        "hazard_import",
        None,
        after={"created": len(created), "unit": prepared[0]["unit"].name if prepared else ""},
        note=f"Imported {len(created)} hazard(s) from {name}.",
    )
    return {"created": len(created), "rows": rows}


def _resolve_import_unit(user, value, errors: dict):
    if value in (None, ""):
        if is_administrator(user):
            errors["unit"] = "The Administrator must name the unit on every row."
            return None
        return _writable_unit(user, None)
    name = _text(value)
    if is_administrator(user):
        unit = Unit.objects.filter(name__iexact=name, is_active=True).first()
        if unit is None:
            unit = Unit.objects.filter(acronym__iexact=name, is_active=True).first()
        if unit is None:
            errors["unit"] = f"No active unit is named {name!r}."
            return None
        return _writable_unit(user, unit.id)
    own = getattr(user, "unit", None)
    own_names = {getattr(own, "name", "").lower(), getattr(own, "acronym", "").lower()}
    if name.lower() not in own_names:
        errors["unit"] = "You may only import into your own unit."
        return None
    return _writable_unit(user, None)
