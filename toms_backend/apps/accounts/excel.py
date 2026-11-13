"""Personnel Excel template and bulk registration.

Only ``.xlsx`` workbooks produced by the downloadable template are accepted.
Every row is validated by the same serializer the single-record form uses, so
the import cannot bypass role, unit-scope, password-policy or personnel-detail
validation.  A workbook with any invalid row imports nothing: the operator
fixes the reported rows and uploads it again, which avoids a half-registered
batch.
"""

from __future__ import annotations

import datetime as dt
import re
from io import BytesIO
from types import SimpleNamespace

from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel
from openpyxl.worksheet.datavalidation import DataValidation
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.personnel_details import (
    BRANCH_OPTIONS,
    DATE_DETAIL_FIELDS,
    PERSONNEL_DETAIL_FIELDS,
    RETIRED_DESIGNATIONS,
)
from apps.accounts.serializers import PersonnelWriteSerializer, validate_password_policy
from apps.core.audit import log_action
from apps.core.legacy_audit import record_activity
from apps.legacy.models import Unit

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 1000

#: Values the template no longer offers. "Other" is handled separately: it is
#: the old name of "Personnel" and is renamed rather than refused.
RETIRED_DESIGNATION_KEYS = {
    value.casefold() for value in RETIRED_DESIGNATIONS if value.casefold() != "other"
}

#: (field, header, required).  Username is deliberately absent: registration
#: uses the serial number, as required by the unit procedure.
TEMPLATE_COLUMNS = [
    ("serial_number", "Serial Number", True),
    ("first_name", "First Name", True),
    ("middle_name", "Middle Name", False),
    ("last_name", "Last Name", True),
    ("rank", "Rank", False),
    ("designation", "Designation", False),
    ("authority", "Account Role", True),
    ("category", "Category", True),
    ("unit", "Unit", True),
    ("branch_of_service", "Branch of Service", False),
    ("contact_number", "Contact Number", False),
    ("email", "Email Address", False),
    ("sex", "Sex / Gender", False),
    ("date_of_birth", "Date of Birth", False),
    ("blood_type", "Blood Type", False),
    ("home_address", "Home Address", False),
    ("emergency_contact_name", "Emergency Contact Name", False),
    ("emergency_contact_number", "Emergency Contact Number", False),
    ("date_enlisted", "Date Enlisted", False),
    ("date_commissioned", "Date Commissioned", False),
    ("afsc_primary", "AFSC Primary", False),
    ("afsc_secondary", "AFSC Secondary", False),
    ("afsc_tertiary", "AFSC Tertiary", False),
    ("highest_education", "Highest Education", False),
    ("civil_status", "Civil Status", False),
]

HEADER_ALIASES = {
    "role": "authority",
    "account role": "authority",
    "access role": "authority",
    "sex": "sex",
    "gender": "sex",
    "sex gender": "sex",
    "dob": "date_of_birth",
    "birth date": "date_of_birth",
    "date of birth": "date_of_birth",
    "unit name": "unit",
    "assigned unit": "unit",
    "branch": "branch_of_service",
    "branch of service": "branch_of_service",
    "mobile": "contact_number",
    "phone": "contact_number",
    "contact": "contact_number",
    "contact number": "contact_number",
    "email": "email",
    "email address": "email",
    "home address": "home_address",
    "address": "home_address",
    "emergency contact": "emergency_contact_name",
    "emergency contact name": "emergency_contact_name",
    "emergency contact number": "emergency_contact_number",
    "emergency number": "emergency_contact_number",
    "date enlisted": "date_enlisted",
    "date commissioned": "date_commissioned",
    "afsc": "afsc_primary",
    "afsc primary": "afsc_primary",
    "primary afsc": "afsc_primary",
    "afsc secondary": "afsc_secondary",
    "secondary afsc": "afsc_secondary",
    "afsc tertiary": "afsc_tertiary",
    "tertiary afsc": "afsc_tertiary",
    "highest education": "highest_education",
    "educational attainment": "highest_education",
    "civil status": "civil_status",
    "serial": "serial_number",
    "serial number": "serial_number",
    "first name": "first_name",
    "given name": "first_name",
    "middle name": "middle_name",
    "last name": "last_name",
    "surname": "last_name",
    "rank": "rank",
    "designation": "designation",
    "category": "category",
}


def _normalise_header(value) -> str:
    text = str(value or "").strip().lower().replace("*", " ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _build_header_map() -> dict[str, str]:
    mapping = dict(HEADER_ALIASES)
    for field, label, _required in TEMPLATE_COLUMNS:
        mapping[_normalise_header(field)] = field
        mapping[_normalise_header(label)] = field
    return mapping


HEADER_MAP = _build_header_map()


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value).strip()


def _as_date(value, label: str) -> dt.date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)):
        try:
            parsed = from_excel(value)
        except Exception:  # noqa: BLE001 - reported as a friendly row error
            raise ValueError(f"{label}: use YYYY-MM-DD.")
        return parsed.date() if isinstance(parsed, dt.datetime) else parsed
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{label}: use YYYY-MM-DD (for example 1990-05-24).")


def _unit_lookup(unit_ids=None) -> dict[str, Unit]:
    """Resolve a unit by name, acronym or the template's 'ACRONYM — Name'.

    ``unit_ids`` limits the lookup to the units a template was built for, so a
    workbook cannot smuggle in a unit outside the selected chain.
    """
    queryset = Unit.objects.filter(is_active=True).order_by("name")
    if unit_ids is not None:
        queryset = queryset.filter(id__in=unit_ids)
    units = list(queryset)
    acronyms: dict[str, list[Unit]] = {}
    for unit in units:
        acronyms.setdefault(unit.acronym.strip().lower(), []).append(unit)

    lookup: dict[str, Unit] = {}
    for unit in units:
        values = {
            unit.name,
            unit.acronym,
            f"{unit.acronym} - {unit.name}",
            f"{unit.acronym} — {unit.name}",
            f"{unit.acronym}: {unit.name}",
        }
        for value in values:
            lookup[value.strip().lower()] = unit
    for acronym, matches in acronyms.items():
        key = acronym.strip().lower()
        if len(matches) == 1:
            lookup[key] = matches[0]
        else:
            lookup.pop(key, None)
    return lookup


def _resolve_unit(raw: str, lookup: dict[str, Unit]) -> Unit | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return next((unit for unit in lookup.values() if unit.id == int(text)), None)
    return lookup.get(text.lower())


def _flatten_errors(errors, prefix: str = "") -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    if isinstance(errors, dict):
        for key, value in errors.items():
            field = f"{prefix}.{key}" if prefix else str(key)
            messages.extend(_flatten_errors(value, field))
    elif isinstance(errors, (list, tuple)):
        for value in errors:
            messages.extend(_flatten_errors(value, prefix))
    else:
        messages.append((prefix or "detail", str(errors)))
    return messages


def _list_values(options: dict, key: str) -> list[str]:
    values = options.get(key) or []
    return [str(value) for value in values if str(value).strip()]


def build_personnel_template(options: dict, scope_label: str = "") -> bytes:
    """Build the downloadable workbook with list validation on every picker.

    The Unit dropdown is whatever ``options["units"]`` holds: when the template
    is built for one unit, that is the unit and its subordinate units only.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Personnel"

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    required_fill = PatternFill("solid", fgColor="C00000")
    thin = Side(style="thin", color="B7C3D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for index, (_field, label, required) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=index, value=f"{label}{' *' if required else ''}")
        cell.fill = required_fill if required else header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
        sheet.column_dimensions[get_column_letter(index)].width = (
            32 if label in {"Home Address", "Emergency Contact Name"} else 19
        )

    date_columns = {"Date of Birth", "Date Enlisted", "Date Commissioned"}
    for index, (_field, label, _required) in enumerate(TEMPLATE_COLUMNS, start=1):
        letter = get_column_letter(index)
        for row in range(2, MAX_IMPORT_ROWS + 2):
            cell = sheet.cell(row=row, column=index)
            cell.border = border
            if label in date_columns:
                cell.number_format = "yyyy-mm-dd"
            elif label in {"Serial Number", "Contact Number", "Emergency Contact Number"}:
                cell.number_format = "@"

    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(TEMPLATE_COLUMNS))}1"
    sheet.row_dimensions[1].height = 34

    reference = workbook.create_sheet("Reference")
    # Administrator accounts are provisioned in TOWEMS itself, never through a
    # bulk workbook, so the template does not offer that Account Role.
    account_roles = [
        value
        for value in _list_values(options, "account_roles")
        if value.casefold() != "administrator"
    ]
    lists = [
        ("AccountRole", account_roles),
        ("Category", _list_values(options, "categories")),
        ("Designation", _list_values(options, "designations")),
        ("Rank", _list_values(options, "ranks")),
        ("Unit", [row["label"] for row in options.get("units", [])]),
        ("Branch", _list_values(options, "branches") or BRANCH_OPTIONS),
        ("Sex", _list_values(options, "sexes")),
        ("BloodType", _list_values(options, "blood_types")),
        ("Education", _list_values(options, "educations")),
        ("CivilStatus", _list_values(options, "civil_statuses")),
    ]

    reference_columns: dict[str, str] = {}
    for col_index, (name, values) in enumerate(lists, start=1):
        letter = get_column_letter(col_index)
        reference_columns[name] = letter
        reference.cell(row=1, column=col_index, value=name)
        reference.cell(row=1, column=col_index).font = Font(bold=True)
        for row_index, value in enumerate(values, start=2):
            reference.cell(row=row_index, column=col_index, value=value)
        reference.column_dimensions[letter].width = max(
            14, min(46, max(len(value) for value in values) + 2) if values else 14
        )

    reference.sheet_state = "hidden"

    validation_columns = {
        "Account Role": "AccountRole",
        "Category": "Category",
        "Designation": "Designation",
        "Rank": "Rank",
        "Unit": "Unit",
        "Branch of Service": "Branch",
        "Sex / Gender": "Sex",
        "Blood Type": "BloodType",
        "Highest Education": "Education",
        "Civil Status": "CivilStatus",
    }
    headers_by_label = {
        label: get_column_letter(index)
        for index, (_field, label, _required) in enumerate(TEMPLATE_COLUMNS, start=1)
    }
    for label, list_name in validation_columns.items():
        letter = headers_by_label.get(label)
        if letter is None:
            continue
        values = dict(lists)[list_name]
        if not values:
            continue
        reference_letter = reference_columns[list_name]
        highest = max(2, len(values) + 1)
        validation = DataValidation(
            type="list",
            formula1=f"Reference!${reference_letter}$2:${reference_letter}${highest}",
            allow_blank=True,
            showDropDown=False,
        )
        validation.error = "Choose a value from the dropdown list."
        validation.errorTitle = "Invalid value"
        validation.prompt = "Select a value from the list."
        validation.promptTitle = label
        sheet.add_data_validation(validation)
        validation.add(f"{letter}2:{letter}{MAX_IMPORT_ROWS + 1}")

    instructions = workbook.create_sheet("Instructions")
    instructions.column_dimensions["A"].width = 110
    lines = [
        ("TOWEMS personnel bulk registration", True),
        ("", False),
        ("1. Fill in one person per row on the Personnel sheet. Required columns are marked *.", False),
        ("2. Do not change the header row or add extra columns; the importer reads it by header name.", False),
        ("3. Username is created automatically from the Serial Number.", False),
        ("4. The administrator enters one initial password in the import window. Every new account must change it at first sign-in.", False),
        ("5. Roles, categories, designations, ranks, units, sex, blood type, education and civil status use dropdown lists from the live system. Administrator is not offered as an Account Role here; create administrators in TOWEMS.", False),
        ("6. Dates must be entered as YYYY-MM-DD (for example 1990-05-24).", False),
        ("7. Designation and rank are stored as text. Designations retired from the picker are refused; \"Other\" is recorded as \"Personnel\".", False),
        ("8. A workbook with any invalid row imports nothing. Fix the listed rows and upload it again.", False),
    ]
    if scope_label:
        lines.append(
            (
                f"9. This template is for {scope_label} only. The Unit list and the import are "
                "limited to that unit and its subordinate units.",
                False,
            )
        )
    for row_index, (text, bold) in enumerate(lines, start=1):
        cell = instructions.cell(row=row_index, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = Font(bold=True, size=14, color="1F4E78")

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def import_personnel_workbook(upload, password: str, actor, scope_unit=None) -> dict:
    """Validate and import one workbook.

    Returns ``{"created": n, "users": [...], "errors": [...]}``.  Any row error
    means nothing is created, so a failed batch never leaves half a unit
    registered.

    ``scope_unit`` pins the batch to one unit's chain: a row naming any other
    unit is rejected, whatever the workbook says.
    """
    errors: list[dict] = []
    if upload is None:
        return {
            "created": 0,
            "users": [],
            "errors": [{"row": 0, "field": "file", "message": "Choose an .xlsx file."}],
        }
    if not str(upload.name or "").lower().endswith(".xlsx"):
        return {
            "created": 0,
            "users": [],
            "errors": [
                {
                    "row": 0,
                    "field": "file",
                    "message": "Upload the .xlsx template. Legacy .xls files are not supported.",
                }
            ],
        }
    if upload.size > MAX_IMPORT_BYTES:
        return {
            "created": 0,
            "users": [],
            "errors": [
                {
                    "row": 0,
                    "field": "file",
                    "message": f"Maximum file size is {MAX_IMPORT_BYTES // (1024 * 1024)} MiB.",
                }
            ],
        }

    head = upload.read(4)
    upload.seek(0)
    if head != b"PK\x03\x04":
        return {
            "created": 0,
            "users": [],
            "errors": [
                {"row": 0, "field": "file", "message": "That file is not a valid .xlsx workbook."}
            ],
        }

    try:
        validate_password_policy(password)
    except serializers.ValidationError as exc:
        messages = _flatten_errors(exc.detail)
        return {
            "created": 0,
            "users": [],
            "errors": [
                {"row": 0, "field": field, "message": message}
                for field, message in messages
            ],
        }

    try:
        workbook = load_workbook(upload, data_only=True, read_only=True)
    except Exception:  # noqa: BLE001 - malformed workbook is an operator error
        return {
            "created": 0,
            "users": [],
            "errors": [
                {
                    "row": 0,
                    "field": "file",
                    "message": "The workbook could not be read. Download a fresh template and try again.",
                }
            ],
        }

    sheet = workbook["Personnel"] if "Personnel" in workbook.sheetnames else workbook.active
    rows = sheet.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        return {
            "created": 0,
            "users": [],
            "errors": [{"row": 0, "field": "file", "message": "The workbook is empty."}],
        }

    column_map: dict[int, str] = {}
    for index, value in enumerate(header, start=1):
        field = HEADER_MAP.get(_normalise_header(value))
        if field:
            column_map[index] = field

    required_fields = {
        field for field, _label, required in TEMPLATE_COLUMNS if required
    }
    present_fields = set(column_map.values())
    missing = sorted(required_fields - present_fields)
    if missing:
        labels = {
            field: label for field, label, _required in TEMPLATE_COLUMNS
        }
        return {
            "created": 0,
            "users": [],
            "errors": [
                {
                    "row": 1,
                    "field": field,
                    "message": f"Missing required column: {labels.get(field, field)}.",
                }
                for field in missing
            ],
        }

    scope_ids = None
    scope_label = ""
    if scope_unit is not None:
        from apps.command.services import descendant_ids

        scope_ids = descendant_ids(scope_unit.id)
        scope_label = f"{scope_unit.acronym} ({scope_unit.name}) and its subordinate units"

    unit_lookup = _unit_lookup(scope_ids)
    everything_lookup = _unit_lookup() if scope_ids is not None else unit_lookup
    request_stub = SimpleNamespace(user=actor)
    valid_rows: list[tuple[int, PersonnelWriteSerializer]] = []
    seen_serials: set[str] = set()

    for row_number, values in enumerate(rows, start=2):
        if row_number > MAX_IMPORT_ROWS + 1:
            errors.append(
                {
                    "row": row_number,
                    "field": "file",
                    "message": f"Import at most {MAX_IMPORT_ROWS} people per workbook.",
                }
            )
            break

        raw = {
            field: values[index - 1] if index - 1 < len(values) else None
            for index, field in column_map.items()
        }
        if not any(_as_text(value) for value in raw.values()):
            continue

        serial_number = _as_text(raw.get("serial_number"))
        unit = _resolve_unit(_as_text(raw.get("unit")), unit_lookup)
        if not serial_number:
            errors.append(
                {
                    "row": row_number,
                    "field": "serial_number",
                    "message": "Serial Number is required.",
                }
            )
        elif serial_number.casefold() in seen_serials:
            errors.append(
                {
                    "row": row_number,
                    "field": "serial_number",
                    "message": f"Serial Number {serial_number} appears more than once in this workbook.",
                }
            )
        else:
            seen_serials.add(serial_number.casefold())
            # Serial numbers identify a person across the whole force: one that
            # already exists - in any unit - is never registered a second time.
            existing = (
                User.objects.filter(serial_number__iexact=serial_number)
                .select_related("unit")
                .first()
            )
            if existing is not None:
                where = (
                    f"{existing.unit.acronym} ({existing.unit.name})"
                    if existing.unit_id
                    else "another unit"
                )
                errors.append(
                    {
                        "row": row_number,
                        "field": "serial_number",
                        "message": (
                            f"Serial Number {serial_number} is already registered to "
                            f"{where} (@{existing.username}). A serial number can only "
                            "belong to one person, across all units."
                        ),
                    }
                )

        if not _as_text(raw.get("unit")):
            errors.append(
                {"row": row_number, "field": "unit", "message": "Unit is required."}
            )
        elif unit is None:
            outside = _resolve_unit(_as_text(raw.get("unit")), everything_lookup)
            if outside is not None:
                errors.append(
                    {
                        "row": row_number,
                        "field": "unit",
                        "message": (
                            f"Unit {outside.acronym} is outside this template's scope. "
                            f"It covers {scope_label} only."
                        ),
                    }
                )
            else:
                errors.append(
                    {
                        "row": row_number,
                        "field": "unit",
                        "message": "Unit was not found. Use the dropdown value from the template.",
                    }
                )

        authority = _as_text(raw.get("authority"))
        if authority.casefold() == "administrator":
            errors.append(
                {
                    "row": row_number,
                    "field": "authority",
                    "message": (
                        "Administrator accounts cannot be created from a workbook. "
                        "Choose another Account Role, or create the administrator in TOWEMS."
                    ),
                }
            )

        designation = _as_text(raw.get("designation"))
        if designation.casefold() == "other":
            # The picker calls this value "Personnel" now; keep imports in step
            # with the dropdown instead of storing the retired spelling.
            designation = "Personnel"
        elif designation.casefold() in RETIRED_DESIGNATION_KEYS:
            errors.append(
                {
                    "row": row_number,
                    "field": "designation",
                    "message": (
                        f"{designation} is no longer an available designation. "
                        "Choose a value from the template's Designation dropdown."
                    ),
                }
            )

        data = {
            "serial_number": serial_number,
            "first_name": _as_text(raw.get("first_name")),
            "middle_name": _as_text(raw.get("middle_name")),
            "last_name": _as_text(raw.get("last_name")),
            "rank": _as_text(raw.get("rank")),
            "designation": designation,
            "authority": authority,
            "category": _as_text(raw.get("category")),
            "unit_id": unit.id if unit else None,
            "branch_of_service": _as_text(raw.get("branch_of_service")) or "PAF",
            "contact_number": _as_text(raw.get("contact_number")),
            "password": password,
            "is_active": True,
        }
        for field in PERSONNEL_DETAIL_FIELDS:
            if field in DATE_DETAIL_FIELDS:
                try:
                    data[field] = _as_date(
                        raw.get(field), field.replace("_", " ").title()
                    )
                except ValueError as exc:
                    errors.append(
                        {"row": row_number, "field": field, "message": str(exc)}
                    )
            else:
                data[field] = _as_text(raw.get(field))

        serializer = PersonnelWriteSerializer(
            data=data, context={"request": request_stub}
        )
        if not serializer.is_valid():
            for field, message in _flatten_errors(serializer.errors):
                errors.append(
                    {"row": row_number, "field": field, "message": message}
                )
        else:
            valid_rows.append((row_number, serializer))

    if errors:
        # Cap the payload so a badly formatted sheet cannot flood the console.
        return {"created": 0, "users": [], "errors": errors[:200]}

    created_users: list[User] = []
    try:
        with transaction.atomic():
            for _row_number, serializer in valid_rows:
                created_users.append(serializer.save())
    except Exception as exc:  # noqa: BLE001 - transaction rolled back below
        return {
            "created": 0,
            "users": [],
            "errors": [
                {
                    "row": 0,
                    "field": "database",
                    "message": f"The import was rolled back: {exc}",
                }
            ],
        }

    for user in created_users:
        record_activity(
            user=actor,
            activity_type="user_created",
            module="users",
            description=f"Imported personnel {user.username} for {user.unit_legacy or 'unassigned'}",
            resource_id=user.id,
            resource_type="user",
        )
    log_action(
        actor=actor,
        action="import_personnel",
        entity_type="personnel",
        note=f"Imported {len(created_users)} personnel from an Excel workbook",
        after={"created": len(created_users)},
    )

    return {
        "created": len(created_users),
        "users": [
            {
                "id": user.id,
                "username": user.username,
                "full_name": user.full_name,
                "unit_id": user.unit_id,
            }
            for user in created_users
        ],
        "errors": [],
    }
