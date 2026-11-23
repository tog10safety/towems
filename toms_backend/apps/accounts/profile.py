"""Self-service profile editing (every authenticated user).

Design rules taken straight from the specification:

* A user edits **only their own** record - the endpoint has no user id at all,
  so a manipulated request cannot reach another account.
* The permitted fields are an explicit allow-list. ``authority``, ``unit_id``,
  ``username``, ``serial_number`` and ``is_active`` are not on it, so role,
  unit and administrative status cannot be changed through this route.
* Passwords never appear: the existing ``ChangePasswordView`` handles them.
* ``password_hash`` is never serialised.
"""

from __future__ import annotations

import logging
import os
import uuid

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import mfa, setup_gate
from apps.accounts.models import UserProfile
from apps.accounts.personnel_details import (
    PERSONNEL_DETAIL_FIELDS,
    SELF_EDITABLE_DETAIL_FIELDS,
    profile_dict,
)
from apps.accounts.serializers import PersonnelDetailsSerializer
from apps.core import roles
from apps.core.audit import log_action
from apps.core.scoping import accessible_unit_ids

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB
ALLOWED_IMAGE_TYPES = {"png", "jpg", "jpeg", "webp"}

#: Only these may be changed by the account owner.  Enlistment and
#: commissioning dates remain administrative data, so they are excluded.
SELF_EDITABLE_FIELDS = (
    "contact_number",
    "bio",
    *SELF_EDITABLE_DETAIL_FIELDS,
)


def avatar_directory():
    path = settings.LEGACY_UPLOAD_DIR / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def signature_directory():
    path = settings.LEGACY_SIGNATURE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def sniff_image(head: bytes) -> str | None:
    """Identify an image from its magic bytes (no Pillow dependency)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    return None


def _read_upload(request, field: str) -> tuple[bytes, str]:
    upload = request.FILES.get(field)
    if upload is None:
        raise ValidationError({field: f"Attach a file using the '{field}' field."})
    if upload.size <= 0:
        raise ValidationError({field: "The uploaded file is empty."})
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            {field: f"Maximum file size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB."}
        )
    data = upload.read()
    kind = sniff_image(data[:16])
    if kind is None or (kind not in ALLOWED_IMAGE_TYPES and kind != "gif"):
        raise ValidationError(
            {field: "Upload a PNG, JPEG or WebP image."}
        )
    return data, ("jpg" if kind == "jpeg" else kind)


def _profile_for(user) -> UserProfile:
    profile, _created = UserProfile.objects.get_or_create(user_id=user.id)
    # Point the reverse one-to-one cache at the row just read, so a stale
    # instance created earlier in the same request cannot leak into the
    # response the caller sees right after saving.
    try:
        user._state.fields_cache["profile"] = profile
    except Exception:  # noqa: BLE001 - cache layout is an implementation detail
        pass
    return profile


def _safe_name(filename: str) -> str:
    """Reject anything that is not a bare filename inside the signatures dir."""
    if not filename:
        return ""
    if os.path.basename(filename) != filename or filename.startswith("."):
        return ""
    return filename


def avatar_url(user) -> str:
    """URL of the user's picture, or "" when none is set.

    The filename is appended as a version so a replacement picture is fetched
    immediately instead of being served from the browser cache.
    """
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return ""
    filename = getattr(profile, "avatar_filename", "") or ""
    if not filename:
        return ""
    return f"/api/auth/profile/avatar/?v={filename}"


def preferences_payload(user) -> dict:
    """Display preferences that follow the account across devices."""
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return {"theme": "system"}
    theme = profile.theme_preference or "system"
    if theme not in {"system", "light", "dark"}:
        theme = "system"
    return {"theme": theme}


def profile_payload(user) -> dict:
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        profile = None

    avatar_filename = getattr(profile, "avatar_filename", "") or ""
    saved_signature = _safe_name(user.signature_filename or "")
    sections = [
        {
            "id": row.section_id,
            "name": row.section.name,
            "code": row.section.code,
            "kind": row.section.kind,
            "assignment_type": row.assignment_type,
        }
        for row in user.section_assignments.select_related("section").filter(is_active=True)
    ]
    appointments = [
        {
            "id": row.id,
            "role": row.role,
            "role_label": roles.ROLE_LABELS.get(row.role, row.role),
            "unit_id": row.unit_id,
            "unit_acronym": row.unit.acronym if row.unit_id else "",
        }
        for row in user.appointments.select_related("unit").filter(is_active=True)
    ]

    setup_gaps = setup_gate.profile_gaps(user)
    mfa_state = mfa.get_state(user)

    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "rank": user.rank,
        "designation": user.designation,
        "authority": user.authority,
        "category": user.category,
        "serial_number": user.serial_number,
        "branch_of_service": user.branch_of_service,
        "unit_id": user.unit_id,
        "unit_name": user.unit.name if user.unit_id else "",
        "unit_acronym": user.unit.acronym if user.unit_id else "",
        "contact_number": user.contact_number or "",
        "bio": getattr(profile, "bio", "") or "",
        **profile_dict(profile),
        "avatar_url": f"/api/auth/profile/avatar/?v={avatar_filename}" if avatar_filename else "",
        "has_signature": bool(saved_signature),
        "signature_filename": saved_signature,
        "signature_url": f"/api/auth/profile/signature/" if saved_signature else "",
        "roles": sorted(roles.effective_roles(user)),
        "appointments": appointments,
        "sections": sections,
        "editable_fields": list(SELF_EDITABLE_FIELDS),
        "preferences": preferences_payload(user),
        "password_expiry_date": user.password_expiry_date,
        "password_change_required": user.needs_password_change(),
        "profile_setup_gaps": setup_gaps,
        "profile_setup_gap_labels": setup_gate.gap_labels(setup_gaps),
        "profile_email_suffix": setup_gate.required_email_suffix(),
        #: The account the authenticator entry was created under. Shown next to
        #: the profile address so a mismatch is visible at a glance.
        "authenticator_account": (mfa_state.account or "") if mfa_state else "",
    }


class ProfileView(APIView):
    """``GET`` the caller's profile, ``PATCH`` the fields they may change."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(profile_payload(request.user))

    def patch(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)

        rejected = sorted(
            key
            for key in request.data
            if key not in SELF_EDITABLE_FIELDS
            and key not in {"bio", "contact_number", "current_password"}
        )
        if rejected:
            raise ValidationError(
                {
                    "detail": (
                        "These fields cannot be changed through the profile: "
                        + ", ".join(rejected)
                        + ". Contact an administrator for role or unit changes."
                    )
                }
            )

        email = request.data.get("email")
        if email is not None and (email or "").strip() and not setup_gate.email_ok(email):
            suffix = setup_gate.required_email_suffix()
            raise ValidationError(
                {
                    "email": (
                        "Use the Google (Gmail) account you will sign in with"
                        + (f"; it must end with {suffix}." if suffix else ".")
                    )
                }
            )

        user = request.user
        profile = _profile_for(user)
        before = {
            "contact_number": user.contact_number or "",
            "bio": profile.bio or "",
            **profile_dict(profile),
        }
        changed: dict = {}

        if "contact_number" in request.data:
            value = (request.data.get("contact_number") or "").strip()
            if value and not value.replace("+", "").replace("-", "").replace(" ", "").isdigit():
                raise ValidationError(
                    {"contact_number": "Use digits, spaces, '+' or '-' only."}
                )
            if len(value) > 20:
                raise ValidationError({"contact_number": "Maximum 20 characters."})
            user.contact_number = value or None
            user.save(update_fields=["contact_number"])
            changed["contact_number"] = value

        bio = None
        if "bio" in request.data:
            bio = (request.data.get("bio") or "").strip()
            if len(bio) > 2000:
                raise ValidationError({"bio": "Maximum 2000 characters."})
            profile.bio = bio
            changed["bio"] = bio

        details_data = {
            field: request.data[field]
            for field in PERSONNEL_DETAIL_FIELDS
            if field in request.data and field in SELF_EDITABLE_FIELDS
        }
        if details_data:
            serializer = PersonnelDetailsSerializer(
                instance=profile, data=details_data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)
            for field, value in serializer.validated_data.items():
                setattr(profile, field, value)
                changed[field] = value.isoformat() if hasattr(value, "isoformat") else value

        profile.save()
        setup_gate.sync_session(user, getattr(request, "session", None))

        after = {
            "contact_number": user.contact_number or "",
            "bio": profile.bio or "",
            **profile_dict(profile),
        }
        diff = {key: after.get(key) for key in changed if before.get(key) != after.get(key)}
        log_action(
            actor=user,
            action="update_profile",
            entity_type="profile",
            entity_id=user.id,
            before={key: before.get(key) for key in diff},
            after=diff,
            request=request,
        )
        return Response(profile_payload(user))


class PreferencesView(APIView):
    """The caller's display preferences (appearance today, extensible)."""

    permission_classes = [IsAuthenticated]
    ALLOWED_THEMES = {"system", "light", "dark"}

    def get(self, request):
        return Response(preferences_payload(request.user))

    def patch(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        unknown = sorted(key for key in request.data if key != "theme")
        if unknown:
            raise ValidationError(
                {"detail": "Unknown preference(s): " + ", ".join(unknown) + "."}
            )

        theme = request.data.get("theme")
        if theme not in self.ALLOWED_THEMES:
            raise ValidationError(
                {"theme": "Choose 'system', 'light' or 'dark'."}
            )

        profile = _profile_for(request.user)
        before = profile.theme_preference
        profile.theme_preference = theme
        profile.save(update_fields=["theme_preference", "updated_at"])

        log_action(
            actor=request.user,
            action="update_preferences",
            entity_type="profile",
            entity_id=request.user.id,
            before={"theme": before},
            after={"theme": theme},
            request=request,
        )
        return Response(preferences_payload(request.user))


class OnboardingCompleteView(APIView):
    """Finish first sign-in setup after password, picture and signature exist.

    The administrator supplied the initial password, so the new account must
    replace it and add the picture and signature used by MRM certificates
    before the prompt is cleared.  Profile details remain editable afterwards.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        user = request.user
        profile = _profile_for(user)

        # One rule set, shared with the request gate and the profile page, so
        # the four required steps cannot drift apart.
        gaps = setup_gate.profile_gaps(user)
        if gaps:
            raise ValidationError(
                {
                    "detail": (
                        "Complete these steps before finishing setup: "
                        + ", ".join(setup_gate.gap_labels(gaps))
                        + "."
                    ),
                    "gaps": gaps,
                }
            )

        if profile.onboarding_required or not profile.onboarding_completed_at:
            profile.onboarding_required = False
            profile.onboarding_completed_at = timezone.now()
            profile.save(
                update_fields=[
                    "onboarding_required",
                    "onboarding_completed_at",
                    "updated_at",
                ]
            )
            log_action(
                actor=user,
                action="complete_profile_onboarding",
                entity_type="profile",
                entity_id=user.id,
                request=request,
            )

        # The profile is complete: the session moves on to authenticator setup.
        setup_gate.sync_session(user, getattr(request, "session", None))
        return Response(profile_payload(user))


class ProfileAvatarView(APIView):
    """Upload, replace, remove and stream the caller's profile picture."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return _serve_avatar(request.user)

    def post(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        data, extension = _read_upload(request, "avatar")

        profile = _profile_for(request.user)
        directory = avatar_directory()
        filename = f"avatar_{request.user.id}_{uuid.uuid4().hex[:10]}.{extension}"
        path = directory / filename
        with open(path, "wb") as handle:
            handle.write(data)

        previous = _safe_name(profile.avatar_filename or "")
        profile.avatar_filename = filename
        profile.avatar_content_type = f"image/{extension}"
        profile.save(update_fields=["avatar_filename", "avatar_content_type", "updated_at"])
        setup_gate.sync_session(request.user, getattr(request, "session", None))

        # Remove only files this endpoint created for this user.
        if previous and previous != filename and previous.startswith(f"avatar_{request.user.id}_"):
            try:
                (directory / previous).unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort cleanup
                logger.warning("Could not remove previous avatar %s", previous)

        log_action(
            actor=request.user,
            action="update_profile_avatar",
            entity_type="profile",
            entity_id=request.user.id,
            request=request,
        )
        return Response(profile_payload(request.user), status=status.HTTP_200_OK)

    def delete(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        profile = _profile_for(request.user)
        previous = _safe_name(profile.avatar_filename or "")
        profile.avatar_filename = ""
        profile.avatar_content_type = ""
        profile.save(update_fields=["avatar_filename", "avatar_content_type", "updated_at"])
        setup_gate.sync_session(request.user, getattr(request, "session", None))
        if previous and previous.startswith(f"avatar_{request.user.id}_"):
            try:
                (avatar_directory() / previous).unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass
        log_action(
            actor=request.user,
            action="remove_profile_avatar",
            entity_type="profile",
            entity_id=request.user.id,
            request=request,
        )
        return Response(profile_payload(request.user))


class ProfileSignatureView(APIView):
    """Upload or replace the signature image used on approved MRM PDFs."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return _serve_signature(request.user)

    def post(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        data, extension = _read_upload(request, "signature")

        filename = f"signature_{request.user.id}_{uuid.uuid4().hex[:10]}.{extension}"
        with open(signature_directory() / filename, "wb") as handle:
            handle.write(data)

        user = request.user
        user.signature_filename = filename
        user.save(update_fields=["signature_filename"])
        setup_gate.sync_session(user, getattr(request, "session", None))
        log_action(
            actor=user,
            action="update_profile_signature",
            entity_type="profile",
            entity_id=user.id,
            request=request,
        )
        return Response(profile_payload(user))

    def delete(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        user = request.user
        user.signature_filename = None
        user.save(update_fields=["signature_filename"])
        setup_gate.sync_session(user, getattr(request, "session", None))
        log_action(
            actor=user,
            action="remove_profile_signature",
            entity_type="profile",
            entity_id=user.id,
            request=request,
        )
        return Response(profile_payload(user))


def _serve_avatar(user):
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        raise Http404("No profile picture uploaded.")
    filename = _safe_name(profile.avatar_filename or "")
    if not filename:
        raise Http404("No profile picture uploaded.")
    path = avatar_directory() / filename
    if not path.exists():
        raise Http404("Profile picture file is missing.")
    return FileResponse(
        open(path, "rb"),
        content_type=profile.avatar_content_type or "application/octet-stream",
    )


def _serve_signature(user):
    filename = _safe_name(user.signature_filename or "")
    if not filename:
        raise Http404("No signature uploaded.")
    path = signature_directory() / filename
    if not path.exists():
        raise Http404("Signature file is missing.")
    return FileResponse(open(path, "rb"), content_type="image/png")


class UserAvatarView(APIView):
    """Stream another person's avatar to callers inside their unit scope."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk: int):
        from apps.accounts.models import User

        user = User.objects.filter(pk=pk).first()
        if user is None:
            raise Http404("Unknown person.")
        if user.id != request.user.id:
            allowed = accessible_unit_ids(request.user)
            if user.unit_id not in allowed:
                raise Http404("Unknown person.")
        return _serve_avatar(user)
