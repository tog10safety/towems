"""The EXISTING ``users`` table.

``managed = False``: Django reads and writes rows but will never emit DDL for
this table. Every column name, nullability and length matches the live schema
(see docs/DATABASE_SCHEMA_INVENTORY.md).

Two quirks are preserved deliberately:

* ``password_hash`` holds a Werkzeug scrypt digest, not a Django hash. The
  custom authentication backend verifies it so all 484 accounts keep working.
* The database has BOTH ``unit`` (legacy varchar) and ``unit_id`` (FK). The
  legacy ORM masked the varchar column with a Python property, making its data
  invisible. Here both are mapped: ``unit`` -> ``unit_legacy`` (preserved) and
  ``unit_id`` -> ``unit`` (authoritative relationship).
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.contrib.auth.hashers import check_password as django_check_password
from django.db import models
from django.utils.crypto import salted_hmac

from apps.accounts.personnel_details import (
    BLOOD_TYPE_CHOICES,
    CIVIL_STATUS_CHOICES,
    HIGHEST_EDUCATION_CHOICES,
    SEX_CHOICES,
)

AUTHORITY_CHOICES = [
    ("Administrator", "Administrator"),
    ("Evaluator", "Evaluator"),
    ("Creator", "Creator"),
    ("Unit Safety Officer", "Unit Safety Officer"),
]

CATEGORY_CHOICES = [
    ("Officer", "Officer"),
    ("Enlisted Personnel", "Enlisted Personnel"),
    ("Civilian Human Resource (CivHR)", "Civilian Human Resource (CivHR)"),
]

AUTHORITY_LEVEL_CHOICES = [
    ("Operator", "Operator"),
    ("Supervisor", "Supervisor"),
    ("Squadron Commander", "Squadron Commander"),
    ("Director for Operations", "Director for Operations"),
    ("Commander", "Commander"),
]


class User(models.Model):
    """A TOWEMS user / personnel record (existing ``users`` table)."""

    id = models.AutoField(primary_key=True)

    username = models.CharField(max_length=80, unique=True)
    password_hash = models.CharField(max_length=255)

    authority = models.CharField(max_length=40, choices=AUTHORITY_CHOICES, default="Creator")
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES)
    rank = models.CharField(max_length=50, null=True, blank=True)
    designation = models.CharField(max_length=100, null=True, blank=True)

    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, null=True, blank=True)
    last_name = models.CharField(max_length=100)

    serial_number = models.CharField(max_length=50, unique=True)
    branch_of_service = models.CharField(max_length=100)

    # Legacy varchar column `unit` - preserved, not authoritative.
    # The live column is NOT NULL (verified by verify_legacy_mapping), so the
    # model must always write a value; an empty string is acceptable there.
    unit_legacy = models.CharField(
        db_column="unit", max_length=100, blank=True, default=""
    )
    # Authoritative relationship.
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="users",
    )

    contact_number = models.CharField(max_length=20, null=True, blank=True)
    signature_filename = models.CharField(max_length=200, null=True, blank=True)
    date_created = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(null=True, default=True)

    password_changed_date = models.DateTimeField(null=True, blank=True)
    password_expiry_date = models.DateTimeField(null=True, blank=True)
    password_expiry_notified = models.BooleanField(null=True, default=False)
    force_password_change = models.BooleanField(null=True, default=False)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        managed = False
        db_table = "users"
        ordering = ["last_name", "first_name"]
        verbose_name = "personnel record"
        verbose_name_plural = "personnel"

    # -- Django auth interface -------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_username(self) -> str:
        return self.username

    def get_full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p).strip()

    def get_short_name(self) -> str:
        return self.first_name or self.username

    @property
    def full_name(self) -> str:
        """Rank-prefixed display name, matching the legacy ``full_name`` hybrid."""
        parts = [self.rank, self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p).strip()

    def get_session_auth_hash(self) -> str:
        """Session invalidation hash derived from the legacy digest.

        Mirrors Django's implementation but sources the digest from
        ``password_hash`` so changing a password invalidates sessions.
        """
        return salted_hmac(
            "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash",
            self.password_hash or "",
            algorithm="sha256",
        ).hexdigest()

    def check_password(self, raw_password: str) -> bool:
        from apps.accounts.backends import verify_password

        return verify_password(self.password_hash, raw_password)

    def set_password(self, raw_password: str) -> None:
        """Hash with Werkzeug (same algorithm as the legacy app).

        Callers must be authorised; this writes to the legacy ``users`` table.
        """
        from werkzeug.security import generate_password_hash

        from apps.core import timeutils

        self.password_hash = generate_password_hash(raw_password)
        # Philippine time (spec §7), not the host clock.
        self.password_changed_date = timeutils.now()
        self.password_expiry_date = timeutils.now() + dt.timedelta(days=90)
        self.force_password_change = False
        self.password_expiry_notified = False

    def is_password_expired(self) -> bool:
        from apps.core import timeutils

        if not self.password_expiry_date:
            return False
        return timeutils.now() > self.password_expiry_date

    def days_until_expiry(self) -> int:
        from apps.core import timeutils

        if not self.password_expiry_date:
            return 90
        return max(0, (self.password_expiry_date - timeutils.now()).days)

    def needs_password_change(self) -> bool:
        return bool(self.force_password_change) or self.is_password_expired()

    def get_accessible_unit_ids(self) -> list[int]:
        """Unit ids visible to this user (legacy-compatible rules)."""
        from apps.core.scoping import accessible_unit_ids

        return sorted(accessible_unit_ids(self))

    def can_manage_unit(self, target_unit_id) -> bool:
        if self.authority == "Administrator":
            return True
        if target_unit_id is None:
            return False
        try:
            target = int(target_unit_id)
        except (TypeError, ValueError):
            return False
        return target in self.get_accessible_unit_ids()

    def __str__(self) -> str:
        return f"{self.username} ({self.authority})"


class UserProfile(models.Model):
    """Self-service profile data that has no home in the legacy table.

    ``users`` has no avatar column and adding one would alter a production
    table, so the picture lives in this additive one-to-one side table. The
    signature needs no new storage: ``users.signature_filename`` already exists
    and is read by the MRM PDF generator, so uploading a signature updates that
    column and writes into the existing ``signatures/`` directory.
    """

    user = models.OneToOneField(
        "accounts.User",
        db_column="user_id",
        primary_key=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="profile",
    )
    THEME_CHOICES = [
        ("system", "System default"),
        ("light", "Light"),
        ("dark", "Dark"),
    ]

    avatar_filename = models.CharField(max_length=200, blank=True, default="")
    avatar_content_type = models.CharField(max_length=60, blank=True, default="")
    bio = models.TextField(blank=True, default="")
    #: Appearance preference, stored per account so it follows the user across
    #: browsers and devices. ``system`` follows the operating-system setting.
    theme_preference = models.CharField(
        max_length=10, choices=THEME_CHOICES, default="system"
    )

    # Personnel details that the legacy ``users`` table never carried.  They
    # are additive columns on this side table, so the production schema is not
    # touched by any of them.
    email = models.EmailField(blank=True, default="")
    sex = models.CharField(
        max_length=10, choices=SEX_CHOICES, blank=True, default=""
    )
    date_of_birth = models.DateField(null=True, blank=True)
    blood_type = models.CharField(
        max_length=5, choices=BLOOD_TYPE_CHOICES, blank=True, default=""
    )
    home_address = models.CharField(max_length=255, blank=True, default="")
    emergency_contact_name = models.CharField(
        max_length=150, blank=True, default=""
    )
    emergency_contact_number = models.CharField(
        max_length=20, blank=True, default=""
    )
    date_enlisted = models.DateField(null=True, blank=True)
    date_commissioned = models.DateField(null=True, blank=True)
    #: Air Force Specialty Code - primary, secondary and tertiary.
    afsc_primary = models.CharField(max_length=100, blank=True, default="")
    afsc_secondary = models.CharField(max_length=100, blank=True, default="")
    afsc_tertiary = models.CharField(max_length=100, blank=True, default="")
    highest_education = models.CharField(
        max_length=50, choices=HIGHEST_EDUCATION_CHOICES, blank=True, default=""
    )
    civil_status = models.CharField(
        max_length=20, choices=CIVIL_STATUS_CHOICES, blank=True, default=""
    )

    #: Newly registered personnel complete password, picture, signature and
    #: profile details on their first sign-in.  Existing accounts keep the
    #: default ``False`` and are not interrupted.
    onboarding_required = models.BooleanField(default=False)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    #: Last authenticated request, stamped by ``PresenceMiddleware``. "Online"
    #: is "used TOWEMS in the last few minutes": the stamp simply ages out, so a
    #: closed browser or an expired session needs no bookkeeping.
    last_seen = models.DateTimeField(null=True, blank=True)

    #: Exactly one account owns the system (see ``apps.accounts.ownership``).
    #: The flag lives here rather than on the legacy ``users`` table, which is
    #: ``managed = False`` and must never receive DDL.
    is_system_owner = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = "accounts_user_profile"
        constraints = [
            # The database, not application code, guarantees the single seat:
            # every row satisfying the condition holds is_system_owner = True,
            # so saving a second one is refused by the unique index.
            models.UniqueConstraint(
                fields=["is_system_owner"],
                condition=models.Q(is_system_owner=True),
                name="acct_profile_single_owner",
            ),
        ]
        # Explicit ordering: without it the query inherits ``users`` ordering
        # through the primary-key relation, which adds a needless join.
        ordering = ["user_id"]
        verbose_name = "user profile"

    def __str__(self) -> str:
        return f"Profile of user {self.user_id}"


class PasswordResetToken(models.Model):
    """Single-use administrator-issued password reset link.

    Only the SHA-256 digest of the token is stored, so a database reader cannot
    replay one. The raw token is returned exactly once, to the administrator who
    issued it, for hand-over through an approved channel. Sessions are
    invalidated automatically because ``User.get_session_auth_hash`` derives
    from the password digest, which changes when the reset is completed.
    """

    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="password_reset_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.CharField(max_length=45, blank=True, default="")

    class Meta:
        db_table = "accounts_password_reset_token"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "used_at"], name="acct_pwreset_user_idx"),
        ]

    def __str__(self) -> str:
        return f"Reset token for user {self.user_id} (used={bool(self.used_at)})"


class UserMFA(models.Model):
    """TOTP enrolment state for one account.

    Additive on purpose: the legacy ``users`` table is unmanaged and must not
    gain columns. ``secret`` is only populated while enrolment is in progress
    or after it completes; it is never returned once MFA is enabled.
    """

    user = models.OneToOneField(
        "accounts.User",
        db_column="user_id",
        primary_key=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="mfa",
    )
    secret = models.CharField(max_length=64, blank=True, default="")
    #: The Gmail address the authenticator entry was created under. Kept so an
    #: administrator can see that the person enrolled a different account from
    #: the one on their profile (a typo or someone else's address).
    account = models.CharField(max_length=254, blank=True, default="")
    enabled_at = models.DateTimeField(null=True, blank=True)
    #: First privileged sign-in starts the 14-day enrolment window. Kept per
    #: account so an administrator reset restarts it for that person only.
    grace_started_at = models.DateTimeField(null=True, blank=True)
    last_used_counter = models.BigIntegerField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_user_mfa"
        verbose_name = "user MFA state"

    def __str__(self) -> str:
        return f"MFA for user {self.user_id} (enabled={bool(self.enabled_at)})"


class MFARecoveryCode(models.Model):
    """A one-time recovery code, stored only as a Django password hash."""

    mfa = models.ForeignKey(
        UserMFA,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
    )
    code_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_mfa_recovery_code"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["mfa", "used_at"], name="acct_mfa_recovery_idx"),
        ]

    def __str__(self) -> str:
        return f"Recovery code for user {self.mfa_id} (used={bool(self.used_at)})"


class UnitChangeRequest(models.Model):
    """A person's request to move to another unit, awaiting a decision.

    A transfer is destructive: every post held in the unit being left is ended
    and the account is reset to the Creator role. It is therefore never applied
    on request. The row stays ``pending`` until an Administrator or the Super
    Administrator approves or declines it; they are told through the
    notification bell (``ops_notification``).

    Only one request may be pending per person - the partial unique constraint
    below enforces that in the database - so the review queue cannot collect
    duplicate asks from the same account.
    """

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Declined"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    requester = models.ForeignKey(
        "accounts.User",
        db_column="requester_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="unit_change_requests",
    )
    #: The unit the person held when the request was raised. Kept even if the
    #: account later moves, so the decision records what was actually left.
    from_unit = models.ForeignKey(
        "legacy.Unit",
        db_column="from_unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    to_unit = models.ForeignKey(
        "legacy.Unit",
        db_column="to_unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    requester_note = models.TextField(blank=True, default="")
    decision_note = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        "accounts.User",
        db_column="decided_by_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_unit_change_request"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["requester"],
                condition=models.Q(status="pending"),
                name="acct_unit_change_one_pending",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "created_at"], name="acct_unit_change_status_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"Unit change {self.pk} ({self.status})"


class DeviceSession(models.Model):
    """One signed-in browser session, with the device it came from.

    Django's own ``django_session`` table stays the source of truth for whether
    a session is alive; this row only maps a session key to the account, the
    device label and the last activity, so "is this person already signed in
    somewhere else?" is one small query instead of a scan of every session
    payload. A row whose session has expired is ignored and deleted.
    """

    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="device_sessions",
    )
    session_key = models.CharField(max_length=64, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    device_label = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_device_session"
        ordering = ["-last_seen_at", "-id"]
        indexes = [
            models.Index(fields=["user", "last_seen_at"], name="acct_device_user_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} on {self.device_label or 'a device'}"
