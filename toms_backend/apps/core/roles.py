"""Single authorisation policy layer for TOWEMS.

Why this module exists
======================
The legacy system decided access from one enum column, ``users.authority``,
whose values are fixed in a PostgreSQL enum type (``Administrator``,
``Evaluator``, ``Creator``, ``Unit Safety Officer``). That column cannot express
the TOWEMS command structure (Commander, Deputy Commander, Director for
Operations, Logistics Officer, Operations Officer, Admin Officer, Logistic
NCOIC) and it cannot scope a role to a unit.

Rather than altering the enum - which the data-preservation rules forbid - a
person's *appointments* are rows in ``command_unit_appointment``. This module
merges:

1. the legacy base role from ``users.authority`` (unchanged, global), and
2. active unit appointments (role scoped to a unit and its subordinates), and
3. active *acting* appointments (temporary delegation, auditable, expiring)

into a flat set of capability names. Every view asks this module, never the
frontend, never ``user.authority`` directly.

Capabilities are additive but never escalate: an appointment grants only what
its role declares, and an acting delegation may only narrow that set. No
appointment and no delegation can ever grant ``system_config``.
"""

from __future__ import annotations

import datetime as dt

# ---------------------------------------------------------------------------
# Base roles (legacy ``users.authority`` values) - never rename these.
# ---------------------------------------------------------------------------

BASE_ADMIN = "Administrator"
BASE_EVALUATOR = "Evaluator"
BASE_CREATOR = "Creator"
BASE_SAFETY = "Unit Safety Officer"

# ---------------------------------------------------------------------------
# Appointment roles (new table, not enum values)
# ---------------------------------------------------------------------------

ROLE_ADMINISTRATOR = "administrator"
ROLE_COMMANDER = "commander"
ROLE_DEPUTY_COMMANDER = "deputy_commander"
ROLE_DIRECTOR_OPERATIONS = "director_operations"
#: The Operations Center is an *office* inside a unit that oversees that unit's
#: TOWEMS: it receives, screens, coordinates and routes work. It is conferred by
#: membership of that office (``command_office_section.function``), not by a
#: personal appointment, so the authority belongs to the office and moves with
#: its staff.
ROLE_OPERATIONS_CENTER = "operations_center"
ROLE_SAFETY_OFFICER = "safety_officer"
ROLE_LOGISTICS_OFFICER = "logistics_officer"
ROLE_OPERATIONS_OFFICER = "operations_officer"
ROLE_ADMIN_OFFICER = "admin_officer"
ROLE_LOGISTICS_NCOIC = "logistics_ncoic"
ROLE_EVALUATOR = "evaluator"
ROLE_CREATOR = "creator"

APPOINTMENT_ROLE_CHOICES = [
    (ROLE_COMMANDER, "Commander"),
    (ROLE_DEPUTY_COMMANDER, "Deputy Commander"),
    (ROLE_DIRECTOR_OPERATIONS, "Director for Operations"),
    (ROLE_SAFETY_OFFICER, "Safety Officer"),
    (ROLE_LOGISTICS_OFFICER, "Logistics Officer"),
    (ROLE_OPERATIONS_OFFICER, "Operations Officer"),
    (ROLE_ADMIN_OFFICER, "Admin Officer"),
    (ROLE_LOGISTICS_NCOIC, "Logistic NCOIC"),
]

#: At most one *active* holder per unit. Enforced by a partial unique index.
EXCLUSIVE_APPOINTMENT_ROLES = (
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_DIRECTOR_OPERATIONS,
)

#: Posts that may be held by more than one person per unit.
SHARED_APPOINTMENT_ROLES = (
    ROLE_SAFETY_OFFICER,
    ROLE_LOGISTICS_OFFICER,
    ROLE_OPERATIONS_OFFICER,
    ROLE_ADMIN_OFFICER,
    ROLE_LOGISTICS_NCOIC,
)

ROLE_LABELS = dict(APPOINTMENT_ROLE_CHOICES)
ROLE_LABELS[ROLE_ADMINISTRATOR] = "Administrator"
ROLE_LABELS[ROLE_EVALUATOR] = "Evaluator"
ROLE_LABELS[ROLE_CREATOR] = "Creator"
#: Conferred by office membership rather than by a post.
ROLE_LABELS[ROLE_OPERATIONS_CENTER] = "Operations Center"

#: Read-only AOR/GIS viewer: command, operations and safety posts. The
#: Administrator keeps the management screen; this flag only opens the map-only
#: view. It is derived from effective roles, so an appointment or Operations
#: Center office membership is enough and a plain Evaluator/Creator is not.
AOR_VIEWER_ROLES = frozenset(
    {
        ROLE_ADMINISTRATOR,
        ROLE_COMMANDER,
        ROLE_DEPUTY_COMMANDER,
        ROLE_DIRECTOR_OPERATIONS,
        ROLE_OPERATIONS_OFFICER,
        ROLE_OPERATIONS_CENTER,
        ROLE_SAFETY_OFFICER,
    }
)

#: Values accepted by ``command_unit_appointment.role``. ``ROLE_LABELS`` is
#: deliberately wider: it also names the account roles, which are assigned
#: through ``users.authority``, and the office role, which is conferred by
#: office membership. Neither can ever be a unit appointment.
APPOINTMENT_ROLE_VALUES = tuple(value for value, _ in APPOINTMENT_ROLE_CHOICES)

#: Posts a person may hold in a *higher* headquarters in addition to their own
#: unit post - detached service (DS). Command is deliberately excluded: a person
#: may be DS as Director for Operations of a higher command, but never as its
#: Commander or Deputy Commander, because one person never commands two units.
DETACHED_SERVICE_ROLES = tuple(
    role
    for role in APPOINTMENT_ROLE_VALUES
    if role not in (ROLE_COMMANDER, ROLE_DEPUTY_COMMANDER)
)

#: Office function -> role granted to every active member of that office.
#: ``command_office_section.function`` stores the key; membership rows are
#: ``command_section_assignment`` (primary or secondary).
OFFICE_FUNCTION_ROLE_MAP = {
    "operations_center": ROLE_OPERATIONS_CENTER,
}
OFFICE_ROLE_VALUES = tuple(OFFICE_FUNCTION_ROLE_MAP.values())

#: Account roles live in ``users.authority``, a fixed PostgreSQL enum, and are
#: assigned per person. Appointments are rows in ``command_unit_appointment``
#: and are always scoped to one unit.
ACCOUNT_ROLE_VALUES = (
    ROLE_ADMINISTRATOR,
    ROLE_SAFETY_OFFICER,
    ROLE_EVALUATOR,
    ROLE_CREATOR,
)

#: One-line statement of each post's purpose, shown in the admin interface.
ROLE_DESCRIPTIONS = {
    ROLE_ADMINISTRATOR: "System administration, user and role management, and exclusive AOR management.",
    ROLE_COMMANDER: "Command oversight of the assigned unit and its authorised subordinate units.",
    ROLE_DEPUTY_COMMANDER: "Assists the Commander and exercises approved acting authority when formally designated.",
    ROLE_DIRECTOR_OPERATIONS: "Operational oversight, review of approved missions and instructions, and authorised routing.",
    ROLE_OPERATIONS_CENTER: (
        "Office that oversees the unit's TOWEMS: receives, monitors, coordinates and routes "
        "missions and other workflow items."
    ),
    ROLE_OPERATIONS_OFFICER: "Reviews and processes Air and Ground Operations within the assigned unit.",
    ROLE_ADMIN_OFFICER: "Administers authorised personnel information and office/section assignments.",
    ROLE_LOGISTICS_OFFICER: (
        "Owns the unit's inventory and vehicle register: records stock movements, decides "
        "inventory requests, and adds or retires vehicles."
    ),
    ROLE_LOGISTICS_NCOIC: "Prepares and routes logistics and inventory requests for the Logistics Officer's review.",
    ROLE_SAFETY_OFFICER: "Manages hazard records, safety information and MRM within authorised permissions.",
    ROLE_EVALUATOR: "Evaluates assigned MRM assessments and records the evaluation decision.",
    ROLE_CREATOR: "Creates MRM assessments and submits them through the required workflow.",
}

#: Human-readable capability names for the permission matrix shown to admins.
CAPABILITY_LABELS = {
    "view": "View records in scope",
    "create": "Create",
    "edit": "Edit",
    "delete": "Delete",
    "submit": "Submit for review",
    "review": "Review",
    "review_missions": "Review missions at the Operations Officer stage",
    "view_missions": "View missions in the assigned unit scope",
    "evaluate": "Evaluate MRM assessments",
    "approve": "Approve",
    "reject": "Reject",
    "return_for_correction": "Return for correction",
    "comment": "Add comments and instructions",
    "view_all_instructions": "See every instruction in scope, not only their own",
    "route": "Route to the next authority",
    "manage_sections": "Manage offices and sections",
    "manage_personnel": "Manage personnel records",
    "manage_missions": "Manage missions",
    "approve_missions": "Give the final mission decision",
    "manage_inventory": "Manage inventory",
    "record_air_fuel": "Record aircraft fuel consumption",
    "manage_vehicles": "Manage the vehicle register",
    "request_inventory": "Request inventory changes",
    "approve_inventory": "Approve inventory requests",
    "view_sensitive_inventory": "View sensitive inventory (firearms, ammunition)",
    "manage_appointments": "Assign and revoke unit appointments",
    "manage_acting_authority": "Delegate acting command",
    "view_analytics": "View reports and analytics",
    "view_subordinate_units": "See subordinate units",
    "monitor_vehicle_tracking": "Monitor live vehicle movement",
    "view_audit_log": "View the audit trail",
    "system_config": "Change system configuration",
    "manage_users": "Manage user accounts",
    "manage_aor": "Manage unit AOR coverage",
    "maintain_aor": "Edit, archive and restore AOR records in the assigned unit",
    "view_resources": "Open the unit's resource registers (personnel, offices, inventory, trips)",
    "issue_instructions": "Issue instructions and action items",
}

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

CAP_VIEW = "view"
CAP_CREATE = "create"
CAP_EDIT = "edit"
CAP_DELETE = "delete"
CAP_SUBMIT = "submit"
CAP_REVIEW = "review"
#: Acting at the Operations Officer (mission review) stage. Distinct from
#: ``review`` so the Operations Center can screen and route a mission without
#: inheriting the reviewer's decision authority.
CAP_REVIEW_MISSIONS = "review_missions"
#: Mission records are restricted: only command/officer posts and the
#: Operations Center office see them, plus personnel attached to a mission.
CAP_VIEW_MISSIONS = "view_missions"
CAP_EVALUATE = "evaluate"
CAP_APPROVE = "approve"
CAP_REJECT = "reject"
CAP_RETURN = "return_for_correction"
CAP_COMMENT = "comment"
CAP_ROUTE = "route"
CAP_MANAGE_SECTIONS = "manage_sections"
CAP_MANAGE_PERSONNEL = "manage_personnel"
CAP_MANAGE_MISSIONS = "manage_missions"
CAP_APPROVE_MISSIONS = "approve_missions"
CAP_MANAGE_INVENTORY = "manage_inventory"
#: Recording the POL-01 an airframe burned on an itinerary leg. The Logistics
#: Officer, the Logistic NCOIC and the Operations Center hold it.
CAP_RECORD_AIR_FUEL = "record_air_fuel"
#: The vehicle register (add, edit, retire a vehicle). Logistics owns it; the
#: Operations Center only *selects* existing vehicles when planning a mission.
CAP_MANAGE_VEHICLES = "manage_vehicles"
CAP_REQUEST_INVENTORY = "request_inventory"
CAP_APPROVE_INVENTORY = "approve_inventory"
CAP_VIEW_SENSITIVE_INVENTORY = "view_sensitive_inventory"
CAP_MANAGE_APPOINTMENTS = "manage_appointments"
CAP_MANAGE_ACTING = "manage_acting_authority"
CAP_VIEW_ANALYTICS = "view_analytics"
CAP_VIEW_SUBORDINATES = "view_subordinate_units"
CAP_VIEW_AUDIT = "view_audit_log"
#: Watch the live vehicle movement board for missions in scope. Drivers do
#: not need it: their access is scoped to the run they are actually driving.
CAP_MONITOR_VEHICLE_TRACKING = "monitor_vehicle_tracking"
CAP_SYSTEM_CONFIG = "system_config"
#: Account administration (deactivate, reactivate, password reset, role/unit
#: changes). Deliberately narrower than "administrator" in general: only the
#: Administrator role holds it.
CAP_MANAGE_USERS = "manage_users"
#: AOR boundary management (assign/transfer/remove administrative areas).
#: Administrator only - command, safety and operations roles keep read access.
CAP_MANAGE_AOR = "manage_aor"
#: Day-to-day upkeep of an existing AOR row - rename it, change its colour or
#: notes, archive it from active coverage and restore it. Held by the
#: Administrator and by the unit's Operations Center office for that unit only;
#: it deliberately does not carry the coverage powers above (assign, transfer,
#: redraw the boundary, or delete the record).
CAP_MAINTAIN_AOR = "maintain_aor"
#: The unit's institutional hazard library (the legacy ``hazard_registry``
#: table). The Unit Safety Officer maintains it; command, operations posts,
#: Creators and Evaluators read it; every other account cannot see it at all.
CAP_VIEW_HAZARDS = "view_hazards"
CAP_MANAGE_HAZARDS = "manage_hazards"
#: Auditing the post-mission feedback tagged personnel submit for an MRM.
CAP_AUDIT_FEEDBACK = "audit_post_mission_feedback"
#: The \"All visible\" view of instructions: every record in scope, not just the
#: ones addressed to or issued by the caller. Held by the Administrator, the
#: Commander, the Deputy Commander and the unit's Operations Center office; a
#: recipient keeps the narrower \"Addressed to me\" and \"Outstanding\" views.
CAP_VIEW_ALL_INSTRUCTIONS = "view_all_instructions"
#: The Resources modules - Personnel, Offices & Sections, Inventory and Vehicle
#: Trips. Staff and command work; the Creator and a plain Evaluator work on MRM
#: assessments instead. An Evaluator holding a command/staff designation (or the
#: Operations Center office) keeps them through that role.
CAP_VIEW_RESOURCES = "view_resources"
#: Issuing an instruction. A Creator receives instructions but never issues one.
CAP_ISSUE_INSTRUCTIONS = "issue_instructions"

#: Office *code* -> capabilities its active members hold for that office's unit.
#: This is deliberately separate from ``ROLE_CAPABILITIES``: an office is not a
#: role, and assigning somebody to staff office A3 gives them the office's
#: functional access without turning into an appointment or a system-wide role.
#: A3 - Operations is the office that plans missions (see
#: ``apps.command.offices.DEFAULT_OFFICE_CATALOGUE``); it grants mission
#: planning for its own unit only, never user, role or AOR administration.
OFFICE_CODE_CAPABILITIES: dict[str, frozenset[str]] = {
    "A3": frozenset({CAP_VIEW_MISSIONS, CAP_MANAGE_MISSIONS}),
}

ALL_CAPABILITIES = frozenset(
    {
        CAP_VIEW,
        CAP_CREATE,
        CAP_EDIT,
        CAP_DELETE,
        CAP_SUBMIT,
        CAP_REVIEW,
        CAP_REVIEW_MISSIONS,
        CAP_VIEW_MISSIONS,
        CAP_EVALUATE,
        CAP_APPROVE,
        CAP_REJECT,
        CAP_RETURN,
        CAP_COMMENT,
        CAP_ROUTE,
        CAP_MANAGE_SECTIONS,
        CAP_MANAGE_PERSONNEL,
        CAP_MANAGE_MISSIONS,
        CAP_APPROVE_MISSIONS,
        CAP_MANAGE_INVENTORY,
        CAP_RECORD_AIR_FUEL,
        CAP_MANAGE_VEHICLES,
        CAP_REQUEST_INVENTORY,
        CAP_APPROVE_INVENTORY,
        CAP_VIEW_SENSITIVE_INVENTORY,
        CAP_MANAGE_APPOINTMENTS,
        CAP_MANAGE_ACTING,
        CAP_VIEW_ANALYTICS,
        CAP_VIEW_SUBORDINATES,
        CAP_VIEW_AUDIT,
        CAP_MONITOR_VEHICLE_TRACKING,
        CAP_SYSTEM_CONFIG,
        CAP_MANAGE_USERS,
        CAP_MANAGE_AOR,
        CAP_MAINTAIN_AOR,
        CAP_VIEW_HAZARDS,
        CAP_MANAGE_HAZARDS,
        CAP_AUDIT_FEEDBACK,
        CAP_VIEW_ALL_INSTRUCTIONS,
        CAP_VIEW_RESOURCES,
        CAP_ISSUE_INSTRUCTIONS,
    }
)

_READ_ONLY = frozenset({CAP_VIEW})

#: The permission matrix. Documented in docs/ROLE_PERMISSION_MATRIX.md.
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    # -- system --------------------------------------------------------
    ROLE_ADMINISTRATOR: ALL_CAPABILITIES,
    # -- command -------------------------------------------------------
    ROLE_COMMANDER: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            CAP_COMMENT,
            CAP_VIEW_ALL_INSTRUCTIONS,
            CAP_ROUTE,
            CAP_APPROVE,
            CAP_REJECT,
            CAP_RETURN,
            CAP_APPROVE_MISSIONS,
            CAP_VIEW_ANALYTICS,
            CAP_VIEW_SUBORDINATES,
            CAP_MONITOR_VEHICLE_TRACKING,
            CAP_MANAGE_ACTING,
            # Command reads its unit's hazard library; changing it is the
            # Safety Officer's job.
            CAP_VIEW_HAZARDS,
        }
    ),
    ROLE_DEPUTY_COMMANDER: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            CAP_COMMENT,
            CAP_VIEW_ALL_INSTRUCTIONS,
            CAP_ROUTE,
            CAP_APPROVE,
            CAP_REJECT,
            CAP_RETURN,
            CAP_APPROVE_MISSIONS,
            CAP_VIEW_ANALYTICS,
            CAP_VIEW_SUBORDINATES,
            CAP_MONITOR_VEHICLE_TRACKING,
            CAP_VIEW_HAZARDS,
        }
    ),
    ROLE_DIRECTOR_OPERATIONS: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            CAP_COMMENT,
            CAP_ROUTE,
            CAP_VIEW_ANALYTICS,
            CAP_VIEW_SUBORDINATES,
            CAP_MONITOR_VEHICLE_TRACKING,
            CAP_VIEW_HAZARDS,
        }
    ),
    # The watch that receives missions. It screens and routes, but it holds no
    # approval authority: it cannot act at the Operations Officer stage and
    # cannot give the final mission decision.
    # Members of a unit's Operations Center office. Unit-only oversight: the
    # office watches its own unit, does not inherit command over subordinates,
    # and cannot review at the Operations Officer stage or approve.
    ROLE_OPERATIONS_CENTER: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            # The office is the unit's planning authority: it creates and
            # plans Air and Ground operations, then routes them onward.
            CAP_MANAGE_MISSIONS,
            CAP_COMMENT,
            CAP_VIEW_ALL_INSTRUCTIONS,
            CAP_ROUTE,
            CAP_VIEW_ANALYTICS,
            # The office that plans the sortie also records the POL-01 burned
            # on each itinerary leg, so a landed airframe is never left owing
            # a figure while Logistics is away from the desk.
            CAP_RECORD_AIR_FUEL,
            # Upkeep of the unit's own AOR record: rename, recolour, archive,
            # restore. Moving boundaries between units stays Administrator-only.
            CAP_MAINTAIN_AOR,
            CAP_MONITOR_VEHICLE_TRACKING,
        }
    ),
    # -- functional ----------------------------------------------------
    ROLE_SAFETY_OFFICER: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            CAP_CREATE,
            CAP_EDIT,
            CAP_SUBMIT,
            CAP_REVIEW,
            CAP_REVIEW_MISSIONS,
            CAP_EVALUATE,
            CAP_APPROVE,
            CAP_REJECT,
            CAP_RETURN,
            CAP_COMMENT,
            # Mission planning belongs to the Operations Center; the Safety
            # Officer keeps mission visibility and the final decision.
            CAP_APPROVE_MISSIONS,
            # Inventory is the Logistics Officer's register: safety keeps
            # read access (including controlled items) but no write rights.
            CAP_VIEW_SENSITIVE_INVENTORY,
            CAP_VIEW_ANALYTICS,
            CAP_VIEW_SUBORDINATES,
            CAP_MONITOR_VEHICLE_TRACKING,
            # The Safety Officer owns the hazard library and audits the
            # post-mission feedback for the unit.
            CAP_VIEW_HAZARDS,
            CAP_MANAGE_HAZARDS,
            CAP_AUDIT_FEEDBACK,
        }
    ),
    ROLE_LOGISTICS_OFFICER: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            CAP_COMMENT,
            CAP_APPROVE_INVENTORY,
            CAP_REJECT,
            CAP_RETURN,
            CAP_MANAGE_INVENTORY,
            CAP_RECORD_AIR_FUEL,
            CAP_MANAGE_VEHICLES,
            CAP_VIEW_SENSITIVE_INVENTORY,
            CAP_VIEW_ANALYTICS,
            CAP_VIEW_SUBORDINATES,
        }
    ),
    ROLE_LOGISTICS_NCOIC: frozenset(
        {
            CAP_VIEW,
            CAP_REQUEST_INVENTORY,
            # The NCOIC records the fuel figure the crew confirms at shutdown.
            CAP_RECORD_AIR_FUEL,
            CAP_COMMENT,
            CAP_VIEW_SUBORDINATES,
        }
    ),
    ROLE_OPERATIONS_OFFICER: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            CAP_COMMENT,
            CAP_REVIEW,
            CAP_REVIEW_MISSIONS,
            CAP_ROUTE,
            CAP_APPROVE,
            CAP_REJECT,
            CAP_RETURN,
            CAP_VIEW_ANALYTICS,
            CAP_MONITOR_VEHICLE_TRACKING,
            CAP_VIEW_HAZARDS,
        }
    ),
    ROLE_ADMIN_OFFICER: frozenset(
        {
            CAP_VIEW,
            CAP_VIEW_MISSIONS,
            CAP_COMMENT,
            CAP_MANAGE_SECTIONS,
            CAP_MANAGE_PERSONNEL,
            CAP_VIEW_SUBORDINATES,
        }
    ),
    # -- MRM -----------------------------------------------------------
    ROLE_EVALUATOR: frozenset(
        {
            CAP_VIEW,
            CAP_CREATE,
            CAP_SUBMIT,
            CAP_REVIEW,
            CAP_REVIEW_MISSIONS,
            CAP_EVALUATE,
            CAP_EDIT,
            CAP_COMMENT,
            # Evaluators read their unit's hazard library as reference while
            # reviewing assessments; maintaining it stays with the Safety
            # Officer. The menu entry follows this capability.
            CAP_VIEW_HAZARDS,
        }
    ),
    # Creators read the hazard library of their own unit only (see
    # ``apps.ops.hazards.read_unit_ids``); they do not reach subordinates.
    ROLE_CREATOR: frozenset(
        {CAP_VIEW, CAP_CREATE, CAP_SUBMIT, CAP_EDIT, CAP_COMMENT, CAP_VIEW_HAZARDS}
    ),
}

#: Resources and issuing instructions are command/staff functions. The Creator
#: account raises assessments and receives instructions, so it is deliberately
#: the one role without either - its Resources menu is empty and the server
#: refuses its attempts to issue.
for _staff_role in (
    ROLE_ADMINISTRATOR,
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_DIRECTOR_OPERATIONS,
    ROLE_OPERATIONS_CENTER,
    ROLE_SAFETY_OFFICER,
    ROLE_LOGISTICS_OFFICER,
    ROLE_LOGISTICS_NCOIC,
    ROLE_OPERATIONS_OFFICER,
    ROLE_ADMIN_OFFICER,
):
    ROLE_CAPABILITIES[_staff_role] = ROLE_CAPABILITIES[_staff_role] | {
        CAP_VIEW_RESOURCES,
        CAP_ISSUE_INSTRUCTIONS,
    }
del _staff_role

#: An Evaluator is an MRM account, not a staff post: on its own it does not open
#: the resource registers. Issuing instructions is unchanged for Evaluators.
ROLE_CAPABILITIES[ROLE_EVALUATOR] = ROLE_CAPABILITIES[ROLE_EVALUATOR] | {
    CAP_ISSUE_INSTRUCTIONS
}

#: The designations that also open the resource registers to an Evaluator
#: account. Each contributes ``CAP_VIEW_RESOURCES`` through its own role (the
#: Operations Center through office membership). Logistics NCOIC is deliberately
#: absent: it does not make an Evaluator a resource user.
EVALUATOR_RESOURCE_ROLES = frozenset(
    {
        ROLE_COMMANDER,
        ROLE_DEPUTY_COMMANDER,
        ROLE_DIRECTOR_OPERATIONS,
        ROLE_ADMIN_OFFICER,
        ROLE_LOGISTICS_OFFICER,
        ROLE_SAFETY_OFFICER,
        ROLE_OPERATIONS_OFFICER,
        ROLE_OPERATIONS_CENTER,
    }
)

#: Roles a user holds without any appointment, derived from the legacy column.
BASE_ROLE_MAP = {
    BASE_ADMIN: ROLE_ADMINISTRATOR,
    BASE_SAFETY: ROLE_SAFETY_OFFICER,
    BASE_EVALUATOR: ROLE_EVALUATOR,
    BASE_CREATOR: ROLE_CREATOR,
}

# ---------------------------------------------------------------------------
# Default approval rules (overridable per unit/category by ApprovalRule rows)
# ---------------------------------------------------------------------------

DEFAULT_MISSION_STEPS = [
    "operations_center",
    "operations_officer",
    "decision_authority",
]

DEFAULT_MRM_STEPS = ["evaluator", "decision_authority"]

DEFAULT_MISSION_APPROVER_ROLES = [
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_ADMINISTRATOR,
]

DEFAULT_MRM_APPROVER_ROLES = [
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_ADMINISTRATOR,
    ROLE_SAFETY_OFFICER,
]

#: Capabilities that can never be delegated to an acting appointment.
NON_DELEGABLE = frozenset(
    {
        CAP_SYSTEM_CONFIG,
        CAP_MANAGE_APPOINTMENTS,
        CAP_MANAGE_USERS,
        # AOR coverage is a system-wide geographic record: acting command must
        # not be able to redraw it (spec §1 "Administrator only").
        CAP_MANAGE_AOR,
    }
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def _now() -> dt.datetime:
    from django.utils import timezone

    return timezone.now()


def base_role(user) -> str:
    authority = getattr(user, "authority", None)
    return BASE_ROLE_MAP.get(authority, ROLE_CREATOR)


def _unit_chain() -> dict[int, int | None]:
    """``{unit_id: parent_id}`` for the whole hierarchy (small table)."""
    from apps.legacy.models import Unit

    return dict(Unit.objects.values_list("id", "parent_id"))


def _covers(scope_unit_id, target_unit_id, chain) -> bool:
    """True when an appointment at ``scope`` reaches ``target``.

    An appointment covers its own unit and every subordinate unit, matching the
    ``accessible_unit_ids`` scoping rule used by the querysets.
    """
    if scope_unit_id is None:
        return False
    if target_unit_id is None:
        return False
    current = int(target_unit_id)
    seen = set()
    while current is not None and current not in seen:
        if current == int(scope_unit_id):
            return True
        seen.add(current)
        current = chain.get(current)
    return False


def active_appointments(user) -> list[dict]:
    """Active appointment rows for ``user``.

    Deliberately *not* cached on the user object: an appointment can be revoked
    or created while a session is live, and a stale cached set would keep
    granting authority the administrator has already taken away.
    """
    if user is None or not getattr(user, "id", None):
        return []

    from apps.command.models import UnitAppointment

    return list(
        UnitAppointment.objects.filter(user_id=user.id, is_active=True).values(
            "id", "unit_id", "role"
        )
    )


def active_acting_appointments(user, now=None) -> list:
    """Acting appointments in force right now (expired ones are excluded)."""
    if user is None or not getattr(user, "id", None):
        return []
    from apps.command.models import ActingAppointment

    return list(
        ActingAppointment.objects.filter(
            user_id=user.id,
            is_active=True,
            revoked_at__isnull=True,
            starts_at__lte=now or _now(),
            ends_at__gt=now or _now(),
        ).select_related("unit")
    )


def active_office_roles(user) -> list[dict]:
    """Grants conferred by membership of an office.

    A person assigned - primary or secondary - to an active office holds that
    office's role when its ``function`` maps to one (the Operations Center), and
    the code-based capabilities in ``OFFICE_CODE_CAPABILITIES`` (A3 - Operations
    plans missions). Ending the assignment, or deactivating the office, removes
    both on the next request because nothing is cached.
    """
    if user is None or not getattr(user, "id", None):
        return []

    from apps.command.models import SectionAssignment

    rows = SectionAssignment.objects.filter(
        user_id=user.id, is_active=True, section__is_active=True
    ).values_list("section__unit_id", "section__function", "section__code")

    grants: list[dict] = []
    for unit_id, function, code in rows:
        if unit_id is None:
            continue
        grants.append(
            {
                "unit_id": unit_id,
                "role": OFFICE_FUNCTION_ROLE_MAP.get(function),
                "code": (code or "").strip().upper(),
            }
        )
    return grants


def _office_covers(office_unit_id, unit_id) -> bool:
    """Office authority is unit-only; it does not reach subordinates."""
    return unit_id is None or int(office_unit_id) == int(unit_id)


def effective_roles(user, unit_id=None) -> set[str]:
    """Role names in force for ``user``, optionally limited to one unit."""
    roles = {base_role(user)}
    chain = _unit_chain()

    for appointment in active_appointments(user):
        if unit_id is None or _covers(appointment["unit_id"], unit_id, chain):
            roles.add(appointment["role"])

    for acting in active_acting_appointments(user):
        if unit_id is None or _covers(acting.unit_id, unit_id, chain):
            roles.add(acting.acting_role)

    for office in active_office_roles(user):
        if office["role"] and _office_covers(office["unit_id"], unit_id):
            roles.add(office["role"])

    return roles


def capabilities_for(user, unit_id=None) -> set[str]:
    """The capability set for ``user`` (optionally at one unit)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return set()

    capabilities: set[str] = set(ROLE_CAPABILITIES.get(base_role(user), _READ_ONLY))
    chain = _unit_chain()

    for appointment in active_appointments(user):
        if unit_id is None or _covers(appointment["unit_id"], unit_id, chain):
            capabilities |= ROLE_CAPABILITIES.get(appointment["role"], _READ_ONLY)

    for acting in active_acting_appointments(user):
        if unit_id is not None and not _covers(acting.unit_id, unit_id, chain):
            continue
        granted = set(ROLE_CAPABILITIES.get(acting.acting_role, _READ_ONLY))
        delegated = acting.delegated_capabilities or None
        if delegated:
            # Delegation may only narrow the acting role, never widen it.
            granted &= set(delegated) | {CAP_VIEW}
        capabilities |= granted - NON_DELEGABLE

    for office in active_office_roles(user):
        if not _office_covers(office["unit_id"], unit_id):
            continue
        if office["role"]:
            capabilities |= ROLE_CAPABILITIES.get(office["role"], _READ_ONLY)
        capabilities |= OFFICE_CODE_CAPABILITIES.get(office["code"], frozenset())

    # A plain Evaluator is an MRM role and does not see the resource modules.
    # Only the listed command/staff designations reopen them; in particular a
    # Logistics NCOIC post must not leak them through its own capability set.
    if base_role(user) == ROLE_EVALUATOR:
        designated = effective_roles(user, unit_id)
        if not designated & EVALUATOR_RESOURCE_ROLES:
            capabilities.discard(CAP_VIEW_RESOURCES)

    return capabilities


def has_capability(user, capability: str, unit_id=None) -> bool:
    return capability in capabilities_for(user, unit_id)


# ---------------------------------------------------------------------------
# Role catalogue (authoritative list for the admin interface)
# ---------------------------------------------------------------------------

SCOPE_LABELS = {
    "organization": "System-wide (all active units)",
    "unit_and_subordinates": "Assigned unit and authorised subordinate units",
    "unit": "Assigned unit only",
}


def role_scope(role: str) -> str:
    """Authority scope of a role, derived from its capabilities.

    This is what the role is allowed to *act* on. It is narrower than the record
    visibility every account has within its own unit tree (see
    :func:`role_read_scope`), which is a queryset rule and not a role grant.
    """
    if role == ROLE_ADMINISTRATOR:
        return "organization"
    caps = ROLE_CAPABILITIES.get(role, _READ_ONLY)
    return "unit_and_subordinates" if CAP_VIEW_SUBORDINATES in caps else "unit"


def role_read_scope(role: str) -> str:
    """Records a holder can see, per ``apps.core.scoping.accessible_unit_ids``."""
    if role == ROLE_ADMINISTRATOR:
        return "organization"
    return "unit_and_subordinates"


def role_catalogue() -> list[dict]:
    """Every assignable role in one payload.

    The React admin interface builds its pickers from this list, so a role can
    never exist in the backend while being absent from the interface (or
    linger in the interface after being withdrawn).
    """
    appointment_values = tuple(value for value, _ in APPOINTMENT_ROLE_CHOICES)
    #: Safety Officer is deliberately both: an account role and a unit post.
    #: The catalogue lists it once so the interface cannot show two competing
    #: "Safety Officer" entries.
    authority_by_role = {role: authority for authority, role in BASE_ROLE_MAP.items()}
    ordered = list(ACCOUNT_ROLE_VALUES) + [
        value for value in appointment_values + OFFICE_ROLE_VALUES
        if value not in ACCOUNT_ROLE_VALUES
    ]

    entries: list[dict] = []
    for role in ordered:
        capabilities = ROLE_CAPABILITIES.get(role, _READ_ONLY)
        scope = role_scope(role)
        is_account = role in ACCOUNT_ROLE_VALUES
        is_appointment = role in appointment_values
        if role in OFFICE_ROLE_VALUES:
            # Conferred by office membership, never by appointment.
            kind = "office"
        elif is_account and is_appointment:
            kind = "both"
        else:
            kind = "account" if is_account else "appointment"
        entries.append(
            {
                "value": role,
                "label": ROLE_LABELS.get(role, role),
                "kind": kind,
                # The legacy enum value an account role must be saved as; blank
                # for posts that exist only as unit appointments.
                "account_value": authority_by_role.get(role, "") if is_account else "",
                "appointment": is_appointment,
                "scope": scope,
                "scope_label": SCOPE_LABELS[scope],
                "read_scope": role_read_scope(role),
                "read_scope_label": SCOPE_LABELS[role_read_scope(role)],
                "exclusive": role in EXCLUSIVE_APPOINTMENT_ROLES,
                "description": ROLE_DESCRIPTIONS.get(role, ""),
                "capabilities": sorted(capabilities),
                "capability_labels": [
                    CAPABILITY_LABELS.get(name, name) for name in sorted(capabilities)
                ],
            }
        )
    return entries


def related_unit_ids(unit_id) -> set[int]:
    """The unit, its ancestors and all subordinate units.

    Used to scope MRM team candidates: personnel from a sister branch or an
    unrelated unit cannot be assigned to another unit's mission risk team.
    """
    if unit_id is None:
        return set()
    from apps.legacy.models import Unit

    rows = dict(Unit.objects.values_list("id", "parent_id"))
    if int(unit_id) not in rows:
        return set()

    related = {int(unit_id)}
    current = rows.get(int(unit_id))
    seen = set()
    while current is not None and current not in seen:
        related.add(current)
        seen.add(current)
        current = rows.get(current)

    frontier = [int(unit_id)]
    while frontier:
        parent = frontier.pop()
        children = [child for child, up in rows.items() if up == parent and child not in related]
        related.update(children)
        frontier.extend(children)
    return related


def can_prepare_mrm(user, unit_id) -> bool:
    """True when ``user`` may author or evaluate an MRM for ``unit_id``.

    Mission MRM team assignment uses this check so an inactive account, a user
    from an unrelated unit, or someone with no MRM capability cannot be put on
    a mission's risk team.
    """
    if user is None or not getattr(user, "is_active", False):
        return False
    if unit_id is None or getattr(user, "unit_id", None) is None:
        return False
    if user.unit_id not in related_unit_ids(unit_id):
        return False
    caps = capabilities_for(user, unit_id)
    return bool(caps & {CAP_CREATE, CAP_EVALUATE})


def permissions_payload(user) -> dict:
    """Boolean flags consumed by the React app (mirrors capabilities)."""
    caps = capabilities_for(user)
    is_admin = getattr(user, "authority", None) == BASE_ADMIN
    roles = sorted(effective_roles(user))
    role_set = set(roles)

    # The single Super Administrator seat. The React app uses this to show the
    # permanent-delete control only to the one account allowed to use it.
    from apps.accounts.ownership import is_system_owner

    is_owner = is_system_owner(user)

    return {
        # -- legacy flag names kept so existing components keep working --
        "is_admin": is_admin,
        "is_system_owner": is_owner,
        "can_manage_personnel": CAP_MANAGE_PERSONNEL in caps,
        "can_manage_inventory": CAP_MANAGE_INVENTORY in caps,
        "can_record_air_fuel": CAP_RECORD_AIR_FUEL in caps,
        "can_manage_vehicles": CAP_MANAGE_VEHICLES in caps,
        "can_view_sensitive_inventory": CAP_VIEW_SENSITIVE_INVENTORY in caps,
        "can_approve_operations": CAP_APPROVE_MISSIONS in caps,
        "can_review_risk": CAP_REVIEW in caps,
        "can_plan_operations": CAP_CREATE in caps,
        "can_plan_missions": CAP_MANAGE_MISSIONS in caps,
        "can_view_missions": CAP_VIEW_MISSIONS in caps,
        "can_monitor_vehicle_tracking": CAP_MONITOR_VEHICLE_TRACKING in caps,
        "view_audit_log": CAP_VIEW_AUDIT in caps,
        # -- TOWEMS additions --------------------------------------------
        "capabilities": {name: name in caps for name in sorted(ALL_CAPABILITIES)},
        "roles": roles,
        "role_labels": [ROLE_LABELS.get(role, role) for role in roles],
        "can_review_missions": CAP_REVIEW_MISSIONS in caps,
        "can_manage_sections": CAP_MANAGE_SECTIONS in caps,
        "can_manage_appointments": CAP_MANAGE_APPOINTMENTS in caps,
        "can_manage_acting_authority": CAP_MANAGE_ACTING in caps,
        "can_approve_inventory": CAP_APPROVE_INVENTORY in caps,
        "can_request_inventory": CAP_REQUEST_INVENTORY in caps,
        "can_comment": CAP_COMMENT in caps,
        # The "All visible" instruction tab is command/Operations Center work;
        # everyone else keeps their own addressed and issued views.
        "can_view_all_instructions": CAP_VIEW_ALL_INSTRUCTIONS in caps,
        # Resources (Personnel, Offices & Sections, Inventory, Vehicle Trips)
        # stay off the Creator account and off a plain Evaluator; command/staff
        # roles and Evaluators holding a listed designation keep them.
        "can_view_resources": CAP_VIEW_RESOURCES in caps,
        # A Creator receives instructions but cannot issue them.
        "can_issue_instructions": CAP_ISSUE_INSTRUCTIONS in caps,
        "can_route": CAP_ROUTE in caps,
        "manage_users": CAP_MANAGE_USERS in caps,
        "manage_aor": CAP_MANAGE_AOR in caps,
        "maintain_aor": CAP_MAINTAIN_AOR in caps,
        "can_prepare_mrm": CAP_CREATE in caps or CAP_EVALUATE in caps,
        # Hazard library: maintained by the Safety Officer, read by command and
        # operations posts, Creators and Evaluators; hidden from everyone else.
        "can_view_hazards": CAP_VIEW_HAZARDS in caps,
        "can_manage_hazards": CAP_MANAGE_HAZARDS in caps,
        # Post-mission feedback audit queue (Unit Safety Officer).
        "can_audit_feedback": CAP_AUDIT_FEEDBACK in caps,
        # The MRM audit is a Safety Officer tool; the server enforces the same
        # role on its endpoints.
        "can_view_safety_audit": ROLE_SAFETY_OFFICER in roles,
        # Read-only AOR/GIS map: command, operations and safety posts.
        "can_view_aor": bool(role_set & AOR_VIEWER_ROLES),
    }


def is_decision_authority(user, unit_id=None) -> bool:
    return bool(
        effective_roles(user, unit_id)
        & {ROLE_COMMANDER, ROLE_DEPUTY_COMMANDER, ROLE_ADMINISTRATOR}
    )


def approver_roles_for(entity: str, unit_id=None, category: str = "") -> list[str]:
    """Configurable Decision Authority lookup (spec §12C).

    Falls back to the defaults above when no ``ApprovalRule`` matches, so a
    fresh deployment behaves predictably without any configuration rows.
    """
    from apps.command.models import ApprovalRule

    rule = (
        ApprovalRule.objects.filter(is_active=True, entity=entity)
        .filter(models_q_unit_or_global(unit_id))
        .filter(models_q_category(category))
        .order_by("-unit_id", "id")
        .first()
    )
    if rule and rule.approver_roles:
        return list(rule.approver_roles)
    if entity == "mrm":
        return list(DEFAULT_MRM_APPROVER_ROLES)
    return list(DEFAULT_MISSION_APPROVER_ROLES)


# Small helpers kept separate so the query above stays readable.
def models_q_unit_or_global(unit_id):
    from django.db.models import Q

    return Q(unit_id=unit_id) | Q(unit__isnull=True)


def models_q_category(category: str):
    from django.db.models import Q

    return Q(category=category) | Q(category="")
