"""Inventory spreadsheets and the register's statistics block.

The workbook is the Logistics Officer's bulk-loading tool: every field an
encoder could mistype is a dropdown, and the import refuses the whole workbook
if a single row is wrong. The statistics block reads the same scoped queryset
the register does, so a figure can never describe a row the caller cannot open.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from apps.ops import inventory_excel
from apps.ops.models import InventoryCategory, InventoryItem, InventoryTransaction

pytestmark = pytest.mark.django_db

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = [label for _field, label in inventory_excel.COLUMNS]


def workbook_bytes(rows, *, headers=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = inventory_excel.SHEET_TITLE
    sheet.append(list(headers or HEADERS))
    for row in rows:
        sheet.append(list(row))
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def upload(payload, name="inventory.xlsx"):
    return SimpleUploadedFile(name, payload, content_type=XLSX)


def row(**overrides):
    """One sheet row in COLUMNS order, with sensible defaults."""
    values = {
        "kind": "",
        "category": "",
        "name": "",
        "asset_tag": "",
        "serial_number": "",
        "description": "",
        "quantity_total": 1,
        "unit_of_measure": "unit",
        "serviceability": "Serviceable",
        "unit": "",
        "storage_location": "",
        "notes": "",
        "fuel_grade": "",
        "fuel_form": "",
        "drum_capacity_litres": "",
        "date_issued": "",
        "testing_date": "",
        "expiry_date": "",
        "da_number": "",
    }
    values.update(overrides)
    return [values[field] for field, _label in inventory_excel.COLUMNS]


@pytest.fixture
def age_category(db):
    return InventoryCategory.objects.get(code="AGE")


@pytest.fixture
def materiel_category(db):
    return InventoryCategory.objects.get(code="MAT")


@pytest.fixture
def fuel_category(db):
    # Seeded by migration 0043.
    return InventoryCategory.objects.get(code="POL-01")


def label(category):
    return inventory_excel.category_label(category)


# ---------------------------------------------------------------------------
# Template: every free-text field is a dropdown
# ---------------------------------------------------------------------------


def test_logistics_officer_downloads_the_template(as_user, logistics):
    response = as_user(logistics).get("/api/inventory/template/")

    assert response.status_code == 200
    assert response["Content-Type"] == XLSX
    sheet = load_workbook(BytesIO(response.content))[inventory_excel.SHEET_TITLE]
    assert [cell.value for cell in sheet[1]] == HEADERS


def test_the_template_columns_are_dropdowns_not_free_text(as_user, logistics):
    workbook = load_workbook(BytesIO(as_user(logistics).get("/api/inventory/template/").content))
    sheet = workbook[inventory_excel.SHEET_TITLE]
    validations = {str(rule.sqref).split(" ")[0][0]: rule for rule in sheet.data_validations.dataValidation}

    # Asset Class, Category, Unit of Measure, Condition and Unit each point at
    # the Lists sheet - the encoder picks a value, they cannot type one.
    for column in ("A", "B", "H", "I", "J"):
        assert column in validations, f"column {column} has no picker"
        assert validations[column].type == "list"
        assert validations[column].formula1.startswith("Lists!")
    # Quantity is a whole number, never negative.
    assert validations["G"].type == "whole"
    assert validations["G"].formula1 == "0"


def test_the_template_categories_come_from_the_register(as_user, logistics, age_category):
    """The picker is built from the register, so it can only offer real rows."""
    workbook = load_workbook(BytesIO(as_user(logistics).get("/api/inventory/template/").content))
    listed = {
        row[1]
        for row in workbook["Lists"].iter_rows(min_row=2, values_only=True)
        if row[1]
    }
    codes = set(InventoryCategory.objects.values_list("code", flat=True))
    assert label(age_category) in listed
    assert all(entry.split(" - ")[0] in codes for entry in listed)


def test_the_template_units_are_the_callers_own_tree(as_user, logistics, unit, other_unit):
    workbook = load_workbook(BytesIO(as_user(logistics).get("/api/inventory/template/").content))
    names = {
        row[4]
        for row in workbook["Lists"].iter_rows(min_row=2, values_only=True)
        if row[4]
    }
    assert unit.name in names
    assert other_unit.name not in names


def test_template_export_and_import_belong_to_the_logistics_post(as_user, safety, creator, unit):
    for user in (safety, creator):
        client = as_user(user)
        assert client.get("/api/inventory/template/").status_code == 403
        assert client.get("/api/inventory/export/").status_code == 403
        response = client.post(
            "/api/inventory/import/",
            {"file": upload(workbook_bytes([row(category="MAT - Materiel", name="X")]))},
            format="multipart",
        )
        assert response.status_code == 403
    assert not InventoryItem.objects.exists()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_creates_the_item_and_opens_the_ledger(as_user, logistics, age_category, unit):
    content = workbook_bytes(
        [
            row(
                kind="Aerospace Ground Equipment (AGE)",
                category=label(age_category),
                name="Ground Power Unit 28V",
                asset_tag="AGE-0007",
                serial_number="GSE-4471",
                description="28V DC ground power unit",
                quantity_total=3,
                unit_of_measure="unit",
                serviceability="Under Maintenance",
                unit=unit.name,
                storage_location="AGE Bay 2",
            )
        ]
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 201, response.content
    assert response.json()["created"] == 1
    item = InventoryItem.objects.get(asset_tag="AGE-0007")
    assert item.category == age_category
    assert item.unit == unit
    assert item.quantity_total == 3
    assert item.quantity_available == 3
    assert item.serviceability == "maintenance"
    # Stock on hand is explained by a receipt, so the ledger reconciles.
    ledger = InventoryTransaction.objects.get(item=item)
    assert ledger.kind == "receipt"
    assert ledger.quantity == 3
    assert ledger.actor_id == logistics.id


def test_import_defaults_a_blank_unit_to_the_officers_own_unit(
    as_user, logistics, materiel_category, unit
):
    content = workbook_bytes([row(category=label(materiel_category), name="Field Rations")])

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 201, response.content
    assert InventoryItem.objects.get(name="Field Rations").unit == unit


def test_import_refuses_a_unit_outside_the_officers_scope(
    as_user, logistics, materiel_category, other_unit
):
    content = workbook_bytes(
        [row(category=label(materiel_category), name="Rations", unit=other_unit.name)]
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 400
    assert "unit" in str(response.json()).lower()
    assert not InventoryItem.objects.exists()


def test_an_unknown_category_imports_nothing(as_user, logistics):
    content = workbook_bytes([row(category="MAT - No Such Category", name="Field Bed")])

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 400
    assert "row 2" in str(response.json())
    assert not InventoryItem.objects.exists()


def test_a_category_from_another_class_is_reported(as_user, logistics, materiel_category):
    content = workbook_bytes(
        [
            row(
                kind="Firearms",
                category=label(materiel_category),
                name="Field Bed",
            )
        ]
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 400
    assert "Asset Class" in str(response.json())
    assert not InventoryItem.objects.exists()


def test_a_duplicate_asset_tag_is_reported_not_overwritten(
    as_user, logistics, materiel_category, unit
):
    InventoryItem.objects.create(
        category=materiel_category,
        name="Existing Bed",
        asset_tag="MAT-0001",
        unit=unit,
        quantity_total=4,
        quantity_available=2,
    )
    content = workbook_bytes(
        [row(category=label(materiel_category), name="New Bed", asset_tag="MAT-0001")]
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 400
    assert "already exists" in str(response.json())
    existing = InventoryItem.objects.get(asset_tag="MAT-0001")
    assert existing.name == "Existing Bed"
    assert existing.quantity_available == 2


def test_a_negative_quantity_is_refused(as_user, logistics, materiel_category):
    content = workbook_bytes(
        [row(category=label(materiel_category), name="Field Bed", quantity_total=-2)]
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 400
    assert not InventoryItem.objects.exists()


def test_a_misspelled_condition_is_refused(as_user, logistics, materiel_category):
    content = workbook_bytes(
        [
            row(
                category=label(materiel_category),
                name="Field Bed",
                serviceability="Servicable",
            )
        ]
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 400
    assert "condition" in str(response.json()).lower()
    assert not InventoryItem.objects.exists()


def test_a_controlled_category_marks_the_imported_item_controlled(
    as_user, logistics, unit
):
    ammunition = InventoryCategory.objects.get(code="AMMO")
    content = workbook_bytes(
        [
            row(
                category=label(ammunition),
                name="5.56mm Ball",
                asset_tag="AMM-0009",
                quantity_total=1200,
                unit_of_measure="round",
            )
        ]
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 201, response.content
    assert InventoryItem.objects.get(asset_tag="AMM-0009").is_sensitive is True


def test_a_workbook_missing_required_columns_is_refused(as_user, logistics):
    content = workbook_bytes(
        [["MAT - Materiel", "Field Bed"]],
        headers=["Category", "Item Name"],
    )

    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(content)}, format="multipart"
    )

    assert response.status_code == 400
    assert "missing required columns" in str(response.json()).lower()


def test_only_xlsx_workbooks_are_accepted(as_user, logistics, materiel_category):
    response = as_user(logistics).post(
        "/api/inventory/import/",
        {"file": upload(b"not a workbook", name="inventory.csv")},
        format="multipart",
    )

    assert response.status_code == 400
    assert not InventoryItem.objects.exists()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_writes_the_register_rows(as_user, logistics, materiel_category, unit):
    InventoryItem.objects.create(
        category=materiel_category,
        name="Field Rations",
        asset_tag="MAT-0002",
        unit=unit,
        quantity_total=40,
        quantity_available=40,
    )

    response = as_user(logistics).get("/api/inventory/export/")

    assert response.status_code == 200
    assert response["Content-Type"] == XLSX
    sheet = load_workbook(BytesIO(response.content))[inventory_excel.SHEET_TITLE]
    assert [cell.value for cell in sheet[1]] == HEADERS
    assert sheet.cell(row=2, column=3).value == "Field Rations"
    assert sheet.cell(row=2, column=7).value == 40


def test_export_follows_the_asset_class_filter(as_user, logistics, materiel_category, age_category):
    InventoryItem.objects.create(
        category=materiel_category, name="Field Rations", quantity_total=1, quantity_available=1
    )
    InventoryItem.objects.create(
        category=age_category, name="Ground Power Unit", quantity_total=1, quantity_available=1
    )

    response = as_user(logistics).get("/api/inventory/export/?kind=age")

    sheet = load_workbook(BytesIO(response.content))[inventory_excel.SHEET_TITLE]
    names = [sheet.cell(row=index, column=3).value for index in range(2, sheet.max_row + 1)]
    assert names == ["Ground Power Unit"]


def test_the_export_carries_the_pol_batch_dates(as_user, logistics, unit, fuel_category):
    import datetime as dt

    InventoryItem.objects.create(
        category=fuel_category,
        name="Petron Corporation",
        unit=unit,
        quantity_total=5000,
        quantity_available=5000,
        unit_of_measure="litre",
        fuel_grade="jet_a1",
        fuel_form="bulk",
        date_issued=dt.date(2026, 11, 1),
        testing_date=dt.date(2026, 11, 5),
        expiry_date=dt.date(2027, 11, 1),
    )
    response = as_user(logistics).get("/api/inventory/export/?kind=fuel")
    sheet = load_workbook(BytesIO(response.content))[inventory_excel.SHEET_TITLE]
    headers = {cell.value: index for index, cell in enumerate(sheet[1], start=1)}

    assert sheet.cell(row=2, column=headers["Date Issued"]).value == "2026-11-01"
    assert sheet.cell(row=2, column=headers["Testing Date"]).value == "2026-11-05"
    assert sheet.cell(row=2, column=headers["Expiry Date"]).value == "2027-11-01"


def test_the_template_offers_the_jet_a1_fuel_pickers(as_user, logistics):
    workbook = load_workbook(BytesIO(as_user(logistics).get("/api/inventory/template/").content))
    sheet = workbook[inventory_excel.SHEET_TITLE]
    validations = {
        str(rule.sqref).split(" ")[0][0]: rule
        for rule in sheet.data_validations.dataValidation
    }
    # Fuel Grade and Fuel Form are dropdowns too, so an encoder picks them.
    for column in ("M", "N"):
        assert column in validations, f"column {column} has no picker"
        assert validations[column].type == "list"
        assert validations[column].formula1.startswith("Lists!")

    lists = workbook["Lists"]
    titles = {
        cell.value: index for index, cell in enumerate(lists[1], start=1) if cell.value
    }
    grades = {
        lists.cell(row=row, column=titles["Fuel Grade"]).value
        for row in range(2, lists.max_row + 1)
        if lists.cell(row=row, column=titles["Fuel Grade"]).value
    }
    forms = {
        lists.cell(row=row, column=titles["Fuel Form"]).value
        for row in range(2, lists.max_row + 1)
        if lists.cell(row=row, column=titles["Fuel Form"]).value
    }
    assert grades == {"AVGas", "Jet A1", "Diesel", "Gasoline"}
    assert forms == {"Drums", "Bulk tank (litres)"}


def test_an_import_files_the_pol_batch_dates(as_user, logistics, fuel_category):
    payload = workbook_bytes(
        [
            row(
                category=label(fuel_category),
                name="Petron Corporation",
                quantity_total=5000,
                unit_of_measure="litre",
                fuel_grade="Jet A1",
                fuel_form="Bulk tank (litres)",
                date_issued="2026-11-01",
                testing_date="2026-11-05",
                expiry_date="2027-11-01",
            )
        ]
    )
    response = as_user(logistics).post(
        "/api/inventory/import/?kind=fuel",
        {"file": upload(payload)},
        format="multipart",
    )

    assert response.status_code == 201, response.content
    item = InventoryItem.objects.get(name="Petron Corporation")
    assert item.date_issued.isoformat() == "2026-11-01"
    assert item.testing_date.isoformat() == "2026-11-05"
    assert item.expiry_date.isoformat() == "2027-11-01"


def test_a_template_downloaded_from_a_tab_offers_only_that_section(as_user, logistics):
    """A POL template lists POL categories, so the workbook matches the tab."""
    response = as_user(logistics).get("/api/inventory/template/?kind=fuel")
    workbook = load_workbook(BytesIO(response.content))
    rows = list(workbook["Lists"].iter_rows(min_row=2, values_only=True))
    categories = {row[1] for row in rows if row[1]}
    classes = {row[0] for row in rows if row[0]}

    assert categories and all(value.startswith("POL-") for value in categories)
    assert classes == {"Fuel & Lubricants"}


def test_an_import_from_a_tab_refuses_rows_from_another_section(
    as_user, logistics, materiel_category
):
    """Uploading from POL cannot file a materiel row."""
    payload = workbook_bytes(
        [row(category=label(materiel_category), name="Field Rations")]
    )
    response = as_user(logistics).post(
        "/api/inventory/import/?kind=fuel",
        {"file": upload(payload)},
        format="multipart",
    )

    assert response.status_code == 400
    # The error envelope nests the per-row errors once more under "errors".
    rows = response.json()["errors"]["errors"]
    messages = [message for values in rows.values() for message in values]
    assert any("another section" in message for message in messages)
    assert not InventoryItem.objects.filter(name="Field Rations").exists()


def test_jet_a1_round_trips_through_export_and_import(as_user, logistics, unit):
    """The workbook carries grade, form and litres per drum back in."""
    fuel_category = InventoryCategory.objects.get(code="POL-01")
    InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 drums",
        unit=unit,
        storage_location="POL Bay 1",
        quantity_total=12,
        quantity_available=12,
        unit_of_measure="drum",
        fuel_grade="jet_a1",
        fuel_form="drum",
        drum_capacity_litres=200,
    )
    InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 bulk tank",
        unit=unit,
        quantity_total=15000,
        quantity_available=15000,
        unit_of_measure="litre",
        fuel_grade="jet_a1",
        fuel_form="bulk",
    )
    client = as_user(logistics)
    exported = client.get("/api/inventory/export/").content
    sheet = load_workbook(BytesIO(exported))[inventory_excel.SHEET_TITLE]
    headers = [cell.value for cell in sheet[1]]
    assert headers == HEADERS
    grade = headers.index("Fuel Grade") + 1
    form = headers.index("Fuel Form") + 1
    capacity = headers.index("Litres per Drum") + 1
    rows = {
        sheet.cell(row=row, column=3).value: row
        for row in range(2, sheet.max_row + 1)
    }

    drums_row = rows["Jet A1 drums"]
    assert sheet.cell(row=drums_row, column=grade).value == "Jet A1"
    assert sheet.cell(row=drums_row, column=form).value == "Drums"
    assert sheet.cell(row=drums_row, column=capacity).value == 200
    bulk_row = rows["Jet A1 bulk tank"]
    assert sheet.cell(row=bulk_row, column=grade).value == "Jet A1"
    assert sheet.cell(row=bulk_row, column=form).value == "Bulk tank (litres)"
    assert sheet.cell(row=bulk_row, column=capacity).value in (None, "")

    # Clear the register and feed the export straight back in.
    InventoryItem.objects.all().delete()
    response = client.post(
        "/api/inventory/import/", {"file": upload(exported)}, format="multipart"
    )
    assert response.status_code == 201, response.content

    imported = {item.name: item for item in InventoryItem.objects.all()}
    drums = imported["Jet A1 drums"]
    assert drums.fuel_grade == "jet_a1"
    assert drums.fuel_form == "drum"
    assert drums.drum_capacity_litres == 200
    assert drums.litres(drums.quantity_available) == 12 * 200
    bulk = imported["Jet A1 bulk tank"]
    assert bulk.fuel_form == "bulk"
    assert bulk.drum_capacity_litres is None
    assert bulk.litres(bulk.quantity_available) == 15000


def test_an_imported_fuel_row_is_validated_like_the_api(
    as_user, logistics, fuel_category, unit
):
    client = as_user(logistics)

    def send(**overrides):
        body = row(category=label(fuel_category), name="Jet A1 drums", **overrides)
        return client.post(
            "/api/inventory/import/",
            {"file": upload(workbook_bytes([body]))},
            format="multipart",
        )

    no_form = send(fuel_grade="Jet A1")
    assert no_form.status_code == 400
    assert "Fuel Form" in str(no_form.json())

    no_capacity = send(fuel_grade="Jet A1", fuel_form="Drums")
    assert no_capacity.status_code == 400
    assert "Litres per Drum" in str(no_capacity.json())

    bulk_with_capacity = send(
        fuel_grade="Jet A1", fuel_form="Bulk tank (litres)", drum_capacity_litres=200
    )
    assert bulk_with_capacity.status_code == 400
    assert "Litres per Drum" in str(bulk_with_capacity.json())

    unknown_grade = send(fuel_grade="Kerosene", fuel_form="Drums", drum_capacity_litres=200)
    assert unknown_grade.status_code == 400
    assert "Fuel Grade" in str(unknown_grade.json())

    # Nothing above was written.
    assert not InventoryItem.objects.exists()

    created = send(
        fuel_grade="Jet A1",
        fuel_form="Drums",
        drum_capacity_litres=200,
        unit_of_measure="drum",
    )
    assert created.status_code == 201, created.content
    item = InventoryItem.objects.get(name="Jet A1 drums")
    assert item.fuel_grade == "jet_a1"
    assert item.fuel_form == "drum"
    assert item.drum_capacity_litres == 200
    assert item.litres(item.quantity_total) == 200
    # A drum row with no location lands at the unit's POL dump, like the API.
    assert item.storage_location == f"{unit.acronym} POL Dump"


def test_export_and_import_round_trip(as_user, logistics, materiel_category, unit):
    InventoryItem.objects.create(
        category=materiel_category,
        name="Field Bed",
        asset_tag="MAT-0100",
        unit=unit,
        quantity_total=6,
        quantity_available=6,
        storage_location="Warehouse A",
    )
    exported = as_user(logistics).get("/api/inventory/export/").content
    sheet = load_workbook(BytesIO(exported))[inventory_excel.SHEET_TITLE]

    # Feed the exported workbook straight back in: the values the export writes
    # are the ones the import accepts. The asset tag now exists, so the row is
    # reported rather than duplicated - which is the safety property we want.
    reimport = workbook_bytes(
        [
            [
                sheet.cell(row=2, column=column).value
                for column in range(1, len(inventory_excel.COLUMNS) + 1)
            ]
        ]
    )
    response = as_user(logistics).post(
        "/api/inventory/import/", {"file": upload(reimport)}, format="multipart"
    )

    assert response.status_code == 400
    assert "already exists" in str(response.json())
    assert InventoryItem.objects.count() == 1


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def test_statistics_describe_the_visible_register(
    as_user, logistics, materiel_category, age_category, unit
):
    InventoryItem.objects.create(
        category=materiel_category,
        name="Field Rations",
        unit=unit,
        quantity_total=100,
        quantity_available=40,
    )
    InventoryItem.objects.create(
        category=age_category,
        name="Ground Power Unit",
        unit=unit,
        quantity_total=2,
        quantity_available=2,
    )
    InventoryItem.objects.create(
        category=age_category,
        name="Air Compressor",
        unit=unit,
        quantity_total=2,
        quantity_available=0,
        serviceability="unserviceable",
    )

    body = as_user(logistics).get("/api/inventory/stats/").json()

    assert body["items"] == 3
    assert body["quantity_total"] == 104
    assert body["quantity_available"] == 42
    assert body["out_of_stock"] == 1
    assert body["unserviceable"] == 1
    assert body["low_stock"] == 2
    assert body["can_manage"] is True
    classes = {entry["kind"]: entry for entry in body["by_class"]}
    assert classes["age"]["items"] == 2
    assert classes["materiel"]["quantity_available"] == 40
    # Every class is listed, including the ones the unit holds nothing of.
    assert "firearms" in classes
    assert classes["firearms"]["items"] == 0


def test_statistics_hide_controlled_items_from_a_creator(as_user, creator):
    InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="AMMO"),
        name="5.56mm Ball",
        quantity_total=500,
        quantity_available=500,
        is_sensitive=True,
    )

    body = as_user(creator).get("/api/inventory/stats/").json()

    assert body["items"] == 0
    assert body["controlled"] == 0
    assert body["can_view_sensitive"] is False


def test_statistics_are_scoped_to_the_callers_units(as_user, logistics, other_unit):
    InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="MAT"),
        name="Elsewhere Rations",
        unit=other_unit,
        quantity_total=9,
        quantity_available=9,
    )

    body = as_user(logistics).get("/api/inventory/stats/").json()

    assert body["items"] == 0


def test_statistics_require_authentication(api):
    assert api.get("/api/inventory/stats/").status_code in (401, 403)
