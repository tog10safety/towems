"""Self-service profile editing (spec §3).

Every user may edit their own permitted fields and nothing else. The endpoint
has no user id, so a manipulated request cannot address another account, and
role/unit/permissions are not in the allow-list.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import UserProfile
from apps.core.factories import make_user

pytestmark = pytest.mark.django_db

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def csrf_token(api) -> str:
    api.get("/api/auth/csrf/")
    return api.cookies[settings.CSRF_COOKIE_NAME].value


def upload(api, path, field, content=PNG, name="image.png"):
    token = csrf_token(api)
    return api.post(
        path,
        {"avatar" if field == "avatar" else field: SimpleUploadedFile(name, content)},
        format="multipart",
        HTTP_X_CSRFTOKEN=token,
    )


def test_every_authenticated_user_can_read_their_profile(as_user, creator):
    response = as_user(creator).get("/api/auth/profile/")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == creator.username
    assert body["unit_id"] == creator.unit_id
    assert body["editable_fields"] == [
        "contact_number",
        "bio",
        "email",
        "sex",
        "date_of_birth",
        "blood_type",
        "home_address",
        "emergency_contact_name",
        "emergency_contact_number",
        "afsc_primary",
        "afsc_secondary",
        "afsc_tertiary",
        "highest_education",
        "civil_status",
    ]
    # No credential material of any kind is exposed.
    assert "password_hash" not in body
    assert "hash" not in response.content.decode().lower().replace("password_hash", "")


def test_user_updates_contact_and_bio(api, login, creator):
    login(creator)
    token = csrf_token(api)
    response = api.patch(
        "/api/auth/profile/",
        {"contact_number": "+63 917 555 0100", "bio": "Duty operations officer."},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["contact_number"] == "+63 917 555 0100"
    assert body["bio"] == "Duty operations officer."

    creator.refresh_from_db()
    assert creator.contact_number == "+63 917 555 0100"


@pytest.mark.parametrize("field", ["authority", "unit_id", "is_active", "username", "serial_number"])
def test_profile_cannot_change_role_unit_or_identity(api, login, creator, field):
    login(creator)
    token = csrf_token(api)
    response = api.patch(
        "/api/auth/profile/",
        {field: "Administrator" if field == "authority" else 1},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400
    assert "cannot be changed" in response.json()["detail"]

    creator.refresh_from_db()
    assert creator.authority == "Creator"


def test_profile_update_always_targets_the_caller(api, login, creator):
    """Supplying someone else's id cannot redirect the change."""
    other = make_user(unit=creator.unit, authority="Creator")
    login(creator)
    token = csrf_token(api)
    response = api.patch(
        "/api/auth/profile/?user_id=%s" % other.id,
        {"bio": "Not mine", "user_id": other.id},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400

    other.refresh_from_db()
    assert not UserProfile.objects.filter(user=other).exists()


def test_user_updates_personnel_details(api, login, creator):
    login(creator)
    token = csrf_token(api)
    response = api.patch(
        "/api/auth/profile/",
        {
            "email": "person@gmail.com",
            "sex": "Male",
            "date_of_birth": "1991-02-03",
            "blood_type": "O+",
            "home_address": "Butuan City",
            "emergency_contact_name": "Maria Person",
            "emergency_contact_number": "09171234567",
            "afsc_primary": "11F3",
            "afsc_secondary": "11G3",
            "afsc_tertiary": "11A3",
            "highest_education": "Bachelor's Degree",
            "civil_status": "Married",
        },
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["email"] == "person@gmail.com"
    assert body["date_of_birth"] == "1991-02-03"
    assert body["afsc_tertiary"] == "11A3"

    profile = UserProfile.objects.get(user=creator)
    assert profile.sex == "Male"
    assert profile.blood_type == "O+"
    assert profile.civil_status == "Married"


def test_self_service_cannot_change_service_dates(api, login, creator):
    login(creator)
    token = csrf_token(api)
    response = api.patch(
        "/api/auth/profile/",
        {"date_enlisted": "2010-01-01", "date_commissioned": "2012-01-01"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400
    assert "cannot be changed" in response.json()["detail"]


def test_onboarding_completes_after_password_picture_and_signature(api, login, creator):
    profile = UserProfile.objects.create(
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
    token = csrf_token(api)
    response = api.post(
        "/api/auth/onboarding/complete/",
        {},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["onboarding_required"] is False
    assert body["onboarding_completed_at"]

    profile.refresh_from_db()
    assert profile.onboarding_required is False
    assert profile.onboarding_completed_at is not None


def test_onboarding_is_blocked_without_account_address(api, login, creator):
    UserProfile.objects.create(
        user=creator,
        onboarding_required=True,
        avatar_filename="avatar_1.png",
        avatar_content_type="image/png",
    )
    creator.signature_filename = "signature_1.png"
    creator.force_password_change = False
    creator.save(update_fields=["signature_filename", "force_password_change"])

    login(creator)
    token = csrf_token(api)
    response = api.post(
        "/api/auth/onboarding/complete/",
        {},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400
    assert response.json()["errors"]["gaps"] == ["email"]


def test_onboarding_is_blocked_without_signature(api, login, creator):
    UserProfile.objects.create(
        user=creator,
        onboarding_required=True,
        avatar_filename="avatar_1.png",
        avatar_content_type="image/png",
    )
    creator.force_password_change = False
    creator.save(update_fields=["force_password_change"])

    login(creator)
    token = csrf_token(api)
    response = api.post(
        "/api/auth/onboarding/complete/",
        {},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400
    assert "signature" in response.json()["detail"].lower()


def test_invalid_contact_number_is_rejected(api, login, creator):
    login(creator)
    token = csrf_token(api)
    response = api.patch(
        "/api/auth/profile/",
        {"contact_number": "call me maybe"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400
    assert "contact_number" in response.json()["errors"]


def test_avatar_upload_replace_and_remove(api, login, creator):
    login(creator)
    first = upload(api, "/api/auth/profile/avatar/", "avatar")
    assert first.status_code == 200, first.content
    assert first.json()["avatar_url"]

    served = api.get("/api/auth/profile/avatar/")
    assert served.status_code == 200
    assert served["Content-Type"] == "image/png"
    served.close()  # release the file handle before replacement cleanup

    # A replacement replaces the filename; the old file is cleaned up.
    first_name = UserProfile.objects.get(user=creator).avatar_filename
    second = upload(api, "/api/auth/profile/avatar/", "avatar", name="new.png")
    assert second.status_code == 200
    second_name = UserProfile.objects.get(user=creator).avatar_filename
    assert second_name != first_name
    assert not (settings.LEGACY_UPLOAD_DIR / "avatars" / first_name).exists()

    removed = api.delete("/api/auth/profile/avatar/", HTTP_X_CSRFTOKEN=csrf_token(api))
    assert removed.status_code == 200
    assert api.get("/api/auth/profile/avatar/").status_code == 404


def test_the_shell_payload_carries_the_avatar_for_the_user_chip(api, login, creator):
    """The top bar shows the picture beside the name, so /auth/me/ needs it."""
    login(creator)
    assert api.get("/api/auth/me/").json()["avatar_url"] == ""

    assert upload(api, "/api/auth/profile/avatar/", "avatar").status_code == 200
    body = api.get("/api/auth/me/").json()
    assert body["avatar_url"].startswith("/api/auth/profile/avatar/?v=")
    # The version keeps a replacement from being served out of the cache.
    assert UserProfile.objects.get(user=creator).avatar_filename in body["avatar_url"]


def test_avatar_rejects_non_image_content(api, login, creator):
    login(creator)
    response = upload(
        api,
        "/api/auth/profile/avatar/",
        "avatar",
        content=b"<svg onload=alert(1)></svg>",
        name="evil.svg",
    )
    assert response.status_code == 400
    assert "PNG, JPEG or WebP" in response.json()["detail"]


def test_avatar_rejects_oversized_file(api, login, creator):
    login(creator)
    oversized = PNG + b"\x00" * (2 * 1024 * 1024)
    response = upload(api, "/api/auth/profile/avatar/", "avatar", content=oversized)
    assert response.status_code == 400
    assert "Maximum file size" in response.json()["detail"]


def test_signature_upload_writes_existing_column_and_serves(api, login, creator):
    login(creator)
    response = upload(api, "/api/auth/profile/signature/", "signature")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["has_signature"] is True
    assert body["signature_filename"].startswith(f"signature_{creator.id}_")

    creator.refresh_from_db()
    assert creator.signature_filename == body["signature_filename"]
    assert api.get("/api/auth/profile/signature/").status_code == 200

    removed = api.delete(
        "/api/auth/profile/signature/", HTTP_X_CSRFTOKEN=csrf_token(api)
    )
    assert removed.status_code == 200
    creator.refresh_from_db()
    assert not creator.signature_filename


def test_profile_requires_authentication(api):
    assert api.get("/api/auth/profile/").status_code in (401, 403)
    assert api.patch("/api/auth/profile/", {"bio": "x"}, format="json").status_code in (401, 403)


def test_another_users_avatar_is_not_served_outside_the_unit(as_user, creator, other_unit):
    from apps.core.factories import make_user

    outsider = make_user(unit=other_unit)
    response = as_user(creator).get(f"/api/personnel/{outsider.id}/avatar/")
    assert response.status_code == 404


def test_admin_officer_never_receives_credentials(as_user, unit):
    from apps.command.models import UnitAppointment

    officer = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=officer, role="admin_officer")

    response = as_user(officer).get("/api/personnel/")
    assert response.status_code == 200
    body = response.content.decode().lower()
    assert "password_hash" not in body
    assert "pbkdf2" not in body
    assert "werkzeug" not in body
