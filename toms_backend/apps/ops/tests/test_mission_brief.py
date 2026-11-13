"""The consolidated Mission Brief covers the whole mission record."""

from __future__ import annotations

import datetime as dt

import pytest

from apps.ops import mission_brief_pdf
from apps.ops.models import Mission

pytestmark = pytest.mark.django_db


@pytest.fixture
def brief_mission(planner, unit):
    return Mission.objects.create(
        mission_number="TOWEMS-GO-BRIEF-001",
        category=Mission.CATEGORY_GROUND,
        title="Community outreach",
        purpose="Conduct a community outreach activity with the local government.",
        unit=unit,
        created_by=planner,
        status="planned",
        start_at=dt.datetime(2026, 11, 12, 8, 0),
        end_at=dt.datetime(2026, 11, 12, 12, 0),
        location_name="Butuan City",
        mgrs="51PYK7316589902",
        approving_authority=planner,
        approving_authority_role=Mission.AUTHORITY_DIRECTOR_OPERATIONS,
    )


@pytest.fixture
def air_mission(planner, unit):
    return Mission.objects.create(
        mission_number="TOWEMS-AIR-BRIEF-001",
        category=Mission.CATEGORY_AIR,
        title="Support sortie",
        purpose="Provide air support to ground units.",
        unit=unit,
        created_by=planner,
        status="planned",
        start_at=dt.datetime(2026, 11, 13, 9, 0),
        end_at=dt.datetime(2026, 11, 13, 11, 0),
        location_name="Bancasi",
    )


def test_brief_is_one_pdf_from_the_mission_record(
    as_user, planner, brief_mission
):
    response = as_user(planner).get(f"/api/missions/{brief_mission.id}/brief/")

    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 2_000
    assert (
        'inline; filename="MISSION_BRIEF_TOWEMS-GO-BRIEF-001.pdf"'
        in response["Content-Disposition"]
    )
    assert response["Cache-Control"].startswith("no-store")


def test_brief_can_be_downloaded(as_user, planner, brief_mission):
    response = as_user(planner).get(
        f"/api/missions/{brief_mission.id}/brief/?download=1"
    )

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")


def test_brief_also_covers_air_missions(as_user, planner, air_mission):
    response = as_user(planner).get(f"/api/missions/{air_mission.id}/brief/")

    assert response.status_code == 200, response.content
    assert response.content.startswith(b"%PDF")


def test_brief_generator_handles_a_mission_without_mrm_or_order(brief_mission):
    content = mission_brief_pdf.generate_mission_brief_pdf(brief_mission)

    assert content.startswith(b"%PDF")
    assert len(content) > 2_000
