"""Per-account appearance preference (spec §1)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_default_is_system(as_user, creator):
    response = as_user(creator).get("/api/auth/preferences/")
    assert response.status_code == 200
    assert response.json() == {"theme": "system"}


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_theme_round_trips_and_is_returned_by_me(as_user, creator, theme):
    client = as_user(creator)
    saved = client.patch("/api/auth/preferences/", {"theme": theme}, format="json")
    assert saved.status_code == 200, saved.content
    assert saved.json()["theme"] == theme

    me = client.get("/api/auth/me/").json()
    assert me["preferences"]["theme"] == theme


def test_invalid_theme_is_rejected(as_user, creator):
    response = as_user(creator).patch(
        "/api/auth/preferences/", {"theme": "neon"}, format="json"
    )
    assert response.status_code == 400
    assert "theme" in response.json()["errors"]


def test_unknown_preferences_are_rejected(as_user, creator):
    response = as_user(creator).patch(
        "/api/auth/preferences/", {"theme": "dark", "role": "Administrator"}, format="json"
    )
    assert response.status_code == 400


def test_preference_is_per_account(as_user, creator, evaluator):
    as_user(creator).patch("/api/auth/preferences/", {"theme": "light"}, format="json")
    other = as_user(evaluator).get("/api/auth/preferences/").json()
    assert other["theme"] == "system"


def test_preferences_require_authentication(api):
    assert api.get("/api/auth/preferences/").status_code in (401, 403)
