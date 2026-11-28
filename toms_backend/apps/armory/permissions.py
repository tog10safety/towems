"""Server-side armory authorisation.

Frontend visibility is cosmetic. These classes are the enforcement point for
every armory route, including the custom issuance/return actions.
"""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core import roles
from apps.core.permissions import is_authenticated


class CanViewArmory(BasePermission):
    """Read the unit armory register (Logistics, command, safety)."""

    def has_permission(self, request, view):
        return is_authenticated(request) and roles.has_capability(
            request.user, roles.CAP_VIEW_ARMORY
        )


class CanManageArmory(BasePermission):
    """Encode firearms/ammunition and maintain personnel designations."""

    def has_permission(self, request, view):
        return is_authenticated(request) and roles.has_capability(
            request.user, roles.CAP_MANAGE_ARMORY
        )


class CanProcessArmory(BasePermission):
    """Issue and receive firearms, with an optional linked Mission Order."""

    def has_permission(self, request, view):
        return is_authenticated(request) and roles.has_capability(
            request.user, roles.CAP_PROCESS_ARMORY
        )


class ArmoryInventoryPermission(BasePermission):
    """Read with view_armory; every mutation needs manage_armory."""

    def has_permission(self, request, view):
        if not is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return roles.has_capability(request.user, roles.CAP_VIEW_ARMORY)
        return roles.has_capability(request.user, roles.CAP_MANAGE_ARMORY)


class ArmoryReadOnlyPermission(BasePermission):
    """Transactions and assignment history are read-only for the register."""

    def has_permission(self, request, view):
        return (
            is_authenticated(request)
            and request.method in SAFE_METHODS
            and roles.has_capability(request.user, roles.CAP_VIEW_ARMORY)
        )
