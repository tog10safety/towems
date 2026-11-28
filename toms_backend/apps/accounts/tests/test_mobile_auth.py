"""Android biometric token chain, device administration and auto-deactivation."""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import mobile_auth
from apps.accounts.models import MobileDevice, MobileSession, UserMFA, UserProfile
from apps.core.factories import PASSWORD
from apps.ops.models import AuditLog

pytestmark = pytest.mark.django_db

DEVICE_ID = "android_test_device_01"


def _csrf(client):
    assert client.get("/api/auth/csrf/").status_code == 200
    return client.cookies[settings.CSRF_COOKIE_NAME].value


def _login_session(api, user):
    token = _csrf(api)
    response = api.post(
        "/api/auth/login/",
        {"username": user.username, "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    return token


def _mark_mfa_complete(api, user):
    """The factory account has not been through the interactive TOTP setup.

    The live production flow reaches this state through MFAChallengeView; tests
    mark the session and the MFA row so the enrollment endpoint is exercised
    with the same server-side preconditions.
    """
    UserMFA.objects.update_or_create(
        user=user,
        defaults={
            "secret": "JBSWY3DPEHPK3PXP",
            "account": "test@example.com",
            "enabled_at": timezone.now(),
        },
    )
    session = api.session
    session["mfa_verified"] = True
    session.pop("mfa_setup_required", None)
    session.pop("mfa_enrollment_required", None)
    session.save()


def _enroll(api, csrf, device_id=DEVICE_ID):
    return api.post(
        "/api/auth/mobile/enroll/",
        {
            "device_id": device_id,
            "label": "Test Pixel",
            "platform": "android",
            "app_version": "1.0",
        },
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )


def _bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def test_enrollment_mints_access_and_rotating_refresh(api, creator):
    csrf = _login_session(api, creator)
    _mark_mfa_complete(api, creator)
    response = _enroll(api, csrf)
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["expires_in"] == 900
    assert body["refresh_token"].startswith(mobile_auth.REFRESH_PREFIX)
    assert MobileDevice.objects.filter(user=creator, device_id=DEVICE_ID).exists()
    session = MobileSession.objects.get(user=creator)
    # The plaintext refresh token is never stored.
    assert session.refresh_hash != body["refresh_token"]
    assert AuditLog.objects.filter(action="mobile_device_registered").exists()

    me = APIClient().get("/api/auth/me/", **_bearer(body["access_token"]))
    assert me.status_code == 200, me.content
    assert me.json()["username"] == creator.username


def test_refresh_rotates_and_rejects_the_previous_token(api, creator):
    csrf = _login_session(api, creator)
    _mark_mfa_complete(api, creator)
    first = _enroll(api, csrf).json()

    client = APIClient()
    renewed = client.post(
        "/api/auth/mobile/refresh/",
        {"refresh_token": first["refresh_token"], "device_id": DEVICE_ID},
        format="json",
    )
    assert renewed.status_code == 200, renewed.content
    rotated = renewed.json()
    assert rotated["refresh_token"] != first["refresh_token"]

    replay = APIClient().post(
        "/api/auth/mobile/refresh/",
        {"refresh_token": first["refresh_token"], "device_id": DEVICE_ID},
        format="json",
    )
    assert replay.status_code == 401
    assert AuditLog.objects.filter(action="mobile_token_refresh").exists()


def test_expired_activity_window_requires_reauth(api, creator):
    csrf = _login_session(api, creator)
    _mark_mfa_complete(api, creator)
    body = _enroll(api, csrf).json()
    session = MobileSession.objects.get(user=creator)
    session.last_activity_at = timezone.now() - dt.timedelta(days=31)
    session.save(update_fields=["last_activity_at"])

    response = APIClient().get("/api/auth/me/", **_bearer(body["access_token"]))
    assert response.status_code in (401, 403)
    session.refresh_from_db()
    assert session.revoked_at is not None


def test_thirty_day_absolute_limit_requires_fresh_login(api, creator):
    csrf = _login_session(api, creator)
    _mark_mfa_complete(api, creator)
    body = _enroll(api, csrf).json()
    session = MobileSession.objects.get(user=creator)
    session.session_started_at = timezone.now() - dt.timedelta(days=31)
    session.last_activity_at = timezone.now()
    session.save(update_fields=["session_started_at", "last_activity_at"])

    response = APIClient().post(
        "/api/auth/mobile/refresh/",
        {"refresh_token": body["refresh_token"], "device_id": DEVICE_ID},
        format="json",
    )
    assert response.status_code == 401
    assert "30-day" in response.json()["detail"]


def test_refresh_keeps_a_closed_app_session_alive(api, creator):
    csrf = _login_session(api, creator)
    _mark_mfa_complete(api, creator)
    body = _enroll(api, csrf).json()
    session = MobileSession.objects.get(user=creator)
    # Closing the app for nearly the whole absolute window must not force the
    # person back to a password; the biometric unlock refreshes the chain.
    old = timezone.now() - dt.timedelta(days=29)
    session.last_activity_at = old
    session.save(update_fields=["last_activity_at"])

    renewed = APIClient().post(
        "/api/auth/mobile/refresh/",
        {"refresh_token": body["refresh_token"], "device_id": DEVICE_ID},
        format="json",
    )
    assert renewed.status_code == 200, renewed.content
    session.refresh_from_db()
    assert session.last_activity_at > old


def test_revoking_device_immediately_ends_access_and_refresh(api, admin, creator):
    csrf = _login_session(api, creator)
    _mark_mfa_complete(api, creator)
    body = _enroll(api, csrf).json()
    device = MobileDevice.objects.get(user=creator, device_id=DEVICE_ID)

    admin_client = APIClient()
    admin_client.force_authenticate(user=admin)
    listed = admin_client.get("/api/admin/mobile-devices/")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    revoked = admin_client.post(
        f"/api/admin/mobile-devices/{device.id}/revoke/",
        {"reason": "Phone lost"},
        format="json",
    )
    assert revoked.status_code == 200, revoked.content
    device.refresh_from_db()
    assert device.is_authorized is False
    # The WebView session the device enrolled from is ended too.
    assert api.get("/api/auth/me/").status_code in (401, 403)

    assert APIClient().get("/api/auth/me/", **_bearer(body["access_token"])).status_code in (401, 403)
    assert (
        APIClient()
        .post(
            "/api/auth/mobile/refresh/",
            {"refresh_token": body["refresh_token"], "device_id": DEVICE_ID},
            format="json",
        )
        .status_code
        in (401, 403)
    )

    authorised = admin_client.post(
        f"/api/admin/mobile-devices/{device.id}/authorize/", {}, format="json"
    )
    assert authorised.status_code == 200
    device.refresh_from_db()
    assert device.is_authorized is True
    assert device.revoked_at is None


def test_device_admin_is_restricted_to_administrators(as_user, creator):
    denied = as_user(creator).get("/api/admin/mobile-devices/")
    assert denied.status_code == 403


def test_administrator_deactivation_revokes_mobile_chain(login, api, admin, creator):
    login(admin)
    # Enrol the creator through a live session for the token that must be revoked.
    creator_client = APIClient()
    creator_csrf = _login_session(creator_client, creator)
    _mark_mfa_complete(creator_client, creator)
    creator_body = _enroll(creator_client, creator_csrf, "android_creator_02").json()

    response = api.post(
        f"/api/admin/users/{creator.id}/deactivate/",
        {"reason": "Security review"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert creator.mobile_sessions.filter(revoked_at__isnull=True).count() == 0
    assert creator.mobile_devices.filter(is_authorized=True).count() == 0
    assert (
        APIClient()
        .get("/api/auth/me/", **_bearer(creator_body["access_token"]))
        .status_code
        in (401, 403)
    )


def test_command_deactivates_idle_account_and_is_audited(creator):
    Profile = UserProfile.objects.get_or_create(user=creator)[0]
    Profile.security_last_active_at = timezone.now() - dt.timedelta(days=31)
    # A background poll may still have touched last_seen; it must not hold the
    # account open when there has been no authenticated human activity.
    Profile.last_seen = timezone.now()
    Profile.save(update_fields=["security_last_active_at", "last_seen"])

    call_command("deactivate_inactive_accounts", verbosity=0)
    creator.refresh_from_db()
    assert creator.is_active is False
    assert AuditLog.objects.filter(
        action="auto_deactivate_user", entity_id=str(creator.id)
    ).exists()


def test_command_keeps_recently_active_account(creator):
    Profile = UserProfile.objects.get_or_create(user=creator)[0]
    Profile.security_last_active_at = timezone.now() - dt.timedelta(days=2)
    Profile.save(update_fields=["security_last_active_at"])

    call_command("deactivate_inactive_accounts", verbosity=0)
    creator.refresh_from_db()
    assert creator.is_active is True


def test_web_session_starts_with_both_security_clocks(api, creator):
    _login_session(api, creator)
    session = api.session
    assert "auth_session_started" in session
    assert "auth_last_activity" in session


def test_web_session_idle_limit_ends_the_session(api, creator):
    _login_session(api, creator)
    session = api.session
    session["auth_last_activity"] = (
        timezone.now() - dt.timedelta(minutes=31)
    ).isoformat()
    session.save()
    assert api.get("/api/auth/me/").status_code in (401, 403)


def test_background_poll_does_not_extend_web_session(api, creator):
    _login_session(api, creator)
    session = api.session
    old = (timezone.now() - dt.timedelta(minutes=29)).isoformat()
    session["auth_last_activity"] = old
    session.save()

    assert api.get("/api/auth/me/").status_code == 200
    assert api.session["auth_last_activity"] == old

    # A real user event carries the activity marker and slides the clock.
    assert (
        api.get("/api/auth/me/", HTTP_X_TOWEMS_ACTIVITY="1").status_code
        == 200
    )
    assert api.session["auth_last_activity"] != old


def test_web_session_thirty_day_limit_ends_the_session(api, creator):
    _login_session(api, creator)
    session = api.session
    session["auth_session_started"] = (
        timezone.now() - dt.timedelta(days=31)
    ).isoformat()
    session.save()
    assert api.get("/api/auth/me/").status_code in (401, 403)
