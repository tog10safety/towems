"""Permanent account deletion: the seat holder's alone, and only when nothing refers to it."""

from __future__ import annotations

import pytest

from apps.accounts import ownership
from apps.accounts.models import User
from apps.core.factories import make_user
from apps.ops.models import AuditLog

pytestmark = pytest.mark.django_db


def appoint(user):
    return ownership.hand_over(
        user, actor=None, reason="Initial appointment", require_owner=False
    )


def test_only_the_seat_holder_may_delete_an_account(as_user, admin):
    appoint(admin)
    other_admin = make_user(authority="Administrator")
    target = make_user(username="temp.account")

    response = as_user(other_admin).delete(
        f"/api/admin/users/{target.id}/", {"reason": "Cleanup"}, format="json"
    )

    assert response.status_code == 400
    assert "Super Administrator" in str(response.json())
    assert User.objects.filter(pk=target.id).exists()


def test_the_seat_holder_deletes_an_account_with_no_history(as_user, admin):
    appoint(admin)
    target = make_user(username="temp.account")

    response = as_user(admin).delete(
        f"/api/admin/users/{target.id}/", {"reason": "Opened in error"}, format="json"
    )

    assert response.status_code == 204, response.content
    assert not User.objects.filter(pk=target.id).exists()
    entry = AuditLog.objects.filter(action="delete_user").first()
    assert entry is not None
    assert entry.actor_id == admin.id
    assert entry.note == "Opened in error"


def test_an_account_with_history_must_be_deactivated(as_user, admin):
    appoint(admin)
    target = make_user(username="used.account")
    AuditLog.objects.create(actor=target, action="login", entity_type="session")

    response = as_user(admin).delete(
        f"/api/admin/users/{target.id}/", {"reason": "Cleanup"}, format="json"
    )

    assert response.status_code == 400
    assert "operational history" in str(response.json())
    assert User.objects.filter(pk=target.id).exists()


def test_the_seat_holder_cannot_delete_their_own_account(as_user, admin):
    appoint(admin)

    response = as_user(admin).delete(
        f"/api/admin/users/{admin.id}/", {"reason": "Cleanup"}, format="json"
    )

    assert response.status_code == 400
    assert User.objects.filter(pk=admin.id).exists()


def test_another_seat_holder_cannot_be_deleted(as_user, admin):
    appoint(admin)
    other_admin = make_user(authority="Administrator")

    response = as_user(other_admin).delete(
        f"/api/admin/users/{admin.id}/", {"reason": "Cleanup"}, format="json"
    )

    assert response.status_code == 400
    assert User.objects.filter(pk=admin.id).exists()


def test_a_reason_is_required(as_user, admin):
    appoint(admin)
    target = make_user(username="temp.account")

    response = as_user(admin).delete(
        f"/api/admin/users/{target.id}/", {"reason": " "}, format="json"
    )

    assert response.status_code == 400
    assert User.objects.filter(pk=target.id).exists()


def test_personnel_purge_is_the_seat_holders_alone(as_user, admin, unit):
    appoint(admin)
    other_admin = make_user(unit=unit, authority="Administrator")
    target = make_user(unit=unit)

    refused = as_user(other_admin).delete(
        f"/api/personnel/{target.id}/?purge=true", {"reason": "Cleanup"}, format="json"
    )
    assert refused.status_code == 400
    target.refresh_from_db()
    assert target.is_active is True

    allowed = as_user(admin).delete(
        f"/api/personnel/{target.id}/?purge=true",
        {"reason": "Registered in error"},
        format="json",
    )
    assert allowed.status_code == 204, allowed.content
    assert not User.objects.filter(pk=target.id).exists()
