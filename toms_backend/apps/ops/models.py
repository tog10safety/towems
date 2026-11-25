"""NEW TOWEMS tables.

Every model in this module is ``managed = True`` and was introduced by a
reviewed, additive migration. No existing table or column is modified.

Relationships that point at legacy tables (``users``, ``units``, ``mrm_forms``)
use ``db_constraint=False`` so that:

1. a fresh/staging database can run migrations before the legacy schema is
   copied in, and
2. Django never attempts to create, alter or drop a constraint on a
   production-owned table.

Referential integrity for those links is enforced in the service layer and by
periodic integrity reports (see docs/MIGRATION_PLAN.md §7).
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Mission(TimestampedModel):
    """Shared mission header for Air Operations and Ground Operations.

    The two categories keep distinct detail tables so that Air-only fields
    (aircraft, flight identification, route) never appear on a ground form and
    vice versa, while shared facts live once here.
    """

    CATEGORY_AIR = "air"
    CATEGORY_GROUND = "ground"
    CATEGORY_CHOICES = [(CATEGORY_AIR, "Air Operations"), (CATEGORY_GROUND, "Ground Operations")]

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("planned", "Planned"),
        ("approved", "Approved"),
        ("active", "Active"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    mission_number = models.CharField(max_length=40, unique=True)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=255)
    purpose = models.TextField(blank=True, default="")

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="missions",
        help_text="Unit Primary Responsible (UPR).",
    )
    office_section = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Office/Section Primary Responsible (O/SPR).",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")

    # -- Board hold ----------------------------------------------------------
    #: A held mission keeps its planning status but may not take off. The Daily
    #: Operations board is the normal way to place and release the hold.
    is_held = models.BooleanField(default=False)
    held_at = models.DateTimeField(null=True, blank=True)

    # -- abort order ---------------------------------------------------------
    #: A mission in progress may be aborted by the Operations Center, which also
    #: pins where it ends: back at the station or on the plan's last stop. The
    #: order is kept on the mission so the board and the drivers' screens read
    #: the same instruction without re-deriving it from a notification.
    ABORT_STATION = "station"
    #: The last stop any vehicle has actually recorded reaching - never further
    #: out than where the convoy already is.
    ABORT_LAST_STOP = "last_stop"
    #: The plan's own final stop, which may still be ahead of the convoy.
    ABORT_FINAL_STOP = "final_stop"
    ABORT_DESTINATION_CHOICES = [
        (ABORT_STATION, "Return to station"),
        (ABORT_LAST_STOP, "Last stop reached"),
        (ABORT_FINAL_STOP, "Planned final stop"),
    ]

    aborted_at = models.DateTimeField(null=True, blank=True)
    abort_reason = models.TextField(blank=True, default="")
    abort_destination = models.CharField(
        max_length=20, choices=ABORT_DESTINATION_CHOICES, blank=True, default=""
    )
    abort_target_name = models.CharField(max_length=255, blank=True, default="")
    abort_target_latitude = models.FloatField(null=True, blank=True)
    abort_target_longitude = models.FloatField(null=True, blank=True)
    aborted_by = models.ForeignKey(
        "accounts.User",
        db_column="aborted_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="aborted_missions",
    )

    # -- mission sequencing ------------------------------------------------
    #: An earlier mission this one waits for. PROTECT keeps a completed or
    #: planned predecessor from being deleted out from under a dependent.
    DEPENDENCY_COMPLETION = "completion"
    DEPENDENCY_RENDEZVOUS_READY = "rendezvous_ready"
    DEPENDENCY_RELEASE_CHOICES = [
        (DEPENDENCY_COMPLETION, "Predecessor completed"),
        (DEPENDENCY_RENDEZVOUS_READY, "Predecessor rendezvous ready"),
    ]
    depends_on = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_constraint=False,
        related_name="dependent_missions",
        help_text="An earlier mission that must finish before this one executes.",
    )
    dependency_release = models.CharField(
        max_length=24,
        choices=DEPENDENCY_RELEASE_CHOICES,
        default=DEPENDENCY_COMPLETION,
    )
    dependency_buffer_minutes = models.PositiveSmallIntegerField(default=30)

    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    #: When the Operations Center released the mission into execution. Kept
    #: separately from the mission's planned start and from each vehicle's ATD.
    dispatched_at = models.DateTimeField(null=True, blank=True)
    #: When the Operations Center closed the mission after its vehicle runs.
    completed_at = models.DateTimeField(null=True, blank=True)

    location_name = models.CharField(max_length=255, blank=True, default="")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    mgrs = models.CharField(max_length=32, blank=True, default="")

    risk_form = models.ForeignKey(
        "legacy.MRMForm",
        db_column="risk_form_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="toms_missions",
        help_text="The MRM assessment that governs this mission.",
    )

    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="created_missions",
    )
    approved_by = models.ForeignKey(
        "accounts.User",
        db_column="approved_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="approved_missions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="")

    # -- approving authority (spec §2) ------------------------------------
    # The Operations Center names one eligible post *and* the person holding
    # it. The role says what authority the approval carries; the person is who
    # the task is actually assigned to. Both are stored so an approval can
    # never be claimed by "whoever holds the post today" after a change.
    AUTHORITY_COMMANDER = "commander"
    AUTHORITY_DEPUTY_COMMANDER = "deputy_commander"
    AUTHORITY_DIRECTOR_OPERATIONS = "director_operations"
    AUTHORITY_OPERATIONS_OFFICER = "operations_officer"
    APPROVING_AUTHORITY_CHOICES = [
        (AUTHORITY_COMMANDER, "Commander"),
        (AUTHORITY_DEPUTY_COMMANDER, "Deputy Commander"),
        (AUTHORITY_DIRECTOR_OPERATIONS, "Director for Operations"),
        (AUTHORITY_OPERATIONS_OFFICER, "Operations Officer"),
    ]

    APPROVAL_NOT_ROUTED = "not_routed"
    APPROVAL_PENDING = "pending"
    APPROVAL_RETURNED = "returned"
    APPROVAL_APPROVED = "approved"
    APPROVAL_REJECTED = "rejected"
    APPROVAL_STATUS_CHOICES = [
        (APPROVAL_NOT_ROUTED, "Not routed"),
        (APPROVAL_PENDING, "Pending approval"),
        (APPROVAL_RETURNED, "Returned for revision"),
        (APPROVAL_APPROVED, "Approved"),
        (APPROVAL_REJECTED, "Rejected"),
    ]

    approving_authority = models.ForeignKey(
        "accounts.User",
        db_column="approving_authority_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="missions_awaiting_approval",
        help_text="The person the approval task is assigned to.",
    )
    approving_authority_role = models.CharField(
        max_length=40, choices=APPROVING_AUTHORITY_CHOICES, blank=True, default=""
    )
    approval_status = models.CharField(
        max_length=20, choices=APPROVAL_STATUS_CHOICES, default=APPROVAL_NOT_ROUTED
    )
    approval_assigned_at = models.DateTimeField(null=True, blank=True)
    approval_decided_at = models.DateTimeField(null=True, blank=True)
    approval_note = models.TextField(blank=True, default="")
    #: Bumped whenever the mission changes materially. An approval only covers
    #: the revision it was given for, so a later change cannot ride on it.
    revision = models.PositiveIntegerField(default=1)
    approved_revision = models.PositiveIntegerField(default=0)

    # -- Mission Order (drafted with the mission, signed after approval) ---
    # The order is part of the mission plan, not a separate document stream.
    # It follows the mission through staff routing and is then handed to the
    # designated signatory. Dispatch requires the signature to cover the same
    # revision as the mission approval.
    MISSION_ORDER_DRAFT = "draft"
    MISSION_ORDER_AWAITING_SIGNATURE = "awaiting_signature"
    MISSION_ORDER_APPROVED = "approved"
    MISSION_ORDER_STATUS_CHOICES = [
        (MISSION_ORDER_DRAFT, "Draft with mission"),
        (MISSION_ORDER_AWAITING_SIGNATURE, "Awaiting signature"),
        (MISSION_ORDER_APPROVED, "Signed and approved"),
    ]

    mission_order_signatory = models.ForeignKey(
        "accounts.User",
        db_column="mission_order_signatory_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="missions_awaiting_order_signature",
        help_text="The person designated to sign and approve the Mission Order.",
    )
    mission_order_status = models.CharField(
        max_length=24,
        choices=MISSION_ORDER_STATUS_CHOICES,
        default=MISSION_ORDER_DRAFT,
    )
    mission_order_assigned_at = models.DateTimeField(null=True, blank=True)
    mission_order_signed_at = models.DateTimeField(null=True, blank=True)
    mission_order_signed_by = models.ForeignKey(
        "accounts.User",
        db_column="mission_order_signed_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="signed_mission_orders",
    )
    mission_order_note = models.TextField(blank=True, default="")
    #: The mission revision the signature covers. A later material change makes
    #: the order stale even if the approval record itself still says approved.
    mission_order_signed_revision = models.PositiveIntegerField(default=0)
    #: The signature block as it stood on the day the order was signed. A signed
    #: order is a record, not a live view of the roster: moving the signer to
    #: another post or unit must not rewrite the document they signed.
    mission_order_signer_designation = models.CharField(
        max_length=120, blank=True, default=""
    )
    mission_order_signer_as_commander = models.BooleanField(default=False)

    class Meta:
        db_table = "ops_mission"
        ordering = ["-start_at", "-id"]
        indexes = [
            models.Index(fields=["category", "status"], name="ops_mission_cat_status_idx"),
            models.Index(fields=["unit"], name="ops_mission_unit_idx"),
            models.Index(fields=["start_at"], name="ops_mission_start_idx"),
        ]

    def air_is_airborne(self) -> bool:
        """True while any aircraft is between takeoff and its destination.

        A normal leg finishes on ATA; an overflight leg finishes on TOOT. A
        mission on the ground between legs is *not* airborne, so the board may
        hold or cancel its remaining itinerary once everyone has landed.
        """
        if self.category != self.CATEGORY_AIR:
            return False
        legs = AirMissionItinerary.objects.filter(
            aircraft__element__air_detail__mission=self
        ).only("atd", "ata", "tot", "toot", "overflight")
        for leg in legs:
            started = bool(leg.atd or leg.tot or leg.toot)
            if not started:
                continue
            finished = bool(leg.ata) or (bool(leg.overflight) and bool(leg.toot))
            if not finished:
                return True
        return False

    def __str__(self) -> str:
        return f"{self.mission_number} - {self.title}"

    #: Fields whose change after approval invalidates that approval.
    MATERIAL_FIELDS = frozenset(
        {
            "title",
            "purpose",
            "category",
            "unit_id",
            "start_at",
            "end_at",
            "location_name",
            "latitude",
            "longitude",
            "mgrs",
            "risk_form_id",
        }
    )


class GroundMissionDetail(models.Model):
    """Ground-operations-only fields (1:1 with Mission)."""

    mission = models.OneToOneField(
        Mission, on_delete=models.CASCADE, related_name="ground_detail"
    )
    estimated_start = models.DateTimeField(null=True, blank=True)
    estimated_end = models.DateTimeField(null=True, blank=True)
    upr_unit = models.ForeignKey(
        "legacy.Unit",
        db_column="upr_unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    osp_section = models.CharField(max_length=200, blank=True, default="")
    operational_area = models.CharField(max_length=255, blank=True, default="")
    ground_risk_notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_ground_mission_detail"


class AirMissionDetail(models.Model):
    """Air-operations-only fields (1:1 with Mission)."""

    CONFIGURATION_CHOICES = [
        ("single", "Single Aircraft"),
        ("element", "Element"),
        ("three_ship", "Three-Ship"),
        ("flight", "Flight"),
        ("multi_aircraft", "Multi-Aircraft (5–6)"),
        ("large_formation", "Large Formation (7–12)"),
        ("custom_formation", "Custom Formation (13+)"),
    ]
    CONFIGURATION_RANGES = {
        "single": (1, 1),
        "element": (2, 2),
        "three_ship": (3, 3),
        "flight": (4, 4),
        "multi_aircraft": (5, 6),
        "large_formation": (7, 12),
        "custom_formation": (13, 50),
    }

    mission = models.OneToOneField(
        Mission, on_delete=models.CASCADE, related_name="air_detail"
    )
    #: The day this Flight Operations sheet covers.  It is kept separately from
    #: start_at because a sheet can contain legs whose planned times are TBD.
    operation_date = models.DateField(null=True, blank=True)
    mission_configuration = models.CharField(
        max_length=32,
        choices=CONFIGURATION_CHOICES,
        default="single",
    )
    aircraft_count = models.PositiveSmallIntegerField(default=1)
    air_asset = models.ForeignKey(
        "AirAsset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="air_mission_details",
        help_text="Air Assets register entry linked to this flight.",
    )
    aircraft_type = models.CharField(max_length=120, blank=True, default="")
    aircraft_tail_number = models.CharField(max_length=60, blank=True, default="")
    flight_identification = models.CharField(max_length=60, blank=True, default="")
    departure_location = models.CharField(max_length=255, blank=True, default="")
    destination_location = models.CharField(max_length=255, blank=True, default="")
    route = models.TextField(blank=True, default="")
    scheduled_departure = models.DateTimeField(null=True, blank=True)
    scheduled_arrival = models.DateTimeField(null=True, blank=True)
    #: Recorded when the aircraft actually arrives at the rendezvous point.
    actual_arrival = models.DateTimeField(null=True, blank=True)
    #: Set once the aircraft is parked, secure and ready to join the formation.
    rendezvous_ready_at = models.DateTimeField(null=True, blank=True)
    #: The friendly units this flight supports, chosen from Operational Mapping.
    #: A single sortie can support several plotted units, so the link is
    #: many-to-many; deleting a plotted unit simply removes it from the flight.
    supporting_units = models.ManyToManyField(
        "OperationalMapFeature",
        blank=True,
        related_name="supported_missions",
        help_text="Friendly units supported by this air mission.",
    )

    class Meta:
        db_table = "ops_air_mission_detail"

    @classmethod
    def configuration_bounds(cls, value):
        return cls.CONFIGURATION_RANGES.get(value, (1, 1))

    @classmethod
    def default_aircraft_count(cls, value):
        return cls.configuration_bounds(value)[0]


class AirMissionElement(models.Model):
    """One numbered element (NR) on a daily Flight Operations sheet.

    An element may contain a single aircraft (the C-295 row) or a formation
    (the two S-70i rows).  Its mission is shared by every aircraft and leg
    unless an itinerary supplies its own mission text.
    """

    air_detail = models.ForeignKey(
        AirMissionDetail, on_delete=models.CASCADE, related_name="elements"
    )
    sequence = models.PositiveSmallIntegerField(default=1)
    mission = models.TextField(blank=True, default="")
    remarks = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_air_mission_element"
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["air_detail", "sequence"],
                name="ops_air_element_sequence_unique",
            )
        ]

    def __str__(self) -> str:
        return f"Element {self.sequence}"


class AirMissionAircraft(models.Model):
    """A manually entered aircraft inside an element.

    Deliberately no AirAsset foreign key: daily flight operations describe the
    aircraft as typed by the Operations Center, not as linked inventory.
    """

    element = models.ForeignKey(
        AirMissionElement, on_delete=models.CASCADE, related_name="aircraft"
    )
    sequence = models.PositiveSmallIntegerField(default=1)
    aircraft_type = models.CharField(max_length=120, blank=True, default="")
    tail_number = models.CharField(max_length=60, blank=True, default="")
    callsign = models.CharField(max_length=60, blank=True, default="")
    is_lead = models.BooleanField(default=False)

    class Meta:
        db_table = "ops_air_mission_aircraft"
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["element", "sequence"],
                name="ops_air_aircraft_sequence_unique",
            )
        ]

    @property
    def display_name(self) -> str:
        parts = [self.aircraft_type, self.tail_number, self.callsign]
        return " · ".join(part for part in parts if part)

    def __str__(self) -> str:
        return self.display_name or f"Aircraft {self.sequence}"


class AirMissionCrew(models.Model):
    """A named crew member on one aircraft."""

    ROLE_IP = "IP"
    ROLE_PIC = "PIC"
    ROLE_CP = "CP"
    ROLE_OTHER = "OTHER"
    ROLE_CHOICES = [
        (ROLE_IP, "IP"),
        (ROLE_PIC, "PIC"),
        (ROLE_CP, "CP"),
        (ROLE_OTHER, "Other"),
    ]

    aircraft = models.ForeignKey(
        AirMissionAircraft, on_delete=models.CASCADE, related_name="crew"
    )
    sequence = models.PositiveSmallIntegerField(default=1)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_PIC)
    name = models.CharField(max_length=140)

    class Meta:
        db_table = "ops_air_mission_crew"
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["aircraft", "sequence"],
                name="ops_air_crew_sequence_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.role} {self.name}"


class AirMissionItinerary(models.Model):
    """One planned leg and, once flown, its actual takeoff and landing.

    ``etd``/``eta`` are the schedule.  ``atd``/``ata`` are written when the leg
    actually takes off and lands.  ``se``/``sd`` bracket the movement with the
    engine start and shutdown.  An overflight leg also records ``tot`` (time
    over target) and ``toot`` (time off target).  A missing planned time is
    rendered as TBD; a missing actual time is rendered as NOT RECORDED, never as
    the estimate.
    """

    STATUS_PLANNED = "planned"
    STATUS_AIRBORNE = "airborne"
    STATUS_LANDED = "landed"
    STATUS_CANCELLED = "cancelled"
    STATUS_DIVERTED = "diverted"
    STATUS_CHOICES = [
        (STATUS_PLANNED, "Planned"),
        (STATUS_AIRBORNE, "Airborne"),
        (STATUS_LANDED, "Landed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_DIVERTED, "Diverted"),
    ]
    POINT_ROLE_CHOICES = [
        ("", "Waypoint"),
        ("target", "Target"),
        ("landing_zone", "Landing Zone"),
    ]

    aircraft = models.ForeignKey(
        AirMissionAircraft, on_delete=models.CASCADE, related_name="itineraries"
    )
    sequence = models.PositiveSmallIntegerField(default=1)
    etd = models.DateTimeField(null=True, blank=True)
    eta = models.DateTimeField(null=True, blank=True)
    atd = models.DateTimeField(null=True, blank=True)
    ata = models.DateTimeField(null=True, blank=True)
    #: Time over target and time off target, recorded only for overflight legs.
    tot = models.DateTimeField(null=True, blank=True)
    toot = models.DateTimeField(null=True, blank=True)
    #: Engine start before departure and shutdown after landing. Neither is
    #: recorded at the target end of an overflight leg: the aircraft arrives
    #: over the target (TOT) and leaves it (TOOT) without shutting down.
    se = models.DateTimeField(null=True, blank=True)
    sd = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PLANNED
    )
    origin = models.CharField(max_length=255, blank=True, default="")
    destination = models.CharField(max_length=255, blank=True, default="")
    route = models.TextField(blank=True, default="")
    mission = models.TextField(blank=True, default="")
    #: The destination is a waypoint unless the planner marks it as the target
    #: or landing zone that needs its MGRS shown in the route summary.
    point_role = models.CharField(
        max_length=20, choices=POINT_ROLE_CHOICES, blank=True, default=""
    )
    overflight = models.BooleanField(default=False)
    return_to_origin = models.BooleanField(default=False)
    remarks = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_air_mission_itinerary"
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["aircraft", "sequence"],
                name="ops_air_itinerary_sequence_unique",
            )
        ]

    @property
    def route_label(self) -> str:
        if self.route.strip():
            return self.route.strip()
        if self.origin and self.destination:
            return f"{self.origin} – {self.destination}"
        return self.origin or self.destination or ""

    def __str__(self) -> str:
        return self.route_label or f"Leg {self.sequence}"


class MissionOperationalArea(models.Model):
    """An *additional* operational area for a ground mission.

    The mission's primary operational area stays where it always was - the
    ``operational_area`` text on ``GroundMissionDetail`` plus the mission's own
    latitude/longitude - so nothing about existing records changes. This table
    only holds the extra areas a planner adds on top of that one.
    """

    mission = models.ForeignKey(
        Mission, on_delete=models.CASCADE, related_name="operational_areas"
    )
    sequence = models.PositiveSmallIntegerField(default=1)
    name = models.CharField(max_length=255)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    mgrs = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        db_table = "ops_mission_operational_area"
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "sequence"],
                name="ops_mission_operational_area_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.mission_id}: {self.name}"


class MissionPersonnel(models.Model):
    """Personnel assigned to a mission, with an operational role."""

    ROLE_CHOICES = [
        ("Officer-In-Charge", "Officer-In-Charge"),
        # Legacy spellings stay valid so old mission records keep their meaning.
        ("Officer In Charge", "Officer In Charge"),
        ("Team Leader", "Team Leader"),
        ("Assistant Team Leader", "Assistant Team Leader"),
        ("Driver", "Driver"),
        ("Security", "Security"),
        ("Member", "Member"),
        ("Specify", "Specify"),
        ("Crew", "Crew"),
        ("Medic", "Medic"),
        ("Other", "Other"),
    ]

    mission = models.ForeignKey(
        Mission, on_delete=models.CASCADE, related_name="personnel"
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="mission_assignments",
    )
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default="Member")
    #: Free text supplied when the planner chooses "Specify" above.
    custom_role = models.CharField(max_length=80, blank=True, default="")
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ops_mission_personnel"
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "user"], name="ops_mission_personnel_unique"
            )
        ]


class MissionRiskTeam(models.Model):
    """Personnel responsible for preparing a mission's MRM assessment.

    Additive table: the mission and the legacy MRM record are untouched. The
    team is what drives the "prepare the MRM" notifications and the MRM status
    shown on the mission; it is not an evaluator assignment (that stays on
    ``mrm_forms.reviewed_by`` so separation of duties is preserved).
    """

    ROLE_LEAD = "lead"
    ROLE_ASSESSOR = "assessor"
    ROLE_REVIEWER = "reviewer"
    ROLE_CHOICES = [
        (ROLE_LEAD, "MRM lead"),
        (ROLE_ASSESSOR, "MRM assessor"),
        (ROLE_REVIEWER, "Safety reviewer"),
    ]

    mission = models.ForeignKey(
        Mission, on_delete=models.CASCADE, related_name="mrm_team"
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="mrm_team_assignments",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_ASSESSOR)
    assigned_by = models.ForeignKey(
        "accounts.User",
        db_column="assigned_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "ops_mission_risk_team"
        ordering = ["assigned_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "user"], name="ops_mission_risk_team_unique"
            )
        ]
        indexes = [
            models.Index(fields=["user", "is_active"], name="ops_mrm_team_user_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.mission_id}:{self.user_id}:{self.role}"


class InventoryCategory(models.Model):
    #: The asset classes the register is organised around. The keys are stored,
    #: so they never change; the labels are the staff vocabulary. Aerospace
    #: Ground Equipment (AGE) is its own class because it needs its own
    #: custodianship and inspection cycle, separate from general equipment.
    KIND_CHOICES = [
        ("materiel", "Materiel"),
        ("equipment", "Equipment"),
        ("age", "Aerospace Ground Equipment (AGE)"),
        ("firearms", "Firearms"),
        ("ammunition", "Ammunition"),
        ("fuel", "Fuel & Lubricants"),
        ("vehicle", "Vehicles"),
        ("communications", "Communications Equipment"),
        ("other", "Other Authorised Assets"),
    ]

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="materiel")
    is_sensitive = models.BooleanField(
        default=False,
        help_text="Firearms/ammunition are restricted to command and safety roles.",
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    class Meta:
        db_table = "ops_inventory_category"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class InventoryItem(TimestampedModel):
    SERVICEABILITY_CHOICES = [
        ("serviceable", "Serviceable"),
        ("unserviceable", "Unserviceable"),
        ("maintenance", "Under Maintenance"),
        ("condemned", "Condemned"),
    ]

    #: The grades POL stock is bought in. Aviation fuel (POL-01) is AVGas or
    #: Jet A1 and automotive fuel (POL-02) is diesel or gasoline; the remaining
    #: POL categories are single products, so the column stays blank for them
    #: and for everything that is not POL at all.
    FUEL_GRADE_CHOICES = [
        ("", "Not graded"),
        ("avgas", "AVGas"),
        ("jet_a1", "Jet A1"),
        ("diesel", "Diesel"),
        ("gasoline", "Gasoline"),
    ]

    #: How the fuel is physically held. Drums are counted as drums and each
    #: item says how many litres one drum holds; bulk stock is already litres.
    FUEL_FORM_CHOICES = [
        ("", "Not fuel"),
        ("drum", "Drums"),
        ("bulk", "Bulk tank (litres)"),
    ]

    category = models.ForeignKey(
        InventoryCategory, on_delete=models.PROTECT, related_name="items"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    asset_tag = models.CharField(max_length=60, unique=True, null=True, blank=True)
    serial_number = models.CharField(max_length=120, blank=True, default="")

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="inventory_items",
    )
    custodian = models.ForeignKey(
        "accounts.User",
        db_column="custodian_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="custodied_items",
    )
    storage_location = models.CharField(max_length=200, blank=True, default="")
    #: Where the store physically stands once stock has been moved off its
    #: original site. A transferred drum batch carries the destination's own
    #: point, so the map pins it there instead of at the unit that owns it.
    location_latitude = models.FloatField(null=True, blank=True)
    location_longitude = models.FloatField(null=True, blank=True)

    quantity_total = models.IntegerField(default=0)
    quantity_available = models.IntegerField(default=0)
    unit_of_measure = models.CharField(max_length=20, default="unit")
    fuel_grade = models.CharField(
        max_length=40, choices=FUEL_GRADE_CHOICES, blank=True, default=""
    )
    fuel_form = models.CharField(
        max_length=20, choices=FUEL_FORM_CHOICES, blank=True, default=""
    )
    drum_capacity_litres = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Litres in one drum. Bulk stock is counted in litres and leaves this empty.",
    )
    #: POL stock has a shelf life, so the batch it came from is dated. These
    #: stay empty for everything that is not POL.
    date_issued = models.DateField(null=True, blank=True)
    testing_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the batch was last tested - aviation fuel only.",
    )
    expiry_date = models.DateField(null=True, blank=True)
    #: The delivery advice behind a bulk aviation receipt. The ledger keeps
    #: every DA number; this holds the latest one against the tank.
    da_number = models.CharField(max_length=60, blank=True, default="")
    serviceability = models.CharField(
        max_length=20, choices=SERVICEABILITY_CHOICES, default="serviceable"
    )
    is_sensitive = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_inventory_item"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["category", "serviceability"], name="ops_item_cat_svc_idx"),
            models.Index(fields=["unit"], name="ops_item_unit_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_available__gte=0),
                name="ops_item_qty_available_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_total__gte=0),
                name="ops_item_qty_total_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.asset_tag or self.pk})"

    @property
    def is_fuel(self) -> bool:
        """POL stock, graded or not: anything filed under a fuel category.

        A lubricant has no grade but is still counted in drums or litres, so
        the class - not the grade - decides whether the litre figures apply.
        """
        return bool(self.category_id) and self.category.kind == "fuel"

    @property
    def litres_per_unit(self) -> int:
        """Litres represented by one stored unit: a drum's capacity or 1 litre."""
        if self.fuel_form == "drum":
            return int(self.drum_capacity_litres or 0)
        return 1

    def litres(self, quantity: int) -> int:
        return max(0, int(quantity or 0)) * self.litres_per_unit


class AirAsset(TimestampedModel):
    """An aircraft in the unit inventory.

    Air assets are accountable records like any other inventory item, but they
    carry aviation-specific identity and capability data. The Administrator
    maintains them; every resource-menu account may read the register within
    its unit scope.
    """

    AIRCRAFT_TYPE_CHOICES = [
        ("rotary_wing", "Rotary-Wing Aircraft"),
        ("fixed_wing", "Fixed-Wing Aircraft"),
        ("uav", "Unmanned Aerial Vehicle (UAV)"),
        ("tiltrotor", "Tiltrotor Aircraft"),
        ("other", "Other Aircraft"),
    ]

    #: The owning air units, which are separate from the ground-unit inventory.
    ASSIGNED_UNIT_CHOICES = [
        ("5fw", "5th Fighter Wing"),
        ("205th_thw", "205th Tactical Helicopter Wing"),
        ("15sw", "15th Strike Wing"),
        ("220aw", "220th Airlift Wing"),
        ("250paw", "250th Presidential Airlift Wing"),
        ("300aisw", "300th Air Intelligence Surveillance Wing"),
        ("505sarg", "505th Search and Rescue Group"),
    ]

    #: Multiple boxes may be ticked: one airframe can serve several roles.
    CATEGORY_CHOICES = [
        ("fighter_interceptor", "Fighter / Interceptor"),
        ("attack_cas", "Attack / Close Air Support"),
        ("light_attack", "Light Attack"),
        ("transport_cargo", "Transport / Cargo"),
        ("utility", "Utility"),
        ("search_rescue", "Search and Rescue (SAR)"),
        ("combat_search_rescue", "Combat Search and Rescue (CSAR)"),
        ("reconnaissance_surveillance", "Reconnaissance / Surveillance"),
        ("maritime_patrol", "Maritime Patrol"),
        ("training", "Training"),
        ("air_ambulance_medevac", "Air Ambulance / Medical Evacuation (MEDEVAC)"),
        ("unmanned_aerial_vehicle", "Unmanned Aerial Vehicle (UAV)"),
        ("aerial_refueling", "Aerial Refueling"),
        ("special_operations", "Special Operations"),
        ("other_unclassified", "Other / Unclassified"),
    ]

    OPERATIONAL_STATUS_CHOICES = [
        ("available", "Available"),
        ("scheduled", "Scheduled"),
        ("in_flight", "In Flight"),
        ("maintenance", "Maintenance"),
        ("grounded", "Grounded"),
        ("unserviceable", "Unserviceable"),
        ("reserved", "Reserved"),
    ]

    FIREPOWER_CAPABILITY_CHOICES = [
        ("gun_system", "Gun System"),
        ("air_to_air", "Air-to-Air Capability"),
        ("air_to_ground", "Air-to-Ground Capability"),
        ("precision_strike", "Precision Strike Capability"),
        ("defensive_systems", "Defensive Systems"),
        ("electronic_warfare", "Electronic Warfare"),
        ("multi_role", "Multi-Role Capability"),
        ("none", "No Firepower Capability"),
        ("other_unclassified", "Other / Unclassified"),
    ]

    MUNITIONS_CATEGORY_CHOICES = [
        ("air_to_air", "Air-to-Air Munitions"),
        ("air_to_ground", "Air-to-Ground Munitions"),
        ("guided", "Guided Munitions"),
        ("unguided", "Unguided Munitions"),
        ("gun_ammunition", "Gun Ammunition"),
        ("training", "Training Munitions"),
        ("practice", "Practice Munitions"),
        ("other_unclassified", "Other / Unclassified"),
    ]

    aircraft_type = models.CharField(max_length=20, choices=AIRCRAFT_TYPE_CHOICES)
    categories = models.JSONField(
        default=list,
        blank=True,
        help_text="One or more CATEGORY_CHOICES keys.",
    )
    model = models.CharField(max_length=140)
    tail_number = models.CharField(max_length=60)
    callsign = models.CharField(max_length=60, blank=True, default="")
    cruising_speed_knots = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(2000)],
        help_text="Cruising speed used when calculating flight ETAs.",
    )
    current_location = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Operational-mapping location: name, place and MGRS grid.",
    )
    assigned_unit = models.CharField(
        max_length=50,
        choices=ASSIGNED_UNIT_CHOICES,
    )
    opcon_unit = models.ForeignKey(
        "legacy.Unit",
        db_column="opcon_unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="air_assets",
    )
    operational_status = models.CharField(
        max_length=20,
        choices=OPERATIONAL_STATUS_CHOICES,
        default="available",
    )
    firepower_capabilities = models.JSONField(
        default=list,
        blank=True,
        help_text="Zero or more FIREPOWER_CAPABILITY_CHOICES keys.",
    )
    munitions_categories = models.JSONField(
        default=list,
        blank=True,
        help_text="Zero or more MUNITIONS_CATEGORY_CHOICES keys.",
    )

    class Meta:
        db_table = "ops_air_asset"
        ordering = ["model", "tail_number"]
        indexes = [
            models.Index(fields=["aircraft_type"], name="ops_air_asset_type_idx"),
            models.Index(fields=["operational_status"], name="ops_air_asset_status_idx"),
            models.Index(fields=["opcon_unit"], name="ops_air_asset_opcon_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["aircraft_type", "model", "tail_number"],
                name="ops_air_asset_identity_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.model} ({self.tail_number})"


class AirMissionAsset(models.Model):
    """One aircraft assigned to an air mission formation.

    The register row is the live reference; the identity fields are a snapshot
    so a mission still reads correctly if an aircraft is later retired from the
    register. ``is_lead`` marks the formation lead and mirrors the mission
    detail's legacy single ``air_asset`` pointer.
    """

    air_detail = models.ForeignKey(
        AirMissionDetail,
        on_delete=models.CASCADE,
        related_name="assigned_aircraft",
    )
    air_asset = models.ForeignKey(
        "AirAsset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mission_assignments",
    )
    sequence = models.PositiveSmallIntegerField(default=1)
    is_lead = models.BooleanField(default=False)
    aircraft_model = models.CharField(max_length=140, blank=True, default="")
    tail_number = models.CharField(max_length=60, blank=True, default="")
    callsign = models.CharField(max_length=60, blank=True, default="")
    element_role = models.CharField(
        max_length=20,
        choices=[
            ("element_lead", "Element Lead"),
            ("wing_man", "Wing Man"),
        ],
        default="wing_man",
    )
    pilot_in_command = models.CharField(max_length=140, blank=True, default="")
    co_pilot = models.CharField(max_length=140, blank=True, default="")

    class Meta:
        db_table = "ops_air_mission_asset"
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["air_detail", "sequence"],
                name="ops_air_mission_asset_sequence_uniq",
            ),
            models.UniqueConstraint(
                fields=["air_detail", "air_asset"],
                condition=models.Q(air_asset__isnull=False),
                name="ops_air_mission_asset_aircraft_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.aircraft_model} ({self.tail_number})"


class InventoryTransaction(models.Model):
    """Immutable ledger entry for every stock movement."""

    KIND_CHOICES = [
        ("receipt", "Receipt"),
        ("issue", "Issue"),
        ("return", "Return"),
        ("transfer", "Transfer"),
        ("maintenance", "Maintenance"),
        ("inspection", "Inspection"),
        ("adjustment", "Adjustment"),
    ]

    item = models.ForeignKey(
        InventoryItem, on_delete=models.PROTECT, related_name="transactions"
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    quantity = models.PositiveIntegerField(default=1)
    actor = models.ForeignKey(
        "accounts.User",
        db_column="actor_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="inventory_transactions",
    )
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
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    mission = models.ForeignKey(
        Mission,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inventory_transactions",
    )
    reference = models.CharField(max_length=120, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ops_inventory_transaction"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["item", "-created_at"], name="ops_txn_item_time_idx"),
            models.Index(fields=["kind"], name="ops_txn_kind_idx"),
        ]


class AirFuelDraw(TimestampedModel):
    """POL-01 an aircraft burned, confirmed by the crew at engine shutdown.

    The store's own unit is the accounting unit: a drum-held batch leaves the
    register in whole drums, a bulk tank in whole litres. ``litres`` carries
    the fuel actually burned for consumption reporting, never a fraction of
    store stock.
    """

    SOURCE_DRUM = "drum"
    SOURCE_BULK = "bulk"
    SOURCE_CHOICES = [
        (SOURCE_DRUM, "Drums"),
        (SOURCE_BULK, "Bulk (litres)"),
    ]

    #: ``pending`` is a recorded draw the store could not cover yet: the crew's
    #: figure is kept, no ledger entry is written, and Logistics clears it when
    #: the stock lands.
    STATE_POSTED = "posted"
    STATE_PENDING = "pending"
    #: The crew confirmed the sortie burned nothing to record. It clears the
    #: mission's outstanding list without any stock leaving the register.
    STATE_NONE = "none"
    STATE_VOID = "void"
    STATE_CHOICES = [
        (STATE_POSTED, "Posted to stock"),
        (STATE_PENDING, "Awaiting stock"),
        (STATE_NONE, "No fuel consumed"),
        (STATE_VOID, "Void"),
    ]

    mission = models.ForeignKey(
        Mission, on_delete=models.CASCADE, related_name="air_fuel_draws"
    )
    aircraft = models.ForeignKey(
        AirMissionAircraft, on_delete=models.CASCADE, related_name="fuel_draws"
    )
    #: The itinerary leg whose shutdown the figure was confirmed at, so a
    #: multi-leg sortie records the POL-01 it burned leg by leg.
    leg = models.ForeignKey(
        AirMissionItinerary,
        on_delete=models.CASCADE,
        related_name="fuel_draws",
        help_text="The itinerary leg this figure was recorded for.",
    )
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    quantity = models.PositiveIntegerField(
        help_text="Whole drums, or whole litres for bulk stock."
    )
    litres = models.DecimalField(
        max_digits=10,
        decimal_places=1,
        default=0,
        help_text="Litres the draw represents, for reporting only.",
    )
    item = models.ForeignKey(
        InventoryItem,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="air_fuel_draws",
    )
    transaction = models.ForeignKey(
        InventoryTransaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="air_fuel_draws",
    )
    state = models.CharField(
        max_length=10, choices=STATE_CHOICES, default=STATE_POSTED
    )
    no_consumption = models.BooleanField(
        default=False,
        help_text="The crew confirmed this sortie burned no POL-01 to record.",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    recorded_by = models.ForeignKey(
        "accounts.User",
        db_column="recorded_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="air_fuel_draws",
    )

    class Meta:
        db_table = "ops_air_fuel_draw"
        ordering = ["aircraft__sequence", "leg__sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["aircraft", "leg"], name="ops_air_fuel_draw_leg_uniq"
            )
        ]

    def __str__(self) -> str:
        return f"{self.aircraft_id} L{self.leg_id}: {self.quantity} {self.source}"


class InventoryRequest(TimestampedModel):
    """A stock change awaiting Logistics Officer approval.

    Official balances move only when this row reaches ``approved`` and the
    service layer writes the ledger entry (spec §9C: a pending request must
    never change stock).
    """

    # ``transfer`` is deliberately absent: a transfer needs a destination unit,
    # which this request form does not collect, so it stays on the direct
    # movement endpoint where the destination is explicit.
    KIND_CHOICES = [
        ("receipt", "Receipt"),
        ("issue", "Issue"),
        ("return", "Return"),
        ("adjustment", "Adjustment"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_RETURNED = "returned"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_RETURNED, "Returned for correction"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    item = models.ForeignKey(
        InventoryItem, on_delete=models.PROTECT, related_name="requests"
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="inventory_requests",
    )
    mission = models.ForeignKey(
        Mission,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inventory_requests",
    )
    justification = models.TextField(blank=True, default="")

    requested_by = models.ForeignKey(
        "accounts.User",
        db_column="requested_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="inventory_requests",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    decided_by = models.ForeignKey(
        "accounts.User",
        db_column="decided_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")
    committed_transaction = models.ForeignKey(
        InventoryTransaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_request",
    )

    class Meta:
        db_table = "ops_inventory_request"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="ops_invreq_status_idx"),
            models.Index(fields=["requested_by", "status"], name="ops_invreq_requester_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.quantity}x {self.item_id} ({self.status})"


class Vehicle(TimestampedModel):
    STATUS_CHOICES = [
        ("available", "Available"),
        ("on_trip", "On Trip"),
        ("maintenance", "Under Maintenance"),
        ("unserviceable", "Unserviceable"),
    ]

    # ``registration_number`` is the plate number - the identifier painted on the
    # vehicle. ``vehicle_type``/``make_model`` are the older, combined columns:
    # they stay filled so documents and labels that still read them keep working,
    # while ``wheels``/``make``/``model`` below are the register's own fields.
    registration_number = models.CharField(max_length=40, unique=True)
    vehicle_type = models.CharField(max_length=80, blank=True, default="")
    make_model = models.CharField(max_length=120, blank=True, default="")
    wheels = models.PositiveSmallIntegerField(default=4)
    make = models.CharField(max_length=80, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    registration_no = models.CharField(max_length=60, blank=True, default="")
    official_receipt_no = models.CharField(max_length=60, blank=True, default="")
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="vehicles",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="available")
    capacity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    # -- privately owned vehicles (POV) ----------------------------------
    # A POV belongs to a person, not to the unit's register: the Operations
    # Center describes it (type, model, fuel, owner) when planning that person's
    # trip. It is stored as a vehicle so its driver, itinerary, planned route and
    # movement record are the same objects every other vehicle uses - but it is
    # kept out of the register and the fleet screens.
    is_pov = models.BooleanField(default=False)
    owner = models.ForeignKey(
        "accounts.User",
        db_column="owner_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="pov_vehicles",
    )
    fuel_type = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        db_table = "ops_vehicle"
        ordering = ["registration_number"]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        """How a vehicle reads everywhere: MAKE MODEL PLATE (KIA KM450 12389)."""
        make = (self.make or self.make_model or "").strip()
        model = (self.model or self.vehicle_type or "").strip()
        return " ".join(
            part for part in (make, model, self.registration_number) if part
        )


class VehicleTrip(TimestampedModel):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("planned", "Planned"),
        ("approved", "Approved"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    trip_number = models.CharField(max_length=40, unique=True)
    title = models.CharField(max_length=200)
    purpose = models.TextField(blank=True, default="")
    mission = models.ForeignKey(
        Mission, null=True, blank=True, on_delete=models.SET_NULL, related_name="trips"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    departure_at = models.DateTimeField(null=True, blank=True)
    return_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="created_trips",
    )

    # -- planned road route (spec §5.3) -----------------------------------
    # Cached output of the routing engine for this trip's ordered itinerary.
    # This is a *plan*, never a GPS track: nothing here is updated by vehicle
    # movement. ``route_status`` records whether the last calculation succeeded
    # so the UI can explain a failure instead of drawing a straight line.
    ROUTE_NOT_CALCULATED = "not_calculated"
    ROUTE_CALCULATED = "calculated"
    ROUTE_FAILED = "failed"
    ROUTE_STATUS_CHOICES = [
        (ROUTE_NOT_CALCULATED, "Not calculated"),
        (ROUTE_CALCULATED, "Calculated"),
        (ROUTE_FAILED, "Calculation failed"),
    ]

    route_status = models.CharField(
        max_length=20, choices=ROUTE_STATUS_CHOICES, default=ROUTE_NOT_CALCULATED
    )
    route_geometry = models.JSONField(null=True, blank=True)
    route_legs = models.JSONField(null=True, blank=True)
    route_distance_m = models.FloatField(null=True, blank=True)
    route_duration_s = models.FloatField(null=True, blank=True)
    route_provider = models.CharField(max_length=120, blank=True, default="")
    route_calculated_at = models.DateTimeField(null=True, blank=True)
    route_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_vehicle_trip"
        ordering = ["-departure_at", "-id"]
        indexes = [models.Index(fields=["status"], name="ops_trip_status_idx")]

    def __str__(self) -> str:
        return f"{self.trip_number} - {self.title}"


class TripItinerary(models.Model):
    trip = models.ForeignKey(
        VehicleTrip, on_delete=models.CASCADE, related_name="itinerary"
    )
    sequence = models.PositiveIntegerField()
    location_name = models.CharField(max_length=255)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    mgrs = models.CharField(max_length=32, blank=True, default="")
    planned_departure = models.DateTimeField(null=True, blank=True)
    planned_arrival = models.DateTimeField(null=True, blank=True)
    purpose = models.CharField(max_length=255, blank=True, default="")
    remarks = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_trip_itinerary"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["trip", "sequence"], name="ops_itinerary_trip_seq_unique"
            )
        ]


class VehicleTripVehicle(models.Model):
    trip = models.ForeignKey(
        VehicleTrip, on_delete=models.CASCADE, related_name="vehicles"
    )
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="trip_assignments"
    )
    driver = models.ForeignKey(
        "accounts.User",
        db_column="driver_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="driven_trips",
    )
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "ops_vehicle_trip_vehicle"
        constraints = [
            models.UniqueConstraint(
                fields=["trip", "vehicle"], name="ops_trip_vehicle_unique"
            )
        ]


class VehicleRun(TimestampedModel):
    """One vehicle's movement record for one mission trip.

    The planned route stays on ``VehicleTrip``. A run is the *execution* of that
    plan: its ETD is copied from the trip, its ATD is recorded when the driver
    (or the lead/Operations Center) confirms departure, and its events and
    pings are append-only evidence. One vehicle can only have one run per trip.
    """

    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Awaiting departure"),
        (STATUS_ACTIVE, "In progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    ATD_DRIVER = "driver"
    ATD_LEAD = "lead"
    ATD_OPERATIONS_CENTER = "operations_center"
    ATD_AUTO_DISPATCH = "auto_dispatch"
    ATD_SOURCE_CHOICES = [
        (ATD_DRIVER, "Driver confirmation"),
        (ATD_LEAD, "Convoy lead confirmation"),
        (ATD_OPERATIONS_CENTER, "Operations Center confirmation"),
        (ATD_AUTO_DISPATCH, "Dispatch time (unconfirmed)"),
    ]

    mission = models.ForeignKey(
        Mission, on_delete=models.CASCADE, related_name="vehicle_runs"
    )
    trip = models.ForeignKey(
        VehicleTrip, on_delete=models.CASCADE, related_name="runs"
    )
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="runs"
    )
    driver = models.ForeignKey(
        "accounts.User",
        db_column="driver_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="vehicle_runs",
    )
    #: Exactly one run per mission is the convoy lead. The constraint is below.
    is_lead = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    #: Planned departure, copied from the trip when the run is created.
    etd = models.DateTimeField(null=True, blank=True)
    #: Actual departure, set only by a person or by an explicit dispatch fallback.
    atd = models.DateTimeField(null=True, blank=True)
    atd_source = models.CharField(
        max_length=20, choices=ATD_SOURCE_CHOICES, blank=True, default=""
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    #: Denormalised latest ping, so the monitor board does not scan pings.
    last_ping_at = models.DateTimeField(null=True, blank=True)
    last_latitude = models.FloatField(null=True, blank=True)
    last_longitude = models.FloatField(null=True, blank=True)
    last_accuracy_m = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_vehicle_run"
        ordering = ["mission_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "trip", "vehicle"],
                name="ops_vehicle_run_unique",
            ),
            models.UniqueConstraint(
                fields=["mission"],
                condition=models.Q(is_lead=True),
                name="ops_vehicle_run_one_lead",
            ),
        ]
        indexes = [
            models.Index(
                fields=["mission", "status"], name="ops_vehicle_run_status_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle.registration_number} — {self.trip.trip_number}"


class MovementEvent(models.Model):
    """An append-only actual-time log for a vehicle run.

    ``planned_at`` keeps the itinerary's planned time beside the actual time;
    ``actual_at`` is what the driver, lead or Operations Center recorded. A GPS
    suggestion is a separate source and never silently becomes the official time.
    """

    KIND_DEPART_ORIGIN = "depart_origin"
    KIND_ARRIVE_STOP = "arrive_stop"
    KIND_DEPART_STOP = "depart_stop"
    KIND_ARRIVE_DESTINATION = "arrive_destination"
    KIND_RETURN_ORIGIN = "return_origin"
    KIND_COMPLETE = "complete"
    KIND_PAUSE = "pause"
    KIND_RESUME = "resume"
    KIND_CANCEL = "cancel"
    KIND_EXCEPTION = "exception"
    KIND_CHOICES = [
        (KIND_DEPART_ORIGIN, "Depart origin"),
        (KIND_ARRIVE_STOP, "Arrive stop"),
        (KIND_DEPART_STOP, "Depart stop"),
        (KIND_ARRIVE_DESTINATION, "Arrive destination"),
        (KIND_RETURN_ORIGIN, "Return to origin"),
        (KIND_COMPLETE, "Complete run"),
        (KIND_PAUSE, "Pause"),
        (KIND_RESUME, "Resume"),
        (KIND_CANCEL, "Cancel"),
        (KIND_EXCEPTION, "Exception"),
    ]

    SOURCE_DRIVER = "driver"
    SOURCE_LEAD = "lead"
    SOURCE_OPERATIONS_CENTER = "operations_center"
    SOURCE_GPS_SUGGESTED = "gps_suggested"
    SOURCE_AUTO_DISPATCH = "auto_dispatch"
    SOURCE_SYSTEM = "system"
    SOURCE_CHOICES = [
        (SOURCE_DRIVER, "Driver"),
        (SOURCE_LEAD, "Convoy lead"),
        (SOURCE_OPERATIONS_CENTER, "Operations Center"),
        (SOURCE_GPS_SUGGESTED, "GPS suggestion"),
        (SOURCE_AUTO_DISPATCH, "Dispatch fallback"),
        (SOURCE_SYSTEM, "System"),
    ]

    run = models.ForeignKey(
        VehicleRun, on_delete=models.CASCADE, related_name="events"
    )
    stop = models.ForeignKey(
        TripItinerary,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movement_events",
    )
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    planned_at = models.DateTimeField(null=True, blank=True)
    actual_at = models.DateTimeField()
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default=SOURCE_DRIVER
    )
    recorded_by = models.ForeignKey(
        "accounts.User",
        db_column="recorded_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="movement_events",
    )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ops_movement_event"
        ordering = ["actual_at", "id"]
        indexes = [
            models.Index(
                fields=["run", "actual_at"], name="ops_movement_event_time_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} {self.kind} @ {self.actual_at}"


class TrackingPing(models.Model):
    """A raw GPS breadcrumb from a driver's phone.

    This is evidence of where the phone was, not an official arrival time. Both
    the device time and the server receive time are kept so a wrong phone clock
    can be seen rather than silently trusted.
    """

    SOURCE_MOBILE = "mobile"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_MOBILE, "Driver mobile"),
        (SOURCE_MANUAL, "Manual report"),
    ]

    run = models.ForeignKey(
        VehicleRun, on_delete=models.CASCADE, related_name="pings"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy_m = models.FloatField(null=True, blank=True)
    speed_mps = models.FloatField(null=True, blank=True)
    heading_deg = models.FloatField(null=True, blank=True)
    recorded_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MOBILE
    )

    class Meta:
        db_table = "ops_tracking_ping"
        ordering = ["-received_at", "-id"]
        indexes = [
            models.Index(
                fields=["run", "-received_at"], name="ops_tracking_ping_run_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} @ {self.recorded_at}"


class AdministrativeArea(TimestampedModel):
    """A Philippine province, district, city or municipality (PSGC).

    Imported from the bundled PSA/PSGC boundary dataset by
    ``manage.py import_admin_boundaries``. The geometry is the authoritative
    boundary used for AOR assignment and conflict detection; nothing here is
    invented or approximated.
    """

    LEVEL_PROVINCE = "province"
    LEVEL_DISTRICT = "district"
    LEVEL_CITY = "city"
    LEVEL_MUNICIPALITY = "municipality"
    LEVEL_CHOICES = [
        (LEVEL_PROVINCE, "Province"),
        (LEVEL_DISTRICT, "District / special area"),
        (LEVEL_CITY, "City"),
        (LEVEL_MUNICIPALITY, "Municipality"),
    ]

    psgc = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=160)
    #: Lower-cased name and aliases, so search is case-insensitive and partial.
    search_text = models.TextField(blank=True, default="")
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, db_index=True)
    parent_psgc = models.BigIntegerField(null=True, blank=True, db_index=True)
    parent_name = models.CharField(max_length=160, blank=True, default="")
    region_psgc = models.BigIntegerField(null=True, blank=True)
    region_name = models.CharField(max_length=160, blank=True, default="")
    area_km2 = models.FloatField(null=True, blank=True)

    geometry = models.JSONField(help_text="GeoJSON Polygon or MultiPolygon (WGS84).")
    min_lon = models.FloatField(null=True, blank=True)
    min_lat = models.FloatField(null=True, blank=True)
    max_lon = models.FloatField(null=True, blank=True)
    max_lat = models.FloatField(null=True, blank=True)
    centroid_lon = models.FloatField(null=True, blank=True)
    centroid_lat = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "ops_administrative_area"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["level", "name"], name="ops_adminarea_lvl_name_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_level_display()})"


class AreaOfResponsibility(TimestampedModel):
    """AOR polygon stored as GeoJSON (no PostGIS required).

    An AOR is either drawn directly (``admin_area`` is null, legacy behaviour) or
    created from an official administrative boundary, in which case
    ``admin_area`` records which PSGC area the polygon represents.
    """

    name = models.CharField(max_length=160)
    code = models.CharField(max_length=40, unique=True)
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="aors",
    )
    admin_area = models.ForeignKey(
        AdministrativeArea,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="aors",
        db_column="admin_area_id",
    )
    geometry = models.JSONField(help_text="GeoJSON Polygon or MultiPolygon (WGS84).")
    color = models.CharField(max_length=20, default="#2b6cb0")
    description = models.TextField(blank=True, default="")

    # Derived, denormalised so filtering/plotting needs no JSON parsing.
    min_lon = models.FloatField(null=True, blank=True)
    min_lat = models.FloatField(null=True, blank=True)
    max_lon = models.FloatField(null=True, blank=True)
    max_lat = models.FloatField(null=True, blank=True)
    centroid_lon = models.FloatField(null=True, blank=True)
    centroid_lat = models.FloatField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="created_aors",
    )

    class Meta:
        db_table = "ops_area_of_responsibility"
        ordering = ["name"]
        indexes = [models.Index(fields=["unit"], name="ops_aor_unit_idx")]
        constraints = [
            # One active AOR row per administrative area: the same province or
            # city cannot be assigned to two units at once. Inactive rows keep
            # the reassignment history. PostgreSQL and SQLite both support a
            # partial unique index.
            models.UniqueConstraint(
                fields=["admin_area"],
                condition=models.Q(is_active=True, admin_area__isnull=False),
                name="ops_aor_active_adminarea_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class OperationalMapFeature(TimestampedModel):
    """A plotted operational-map feature (no PostGIS required).

    The Operations Center keeps its working picture here: friendly troop
    dispositions and frequently used locations are points, operational areas
    are polygons. Geometries are GeoJSON in WGS84, like AOR coverage, so the
    same map components and coordinate helpers apply.
    """

    TYPE_FRIENDLY_TROOP = "friendly_troop"
    TYPE_FREQUENT_LOCATION = "frequent_location"
    TYPE_OPERATIONAL_AREA = "operational_area"
    TYPE_CHOICES = [
        (TYPE_FRIENDLY_TROOP, "Friendly troops"),
        (TYPE_FREQUENT_LOCATION, "Frequently searched location"),
        (TYPE_OPERATIONAL_AREA, "Operational area"),
    ]
    POINT_TYPES = frozenset({TYPE_FRIENDLY_TROOP, TYPE_FREQUENT_LOCATION})

    # -- friendly-troop details -------------------------------------------
    # A plotted friendly unit carries the same particulars a mission needs when
    # it names the unit it supports, so the two registers never disagree.
    SERVICE_ARMY = "army"
    SERVICE_NAVY = "navy"
    SERVICE_PNP = "pnp"
    SERVICE_COAST_GUARD = "coast_guard"
    SERVICE_CATEGORY_CHOICES = [
        (SERVICE_ARMY, "Philippine Army"),
        (SERVICE_NAVY, "Philippine Navy"),
        (SERVICE_PNP, "Philippine National Police"),
        (SERVICE_COAST_GUARD, "Coast Guard"),
    ]

    UNIT_DIVISION = "division"
    UNIT_BRIGADE = "brigade"
    UNIT_BATTALION = "battalion"
    UNIT_COMPANY = "company"
    UNIT_CATEGORY_CHOICES = [
        (UNIT_DIVISION, "Division"),
        (UNIT_BRIGADE, "Brigade"),
        (UNIT_BATTALION, "Battalion"),
        (UNIT_COMPANY, "Company"),
    ]

    service_category = models.CharField(
        max_length=20, choices=SERVICE_CATEGORY_CHOICES, blank=True, default=""
    )
    unit_category = models.CharField(
        max_length=20, choices=UNIT_CATEGORY_CHOICES, blank=True, default=""
    )
    #: The unit's short name (``name`` above is how the pin reads on the map).
    acronym = models.CharField(max_length=40, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    mgrs = models.CharField(max_length=32, blank=True, default="")
    contact_number = models.CharField(max_length=40, blank=True, default="")

    name = models.CharField(max_length=160)
    feature_type = models.CharField(
        max_length=30, choices=TYPE_CHOICES, default=TYPE_FRIENDLY_TROOP
    )
    description = models.TextField(blank=True, default="")
    geometry = models.JSONField(
        help_text="GeoJSON Point or Polygon (WGS84). Point for troops and places, polygon for areas."
    )
    color = models.CharField(max_length=20, default="#4f9cf9")
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="operational_map_features",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="created_operational_map_features",
    )

    class Meta:
        db_table = "ops_operational_map_feature"
        ordering = ["feature_type", "name"]
        indexes = [models.Index(fields=["feature_type"], name="ops_opmap_type_idx")]

    def __str__(self) -> str:
        return f"{self.get_feature_type_display()} - {self.name}"


class RiskAssessment(TimestampedModel):
    """Links an existing MRM form to a TOWEMS mission.

    The legacy ``mrm_forms`` row remains the risk record of truth; this table
    adds the mission link and the evaluator assignment without touching it.
    """

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("under_review", "Under Review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    mission = models.OneToOneField(
        Mission, on_delete=models.CASCADE, related_name="risk_assessment"
    )
    mrm_form = models.ForeignKey(
        "legacy.MRMForm",
        db_column="mrm_form_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="toms_risk_links",
    )
    evaluator = models.ForeignKey(
        "accounts.User",
        db_column="evaluator_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="evaluations",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    max_risk = models.IntegerField(null=True, blank=True)
    residual_risk = models.IntegerField(null=True, blank=True)
    total_percent = models.FloatField(null=True, blank=True)
    risk_category = models.CharField(max_length=20, blank=True, default="")
    assessed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "accounts.User",
        db_column="approved_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_risk_assessment"


class Notification(models.Model):
    """A recipient's notification.

    An *action* notification (``requires_action=True``) is a durable work item:
    reading it sets ``is_read`` only. It leaves the open state exclusively via
    ``acknowledged_at`` / ``completed_at``, which the owning workflow records and
    the API audits. Nothing about reading, refreshing or logging out can clear
    ``action_status`` - that column is the single source of truth for the bell.
    """

    LEVEL_CHOICES = [
        ("info", "Info"),
        ("success", "Success"),
        ("warning", "Warning"),
        ("danger", "Critical"),
    ]

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]

    ACTION_NONE = "none"
    ACTION_OPEN = "open"
    ACTION_IN_PROGRESS = "in_progress"
    ACTION_COMPLETED = "completed"
    ACTION_CHOICES = [
        (ACTION_NONE, "Informational"),
        (ACTION_OPEN, "Action required"),
        (ACTION_IN_PROGRESS, "In progress"),
        (ACTION_COMPLETED, "Addressed"),
    ]

    recipient = models.ForeignKey(
        "accounts.User",
        db_column="recipient_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True, default="")
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="info")
    category = models.CharField(max_length=40, blank=True, default="")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    related_type = models.CharField(max_length=60, blank=True, default="")
    related_id = models.CharField(max_length=60, blank=True, default="")
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

    # --- enhancement: persistent action notifications ---------------------
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default="normal"
    )
    requires_action = models.BooleanField(default=False)
    action_status = models.CharField(
        max_length=20, choices=ACTION_CHOICES, default=ACTION_NONE
    )
    action_label = models.CharField(max_length=120, blank=True, default="")
    action_url = models.CharField(max_length=300, blank=True, default="")
    due_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        "accounts.User",
        db_column="acknowledged_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        "accounts.User",
        db_column="completed_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    completion_note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ops_notification"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "is_read"], name="ops_notif_recipient_idx"),
            models.Index(
                fields=["recipient", "action_status"], name="ops_notif_action_idx"
            ),
        ]

    @property
    def is_pending_action(self) -> bool:
        """True while the recipient still owes work. Reading never changes it."""
        return bool(
            self.requires_action
            and self.action_status in {self.ACTION_OPEN, self.ACTION_IN_PROGRESS}
        )


class AppendOnlyQuerySet(models.QuerySet):
    """Blocks the bulk paths that would otherwise bypass ``Model.save``."""

    def update(self, **kwargs):
        raise ValueError("Audit records are append-only: bulk update is refused.")

    def delete(self):
        raise ValueError("Audit records cannot be deleted.")


class MRMAttentionDismissal(models.Model):
    """An officer's personal "hide" of one MRM on the dashboard.

    Dismissals are per account, not per record: hiding an assessment only
    clears it from that officer's *Assessments needing attention* list, while
    the assessment itself remains untouched in the risk register. The paired
    restore endpoint removes the row, so the action is reversible.
    """

    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="mrm_attention_dismissals",
    )
    mrm_form = models.ForeignKey(
        "legacy.MRMForm",
        db_column="mrm_form_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="attention_dismissals",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ops_mrm_attention_dismissal"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "mrm_form"],
                name="ops_mrm_attention_dismissal_unique",
            )
        ]

    def __str__(self) -> str:
        return f"MRM {self.mrm_form_id} hidden by user {self.user_id}"


class AuditLog(models.Model):
    """TOWEMS audit trail (additive; the legacy trail is preserved separately).

    Append-only by construction: the model refuses updates and deletes, the API
    exposes it read-only, and no Django admin is registered for it. Removing the
    *other* two guards would require a code change, which is itself reviewed and
    visible in the repository - an operator cannot silently rewrite history from
    the application.
    """

    actor = models.ForeignKey(
        "accounts.User",
        db_column="actor_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="toms_audit_entries",
    )
    action = models.CharField(max_length=40)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=64, blank=True, default="")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    note = models.TextField(blank=True, default="")
    ip_address = models.CharField(max_length=45, blank=True, default="")
    user_agent = models.CharField(max_length=400, blank=True, default="")
    correlation_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "core_audit_log"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="core_audit_entity_idx"),
            models.Index(fields=["actor", "-created_at"], name="core_audit_actor_idx"),
            models.Index(fields=["created_at"], name="core_audit_time_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(
                "Audit records are append-only and cannot be modified. "
                "Record a new entry instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "Audit records cannot be deleted. Retention is handled by the database "
            "operator, never by the application."
        )

    def update(self, *args, **kwargs):  # pragma: no cover - queryset guard
        raise ValueError("Audit records are append-only.")

    def refresh_from_db(self, *args, **kwargs):
        """Harmless read helper - kept writable so callers can re-read a row."""
        return super().refresh_from_db(*args, **kwargs)


class Airfield(models.Model):
    """An airport or air strip drawn on the operational map.

    Reference data, not an operational record: the register is shared by every
    account, because the strip a mission might use is the same fact for every
    unit. The Super Administrator maintains it; everyone reads it.
    """

    KIND_AIRPORT = "airport"
    KIND_AIRSTRIP = "airstrip"
    KIND_CHOICES = (
        (KIND_AIRPORT, "Airport"),
        (KIND_AIRSTRIP, "Air strip"),
    )

    code = models.CharField(
        max_length=10, unique=True, help_text="ICAO or local code, e.g. RPMY."
    )
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_AIRPORT)
    municipality = models.CharField(max_length=120, blank=True, default="")
    province = models.CharField(max_length=120, blank=True, default="")
    latitude = models.FloatField()
    longitude = models.FloatField()
    #: Derived on save/seed so the grid column matches the plotted point.
    mgrs = models.CharField(max_length=32, blank=True, default="")
    runway_length_m = models.PositiveIntegerField(null=True, blank=True)
    runway_width_m = models.PositiveIntegerField(null=True, blank=True)
    runway_orientation = models.CharField(max_length=20, blank=True, default="")
    elevation_m = models.IntegerField(null=True, blank=True)
    surface = models.CharField(max_length=40, blank=True, default="")
    operator = models.CharField(max_length=120, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["kind"], name="ops_airfield_kind_idx")]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"
