"""Serializers for personnel records and authentication payloads."""

from __future__ import annotations

import datetime as dt
import re

from rest_framework import serializers

from apps.accounts.models import (
    AUTHORITY_CHOICES,
    CATEGORY_CHOICES,
    UnitChangeRequest,
    User,
    UserProfile,
)
from apps.accounts.personnel_details import PERSONNEL_DETAIL_FIELDS, profile_dict

PASSWORD_POLICY = {
    "min_length": 8,
    "min_uppercase": 1,
    "min_lowercase": 1,
    "min_digits": 1,
    "min_special": 1,
    "max_consecutive": 3,
}


def validate_password_policy(password: str) -> None:
    """Enforce the legacy password policy server-side."""
    errors = []
    if len(password or "") < PASSWORD_POLICY["min_length"]:
        errors.append(f"at least {PASSWORD_POLICY['min_length']} characters")
    if len(re.findall(r"[A-Z]", password or "")) < PASSWORD_POLICY["min_uppercase"]:
        errors.append("at least one uppercase letter")
    if len(re.findall(r"[a-z]", password or "")) < PASSWORD_POLICY["min_lowercase"]:
        errors.append("at least one lowercase letter")
    if len(re.findall(r"\d", password or "")) < PASSWORD_POLICY["min_digits"]:
        errors.append("at least one digit")
    if len(re.findall(r"[^A-Za-z0-9]", password or "")) < PASSWORD_POLICY["min_special"]:
        errors.append("at least one special character")
    if re.search(r"(.)\1{%d,}" % PASSWORD_POLICY["max_consecutive"], password or ""):
        errors.append(
            f"no more than {PASSWORD_POLICY['max_consecutive']} identical characters in a row"
        )
    if errors:
        raise serializers.ValidationError(
            {"password": "Password must contain " + ", ".join(errors) + "."}
        )


class ProfileDetailsMixin:
    """Merge flat personnel-detail fields into a user representation.

    The values live on the additive profile table, but the rest of the API and
    the SPA work with one flat personnel record.
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.update(profile_dict(getattr(instance, "profile", None)))
        return data


class OptionalDateField(serializers.DateField):
    """A date input that treats an empty string as "not supplied"."""

    def to_internal_value(self, value):
        if value in ("", None):
            return None
        return super().to_internal_value(value)


class PersonnelDetailsSerializer(serializers.ModelSerializer):
    """Validation for the additive personnel profile columns."""

    date_of_birth = OptionalDateField(required=False, allow_null=True)
    date_enlisted = OptionalDateField(required=False, allow_null=True)
    date_commissioned = OptionalDateField(required=False, allow_null=True)

    class Meta:
        model = UserProfile
        fields = list(PERSONNEL_DETAIL_FIELDS)

    def validate_email(self, value):
        return (value or "").strip().lower()

    @staticmethod
    def _clean_contact(value: str) -> str:
        value = (value or "").strip()
        if value and not value.replace("+", "").replace("-", "").replace(" ", "").isdigit():
            raise serializers.ValidationError("Use digits, spaces, '+' or '-' only.")
        if len(value) > 20:
            raise serializers.ValidationError("Maximum 20 characters.")
        return value

    def validate_emergency_contact_number(self, value):
        return self._clean_contact(value)

    @staticmethod
    def _not_future(value, label):
        # ``USE_TZ`` is False for legacy compatibility, so ``timezone.localdate``
        # would raise on a naive clock. The process zone is Asia/Manila anyway.
        if value and value > dt.date.today():
            raise serializers.ValidationError(f"{label} cannot be in the future.")
        return value

    def validate_date_of_birth(self, value):
        return self._not_future(value, "Date of birth")

    def validate_date_enlisted(self, value):
        return self._not_future(value, "Date enlisted")

    def validate_date_commissioned(self, value):
        return self._not_future(value, "Date commissioned")

    def validate(self, attrs):
        def value(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        enlisted = value("date_enlisted")
        commissioned = value("date_commissioned")
        if enlisted and commissioned and commissioned < enlisted:
            raise serializers.ValidationError(
                {"date_commissioned": "Commissioning cannot precede enlistment."}
            )
        return attrs


class SeatUnitPrivacyMixin:
    """Hide the seat holder's unit from everyone but the seat holder.

    The Super Administrator reaches every unit, so naming the one they are
    carried against adds nothing to the register - it only points at the account
    that owns the system. The seat holder's own session still reads it (the
    profile page and ``/auth/me``), because hiding it from them would be absurd.
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # A serializer built without a request is the caller's own payload
        # (login, /auth/me), so there is nobody else to hide the unit from.
        request = self.context.get("request")
        if request is None:
            return data

        from apps.accounts import ownership

        viewer = getattr(request, "user", None)
        if ownership.is_system_owner(instance) and not ownership.is_system_owner(viewer):
            for field in ("unit", "unit_id", "unit_name", "unit_acronym", "unit_legacy"):
                if field in data:
                    data[field] = None
            if "accessible_unit_ids" in data:
                data["accessible_unit_ids"] = []
            # Appointments and offices carry unit names too; blank them so a
            # post on the seat holder's row cannot leak the unit indirectly.
            for row in data.get("appointments") or []:
                if isinstance(row, dict):
                    row["unit_id"] = None
                    row["unit_acronym"] = ""
                    row["unit_name"] = ""
            for row in data.get("sections") or []:
                if isinstance(row, dict):
                    row["unit_id"] = None
                    row["unit_acronym"] = ""
        return data


class UserSummarySerializer(SeatUnitPrivacyMixin, ProfileDetailsMixin, serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    accessible_unit_ids = serializers.SerializerMethodField()
    sections = serializers.SerializerMethodField()
    mfa_required = serializers.SerializerMethodField()
    mfa_enabled = serializers.SerializerMethodField()
    mfa_account = serializers.SerializerMethodField()
    mfa_enrollment_required = serializers.SerializerMethodField()
    mfa_setup_required = serializers.SerializerMethodField()
    mfa_verified = serializers.SerializerMethodField()
    mfa_grace_deadline = serializers.SerializerMethodField()
    mfa_grace_days_remaining = serializers.SerializerMethodField()
    mfa_recovery_codes_remaining = serializers.SerializerMethodField()
    mfa_enrolled_at = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "full_name",
            "last_name",
            "rank",
            "designation",
            "authority",
            "category",
            "serial_number",
            "branch_of_service",
            "unit_id",
            "unit_name",
            "unit_acronym",
            "unit_legacy",
            "contact_number",
            "signature_filename",
            "date_created",
            "is_active",
            "force_password_change",
            "password_expiry_date",
            "accessible_unit_ids",
            "sections",
            "mfa_required",
            "mfa_enabled",
            "mfa_account",
            "mfa_enrollment_required",
            "mfa_setup_required",
            "mfa_verified",
            "mfa_grace_deadline",
            "mfa_grace_days_remaining",
            "mfa_recovery_codes_remaining",
            "mfa_enrolled_at",
        ]

    def _mfa(self, obj) -> dict:
        from apps.accounts import mfa

        cache = getattr(self, "_mfa_cache", {})
        if obj.pk not in cache:
            request = self.context.get("request")
            if request is not None and getattr(request.user, "pk", None) != obj.pk:
                # Personnel records are visible to colleagues; second-factor
                # state and recovery-code counts are not. Only the account
                # itself (login, /me) and the admin serializer receive them.
                cache[obj.pk] = {
                    "mfa_required": False,
                    "mfa_enabled": False,
                    "mfa_account": "",
                    "mfa_enrollment_required": False,
                    "mfa_setup_required": False,
                    "mfa_verified": False,
                    "mfa_grace_deadline": None,
                    "mfa_grace_days_remaining": None,
                    "mfa_recovery_codes_remaining": 0,
                    "mfa_enrolled_at": None,
                }
            else:
                cache[obj.pk] = mfa.status_payload(obj, request)
        self._mfa_cache = cache
        return cache[obj.pk]

    def get_mfa_required(self, obj):
        return self._mfa(obj)["mfa_required"]

    def get_mfa_enabled(self, obj):
        return self._mfa(obj)["mfa_enabled"]

    def get_mfa_account(self, obj):
        return self._mfa(obj)["mfa_account"]

    def get_mfa_enrollment_required(self, obj):
        return self._mfa(obj)["mfa_enrollment_required"]

    def get_mfa_setup_required(self, obj):
        return self._mfa(obj)["mfa_setup_required"]

    def get_mfa_verified(self, obj):
        return self._mfa(obj)["mfa_verified"]

    def get_mfa_grace_deadline(self, obj):
        return self._mfa(obj)["mfa_grace_deadline"]

    def get_mfa_grace_days_remaining(self, obj):
        return self._mfa(obj)["mfa_grace_days_remaining"]

    def get_mfa_recovery_codes_remaining(self, obj):
        return self._mfa(obj)["mfa_recovery_codes_remaining"]

    def get_mfa_enrolled_at(self, obj):
        return self._mfa(obj)["mfa_enrolled_at"]

    def get_accessible_unit_ids(self, obj):
        return obj.get_accessible_unit_ids()

    def get_sections(self, obj):
        """Active primary/secondary office, shown on the personnel record."""
        return [
            {
                "id": row.id,
                "code": row.section.code,
                "name": row.section.name,
                "assignment_type": row.assignment_type,
                "unit_id": row.section.unit_id,
            }
            for row in obj.section_assignments.all()
            if row.is_active and row.section.is_active
        ]


class PersonnelWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    unit_id = serializers.IntegerField(required=False, allow_null=True)
    #: Username is optional: newly registered personnel use their serial
    #: number, exactly as the unit registration procedure requires.
    username = serializers.CharField(required=False, allow_blank=True, max_length=80)

    #: Flat personnel details, stored on the additive profile table.
    email = serializers.EmailField(required=False, allow_blank=True)
    sex = serializers.CharField(required=False, allow_blank=True, max_length=10)
    date_of_birth = OptionalDateField(required=False, allow_null=True)
    blood_type = serializers.CharField(required=False, allow_blank=True, max_length=5)
    home_address = serializers.CharField(required=False, allow_blank=True, max_length=255)
    emergency_contact_name = serializers.CharField(
        required=False, allow_blank=True, max_length=150
    )
    emergency_contact_number = serializers.CharField(
        required=False, allow_blank=True, max_length=20
    )
    date_enlisted = OptionalDateField(required=False, allow_null=True)
    date_commissioned = OptionalDateField(required=False, allow_null=True)
    afsc_primary = serializers.CharField(required=False, allow_blank=True, max_length=100)
    afsc_secondary = serializers.CharField(required=False, allow_blank=True, max_length=100)
    afsc_tertiary = serializers.CharField(required=False, allow_blank=True, max_length=100)
    highest_education = serializers.CharField(required=False, allow_blank=True, max_length=50)
    civil_status = serializers.CharField(required=False, allow_blank=True, max_length=20)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "password",
            "authority",
            "category",
            "rank",
            "designation",
            "first_name",
            "middle_name",
            "last_name",
            "serial_number",
            "branch_of_service",
            "unit_id",
            "unit_legacy",
            "contact_number",
            "is_active",
            "force_password_change",
            *PERSONNEL_DETAIL_FIELDS,
        ]
        extra_kwargs = {
            # Uniqueness is enforced in ``validate_serial_number`` so the message
            # names the unit that already holds the number, and case variants
            # of the same number are refused too.
            "serial_number": {"validators": []},
        }

    def validate_password(self, value):
        validate_password_policy(value)
        return value

    def _may_manage_accounts(self) -> bool:
        from apps.core import roles

        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(user and roles.has_capability(user, roles.CAP_MANAGE_USERS))

    def validate_authority(self, value):
        valid = {choice[0] for choice in AUTHORITY_CHOICES}
        if value not in valid:
            raise serializers.ValidationError(f"Unknown authority {value!r}.")

        # Growing the Administrator roll is the Super Administrator's decision:
        # every Administrator may register personnel, but only the seat holder
        # may hand out the role that holds every capability.
        becoming_administrator = value == "Administrator" and (
            self.instance is None or self.instance.authority != "Administrator"
        )
        if becoming_administrator:
            from apps.accounts import ownership

            request = self.context.get("request")
            if not ownership.can_grant_administrator(getattr(request, "user", None)):
                raise serializers.ValidationError(
                    "Only an Administrator may create or promote another "
                    "Administrator account."
                )

        # Role changes are an account-administration action. The legacy
        # personnel form stays exactly as it was for unit safety officers, but
        # it may no longer be used to grant a different role.
        if not self._may_manage_accounts():
            if self.instance is None and value != "Creator":
                raise serializers.ValidationError(
                    "Only an Administrator may assign this role."
                )
            if self.instance is not None and value != self.instance.authority:
                raise serializers.ValidationError(
                    "Only an Administrator may change a role."
                )
        return value

    def validate_category(self, value):
        valid = {choice[0] for choice in CATEGORY_CHOICES}
        if value not in valid:
            raise serializers.ValidationError(f"Unknown category {value!r}.")
        return value

    def validate_username(self, value):
        if self.instance is not None and value != self.instance.username:
            raise serializers.ValidationError(
                "Usernames are stable identifiers and cannot be changed here."
            )
        return value

    def validate_serial_number(self, value):
        serial = (value or "").strip()
        if self.instance is not None and serial != self.instance.serial_number:
            raise serializers.ValidationError(
                "Serial numbers are stable identifiers and cannot be changed here."
            )
        if self.instance is None and serial:
            # One person, one serial number: the check is force-wide, so the
            # same number cannot be registered a second time in another unit.
            clash = (
                User.objects.filter(serial_number__iexact=serial)
                .select_related("unit")
                .first()
            )
            if clash is not None:
                where = (
                    f"{clash.unit.acronym} ({clash.unit.name})"
                    if clash.unit_id
                    else "another unit"
                )
                raise serializers.ValidationError(
                    f"Serial Number {serial} is already registered to {where} "
                    f"(@{clash.username}). A serial number can only belong to one "
                    "person, across all units."
                )
        return serial

    def validate_unit_id(self, value):
        if value is None:
            return value
        from apps.legacy.models import Unit

        unit = Unit.objects.filter(pk=value).first()
        if unit is None:
            raise serializers.ValidationError("Selected unit does not exist.")
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if actor is not None and not actor.can_manage_unit(value):
            raise serializers.ValidationError(
                "That unit is outside your authorised scope."
            )
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "A password is required when creating personnel."}
            )

        if self.instance is None:
            serial_number = attrs.get("serial_number") or ""
            username = (attrs.get("username") or serial_number).strip()
            if not username:
                raise serializers.ValidationError(
                    {"username": "Enter a serial number or username."}
                )
            if User.objects.filter(username=username).exists():
                raise serializers.ValidationError(
                    {"username": "That username is already in use."}
                )
            attrs["username"] = username

        details_data = {
            field: attrs.pop(field)
            for field in PERSONNEL_DETAIL_FIELDS
            if field in attrs
        }
        details = PersonnelDetailsSerializer(
            instance=getattr(self.instance, "profile", None),
            data=details_data,
            partial=self.instance is not None,
            context=self.context,
        )
        details.is_valid(raise_exception=True)
        attrs["_details"] = details.validated_data
        return attrs

    def create(self, validated_data):
        details = validated_data.pop("_details", {})
        password = validated_data.pop("password", None)
        unit_id = validated_data.pop("unit_id", None)
        username = validated_data.get("username") or validated_data["serial_number"]
        validated_data["username"] = username
        user = User(**validated_data)
        if unit_id is not None:
            user.unit_id = unit_id
        # `users.unit` is a NOT NULL legacy varchar column. Keep it consistent
        # with the authoritative unit_id so legacy screens and reports agree.
        user.unit_legacy = self._legacy_unit_name(unit_id) or validated_data.get(
            "unit_legacy", ""
        )
        if not user.date_created:
            from apps.core import timeutils

            user.date_created = timeutils.now()
        user.set_password(password)
        # The administrator supplies the first password; the person must change
        # it on first sign-in and complete their profile setup.
        user.force_password_change = True
        user.save()
        UserProfile.objects.create(
            user=user, onboarding_required=True, **details
        )
        return user

    def update(self, instance, validated_data):
        details = validated_data.pop("_details", {})
        password = validated_data.pop("password", None)
        unit_id = validated_data.pop("unit_id", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if unit_id is not None:
            instance.unit_id = unit_id
            instance.unit_legacy = self._legacy_unit_name(unit_id) or instance.unit_legacy
        if password:
            instance.set_password(password)
        instance.save()

        if details:
            profile, _created = UserProfile.objects.get_or_create(user=instance)
            for field, value in details.items():
                setattr(profile, field, value)
            profile.save()
        return instance

    @staticmethod
    def _legacy_unit_name(unit_id) -> str:
        if not unit_id:
            return ""
        from apps.legacy.models import Unit

        return Unit.objects.filter(pk=unit_id).values_list("name", flat=True).first() or ""


class AdminUserSerializer(SeatUnitPrivacyMixin, ProfileDetailsMixin, serializers.ModelSerializer):
    """Administrator view of an account. Never includes any password field.

    Carries the person's *effective* roles - the account role plus every active
    unit appointment and acting delegation - so the management table can show
    the whole authority picture instead of a single column.
    """

    full_name = serializers.CharField(read_only=True)
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    roles = serializers.SerializerMethodField()
    role_labels = serializers.SerializerMethodField()
    appointments = serializers.SerializerMethodField()
    sections = serializers.SerializerMethodField()
    account_role = serializers.CharField(source="authority", read_only=True)
    is_system_owner = serializers.SerializerMethodField()
    mfa_required = serializers.SerializerMethodField()
    mfa_enabled = serializers.SerializerMethodField()
    mfa_account = serializers.SerializerMethodField()
    mfa_outstanding = serializers.SerializerMethodField()
    mfa_grace_days_remaining = serializers.SerializerMethodField()
    mfa_recovery_codes_remaining = serializers.SerializerMethodField()
    mfa_enrolled_at = serializers.SerializerMethodField()
    profile_refresh_required = serializers.SerializerMethodField()
    profile_setup_gaps = serializers.SerializerMethodField()
    profile_setup_gap_labels = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "full_name",
            "rank",
            "designation",
            "authority",
            "account_role",
            "category",
            "serial_number",
            "branch_of_service",
            "unit_id",
            "unit_name",
            "unit_acronym",
            "contact_number",
            "date_created",
            "is_active",
            "is_system_owner",
            "force_password_change",
            "password_expiry_date",
            "roles",
            "role_labels",
            "appointments",
            "sections",
            "mfa_required",
            "mfa_enabled",
            "mfa_account",
            "mfa_outstanding",
            "mfa_grace_days_remaining",
            "mfa_recovery_codes_remaining",
            "mfa_enrolled_at",
            "profile_refresh_required",
            "profile_setup_gaps",
            "profile_setup_gap_labels",
        ]

    def _mfa(self, obj) -> dict:
        from apps.accounts import mfa

        cache = getattr(self, "_mfa_cache", {})
        if obj.pk not in cache:
            cache[obj.pk] = mfa.status_payload(obj, self.context.get("request"))
        self._mfa_cache = cache
        return cache[obj.pk]

    def get_mfa_required(self, obj):
        return self._mfa(obj)["mfa_required"]

    def get_mfa_enabled(self, obj):
        return self._mfa(obj)["mfa_enabled"]

    def _setup(self, obj) -> dict:
        """First-sign-in status, computed once per row for the register."""
        from apps.accounts import setup_gate

        cache = getattr(self, "_setup_cache", {})
        if obj.pk not in cache:
            gaps = setup_gate.profile_gaps(obj)
            cache[obj.pk] = {
                "profile_refresh_required": setup_gate.onboarding_outstanding(obj),
                "profile_setup_gaps": gaps,
                "profile_setup_gap_labels": setup_gate.gap_labels(gaps),
                "mfa_outstanding": setup_gate.mfa_outstanding(obj),
            }
        self._setup_cache = cache
        return cache[obj.pk]

    def get_mfa_account(self, obj):
        return self._mfa(obj)["mfa_account"]

    def get_mfa_outstanding(self, obj):
        return self._setup(obj)["mfa_outstanding"]

    def get_profile_refresh_required(self, obj):
        return self._setup(obj)["profile_refresh_required"]

    def get_profile_setup_gaps(self, obj):
        return self._setup(obj)["profile_setup_gaps"]

    def get_profile_setup_gap_labels(self, obj):
        return self._setup(obj)["profile_setup_gap_labels"]

    def get_mfa_grace_days_remaining(self, obj):
        return self._mfa(obj)["mfa_grace_days_remaining"]

    def get_mfa_recovery_codes_remaining(self, obj):
        return self._mfa(obj)["mfa_recovery_codes_remaining"]

    def get_mfa_enrolled_at(self, obj):
        return self._mfa(obj)["mfa_enrolled_at"]

    def _active_sections(self, obj):
        return [
            row
            for row in obj.section_assignments.all()
            if row.is_active and row.section.is_active
        ]

    def _role_values(self, obj) -> list[str]:
        """Account role + active posts + offices, from the prefetched rows."""
        from apps.core import roles as role_rules

        values = {role_rules.base_role(obj)}
        values |= {row.role for row in obj.appointments.all() if row.is_active}
        values |= {row.acting_role for row in obj.acting_appointments.all() if row.is_current}
        for row in self._active_sections(obj):
            office_role = role_rules.OFFICE_FUNCTION_ROLE_MAP.get(row.section.function)
            if office_role:
                values.add(office_role)
        return sorted(values)

    def get_is_system_owner(self, obj):
        """Read from the row the viewset already joined; never assign here."""
        profile = getattr(obj, "profile", None)
        return bool(profile and profile.is_system_owner)

    def get_roles(self, obj):
        return self._role_values(obj)

    def get_role_labels(self, obj):
        from apps.core import roles as role_rules

        return [role_rules.ROLE_LABELS.get(role, role) for role in self._role_values(obj)]

    def get_appointments(self, obj):
        return [
            {
                "id": row.id,
                "role": row.role,
                "role_label": row.get_role_display(),
                "unit_id": row.unit_id,
                "unit_acronym": row.unit.acronym if row.unit_id else "",
                "unit_name": row.unit.name if row.unit_id else "",
            }
            for row in obj.appointments.all()
            if row.is_active
        ]

    def get_sections(self, obj):
        """Office/section membership, which is what confers the office roles."""
        return [
            {
                "id": row.id,
                "section": row.section_id,
                "name": row.section.name,
                "code": row.section.code,
                "function": row.section.function,
                "function_label": row.section.get_function_display(),
                "assignment_type": row.assignment_type,
                "unit_id": row.section.unit_id,
                "unit_acronym": row.section.unit.acronym if row.section.unit_id else "",
            }
            for row in self._active_sections(obj)
        ]


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    """Fields an administrator may change, and nothing more.

    ``username`` and ``serial_number`` are intentionally absent: they are the
    stable identity used by audit records and legacy reports. Password changes
    go through the reset workflow, never through this serializer. Account
    status changes go through the explicit deactivate/reactivate actions so
    each one is audited with a reason.
    """

    unit_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            "first_name",
            "middle_name",
            "last_name",
            "rank",
            "designation",
            "contact_number",
            "authority",
            "category",
            "unit_id",
        ]

    def validate_contact_number(self, value):
        value = (value or "").strip()
        if value and not value.replace("+", "").replace("-", "").replace(" ", "").isdigit():
            raise serializers.ValidationError("Use digits, spaces, '+' or '-' only.")
        if len(value) > 20:
            raise serializers.ValidationError("Maximum 20 characters.")
        return value or None

    def validate_authority(self, value):
        valid = {choice[0] for choice in AUTHORITY_CHOICES}
        if value not in valid:
            raise serializers.ValidationError(f"Unknown authority {value!r}.")
        return value

    def validate_category(self, value):
        valid = {choice[0] for choice in CATEGORY_CHOICES}
        if value not in valid:
            raise serializers.ValidationError(f"Unknown category {value!r}.")
        return value

    def validate_unit_id(self, value):
        if value is None:
            return value
        from apps.legacy.models import Unit

        unit = Unit.objects.filter(pk=value).first()
        if unit is None:
            raise serializers.ValidationError("Selected unit does not exist.")
        if not unit.is_active:
            raise serializers.ValidationError("Selected unit is inactive.")
        return value


class PasswordResetCompleteSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password_policy(value)
        return value


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password_policy(value)
        return value


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class MFAChallengeSerializer(serializers.Serializer):
    challenge = serializers.CharField()
    code = serializers.CharField(trim_whitespace=False)
    recovery = serializers.BooleanField(default=False)


class MFACodeSerializer(serializers.Serializer):
    code = serializers.CharField(trim_whitespace=False)


class UnitChangeRequestSerializer(serializers.ModelSerializer):
    """One request in the Administrator's review queue."""

    requester_name = serializers.CharField(source="requester.full_name", read_only=True)
    requester_username = serializers.CharField(source="requester.username", read_only=True)
    requester_rank = serializers.CharField(source="requester.rank", read_only=True, default="")
    requester_serial_number = serializers.CharField(
        source="requester.serial_number", read_only=True, default=""
    )
    from_unit_acronym = serializers.CharField(source="from_unit.acronym", read_only=True, default="")
    from_unit_name = serializers.CharField(source="from_unit.name", read_only=True, default="")
    to_unit_acronym = serializers.CharField(source="to_unit.acronym", read_only=True, default="")
    to_unit_name = serializers.CharField(source="to_unit.name", read_only=True, default="")
    decided_by_name = serializers.CharField(
        source="decided_by.full_name", read_only=True, default=""
    )
    decided_by_username = serializers.CharField(
        source="decided_by.username", read_only=True, default=""
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = UnitChangeRequest
        fields = [
            "id",
            "requester_id",
            "requester_name",
            "requester_username",
            "requester_rank",
            "requester_serial_number",
            "from_unit_id",
            "from_unit_acronym",
            "from_unit_name",
            "to_unit_id",
            "to_unit_acronym",
            "to_unit_name",
            "status",
            "status_label",
            "requester_note",
            "decision_note",
            "decided_by_name",
            "decided_by_username",
            "decided_at",
            "created_at",
        ]
