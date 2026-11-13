"""Authentication tests: legacy password compatibility, CSRF, sessions, JWT."""

from __future__ import annotations

import pytest
from django.conf import settings
from werkzeug.security import check_password_hash

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_health_endpoint_is_public(api):
    response = api.get("/api/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_csrf_endpoint_sets_cookie(api):
    response = api.get("/api/auth/csrf/")
    assert response.status_code == 200
    assert settings.CSRF_COOKIE_NAME in api.cookies


def test_login_with_legacy_werkzeug_hash(api, login, creator):
    # creator.password_hash was written by werkzeug.security.generate_password_hash
    assert creator.password_hash.startswith(("scrypt:", "pbkdf2:"))
    response = login(creator)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["username"] == creator.username
    assert body["permissions"]["is_admin"] is False
    assert body["permissions"]["can_plan_operations"] is True


def test_login_is_rejected_without_csrf_token(creator):
    # Django's test client sets csrf_processing_done unless CSRF checks are
    # explicitly enabled, so a strict client is required to prove the rule.
    from rest_framework.test import APIClient

    strict = APIClient(enforce_csrf_checks=True)
    response = strict.post(
        "/api/auth/login/",
        {"username": creator.username, "password": "Passw0rd!23"},
        format="json",
    )
    assert response.status_code == 403


def test_login_succeeds_with_valid_csrf_token(creator):
    from rest_framework.test import APIClient

    strict = APIClient(enforce_csrf_checks=True)
    assert strict.get("/api/auth/csrf/").status_code == 200
    token = strict.cookies[settings.CSRF_COOKIE_NAME].value
    response = strict.post(
        "/api/auth/login/",
        {"username": creator.username, "password": "Passw0rd!23"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content


def test_login_rejects_wrong_password(api, creator):
    from django.conf import settings as django_settings

    api.get("/api/auth/csrf/")
    token = api.cookies[django_settings.CSRF_COOKIE_NAME].value
    response = api.post(
        "/api/auth/login/",
        {"username": creator.username, "password": "wrong"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 401


def test_inactive_user_cannot_authenticate(api, unit):
    from apps.core.factories import PASSWORD, make_user

    inactive = make_user(unit=unit, is_active=False)
    api.get("/api/auth/csrf/")
    token = api.cookies[settings.CSRF_COOKIE_NAME].value
    response = api.post(
        "/api/auth/login/",
        {"username": inactive.username, "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 401


def test_malformed_hash_does_not_raise(api, unit):
    from apps.core.factories import PASSWORD, make_user

    broken = make_user(unit=unit, password_hash="not-a-valid-hash")
    api.get("/api/auth/csrf/")
    token = api.cookies[settings.CSRF_COOKIE_NAME].value
    response = api.post(
        "/api/auth/login/",
        {"username": broken.username, "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 401


def test_login_writes_legacy_audit_row(login, creator):
    from apps.legacy.models import UserActivity

    response = login(creator)
    assert response.status_code == 200
    assert UserActivity.objects.filter(
        user_id=creator.id, activity_type="login", module="auth"
    ).exists()


def test_me_returns_permissions_and_units(as_user, safety, unit):
    client = as_user(safety)
    response = client.get("/api/auth/me/")
    assert response.status_code == 200
    body = response.json()
    assert body["permissions"]["can_approve_operations"] is True
    assert body["permissions"]["can_view_sensitive_inventory"] is True
    assert unit.id in body["accessible_units"]


def test_me_requires_authentication(api):
    assert api.get("/api/auth/me/").status_code in (401, 403)


def test_jwt_token_obtain_works_for_legacy_credentials(api, creator):
    response = api.post(
        "/api/auth/token/",
        {"username": creator.username, "password": "Passw0rd!23"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert "access" in response.json()


def test_change_password_enforces_policy(as_user, creator):
    client = as_user(creator)
    response = client.post(
        "/api/auth/password/",
        {"current_password": "Passw0rd!23", "new_password": "weak"},
        format="json",
    )
    assert response.status_code in (400, 403)


def test_set_password_uses_werkzeug_format(creator):
    creator.set_password("N3w!Password")
    assert check_password_hash(creator.password_hash, "N3w!Password")
    assert User.objects.filter(pk=creator.pk).exists()
