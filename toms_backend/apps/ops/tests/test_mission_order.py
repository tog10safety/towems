"""The Mission Order PDF is built from the mission's own record."""

from __future__ import annotations

import datetime as dt
import types

import pytest
from rest_framework.test import APIClient

from apps.core.factories import make_user
from apps.ops import mission_order_pdf
from apps.ops.models import (
    Mission,
    MissionPersonnel,
    TripItinerary,
    Vehicle,
    VehicleTrip,
    VehicleTripVehicle,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def planned_mission(planner, unit):
    mission = Mission.objects.create(
        mission_number="TOWEMS-GO-MO-001",
        category=Mission.CATEGORY_GROUND,
        title="Ferry mission",
        purpose="Ferry equipment and personnel to the destination.",
        unit=unit,
        created_by=planner,
        status="planned",
        start_at=dt.datetime(2026, 11, 3, 8, 0),
        end_at=dt.datetime(2026, 11, 4, 17, 0),
        location_name="Davao City",
        approving_authority=planner,
        approving_authority_role=Mission.AUTHORITY_COMMANDER,
    )
    driver = make_user(unit=unit, authority="Creator", rank="Sgt")
    MissionPersonnel.objects.create(mission=mission, user=planner, role="Officer-In-Charge")
    MissionPersonnel.objects.create(mission=mission, user=driver, role="Driver")

    vehicle = Vehicle.objects.create(
        registration_number="SNA 2643",
        make_model="Hilux",
        vehicle_type="Pickup",
        unit=unit,
        status="available",
    )
    trip = VehicleTrip.objects.create(
        trip_number="TRIP-MO-001",
        title="Ferry movement",
        mission=mission,
        status="planned",
        departure_at=mission.start_at,
        return_at=mission.end_at,
        created_by=planner,
    )
    TripItinerary.objects.create(
        trip=trip,
        sequence=1,
        location_name="HTOG 10, Brgy. Bancasi, Butuan City",
        remarks="Origin",
    )
    TripItinerary.objects.create(
        trip=trip,
        sequence=2,
        location_name="HTOWEM, DAS, Davao City",
        remarks="Destination",
    )
    VehicleTripVehicle.objects.create(trip=trip, vehicle=vehicle, driver=driver)
    return mission


def test_mission_order_is_a_pdf_built_from_the_mission(as_user, planner, planned_mission):
    response = as_user(planner).get(
        f"/api/missions/{planned_mission.id}/mission-order/"
    )

    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 2_000
    assert (
        'inline; filename="MISSION_ORDER_TOWEMS-GO-MO-001.pdf"'
        in response["Content-Disposition"]
    )
    assert response["Cache-Control"].startswith("no-store")


def test_mission_order_can_be_downloaded(as_user, planner, planned_mission):
    response = as_user(planner).get(
        f"/api/missions/{planned_mission.id}/mission-order/?download=1"
    )

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")


def test_mission_order_verification_endpoint_is_public(planned_mission):
    token = mission_order_pdf.verification_token(planned_mission)
    response = APIClient().get(f"/api/mission-orders/verify/{token}/")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["generated_by"] == "Tactical Operations Wings Enterprise Management System"
    assert body["document"] == "Mission Order"
    assert body["mission_number"] == planned_mission.mission_number
    assert body["mission_order_number"]
    assert response["Cache-Control"] == "no-store"


def test_mission_order_verification_rejects_a_tampered_token():
    response = APIClient().get("/api/mission-orders/verify/not-a-token/")

    assert response.status_code == 400
    assert response.json()["valid"] is False


def test_signature_barcode_is_date_plus_numeric_serial(planned_mission):
    signer = planned_mission.approving_authority
    signer.serial_number = "O-903125"
    signer.save(update_fields=["serial_number"])
    planned_mission.mission_order_signed_at = dt.datetime(2026, 11, 7, 10, 15)
    planned_mission.save(update_fields=["mission_order_signed_at"])

    value = mission_order_pdf.signature_barcode_value(planned_mission, signer)

    assert value == "20261107903125"
    assert value.isdigit()


def test_a_commander_signs_in_their_own_right(planned_mission):
    """No "FOR THE ... COMMANDER" line, and the designation of the level."""
    mission = planned_mission
    signer = mission.approving_authority
    assert mission.unit.category == "Squadron"

    assert mission_order_pdf._signing_as_commander(mission, signer) is True
    assert (
        mission_order_pdf._signer_designation(mission, signer)
        == "Squadron Commander"
    )


@pytest.mark.parametrize(
    "category,expected",
    [
        ("Command", "Commander"),
        ("Wing", "Wing Commander"),
        ("Group", "Group Commander"),
        ("Squadron", "Squadron Commander"),
    ],
)
def test_commander_designation_follows_the_unit_level(category, expected):
    unit = types.SimpleNamespace(category=category)

    assert mission_order_pdf._commander_designation(unit) == expected


def test_a_commander_order_has_no_for_the_commander_line(planned_mission):
    """The rendered order: no "FOR THE ..." line, titled by the unit level."""
    fitz = pytest.importorskip("fitz")

    pdf = mission_order_pdf.generate_mission_order_pdf(planned_mission)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        text = " ".join(
            " ".join(page.get_text().split()) for page in document
        )

    assert "FOR THE" not in text
    assert "Squadron Commander" in text


def test_an_appointed_commander_signs_in_their_own_right(planned_mission):
    """The appointment is what makes somebody the commander, as in production."""
    from apps.command.models import UnitAppointment
    from apps.core.roles import ROLE_COMMANDER

    mission = planned_mission
    commander = make_user(unit=mission.unit, authority="Creator", rank="Col")
    UnitAppointment.objects.create(
        unit=mission.unit, user=commander, role=ROLE_COMMANDER
    )

    assert mission_order_pdf._signing_as_commander(mission, commander) is True
    assert (
        mission_order_pdf._signer_designation(mission, commander)
        == "Squadron Commander"
    )


def test_a_deputy_still_signs_for_the_commander(planned_mission):
    from apps.command.models import UnitAppointment
    from apps.core.roles import ROLE_DEPUTY_COMMANDER

    mission = planned_mission
    deputy = make_user(unit=mission.unit, authority="Creator", rank="Capt")
    UnitAppointment.objects.create(
        unit=mission.unit, user=deputy, role=ROLE_DEPUTY_COMMANDER
    )

    assert mission_order_pdf._signing_as_commander(mission, deputy) is False


def test_a_signed_order_keeps_the_post_its_signer_held(planned_mission):
    """Signing records the post; a later reassignment cannot rewrite it.

    This is the production case: a Group Commander signed an order and was
    afterwards moved to Personnel and another unit. The signed order must keep
    the commander's block, and no "FOR THE ..." line.
    """
    from django.utils import timezone

    from apps.command.models import UnitAppointment
    from apps.core.factories import make_unit
    from apps.core.roles import ROLE_COMMANDER
    from apps.ops import mission_order_workflow

    mission = planned_mission
    commander = make_user(unit=mission.unit, authority="Creator", rank="Lt Col")
    UnitAppointment.objects.create(
        unit=mission.unit, user=commander, role=ROLE_COMMANDER
    )
    Mission.objects.filter(pk=mission.pk).update(
        approval_status=Mission.APPROVAL_APPROVED,
        mission_order_status=Mission.MISSION_ORDER_AWAITING_SIGNATURE,
        mission_order_signatory=commander,
    )
    mission.refresh_from_db()

    mission_order_workflow.approve(mission, actor=commander, note="Signed.")
    mission.refresh_from_db()

    assert mission.mission_order_signer_designation == "Squadron Commander"
    assert mission.mission_order_signer_as_commander is True

    # The signer is moved to another unit and loses the appointment afterwards.
    UnitAppointment.objects.filter(unit=mission.unit, user=commander).update(
        is_active=False, ended_at=timezone.now()
    )
    commander.designation = "Personnel"
    commander.unit = make_unit()
    commander.save(update_fields=["designation", "unit"])
    mission.refresh_from_db()

    assert mission_order_pdf.signature_block(mission, commander) == (
        "Squadron Commander",
        True,
    )
    assert mission_order_workflow.state(mission)["signatory_designation"] == (
        "Squadron Commander"
    )

    fitz = pytest.importorskip("fitz")

    pdf = mission_order_pdf.generate_mission_order_pdf(mission)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)

    assert "FOR THE" not in text
    assert "Squadron Commander" in text


def test_resetting_the_order_clears_the_recorded_signature_block(planned_mission):
    from apps.ops import mission_order_workflow

    mission = planned_mission
    mission.mission_order_signer_designation = "Squadron Commander"
    mission.mission_order_signer_as_commander = True

    mission_order_workflow.reset(mission)

    assert mission.mission_order_signer_designation == ""
    assert mission.mission_order_signer_as_commander is False


def test_signature_designation_uses_mission_authority_role(planned_mission):
    signer = planned_mission.approving_authority
    signer.designation = "Personnel"
    signer.save(update_fields=["designation"])
    planned_mission.approving_authority_role = Mission.AUTHORITY_DIRECTOR_OPERATIONS
    planned_mission.save(update_fields=["approving_authority_role"])
    separate_signer = signer.__class__.objects.get(pk=signer.pk)

    assert separate_signer is not planned_mission.approving_authority
    assert (
        mission_order_pdf._signer_designation(planned_mission, separate_signer)
        == "Director for Operations"
    )
