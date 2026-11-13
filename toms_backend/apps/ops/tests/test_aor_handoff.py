"""Handing a mission to the unit whose AOR it enters.

The Operations Center plots an operational area; if the point leaves its own
unit's area, the unit owning the destination AOR is told. Only that unit's
Operations Center is notified and monitors the movement. Reading the mission,
the Operations Center may forward its details to the unit's Commander, Deputy
Commander, Director for Operations and Operations Officer.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from apps.command.models import OfficeSection, SectionAssignment, UnitAppointment
from apps.core.factories import make_unit, make_user
from apps.ops import aor_handoff
from apps.ops.models import AreaOfResponsibility, Mission, Notification
from apps.ops.tests.test_missions import ground_payload

pytestmark = pytest.mark.django_db

COMMAND_ROLES = (
    "commander",
    "deputy_commander",
    "director_operations",
    "operations_officer",
)


def square(lon: float = 121.0, lat: float = 14.6, size: float = 0.4) -> dict:
    west, east = lon - size, lon + size
    south, north = lat - size, lat + size
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


def make_aor(unit, *, lon=121.0, lat=14.6, size=0.3, code="AOR-DEST"):
    return AreaOfResponsibility.objects.create(
        name=f"{unit.acronym} AOR",
        code=code,
        unit=unit,
        geometry=square(lon, lat, size),
        min_lon=lon - size,
        max_lon=lon + size,
        min_lat=lat - size,
        max_lat=lat + size,
        centroid_lon=lon,
        centroid_lat=lat,
    )


def destination_posts(unit):
    """One holder for each command post, plus the Operations Center office."""
    people = {}
    for role in COMMAND_ROLES:
        person = make_user(unit=unit, authority="Creator")
        UnitAppointment.objects.create(unit=unit, user=person, role=role)
        people[role] = person

    # The caller's own unit may already have its Operations Center fixture;
    # one unit has one such office, so reuse it rather than colliding.
    office = OfficeSection.objects.filter(
        unit=unit, function=OfficeSection.FUNCTION_OPERATIONS_CENTER
    ).first() or OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPS-C",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    member = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(
        user=member, section=office, assignment_type="primary"
    )
    people["operations_center"] = member
    return people, office


def notifications_for(mission, category=aor_handoff.HANDOFF_CATEGORY):
    return Notification.objects.filter(
        category=category,
        related_type="mission",
        related_id=str(mission.id),
    )


@override_settings(GEOCODER_PROVIDER="off")
def test_pin_reports_the_destination_operations_center(as_user, planner, unit):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    people, _office = destination_posts(destination)

    body = as_user(planner).get(
        f"/api/gis/pin/?lat=14.6&lon=121.0&unit={unit.id}"
    ).json()

    handoff = body["aor_handoff"]
    assert handoff["unit_acronym"] == "BRAVO"
    assert handoff["unit_id"] == destination.id
    assert handoff["aor_name"] == "BRAVO AOR"
    assert handoff["posts"] == ["Operations Center"]
    assert handoff["recipient_count"] == 1
    assert {row["id"] for row in handoff["recipients"]} == {
        people["operations_center"].id
    }


@override_settings(GEOCODER_PROVIDER="off")
def test_pin_reports_no_handoff_inside_the_units_own_aor(as_user, planner, unit):
    make_aor(unit, code="AOR-OWN")
    destination_posts(unit)

    body = as_user(planner).get(
        f"/api/gis/pin/?lat=14.6&lon=121.0&unit={unit.id}"
    ).json()

    assert body["aor_handoff"] is None
    # The owning AOR is still named in the administrative area.
    assert body["administrative_area"]["aor"]["unit_acronym"] == unit.acronym


def create_outside_mission(client, unit, **payload):
    body = ground_payload(unit, latitude=14.6, longitude=121.0, **payload)
    response = client.post("/api/missions/", body, format="json")
    assert response.status_code == 201, response.content
    return Mission.objects.get(pk=response.json()["id"])


def test_creating_outside_mission_notifies_only_the_operations_center(
    as_user, planner, unit
):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    people, _office = destination_posts(destination)

    mission = create_outside_mission(
        as_user(planner), unit, notify_destination_aor=True
    )

    notes = notifications_for(mission)
    assert notes.count() == 1
    assert notes.first().recipient_id == people["operations_center"].id
    sample = notes.first()
    assert mission.mission_number in sample.title
    assert mission.title in sample.message
    assert "North Ridge" in sample.message
    assert "14.600000, 121.000000" in sample.message
    assert "BRAVO AOR" in sample.message
    assert "Forward the details" in sample.message
    # The link opens the mission, which the destination office may now read.
    assert sample.related_type == "mission"
    assert sample.related_id == str(mission.id)


def test_creating_inside_own_aor_notifies_nobody(as_user, planner, unit):
    make_aor(unit, code="AOR-OWN")
    destination_posts(unit)

    mission = create_outside_mission(
        as_user(planner), unit, notify_destination_aor=True
    )

    assert notifications_for(mission).count() == 0


def test_creating_without_the_prompt_notifies_nobody(as_user, planner, unit):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    destination_posts(destination)

    mission = create_outside_mission(as_user(planner), unit)

    assert notifications_for(mission).count() == 0


def test_the_handoff_is_never_repeated(as_user, planner, unit):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    destination_posts(destination)
    mission = create_outside_mission(as_user(planner), unit)

    first = aor_handoff.notify_destination(mission, planner)
    second = aor_handoff.notify_destination(mission, planner)

    assert first["notified"] == 1
    assert second["notified"] == 0
    assert second["already_notified"] == 1
    assert notifications_for(mission).count() == 1


def test_destination_center_can_read_and_monitor_but_not_change(
    as_user, planner, unit
):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    people, _office = destination_posts(destination)
    mission = create_outside_mission(
        as_user(planner), unit, notify_destination_aor=True
    )

    center = people["operations_center"]
    detail = as_user(center).get(f"/api/missions/{mission.id}/")
    assert detail.status_code == 200, detail.content
    assert detail.json()["id"] == mission.id

    refused = as_user(center).patch(
        f"/api/missions/{mission.id}/", {"title": "Changed from afar"}, format="json"
    )
    assert refused.status_code == 403
    assert Mission.objects.get(pk=mission.id).title == mission.title

    # Monitoring the movement belongs to the destination Operations Center.
    board = as_user(center).get(f"/api/missions/{mission.id}/tracking/")
    assert board.status_code == 200, board.content
    assert board.json()["can_monitor"] is True
    assert board.json()["can_manage"] is False
    started = as_user(center).post(
        f"/api/missions/{mission.id}/tracking/start/", {}, format="json"
    )
    assert started.status_code == 403

    # A destination-unit member outside the Operations Center was not notified
    # and cannot see the mission at all.
    outsider = make_user(unit=destination, authority="Creator")
    assert as_user(outsider).get(f"/api/missions/{mission.id}/").status_code == 404


def test_command_posts_are_not_notified_until_the_center_forwards(
    as_user, planner, unit
):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    people, _office = destination_posts(destination)
    mission = create_outside_mission(
        as_user(planner), unit, notify_destination_aor=True
    )

    commander = people["commander"]
    # The commander was not a handoff recipient, so the mission is not visible.
    assert as_user(commander).get(f"/api/missions/{mission.id}/").status_code == 404

    # The mission's own planner can see it, but only the destination Operations
    # Center may forward it.
    refused = as_user(planner).post(
        f"/api/missions/{mission.id}/aor-handoff/", {}, format="json"
    )
    assert refused.status_code == 403


def test_the_center_forwards_to_the_command_posts(as_user, planner, unit):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    people, _office = destination_posts(destination)
    mission = create_outside_mission(
        as_user(planner), unit, notify_destination_aor=True
    )

    center = people["operations_center"]
    response = as_user(center).post(
        f"/api/missions/{mission.id}/aor-handoff/", {}, format="json"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["forwarded"] == 4

    forwarded = notifications_for(mission, aor_handoff.FORWARD_CATEGORY)
    assert forwarded.count() == 4
    assert {row.recipient_id for row in forwarded} == {
        people[role].id for role in COMMAND_ROLES
    }
    assert all(mission.mission_number in row.title for row in forwarded)
    assert all("Forwarded by the BRAVO Operations Center" in row.message for row in forwarded)

    commander = people["commander"]
    detail = as_user(commander).get(f"/api/missions/{mission.id}/")
    assert detail.status_code == 200, detail.content

    # The forwarded details do not hand over the owning unit's work or the
    # live board: monitoring stays with the destination Operations Center.
    assert (
        as_user(commander)
        .patch(f"/api/missions/{mission.id}/", {"title": "Changed"}, format="json")
        .status_code
        == 403
    )
    assert (
        as_user(commander)
        .get(f"/api/missions/{mission.id}/tracking/")
        .status_code
        == 403
    )
    started = as_user(commander).post(
        f"/api/missions/{mission.id}/tracking/start/", {}, format="json"
    )
    assert started.status_code == 403

    # Forwarding again is safe and never duplicates a recipient.
    again = as_user(center).post(
        f"/api/missions/{mission.id}/aor-handoff/", {}, format="json"
    )
    assert again.status_code == 200
    assert again.json()["forwarded"] == 0
    assert again.json()["already_forwarded"] == 4
    assert notifications_for(mission, aor_handoff.FORWARD_CATEGORY).count() == 4


def test_handoff_status_tells_the_center_who_can_be_forwarded_to(
    as_user, planner, unit
):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    people, _office = destination_posts(destination)
    mission = create_outside_mission(
        as_user(planner), unit, notify_destination_aor=True
    )

    center = people["operations_center"]
    status = as_user(center).get(f"/api/missions/{mission.id}/aor-handoff/").json()
    assert status["destination"]["unit_acronym"] == "BRAVO"
    assert status["destination"]["aor_name"] == "BRAVO AOR"
    assert status["can_forward"] is True
    assert [row["post"] for row in status["recipients"]] == [
        "Commander",
        "Deputy Commander",
        "Director for Operations",
        "Operations Officer",
    ]
    assert status["forwarded"] == []

    as_user(center).post(f"/api/missions/{mission.id}/aor-handoff/", {}, format="json")

    after = as_user(people["commander"]).get(
        f"/api/missions/{mission.id}/aor-handoff/"
    ).json()
    assert after["can_forward"] is False
    assert {row["post"] for row in after["forwarded"]} == {
        row["post"] for row in status["recipients"]
    }


def test_forward_refuses_when_the_destination_has_no_command_posts(
    as_user, planner, unit
):
    destination = make_unit(name="Bravo Group", acronym="BRAVO")
    make_aor(destination)
    office = OfficeSection.objects.create(
        unit=destination,
        name="Operations Center",
        code="OPS-C",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    center = make_user(unit=destination, authority="Creator")
    SectionAssignment.objects.create(user=center, section=office, assignment_type="primary")

    mission = create_outside_mission(
        as_user(planner), unit, notify_destination_aor=True
    )

    response = as_user(center).post(
        f"/api/missions/{mission.id}/aor-handoff/", {}, format="json"
    )
    assert response.status_code == 400
    assert "holds a post" in response.json()["detail"]
    assert notifications_for(mission, aor_handoff.FORWARD_CATEGORY).count() == 0
