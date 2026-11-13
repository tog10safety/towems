"""Hazard library: who may read it, who may maintain it, and the workbook round-trip.

The rules under test come straight from the unit procedure: the Unit Safety
Officer (and the Administrator) maintain the registry, while command, operations
posts and Evaluators read it. No other account can see it at all. A hazard an
assessment still references cannot be deleted, because that would break the
record the assessment and its PDF were built from.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from apps.core import timeutils
from apps.core.factories import make_user
from apps.legacy.models import HazardRegistry, MissionHazards
from apps.ops import hazards, umrr_pdf

pytestmark = pytest.mark.django_db

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def make_hazard(unit, creator, **overrides):
    data = {
        "unit": unit.name,
        "unit_id": unit,
        "activity_code": "A1",
        "hazard_description": "Aircraft ground collision",
        "before_likelihood": 4,
        "before_severity": "B",
        "before_risk_rating": "4B",
        "mitigations": "Enforce marshaller procedures",
        "after_likelihood": 2,
        "after_severity": "D",
        "after_risk_rating": "2D",
        "date_updated": timeutils.now(),
        "created_by": creator,
    }
    data.update(overrides)
    return HazardRegistry.objects.create(**data)


def workbook_bytes(rows, *, headers=None):
    from apps.ops.hazards import COLUMNS

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hazard Registry"
    sheet.append(headers or [header for _field, header in COLUMNS])
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Who may read and write
# ---------------------------------------------------------------------------


def test_the_unit_safety_officer_maintains_the_registry(as_user, safety, unit):
    response = as_user(safety).post(
        "/api/hazards/",
        {
            "activity_code": "AIR-01",
            "hazard_description": "Foreign object debris on the runway",
            "before_likelihood": 4,
            "before_severity": "C",
            "mitigations": "Conduct FOD walks each morning",
            "after_likelihood": 2,
            "after_severity": "D",
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    # The rating is calculated, never typed: likelihood then severity.
    assert body["before_rating"] == "4C"
    assert body["after_rating"] == "2D"
    assert body["unit_id"] == unit.id
    row = HazardRegistry.objects.get(pk=body["id"])
    assert row.unit == unit.name
    assert row.unit_id_id == unit.id
    assert row.created_by_id == safety.id


def test_command_posts_read_but_cannot_change_the_registry(
    as_user, commander, safety, unit, creator
):
    hazard = make_hazard(unit, creator)

    listing = as_user(commander).get("/api/hazards/")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["results"]] == [hazard.id]
    assert listing.json()["can_manage"] is False

    refused = as_user(commander).post(
        "/api/hazards/",
        {
            "activity_code": "X",
            "hazard_description": "X",
            "before_likelihood": 1,
            "before_severity": "A",
            "mitigations": "X",
            "after_likelihood": 1,
            "after_severity": "E",
        },
        format="json",
    )
    assert refused.status_code == 403
    assert not HazardRegistry.objects.exclude(pk=hazard.id).exists()


def test_the_operations_officer_reads_and_the_safety_officer_writes(
    as_user, ops_officer, safety, unit, creator
):
    make_hazard(unit, creator)

    assert as_user(ops_officer).get("/api/hazards/").json()["can_manage"] is False
    assert as_user(safety).get("/api/hazards/").json()["can_manage"] is True


def test_the_unit_mission_registry_prints_all_or_selected_hazards(
    as_user, safety, creator, unit
):
    first = make_hazard(unit, creator)
    make_hazard(unit, creator, activity_code="B2")

    # Every hazard the caller may read, with only the assessed risk in the table.
    every = as_user(safety).get("/api/hazards/umrr/")
    assert every.status_code == 200
    assert every["Content-Type"] == "application/pdf"
    assert every.content.startswith(b"%PDF")

    # A specific selection prints just that hazard.
    selected = as_user(safety).get(f"/api/hazards/umrr/?ids={first.id}")
    assert selected.status_code == 200
    assert selected.content.startswith(b"%PDF")

    assert as_user(safety).get("/api/hazards/umrr/?ids=999999").status_code == 403


def test_each_activity_code_is_printed_as_a_complete_form(
    as_user, safety, creator, unit, other_unit
):
    """Selecting a code prints all of it, never a single row out of context."""
    make_hazard(unit, creator, activity_code="A1")
    make_hazard(
        unit, creator, activity_code="A1", hazard_description="Foreign object debris"
    )
    make_hazard(
        unit, creator, activity_code="B2", hazard_description="Unsecured cargo"
    )

    one_code = as_user(safety).get("/api/hazards/umrr/?activities=A1")
    assert one_code.status_code == 200
    assert one_code.content.startswith(b"%PDF")

    # All hazards stay in one file, separated by activity code.
    everything = as_user(safety).get("/api/hazards/umrr/")
    assert everything.status_code == 200
    assert everything.content.startswith(b"%PDF")

    assert as_user(safety).get("/api/hazards/umrr/?activities=ZZ9").status_code == 403

    # A code that only exists in another unit cannot be requested by name.
    make_hazard(other_unit, creator, activity_code="C3")
    assert as_user(safety).get("/api/hazards/umrr/?activities=C3").status_code == 403


def test_selecting_an_activity_code_prints_only_that_code(
    as_user, safety, creator, unit, monkeypatch
):
    """The chosen code is the only one rendered, never the whole library."""
    make_hazard(unit, creator, activity_code="A1")
    make_hazard(unit, creator, activity_code="B2")

    captured = {}
    real_generate = umrr_pdf.generate_umrr_pdf

    def spy(rows, **kwargs):
        captured["codes"] = [row.activity_code for row in rows]
        return real_generate(rows, **kwargs)

    monkeypatch.setattr(umrr_pdf, "generate_umrr_pdf", spy)

    response = as_user(safety).get("/api/hazards/umrr/?activities=A1")

    assert response.status_code == 200
    assert captured["codes"] == ["A1"]


def test_the_registry_qr_carries_date_time_code_and_unit(unit):
    stamp = timeutils.now().replace(
        year=2026, month=11, day=9, hour=14, minute=30, second=0, microsecond=0
    )
    label = "".join((unit.acronym or unit.name).split())

    value = umrr_pdf.qr_value(unit=unit, code="A1", generated_at=stamp)

    assert value == f"202611091430[A1][{label}]"


def test_the_registry_refuses_hazards_outside_the_callers_scope(
    as_user, safety, creator, other_unit
):
    theirs = make_hazard(other_unit, creator)
    response = as_user(safety).get(f"/api/hazards/umrr/?ids={theirs.id}")
    assert response.status_code == 403


def test_a_creator_reads_only_their_own_units_hazards(
    as_user, creator, unit, other_unit
):
    """A Creator reads the library, but never a subordinate's rows."""
    mine = make_hazard(unit, creator)
    theirs = make_hazard(other_unit, creator, activity_code="B2")

    listing = as_user(creator).get("/api/hazards/")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["results"]] == [mine.id]
    assert listing.json()["can_manage"] is False
    assert as_user(creator).get(f"/api/hazards/{theirs.id}/").status_code == 403


def test_an_evaluator_reads_only_their_own_units_hazards(
    as_user, evaluator, creator, unit, other_unit
):
    """The Evaluator gets the library as reference, never the maintenance tools."""
    mine = make_hazard(unit, creator)
    theirs = make_hazard(other_unit, creator, activity_code="B2")

    listing = as_user(evaluator).get("/api/hazards/")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["results"]] == [mine.id]
    assert listing.json()["can_manage"] is False
    assert as_user(evaluator).get(f"/api/hazards/{theirs.id}/").status_code == 403

    refused = as_user(evaluator).post(
        "/api/hazards/",
        {
            "activity_code": "X",
            "hazard_description": "X",
            "before_likelihood": 1,
            "before_severity": "A",
            "mitigations": "X",
            "after_likelihood": 1,
            "after_severity": "E",
        },
        format="json",
    )
    assert refused.status_code == 403
    assert not HazardRegistry.objects.filter(activity_code="X").exists()


def test_the_registry_is_scoped_to_the_callers_units(
    as_user, safety, unit, other_unit, creator
):
    mine = make_hazard(unit, creator)
    theirs = make_hazard(other_unit, creator, activity_code="B2")

    listing = as_user(safety).get("/api/hazards/")
    ids = [row["id"] for row in listing.json()["results"]]

    assert mine.id in ids
    assert theirs.id not in ids
    assert as_user(safety).get(f"/api/hazards/{theirs.id}/").status_code == 403


def test_the_administrator_maintains_any_units_registry(
    as_user, admin, other_unit
):
    response = as_user(admin).post(
        "/api/hazards/",
        {
            "unit": other_unit.id,
            "activity_code": "ADM-1",
            "hazard_description": "Administrator-entered hazard",
            "before_likelihood": 3,
            "before_severity": "C",
            "mitigations": "Control it",
            "after_likelihood": 2,
            "after_severity": "E",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["unit_id"] == other_unit.id


def test_the_safety_officer_cannot_write_another_units_registry(
    as_user, safety, other_unit
):
    response = as_user(safety).post(
        "/api/hazards/",
        {
            "unit": other_unit.id,
            "activity_code": "X",
            "hazard_description": "X",
            "before_likelihood": 1,
            "before_severity": "A",
            "mitigations": "X",
            "after_likelihood": 1,
            "after_severity": "E",
        },
        format="json",
    )
    assert response.status_code == 400
    assert not HazardRegistry.objects.exists()


# ---------------------------------------------------------------------------
# Editing and deleting
# ---------------------------------------------------------------------------


def test_editing_recalculates_the_rating(as_user, safety, unit, creator):
    hazard = make_hazard(unit, creator)

    response = as_user(safety).patch(
        f"/api/hazards/{hazard.id}/",
        {"before_likelihood": 5, "before_severity": "A", "after_severity": "C"},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["before_rating"] == "5A"
    assert body["after_rating"] == "2C"
    hazard.refresh_from_db()
    assert hazard.before_risk_rating == "5A"
    assert hazard.after_risk_rating == "2C"


def test_a_hazard_an_assessment_uses_cannot_be_deleted(
    as_user, safety, unit, creator, mrm_form
):
    hazard = make_hazard(unit, creator)
    MissionHazards.objects.create(mrm=mrm_form, hazard=hazard, selected=True)

    response = as_user(safety).delete(f"/api/hazards/{hazard.id}/")

    assert response.status_code == 400
    assert "used by 1 MRM assessment" in str(response.json())
    assert HazardRegistry.objects.filter(pk=hazard.id).exists()


def test_the_seat_holder_may_delete_a_hazard_an_assessment_uses(
    as_user, seat_admin, unit, creator, mrm_form
):
    """The Super Administrator's erase is not blocked by the library rule."""
    hazard = make_hazard(unit, creator)
    MissionHazards.objects.create(mrm=mrm_form, hazard=hazard, selected=True)

    response = as_user(seat_admin).delete(f"/api/hazards/{hazard.id}/")

    assert response.status_code == 200, response.content
    assert not HazardRegistry.objects.filter(pk=hazard.id).exists()
    assert not MissionHazards.objects.filter(hazard_id=hazard.id).exists()


def test_an_unused_hazard_can_be_deleted(as_user, safety, unit, creator):
    hazard = make_hazard(unit, creator)

    response = as_user(safety).delete(f"/api/hazards/{hazard.id}/")

    assert response.status_code == 200, response.content
    assert not HazardRegistry.objects.filter(pk=hazard.id).exists()


def test_a_command_post_cannot_delete_a_hazard(as_user, commander, unit, creator):
    hazard = make_hazard(unit, creator)

    response = as_user(commander).delete(f"/api/hazards/{hazard.id}/")

    assert response.status_code == 403
    assert HazardRegistry.objects.filter(pk=hazard.id).exists()


# ---------------------------------------------------------------------------
# Workbook: template, import, export
# ---------------------------------------------------------------------------


def test_the_template_downloads_with_the_expected_columns(as_user, safety):
    response = as_user(safety).get("/api/hazards/template/")

    assert response.status_code == 200
    assert response["Content-Type"] == XLSX
    sheet = load_workbook(BytesIO(response.content))["Hazard Registry"]
    headers = [cell.value for cell in sheet[1]]
    assert headers == [header for _field, header in hazards.COLUMNS]


def test_the_template_unit_column_is_a_dropdown_of_real_units(
    as_user, safety, admin, unit, other_unit
):
    """The encoder picks a unit; a name TOWEMS does not record cannot be typed in."""
    response = as_user(safety).get("/api/hazards/template/")
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook["Hazard Registry"]

    validations = [
        rule
        for rule in sheet.data_validations.dataValidation
        if "A2" in str(rule.sqref)
    ]
    assert validations, "the Unit column has no validation"
    assert validations[0].type == "list"
    # The choices are a range on their own sheet, not free text in the column.
    assert validations[0].formula1.startswith("Units!")
    assert workbook["Units"]["A2"].value == unit.name
    # The Safety Officer may only choose their own unit.
    names = [
        row[0]
        for row in workbook["Units"].iter_rows(min_row=2, values_only=True)
        if row[0]
    ]
    assert names == [unit.name]

    admin_workbook = load_workbook(
        BytesIO(as_user(admin).get("/api/hazards/template/").content)
    )
    admin_names = {
        row[0]
        for row in admin_workbook["Units"].iter_rows(min_row=2, values_only=True)
        if row[0]
    }
    assert {unit.name, other_unit.name} <= admin_names


def test_import_still_refuses_a_unit_that_is_not_recorded(as_user, safety):
    content = workbook_bytes(
        [["No Such Wing", "AIR-08", "Hazard", 2, "C", "Control", 1, "E"]]
    )
    upload = SimpleUploadedFile("hazards.xlsx", content, content_type=XLSX)

    response = as_user(safety).post(
        "/api/hazards/import/", {"file": upload}, format="multipart"
    )

    assert response.status_code == 400
    assert "own unit" in str(response.json()).lower()
    assert not HazardRegistry.objects.exists()


def test_import_creates_every_valid_row(as_user, safety, unit):
    content = workbook_bytes(
        [
            [
                "",
                "AIR-02",
                "Bird strike on approach",
                3,
                "B",
                "Brief pilots; monitor bird activity",
                2,
                "D",
            ],
            ["", "AIR-03", "Runway incursion", 5, "A", "Sterile runway checks", 3, "C"],
        ]
    )
    upload = SimpleUploadedFile("hazards.xlsx", content, content_type=XLSX)

    response = as_user(safety).post(
        "/api/hazards/import/", {"file": upload}, format="multipart"
    )

    assert response.status_code == 201, response.content
    assert response.json()["created"] == 2
    rows = HazardRegistry.objects.filter(unit_id=unit.id).order_by("activity_code")
    assert [row.activity_code for row in rows] == ["AIR-02", "AIR-03"]
    assert rows[0].before_risk_rating == "3B"
    assert rows[1].after_risk_rating == "3C"
    assert rows[0].unit == unit.name


def test_an_invalid_row_imports_nothing(as_user, safety, unit):
    content = workbook_bytes(
        [
            ["", "AIR-04", "Valid hazard", 3, "B", "Control it", 2, "D"],
            ["", "AIR-05", "Invalid likelihood", 9, "B", "Control it", 2, "D"],
        ]
    )
    upload = SimpleUploadedFile("hazards.xlsx", content, content_type=XLSX)

    response = as_user(safety).post(
        "/api/hazards/import/", {"file": upload}, format="multipart"
    )

    assert response.status_code == 400
    body = response.json()
    assert "row 3" in str(body)
    assert not HazardRegistry.objects.filter(unit_id=unit.id).exists()


def test_a_workbook_missing_columns_is_refused(as_user, safety):
    content = workbook_bytes(
        [["AIR-06", "Hazard with too few columns"]],
        headers=["Activity Code", "Hazard Description"],
    )
    upload = SimpleUploadedFile("hazards.xlsx", content, content_type=XLSX)

    response = as_user(safety).post(
        "/api/hazards/import/", {"file": upload}, format="multipart"
    )

    assert response.status_code == 400
    assert "missing required columns" in str(response.json()).lower()


def test_only_a_maintainer_can_import(as_user, commander):
    content = workbook_bytes([["", "X", "X", 1, "A", "X", 1, "E"]])
    upload = SimpleUploadedFile("hazards.xlsx", content, content_type=XLSX)

    response = as_user(commander).post(
        "/api/hazards/import/", {"file": upload}, format="multipart"
    )

    assert response.status_code == 403


def test_export_contains_the_filtered_rows(as_user, safety, unit, other_unit, creator):
    make_hazard(unit, creator, activity_code="EXP-1")
    make_hazard(unit, creator, activity_code="EXP-2")
    make_hazard(other_unit, creator, activity_code="ELSEWHERE")

    response = as_user(safety).get("/api/hazards/export/?activity_code=EXP-1")

    assert response.status_code == 200
    assert response["Content-Type"] == XLSX
    sheet = load_workbook(BytesIO(response.content))["Hazard Registry"]
    codes = [sheet.cell(row=row, column=2).value for row in range(2, sheet.max_row + 1)]
    assert codes == ["EXP-1"]


def test_a_command_post_reads_but_does_not_export(as_user, commander, unit, creator):
    """Export is the maintainer's tool: it exists to be edited and re-imported."""
    hazard = make_hazard(unit, creator, activity_code="CMDR-1")

    assert as_user(commander).get("/api/hazards/").json()["results"][0]["id"] == hazard.id
    assert as_user(commander).get("/api/hazards/export/").status_code == 403


def test_the_administrator_exports_a_units_registry(as_user, admin, unit, creator):
    make_hazard(unit, creator, activity_code="ADM-EXP")

    response = as_user(admin).get("/api/hazards/export/?unit=" + str(unit.id))

    assert response.status_code == 200
    sheet = load_workbook(BytesIO(response.content))["Hazard Registry"]
    assert sheet.cell(row=2, column=2).value == "ADM-EXP"


def test_the_safety_officer_cannot_import_into_another_unit(
    as_user, safety, other_unit
):
    content = workbook_bytes(
        [[other_unit.name, "AIR-07", "Other unit hazard", 2, "C", "Control", 1, "E"]]
    )
    upload = SimpleUploadedFile("hazards.xlsx", content, content_type=XLSX)

    response = as_user(safety).post(
        "/api/hazards/import/", {"file": upload}, format="multipart"
    )

    assert response.status_code == 400
    assert not HazardRegistry.objects.exists()


def test_the_listing_carries_the_summary_and_activity_codes(
    as_user, safety, unit, creator
):
    make_hazard(unit, creator, activity_code="AIR-01")
    make_hazard(unit, creator, activity_code="AIR-01", hazard_description="Second")

    body = as_user(safety).get("/api/hazards/?page_size=1").json()

    assert body["count"] == 2
    assert body["summary"]["hazards"] == 2
    assert any(row["activity_code"] == "AIR-01" for row in body["activity_codes"])
    assert len(body["results"]) == 1


def test_an_unassigned_safety_officer_is_told_rather_than_guessed(as_user, unit):
    orphan = make_user(unit=None, authority="Unit Safety Officer")

    response = as_user(orphan).post(
        "/api/hazards/",
        {
            "activity_code": "X",
            "hazard_description": "X",
            "before_likelihood": 1,
            "before_severity": "A",
            "mitigations": "X",
            "after_likelihood": 1,
            "after_severity": "E",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "no unit" in str(response.json()).lower()
