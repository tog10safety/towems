"""Authentication and personnel endpoints."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import update_session_auth_hash
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import device_sessions, mfa, ownership, setup_gate, unit_changes
from apps.accounts.models import AUTHORITY_CHOICES, CATEGORY_CHOICES, UnitChangeRequest, User
from apps.accounts.models import PasswordResetToken
from apps.accounts.serializers import (
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    MFACodeSerializer,
    MFAChallengeSerializer,
    PasswordResetCompleteSerializer,
    PersonnelWriteSerializer,
    UnitChangeRequestSerializer,
    UserSummarySerializer,
)
from apps.core import roles
from apps.core.audit import log_action
from apps.core.csrf import enforce_csrf
from apps.core.legacy_audit import record_activity
from apps.core.permissions import (
    ADMIN,
    UNIT_SAFETY,
    CanViewResources,
    IsUserAdministrator,
)
from apps.core.scoping import accessible_unit_ids, unit_ids_for_filter
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


def _preferences(user) -> dict:
    from apps.accounts.profile import preferences_payload

    return preferences_payload(user)


def _personnel_options(user, unit=None) -> dict:
    """Every value the personnel form and Excel template offer as a picker.

    ``unit`` builds the template for one unit's chain: only that unit and its
    subordinate units are offered as unit values.
    """
    from apps.accounts.personnel_details import (
        BLOOD_TYPE_CHOICES,
        BRANCH_OPTIONS,
        CIVIL_STATUS_CHOICES,
        DESIGNATION_OPTIONS,
        HIGHEST_EDUCATION_CHOICES,
        RANK_OPTIONS,
        RETIRED_DESIGNATIONS,
        SEX_CHOICES,
    )
    from apps.legacy.models import Unit

    unit_ids = unit_ids_for_filter(user)
    units = Unit.objects.filter(is_active=True).order_by("name")
    if unit is not None:
        from apps.command.services import descendant_ids

        units = units.filter(id__in=descendant_ids(unit.id))
    elif unit_ids is not None:
        units = units.filter(id__in=unit_ids)

    designation_query = (
        User.objects.filter(is_active=True)
        .exclude(designation__isnull=True)
        .exclude(designation="")
        .values_list("designation", flat=True)
        .distinct()
    )
    if unit_ids is not None:
        designation_query = designation_query.filter(unit_id__in=unit_ids)
    designations = list(DESIGNATION_OPTIONS)
    # A value already held by a record is normally offered too, so legacy
    # designations stay selectable. Retired ones are the exception: they must
    # not reappear in the picker just because an old record still carries them.
    retired = {value.casefold() for value in RETIRED_DESIGNATIONS}
    seen = {value.casefold() for value in designations}
    for value in designation_query.order_by("designation"):
        key = value.casefold()
        if key in retired or key in seen:
            continue
        seen.add(key)
        designations.append(value)

    branch_query = (
        User.objects.filter(is_active=True)
        .exclude(branch_of_service__isnull=True)
        .exclude(branch_of_service="")
        .values_list("branch_of_service", flat=True)
        .distinct()
    )
    branches = list(BRANCH_OPTIONS)
    for value in branch_query.order_by("branch_of_service"):
        if value not in branches:
            branches.append(value)

    return {
        "account_roles": [value for value, _label in AUTHORITY_CHOICES],
        "categories": [value for value, _label in CATEGORY_CHOICES],
        "designations": designations,
        "ranks": list(RANK_OPTIONS),
        "branches": branches,
        "sexes": [value for value, _label in SEX_CHOICES],
        "blood_types": [value for value, _label in BLOOD_TYPE_CHOICES],
        "educations": [value for value, _label in HIGHEST_EDUCATION_CHOICES],
        "civil_statuses": [value for value, _label in CIVIL_STATUS_CHOICES],
        "units": [
            {
                "id": unit.id,
                "name": unit.name,
                "acronym": unit.acronym,
                "label": f"{unit.acronym} — {unit.name}",
            }
            for unit in units
        ],
        "password_policy": {
            "min_length": 8,
            "requirements": "Upper case, lower case, a digit and a special character.",
        },
    }


def _permissions_payload(user) -> dict:
    """Capability flags for the SPA.

    Derived from :mod:`apps.core.roles`, which merges the legacy base role with
    active unit appointments and acting delegations. The frontend uses these to
    hide what it must not offer; the server re-checks every one of them.
    """
    return roles.permissions_payload(user)


def _user_payload(request, user) -> dict:
    """The signed-in account payload shared by login, /me and MFA verification."""
    payload = UserSummarySerializer(user, context={"request": request}).data
    payload["permissions"] = _permissions_payload(user)
    payload["preferences"] = _preferences(user)
    from apps.accounts.profile import avatar_url

    payload["avatar_url"] = avatar_url(user)
    payload["password_change_required"] = user.needs_password_change()
    payload["accessible_units"] = sorted(accessible_unit_ids(user))
    # First-sign-in gates: the profile refresh, then authenticator enrolment.
    payload.update(setup_gate.status_payload(user, request))
    return payload


def _complete_login(
    request,
    user,
    *,
    verified: bool = True,
    enrollment_required: bool = False,
) -> dict:
    """Create the session and write both audit trails.

    ``verified=False`` marks a session whose second factor has not been
    presented yet. ``setup_gate`` then decides which first-sign-in gate the
    session is in: the profile refresh, or authenticator enrolment once the
    profile is complete.
    """
    login(request, user)
    session = request.session
    # A Creator account may hold one session at a time. The check is made after
    # ``login`` so the key of *this* session is excluded from the comparison, and
    # the session is flushed again if another device already holds one.
    conflict = device_sessions.register_login(
        user,
        request,
        session.session_key,
        verified=verified,
        enrollment_required=enrollment_required,
    )
    if conflict is not None:
        session.flush()
        raise device_sessions.DeviceConflict(conflict)

    session["mfa_verified"] = verified
    if enrollment_required:
        session["mfa_enrollment_required"] = True
    else:
        session.pop("mfa_enrollment_required", None)
    setup_gate.sync_session(user, session)

    log_action(
        actor=user,
        action="login",
        entity_type="auth",
        entity_id=user.id,
        request=request,
    )
    record_activity(
        user=user,
        activity_type="login",
        module="auth",
        description=f"{user.username} signed in",
        request=request,
    )
    return _user_payload(request, user)


class CsrfTokenView(APIView):
    """Seed the CSRF cookie for the SPA before any POST."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request):
        # Login is reachable while anonymous, so CSRF must be checked here.
        enforce_csrf(request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Invalid username or password.", "errors": {}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        state = mfa.get_state(user)

        # An enrolled account never receives a session from the password alone.
        if mfa.is_enabled(state):
            return Response(
                {
                    "mfa_required": True,
                    "challenge": mfa.issue_challenge(user),
                    "expires_in": mfa.CHALLENGE_MAX_AGE,
                    "username": user.username,
                    "recovery_codes_remaining": mfa.recovery_codes_remaining(state),
                }
            )

        # Every active account presents a second factor. An unenrolled account
        # is held in the first-sign-in setup flow: the profile refresh first,
        # then Google Authenticator enrolment, with no grace period.
        if mfa.requires_mfa(user):
            mfa.ensure_state(user)
            return Response(
                _complete_login(
                    request, user, verified=False, enrollment_required=True
                )
            )

        return Response(_complete_login(request, user))


class MFAChallengeView(APIView):
    """Exchange a password challenge plus a TOTP/recovery code for a session."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "login"

    def post(self, request):
        enforce_csrf(request)
        serializer = MFAChallengeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = mfa.read_challenge(serializer.validated_data["challenge"])
        state = mfa.get_state(user) if user is not None else None
        if user is None or not mfa.is_enabled(state):
            return Response(
                {
                    "detail": "This sign-in challenge is invalid or has expired. Sign in again.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        recovery = serializer.validated_data["recovery"]
        if not mfa.verify_login_code(
            state, serializer.validated_data["code"], recovery=recovery
        ):
            return Response(
                {
                    "detail": (
                        "That recovery code is not valid or has already been used."
                        if recovery
                        else "That authenticator code is not valid."
                    ),
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if recovery:
            log_action(
                actor=user,
                action="mfa_recovery_login",
                entity_type="auth",
                entity_id=user.id,
                note="Signed in with a one-time recovery code",
                request=request,
            )
        payload = _complete_login(request, user)
        payload["used_recovery_code"] = recovery
        return Response(payload)


class MFAStatusView(APIView):
    """Enrolment state for the current account, used by the setup page."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = mfa.status_payload(request.user, request)
        payload["setup_available"] = payload["mfa_required"] and not payload["mfa_enabled"]
        return Response(payload)


class MFASetupView(APIView):
    """Start (or resume) enrolment by returning a fresh provisioning secret."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "login"

    def post(self, request):
        enforce_csrf(request)
        if not mfa.requires_mfa(request.user):
            return Response(
                {"detail": "Multi-factor authentication is not required for this account.", "errors": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            payload = mfa.begin_setup(request.user)
        except mfa.MFAError as exc:
            return Response(
                {"detail": str(exc), "errors": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload.update(mfa.status_payload(request.user, request))
        return Response(payload)


class MFAVerifySetupView(APIView):
    """Confirm the first authenticator code and show recovery codes once."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "login"

    def post(self, request):
        enforce_csrf(request)
        serializer = MFACodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            codes = mfa.complete_setup(
                request.user,
                serializer.validated_data["code"],
            )
        except mfa.MFAError as exc:
            return Response(
                {"detail": str(exc), "errors": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.session["mfa_verified"] = True
        request.session.pop("mfa_setup_required", None)
        request.session.pop("mfa_enrollment_required", None)
        payload = _user_payload(request, request.user)
        payload["recovery_codes"] = codes
        return Response(payload)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        log_action(
            actor=user, action="logout", entity_type="auth", entity_id=user.id, request=request
        )
        record_activity(
            user=user,
            activity_type="logout",
            module="auth",
            description=f"{user.username} signed out",
            request=request,
        )
        # Capture the key first: ``logout`` flushes the session it belongs to.
        session_key = request.session.session_key
        logout(request)
        device_sessions.forget(session_key)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceSwitchView(APIView):
    """End the session on the other device and continue signing in here.

    Reached from the dialog the SPA shows when a single-device account is already
    signed in elsewhere. The token is the one returned with the 409, so it can
    only be used after the password (and authenticator, when enrolled) has been
    verified, and only for a few minutes.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "login"

    def post(self, request):
        enforce_csrf(request)
        state = device_sessions.read_conflict(request.data.get("challenge") or "")
        if state is None:
            return Response(
                {
                    "detail": "This confirmation has expired. Sign in again.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        user, verified, enrollment_required = state
        device_sessions.end_others(user)
        log_action(
            actor=user,
            action="device_switch",
            entity_type="auth",
            entity_id=user.id,
            note="Signed out the other device to continue here",
            request=request,
        )
        return Response(
            _complete_login(
                request,
                user,
                verified=verified,
                enrollment_required=enrollment_required,
            )
        )


class DeviceSessionListView(APIView):
    """The devices this account is signed in on (the self-service view)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        key = request.session.session_key
        current = []
        others = device_sessions.other_devices(request.user)
        payload = {
            "single_device": device_sessions.requires_single_device(request.user),
            "current": key[:8] if key else "",
            "devices": others,
        }
        return Response(payload)


class SetupAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Issue an API token only to an account that has finished its setup.

    Token clients never pass through :class:`MFAEnforcementMiddleware`, so the
    first-sign-in gates have to be enforced here. Without this, a password alone
    buys a JWT that skips the profile refresh and Google Authenticator
    enrolment entirely.
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        gaps = setup_gate.profile_gaps(user)
        if gaps:
            raise PermissionDenied(
                {
                    "detail": (
                        "Finish your account setup (new password, picture, "
                        "signature and Gmail account) before using API tokens."
                    ),
                    "code": "profile_setup_required",
                    "gaps": gaps,
                }
            )
        if setup_gate.mfa_outstanding(user):
            raise PermissionDenied(
                {
                    "detail": (
                        "Set up Google Authenticator before using API tokens."
                    ),
                    "code": "mfa_setup_required",
                }
            )
        return data


class SetupAwareTokenObtainPairView(TokenObtainPairView):
    serializer_class = SetupAwareTokenObtainPairSerializer


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        # Recomputed here so a finished setup step clears its gate on the next
        # page load instead of persisting until the session expires.
        setup_gate.sync_session(user, getattr(request, "session", None))
        return Response(_user_payload(request, user))


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        enforce_csrf(request)
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            return Response(
                {"detail": "Current password is incorrect.", "errors": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data["new_password"])
        user.save()
        update_session_auth_hash(request, user)
        setup_gate.sync_session(user, getattr(request, "session", None))
        log_action(
            actor=user,
            action="change_password",
            entity_type="auth",
            entity_id=user.id,
            request=request,
        )
        record_activity(
            user=user,
            activity_type="password_changed",
            module="auth",
            description="Password changed",
            request=request,
        )
        return Response({"detail": "Password updated."})


def _reset_token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _valid_reset_token(raw: str):
    """Return the live token row for ``raw`` or ``None``.

    Used by both the validation and completion routes so their expiry/used
    rules cannot drift apart.
    """
    if not raw:
        return None
    token = (
        PasswordResetToken.objects.select_related("user")
        .filter(token_hash=_reset_token_digest(raw))
        .first()
    )
    if token is None or token.used_at is not None:
        return None
    if token.expires_at <= timezone.now():
        return None
    if not token.user.is_active:
        return None
    return token


class PasswordResetView(APIView):
    """Complete an administrator-issued password reset.

    Anonymous by design: the user cannot sign in (that is why the reset was
    issued). The token is single-use, expires, and is stored only as a digest.
    No endpoint can read an existing password.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        token = _valid_reset_token(request.query_params.get("token", ""))
        return Response(
            {"valid": token is not None, "expires_at": token.expires_at if token else None}
        )

    def post(self, request):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        serializer = PasswordResetCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = _valid_reset_token(serializer.validated_data["token"])
        if token is None:
            return Response(
                {
                    "detail": "This reset link is invalid, expired or already used. Ask an administrator for a new one.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = token.user
        user.set_password(serializer.validated_data["new_password"])
        user.force_password_change = False
        user.save()

        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])

        log_action(
            actor=user,
            action="complete_password_reset",
            entity_type="auth",
            entity_id=user.id,
            note="Password set through a single-use reset link",
            request=request,
        )
        record_activity(
            user=user,
            activity_type="password_changed",
            module="auth",
            description=f"{user.username} completed an administrator password reset",
            request=request,
        )
        return Response({"detail": "Password updated. Sign in with your new password."})


class RoleCatalogueView(APIView):
    """The authoritative list of assignable roles.

    Every role the system enforces is described here - label, whether it is an
    account role or a unit appointment, the data scope it confers, whether it
    is capped at one holder per unit, and the capabilities it grants. The admin
    interface renders its pickers from this payload, so the frontend never
    carries a hardcoded role list again.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "roles": roles.role_catalogue(),
                "capability_labels": roles.CAPABILITY_LABELS,
                "exclusive_roles": list(roles.EXCLUSIVE_APPOINTMENT_ROLES),
            }
        )


class UnitChangeRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """The Administrator's queue of unit transfers awaiting a decision.

    A person asks for a move on their own profile; it is never applied there.
    An Administrator or the Super Administrator approves or declines it, and
    the decision applies the transfer (approve) or leaves the account untouched
    (decline). Requests are never deleted: the row is the record of who asked,
    who decided, when and why.
    """

    permission_classes = [IsAuthenticated, IsUserAdministrator]
    serializer_class = UnitChangeRequestSerializer
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = ["status"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        return UnitChangeRequest.objects.select_related(
            "requester", "from_unit", "to_unit", "decided_by"
        )

    def _decide(self, request, pk, *, approve: bool):
        enforce_csrf(request)
        row = self.get_object()
        note = (request.data.get("note") or "").strip()
        try:
            if approve:
                row = unit_changes.approve_request(
                    row, actor=request.user, note=note, request=request
                )
            else:
                row = unit_changes.reject_request(
                    row, actor=request.user, note=note, request=request
                )
        except unit_changes.UnitChangeError as exc:
            raise ValidationError({"detail": str(exc)})
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, pk, approve=True)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, pk, approve=False)


class UnitChangeCancelView(APIView):
    """Withdraw the caller's own waiting unit change request.

    There is no id in the path, so an account can only ever cancel the request
    it raised. Nothing about the unit, the posts or the role changes: the ask
    simply stops waiting, and its bell item closes. Picking the wrong unit is
    the ordinary reason, so no reason is demanded.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.accounts.profile import profile_payload
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        try:
            unit_changes.cancel_request(request.user, request=request)
        except unit_changes.UnitChangeError as exc:
            raise ValidationError({"detail": str(exc)})

        payload = profile_payload(request.user)
        payload["unit_change_cancelled"] = True
        return Response(payload)


class UserAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """Administrator user management (spec §3).

    Read-only plus PATCH; deactivation, reactivation and password resets are
    explicit actions so each one can require a reason and be audited. Accounts
    and their historical records are never deleted.
    """

    permission_classes = [IsAuthenticated, IsUserAdministrator]
    serializer_class = AdminUserSerializer
    # POST is enabled only for the explicit custom actions below; the viewset
    # has no create route, so the collection remains read-only.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["authority", "is_active", "unit", "category"]
    search_fields = ["username", "first_name", "middle_name", "last_name", "serial_number", "designation"]
    ordering_fields = ["last_name", "username", "date_created"]
    ordering = ["last_name", "first_name"]

    def get_queryset(self):
        queryset = (
            User.objects.all()
            .select_related("unit", "profile")
            .prefetch_related(
                "appointments__unit",
                "acting_appointments",
                "section_assignments__section__unit",
            )
        )
        return self._filter_setup(queryset)

    def _filter_setup(self, queryset):
        """``?setup=`` narrows the register to accounts that still owe a step.

        ``profile``      the deployment profile refresh is not finished
        ``mfa``          the authenticator is not enrolled on a rollout account
        ``outstanding``  either of the above (the chase list)

        The MFA clause mirrors :func:`apps.accounts.setup_gate.mfa_outstanding`:
        an account flagged for the refresh, or one that has finished it, must
        carry a second factor before it can use TOWEMS.
        """
        mode = (self.request.query_params.get("setup") or "").strip().lower()
        if mode not in {"profile", "mfa", "outstanding"}:
            return queryset

        refresh_due = Q(
            profile__onboarding_required=True,
            profile__onboarding_completed_at__isnull=True,
        )
        in_rollout = Q(profile__onboarding_required=True) | Q(
            profile__onboarding_completed_at__isnull=False
        )
        mfa_due = Q(mfa__enabled_at__isnull=True) & in_rollout

        if mode == "profile":
            condition = refresh_due
        elif mode == "mfa":
            condition = mfa_due
        else:
            condition = refresh_due | mfa_due
        return queryset.filter(is_active=True).filter(condition).distinct()

    def _snapshot(self, user) -> dict:
        return {
            "first_name": user.first_name,
            "middle_name": user.middle_name,
            "last_name": user.last_name,
            "rank": user.rank,
            "designation": user.designation,
            "contact_number": user.contact_number,
            "authority": user.authority,
            "category": user.category,
            "unit_id": user.unit_id,
        }

    def destroy(self, request, pk=None):
        """Permanently delete an account (Super Administrator only).

        The reason comes in the request body. Every guard lives in
        ``apps.accounts.purge``: the seat, the history check and the seat-holder
        protection, so this route cannot be the weak one.
        """
        from apps.accounts import purge
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        target = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        username = target.username
        try:
            purge.delete_account(
                target, actor=request.user, reason=reason, request=request
            )
        except purge.PurgeError as exc:
            raise ValidationError({"detail": str(exc)})

        record_activity(
            user=request.user,
            activity_type="user_deleted",
            module="users",
            description=f"Deleted account @{username}: {reason}",
            request=request,
            resource_type="user",
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def partial_update(self, request, pk=None):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        target = self.get_object()

        if target.id == request.user.id and "authority" in request.data:
            raise ValidationError(
                {"authority": "You cannot change your own role. Ask another administrator."}
            )

        if (
            request.data.get("authority") == ADMIN
            and target.authority != ADMIN
            and not ownership.can_grant_administrator(request.user)
        ):
            raise ValidationError(
                {
                    "authority": (
                        "Only the Super Administrator may create or promote an "
                        "Administrator account."
                    )
                }
            )

        if (
            "authority" in request.data
            and request.data.get("authority") != ADMIN
            and ownership.is_system_owner(target)
        ):
            raise ValidationError(
                {
                    "authority": (
                        "Hand the Super Administrator seat over before changing "
                        "this account's role."
                    )
                }
            )

        serializer = AdminUserUpdateSerializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = self._snapshot(target)
        serializer.save()

        if "unit_id" in request.data:
            from apps.legacy.models import Unit

            target.unit_legacy = (
                Unit.objects.filter(pk=target.unit_id).values_list("name", flat=True).first()
                or ""
            )
            target.save(update_fields=["unit_legacy"])

        after = self._snapshot(target)
        changed = {key: value for key, value in after.items() if before.get(key) != value}
        log_action(
            actor=request.user,
            action="update_user_account",
            entity_type="user",
            entity_id=target.id,
            before={key: before[key] for key in changed},
            after=changed,
            request=request,
        )
        return Response(AdminUserSerializer(target).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        target = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if len(reason) < 3:
            raise ValidationError({"reason": "Record why the account is being deactivated."})
        if target.id == request.user.id:
            raise ValidationError({"detail": "You cannot deactivate your own account."})
        if ownership.is_system_owner(target):
            raise ValidationError(
                {
                    "detail": (
                        "Hand the Super Administrator seat over before "
                        "deactivating this account."
                    )
                }
            )
        if not target.is_active:
            raise ValidationError({"detail": "This account is already inactive."})

        active_admins = User.objects.filter(authority=ADMIN, is_active=True).count()
        if target.authority == ADMIN and active_admins <= 1:
            raise ValidationError(
                {"detail": "The last active Administrator cannot be deactivated."}
            )

        target.is_active = False
        target.save(update_fields=["is_active"])
        log_action(
            actor=request.user,
            action="deactivate_user",
            entity_type="user",
            entity_id=target.id,
            before={"is_active": True},
            after={"is_active": False},
            note=reason,
            request=request,
        )
        record_activity(
            user=request.user,
            activity_type="user_deactivated",
            module="users",
            description=f"Deactivated account @{target.username}: {reason}",
            request=request,
            resource_id=target.id,
            resource_type="user",
        )
        return Response(AdminUserSerializer(target).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        target = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if target.is_active:
            raise ValidationError({"detail": "This account is already active."})

        target.is_active = True
        target.save(update_fields=["is_active"])
        log_action(
            actor=request.user,
            action="reactivate_user",
            entity_type="user",
            entity_id=target.id,
            before={"is_active": False},
            after={"is_active": True},
            note=reason,
            request=request,
        )
        record_activity(
            user=request.user,
            activity_type="user_reactivated",
            module="users",
            description=f"Reactivated account @{target.username}" + (f": {reason}" if reason else ""),
            request=request,
            resource_id=target.id,
            resource_type="user",
        )
        return Response(AdminUserSerializer(target).data)

    @action(detail=True, methods=["post"])
    def handover(self, request, pk=None):
        """Hand the single Super Administrator seat to another account.

        Only the current holder may do this. The successor must be an active
        Administrator, and the seat moves in one transaction so it is never
        momentarily vacant. The reason is part of the permanent audit record.
        """
        enforce_csrf(request)
        target = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        try:
            ownership.hand_over(
                target, actor=request.user, reason=reason, request=request
            )
        except ownership.SystemOwnerError as exc:
            raise ValidationError({"detail": str(exc)})

        # ``get_object`` cached the profile row before the seat moved.
        target = self.get_queryset().get(pk=target.pk)

        record_activity(
            user=request.user,
            activity_type="system_owner_handover",
            module="users",
            description=f"Handed the Super Administrator seat to @{target.username}",
            request=request,
            resource_id=target.id,
            resource_type="user",
        )
        return Response(AdminUserSerializer(target).data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        """Issue a single-use reset link. No password value is returned."""
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        target = self.get_object()
        if not target.is_active:
            raise ValidationError(
                {"detail": "Reactivate the account before issuing a password reset."}
            )

        minutes = int(getattr(settings, "PASSWORD_RESET_TOKEN_MINUTES", 60))
        now = timezone.now()
        # Retire previous unused links so only the newest one works.
        PasswordResetToken.objects.filter(user_id=target.id, used_at__isnull=True).update(
            expires_at=now
        )

        raw_token = secrets.token_urlsafe(32)
        token = PasswordResetToken.objects.create(
            user_id=target.id,
            token_hash=_reset_token_digest(raw_token),
            created_by=request.user,
            expires_at=now + dt.timedelta(minutes=minutes),
            requested_ip=request.META.get("REMOTE_ADDR", "")[:45],
        )
        log_action(
            actor=request.user,
            action="issue_password_reset",
            entity_type="user",
            entity_id=target.id,
            after={"expires_at": token.expires_at.isoformat()},
            note="Single-use reset link issued; token value not logged",
            request=request,
        )
        record_activity(
            user=request.user,
            activity_type="password_reset_issued",
            module="users",
            description=f"Issued a password reset link for @{target.username}",
            request=request,
            resource_id=target.id,
            resource_type="user",
        )
        return Response(
            {
                "detail": "Reset link issued. Hand it to the user through an approved channel; it is shown once.",
                "reset_url": f"/reset-password?token={raw_token}",
                "expires_at": token.expires_at,
                "username": target.username,
            }
        )

    @action(detail=True, methods=["post"], url_path="reset-mfa")
    def reset_mfa(self, request, pk=None):
        """Clear a privileged account's TOTP enrolment and restart its window.

        The reason is mandatory and lands in both audit trails. The account can
        enrol again without waiting for an administrator, but only inside a new
        14-day window; after that it is setup-only until it does.
        """
        enforce_csrf(request)
        target = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        try:
            mfa.reset_for_user(
                target,
                actor=request.user,
                reason=reason,
                request=request,
            )
        except mfa.MFAError as exc:
            raise ValidationError({"reason": str(exc)})

        target = self.get_queryset().get(pk=target.pk)
        return Response(
            AdminUserSerializer(target, context={"request": request}).data
        )


class PersonnelViewSet(viewsets.ModelViewSet):
    """Personnel records live in the existing ``users`` table."""

    permission_classes = [IsAuthenticated]
    filterset_fields = ["authority", "category", "unit", "is_active"]
    search_fields = ["username", "first_name", "last_name", "serial_number", "designation"]
    ordering_fields = ["last_name", "date_created", "serial_number"]
    ordering = ["last_name", "first_name"]

    def get_queryset(self):
        queryset = (
            User.objects.all()
            .select_related("unit", "profile")
            .prefetch_related("section_assignments__section")
        )
        ids = unit_ids_for_filter(self.request.user)
        if ids is not None:
            queryset = queryset.filter(unit_id__in=ids)
        return queryset

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return PersonnelWriteSerializer
        return UserSummarySerializer

    def get_permissions(self):
        if self.action in {
            "create",
            "update",
            "partial_update",
            "destroy",
            "import_personnel",
            "import_template",
        }:
            from apps.core.permissions import IsUnitSafetyOrAdministrator

            return [IsAuthenticated(), IsUnitSafetyOrAdministrator()]
        # The Personnel register is a Resources module and is withheld from the
        # Creator account. The ``assignable`` rosters MRM planning still needs
        # are separate actions and stay open.
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated(), CanViewResources()]
        return [IsAuthenticated()]

    def _scope_unit(self, source):
        """Resolve the optional ``unit`` parameter to one manageable unit."""
        raw = ""
        if source is not None and hasattr(source, "get"):
            raw = str(source.get("unit") or "").strip()
        if not raw:
            return None
        try:
            unit_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({"unit": "A numeric unit id is required."})
        from apps.legacy.models import Unit

        unit = Unit.objects.filter(pk=unit_id, is_active=True).first()
        if unit is None:
            raise ValidationError({"unit": "Unknown unit."})
        if unit.id not in accessible_unit_ids(self.request.user):
            raise PermissionDenied("That unit is outside your authorised scope.")
        return unit

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        user = self.get_queryset().get(pk=response.data["id"])
        response.data = UserSummarySerializer(user).data
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        user = self.get_queryset().get(pk=response.data["id"])
        response.data = UserSummarySerializer(user).data
        return response

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="personnel",
            entity_id=user.pk,
            after={"username": user.username, "authority": user.authority},
            request=self.request,
        )
        record_activity(
            user=self.request.user,
            activity_type="user_created",
            module="users",
            description=f"Created personnel {user.username}",
            request=self.request,
            resource_id=user.id,
            resource_type="user",
        )

    def perform_update(self, serializer):
        user = serializer.save()
        log_action(
            actor=self.request.user,
            action="update",
            entity_type="personnel",
            entity_id=user.pk,
            after={"username": user.username, "authority": user.authority},
            request=self.request,
        )

    def destroy(self, request, *args, **kwargs):
        """Deactivate, or erase for the Super Administrator.

        ``?purge=true`` deletes an account that was opened in error. It is the
        seat holder's alone; every other caller keeps the documented behaviour
        below, which deactivates and preserves history.
        """
        instance = self.get_object()
        if str(request.query_params.get("purge", "")).lower() in {"1", "true", "yes"}:
            from apps.accounts import purge

            try:
                purge.delete_account(
                    instance,
                    actor=request.user,
                    reason=(request.data.get("reason") or ""),
                    request=request,
                )
            except purge.PurgeError as exc:
                raise ValidationError({"detail": str(exc)})
            return Response(status=status.HTTP_204_NO_CONTENT)
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Personnel records are never deleted: deactivation preserves mission
        # history and audit references. The reason is optional here for
        # backwards compatibility, but recorded when the caller supplies it.
        reason = ""
        if isinstance(self.request.data, dict):
            reason = (self.request.data.get("reason") or "").strip()
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        log_action(
            actor=self.request.user,
            action="deactivate",
            entity_type="personnel",
            entity_id=instance.pk,
            before={"is_active": True},
            after={"is_active": False},
            note=reason,
            request=self.request,
        )

    @action(detail=False, methods=["get"], url_path="options")
    def personnel_options(self, request):
        """Picker values for the registration form and the Excel template."""
        return Response(_personnel_options(request.user))

    @action(detail=False, methods=["get"], url_path="import-template")
    def import_template(self, request):
        """Download the .xlsx template, scoped to one unit's chain.

        ``?unit=ID`` limits the Unit dropdown - and the import made from this
        template - to that unit and its subordinate units.
        """
        from apps.accounts.excel import build_personnel_template

        unit = self._scope_unit(request.query_params)
        content = build_personnel_template(
            _personnel_options(request.user, unit=unit),
            scope_label=(
                f"{unit.acronym} ({unit.name}) and its subordinate units"
                if unit is not None
                else ""
            ),
        )
        response = HttpResponse(
            content,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        slug = f"-{unit.acronym}" if unit is not None else ""
        response["Content-Disposition"] = (
            f'attachment; filename="TOWEMS-personnel-registration-template{slug}.xlsx"'
        )
        response["Content-Length"] = str(len(content))
        return response

    @action(detail=False, methods=["post"], url_path="import")
    def import_personnel(self, request):
        """Bulk-register personnel from the downloadable .xlsx template."""
        from apps.accounts.excel import import_personnel_workbook
        from apps.core.csrf import enforce_csrf

        enforce_csrf(request)
        result = import_personnel_workbook(
            request.FILES.get("file"),
            (request.data.get("password") or "").strip(),
            request.user,
            scope_unit=self._scope_unit(request.data),
        )
        if result["errors"]:
            return Response(
                {
                    "detail": "The workbook was not imported. Fix the reported rows and try again.",
                    **result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def assignable(self, request):
        """Active personnel available for assignment, scoped by unit.

        Two selector contracts, both enforced here because the frontend cannot
        be trusted to filter:

        * ``?mrm_unit=ID`` - MRM team candidates: the unit itself, its HQ chain
          and its subordinates (used by the MRM team editor).
        * ``?unit=ID&purpose=evaluator|creator`` - personnel *assigned to that
          one unit*, narrowed by the authority the purpose requires. The
          mission planning form uses this so an evaluator from a sister or
          subordinate unit can never be offered, or accepted, for another
          unit's mission.
        """
        from apps.core import roles as role_rules
        from apps.core.scoping import accessible_unit_ids

        queryset = self.get_queryset().filter(is_active=True).select_related("unit")
        purpose = (request.query_params.get("purpose") or "").strip().lower()
        if purpose and purpose not in {"creator", "evaluator"}:
            raise ValidationError({"purpose": "Use 'creator' or 'evaluator'."})

        def _int_param(name):
            raw = request.query_params.get(name)
            if not raw:
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                raise ValidationError({name: "A numeric unit id is required."})

        exact_unit = _int_param("unit")
        mrm_unit = _int_param("mrm_unit")
        capability_unit = exact_unit or mrm_unit

        if purpose and capability_unit is not None:
            # Cheap pre-filter: a person can only hold the required authority
            # through their account role, an appointment or an office post, so
            # narrow in SQL first and confirm per person below.
            from django.db.models import Q

            needed = roles.CAP_EVALUATE if purpose == "evaluator" else roles.CAP_CREATE
            base_authorities = [
                authority_value
                for authority_value, role_name in roles.BASE_ROLE_MAP.items()
                if needed in roles.ROLE_CAPABILITIES.get(role_name, frozenset())
            ]
            appointment_roles = [
                role_name
                for role_name in roles.APPOINTMENT_ROLE_VALUES
                if needed in roles.ROLE_CAPABILITIES.get(role_name, frozenset())
            ]
            condition = Q(authority__in=base_authorities)
            if appointment_roles:
                condition |= Q(
                    appointments__role__in=appointment_roles,
                    appointments__is_active=True,
                )
            queryset = queryset.filter(condition).distinct()

        if exact_unit is not None:
            if exact_unit not in accessible_unit_ids(request.user):
                raise PermissionDenied("That unit is outside your authorised scope.")
            queryset = queryset.filter(unit_id=exact_unit)
        elif mrm_unit is not None:
            allowed_units = role_rules.related_unit_ids(mrm_unit)
            if not allowed_units:
                raise ValidationError({"mrm_unit": "Unknown unit."})
            queryset = queryset.filter(unit_id__in=allowed_units)

        data = []
        for user in queryset.order_by("last_name", "first_name")[:1000]:
            caps = (
                role_rules.capabilities_for(user, capability_unit)
                if capability_unit
                else set()
            )
            row = {
                "id": user.id,
                "full_name": user.full_name,
                "rank": user.rank,
                "serial_number": user.serial_number,
                "role_suggestions": ["Team Leader", "Assistant Team Leader", "Member", "Driver"],
                "unit_id": user.unit_id,
                "unit_acronym": user.unit.acronym if user.unit_id else "",
                "mrm_eligible": True,
            }
            if capability_unit:
                row["can_create_mrm"] = bool(
                    caps & {role_rules.CAP_CREATE, role_rules.CAP_EVALUATE}
                )
                row["can_evaluate"] = role_rules.CAP_EVALUATE in caps
            data.append(row)

        if purpose == "creator":
            data = [row for row in data if row.get("can_create_mrm")]
        elif purpose == "evaluator":
            data = [row for row in data if row.get("can_evaluate")]

        return Response({"count": len(data), "results": data})
