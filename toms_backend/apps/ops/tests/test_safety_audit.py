"""Safety Officer MRM audit: role gate, unit scope, charts and ZIP export."""

from __future__ import annotations

import datetime as dt
import uuid
import zipfile
from io import BytesIO

import pytest

from apps.core.factories import make_unit, make_user
from apps.legacy.models import MRMForm

pytestmark = pytest.mark.django_db


def make_form(unit, user, *, approved_on=None, risk="Low", percent=25.0, **overrides):
    data = {
        "mrm_number": f"MRM-{uuid.uuid4().hex[:8]}",
        "activity_mission": f"Audit assessment for {unit.acronym}",
        "created_by": user,
        "safety_objectives": "Safe operations",
        "status": "reviewed" if approved_on else "draft",
        "review_status": "approved" if approved_on else "pending",
        "authority_approved": bool(approved_on),
        "date_approved": approved_on,
        "date_created": approved_on or dt.datetime(2026, 1, 1, 8, 0),
        "risk_category": risk if approved_on else None,
        "total_percent": percent if approved_on else None,
        "original_unit": unit,
        "original_unit_name": unit.name,
    }
    data.update(overrides)
    return MRMForm.objects.create(**data)


def test_audit_is_limited_to_safety_officers(as_user, creator, admin):
    assert as_user(creator).get("/api/risk-register/audit/").status_code == 403
    # The audit is a safety-office tool, not a general administrator report.
    assert as_user(admin).get("/api/risk-register/audit/").status_code == 403


def test_audit_requires_authentication(api):
    assert api.get("/api/risk-register/audit/").status_code in (401, 403)


def test_audit_covers_the_officers_unit_and_subordinates_only(
    as_user, safety, unit, other_unit
):
    child = make_unit(name="Child Squadron", acronym="CHILD", parent=unit)
    make_form(unit, safety, approved_on=dt.datetime(2026, 1, 5, 9, 0))
    make_form(child, safety, approved_on=dt.datetime(2026, 1, 6, 9, 0))
    make_form(other_unit, make_user(unit=other_unit, authority="Creator"), approved_on=dt.datetime(2026, 1, 7, 9, 0))

    body = as_user(safety).get(
        "/api/risk-register/audit/?date_from=2026-01-01&date_to=2026-01-31"
    ).json()

    assert body["totals"]["mrms"] == 2
    assert body["totals"]["completed"] == 2
    assert {row["unit_id"] for row in body["by_unit"]} == {unit.id, child.id}
    assert set(body["scope"]["unit_ids"]) == {unit.id, child.id}
    assert {row["acronym"] for row in body["scope"]["units"]} == {"ALPHA", "CHILD"}


def test_audit_unit_filter_narrows_to_a_subordinate(as_user, safety, unit):
    child = make_unit(name="Child Squadron", acronym="CHILD", parent=unit)
    own = make_form(unit, safety, approved_on=dt.datetime(2026, 1, 5, 9, 0))
    below = make_form(child, safety, approved_on=dt.datetime(2026, 1, 6, 9, 0))

    body = as_user(safety).get(
        f"/api/risk-register/audit/?date_from=2026-01-01&date_to=2026-01-31&unit={child.id}"
    ).json()

    assert body["totals"]["mrms"] == 1
    assert body["by_unit"][0]["unit_id"] == child.id
    assert below.id != own.id


def test_audit_month_buckets_count_completions_and_empty_periods(
    as_user, safety, unit
):
    make_form(unit, safety, approved_on=dt.datetime(2026, 1, 5, 9, 0))
    make_form(unit, safety, approved_on=dt.datetime(2026, 1, 20, 9, 0))
    make_form(unit, safety, approved_on=dt.datetime(2026, 3, 3, 9, 0))

    body = as_user(safety).get(
        "/api/risk-register/audit/?date_from=2026-01-01&date_to=2026-04-30&granularity=month"
    ).json()

    assert [row["key"] for row in body["buckets"]] == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
    ]
    assert [row["completed"] for row in body["buckets"]] == [2, 0, 1, 0]
    assert body["totals"]["completed"] == 3


def test_audit_quarter_and_year_buckets(as_user, safety, unit):
    make_form(unit, safety, approved_on=dt.datetime(2026, 2, 5, 9, 0))
    make_form(unit, safety, approved_on=dt.datetime(2026, 11, 5, 9, 0))

    quarter = as_user(safety).get(
        "/api/risk-register/audit/?granularity=quarter"
    ).json()
    assert [row["key"] for row in quarter["buckets"]] == ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
    assert [row["completed"] for row in quarter["buckets"]] == [1, 0, 0, 1]

    year = as_user(safety).get("/api/risk-register/audit/?granularity=year").json()
    assert [row["key"] for row in year["buckets"]] == ["2026"]


def test_audit_reports_risk_distribution_and_average(as_user, safety, unit):
    make_form(unit, safety, approved_on=dt.datetime(2026, 1, 5, 9, 0), risk="High", percent=90)
    make_form(unit, safety, approved_on=dt.datetime(2026, 1, 6, 9, 0), risk="Low", percent=20)
    make_form(unit, safety, approved_on=dt.datetime(2026, 1, 7, 9, 0), risk="Medium", percent=60)

    body = as_user(safety).get("/api/risk-register/audit/").json()

    assert body["totals"]["high"] == 1
    assert body["totals"]["medium"] == 1
    assert body["totals"]["low"] == 1
    assert body["totals"]["average_residual"] == 56.7
    assert {row["key"]: row["value"] for row in body["by_risk"]} == {
        "high": 1,
        "medium": 1,
        "low": 1,
        "unrated": 0,
    }


def test_invalid_granularity_is_rejected(as_user, safety):
    response = as_user(safety).get("/api/risk-register/audit/?granularity=fortnight")
    assert response.status_code == 400
    assert "granularity" in response.json()["errors"]


def test_zip_downloads_the_selected_pdfs(
    as_user, safety, unit, tmp_path, settings
):
    settings.LEGACY_PDF_DIR = tmp_path
    (tmp_path / "one.pdf").write_bytes(b"%PDF-1.4 one")
    (tmp_path / "two.pdf").write_bytes(b"%PDF-1.4 two")
    first = make_form(
        unit,
        safety,
        approved_on=dt.datetime(2026, 1, 5, 9, 0),
        approved_pdf_filename="one.pdf",
    )
    second = make_form(
        unit,
        safety,
        approved_on=dt.datetime(2026, 1, 6, 9, 0),
        approved_pdf_filename="two.pdf",
    )

    response = as_user(safety).get(
        f"/api/risk-register/audit-zip/?ids={first.id},{second.id}"
    )

    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "application/zip"
    assert "Safety_Audit_" in response["Content-Disposition"]
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            f"MRM_{first.mrm_number}.pdf",
            f"MRM_{second.mrm_number}.pdf",
        }
        assert archive.read(f"MRM_{first.mrm_number}.pdf") == b"%PDF-1.4 one"


def test_zip_refuses_a_foreign_assessment_id(as_user, safety, other_unit, tmp_path, settings):
    settings.LEGACY_PDF_DIR = tmp_path
    foreign = make_form(
        other_unit,
        make_user(unit=other_unit, authority="Creator"),
        approved_on=dt.datetime(2026, 1, 5, 9, 0),
    )

    response = as_user(safety).get(
        f"/api/risk-register/audit-zip/?ids={foreign.id}"
    )

    assert response.status_code == 400
    assert "No assessments" in response.json()["errors"]["detail"]


def test_zip_without_ids_uses_the_covered_period(as_user, safety, unit, tmp_path, settings):
    settings.LEGACY_PDF_DIR = tmp_path
    (tmp_path / "inside.pdf").write_bytes(b"%PDF-1.4 inside")
    inside = make_form(
        unit,
        safety,
        approved_on=dt.datetime(2026, 2, 5, 9, 0),
        approved_pdf_filename="inside.pdf",
    )
    make_form(
        unit,
        safety,
        approved_on=dt.datetime(2026, 6, 5, 9, 0),
        approved_pdf_filename="outside.pdf",
    )

    response = as_user(safety).get(
        "/api/risk-register/audit-zip/?date_from=2026-02-01&date_to=2026-02-28"
    )

    assert response.status_code == 200, response.content
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == [f"MRM_{inside.mrm_number}.pdf"]
