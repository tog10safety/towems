"""One designation per person across units, with detached service upward.

A person holds a post once - COL HUDAS cannot be Commander of two units. A
second, *different* post is allowed only as detached service (DS) to a higher
headquarters, and never a command post.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.command.models import UnitAppointment
from apps.core.factories import make_unit, make_user

pytestmark = pytest.mark.django_db


def appoint(unit, user, role, actor=None):
    return UnitAppointment.objects.create(unit=unit, user=user, role=role, appointed_by=actor)


def post(client, unit, user, role):
    return client.post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": user.id, "role": role},
        format="json",
    )


def higher_headquarters(unit, acronym="TOW"):
    hq = make_unit(acronym=acronym)
    unit.parent = hq
    unit.save(update_fields=["parent"])
    return hq


def test_the_same_post_cannot_be_held_in_two_units(as_user, admin, unit, other_unit):
    officer = make_user(unit=unit, authority="Creator")
    appoint(unit, officer, "commander", admin)

    response = post(as_user(admin), other_unit, officer, "commander")

    assert response.status_code == 400, response.content
    detail = response.json()["detail"]
    # The serial number is what identifies the person, so a repeated name in
    # another unit cannot pass as somebody else.
    assert officer.serial_number in detail
    assert "already" in detail
    assert UnitAppointment.objects.filter(
        user=officer, role="commander", is_active=True
    ).count() == 1


def test_the_database_itself_refuses_the_duplicate(as_user, admin, unit, other_unit):
    officer = make_user(unit=unit, authority="Creator")
    appoint(unit, officer, "commander", admin)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            appoint(other_unit, officer, "commander", admin)


def test_a_different_post_at_a_higher_headquarters_is_detached_service(
    as_user, admin, unit
):
    hq = higher_headquarters(unit)
    officer = make_user(unit=unit, authority="Creator")
    appoint(unit, officer, "commander", admin)

    response = post(as_user(admin), hq, officer, "director_operations")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["is_detached_service"] is True
    assert body["user_serial_number"] == officer.serial_number
    assert UnitAppointment.objects.filter(user=officer, is_active=True).count() == 2


def test_the_home_post_is_not_detached_service(as_user, admin, unit):
    officer = make_user(unit=unit, authority="Creator")
    response = post(as_user(admin), unit, officer, "operations_officer")

    assert response.status_code == 201, response.content
    assert response.json()["is_detached_service"] is False


def test_command_cannot_be_detached_to_a_higher_headquarters(as_user, admin, unit):
    hq = higher_headquarters(unit)
    officer = make_user(unit=unit, authority="Creator")
    appoint(unit, officer, "deputy_commander", admin)

    response = post(as_user(admin), hq, officer, "commander")

    assert response.status_code == 400, response.content
    assert "command posts" in response.json()["detail"]
    assert not UnitAppointment.objects.filter(user=officer, unit=hq, is_active=True).exists()


def test_a_second_post_is_not_allowed_at_an_unrelated_unit(as_user, admin, unit, other_unit):
    officer = make_user(unit=unit, authority="Creator")
    appoint(unit, officer, "operations_officer", admin)

    response = post(as_user(admin), other_unit, officer, "safety_officer")

    assert response.status_code == 400, response.content
    assert "higher headquarters" in response.json()["detail"]


def test_ending_the_post_frees_the_designation_for_another_unit(
    as_user, admin, unit, other_unit
):
    officer = make_user(unit=unit, authority="Creator")
    first = appoint(unit, officer, "safety_officer", admin)

    assert as_user(admin).delete(f"/api/appointments/{first.id}/").status_code == 200
    response = post(as_user(admin), other_unit, officer, "safety_officer")

    assert response.status_code == 201, response.content


def test_acting_command_is_exercised_in_the_persons_own_unit(
    as_user, admin, unit, other_unit
):
    officer = make_user(unit=unit, authority="Creator")
    now = timezone.now()

    response = as_user(admin).post(
        "/api/acting-appointments/",
        {
            "unit_id": other_unit.id,
            "user_id": officer.id,
            "acting_role": "commander",
            "reason": "Cover",
            "starts_at": now.isoformat(),
            "ends_at": (now + dt.timedelta(days=3)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "own unit" in response.json()["detail"]


def test_acting_command_in_the_persons_own_unit_still_works(as_user, admin, unit):
    officer = make_user(unit=unit, authority="Creator")
    now = timezone.now()

    response = as_user(admin).post(
        "/api/acting-appointments/",
        {
            "unit_id": unit.id,
            "user_id": officer.id,
            "acting_role": "commander",
            "reason": "Commander on leave",
            "starts_at": now.isoformat(),
            "ends_at": (now + dt.timedelta(days=3)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 201, response.content
