"""The single Super Administrator seat: uniqueness, handover and break-glass."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction

from apps.accounts import ownership
from apps.accounts.models import UserProfile
from apps.core.factories import make_user, unique_serial
from apps.ops.models import AuditLog

pytestmark = pytest.mark.django_db


def appoint(user):
    return ownership.hand_over(
        user, actor=None, reason="Initial appointment", require_owner=False
    )


def test_the_database_refuses_a_second_owner(admin, creator):
    appoint(admin)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            UserProfile.objects.create(user_id=creator.id, is_system_owner=True)


def test_handover_moves_the_seat_and_audits_it(as_user, admin):
    appoint(admin)
    successor = make_user(authority="Administrator")

    response = as_user(admin).post(
        f"/api/admin/users/{successor.id}/handover/",
        {"reason": "Change of system custodian"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["is_system_owner"] is True
    assert ownership.current_owner().id == successor.id
    assert ownership.is_system_owner(admin) is False

    entry = AuditLog.objects.filter(action="handover_system_owner").first()
    assert entry is not None
    assert entry.actor_id == admin.id
    assert entry.after["system_owner_id"] == successor.id
    assert entry.note == "Change of system custodian"


def test_only_the_current_owner_can_hand_the_seat_over(as_user, admin):
    owner = make_user(authority="Administrator")
    other_admin = make_user(authority="Administrator")
    successor = make_user(authority="Administrator")
    appoint(owner)

    response = as_user(other_admin).post(
        f"/api/admin/users/{successor.id}/handover/",
        {"reason": "Taking over"},
        format="json",
    )
    assert response.status_code == 400
    assert ownership.current_owner().id == owner.id


def test_handover_requires_a_reason(as_user, admin):
    appoint(admin)
    successor = make_user(authority="Administrator")

    response = as_user(admin).post(
        f"/api/admin/users/{successor.id}/handover/", {"reason": " "}, format="json"
    )
    assert response.status_code == 400
    assert ownership.current_owner().id == admin.id


def test_the_successor_must_be_an_administrator(as_user, admin, creator):
    appoint(admin)

    response = as_user(admin).post(
        f"/api/admin/users/{creator.id}/handover/",
        {"reason": "Wrong account"},
        format="json",
    )
    assert response.status_code == 400
    assert ownership.current_owner().id == admin.id


def test_an_inactive_account_cannot_take_the_seat(as_user, admin):
    appoint(admin)
    successor = make_user(authority="Administrator", is_active=False)

    response = as_user(admin).post(
        f"/api/admin/users/{successor.id}/handover/",
        {"reason": "Reserve account"},
        format="json",
    )
    assert response.status_code == 400
    assert ownership.current_owner().id == admin.id


def test_the_owner_cannot_be_deactivated_or_simply_demoted(as_user, admin):
    appoint(admin)
    other_admin = make_user(authority="Administrator")

    refused_deactivate = as_user(other_admin).post(
        f"/api/admin/users/{admin.id}/deactivate/",
        {"reason": "Cleanup"},
        format="json",
    )
    assert refused_deactivate.status_code == 400
    admin.refresh_from_db()
    assert admin.is_active is True

    refused_demote = as_user(other_admin).patch(
        f"/api/admin/users/{admin.id}/", {"authority": "Evaluator"}, format="json"
    )
    assert refused_demote.status_code == 400
    admin.refresh_from_db()
    assert admin.authority == "Administrator"


def test_break_glass_appoints_and_promotes_without_a_signed_in_actor(creator):
    call_command(
        "appoint_system_owner",
        username=creator.username,
        reason="Recovering a locked-out system",
        promote=True,
    )

    creator.refresh_from_db()
    assert creator.authority == "Administrator"
    assert ownership.current_owner().id == creator.id

    entry = AuditLog.objects.filter(action="handover_system_owner").first()
    assert entry is not None
    assert entry.actor_id is None
    assert "locked-out" in entry.note
    assert entry.after["authority"] == "Administrator"


def test_break_glass_refuses_to_promote_unless_asked(creator):
    with pytest.raises(CommandError):
        call_command(
            "appoint_system_owner",
            username=creator.username,
            reason="Recovery attempt",
        )
    assert ownership.current_owner() is None


def test_break_glass_requires_a_reason(admin):
    with pytest.raises(CommandError):
        call_command("appoint_system_owner", username=admin.username, reason="")


def test_break_glass_can_vacate_the_seat(admin):
    appoint(admin)

    call_command("appoint_system_owner", clear=True, reason="System decommissioned")

    assert ownership.current_owner() is None
    assert AuditLog.objects.filter(action="vacate_system_owner").exists()


def personnel_payload(unit, **overrides):
    body = {
        "password": "Passw0rd!23",
        "authority": "Creator",
        "category": "Officer",
        "first_name": "New",
        "last_name": "Person",
        "serial_number": unique_serial(),
        "branch_of_service": "PAF",
        "unit_id": unit.id,
        "is_active": True,
    }
    body.update(overrides)
    return body


def test_only_the_owner_may_create_an_administrator(as_user, admin, unit):
    appoint(admin)
    other_admin = make_user(unit=unit, authority="Administrator")

    refused = as_user(other_admin).post(
        "/api/personnel/",
        personnel_payload(unit, authority="Administrator"),
        format="json",
    )
    assert refused.status_code == 400
    assert "Super Administrator" in str(refused.json())

    allowed = as_user(admin).post(
        "/api/personnel/",
        personnel_payload(unit, authority="Administrator"),
        format="json",
    )
    assert allowed.status_code == 201, allowed.content


def test_only_the_owner_may_promote_through_an_account_edit(as_user, admin, creator, unit):
    appoint(admin)
    other_admin = make_user(unit=unit, authority="Administrator")

    refused = as_user(other_admin).patch(
        f"/api/admin/users/{creator.id}/",
        {"authority": "Administrator"},
        format="json",
    )
    assert refused.status_code == 400
    creator.refresh_from_db()
    assert creator.authority == "Creator"

    allowed = as_user(admin).patch(
        f"/api/admin/users/{creator.id}/",
        {"authority": "Administrator"},
        format="json",
    )
    assert allowed.status_code == 200, allowed.content
    creator.refresh_from_db()
    assert creator.authority == "Administrator"


def test_the_seat_holders_unit_is_hidden_from_everyone_else(
    as_user, admin, unit, other_unit
):
    """Only the seat holder sees which unit the seat is carried against."""
    appoint(admin)
    other_admin = make_user(unit=other_unit, authority="Administrator")

    seen_by_other = as_user(other_admin).get(f"/api/personnel/{admin.id}/")
    assert seen_by_other.status_code == 200, seen_by_other.content
    body = seen_by_other.json()
    assert body["unit_id"] is None
    assert not body["unit_name"]
    assert not body["unit_acronym"]

    seen_by_seat = as_user(admin).get(f"/api/personnel/{admin.id}/")
    assert seen_by_seat.status_code == 200, seen_by_seat.content
    assert seen_by_seat.json()["unit_id"] == unit.id


def test_the_account_list_also_hides_the_seat_holders_unit(
    as_user, admin, unit, other_unit
):
    appoint(admin)
    other_admin = make_user(unit=other_unit, authority="Administrator")

    response = as_user(other_admin).get(f"/api/admin/users/{admin.id}/")
    assert response.status_code == 200, response.content
    assert response.json()["unit_id"] is None


def test_other_administrators_keep_the_everyday_account_work(as_user, admin, unit):
    appoint(admin)
    other_admin = make_user(unit=unit, authority="Administrator")

    response = as_user(other_admin).post(
        "/api/personnel/",
        personnel_payload(unit, authority="Creator"),
        format="json",
    )
    assert response.status_code == 201, response.content
