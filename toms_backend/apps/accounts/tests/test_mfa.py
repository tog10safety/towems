"""TOTP MFA for Administrators and Commanders."""

from __future__ import annotations

import datetime as dt
import time

import pyotp
import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from apps.accounts.models import MFARecoveryCode, UserMFA, UserProfile
from apps.legacy.models import UserActivity
from apps.ops.models import AuditLog

pytestmark = pytest.mark.django_db


def _csrf(api) -> str:
    return api.cookies[settings.CSRF_COOKIE_NAME].value


def _enrol(api, user) -> tuple[str, list[str]]:
    # Enrolment is keyed to the person's Gmail account, so give the fixture one.
    profile, _created = UserProfile.objects.get_or_create(user=user)
    if not profile.email:
        profile.email = f"{user.username}@gmail.com"
        profile.save(update_fields=["email"])
    response = api.get("/api/auth/csrf/")
    assert response.status_code == 200
    token = _csrf(api)
    response = api.post(
        "/api/auth/login/",
        {"username": user.username, "password": "Passw0rd!23"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    token = _csrf(api)
    response = api.post(
        "/api/auth/mfa/setup/",
        {},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    secret = response.json()["secret"]

    code = pyotp.TOTP(secret).at(int(time.time()) // 30 * 30)
    response = api.post(
        "/api/auth/mfa/setup/verify/",
        {"code": code},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    return secret, response.json()["recovery_codes"]


def _complete_profile(user, *, email="user@gmail.com"):
    """Give ``user`` the profile the deployment refresh asks for."""
    profile, _created = UserProfile.objects.get_or_create(user=user)
    profile.avatar_filename = "avatar_test.png"
    profile.email = email
    # Finished refresh: the account is now held to authenticator enrolment.
    profile.onboarding_completed_at = timezone.now()
    profile.save()
    user.signature_filename = "signature_test.png"
    user.force_password_change = False
    user.save(update_fields=["signature_filename", "force_password_change"])
    return profile


def test_administrator_first_login_starts_fourteen_day_grace(login, admin):
    response = login(admin)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["mfa_required"] is True
    assert body["mfa_enabled"] is False
    assert body["mfa_enrollment_required"] is True
    # An account outside the deployment refresh keeps the grace window; one
    # that has finished the refresh is held to immediate enrolment.
    assert body["mfa_setup_required"] is False

    state = UserMFA.objects.get(pk=admin.pk)
    assert state.grace_started_at is not None
    assert state.enabled_at is None


def test_commander_first_login_requires_enrolment(login, commander):
    response = login(commander)
    assert response.status_code == 200, response.content
    assert response.json()["mfa_enrollment_required"] is True
    assert UserMFA.objects.filter(pk=commander.pk).exists()


def test_every_account_enrols_not_only_privileged_roles(login, creator):
    """Deployment policy: Google Authenticator for all active accounts."""
    response = login(creator)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["mfa_required"] is True
    assert body["mfa_enabled"] is False
    assert body["mfa_enrollment_required"] is True
    assert UserMFA.objects.filter(pk=creator.pk).exists()


def test_setup_returns_qr_and_enables_mfa(api, admin):
    secret, codes = _enrol(api, admin)
    assert len(secret) >= 16
    assert len(codes) == 10
    assert len({code for code in codes}) == 10

    admin.refresh_from_db()
    state = UserMFA.objects.get(pk=admin.pk)
    assert state.account == f"{admin.username}@gmail.com"
    assert state.enabled_at is not None
    assert state.grace_started_at is None
    assert state.recovery_codes.filter(used_at__isnull=True).count() == 10

    me = api.get("/api/auth/me/")
    assert me.status_code == 200
    assert me.json()["mfa_enabled"] is True
    assert me.json()["mfa_verified"] is True
    assert me.json()["mfa_account"] == f"{admin.username}@gmail.com"


def test_enrolled_login_requires_totp_before_session(api, admin):
    secret, _codes = _enrol(api, admin)
    token = _csrf(api)
    assert (
        api.post(
            "/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=token
        ).status_code
        == 204
    )

    login = api.post(
        "/api/auth/login/",
        {"username": admin.username, "password": "Passw0rd!23"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert login.status_code == 200, login.content
    body = login.json()
    assert body["mfa_required"] is True
    assert "challenge" in body
    assert "id" not in body

    # No session was created by the password step.
    assert api.get("/api/auth/me/").status_code in (401, 403)

    code = pyotp.TOTP(secret).at((int(time.time()) // 30 + 1) * 30)
    verified = api.post(
        "/api/auth/mfa/challenge/",
        {"challenge": body["challenge"], "code": code, "recovery": False},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert verified.status_code == 200, verified.content
    assert verified.json()["username"] == admin.username
    assert api.get("/api/auth/me/").status_code == 200


def test_recovery_code_is_one_time(api, admin):
    _secret, codes = _enrol(api, admin)
    token = _csrf(api)
    api.post("/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=token)
    login = api.post(
        "/api/auth/login/",
        {"username": admin.username, "password": "Passw0rd!23"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    challenge = login.json()["challenge"]

    first = api.post(
        "/api/auth/mfa/challenge/",
        {"challenge": challenge, "code": codes[0], "recovery": True},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert first.status_code == 200, first.content
    assert first.json()["used_recovery_code"] is True

    second = api.post(
        "/api/auth/mfa/challenge/",
        {"challenge": challenge, "code": codes[0], "recovery": True},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert second.status_code == 401


def test_expired_window_leaves_only_setup_session(api, login, admin):
    # The profile refresh is finished, so the authenticator gate is next.
    _complete_profile(admin)
    assert login(admin).status_code == 200
    state = UserMFA.objects.get(pk=admin.pk)
    state.grace_started_at = timezone.now() - dt.timedelta(days=15)
    state.save(update_fields=["grace_started_at"])

    token = _csrf(api)
    assert api.post(
        "/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=token
    ).status_code == 204
    response = api.post(
        "/api/auth/login/",
        {"username": admin.username, "password": "Passw0rd!23"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["mfa_setup_required"] is True
    assert body["mfa_enrollment_required"] is True

    # The session can read its own state and reach setup, but no operational API.
    assert api.get("/api/auth/mfa/").status_code == 200
    blocked = api.get("/api/roles/")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "mfa_setup_required"


def test_administrator_reset_is_audited_and_restarts_window(as_user, admin, commander):
    UserMFA.objects.create(
        user=commander,
        secret=pyotp.random_base32(),
        enabled_at=timezone.now(),
        last_used_counter=1,
    )
    state = UserMFA.objects.get(pk=commander.pk)
    MFARecoveryCode.objects.create(
        mfa=state, code_hash=make_password("ABCDEFGH")
    )

    response = as_user(admin).post(
        f"/api/admin/users/{commander.id}/reset-mfa/",
        {"reason": "Lost authenticator device"},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["mfa_enabled"] is False
    assert body["mfa_required"] is True

    state.refresh_from_db()
    assert state.secret == ""
    assert state.enabled_at is None
    assert state.last_used_counter is None
    assert state.grace_started_at is not None
    assert state.recovery_codes.count() == 0

    entry = AuditLog.objects.filter(
        action="reset_mfa", entity_id=str(commander.id)
    ).first()
    assert entry is not None
    assert entry.actor_id == admin.id
    assert entry.note == "Lost authenticator device"
    assert UserActivity.objects.filter(
        user_id=admin.id,
        activity_type="user_modified",
        description__contains="Reset MFA enrolment",
    ).exists()


def test_reset_requires_a_reason(as_user, admin, commander):
    UserMFA.objects.create(
        user=commander,
        secret=pyotp.random_base32(),
        enabled_at=timezone.now(),
    )
    response = as_user(admin).post(
        f"/api/admin/users/{commander.id}/reset-mfa/",
        {"reason": ""},
        format="json",
    )
    assert response.status_code == 400
    assert UserMFA.objects.get(pk=commander.pk).enabled_at is not None


def test_every_account_can_start_mfa_setup(as_user, creator):
    profile, _created = UserProfile.objects.get_or_create(user=creator)
    profile.email = "creator@gmail.com"
    profile.save(update_fields=["email"])

    response = as_user(creator).post("/api/auth/mfa/setup/", {}, format="json")
    assert response.status_code == 200, response.content
    body = response.json()
    assert len(body["secret"]) >= 16
    # The authenticator entry carries the person's Gmail account.
    assert body["account"] == "creator@gmail.com"
    assert "creator%40gmail.com" in body["otpauth_url"]


def test_enrolment_refuses_without_a_gmail_account(as_user, creator):
    response = as_user(creator).post("/api/auth/mfa/setup/", {}, format="json")
    assert response.status_code == 400, response.content
    assert "Gmail" in response.json()["detail"]
    state = UserMFA.objects.filter(pk=creator.pk).first()
    assert state is None or state.secret == ""
