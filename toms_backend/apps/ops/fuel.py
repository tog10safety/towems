"""POL stock rules shared by the API and the spreadsheet import.

The register and a workbook record the same thing, so they must refuse the same
rows: a grade that does not belong to the POL category, a drum row without a
per-drum capacity, or a bulk tank carrying one. Keeping the rules here means an
import can never write a POL row the API would reject.

The POL catalogue is fixed by code: POL-01 Aviation Fuel is bought as AVGas or
Jet A1, POL-02 Automotive Fuel as diesel or gasoline, and the other categories
(lubricants, hydraulic fluids, greases, gear oils, coolants, special-purpose
fluids and solvents) are single products with no grade.
"""

from __future__ import annotations

import re

from apps.ops.models import InventoryItem

#: The grades each POL category is stocked in, keyed by the category code. A
#: category absent from this map must leave ``fuel_grade`` empty.
GRADES_BY_CATEGORY = {
    "POL-01": ("avgas", "jet_a1"),
    "POL-02": ("diesel", "gasoline"),
}


def normalise(value) -> str:
    """The comparison key for a posted value or a spreadsheet cell."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _lookup(choices, aliases: dict[str, str] | None = None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key, label in choices:
        if not key:
            continue
        lookup[normalise(key)] = key
        lookup[normalise(label)] = key
    for alias, key in (aliases or {}).items():
        lookup[normalise(alias)] = key
    return lookup


def fuel_grade_lookup() -> dict[str, str]:
    return _lookup(
        InventoryItem.FUEL_GRADE_CHOICES,
        {
            "jet fuel": "jet_a1",
            "jet a-1": "jet_a1",
            "aviation gasoline": "avgas",
            "av gas": "avgas",
            "gas": "gasoline",
            "petrol": "gasoline",
        },
    )


def fuel_form_lookup() -> dict[str, str]:
    """Drums and bulk, however the cell is worded."""
    return _lookup(
        InventoryItem.FUEL_FORM_CHOICES,
        {"drum": "drum", "drums": "drum", "bulk tank": "bulk", "tank": "bulk"},
    )


def fuel_grade_labels() -> list[str]:
    return [label for key, label in InventoryItem.FUEL_GRADE_CHOICES if key]


def fuel_form_labels() -> list[str]:
    return [label for key, label in InventoryItem.FUEL_FORM_CHOICES if key]


def category_grade_keys(category_code: str) -> tuple[str, ...]:
    """The grades this POL category is stocked in; empty when it has none."""
    return GRADES_BY_CATEGORY.get((category_code or "").strip().upper(), ())


def grade_label(key: str) -> str:
    return dict(InventoryItem.FUEL_GRADE_CHOICES).get(key, key)


def grade_labels(keys) -> str:
    """'AVGas or Jet A1' for the grades a category accepts."""
    return " or ".join(grade_label(key) for key in keys)


def validate_pol_dates(dates: dict, category=None) -> dict[str, str]:
    """The POL batch dates: issue, test and expiry belong to POL stock only.

    A store of fuel or oil has a shelf life, so an expiry before the issue date
    is refused rather than stored as a contradiction.
    """
    errors: dict[str, str] = {}
    is_pol = category is not None and getattr(category, "kind", "") == "fuel"
    if not is_pol:
        if any(value for value in dates.values()):
            errors["date_issued"] = (
                "Issue, testing and expiry dates apply only to a POL category."
            )
        return errors

    issued = dates.get("date_issued")
    expiry = dates.get("expiry_date")
    if issued and expiry and expiry < issued:
        errors["expiry_date"] = "Expiry date cannot be before the date issued."
    return errors


def validate_fuel(grade: str, form: str, capacity, category=None) -> dict[str, str]:
    """Cross-field POL errors, keyed by field name like the API response.

    ``category`` is the ``InventoryCategory`` the row is filed under. A row
    outside the POL class cannot carry grade or form at all, which keeps a
    materiel item from ever reporting litres; inside POL the category decides
    which grades are valid and whether a grade is expected.
    """
    errors: dict[str, str] = {}
    is_pol = category is not None and getattr(category, "kind", "") == "fuel"
    if not is_pol:
        if grade or form:
            errors["fuel_grade"] = (
                "Grade and form apply only to a POL category. Leave both blank."
            )
        return errors

    allowed = category_grade_keys(getattr(category, "code", ""))
    if allowed:
        if not grade:
            errors["fuel_grade"] = f"Choose the grade: {grade_labels(allowed)}."
        elif grade not in allowed:
            errors["fuel_grade"] = (
                f"{category.name} is stocked as {grade_labels(allowed)}; "
                f"{grade_label(grade)} is not one of them."
            )
    elif grade:
        errors["fuel_grade"] = f"{category.name} has no grades; leave Grade empty."

    if not form:
        errors["fuel_form"] = "Choose whether this POL stock is held in drums or in bulk."
    if form == "drum" and not capacity:
        errors["drum_capacity_litres"] = "Give the capacity of one drum in litres."
    if form == "bulk" and capacity:
        errors["drum_capacity_litres"] = (
            "Bulk stock is measured in litres; leave the per-drum capacity empty."
        )
    return errors
