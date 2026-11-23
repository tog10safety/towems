"""Server-side authorisation.

The legacy application enforced roles with per-route decorators across 115
routes, so a single forgotten decorator meant an unprotected endpoint. TOWEMS
centralises the rules here and applies them declaratively on viewsets.

Role resolution itself lives in :mod:`apps.core.roles`, which merges the legacy
``users.authority`` value with unit appointments and acting delegations. These
classes are the thin enforcement layer; they never inspect ``user.authority``
for anything the capability matrix can answer.

Frontend menu visibility is cosmetic - every rule below is enforced on the
server regardless of what the React app renders.
"""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core import roles

ADMIN = roles.BASE_ADMIN
EVALUATOR = roles.BASE_EVALUATOR
CREATOR = roles.BASE_CREATOR
UNIT_SAFETY = roles.BASE_SAFETY

PLANNING_ROLES = (ADMIN, UNIT_SAFETY, CREATOR, EVALUATOR)
SENSITIVE_ROLES = (ADMIN, UNIT_SAFETY)


def authority(user) -> str | None:
    return getattr(user, "authority", None) if user and user.is_authenticated else None


def can(user, capability: str, unit_id=None) -> bool:
    """Capability check used by views and serializers."""
    return roles.has_capability(user, capability, unit_id)


def is_authenticated(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_active)


class IsAuthenticated65(BasePermission):
    """Explicit authenticated check that tolerates a legacy user object."""

    def has_permission(self, request, view):
        return is_authenticated(request)


class IsAdministrator(BasePermission):
    def has_permission(self, request, view):
        return is_authenticated(request) and can(request.user, roles.CAP_SYSTEM_CONFIG)


class IsUserAdministrator(BasePermission):
    """Account administration: deactivate, reactivate, reset, edit accounts.

    Only the Administrator capability set holds ``manage_users``; command and
    administrative (Admin Officer) appointments deliberately do not.
    """

    def has_permission(self, request, view):
        return is_authenticated(request) and can(request.user, roles.CAP_MANAGE_USERS)


class IsEvaluatorOrAdministrator(BasePermission):
    def has_permission(self, request, view):
        return is_authenticated(request) and (
            authority(request.user) in (ADMIN, EVALUATOR)
            or can(request.user, roles.CAP_EVALUATE)
        )


class IsUnitSafetyOrAdministrator(BasePermission):
    def has_permission(self, request, view):
        return is_authenticated(request) and authority(request.user) in (ADMIN, UNIT_SAFETY)


class CanViewSafetyAudit(BasePermission):
    """The Unit Safety Officer's MRM audit.

    The audit is a safety-office function, not a command report: only an
    account holding the Safety Officer role (as the legacy account role or as
    a current unit post) may open it. The queryset still restricts every row
    to the officer's own unit and its subordinates.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        return authority(request.user) == UNIT_SAFETY or (
            roles.ROLE_SAFETY_OFFICER in roles.effective_roles(request.user)
        )


class CanPlanOperations(BasePermission):
    """Mission planning belongs to the Operations Center office alone.

    Creating or changing a mission needs ``manage_missions``, which the
    Operations Center office and the Administrator hold - command posts
    deliberately do not, so their oversight stays read-only.

    Reads are allowed through to the view, whose queryset is
    ``services.visible_missions``: a user with ``view_missions`` sees their unit
    scope, a user attached to a mission sees that mission, and everybody else
    gets an empty list and a 404 on retrieve - so an unauthorised user cannot
    even confirm that a mission exists.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can(request.user, roles.CAP_MANAGE_MISSIONS)


class CanPlanMovements(BasePermission):
    """Vehicle trips keep their earlier rule: any operational role may plan.

    Mission planning is restricted to the Operations Center, but a trip is a
    logistics movement record, not a mission, and restricting it here would
    remove a capability nobody asked to change.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return authority(request.user) in PLANNING_ROLES or can(
            request.user, roles.CAP_MANAGE_MISSIONS
        )


class CanRouteMissions(BasePermission):
    """Recording a routing decision (Operations Center, Operations Officer).

    The *stage* rules are enforced inside the action; this class only decides
    whether the caller holds routing authority at all, so an officer without
    planning authority can still act at their own stage.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can(request.user, roles.CAP_ROUTE) or can(
            request.user, roles.CAP_REVIEW_MISSIONS
        )


class CanApproveOperations(BasePermission):
    """Approval is reserved for command/safety roles."""

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return authority(request.user) in (ADMIN, UNIT_SAFETY) or can(
            request.user, roles.CAP_APPROVE_MISSIONS
        )


class InventoryPermission(BasePermission):
    """Read for all operational roles; write for the Logistics Officer alone.

    Adding, editing, deleting an item, changing its condition and recording a
    stock movement all need ``manage_inventory``, which only the Logistics
    Officer post (and the Administrator) holds - the Safety Officer keeps read
    access, controlled items included, but no longer changes the register.

    Sensitive categories (firearms, ammunition) are additionally filtered in
    the queryset so read access does not imply access to everything.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can(request.user, roles.CAP_MANAGE_INVENTORY)


class CanRecordAirFuel(BasePermission):
    """The POL-01 an airframe burned, recorded by Logistics.

    The Logistics Officer and the Logistic NCOIC record the figure; the
    Operations Center plans the sortie but only reads the consumption. The
    caller still reaches only the missions their queryset allows.
    """

    def has_permission(self, request, view):
        return is_authenticated(request) and can(
            request.user, roles.CAP_RECORD_AIR_FUEL
        )


class CanManageVehicles(BasePermission):
    """The vehicle register belongs to the Logistics Officer.

    Reads stay open to every authenticated account because mission planning,
    dispatch and the tracking board all need the vehicle list; creating,
    editing or retiring a vehicle needs ``manage_vehicles``.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can(request.user, roles.CAP_MANAGE_VEHICLES)


class CanRequestInventory(BasePermission):
    """Submitting an inventory request (Logistic NCOIC and above)."""

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can(request.user, roles.CAP_REQUEST_INVENTORY) or can(
            request.user, roles.CAP_MANAGE_INVENTORY
        )


class CanApproveInventory(BasePermission):
    """Deciding an inventory request - Logistics Officer or administrator."""

    def has_permission(self, request, view):
        return is_authenticated(request) and can(
            request.user, roles.CAP_APPROVE_INVENTORY
        )


class CanManageSections(BasePermission):
    """Office/Section management (Admin Officer, within their unit)."""

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can(request.user, roles.CAP_MANAGE_SECTIONS)


class CanManageUnits(BasePermission):
    """Unit management.

    Reading the unit register stays open to every authenticated account: the
    mission, office and AOR selectors need it, and it is reference data rather
    than operational records. Creating, editing, deactivating and reactivating a
    unit require ``system_config``, which no delegation can grant - only the
    Administrator holds it.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can(request.user, roles.CAP_SYSTEM_CONFIG)


class CanManageAppointments(BasePermission):
    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return can(request.user, roles.CAP_VIEW)
        return can(request.user, roles.CAP_MANAGE_APPOINTMENTS)


class CanManageActingAuthority(BasePermission):
    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return can(request.user, roles.CAP_VIEW)
        return can(request.user, roles.CAP_MANAGE_ACTING)


class CanIssueInstructions(BasePermission):
    """Any signed-in account may read its own instructions; issuing is a command act.

    Creating an instruction needs ``issue_instructions``, which the Creator
    account does not hold: a Creator receives and actions instructions but never
    issues one. Acknowledging and completing - the recipient's own actions on a
    record addressed to them - keep the narrower ``comment`` requirement.

    The narrower question - who may open the *"All visible"* view rather than the
    items addressed to or issued by them - is decided in the viewset, because it
    depends on the requested scope rather than on the HTTP method.
    """

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        if getattr(view, "action", None) == "create":
            return can(request.user, roles.CAP_ISSUE_INSTRUCTIONS)
        return can(request.user, roles.CAP_COMMENT)


class CanViewResources(BasePermission):
    """The unit's resource registers: personnel, offices, inventory and trips.

    Withheld from the Creator account, which raises assessments and receives
    instructions rather than administering the unit. The dedicated
    ``personnel/assignable`` endpoints that a Creator still needs for MRM team
    selection are actions on another route and are not gated by this class.
    """

    def has_permission(self, request, view):
        return is_authenticated(request) and can(
            request.user, roles.CAP_VIEW_RESOURCES
        )


class CanViewAuditLog(BasePermission):
    def has_permission(self, request, view):
        return is_authenticated(request) and can(request.user, roles.CAP_VIEW_AUDIT)


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class IsOwnerOrCommand(BasePermission):
    """Object-level: the creator, or a command/safety role, may modify."""

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if authority(request.user) in (ADMIN, UNIT_SAFETY):
            return True
        owner_id = getattr(obj, "created_by_id", None)
        return owner_id is not None and owner_id == request.user.id
