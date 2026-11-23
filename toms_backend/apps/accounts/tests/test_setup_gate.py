"""First-sign-in profile refresh, then mandatory Google Authenticator.

Deployment policy: every active account replaces its password, uploads a
picture and signature, records a Gmail address, and only then enrols a TOTP
authenticator. Each gate blocks the rest of the API until it is satisfied.
"""

from __future__ import annotations

import time

import pyotp
import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import UserMFA, UserProfile
from apps.core.factories import PASSWORD

pytestmark = pytest.mark.django_db


def _csrf(api) -> str:
    return api.cookies[settings.CSRF_COOKIE_NAME].value


def _flagged_profile(user, *, email="user@gmail.com"):
    """An account the deployment refresh is waiting on."""
    profile, _created = UserProfile.objects.get_or_create(user=user)
    profile.onboarding_required = True
    profile.onboarding_completed_at = None
    profile.avatar_filename = "avatar_1.png"
    profile.avatar_content_type = "image/png"
    profile.email = email
    profile.save()
    user.signature_filename = "signature_1.png"
    user.force_password_change = True
    user.save(update_fields=["signature_filename", "force_password_change"])
    return profile


def test_login_holds_an_unfinished_account_in_the_profile_refresh(api, login, creator):
    _flagged_profile(creator)
    response = login(creator)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["profile_setup_required"] is True
    assert body["mfa_setup_required"] is False
    assert body["profile_setup_gaps"] == ["password"]
    assert body["profile_email_suffix"] == "@gmail.com"

    blocked = api.get("/api/auth/preferences/")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "profile_setup_required"
    assert blocked.json()["gaps"] == ["password"]

    # The profile, its uploads, the password route and sign-out stay open.
    assert api.get("/api/auth/profile/").status_code == 200


def test_non_gmail_address_is_rejected_by_the_profile_form(api, login, creator):
    _flagged_profile(creator)
    creator.force_password_change = False
    creator.save(update_fields=["force_password_change"])

    login(creator)
    token = _csrf(api)
    rejected = api.patch(
        "/api/auth/profile/",
        {"email": "user@example.mil.ph"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert rejected.status_code == 400
    assert "@gmail.com" in rejected.json()["errors"]["email"]

    accepted = api.patch(
        "/api/auth/profile/",
        {"email": "user@gmail.com"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert accepted.status_code == 200, accepted.content
    assert accepted.json()["profile_setup_gaps"] == []


def test_missing_account_address_does_not_satisfy_the_refresh(api, login, creator):
    _flagged_profile(creator, email="")
    creator.force_password_change = False
    creator.save(update_fields=["force_password_change"])

    response = login(creator)
    assert response.json()["profile_setup_gaps"] == ["email"]

    token = _csrf(api)
    rejected = api.post(
        "/api/auth/onboarding/complete/",
        {},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert rejected.status_code == 400
    assert rejected.json()["errors"]["gaps"] == ["email"]


def test_finishing_the_refresh_moves_straight_to_authenticator_setup(api, login, creator):
    UserProfile.objects.create(
        user=creator,
        onboarding_required=True,
        avatar_filename="avatar_1.png",
        avatar_content_type="image/png",
        email="creator@gmail.com",
    )
    creator.signature_filename = "signature_1.png"
    creator.force_password_change = False
    creator.save(update_fields=["signature_filename", "force_password_change"])

    login(creator)
    token = _csrf(api)
    done = api.post(
        "/api/auth/onboarding/complete/",
        {},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert done.status_code == 200, done.content
    assert done.json()["onboarding_required"] is False

    me = api.get("/api/auth/me/")
    assert me.json()["profile_setup_required"] is False
    assert me.json()["mfa_setup_required"] is True

    blocked = api.get("/api/auth/preferences/")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "mfa_setup_required"

    setup = api.post("/api/auth/mfa/setup/", {}, format="json", HTTP_X_CSRFTOKEN=token)
    assert setup.status_code == 200, setup.content
    assert setup.json()["account"] == "creator@gmail.com"
    code = pyotp.TOTP(setup.json()["secret"]).at(int(time.time()) // 30 * 30)
    verified = api.post(
        "/api/auth/mfa/setup/verify/",
        {"code": code},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert verified.status_code == 200, verified.content
    assert api.get("/api/auth/preferences/").status_code == 200


def _token(api, username):
    return api.post(
        "/api/auth/token/",
        {"username": username, "password": PASSWORD},
        format="json",
    )


def test_api_token_refused_while_the_profile_refresh_is_open(api, creator):
    _flagged_profile(creator)
    response = _token(api, creator.username)
    assert response.status_code == 403, response.content
    body = response.json()
    assert body["errors"]["code"] == "profile_setup_required"
    assert "access" not in body


def test_api_token_refused_before_authenticator_enrolment(api, creator):
    profile = UserProfile.objects.create(
        user=creator,
        onboarding_required=True,
        avatar_filename="avatar_1.png",
        avatar_content_type="image/png",
        email="creator@gmail.com",
        onboarding_completed_at=timezone.now(),
    )
    creator.signature_filename = "signature_1.png"
    creator.force_password_change = False
    creator.save(update_fields=["signature_filename", "force_password_change"])
    assert profile.onboarding_required

    response = _token(api, creator.username)
    assert response.status_code == 403, response.content
    assert response.json()["errors"]["code"] == "mfa_setup_required"


def test_api_token_issued_once_setup_is_complete(api, creator):
    UserProfile.objects.create(
        user=creator,
        onboarding_required=False,
        onboarding_completed_at=timezone.now(),
        avatar_filename="avatar_1.png",
        avatar_content_type="image/png",
        email="creator@gmail.com",
    )
    creator.signature_filename = "signature_1.png"
    creator.force_password_change = False
    creator.save(update_fields=["signature_filename", "force_password_change"])
    UserMFA.objects.create(
        user=creator,
        secret=pyotp.random_base32(),
        account="creator@gmail.com",
        enabled_at=timezone.now(),
    )

    response = _token(api, creator.username)
    assert response.status_code == 200, response.content
    assert response.json()["access"]


def test_deployment_command_flags_every_active_account(admin, creator):
    call_command("require_profile_refresh", verbosity=0)
    for user in (admin, creator):
        user.refresh_from_db()
        profile = UserProfile.objects.get(user=user)
        assert profile.onboarding_required is True
        assert profile.onboarding_completed_at is None
        assert user.force_password_change is True
