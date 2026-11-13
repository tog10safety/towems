"""Excel personnel registration: template, validation and bulk creation."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from apps.accounts.excel import TEMPLATE_COLUMNS
from apps.accounts.models import User, UserProfile
from apps.core.factories import PASSWORD, make_unit, make_user, unique_serial

pytestmark = pytest.mark.django_db

HEADERS = [label for _field, label, _required in TEMPLATE_COLUMNS]


def workbook_bytes(rows: list[dict]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Personnel"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append([row.get(header, "") for header in HEADERS])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def row(unit, **overrides):
    data = {
        "Serial Number": unique_serial(),
        "First Name": "Juan",
        "Middle Name": "Santos",
        "Last Name": "Dela Cruz",
        "Rank": "CPT",
        "Designation": "Operations Officer",
        "Account Role": "Creator",
        "Category": "Officer",
        "Unit": f"{unit.acronym} — {unit.name}",
        "Branch of Service": "PAF",
        "Contact Number": "09171234567",
        "Email Address": "juan.delacruz@example.mil.ph",
        "Sex / Gender": "Male",
        "Date of Birth": dt.date(1990, 5, 24),
        "Blood Type": "O+",
        "Home Address": "Butuan City",
        "Emergency Contact Name": "Maria Dela Cruz",
        "Emergency Contact Number": "09179876543",
        "Date Enlisted": dt.date(2012, 6, 1),
        "Date Commissioned": dt.date(2014, 6, 1),
        "AFSC Primary": "11F3",
        "AFSC Secondary": "11G3",
        "AFSC Tertiary": "11A3",
        "Highest Education": "Bachelor's Degree",
        "Civil Status": "Married",
    }
    data.update(overrides)
    return data


def upload(api, rows, password=PASSWORD, name="personnel.xlsx", scope=None):
    api.get("/api/auth/csrf/")
    token = api.cookies[settings.CSRF_COOKIE_NAME].value
    data = {
        "file": SimpleUploadedFile(
            name,
            workbook_bytes(rows),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        ),
        "password": password,
    }
    if scope is not None:
        data["unit"] = str(scope)
    return api.post(
        "/api/personnel/import/",
        data,
        format="multipart",
        HTTP_X_CSRFTOKEN=token,
    )


def test_template_is_downloadable_with_live_dropdowns(as_user, admin, unit):
    response = as_user(admin).get("/api/personnel/import-template/")
    assert response.status_code == 200
    assert "spreadsheetml" in response["Content-Type"]
    assert "attachment" in response["Content-Disposition"]

    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["Personnel", "Reference", "Instructions"]
    assert workbook["Reference"].sheet_state == "hidden"
    validations = workbook["Personnel"].data_validations.dataValidation
    assert len(validations) >= 9

    reference_values = {
        cell.value
        for row_cells in workbook["Reference"].iter_rows()
        for cell in row_cells
        if cell.value
    }
    # Administrators are provisioned in TOWEMS, never through the bulk
    # workbook; the template deliberately omits that role.
    assert "Administrator" not in reference_values
    assert "Creator" in reference_values
    assert any(unit.name in str(value) for value in reference_values)


def test_import_creates_accounts_with_default_username_and_details(login, api, admin, unit):
    serial = unique_serial()
    login(admin)
    response = upload(api, [row(unit, **{"Serial Number": serial})])
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["created"] == 1
    assert body["errors"] == []

    user = User.objects.get(serial_number=serial)
    assert user.username == serial
    assert user.check_password(PASSWORD)
    assert user.force_password_change is True
    assert user.unit_id == unit.id

    profile = UserProfile.objects.get(user=user)
    assert profile.email == "juan.delacruz@example.mil.ph"
    assert profile.sex == "Male"
    assert profile.date_of_birth == dt.date(1990, 5, 24)
    assert profile.blood_type == "O+"
    assert profile.emergency_contact_name == "Maria Dela Cruz"
    assert profile.date_enlisted == dt.date(2012, 6, 1)
    assert profile.date_commissioned == dt.date(2014, 6, 1)
    assert profile.afsc_primary == "11F3"
    assert profile.highest_education == "Bachelor's Degree"
    assert profile.civil_status == "Married"
    assert profile.onboarding_required is True


def test_invalid_workbook_imports_nothing(login, api, admin, unit):
    first = row(unit)
    second = row(unit, **{"Serial Number": first["Serial Number"]})
    second["Unit"] = "Unknown Unit"
    login(admin)
    response = upload(api, [first, second])
    assert response.status_code == 400
    body = response.json()
    assert body["created"] == 0
    assert any(item["row"] == 3 for item in body["errors"])
    assert User.objects.filter(serial_number=first["Serial Number"]).exists() is False


def test_weak_initial_password_is_rejected_before_any_row(login, api, admin, unit):
    login(admin)
    response = upload(api, [row(unit)], password="weak")
    assert response.status_code == 400
    assert response.json()["created"] == 0
    assert any("password" in item["field"] for item in response.json()["errors"])


def test_creator_cannot_import(as_user, creator, unit):
    response = as_user(creator).post(
        "/api/personnel/import/",
        {
            "file": SimpleUploadedFile("personnel.xlsx", workbook_bytes([row(unit)])),
            "password": PASSWORD,
        },
        format="multipart",
    )
    assert response.status_code == 403


def reference_unit_values(response) -> list[str]:
    workbook = load_workbook(BytesIO(response.content))
    reference = workbook["Reference"]
    headers = [cell.value for cell in reference[1]]
    column = headers.index("Unit") + 1
    values = [
        reference.cell(row=index, column=column).value
        for index in range(2, reference.max_row + 1)
    ]
    return [value for value in values if value]


def test_template_for_a_unit_offers_only_that_unit_and_its_subordinates(
    as_user, admin, unit
):
    group = make_unit(name="TOG 10", acronym="TOG10")
    squadron = make_unit(name="5132 ABS", acronym="5132ABS", parent=group)
    outsider = make_unit(name="TOG 11", acronym="TOG11")

    response = as_user(admin).get(f"/api/personnel/import-template/?unit={group.id}")
    assert response.status_code == 200
    assert "TOG10" in response["Content-Disposition"]

    values = reference_unit_values(response)
    assert f"{group.acronym} \u2014 {group.name}" in values
    assert f"{squadron.acronym} \u2014 {squadron.name}" in values
    assert f"{outsider.acronym} \u2014 {outsider.name}" not in values
    assert f"{unit.acronym} \u2014 {unit.name}" not in values
    assert len(values) == 2


def test_import_accepts_a_row_for_a_subordinate_unit(login, api, admin, unit):
    group = make_unit(name="TOG 10", acronym="TOG10")
    squadron = make_unit(name="5132 ABS", acronym="5132ABS", parent=group)
    person = row(squadron)
    login(admin)

    response = upload(api, [person], scope=group.id)
    assert response.status_code == 201, response.content
    assert response.json()["created"] == 1
    assert User.objects.filter(serial_number=person["Serial Number"]).exists()


def test_import_rejects_a_unit_outside_the_selected_chain(login, api, admin, unit):
    group = make_unit(name="TOG 10", acronym="TOG10")
    squadron = make_unit(name="5132 ABS", acronym="5132ABS", parent=group)
    outsider = make_unit(name="TOG 11", acronym="TOG11")
    login(admin)

    allowed = row(squadron)
    denied = row(outsider)
    response = upload(api, [allowed, denied], scope=group.id)
    assert response.status_code == 400
    body = response.json()
    assert body["created"] == 0
    assert any(
        "outside this template's scope" in item["message"] for item in body["errors"]
    )
    # The valid row is not imported either: the batch is all or nothing.
    assert User.objects.filter(serial_number=allowed["Serial Number"]).exists() is False


def test_serial_number_from_another_unit_is_denied(login, api, admin, unit, other_unit):
    serial = unique_serial()
    make_user(unit=other_unit, serial_number=serial)
    login(admin)

    response = upload(api, [row(unit, **{"Serial Number": serial})])
    assert response.status_code == 400
    body = response.json()
    assert body["created"] == 0
    assert any("already registered" in item["message"] for item in body["errors"])
    assert User.objects.filter(serial_number__iexact=serial).count() == 1


def test_serial_number_is_compared_without_case(login, api, admin, unit, other_unit):
    serial = "AB-903125"
    make_user(unit=other_unit, serial_number=serial)
    login(admin)

    response = upload(api, [row(unit, **{"Serial Number": serial.lower()})])
    assert response.status_code == 400
    body = response.json()
    assert body["created"] == 0
    assert any("already registered" in item["message"] for item in body["errors"])


def test_manual_registration_with_an_existing_serial_is_rejected(
    as_user, admin, unit, other_unit
):
    serial = unique_serial()
    make_user(unit=other_unit, serial_number=serial)

    response = as_user(admin).post(
        "/api/personnel/",
        {
            "serial_number": serial,
            "first_name": "Duplicate",
            "last_name": "Serial",
            "authority": "Creator",
            "category": "Officer",
            "unit_id": unit.id,
            "branch_of_service": "PAF",
            "password": PASSWORD,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "serial_number" in response.json()["errors"]
    assert User.objects.filter(serial_number__iexact=serial).count() == 1
