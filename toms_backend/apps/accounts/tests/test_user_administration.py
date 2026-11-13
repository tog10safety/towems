"""Administrator account management (spec §3)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import PasswordResetToken
from apps.core.factories import PASSWORD
from apps.ops.models import AuditLog

pytestmark = pytest.mark.django_db


def test_only_administrators_reach_user_management(as_user, creator, admin, evaluator):
    assert as_user(creator).get("/api/admin/users/").status_code == 403
    assert as_user(evaluator).get("/api/admin/users/").status_code == 403
    response = as_user(admin).get("/api/admin/users/")
    assert response.status_code == 200
    assert response.json()["count"] >= 2


def test_account_payload_never_contains_a_password(as_user, admin, creator):
    response = as_user(admin).get(f"/api/admin/users/?search={creator.username}")
    assert response.status_code == 200
    body = response.content.decode().lower()
    assert "password_hash" not in body
    assert "password\"" not in body
    assert "scrypt" not in body


def test_deactivate_records_reason_and_blocks_sign_in(login, api, admin, creator):
    login(admin)
    response = api.post(
        f"/api/admin/users/{creator.id}/deactivate/",
        {"reason": "Pending investigation"},
        format="json",
    )
    assert response.status_code == 200, response.content
    creator.refresh_from_db()
    assert creator.is_active is False

    entry = AuditLog.objects.filter(
        action="deactivate_user", entity_id=str(creator.id)
    ).first()
    assert entry is not None
    assert entry.note == "Pending investigation"
    assert entry.actor_id == admin.id
    assert entry.before == {"is_active": True}
    assert entry.after == {"is_active": False}

    fresh = APIClient()
    fresh.get("/api/auth/csrf/")
    csrf = fresh.cookies["csrftoken"].value
    denied = fresh.post(
        "/api/auth/login/",
        {"username": creator.username, "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert denied.status_code == 401


def test_deactivated_user_session_stops_working(login, api, admin, creator):
    login(creator)
    assert api.get("/api/auth/me/").status_code == 200

    admin_client = APIClient()
    admin_client.force_authenticate(user=admin)
    assert (
        admin_client.post(
            f"/api/admin/users/{creator.id}/deactivate/",
            {"reason": "Role change"},
            format="json",
        ).status_code
        == 200
    )

    # The same session cookie now resolves to an anonymous user.
    assert api.get("/api/auth/me/").status_code in (401, 403)


def test_deactivation_preserves_records_and_audit(as_user, admin, creator):
    from apps.ops.models import Notification

    Notification.objects.create(recipient=creator, title="Historical item")
    as_user(admin).post(
        f"/api/admin/users/{creator.id}/deactivate/", {"reason": "Posting"}, format="json"
    )
    assert creator.password_hash  # digest retained
    assert Notification.objects.filter(recipient_id=creator.id).exists()
    assert AuditLog.objects.filter(entity_type="user", entity_id=str(creator.id)).exists()


def test_reactivation_restores_access(as_user, admin, creator):
    as_user(admin).post(
        f"/api/admin/users/{creator.id}/deactivate/", {"reason": "Temporary"}, format="json"
    )
    response = as_user(admin).post(
        f"/api/admin/users/{creator.id}/reactivate/", {"reason": "Cleared"}, format="json"
    )
    assert response.status_code == 200
    creator.refresh_from_db()
    assert creator.is_active is True


def test_cannot_deactivate_yourself(as_user, admin):
    response = as_user(admin).post(
        f"/api/admin/users/{admin.id}/deactivate/", {"reason": "Mistake"}, format="json"
    )
    assert response.status_code == 400


def test_deactivating_an_already_inactive_account_is_refused(as_user, admin, creator):
    as_user(admin).post(
        f"/api/admin/users/{creator.id}/deactivate/", {"reason": "Cleanup"}, format="json"
    )
    response = as_user(admin).post(
        f"/api/admin/users/{creator.id}/deactivate/", {"reason": "Again"}, format="json"
    )
    assert response.status_code == 400


def test_deactivation_requires_a_reason(as_user, admin, creator):
    response = as_user(admin).post(
        f"/api/admin/users/{creator.id}/deactivate/", {"reason": ""}, format="json"
    )
    assert response.status_code == 400
    creator.refresh_from_db()
    assert creator.is_active is True


def test_admin_edit_cannot_change_identity_fields(as_user, admin, creator):
    response = as_user(admin).patch(
        f"/api/admin/users/{creator.id}/",
        {"username": "renamed", "serial_number": "HACKED"},
        format="json",
    )
    assert response.status_code == 200  # unknown fields are ignored by DRF
    creator.refresh_from_db()
    assert creator.username != "renamed"
    assert creator.serial_number != "HACKED"


def test_admin_edit_updates_unit_and_role_with_audit(as_user, admin, creator, other_unit):
    response = as_user(admin).patch(
        f"/api/admin/users/{creator.id}/",
        {"authority": "Evaluator", "unit_id": other_unit.id, "rank": "Maj"},
        format="json",
    )
    assert response.status_code == 200, response.content
    creator.refresh_from_db()
    assert creator.authority == "Evaluator"
    assert creator.unit_id == other_unit.id
    assert creator.unit_legacy == other_unit.name

    entry = AuditLog.objects.filter(
        action="update_user_account", entity_id=str(creator.id)
    ).first()
    assert entry is not None
    assert entry.after["authority"] == "Evaluator"


def test_admin_cannot_change_their_own_role(as_user, admin):
    response = as_user(admin).patch(
        f"/api/admin/users/{admin.id}/", {"authority": "Creator"}, format="json"
    )
    assert response.status_code == 400


def test_unit_safety_officer_cannot_change_roles_as_user(safety, creator, as_user):
    response = as_user(safety).patch(
        f"/api/personnel/{creator.id}/", {"authority": "Administrator"}, format="json"
    )
    assert response.status_code == 400
    creator.refresh_from_db()
    assert creator.authority == "Creator"


def test_reset_issues_single_use_link_without_exposing_a_password(as_user, admin, creator):
    response = as_user(admin).post(
        f"/api/admin/users/{creator.id}/reset-password/", {}, format="json"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["reset_url"].startswith("/reset-password?token=")
    assert "password" not in body
    token = body["reset_url"].split("token=", 1)[1]

    # Only a digest is stored.
    assert not PasswordResetToken.objects.filter(token_hash=token).exists()
    assert PasswordResetToken.objects.filter(user_id=creator.id, used_at__isnull=True).count() == 1
    assert AuditLog.objects.filter(action="issue_password_reset", entity_id=str(creator.id)).exists()

    fresh = APIClient()
    fresh.get("/api/auth/csrf/")
    csrf = fresh.cookies["csrftoken"].value
    completed = fresh.post(
        "/api/auth/password-reset/",
        {"token": token, "new_password": "N3w!Passw0rd"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert completed.status_code == 200, completed.content

    creator.refresh_from_db()
    assert creator.check_password("N3w!Passw0rd")
    assert creator.force_password_change is False

    # Single use.
    replay = fresh.post(
        "/api/auth/password-reset/",
        {"token": token, "new_password": "An0ther!Pass"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert replay.status_code == 400


def test_expired_reset_link_is_refused(as_user, admin, creator, api):
    token = as_user(admin).post(
        f"/api/admin/users/{creator.id}/reset-password/", {}, format="json"
    ).json()["reset_url"].split("token=", 1)[1]
    PasswordResetToken.objects.filter(user_id=creator.id).update(
        expires_at=timezone.now() - dt.timedelta(minutes=1)
    )
    api.get("/api/auth/csrf/")
    csrf = api.cookies["csrftoken"].value
    response = api.post(
        "/api/auth/password-reset/",
        {"token": token, "new_password": "N3w!Passw0rd"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 400
    creator.refresh_from_db()
    assert creator.check_password(PASSWORD)


def test_reset_requires_an_active_account(as_user, admin, creator):
    as_user(admin).post(
        f"/api/admin/users/{creator.id}/deactivate/", {"reason": "Suspended"}, format="json"
    )
    response = as_user(admin).post(
        f"/api/admin/users/{creator.id}/reset-password/", {}, format="json"
    )
    assert response.status_code == 400


def test_password_reset_validation_happens_before_token_consumption(api, as_user, admin, creator):
    token = as_user(admin).post(
        f"/api/admin/users/{creator.id}/reset-password/", {}, format="json"
    ).json()["reset_url"].split("token=", 1)[1]
    api.get("/api/auth/csrf/")
    csrf = api.cookies["csrftoken"].value
    weak = api.post(
        "/api/auth/password-reset/",
        {"token": token, "new_password": "short"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert weak.status_code == 400
    row = PasswordResetToken.objects.get(user_id=creator.id)
    assert row.used_at is None
