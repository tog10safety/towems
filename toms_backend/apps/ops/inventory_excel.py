"""Excel import/export for the inventory register.

The register is a Logistics Officer's tool: a unit's materiel, equipment,
aerospace ground equipment, firearms, ammunition, communications gear and
vehicles are loaded here in bulk, corrected in a spreadsheet and sent back.
Only the holder of the ``manage_inventory`` capability (the Logistics Officer
post, or an Administrator) may download the template, export or import - the
Safety Officer keeps read access to the register itself.

Every field an encoder could otherwise misspell is a dropdown in the workbook
(asset class, category, condition, unit of measure, unit), and the import
re-checks all of them against the database. Nothing is written unless every row
in the workbook is valid, so a half-imported register is not possible.

Import always *creates*. An item that already exists is edited in the app, not
overwritten from a workbook, because changing ``quantity_total`` outside the
ledger would leave stock balances that no transaction explains. A duplicate
asset tag is reported as a row error instead.
"""

from __future__ import annotations

import re
from io import BytesIO

from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from apps.core import roles
from apps.core.scoping import accessible_unit_ids
from apps.legacy.models import Unit
from apps.ops.models import InventoryCategory, InventoryItem, InventoryTransaction

SHEET_TITLE = "Inventory"
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 2000

#: (field, header). The template and the export share this order, so an export
#: can be edited and imported again without moving a column.
COLUMNS = [
    ("kind", "Asset Class"),
    ("category", "Category"),
    ("name", "Item Name"),
    ("asset_tag", "Asset Tag"),
    ("serial_number", "Serial Number"),
    ("description", "Description"),
    ("quantity_total", "Quantity"),
    ("unit_of_measure", "Unit of Measure"),
    ("serviceability", "Condition"),
    ("unit", "Unit"),
    ("storage_location", "Storage Location"),
    ("notes", "Notes"),
]

HEADER_ALIASES = {
    "asset class": "kind",
    "class": "kind",
    "kind": "kind",
    "category": "category",
    "category name": "category",
    "item": "name",
    "item name": "name",
    "name": "name",
    "asset tag": "asset_tag",
    "asset number": "asset_tag",
    "tag": "asset_tag",
    "serial number": "serial_number",
    "serial": "serial_number",
    "description": "description",
    "quantity": "quantity_total",
    "quantity total": "quantity_total",
    "total quantity": "quantity_total",
    "unit of measure": "unit_of_measure",
    "uom": "unit_of_measure",
    "condition": "serviceability",
    "serviceability": "serviceability",
    "unit": "unit",
    "storage location": "storage_location",
    "location": "storage_location",
    "notes": "notes",
    "remarks": "notes",
}

#: Headers a workbook must carry. Everything else has a documented default, so
#: a hand-built workbook still imports; a missing quantity or category does not.
REQUIRED_HEADERS = ("category", "name", "quantity_total")

#: The suggested units of measure. Existing stored values are accepted too, so
#: an export always round-trips (see :func:`units_of_measure`).
UNIT_OF_MEASURE_VALUES = (
    "unit",
    "each",
    "set",
    "pair",
    "kit",
    "box",
    "pack",
    "roll",
    "round",
    "magazine",
    "litre",
    "gallon",
    "kilogram",
    "metre",
    "sheet",
)


class InventoryImportError(Exception):
    """A workbook-level refusal, with optional per-row detail."""

    def __init__(self, message: str, errors: dict | None = None):
        super().__init__(message)
        self.errors = errors or {}


def _authenticated(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def can_manage(user) -> bool:
    """Download the template, export or import - Logistics Officer only."""
    return _authenticated(user) and roles.has_capability(user, roles.CAP_MANAGE_INVENTORY)


def can_view_sensitive(user) -> bool:
    return _authenticated(user) and roles.has_capability(
        user, roles.CAP_VIEW_SENSITIVE_INVENTORY
    )


def is_administrator(user) -> bool:
    return _authenticated(user) and getattr(user, "authority", None) == roles.BASE_ADMIN


# ---------------------------------------------------------------------------
# Choices shared by the template, the export and the import validation
# ---------------------------------------------------------------------------


def class_labels() -> list[str]:
    return [label for _, label in InventoryCategory.KIND_CHOICES]


def _class_lookup() -> dict[str, str]:
    lookup = {_normalise_key(key): key for key, _ in InventoryCategory.KIND_CHOICES}
    lookup.update(
        {_normalise_key(label): key for key, label in InventoryCategory.KIND_CHOICES}
    )
    return lookup


def condition_lookup() -> dict[str, str]:
    lookup = {_normalise_key(key): key for key, _ in InventoryItem.SERVICEABILITY_CHOICES}
    lookup.update(
        {_normalise_key(label): key for key, label in InventoryItem.SERVICEABILITY_CHOICES}
    )
    return lookup


def condition_labels() -> list[str]:
    return [label for _, label in InventoryItem.SERVICEABILITY_CHOICES]


def units_of_measure() -> list[str]:
    """The picker list, plus any value already stored so an export round-trips."""
    stored = (
        InventoryItem.objects.exclude(unit_of_measure="")
        .values_list("unit_of_measure", flat=True)
        .distinct()
    )
    values = {value.strip().lower() for value in stored if value}
    values.update(UNIT_OF_MEASURE_VALUES)
    return sorted(values)


def category_label(category: InventoryCategory) -> str:
    return f"{category.code} - {category.name}"


def categories_for_template() -> list[InventoryCategory]:
    return list(InventoryCategory.objects.all().order_by("kind", "name"))


def units_for_template(user) -> list[Unit]:
    """Units this caller may import into: their own tree (all, for an admin)."""
    allowed = accessible_unit_ids(user)
    if not allowed:
        return []
    return list(Unit.objects.filter(pk__in=allowed, is_active=True).order_by("name"))


# ---------------------------------------------------------------------------
# Workbook construction
# ---------------------------------------------------------------------------


def _style_header(sheet, columns) -> None:
    for index, (_, header) in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"


def _column_widths(sheet) -> None:
    for column, width in {
        "A": 30,
        "B": 34,
        "C": 34,
        "D": 18,
        "E": 20,
        "F": 44,
        "G": 11,
        "H": 16,
        "I": 18,
        "J": 26,
        "K": 30,
        "L": 32,
    }.items():
        sheet.column_dimensions[column].width = width


def _lists_sheet(workbook, categories, units, uom) -> dict[str, str]:
    """The picker source sheet.

    Excel rejects an inline list longer than 255 characters, and the category
    list alone runs past that in a real unit, so every dropdown points at this
    sheet instead of embedding its values in the validation formula.
    """
    sheet = workbook.create_sheet("Lists")
    columns = [
        ("Asset Class", class_labels(), 30),
        ("Category", [category_label(row) for row in categories], 44),
        ("Condition", condition_labels(), 20),
        ("Unit of Measure", list(uom), 18),
        ("Unit", [unit.name for unit in units], 40),
    ]
    ranges: dict[str, str] = {}
    keys = ("kind", "category", "serviceability", "unit_of_measure", "unit")
    for index, (title, values, width) in enumerate(columns, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = width
        header = sheet.cell(row=1, column=index, value=title)
        header.font = Font(bold=True)
        for offset, value in enumerate(values, start=2):
            sheet.cell(row=offset, column=index, value=value)
        if values:
            ranges[keys[index - 1]] = f"Lists!${letter}$2:${letter}${len(values) + 1}"
    sheet.sheet_state = "hidden"
    return ranges


def _add_pickers(sheet, ranges: dict[str, str], *, rows: int = 1000) -> None:
    targets = {
        "kind": ("A", "Asset class", "Pick the class this item belongs to."),
        "category": ("B", "Category", "Pick a category from the list. A category carries the class and the controlled flag."),
        "unit_of_measure": ("H", "Unit of measure", "Pick how this item is counted (unit, rounds, litres…)."),
        "serviceability": ("I", "Condition", "Pick the item's condition."),
        "unit": ("J", "Unit", "Pick the holding unit from the dropdown."),
    }
    for field, (letter, title, prompt) in targets.items():
        reference = ranges.get(field)
        if not reference:
            continue
        picker = DataValidation(
            type="list",
            formula1=reference,
            allow_blank=True,
            showErrorMessage=True,
            showInputMessage=True,
        )
        picker.errorTitle = f"Unknown {title.lower()}"
        picker.error = "Choose a value from the dropdown; free text is not accepted."
        picker.promptTitle = title
        picker.prompt = prompt
        sheet.add_data_validation(picker)
        for row in range(2, rows + 2):
            picker.add(f"{letter}{row}")

    quantity = DataValidation(
        type="whole",
        operator="greaterThanOrEqual",
        formula1="0",
        allow_blank=False,
        showErrorMessage=True,
        showInputMessage=True,
    )
    quantity.errorTitle = "Invalid quantity"
    quantity.error = "Quantity must be a whole number of 0 or more."
    quantity.promptTitle = "Quantity"
    quantity.prompt = "How many of this item the unit holds. Recorded as the opening balance."
    sheet.add_data_validation(quantity)
    for row in range(2, rows + 2):
        quantity.add(f"G{row}")


def build_template(user=None) -> bytes:
    """The workbook an encoder fills in: headers, dropdowns and instructions."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    _style_header(sheet, COLUMNS)
    _column_widths(sheet)

    categories = categories_for_template()
    units = units_for_template(user) if user is not None else []
    ranges = _lists_sheet(workbook, categories, units, units_of_measure())
    _add_pickers(sheet, ranges)

    notes = workbook.create_sheet("Instructions")
    notes.column_dimensions["A"].width = 118
    lines = [
        ("TOWEMS inventory import", True),
        ("", False),
        ("1. Fill in the 'Inventory' sheet. Do not rename or reorder the columns.", False),
        ("2. Asset Class and Category are dropdowns. The category decides the asset class,"
         " and a controlled category (Firearms, Ammunition) marks the item controlled.", False),
        ("3. Condition is one of: " + ", ".join(condition_labels()) + ".", False),
        ("4. Unit of Measure is a dropdown: " + ", ".join(units_of_measure()) + ".", False),
        ("5. Unit is a dropdown of the units you may hold stock for. Leave it blank to use"
         " your own unit.", False),
        ("6. Quantity is the opening balance. It is written to the ledger as a receipt, so the"
         " stock balance always reconciles.", False),
        ("7. Asset Tag must be unique. An item that already exists is edited in the app;"
         " importing never overwrites stock on hand.", False),
        ("8. Every row is validated before anything is saved. A workbook with any invalid row"
         " imports nothing, so you can fix the reported rows and upload it again.", False),
        ("9. Import at most 2000 rows and keep the file under 5 MB.", False),
    ]
    for index, (text, bold) in enumerate(lines, start=1):
        cell = notes.cell(row=index, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = Font(bold=True, size=13)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_workbook(items, user=None) -> bytes:
    """The caller's current selection as a re-importable workbook."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    _style_header(sheet, COLUMNS)
    _column_widths(sheet)

    categories = categories_for_template()
    units = units_for_template(user) if user is not None else []
    ranges = _lists_sheet(workbook, categories, units, units_of_measure())
    _add_pickers(sheet, ranges)

    condition_labels_by_key = dict(InventoryItem.SERVICEABILITY_CHOICES)
    for index, row in enumerate(items, start=2):
        values = [
            row.category.get_kind_display(),
            category_label(row.category),
            row.name,
            row.asset_tag or "",
            row.serial_number or "",
            row.description or "",
            row.quantity_total,
            row.unit_of_measure or "unit",
            condition_labels_by_key.get(row.serviceability, row.serviceability),
            row.unit.name if row.unit_id else "",
            row.storage_location or "",
            row.notes or "",
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=index, column=column, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_header(value) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return HEADER_ALIASES.get(text, "")


def _normalise_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _category_lookup() -> tuple[
    dict[str, InventoryCategory], dict[str, list[InventoryCategory]]
]:
    by_key: dict[str, InventoryCategory] = {}
    by_name: dict[str, list[InventoryCategory]] = {}
    for category in InventoryCategory.objects.all():
        by_key[_normalise_key(category.code)] = category
        by_key[_normalise_key(category_label(category))] = category
        by_name.setdefault(_normalise_key(category.name), []).append(category)
    return by_key, by_name


def _resolve_category(value, errors: dict) -> InventoryCategory | None:
    text = _text(value)
    if not text:
        errors["category"] = "Choose a category. It decides the asset class."
        return None
    by_key, by_name = _category_lookup()
    if _normalise_key(text) in by_key:
        return by_key[_normalise_key(text)]

    # A "CODE - Name" cell re-spaced or re-cased still resolves, but the code
    # alone is not enough: the name must belong to that same category, so a
    # typo cannot silently file stock under the first category it resembles.
    parts = re.split(r"\s*[-–—]\s*", text, maxsplit=1)
    if len(parts) == 2:
        candidate = by_key.get(_normalise_key(parts[0]))
        if candidate is not None and _normalise_key(parts[1]) == _normalise_key(candidate.name):
            return candidate

    matches = by_name.get(_normalise_key(text)) or []
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        errors["category"] = (
            f"{len(matches)} categories are named {text!r}; use the category code instead."
        )
        return None
    errors["category"] = f"No category is named {text!r}. Use the template's dropdown."
    return None


def _resolve_unit(user, value, errors: dict) -> Unit | None:
    allowed = accessible_unit_ids(user)
    text = _text(value)
    if not text:
        own = getattr(user, "unit", None)
        if own is None or (allowed and own.id not in allowed):
            errors["unit"] = "Name the unit this stock is held by."
            return None
        return own

    key = text.lower()
    candidates = list(Unit.objects.filter(pk__in=allowed, is_active=True)) if allowed else []
    for unit in candidates:
        if key in {unit.name.lower(), (unit.acronym or "").lower()}:
            return unit
    errors["unit"] = (
        f"No active unit you hold stock for is named {text!r}. "
        "Choose one from the dropdown."
    )
    return None


def _resolve_condition(value, errors: dict) -> str:
    text = _text(value)
    if not text:
        return "serviceable"
    key = condition_lookup().get(_normalise_key(text))
    if key is None:
        errors["serviceability"] = (
            f"Unknown condition {text!r}. Choose one of: "
            + ", ".join(condition_labels())
            + "."
        )
        return ""
    return key


def _resolve_uom(value, errors: dict) -> str:
    text = _text(value).lower()
    if not text:
        return "unit"
    allowed = units_of_measure()
    if text not in allowed:
        errors["unit_of_measure"] = (
            f"Unknown unit of measure {text!r}. Choose one of: " + ", ".join(allowed) + "."
        )
        return ""
    return text


def _resolve_quantity(value, errors: dict) -> int:
    text = _text(value)
    if text == "":
        errors["quantity_total"] = "Enter the quantity on hand."
        return 0
    try:
        number = int(float(text))
    except (TypeError, ValueError):
        errors["quantity_total"] = f"{text!r} is not a whole number."
        return 0
    if number < 0:
        errors["quantity_total"] = "Quantity cannot be negative."
        return 0
    return number


def _require_header(mapping: dict, errors: list[str]) -> None:
    for field in REQUIRED_HEADERS:
        if field not in mapping:
            errors.append(field)


def import_workbook(user, upload) -> dict:
    """Import every row of a template workbook, or none of them."""
    if not can_manage(user):
        raise InventoryImportError(
            "Only the Logistics Officer or an Administrator may import inventory."
        )
    name = getattr(upload, "name", "") or ""
    if not name.lower().endswith(".xlsx"):
        raise InventoryImportError("Upload an .xlsx workbook produced from the TOWEMS template.")
    if getattr(upload, "size", 0) > MAX_IMPORT_BYTES:
        raise InventoryImportError("The workbook is larger than 5 MB.")

    try:
        workbook = load_workbook(filename=BytesIO(upload.read()), data_only=True)
    except Exception:  # openpyxl raises several unrelated errors
        raise InventoryImportError("That file is not a readable .xlsx workbook.") from None

    sheet = workbook[SHEET_TITLE] if SHEET_TITLE in workbook.sheetnames else workbook.active
    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    mapping: dict[str, int] = {}
    for index, value in enumerate(header_row):
        field = _normalise_header(value)
        if field and field not in mapping:
            mapping[field] = index

    missing: list[str] = []
    _require_header(mapping, missing)
    if missing:
        labels = dict(COLUMNS)
        raise InventoryImportError(
            "The workbook is missing required columns: "
            + ", ".join(labels.get(field, field) for field in missing)
            + ". Download the template and use its headers."
        )

    class_lookup = _class_lookup()
    existing_tags = {
        tag.lower()
        for tag in InventoryItem.objects.exclude(asset_tag__isnull=True)
        .exclude(asset_tag="")
        .values_list("asset_tag", flat=True)
    }
    prepared: list[dict] = []
    errors: dict[str, list[str]] = {}
    seen_tags: set[str] = set()
    rows = 0
    labels = dict(COLUMNS)

    for offset, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if all(value in (None, "") for value in values):
            continue
        rows += 1
        if rows > MAX_IMPORT_ROWS:
            raise InventoryImportError(
                f"Import at most {MAX_IMPORT_ROWS} items per workbook."
            )
        raw = {
            field: values[index] if index < len(values) else None
            for field, index in mapping.items()
            if index < len(values)
        }
        row_errors: dict[str, str] = {}

        category = _resolve_category(raw.get("category"), row_errors)
        item_name = _text(raw.get("name"))
        if not item_name:
            row_errors["name"] = "Enter the item's name."
        if len(item_name) > 200:
            row_errors["name"] = "Keep the item name under 200 characters."

        # The class column is a convenience that must agree with the category.
        if category is not None:
            declared = _text(raw.get("kind"))
            if declared:
                key = class_lookup.get(_normalise_key(declared))
                if key is None:
                    row_errors["kind"] = (
                        f"Unknown asset class {declared!r}. Choose one of: "
                        + ", ".join(class_labels())
                        + "."
                    )
                elif key != category.kind:
                    row_errors["kind"] = (
                        f"{category_label(category)} is {category.get_kind_display()}, "
                        f"not {declared!r}."
                    )
            if category.is_sensitive and not can_view_sensitive(user):
                row_errors["category"] = (
                    f"{category_label(category)} holds controlled stores; "
                    "your role may not add them."
                )

        unit = _resolve_unit(user, raw.get("unit"), row_errors)
        condition = _resolve_condition(raw.get("serviceability"), row_errors)
        uom = _resolve_uom(raw.get("unit_of_measure"), row_errors)
        quantity = _resolve_quantity(raw.get("quantity_total"), row_errors)

        asset_tag = _text(raw.get("asset_tag"))
        if asset_tag:
            if len(asset_tag) > 60:
                row_errors["asset_tag"] = "Keep the asset tag under 60 characters."
            elif asset_tag.lower() in existing_tags:
                row_errors["asset_tag"] = (
                    f"Asset tag {asset_tag!r} already exists. Edit that item in the app "
                    "instead of importing it again."
                )
            elif asset_tag.lower() in seen_tags:
                row_errors["asset_tag"] = f"Asset tag {asset_tag!r} appears twice in this workbook."
            else:
                seen_tags.add(asset_tag.lower())

        if row_errors:
            errors[f"row {offset}"] = [
                f"{labels.get(field, field)}: {message}" for field, message in row_errors.items()
            ]
            continue

        prepared.append(
            {
                "category": category,
                "name": item_name,
                "asset_tag": asset_tag or None,
                "serial_number": _text(raw.get("serial_number")),
                "description": _text(raw.get("description")),
                "quantity_total": quantity,
                "unit_of_measure": uom,
                "serviceability": condition,
                "unit": unit,
                "storage_location": _text(raw.get("storage_location")),
                "notes": _text(raw.get("notes")),
            }
        )

    if not prepared and not errors:
        raise InventoryImportError("The workbook has no inventory rows.")
    if errors:
        raise InventoryImportError(
            f"{len(errors)} row(s) need attention; nothing was imported.", errors
        )

    with transaction.atomic():
        created = InventoryItem.objects.bulk_create(
            [
                InventoryItem(
                    category=values["category"],
                    name=values["name"],
                    asset_tag=values["asset_tag"],
                    serial_number=values["serial_number"],
                    description=values["description"],
                    unit=values["unit"],
                    storage_location=values["storage_location"],
                    quantity_total=values["quantity_total"],
                    # A new item's opening balance is available immediately; the
                    # receipt below is what explains it in the ledger.
                    quantity_available=values["quantity_total"],
                    unit_of_measure=values["unit_of_measure"],
                    serviceability=values["serviceability"],
                    is_sensitive=values["category"].is_sensitive,
                    notes=values["notes"],
                )
                for values in prepared
            ]
        )
        # bulk_create() returns objects with primary keys on every supported
        # backend, so the ledger rows can point at them in the same transaction.
        InventoryTransaction.objects.bulk_create(
            [
                InventoryTransaction(
                    item=item,
                    kind="receipt",
                    quantity=item.quantity_total,
                    actor=user,
                    to_unit=item.unit,
                    reference="Excel import",
                    notes=f"Opening balance imported from {name}.",
                )
                for item in created
                if item.quantity_total
            ]
        )

    from apps.core.audit import log_action

    log_action(
        actor=user,
        action="inventory_import",
        entity_type="inventory_item",
        after={"created": len(created)},
        note=f"Imported {len(created)} inventory item(s) from {name}.",
    )
    return {"created": len(created), "rows": rows}
