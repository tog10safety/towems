"""Mappings for the 11 pre-existing MRMS tables.

Every model here is ``managed = False``. Django reads and writes rows, but it
will never create, alter, rename or drop these tables - that is the mechanism
that makes the preservation rules in docs/DATABASE_COMPATIBILITY.md
structurally enforced rather than a matter of discipline.

Column names, nullability and lengths come from read-only introspection of the
live database (docs/DATABASE_SCHEMA_INVENTORY.md), not from the legacy
SQLAlchemy models, because the two had drifted.

Notes:

* PostgreSQL ENUM columns are mapped to ``CharField`` with the **live** label
  set. PostgreSQL casts a string to its enum type on write, and no
  ``CREATE TYPE`` is emitted because no migration targets these tables.
* Foreign keys use ``db_constraint=False``: the constraints already exist and
  Django must not re-declare (or later try to "fix") them. It also lets a
  fresh database be migrated before legacy tables are present.
* Every FK sets an explicit ``db_column`` because Django would otherwise use
  ``<field>_id`` while the real columns are named ``created_by``, ``mrm_id``,
  ``hazard_id``, etc.
"""

from __future__ import annotations

from django.db import models

# Live enum labels (verified against the database).
ACTIVITY_TYPE_CHOICES = [
    ("login", "login"),
    ("logout", "logout"),
    ("create_hazard", "create_hazard"),
    ("edit_hazard", "edit_hazard"),
    ("delete_hazard", "delete_hazard"),
    ("create_mrm", "create_mrm"),
    ("edit_mrm", "edit_mrm"),
    ("submit_mrm", "submit_mrm"),
    ("review_mrm", "review_mrm"),
    ("approve_mrm", "approve_mrm"),
    ("export_data", "export_data"),
    ("import_data", "import_data"),
    ("user_created", "user_created"),
    ("user_modified", "user_modified"),
    ("password_changed", "password_changed"),
    ("signature_uploaded", "signature_uploaded"),
    ("system_access", "system_access"),
]

# NOTE: the legacy ORM declares 'unit_management' as a module label but the
# live PostgreSQL enum does not contain it (drift D1). Using it raises
# InvalidTextRepresentation, so it is deliberately excluded here.
MODULE_CHOICES = [
    ("auth", "auth"),
    ("hazards", "hazards"),
    ("mrm", "mrm"),
    ("admin", "admin"),
    ("users", "users"),
    ("unit_safety", "unit_safety"),
]

SEVERITY_CHOICES = [(s, s) for s in "ABCDE"]

MRM_STATUS_CHOICES = [
    ("draft", "draft"),
    ("submitted", "submitted"),
    ("reviewed", "reviewed"),
]

REVIEW_STATUS_CHOICES = [
    ("pending", "pending"),
    ("approved", "approved"),
    ("rejected", "rejected"),
    ("needs_revision", "needs_revision"),
]

AUTHORITY_LEVEL_CHOICES = [
    ("Operator", "Operator"),
    ("Supervisor", "Supervisor"),
    ("Squadron Commander", "Squadron Commander"),
    ("Director for Operations", "Director for Operations"),
    ("Commander", "Commander"),
]

UNIT_CATEGORY_CHOICES = [
    ("Command", "Command"),
    ("Wing", "Wing"),
    ("Group", "Group"),
    ("Center", "Center"),
    ("Squadron", "Squadron"),
]

RISK_COLOR_RED = {"5A", "5B", "5C", "4A", "4B", "3A"}
RISK_COLOR_YELLOW = {
    "5D", "5E", "4C", "4D", "4E", "3B", "3C", "3D", "2A", "2B", "2C", "1A",
}


class Unit(models.Model):
    """Existing ``units`` table - hierarchical unit structure."""

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    acronym = models.CharField(max_length=20)
    category = models.CharField(max_length=20, choices=UNIT_CATEGORY_CHOICES)
    location = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "self",
        db_column="parent_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="children",
    )
    commanding_officer = models.CharField(max_length=100, null=True, blank=True)
    date_created = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(null=True, default=True)

    class Meta:
        managed = False
        db_table = "units"
        ordering = ["name"]

    @property
    def full_name(self) -> str:
        return f"{self.name} ({self.acronym})"

    def get_hierarchy_display(self) -> str:
        names = []
        node = self
        seen = set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            names.insert(0, node.acronym)
            node = node.parent
        return " > ".join(names)

    def get_subordinate_unit_ids(self, include_self: bool = True) -> list[int]:
        ids: list[int] = [self.id] if include_self else []
        for child in self.children.filter(is_active=True):
            ids.extend(child.get_subordinate_unit_ids(include_self=True))
        return ids

    def __str__(self) -> str:
        return f"{self.acronym} - {self.name}"


class UnitAssignment(models.Model):
    """Existing ``unit_assignments`` table (safety officers -> units)."""

    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="unit_assignments",
    )
    unit = models.ForeignKey(
        Unit,
        db_column="unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="assignments",
    )
    assigned_date = models.DateTimeField(null=True, blank=True)
    is_primary = models.BooleanField(null=True, default=False)

    class Meta:
        managed = False
        db_table = "unit_assignments"


class UnitLogo(models.Model):
    """Existing ``unit_logos`` table - dual legacy/new identity (drift D3)."""

    id = models.AutoField(primary_key=True)
    unit = models.OneToOneField(
        Unit,
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="logo",
    )
    logo_filename = models.CharField(max_length=200)
    date_uploaded = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User",
        db_column="uploaded_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    unit_name = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "unit_logos"


class HazardRegistry(models.Model):
    """Existing ``hazard_registry`` table - the institutional hazard library.

    Risk ratings are stored as concatenated strings ("4B"), not products. The
    algorithm is reproduced exactly because 1,923 rows and their historical
    ratings depend on it.
    """

    id = models.AutoField(primary_key=True)
    unit = models.CharField(max_length=100)  # legacy string, still written
    unit_id = models.ForeignKey(
        Unit,
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="hazards",
    )
    activity_code = models.CharField(max_length=100)
    hazard_description = models.TextField()
    before_likelihood = models.IntegerField()
    before_severity = models.CharField(max_length=1, choices=SEVERITY_CHOICES)
    before_risk_rating = models.CharField(max_length=5)
    mitigations = models.TextField()
    after_likelihood = models.IntegerField()
    after_severity = models.CharField(max_length=1, choices=SEVERITY_CHOICES)
    after_risk_rating = models.CharField(max_length=5)
    date_updated = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )

    class Meta:
        managed = False
        db_table = "hazard_registry"
        ordering = ["-date_updated", "-id"]

    @staticmethod
    def calculate_risk_rating(likelihood, severity) -> str:
        if likelihood and severity:
            return f"{likelihood}{severity}"
        return "N/A"

    @staticmethod
    def risk_color(rating: str) -> str:
        if rating in RISK_COLOR_RED:
            return "danger"
        if rating in RISK_COLOR_YELLOW:
            return "warning"
        return "success"

    def __str__(self) -> str:
        return f"Hazard {self.id} - {self.activity_code}"


class MRMForm(models.Model):
    """Existing ``mrm_forms`` table - the mission risk assessment record."""

    id = models.AutoField(primary_key=True)
    mrm_number = models.CharField(max_length=20, unique=True)
    activity_mission = models.CharField(max_length=255)

    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    reviewed_by = models.ForeignKey(
        "accounts.User",
        db_column="reviewed_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
        help_text="The assigned evaluator.",
    )

    date_created = models.DateTimeField(null=True, blank=True)
    safety_objectives = models.TextField()
    imsafes = models.TextField(null=True, blank=True)
    aircraft_vehicle = models.CharField(max_length=255, null=True, blank=True)
    environment = models.TextField(null=True, blank=True)
    mission_statement = models.TextField(null=True, blank=True)

    max_risk = models.IntegerField(null=True, blank=True)
    residual_risk = models.IntegerField(null=True, blank=True)
    total_percent = models.FloatField(null=True, blank=True)

    authority_level = models.CharField(
        max_length=40, choices=AUTHORITY_LEVEL_CHOICES, null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=MRM_STATUS_CHOICES, default="draft")
    date_submitted = models.DateTimeField(null=True, blank=True)
    date_reviewed = models.DateTimeField(null=True, blank=True)
    review_status = models.CharField(
        max_length=20, choices=REVIEW_STATUS_CHOICES, default="pending"
    )
    review_notes = models.TextField(null=True, blank=True)

    creator_signed = models.BooleanField(null=True, default=False)
    creator_signature_date = models.DateTimeField(null=True, blank=True)
    evaluator_signed = models.BooleanField(null=True, default=False)
    evaluator_signature_date = models.DateTimeField(null=True, blank=True)

    authority_assigned_to = models.ForeignKey(
        "accounts.User",
        db_column="authority_assigned_to",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    authority_approved = models.BooleanField(null=True, default=False)
    authority_approval_date = models.DateTimeField(null=True, blank=True)
    authority_signature_date = models.DateTimeField(null=True, blank=True)

    approved_pdf_filename = models.CharField(max_length=255, null=True, blank=True)
    last_edited = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(
        "accounts.User",
        db_column="last_edited_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    edit_history = models.JSONField(null=True, blank=True)

    # Legacy string snapshots, preserved alongside the FK snapshots below.
    # The database carries BOTH `<name>` (varchar) and `<name>_id` (integer FK),
    # so the string columns are mapped as `*_name` while the relation keeps the
    # Django-conventional name. That makes `form.original_unit_id` address the
    # real column exactly (see docs/DATABASE_COMPATIBILITY.md drift D4).
    original_unit_name = models.CharField(db_column="original_unit", max_length=100, null=True, blank=True)
    original_creator_unit_name = models.CharField(
        db_column="original_creator_unit", max_length=100, null=True, blank=True
    )
    original_reviewer_unit_name = models.CharField(
        db_column="original_reviewer_unit", max_length=100, null=True, blank=True
    )
    original_authority_unit_name = models.CharField(
        db_column="original_authority_unit", max_length=100, null=True, blank=True
    )

    original_unit = models.ForeignKey(
        Unit,
        db_column="original_unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    original_creator_unit = models.ForeignKey(
        Unit,
        db_column="original_creator_unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    original_reviewer_unit = models.ForeignKey(
        Unit,
        db_column="original_reviewer_unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    original_authority_unit = models.ForeignKey(
        Unit,
        db_column="original_authority_unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )

    risk_category = models.CharField(max_length=20, null=True, blank=True)
    completion_time_seconds = models.IntegerField(null=True, blank=True)
    date_approved = models.DateTimeField(null=True, blank=True)

    reviewer_name = models.CharField(max_length=200, null=True, blank=True)
    authority_name = models.CharField(max_length=200, null=True, blank=True)
    creator_name = models.CharField(max_length=200, null=True, blank=True)

    feedback_deadline = models.DateTimeField(null=True, blank=True)
    feedback_collection_closed = models.BooleanField(null=True, default=False)
    debrief_date = models.DateTimeField(null=True, blank=True)
    lessons_learned_summary = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "mrm_forms"
        ordering = ["-date_created", "-id"]

    @property
    def is_approvable(self) -> bool:
        return bool(
            self.status == "reviewed"
            and self.review_status == "approved"
            and self.creator_signed
            and self.evaluator_signed
        )

    def __str__(self) -> str:
        return f"MRM {self.mrm_number} ({self.status})"


class MissionHazards(models.Model):
    """Existing ``mission_hazards`` table - hazards selected for a mission."""

    id = models.AutoField(primary_key=True)
    mrm = models.ForeignKey(
        MRMForm,
        db_column="mrm_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="hazard_links",
    )
    hazard = models.ForeignKey(
        HazardRegistry,
        db_column="hazard_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="mission_links",
    )
    mitigation_override = models.TextField(null=True, blank=True)
    selected = models.BooleanField(null=True, default=True)
    override_likelihood = models.IntegerField(null=True, blank=True)
    override_severity = models.CharField(max_length=1, null=True, blank=True)
    additional_mitigations = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "mission_hazards"


class MRMPersonnel(models.Model):
    """Existing ``mrm_personnel`` table - personnel tagged to an MRM."""

    id = models.AutoField(primary_key=True)
    mrm = models.ForeignKey(
        MRMForm,
        db_column="mrm_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="tagged_personnel",
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="mrm_tags",
    )
    role = models.CharField(max_length=100, null=True, blank=True)
    tagged_by = models.ForeignKey(
        "accounts.User",
        db_column="tagged_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    tagged_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(null=True, default=True)

    class Meta:
        managed = False
        db_table = "mrm_personnel"


class MRMFeedback(models.Model):
    """Existing ``mrm_feedback`` table - post-mission feedback."""

    id = models.AutoField(primary_key=True)
    mrm = models.ForeignKey(
        MRMForm,
        db_column="mrm_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="feedback_items",
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    risk_assessment_accuracy = models.IntegerField(null=True, blank=True)
    hazard_identification_rating = models.IntegerField(null=True, blank=True)
    mitigation_effectiveness = models.IntegerField(null=True, blank=True)
    what_went_well = models.TextField(null=True, blank=True)
    what_could_be_improved = models.TextField(null=True, blank=True)
    new_hazards_identified = models.TextField(null=True, blank=True)
    lessons_learned = models.TextField(null=True, blank=True)
    additional_comments = models.TextField(null=True, blank=True)
    recommend_add_to_hazard_registry = models.BooleanField(null=True, default=False)
    recommended_hazard_description = models.TextField(null=True, blank=True)
    recommended_mitigations = models.TextField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    is_anonymous = models.BooleanField(null=True, default=False)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        db_column="reviewed_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, null=True, default="pending")
    action_taken = models.TextField(null=True, blank=True)
    action_taken_by = models.ForeignKey(
        "accounts.User",
        db_column="action_taken_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    action_taken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "mrm_feedback"


class FeedbackNotification(models.Model):
    """Existing ``feedback_notifications`` table."""

    id = models.AutoField(primary_key=True)
    feedback = models.ForeignKey(
        MRMFeedback,
        db_column="feedback_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="notifications",
    )
    unit_safety_officer = models.ForeignKey(
        "accounts.User",
        db_column="unit_safety_officer_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="feedback_notifications",
    )
    is_read = models.BooleanField(null=True, default=False)
    created_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "feedback_notifications"


class UserActivity(models.Model):
    """Existing ``user_activities`` table - the historical audit trail."""

    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="activities",
    )
    activity_type = models.CharField(max_length=32, choices=ACTIVITY_TYPE_CHOICES)
    module = models.CharField(max_length=32, choices=MODULE_CHOICES)
    description = models.TextField()
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    resource_id = models.IntegerField(null=True, blank=True)
    resource_type = models.CharField(max_length=50, null=True, blank=True)
    activity_data = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "user_activities"
        ordering = ["-timestamp", "-id"]
