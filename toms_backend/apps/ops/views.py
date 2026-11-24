"""TOWEMS API views.

Authorisation is enforced here and in ``apps.core.permissions`` - never by
whether the React app renders a menu item.
"""

from __future__ import annotations

import csv
import datetime as dt

from django.db import transaction
from django.db.models import Avg, Count, F, Prefetch, Q, Sum
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core import geo, roles
from apps.core.audit import AuditedModelViewSetMixin, log_action, snapshot
from apps.core.legacy_audit import record_activity
from apps.core.mgrs import MGRSError, from_mgrs, to_mgrs
from apps.core.permissions import (
    ADMIN,
    EVALUATOR,
    UNIT_SAFETY,
    CanApproveInventory,
    CanApproveOperations,
    CanPlanMovements,
    CanPlanOperations,
    CanManageUnits,
    CanManageVehicles,
    CanRecordAirFuel,
    CanRequestInventory,
    CanRouteMissions,
    CanViewResources,
    CanViewSafetyAudit,
    InventoryPermission,
    IsAdministrator,
    IsUnitSafetyOrAdministrator,
    authority,
    can,
)
from apps.core.scoping import accessible_unit_ids, unit_ids_for_filter
from apps.legacy.models import MRMForm, MissionHazards, Unit
from apps.ops import aor as aor_service
from apps.ops import aor_handoff
from apps.ops import approvals
from apps.ops import formation_rules
from apps.ops import map_config
from apps.ops import pdf as pdf_service
from apps.ops import places
from apps.ops import risk as risk_engine
from apps.ops import routing
from apps.ops import safety_audit
from apps.ops import serializers, services
from apps.command import unit_map
from apps.ops.notifications import complete_action, notify, notify_many
from apps.ops.models import (
    AdministrativeArea,
    AirAsset,
    Airfield,
    AirMissionAircraft,
    AirMissionAsset,
    AirMissionDetail,
    AirMissionItinerary,
    AreaOfResponsibility,
    AuditLog,
    GroundMissionDetail,
    InventoryCategory,
    InventoryItem,
    InventoryRequest,
    InventoryTransaction,
    MRMAttentionDismissal,
    Mission,
    MissionPersonnel,
    Notification,
    OperationalMapFeature,
    RiskAssessment,
    TripItinerary,
    Vehicle,
    VehicleTrip,
    VehicleTripVehicle,
)


def _now() -> dt.datetime:
    # One clock convention: naive Philippine time (apps.core.timeutils).
    from apps.core import timeutils

    return timeutils.now()


def _relocate_air_assets(detail):
    """Move every aircraft on a detail to its destination and report the moves."""
    from apps.ops import formation_rules

    relocated = []
    assembly = (detail.destination_location or "").strip()
    if not assembly:
        return relocated
    for asset in formation_rules.assets_from_detail(detail):
        if asset.current_location == assembly:
            continue
        asset.current_location = assembly[:255]
        asset.save(update_fields=["current_location", "updated_at"])
        relocated.append(
            {
                "air_asset": asset.pk,
                "tail_number": asset.tail_number,
                "current_location": asset.current_location,
            }
        )
    return relocated


def _air_mission_final_time(detail):
    """The last actual arrival (or overflight departure) for every aircraft."""
    aircraft_rows = AirMissionAircraft.objects.filter(element__air_detail=detail)
    if not aircraft_rows.exists():
        return None
    final_times = []
    for aircraft in aircraft_rows:
        last = aircraft.itineraries.order_by("-sequence").first()
        if last is None:
            return None
        if last.overflight:
            if last.toot is None:
                return None
            final_times.append(last.toot)
        else:
            if last.ata is None:
                return None
            final_times.append(last.ata)
    return max(final_times) if final_times else None


def _can_see_sensitive(user) -> bool:
    """Sensitive inventory visibility, capability-derived (appointments count)."""
    return can(user, roles.CAP_VIEW_SENSITIVE_INVENTORY)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


RETURNED_REVIEW_STATES = ["needs_revision", "rejected"]


def _safety_analytics(user):
    """Safety picture for a Unit Safety Officer's *own* unit.

    Returns ``None`` for every other account, so the block is computed only when
    it will be shown. The scope is deliberately the officer's primary unit, not
    their whole descendant tree: this is the unit's safety file, not command
    oversight of subordinates.
    """
    from apps.legacy.models import (
        RISK_COLOR_RED,
        RISK_COLOR_YELLOW,
        HazardRegistry,
    )

    if roles.ROLE_SAFETY_OFFICER not in roles.effective_roles(user, user.unit_id):
        return None

    unit = user.unit if user.unit_id else None
    empty = {
        "unit": (
            {"id": unit.id, "name": unit.name, "acronym": unit.acronym}
            if unit is not None
            else None
        ),
        "mrm": {
            "total": 0,
            "draft": 0,
            "submitted": 0,
            "reviewed": 0,
            "approved": 0,
            "awaiting": 0,
            "returned": 0,
        },
        "risk": {
            "low": 0,
            "medium": 0,
            "high": 0,
            "unclassified": 0,
            "average_percent": 0.0,
        },
        "hazards": {
            "total": 0,
            "red": 0,
            "yellow": 0,
            "green": 0,
            "unclassified": 0,
            "activity_codes": 0,
        },
        "missions": {"total": 0, "active": 0, "without_approved_mrm": 0},
        "attention": [],
        "attention_hidden": [],
        "attention_hidden_count": 0,
    }
    if unit is None:
        return empty

    mrm = MRMForm.objects.filter(
        Q(original_unit_id=unit.id)
        | Q(original_unit_id__isnull=True, created_by__unit_id=unit.id)
    )
    total = mrm.count()
    draft = mrm.filter(status="draft").count()
    submitted = mrm.filter(status="submitted").count()
    reviewed = mrm.filter(status="reviewed").count()
    returned = mrm.filter(review_status__in=RETURNED_REVIEW_STATES).count()
    approved = (
        mrm.filter(authority_approved=True)
        .exclude(review_status__in=RETURNED_REVIEW_STATES)
        .count()
    )
    awaiting = (
        mrm.filter(authority_approved=False)
        .exclude(review_status__in=RETURNED_REVIEW_STATES)
        .count()
    )

    risk = {"low": 0, "medium": 0, "high": 0, "unclassified": 0}
    for row in mrm.values("risk_category").annotate(n=Count("id")):
        category = (row["risk_category"] or "").strip().lower()
        if category in risk:
            risk[category] += row["n"]
        else:
            risk["unclassified"] += row["n"]
    average = (
        mrm.filter(authority_approved=True)
        .exclude(review_status__in=RETURNED_REVIEW_STATES)
        .aggregate(value=Avg("total_percent"))["value"]
    )

    hazard_scope = HazardRegistry.objects.filter(
        Q(unit_id=unit.id) | Q(unit_id__isnull=True, unit=unit.name)
    )
    hazards = {"total": 0, "red": 0, "yellow": 0, "green": 0, "unclassified": 0}
    for row in hazard_scope.values("before_risk_rating").annotate(n=Count("id")):
        rating = (row["before_risk_rating"] or "").strip().upper()
        hazards["total"] += row["n"]
        if rating in RISK_COLOR_RED:
            hazards["red"] += row["n"]
        elif rating in RISK_COLOR_YELLOW:
            hazards["yellow"] += row["n"]
        elif rating and rating != "N/A":
            hazards["green"] += row["n"]
        else:
            hazards["unclassified"] += row["n"]
    hazards["activity_codes"] = (
        hazard_scope.exclude(activity_code="")
        .values("activity_code")
        .distinct()
        .count()
    )

    mission_scope = Mission.objects.filter(unit_id=unit.id).exclude(status="cancelled")
    # The safety watch follows the operations tile: air missions carry no MRM,
    # and a ground mission that has finished is a record, not live work.
    live_ground_missions = (
        Mission.objects.filter(unit_id=unit.id, category=Mission.CATEGORY_GROUND)
        .exclude(status__in=["cancelled", "completed"])
    )
    without_mrm = sum(
        1
        for mission in live_ground_missions.select_related(
            "risk_assessment__mrm_form"
        )
        if not approvals.mrm_complete(mission)
    )

    from apps.ops import mrm_access

    dismissed_ids = set(
        MRMAttentionDismissal.objects.filter(user=user).values_list(
            "mrm_form_id", flat=True
        )
    )
    # Neither an air mission's form nor a finished mission's form is a queued
    # action, so they leave the attention list. A form with no mission at all
    # is still the unit's live paperwork and stays.
    inactive_form_ids = set(
        Mission.objects.filter(
            Q(category=Mission.CATEGORY_AIR)
            | Q(status__in=["cancelled", "completed"]),
            risk_form_id__isnull=False,
        ).values_list("risk_form_id", flat=True)
    )
    inactive_form_ids.update(
        RiskAssessment.objects.filter(mrm_form_id__isnull=False)
        .filter(
            Q(mission__category=Mission.CATEGORY_AIR)
            | Q(mission__status__in=["cancelled", "completed"])
        )
        .values_list("mrm_form_id", flat=True)
    )
    open_mrms = mrm.filter(authority_approved=False).exclude(id__in=inactive_form_ids)

    def attention_row(row):
        allowed, _ = mrm_access.can_delete(user, row)
        return {
            "id": row.id,
            "mrm_number": row.mrm_number,
            "activity_mission": row.activity_mission,
            "status": row.status,
            "review_status": row.review_status,
            "risk_category": row.risk_category or "",
            "total_percent": row.total_percent,
            "date_created": row.date_created,
            "can_delete": allowed,
        }

    attention = [
        attention_row(row)
        for row in open_mrms.exclude(id__in=dismissed_ids).order_by(
            "date_created", "id"
        )[:5]
    ]
    hidden_attention = [
        attention_row(row)
        for row in open_mrms.filter(id__in=dismissed_ids).order_by(
            "-date_created", "-id"
        )[:50]
    ]
    hidden_count = open_mrms.filter(id__in=dismissed_ids).count()

    empty["mrm"] = {
        "total": total,
        "draft": draft,
        "submitted": submitted,
        "reviewed": reviewed,
        "approved": approved,
        "awaiting": awaiting,
        "returned": returned,
    }
    empty["risk"] = {
        **risk,
        "average_percent": round(float(average or 0), 1),
    }
    empty["hazards"] = hazards
    empty["missions"] = {
        "total": mission_scope.count(),
        "active": mission_scope.filter(status__in=["approved", "active"]).count(),
        "without_approved_mrm": without_mrm,
    }
    empty["attention"] = attention
    empty["attention_hidden"] = hidden_attention
    empty["attention_hidden_count"] = hidden_count
    return empty


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        unit_ids = unit_ids_for_filter(user)
        # Mission counters use the same visibility rule as the mission list, so
        # a user who cannot open missions cannot infer them from the dashboard.
        missions = services.visible_missions(user)

        by_category = {
            row["category"]: row["n"]
            for row in missions.values("category").annotate(n=Count("id"))
        }
        by_status = {
            row["status"]: row["n"]
            for row in missions.values("status").annotate(n=Count("id"))
        }

        active_statuses = ["approved", "active"]
        active_missions = missions.filter(status__in=active_statuses)

        assigned_personnel_ids = set(
            MissionPersonnel.objects.filter(
                is_active=True, mission__in=active_missions
            ).values_list("user_id", flat=True)
        )

        personnel = User.objects.filter(is_active=True)
        if unit_ids is not None:
            personnel = personnel.filter(unit_id__in=unit_ids)
        personnel_total = personnel.count()
        available_personnel = max(0, personnel_total - len(assigned_personnel_ids))

        items = InventoryItem.objects.all()
        if unit_ids is not None:
            items = items.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        if not _can_see_sensitive(user):
            items = items.filter(is_sensitive=False)
        item_stats = items.aggregate(
            total=Count("id"),
            unserviceable=Count("id", filter=Q(serviceability="unserviceable")),
        )
        low_stock = items.filter(quantity_available__lte=5).count()

        # The same fleet the Mobility Assets register lists: a personal vehicle
        # belongs to a person, not the unit's establishment, so counting it here
        # made the tile disagree with the register it summarises.
        vehicles = Vehicle.objects.filter(is_active=True, is_pov=False)
        if unit_ids is not None:
            vehicles = vehicles.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        vehicle_available = vehicles.filter(status="available").count()
        vehicle_on_trip = vehicles.filter(status="on_trip").count()
        # Which vehicles are out, and on what. A POV is a person's car on a
        # mission trip; a PAFMV is a unit vehicle from the register counted
        # above. Naming the difference stops the two being pooled into one
        # figure the register can never match.
        trip_scope = VehicleTrip.objects.filter(status="in_progress")
        if unit_ids is not None:
            trip_scope = trip_scope.filter(
                Q(vehicles__vehicle__unit_id__in=unit_ids)
                | Q(vehicles__vehicle__unit_id__isnull=True)
            ).distinct()
        on_trip_details = sorted(
            [
                {
                    "vehicle_id": assignment.vehicle_id,
                    "registration_number": assignment.vehicle.registration_number,
                    "kind": "POV" if assignment.vehicle.is_pov else "PAFMV",
                    "trip_number": trip.trip_number,
                    "trip_title": trip.title,
                }
                for trip in trip_scope.prefetch_related("vehicles__vehicle")
                for assignment in trip.vehicles.all()
            ],
            key=lambda row: (row["kind"], row["registration_number"]),
        )

        trips = VehicleTrip.objects.all()
        trips_in_progress = trips.filter(status="in_progress").count()
        trips_planned = trips.filter(status="planned").count()

        # Risk counters follow the same unit scope as the risk register itself.
        risk_scope = MRMForm.objects.all()
        if unit_ids is not None:
            risk_scope = risk_scope.filter(
                Q(original_unit_id__in=unit_ids)
                | Q(original_unit_id__isnull=True)
                | Q(created_by_id=user.id)
                | Q(reviewed_by_id=user.id)
            )
        pending_reviews = risk_scope.filter(status="submitted", review_status="pending").count()
        pending_approvals = risk_scope.filter(
            status="reviewed", review_status="approved", authority_approved=False
        ).count()
        approved_risk = risk_scope.filter(authority_approved=True).count()

        aor = AreaOfResponsibility.objects.filter(is_active=True)
        if unit_ids is not None:
            aor = aor.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))

        from django.utils import timezone as _tz

        # The Wing monitors MRMs for ground missions only: air missions are
        # excluded from the MRM requirement, so an air sortie without one is not
        # a gap. Outstanding risk work is also live work - a completed mission
        # has flown already, so its missing MRM belongs to the audit trail, not
        # to the operations watch.
        mrm_scope = missions.exclude(status__in=["cancelled", "completed"]).filter(
            category=Mission.CATEGORY_GROUND
        )
        mrm_missing = mrm_scope.filter(risk_assessment__isnull=True).filter(
            Q(start_at__isnull=False) | Q(status__in=["planned", "approved", "active"])
        ).count()
        mrm_pending = mrm_scope.filter(risk_assessment__isnull=False).exclude(
            risk_assessment__mrm_form__authority_approved=True
        ).count()
        mrm_overdue = mrm_scope.filter(start_at__lt=_tz.now()).exclude(
            risk_assessment__mrm_form__authority_approved=True
        ).count()

        # Operational watch: POL drum batches whose shelf life runs out first,
        # in the same unit and sensitivity scope as the register above. A batch
        # already past its date stays listed - it is the most urgent - and only
        # stock still on hand counts, because an emptied batch is nothing left
        # to draw down.
        today = timezone.now().date()
        watch_horizon = today + dt.timedelta(days=30)
        expiring = items.filter(
            category__kind="fuel",
            fuel_form="drum",
            expiry_date__isnull=False,
            expiry_date__lte=watch_horizon,
            quantity_available__gt=0,
        ).order_by("expiry_date", "id")
        expiring_count = expiring.count()

        # The POL figures the dashboard tiles show. Litres are summed with the
        # model's own conversion, so a drum and a bulk tank are compared in the
        # unit the Wing actually plans with, and the expiring count is the same
        # one the watch list below the tiles reports.
        fuel_items = items.filter(category__kind="fuel")
        fuel_litres = 0
        fuel_drums = 0
        for stock in fuel_items:
            fuel_litres += stock.litres(stock.quantity_available)
            if stock.fuel_form == "drum":
                fuel_drums += int(stock.quantity_available or 0)

        unread = Notification.objects.filter(recipient_id=user.id, is_read=False).count()
        pending_actions = Notification.objects.filter(
            recipient_id=user.id,
            requires_action=True,
            action_status__in=[Notification.ACTION_OPEN, Notification.ACTION_IN_PROGRESS],
        ).count()
        recent = AuditLog.objects.select_related("actor").order_by("-created_at", "-id")[:10]

        return Response(
            {
                "missions": {
                    "total": missions.count(),
                    "by_category": {
                        "air": by_category.get(Mission.CATEGORY_AIR, 0),
                        "ground": by_category.get(Mission.CATEGORY_GROUND, 0),
                    },
                    "by_status": by_status,
                    "active": active_missions.count(),
                },
                "risk": {
                    "pending_reviews": pending_reviews,
                    "pending_approvals": pending_approvals,
                    "approved": approved_risk,
                },
                "mrm": {
                    "missing": mrm_missing,
                    "pending": mrm_pending,
                    "overdue": mrm_overdue,
                },
                "personnel": {
                    "total": personnel_total,
                    "available": available_personnel,
                    "on_mission": len(assigned_personnel_ids),
                },
                "inventory": {
                    "items": item_stats["total"] or 0,
                    "unserviceable": item_stats["unserviceable"] or 0,
                    "low_stock": low_stock,
                    "sensitive_visible": _can_see_sensitive(user),
                },
                "fuel": {
                    "litres_available": fuel_litres,
                    "drums_available": fuel_drums,
                    "expiring": expiring_count,
                },
                "vehicles": {
                    "available": vehicle_available,
                    "on_trip": vehicle_on_trip,
                    "on_trip_details": on_trip_details,
                },
                "trips": {"in_progress": trips_in_progress, "planned": trips_planned},
                "aor": {"count": aor.count()},
                # The shelf-life warning the register alone never gave: the
                # batches an officer must issue, test or extend first.
                "watch": {
                    "horizon_days": 30,
                    "fuel_expiring_count": expiring_count,
                    "fuel_expiring": [
                        {
                            "id": item.id,
                            "name": item.name,
                            "grade": (
                                item.get_fuel_grade_display()
                                if item.fuel_grade
                                else item.name
                            ),
                            "drums": item.quantity_available,
                            "litres": item.litres(item.quantity_available),
                            "location": item.storage_location or "",
                            "unit": item.unit.acronym if item.unit_id else "",
                            "expiry_date": item.expiry_date.isoformat(),
                            "days_left": (item.expiry_date - today).days,
                        }
                        for item in expiring[:5]
                    ],
                },
                # Present only for a Unit Safety Officer; scoped to their own unit.
                "safety": _safety_analytics(user),
                "notifications": {"unread": unread, "pending_actions": pending_actions},
                "scope": {
                    "unit_id": user.unit_id,
                    "unit_name": user.unit.name if user.unit_id else "",
                    "unit_acronym": user.unit.acronym if user.unit_id else "",
                    "unit_ids": sorted(unit_ids) if unit_ids is not None else [],
                    "unit_count": len(unit_ids) if unit_ids is not None else 0,
                    "subordinate_count": max(0, (len(unit_ids) - 1) if unit_ids else 0),
                },
                # The feed shows the *account* that acted, not their full name:
                # usernames are the stable operational identifier (spec §2).
                "recent_activity": [
                    {
                        "id": entry.id,
                        "action": entry.action,
                        "entity_type": entry.entity_type,
                        "entity_id": entry.entity_id,
                        "actor": entry.actor.username if entry.actor_id else "system",
                        "actor_username": entry.actor.username if entry.actor_id else "system",
                        "actor_name": entry.actor.full_name if entry.actor_id else "System",
                        "created_at": entry.created_at,
                    }
                    for entry in recent
                ],
            }
        )


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


class UnitViewSet(viewsets.ModelViewSet):
    """Unit register and hierarchy.

    Reads stay open to every authenticated account (the mission, office and AOR
    selectors need the register). Creating, editing, deactivating and
    reactivating a unit are Administrator-only, checked by ``CanManageUnits``;
    a unit is never deleted, because missions, MRM forms, personnel and audit
    history reference it by id.
    """

    permission_classes = [IsAuthenticated, CanManageUnits]
    serializer_class = serializers.UnitSerializer
    queryset = Unit.objects.all()
    filterset_fields = ["category", "is_active", "parent"]
    search_fields = ["name", "acronym", "location"]

    def get_serializer_class(self):
        if self.request.method in ("POST", "PUT", "PATCH"):
            return serializers.UnitWriteSerializer
        return serializers.UnitSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        # Answer with the register shape, not the input shape, so the caller gets
        # the same row it would read back from /units/.
        return Response(
            serializers.UnitSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
            headers=self.get_success_headers(serializer.data),
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializers.UnitSerializer(serializer.instance).data)

    def perform_create(self, serializer):
        unit = serializer.save(is_active=True, date_created=timezone.now())
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="unit",
            entity_id=unit.pk,
            after=snapshot(unit),
            note=f"Unit {unit.acronym} added",
            request=self.request,
        )

    def perform_update(self, serializer):
        before = snapshot(serializer.instance)
        unit = serializer.save()
        log_action(
            actor=self.request.user,
            action="update",
            entity_type="unit",
            entity_id=unit.pk,
            before=before,
            after=snapshot(unit),
            note=f"Unit {unit.acronym} updated",
            request=self.request,
        )

    def destroy(self, request, *args, **kwargs):
        """Refuse deletion: units are referenced by operational history."""
        return Response(
            {
                "detail": (
                    "Units cannot be deleted. Deactivate the unit instead: its missions, "
                    "MRM forms, personnel and audit history stay intact."
                )
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # -- lifecycle -------------------------------------------------------

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """Take a unit out of use without touching its records."""
        unit = self.get_object()
        if unit.is_active is False:
            return Response(serializers.UnitSerializer(unit).data)

        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "Give the reason for deactivating this unit."})

        active_children = unit.children.filter(is_active=True)
        if active_children.exists():
            names = ", ".join(active_children.values_list("acronym", flat=True)[:5])
            raise ValidationError(
                {
                    "detail": (
                        f"Deactivate its subordinate units first: {names}"
                        f" ({active_children.count()} active)."
                    )
                }
            )

        open_missions = unit.missions.filter(
            status__in=["planned", "approved", "active"]
        )
        if open_missions.exists():
            raise ValidationError(
                {
                    "detail": (
                        "This unit still has "
                        f"{open_missions.count()} planned, approved or active mission(s). "
                        "Complete or cancel them before deactivating."
                    )
                }
            )

        # Personnel keep their unit assignment and their records: deactivating a
        # unit is not deleting its people. The count goes into the audit note so
        # the Administrator can see who is left on an inactive unit.
        assigned = User.objects.filter(unit_id=unit.id, is_active=True).count()

        before = snapshot(unit)
        unit.is_active = False
        unit.save(update_fields=["is_active"])
        log_action(
            actor=request.user,
            action="deactivate",
            entity_type="unit",
            entity_id=unit.pk,
            before=before,
            after=snapshot(unit),
            note=(
                f"Unit {unit.acronym} deactivated: {reason}"
                + (f" ({assigned} active account(s) still assigned)" if assigned else "")
            ),
            request=request,
        )
        return Response(serializers.UnitSerializer(unit).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Return a deactivated unit to use."""
        unit = self.get_object()
        if unit.is_active is not False:
            return Response(serializers.UnitSerializer(unit).data)
        before = snapshot(unit)
        unit.is_active = True
        unit.save(update_fields=["is_active"])
        log_action(
            actor=request.user,
            action="activate",
            entity_type="unit",
            entity_id=unit.pk,
            before=before,
            after=snapshot(unit),
            note=f"Unit {unit.acronym} reactivated",
            request=request,
        )
        return Response(serializers.UnitSerializer(unit).data)

    def get_queryset(self):
        # ``select_related`` keeps the map-marker fields on the unit list to one
        # query instead of one per unit.
        queryset = super().get_queryset().select_related("map_marker")
        # The system-administration unit is not offered to anyone but the Super
        # Administrator, so it is absent from every unit selector built on this
        # register - whatever the caller's role.
        from apps.core.scoping import hidden_unit_ids

        hidden = hidden_unit_ids(self.request.user)
        if hidden:
            queryset = queryset.exclude(id__in=hidden)
        # ``?scope=mine`` is the selector contract: the caller's own unit and
        # its authorised subordinates only. It never widens, and the default
        # (no parameter) keeps the reference list unchanged.
        if (self.request.query_params.get("scope") or "").lower() == "mine":
            ids = accessible_unit_ids(self.request.user)
            queryset = queryset.filter(id__in=ids) if ids else queryset.none()
        return queryset

    # -- map marker ------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="markers")
    def markers(self, request):
        """Units that have a pin, so any operational map can draw them.

        Unit locations are reference data, exactly like the unit register
        itself: every signed-in account may see the plotted picture, including
        neighbouring units outside its own chain. Restricting this to the
        caller's unit tree hid TOG 11 and TOWEASTMIN from a TOG 10 operator
        even though an Administrator had plotted them. Changing a pin or seal
        remains Administrator-only.
        """
        rows = unit_map.markers_with_coordinates(Unit.objects.filter(is_active=True))
        return Response({"count": len(rows), "results": rows})

    @action(detail=True, methods=["get", "patch"], url_path="marker")
    def marker(self, request, pk=None):
        """Read a unit's pin, or set its coordinates (Administrator only)."""
        unit = self.get_object()
        if request.method == "GET":
            return Response(unit_map.payload(unit))
        if not unit_map.can_maintain(request.user):
            raise PermissionDenied(
                "Only an Administrator may set a unit's map location."
            )
        try:
            body = unit_map.set_marker(
                actor=request.user, unit=unit, data=request.data, request=request
            )
        except unit_map.MarkerError as exc:
            return Response(
                {"detail": exc.message, "errors": {exc.field: [exc.message]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(body)

    @action(detail=True, methods=["get", "post", "delete"], url_path="marker-logo")
    def marker_logo(self, request, pk=None):
        """Serve, upload or remove the unit seal used as its map pin."""
        unit = self.get_object()
        if request.method == "GET":
            return unit_map.logo_response(unit)
        if not unit_map.can_maintain(request.user):
            raise PermissionDenied("Only an Administrator may change a unit's seal.")
        try:
            if request.method == "POST":
                body = unit_map.upload_logo(actor=request.user, unit=unit, request=request)
            else:
                body = unit_map.remove_logo(actor=request.user, unit=unit, request=request)
        except unit_map.MarkerError as exc:
            return Response(
                {"detail": exc.message, "errors": {exc.field: [exc.message]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(body)

    @action(detail=False, methods=["get"])
    def tree(self, request):
        """Nested unit hierarchy for selectors and org charts."""
        from apps.core.scoping import hidden_unit_ids

        units = list(
            Unit.objects.filter(is_active=True).exclude(
                id__in=hidden_unit_ids(request.user)
            )
        )
        by_parent: dict[int | None, list] = {}
        for unit in units:
            by_parent.setdefault(unit.parent_id, []).append(unit)

        def build(parent_id):
            return [
                {
                    "id": unit.id,
                    "name": unit.name,
                    "acronym": unit.acronym,
                    "category": unit.category,
                    "children": build(unit.id),
                }
                for unit in sorted(by_parent.get(parent_id, []), key=lambda u: u.name)
            ]

        roots = [u for u in units if u.parent_id is None]
        return Response({"count": len(units), "tree": build(None), "roots": len(roots)})


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------


def _vehicle_conflict(vehicle, mission):
    """An active trip already holding this vehicle over the same window.

    Only windows that can actually be compared are treated as conflicts: a trip
    with no dates cannot be shown to overlap, and inventing one would block
    planning for no reason.
    """
    if mission.start_at is None or mission.end_at is None:
        return None
    trips = (
        VehicleTrip.objects.select_related("mission")
        .filter(
            vehicles__vehicle=vehicle,
            status__in=["draft", "planned", "approved", "in_progress"],
        )
        .exclude(mission=mission)
    )
    for trip in trips:
        if trip.departure_at is None or trip.return_at is None:
            continue
        if trip.departure_at < mission.end_at and mission.start_at < trip.return_at:
            return trip
    return None


def _clear_route(trip):
    """Forget a cached road route that no longer describes the itinerary."""
    trip.route_status = VehicleTrip.ROUTE_NOT_CALCULATED
    trip.route_geometry = None
    trip.route_legs = None
    trip.route_distance_m = None
    trip.route_duration_s = None
    trip.route_provider = ""
    trip.route_calculated_at = None
    trip.route_error = ""
    trip.save(
        update_fields=[
            "route_status",
            "route_geometry",
            "route_legs",
            "route_distance_m",
            "route_duration_s",
            "route_provider",
            "route_calculated_at",
            "route_error",
            "updated_at",
        ]
    )


def _mark_plan_changed(trip):
    """A changed route plan no longer matches an approval that covered it."""
    if trip.mission_id is None:
        return
    mission = Mission.objects.filter(pk=trip.mission_id).first()
    if mission is None or mission.approval_status != Mission.APPROVAL_APPROVED:
        return
    mission.revision = (mission.revision or 0) + 1
    mission.save(update_fields=["revision", "updated_at"])


def _next_pov_number() -> str:
    """A POV has no unit plate, so it gets a stable internal reference."""
    index = Vehicle.objects.filter(is_pov=True).count() + 1
    while Vehicle.objects.filter(registration_number=f"POV-{index:04d}").exists():
        index += 1
    return f"POV-{index:04d}"


def _pov_registration_number(plate_number: str, current: str = "") -> str:
    """The identifier a POV is stored under: the plate the planner typed.

    ``registration_number`` is the plate painted on a vehicle, and a POV is
    identified the same way a unit vehicle is - an internal ``POV-0003`` is not
    what the board, the driver's screen or the order should read.

    The internal reference stays as the fallback, and only as the fallback: a
    POV whose plate was left blank, or whose plate is already registered to
    another vehicle, keeps one so that no car can take another vehicle's
    identifier.
    """
    plate = (plate_number or "").strip()[:40]
    if plate and not (
        Vehicle.objects.filter(registration_number__iexact=plate)
        .exclude(registration_number=current)
        .exists()
    ):
        return plate
    return current or _next_pov_number()


def _apply_mission_vehicles(mission, vehicles, actor, drivers=None):
    """Give each selected vehicle its own trip, keeping existing itineraries.

    ``drivers`` maps a vehicle id to the user designated to drive it. Every
    vehicle in one assignment keeps its own trip, so its driver, itinerary and
    road route stay its own.
    """
    drivers = drivers or {}
    existing = list(
        VehicleTrip.objects.filter(mission=mission)
        .exclude(status="cancelled")
        .prefetch_related("vehicles")
    )
    by_vehicle = {
        link.vehicle_id: trip for trip in existing for link in trip.vehicles.all()
    }

    keep: set[int] = set()
    for vehicle in vehicles:
        trip = by_vehicle.get(vehicle.id)
        if trip is None:
            trip = services.create_trip_with_number(
                title=f"{mission.title} — {vehicle.registration_number}"[:200],
                purpose=mission.purpose or "",
                mission=mission,
                status="planned",
                departure_at=mission.start_at,
                return_at=mission.end_at,
                created_by=actor,
            )
            VehicleTripVehicle.objects.create(
                trip=trip, vehicle=vehicle, driver_id=drivers.get(vehicle.id)
            )
        elif drivers:
            # Re-saving the plan may change who drives an already-planned
            # vehicle, without touching its itinerary or calculated route.
            VehicleTripVehicle.objects.filter(trip=trip, vehicle=vehicle).update(
                driver_id=drivers.get(vehicle.id)
            )
        keep.add(trip.id)

    # Removing a vehicle ends its planned route but keeps the record as history.
    for trip in existing:
        if trip.id in keep:
            continue
        trip.status = "cancelled"
        trip.save(update_fields=["status", "updated_at"])
        trip.vehicles.all().delete()


class MissionViewSet(AuditedModelViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanPlanOperations]
    filterset_fields = ["category", "status", "unit", "risk_form"]
    search_fields = ["mission_number", "title", "location_name", "purpose"]
    ordering_fields = ["start_at", "created_at", "mission_number"]
    audit_entity = "mission"
    audit_fields = [
        "mission_number",
        "category",
        "title",
        "status",
        "is_held",
        "held_at",
        "unit_id",
        "risk_form_id",
    ]

    def get_queryset(self):
        queryset = (
            services.visible_missions(self.request.user)
            .select_related(
                "unit",
                "created_by",
                "approved_by",
                "mission_order_signatory",
                "mission_order_signed_by",
                "risk_form",
                "risk_assessment__mrm_form",
                "air_detail__air_asset",
            )
            .prefetch_related(
                "personnel__user",
                "trips",
                "trips__vehicles__vehicle",
                "trips__vehicles__driver",
                "mrm_team__user",
                "air_detail__assigned_aircraft",
                "air_detail__elements__aircraft__crew",
                "air_detail__elements__aircraft__itineraries",
                # The Fuel card reads these on every mission in the list, so
                # they load with it instead of once per row.
                "air_fuel_draws__aircraft",
                "air_fuel_draws__leg",
                "air_fuel_draws__item",
                "air_fuel_draws__recorded_by",
            )
        )
        category = self.request.query_params.get("category")
        if category in {Mission.CATEGORY_AIR, Mission.CATEGORY_GROUND}:
            queryset = queryset.filter(category=category)
        return self._apply_monitoring_filters(queryset)

    # -- monitoring filters (spec §4.3) -----------------------------------

    def _apply_monitoring_filters(self, queryset):
        """Filters the monitoring table needs, applied in the database.

        Readiness is derived from the same two facts the execution gate uses
        (an approved MRM and an approval covering the current revision), so the
        filter can never disagree with whether dispatch would actually work.
        """
        from django.utils.dateparse import parse_date

        params = self.request.query_params

        approval_status = params.get("approval_status")
        if approval_status in {choice[0] for choice in Mission.APPROVAL_STATUS_CHOICES}:
            queryset = queryset.filter(approval_status=approval_status)

        if params.get("approving_authority") == "me":
            queryset = queryset.filter(approving_authority_id=self.request.user.id)

        mrm_status = params.get("mrm_status")
        if mrm_status == "approved":
            queryset = queryset.filter(risk_form__authority_approved=True)
        elif mrm_status == "returned":
            queryset = queryset.filter(
                risk_form__review_status__in=["needs_revision", "rejected"]
            )
        elif mrm_status == "submitted":
            queryset = queryset.filter(
                risk_form__status__in=["submitted", "reviewed"],
                risk_form__authority_approved=False,
            )
        elif mrm_status == "in_progress":
            queryset = queryset.filter(
                risk_form__status="draft", risk_form__authority_approved=False
            )
        elif mrm_status == "not_created":
            queryset = queryset.filter(risk_assessment__isnull=True)
        elif mrm_status == "pending":
            queryset = queryset.filter(
                risk_assessment__isnull=True, mrm_team__is_active=True
            ).distinct()

        ready = Q(
            risk_form__authority_approved=True,
            approval_status=Mission.APPROVAL_APPROVED,
            approved_revision=F("revision"),
            mission_order_status=Mission.MISSION_ORDER_APPROVED,
            mission_order_signed_revision=F("revision"),
        ) & ~Q(status__in=["active", "completed", "cancelled"])
        readiness = params.get("readiness")
        if readiness == "ready":
            queryset = queryset.filter(ready)
        elif readiness == "blocked":
            queryset = queryset.exclude(ready).filter(
                status__in=["draft", "planned", "approved"]
            )
        elif readiness == "in_progress":
            queryset = queryset.filter(status="active")
        elif readiness == "completed":
            queryset = queryset.filter(status__in=["completed", "cancelled"])

        start_after = parse_date(params.get("start_after") or "")
        start_before = parse_date(params.get("start_before") or "")
        if start_after:
            queryset = queryset.filter(start_at__date__gte=start_after)
        if start_before:
            queryset = queryset.filter(start_at__date__lte=start_before)
        air_date = parse_date(params.get("air_date") or "")
        if air_date:
            queryset = queryset.filter(
                Q(air_detail__operation_date=air_date)
                | Q(
                    air_detail__operation_date__isnull=True,
                    start_at__date=air_date,
                )
            )
        return queryset

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return serializers.MissionWriteSerializer
        return serializers.MissionSerializer

    def get_permissions(self):
        if self.action in {"fuel_draws", "fuel_items", "fuel_unrecorded"}:
            # Fuel is the Logistics register's business: the Logistics Officer
            # and the Logistic NCOIC record it, and the planners read the result.
            return [IsAuthenticated(), CanRecordAirFuel()]
        # Only the final decision uses approval authority. Planning, editing,
        # submitting, routing and removing a draft all stay with the Operations
        # Center: command posts observe the mission, they do not change it.
        if self.action == "approve":
            return [IsAuthenticated(), CanApproveOperations()]
        if self.action in {"route", "begin_routing", "resubmit"}:
            return [IsAuthenticated(), CanRouteMissions()]
        if self.action == "start_mrm":
            # Checked against the mission's own MRM team inside the action.
            return [IsAuthenticated()]
        if self.action == "mission_order_approve":
            # The designated signatory is checked against the mission inside the
            # action; a commander may sign without mission-planning capability.
            return [IsAuthenticated()]
        if self.action in {
            "tracking",
            "tracking_start",
            "tracking_lead",
            "tracking_event",
            "tracking_ping",
            "tracking_emergency",
            "tracking_abort",
            "tracking_complete",
            "tracking_log",
            # The destination Operations Center reads the handoff and decides
            # whether to forward it to its command posts.
            "handoff",
        }:
            # Drivers, the convoy lead, monitors and the Operations Center all
            # act here; each action checks its own rule against the run.
            return [IsAuthenticated()]
        if self.action in {"approval", "routing_decision"}:
            # The current signer at each configured stage is checked inside the
            # service, so the refusal names the post and person who can act.
            return [IsAuthenticated()]
        if self.action == "destroy":
            # Removing a mission destroys its record and any drafts hanging off
            # it, so it stays with the Administrator alone. The Operations
            # Center plans and cancels missions; it does not erase them.
            return [IsAuthenticated(), IsAdministrator()]
        return [IsAuthenticated(), CanPlanOperations()]

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        instance = self.get_queryset().get(pk=response.data["id"])
        response.data = serializers.MissionSerializer(instance).data
        return response

    def update(self, request, *args, **kwargs):
        from apps.ops.models import MissionRiskTeam

        mission_id = kwargs.get("pk")

        # A destination-AOR unit may read the mission but never change it: the
        # mission belongs to the unit that planned it.
        guarded = self.get_object()
        allowed = accessible_unit_ids(request.user)
        if guarded.unit_id is None or guarded.unit_id not in allowed:
            raise PermissionDenied(
                f"Mission {guarded.mission_number} belongs to another unit. Your "
                "unit can monitor it as the destination AOR, but not change it."
            )

        before = set(
            MissionRiskTeam.objects.filter(mission_id=mission_id, is_active=True).values_list(
                "user_id", flat=True
            )
        )
        response = super().update(request, *args, **kwargs)
        instance = self.get_queryset().get(pk=response.data["id"])
        after = set(
            MissionRiskTeam.objects.filter(mission=instance, is_active=True).values_list(
                "user_id", flat=True
            )
        )
        if after - before:
            _notify_mrm_team(instance, request.user, only_ids=after - before)
        response.data = serializers.MissionSerializer(instance).data
        return response

    def perform_create(self, serializer):
        mission = serializer.save(created_by=self.request.user)
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="mission",
            entity_id=mission.pk,
            after=snapshot(mission, self.audit_fields),
            request=self.request,
        )
        notify(
            self.request.user,
            title=f"Mission {mission.mission_number} created",
            message=mission.title,
            category="mission",
            related=("mission", mission.id),
            actor=self.request.user,
        )
        _notify_mrm_team(mission, self.request.user)

        # A mission plotted outside the unit's own area: the form asked the
        # planner before saving, and this records that answer. Inside the
        # unit's own area the flag is ignored, so a stale checkbox can never
        # notify an unrelated unit.
        wants_handoff = (
            str(self.request.data.get("notify_destination_aor", "")).strip().lower()
            in {"true", "1", "yes", "on"}
        )
        if wants_handoff:
            handoff = aor_handoff.notify_destination(mission, self.request.user)
            if handoff["destination"] is not None:
                log_action(
                    actor=self.request.user,
                    action="notify_aor_handoff",
                    entity_type="mission",
                    entity_id=mission.pk,
                    after={
                        "unit": handoff["destination"]["unit_acronym"],
                        "aor": handoff["destination"]["aor_name"],
                        "notified": handoff["notified"],
                        "already_notified": handoff["already_notified"],
                    },
                    request=self.request,
                )

    @action(detail=True, methods=["get", "post"], url_path="aor-handoff")
    def handoff(self, request, pk=None):
        """Destination-AOR handoff status, and the Operations Center's forward.

        Only the destination unit's Operations Center is notified and monitors
        the mission. After reading the details it may forward them, once, to
        its Commander, Deputy Commander, Director for Operations and
        Operations Officer.
        """
        mission = self.get_object()
        if request.method == "GET":
            return Response(aor_handoff.status(mission, request.user))

        if not aor_handoff.can_forward(mission, request.user):
            raise PermissionDenied(
                "Only the destination unit's Operations Center may forward this "
                "mission to its command posts."
            )
        result = aor_handoff.forward(mission, request.user)
        if not result["recipients"]:
            raise ValidationError(
                {
                    "detail": (
                        "No Commander, Deputy Commander, Director for Operations "
                        "or Operations Officer holds a post in the destination unit."
                    )
                }
            )
        log_action(
            actor=request.user,
            action="forward_aor_handoff",
            entity_type="mission",
            entity_id=mission.pk,
            after={
                "unit": result["destination"]["unit_acronym"],
                "aor": result["destination"]["aor_name"],
                "forwarded": result["forwarded"],
                "already_forwarded": result["already_forwarded"],
            },
            request=request,
        )
        return Response(result)

    def perform_destroy(self, instance):
        # Missions are cancelled, not deleted: history must survive. Erasing a
        # mission is the Super Administrator's decision, and the seat may erase
        # one in any state - draft, completed or still running - because no
        # other caller can reach this branch.
        from apps.accounts.ownership import can_delete_records

        if not can_delete_records(self.request.user):
            raise PermissionDenied(
                "Only the Super Administrator may permanently delete a mission. "
                "Cancel it instead."
            )
        from apps.ops import tracking

        # A trip outlives its mission (the link is SET_NULL), so an erased
        # mission closes its trips and frees their vehicles here. Otherwise
        # a trip stays "in progress" for ever with no run left to finish it,
        # and its vehicles never return to the register.
        with transaction.atomic():
            tracking.reset_trips_for_mission(instance, actor=self.request.user)
            super().perform_destroy(instance)

    # -- Aircraft fuel draws ----------------------------------------------

    @action(detail=True, methods=["post"], url_path="fuel-draws")
    def fuel_draws(self, request, pk=None):
        """Record the POL-01 an aircraft burned, in drums or bulk litres."""
        from apps.ops import air_fuel

        mission = self.get_object()
        draw = air_fuel.record_draw(
            mission=mission,
            aircraft_id=request.data.get("aircraft"),
            leg_id=request.data.get("leg"),
            source=request.data.get("source"),
            quantity=request.data.get("quantity"),
            item_id=request.data.get("item"),
            note=request.data.get("note") or "",
            allow_pending=bool(request.data.get("allow_pending")),
            no_consumption=bool(request.data.get("no_consumption")),
            actor=request.user,
        )
        log_action(
            actor=request.user,
            action="air_fuel_draw",
            entity_type="mission",
            entity_id=mission.pk,
            after={
                "aircraft": draw.aircraft_id,
                "leg": draw.leg_id,
                "source": draw.source,
                "quantity": draw.quantity,
                "litres": float(draw.litres or 0),
                "item": draw.item_id,
                "transaction": draw.transaction_id,
                "state": draw.state,
            },
            note=(
                f"Fuel draw {draw.quantity} {draw.get_source_display().lower()} "
                f"for {draw.aircraft.display_name} on leg "
                f"{draw.leg.sequence if draw.leg_id else '—'}"
            ),
            request=request,
        )
        # Re-read so the response carries the posted draw and the ledger link.
        mission = self.get_queryset().get(pk=mission.pk)
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=False, methods=["get"], url_path="fuel-items")
    def fuel_items(self, request):
        """POL-01 stores at the aircraft's own station, nearest expiry first."""
        from apps.ops import air_fuel

        mission = (
            self.get_queryset()
            .filter(pk=request.query_params.get("mission") or 0)
            .first()
        )
        leg = None
        if mission is not None and request.query_params.get("leg"):
            leg = AirMissionItinerary.objects.filter(
                pk=request.query_params.get("leg"),
                aircraft__element__air_detail__mission=mission,
            ).first()
        return Response(
            {
                "results": air_fuel.items_payload(
                    request.user, mission=mission, leg=leg
                )
            }
        )

    @action(detail=False, methods=["get"], url_path="fuel-unrecorded")
    def fuel_unrecorded(self, request):
        """Landed air sorties that still owe a fuel figure."""
        from django.utils.dateparse import parse_date

        from apps.ops import air_fuel

        rows = air_fuel.unrecorded_missions(
            request.user, air_date=parse_date(request.query_params.get("air_date") or "")
        )
        return Response({"count": len(rows), "results": rows})

    # -- Daily Operations board actions -----------------------------------

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, pk=None):
        """Cancel a mission without erasing it or its history.

        The Daily Operations board uses this for a flight that will not run.
        A cancelled mission is closed: the board refuses further actual times
        and its dependent missions remain blocked until they are replanned.
        """
        mission = self.get_object()
        if mission.status == "cancelled":
            return Response(
                serializers.MissionSerializer(
                    mission, context=self.get_serializer_context()
                ).data
            )
        if mission.status == "completed":
            raise ValidationError(
                {"detail": "A completed mission cannot be cancelled."}
            )
        if mission.air_is_airborne():
            raise ValidationError(
                {
                    "detail": (
                        "Wait until every aircraft has landed at its destination "
                        "before cancelling this mission."
                    )
                }
            )

        reason = (request.data.get("reason") or "").strip()[:500]
        before = snapshot(mission, self.audit_fields)

        # Slash only the legs that were never flown. Recorded legs keep their
        # ATD/ATA and remain evidence of what was accomplished before the
        # cancellation.
        remaining = AirMissionItinerary.objects.filter(
            aircraft__element__air_detail__mission=mission,
            atd__isnull=True,
            ata__isnull=True,
            tot__isnull=True,
            toot__isnull=True,
        )
        remaining.update(status=AirMissionItinerary.STATUS_CANCELLED)

        mission.status = "cancelled"
        mission.is_held = False
        mission.held_at = None
        mission.save(update_fields=["status", "is_held", "held_at", "updated_at"])
        # The ground side of a cancelled mission cannot run any more: close
        # its trips and put their vehicles back before the audit entry is
        # written, so the board never shows a movement for a dead plan.
        from apps.ops import tracking as tracking_service

        tracking_service.cancel_for_mission(mission, request.user, note=reason)

        log_action(
            actor=request.user,
            action="cancel",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after=snapshot(mission, self.audit_fields),
            note=reason or "Cancelled from the Daily Operations board.",
            request=request,
        )
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        """Place or release a board hold without changing the planning status.

        A hold is deliberately not a status: a draft stays a draft and an
        approved mission stays approved, so releasing the hold restores exactly
        where the mission was. It only prevents takeoff and dispatch until it is
        released.
        """
        mission = self.get_object()
        if mission.status in {"completed", "cancelled"}:
            raise ValidationError({"detail": "This mission is closed."})

        raw = request.data.get("hold", True)
        if isinstance(raw, str):
            hold = raw.strip().lower() not in {"", "0", "false", "no", "off"}
        else:
            hold = bool(raw)

        if mission.air_is_airborne():
            raise ValidationError(
                {
                    "detail": (
                        "Wait until every aircraft has landed at its destination "
                        "before placing or releasing a hold."
                    )
                }
            )
        if hold == mission.is_held:
            return Response(
                serializers.MissionSerializer(
                    mission, context=self.get_serializer_context()
                ).data
            )

        reason = (request.data.get("reason") or "").strip()[:500]
        before = snapshot(mission, self.audit_fields)
        mission.is_held = hold
        mission.held_at = _now() if hold else None
        mission.save(update_fields=["is_held", "held_at", "updated_at"])

        log_action(
            actor=request.user,
            action="hold" if hold else "release_hold",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after=snapshot(mission, self.audit_fields),
            note=reason or ("Held from the Daily Operations board." if hold else "Released from hold."),
            request=request,
        )
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"], url_path="change-itinerary")
    @transaction.atomic
    def change_itinerary(self, request, pk=None):
        """Revise only the legs that have not started.

        A leg with any actual time is evidence of what was flown. It stays
        locked, including when the board is changing the remaining itinerary.
        The submitted leg is the element's shared leg, so the same plan is
        applied to every aircraft in that element.
        """
        mission = self.get_object()
        if mission.category != Mission.CATEGORY_AIR:
            raise ValidationError(
                {"detail": "Only air mission itineraries can be changed here."}
            )
        if mission.status in {"completed", "cancelled"}:
            raise ValidationError({"detail": "This mission is closed."})

        rows = request.data.get("legs")
        if not isinstance(rows, list) or not rows:
            raise ValidationError(
                {"detail": "Select at least one remaining itinerary leg."}
            )

        allowed_fields = {
            "etd",
            "eta",
            "origin",
            "destination",
            "route",
            "mission",
            "point_role",
            "overflight",
            "return_to_origin",
            "remarks",
        }
        changed = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValidationError({"detail": "Each itinerary change must be an object."})
            leg_id = row.get("id")
            leg = (
                AirMissionItinerary.objects.select_for_update()
                .select_related("aircraft__element")
                .filter(
                    pk=leg_id,
                    aircraft__element__air_detail__mission=mission,
                )
                .first()
            )
            if leg is None:
                raise ValidationError(
                    {"detail": f"Itinerary {leg_id} does not belong to this mission."}
                )
            if leg.status == AirMissionItinerary.STATUS_CANCELLED:
                raise ValidationError(
                    {"detail": f"Leg {leg.sequence} is cancelled and cannot be changed."}
                )

            targets = list(
                AirMissionItinerary.objects.select_for_update().filter(
                    aircraft__element=leg.aircraft.element,
                    sequence=leg.sequence,
                )
            )
            if any(
                target.atd
                or target.ata
                or target.tot
                or target.toot
                or target.status == AirMissionItinerary.STATUS_CANCELLED
                for target in targets
            ):
                raise ValidationError(
                    {
                        "detail": (
                            f"Leg {leg.sequence} has already started or finished. "
                            "Only remaining legs can be changed."
                        )
                    }
                )

            data = {
                field: row[field]
                for field in allowed_fields
                if field in row
            }
            if not data:
                continue
            serializer = serializers.AirMissionItinerarySerializer(
                leg, data=data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            values = dict(serializer.validated_data)
            if not values:
                continue

            update_fields = list(values.keys())
            for target in targets:
                for field, value in values.items():
                    setattr(target, field, value)
                target.save(update_fields=update_fields)

            # Keep the next remaining leg chained to the revised destination;
            # a completed next leg is never rewritten.
            if "destination" in values:
                next_rows = list(
                    AirMissionItinerary.objects.select_for_update()
                    .filter(
                        aircraft__element=leg.aircraft.element,
                        sequence__gt=leg.sequence,
                    )
                    .order_by("sequence")
                )
                if next_rows:
                    next_sequence = next_rows[0].sequence
                    for next_leg in next_rows:
                        if next_leg.sequence != next_sequence:
                            continue
                        if (
                            next_leg.atd
                            or next_leg.ata
                            or next_leg.tot
                            or next_leg.toot
                            or next_leg.status
                            == AirMissionItinerary.STATUS_CANCELLED
                        ):
                            continue
                        next_leg.origin = (values["destination"] or "")[:255]
                        next_leg.save(update_fields=["origin"])

            changed.append(
                {
                    "id": leg.pk,
                    "element": leg.aircraft.element_id,
                    "leg": leg.sequence,
                    "fields": sorted(values.keys()),
                }
            )

        # Keep the detail-level schedule and final destination in step with the
        # revised legs: completion relocation and the PDF use these fields.
        detail = getattr(mission, "air_detail", None)
        if detail is not None:
            all_legs = list(
                AirMissionItinerary.objects.filter(
                    aircraft__element__air_detail=detail
                ).order_by("aircraft__element__sequence", "sequence")
            )
            if all_legs:
                detail_fields = []
                departures = [leg.etd for leg in all_legs if leg.etd]
                arrivals = [leg.eta for leg in all_legs if leg.eta]
                if departures:
                    detail.scheduled_departure = min(departures)
                    detail_fields.append("scheduled_departure")
                if arrivals:
                    detail.scheduled_arrival = max(arrivals)
                    detail_fields.append("scheduled_arrival")
                origin = all_legs[0].origin or all_legs[0].route_label
                destination = all_legs[-1].destination or all_legs[-1].route_label
                if detail.departure_location != origin:
                    detail.departure_location = origin
                    detail_fields.append("departure_location")
                if detail.destination_location != destination:
                    detail.destination_location = destination
                    detail_fields.append("destination_location")
                route = "\n".join(
                    leg.route_label for leg in all_legs if leg.route_label
                )
                if detail.route != route:
                    detail.route = route
                    detail_fields.append("route")
                if detail_fields:
                    detail.save(update_fields=detail_fields)

        mission.refresh_from_db()
        log_action(
            actor=request.user,
            action="change_itinerary",
            entity_type="mission",
            entity_id=mission.pk,
            after={"legs": changed},
            note="Remaining itinerary changed from the Daily Operations board.",
            request=request,
        )
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    # -- approval workflow ------------------------------------------------

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """Send a draft mission to the Operations Center.

        Step 2 of the chain in the specification: the creator (or an authorised
        requester) hands the mission to the Operations Center, which is what
        makes it eligible for routing to the Operations Officer.
        """
        from apps.command.models import MissionRouting

        mission = self.get_object()
        if (
            mission.created_by_id != request.user.id
            and authority(request.user) not in (ADMIN, UNIT_SAFETY)
            and not can(request.user, roles.CAP_MANAGE_MISSIONS, mission.unit_id)
        ):
            raise PermissionDenied("Only the creator or command may submit this mission.")
        if mission.status not in {"draft", "planned"}:
            raise ValidationError(
                {"detail": "Only a draft or planned mission can be submitted."}
            )
        if MissionRouting.objects.filter(
            mission=mission, stage="operations_center", action="routed"
        ).exists():
            raise ValidationError({"detail": "This mission is already with the Operations Center."})

        before = snapshot(mission, self.audit_fields)
        mission.status = "planned"
        if (
            mission.approving_authority_id
            and mission.approval_status == Mission.APPROVAL_RETURNED
        ):
            # The same authority picks the mission back up; the assignment is
            # unchanged, only the pending decision is reset.
            mission.approval_status = Mission.APPROVAL_PENDING
            mission.approval_assigned_at = _now()
            mission.save(
                update_fields=[
                    "status",
                    "approval_status",
                    "approval_assigned_at",
                    "updated_at",
                ]
            )
            notify(
                mission.approving_authority,
                title=f"Mission {mission.mission_number} resubmitted for approval",
                message=mission.title,
                category="approval",
                priority="high",
                related=("mission", mission.id),
                actor=request.user,
                requires_action=True,
                action_label="Review and approve",
                action_url=f"/missions/{mission.id}",
            )
        else:
            mission.save(update_fields=["status", "updated_at"])
        MissionRouting.objects.create(
            mission=mission,
            stage="operations_center",
            action="routed",
            actor=request.user,
            note=request.data.get("note", "Submitted to the Operations Center"),
        )
        log_action(
            actor=request.user,
            action="submit",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after={"status": "planned", "stage": "operations_center"},
            request=request,
        )

        from apps.command.services import responsible_users

        notify_many(
            responsible_users(
                mission.unit_id,
                roles.ROLE_OPERATIONS_CENTER,
                roles.ROLE_OPERATIONS_OFFICER,
                roles.ROLE_ADMIN_OFFICER,
            ),
            title=f"Mission {mission.mission_number} awaiting Operations Center routing",
            message=mission.title,
            category="mission",
            priority="high",
            related=("mission", mission.id),
            actor=request.user,
            requires_action=True,
            action_label="Check completeness and route",
            action_url=f"/missions/{mission.id}",
        )
        return Response(serializers.MissionSerializer(mission).data)

    @action(detail=True, methods=["post"])
    def route(self, request, pk=None):
        """Record a routing decision for the current stage.

        Enforced order: ``operations_center`` (completeness check) then
        ``operations_officer`` (review). The final decision is ``approve``.
        No stage can be skipped and no decision can be rewritten.
        """
        import datetime as _dt

        from apps.command.models import MissionRouting
        from apps.command.services import responsible_users

        mission = self.get_object()
        stage = request.data.get("stage")
        decision = request.data.get("action", "routed")
        note = request.data.get("note", "")

        if stage not in {choice[0] for choice in MissionRouting.STAGE_CHOICES} - {
            MissionRouting.STAGE_DECISION_AUTHORITY
        }:
            raise ValidationError(
                {"stage": "Use 'operations_center' or 'operations_officer'."}
            )
        allowed_actions = {
            MissionRouting.STAGE_OPERATIONS_CENTER: {
                MissionRouting.ACTION_ROUTED,
                MissionRouting.ACTION_RETURNED,
            },
            MissionRouting.STAGE_OPERATIONS_OFFICER: {
                MissionRouting.ACTION_ROUTED,
                MissionRouting.ACTION_APPROVED,
                MissionRouting.ACTION_REJECTED,
                MissionRouting.ACTION_RETURNED,
            },
        }[stage]
        if decision not in allowed_actions:
            raise ValidationError(
                {
                    "action": (
                        f"At the {stage} stage use one of: "
                        + ", ".join(sorted(allowed_actions))
                        + "."
                    )
                }
            )
        if mission.status in {"completed", "cancelled"}:
            raise ValidationError({"detail": "This mission is closed."})

        if stage == MissionRouting.STAGE_OPERATIONS_CENTER:
            if not can(request.user, roles.CAP_ROUTE, mission.unit_id):
                raise PermissionDenied(
                    "Only a post with routing authority may move a mission from the "
                    "Operations Center."
                )
            if not MissionRouting.objects.filter(
                mission=mission, stage="operations_center", action="routed"
            ).exists():
                raise ValidationError(
                    {"detail": "The mission has not been submitted to the Operations Center."}
                )
            if decision == "routed":
                already = MissionRouting.objects.filter(
                    mission=mission, stage="operations_officer", action="routed"
                ).exists()
                if already:
                    raise ValidationError(
                        {"detail": "The mission has already been routed to the Operations Officer."}
                    )
                # Routing to an approving authority means naming that authority.
                # The form usually assigns it first; accepting it here as well
                # lets the two steps happen in one deliberate action.
                role = request.data.get("approving_authority_role")
                person = request.data.get("approving_authority")
                if role or person:
                    try:
                        mission = approvals.assign_authority(
                            mission,
                            role=role or mission.approving_authority_role,
                            user_id=person or mission.approving_authority_id,
                            actor=request.user,
                            note=note,
                            request=request,
                        )
                    except approvals.ApprovalError as exc:
                        raise ValidationError({"approving_authority": str(exc)}) from exc
                elif mission.approving_authority_id is None:
                    raise ValidationError(
                        {
                            "approving_authority": (
                                "Select the approving authority before routing this "
                                "mission for approval."
                            )
                        }
                    )
        else:  # operations_officer
            # A dedicated capability: the Operations Center screens and routes,
            # it does not inherit the reviewer's decision authority here.
            if not can(request.user, roles.CAP_REVIEW_MISSIONS, mission.unit_id):
                raise PermissionDenied(
                    "Only a post with mission review authority may act at the "
                    "Operations Officer stage."
                )
            if not MissionRouting.objects.filter(
                mission=mission, stage="operations_officer", action="routed"
            ).exists():
                raise ValidationError(
                    {
                        "detail": (
                            "The Operations Center must route this mission to the "
                            "Operations Officer before it can be reviewed here."
                        )
                    }
                )

        # A routing decision by the Operations Center *is* the hand-off to the
        # Operations Officer, so it records both events: the centre's check and
        # the officer's receipt. Nothing is implied later from a single row.
        rows = [(stage, decision)]
        if (
            stage == MissionRouting.STAGE_OPERATIONS_CENTER
            and decision == MissionRouting.ACTION_ROUTED
        ):
            rows.append((MissionRouting.STAGE_OPERATIONS_OFFICER, MissionRouting.ACTION_ROUTED))
        for row_stage, row_action in rows:
            MissionRouting.objects.create(
                mission=mission,
                stage=row_stage,
                action=row_action,
                actor=request.user,
                note=note if row_stage == stage else "Received from the Operations Center",
            )
        log_action(
            actor=request.user,
            action=f"route_{stage}",
            entity_type="mission",
            entity_id=mission.pk,
            after={"stage": stage, "action": decision, "note": note},
            request=request,
        )

        if decision == "returned":
            mission.status = "draft"
            mission.save(update_fields=["status", "updated_at"])
            notify(
                mission.created_by,
                title=f"Mission {mission.mission_number} returned for correction",
                message=note,
                level="warning",
                category="mission",
                related=("mission", mission.id),
                actor=request.user,
                priority="high",
                requires_action=True,
                action_label="Correct and resubmit",
                action_url=f"/missions/{mission.id}",
            )
            complete_action(
                related_type="mission",
                related_ids=[mission.id],
                note=note,
                actor=request.user,
            )
        elif stage == MissionRouting.STAGE_OPERATIONS_CENTER:
            complete_action(
                related_type="mission",
                related_ids=[mission.id],
                note="Routed to the Operations Officer",
                actor=request.user,
            )
            notify_many(
                responsible_users(mission.unit_id, roles.ROLE_OPERATIONS_OFFICER),
                title=f"Mission {mission.mission_number} needs operational review",
                message=mission.title,
                category="mission",
                priority="high",
                related=("mission", mission.id),
                actor=request.user,
                requires_action=True,
                action_label="Review and recommend",
                action_url=f"/missions/{mission.id}",
            )
        else:
            # The Operations Officer has signed. Hand the mission to the next
            # configured step, not to every command post at once.
            from apps.command.models import MissionRouting as _Routing
            from apps.core.roles import approver_roles_for
            from apps.ops import approvals as _approvals

            complete_action(
                related_type="mission",
                related_ids=[mission.id],
                note="Operations Officer review complete",
                actor=request.user,
            )
            next_role = _approvals.current_route_role(mission)
            if next_role is None:
                # The route is fully signed; the designated authority records
                # the final decision through the approval action.
                notify_many(
                    responsible_users(
                        mission.unit_id,
                        *approver_roles_for("mission", mission.unit_id, mission.category),
                    ),
                    title=f"Mission {mission.mission_number} awaits your decision",
                    message=mission.title,
                    category="approval",
                    priority="high",
                    related=("mission", mission.id),
                    actor=request.user,
                    requires_action=True,
                    action_label="Approve or reject",
                    action_url=f"/missions/{mission.id}",
                )
            else:
                next_stage = _approvals.ROUTE_STAGE_BY_ROLE[next_role]
                _Routing.objects.get_or_create(
                    mission=mission,
                    stage=next_stage,
                    action=_Routing.ACTION_ROUTED,
                    defaults={
                        "actor": request.user,
                        "note": "Forwarded from the Operations Officer",
                    },
                )
                _approvals._notify_current_step(mission, next_role, actor=request.user)
                _approvals.notify_route_higher_authorities(mission, actor=request.user)

        return Response(
            {
                "mission": serializers.MissionSerializer(mission).data,
                "routing": [
                    {
                        "stage": row.stage,
                        "action": row.action,
                        "actor": row.actor.username if row.actor_id else "",
                        "note": row.note,
                        "created_at": row.created_at,
                    }
                    for row in MissionRouting.objects.filter(mission=mission).select_related(
                        "actor"
                    )
                ],
                "recorded": {"stage": stage, "action": decision, "at": _now()},
            }
        )

    @action(detail=True, methods=["post"], url_path="begin-routing")
    def begin_routing(self, request, pk=None):
        """Open the route configured by the selected approving authority."""
        mission = self.get_object()
        if mission.status not in {"draft", "planned"}:
            raise ValidationError(
                {"detail": "Only a draft or planned mission can start approval routing."}
            )
        if mission.approval_status == Mission.APPROVAL_APPROVED:
            raise ValidationError({"detail": "This mission is already approved."})
        try:
            approvals.start_route(
                mission,
                actor=request.user,
                note=request.data.get("note", ""),
                request=request,
            )
        except approvals.ApprovalError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        log_action(
            actor=request.user,
            action="begin_routing",
            entity_type="mission",
            entity_id=mission.pk,
            after={
                "approval_status": mission.approval_status,
                "route": approvals.route_plan(mission),
            },
            request=request,
        )
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"], url_path="resubmit")
    def resubmit(self, request, pk=None):
        """Reopen the route after the mission was returned for revision."""
        mission = self.get_object()
        try:
            approvals.resubmit_route(
                mission,
                actor=request.user,
                note=request.data.get("note", ""),
                request=request,
            )
        except approvals.ApprovalError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        mission.refresh_from_db()
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"], url_path="routing-decision")
    def routing_decision(self, request, pk=None):
        """Approve, return or reject at the signer's current route step."""
        mission = self.get_object()
        try:
            approvals.record_route_decision(
                mission,
                decision=request.data.get("decision"),
                note=request.data.get("note", ""),
                actor=request.user,
                request=request,
            )
        except approvals.ApprovalError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        mission.refresh_from_db()
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Final approval - impossible until the routing chain is complete."""
        from apps.command.models import MissionRouting

        mission = self.get_object()
        if mission.status in {"completed", "cancelled"}:
            raise ValidationError({"detail": "This mission can no longer be approved."})
        if mission.category == Mission.CATEGORY_GROUND and not hasattr(mission, "ground_detail"):
            raise ValidationError({"detail": "Ground operations require ground details."})
        if mission.category == Mission.CATEGORY_AIR and not hasattr(mission, "air_detail"):
            raise ValidationError({"detail": "Air operations require air details."})
        plan = approvals.route_plan(mission)
        if not MissionRouting.objects.filter(
            mission=mission, stage="operations_officer", action="approved"
        ).exists():
            raise ValidationError(
                {
                    "detail": (
                        "This mission has not completed the approval chain. It must pass "
                        "through the Operations Center and be approved by the Operations "
                        "Officer first."
                    )
                }
            )
        # The final signer may not shortcut a configured intermediate post. The
        # dedicated routing endpoint records each post's comment in turn; this
        # legacy action stays useful for the final signature only.
        missing = [
            stage["label"]
            for stage in plan["stages"][:-1]
            if stage["status"] != "approved"
        ]
        if missing:
            raise ValidationError(
                {
                    "detail": (
                        "This mission still needs approval from: "
                        + ", ".join(missing)
                        + " before the final decision can be recorded."
                    )
                }
            )

        # Final approval certifies readiness to execute; an incomplete MRM means
        # that certification would be false, so it is refused here too.
        if not approvals.mrm_complete(mission):
            raise ValidationError(
                {
                    "detail": (
                        "This mission's MRM assessment must be completed and approved "
                        "before the mission can be approved."
                    )
                }
            )

        # When the Operations Center named an approving authority, that person
        # alone decides - the post-based rule below must not let somebody else
        # sign on their behalf.
        if mission.approving_authority_id is not None:
            if mission.approving_authority_id != request.user.id:
                raise PermissionDenied(
                    "Only the assigned approving authority may give final approval "
                    "for this mission."
                )
        else:
            from apps.core.roles import approver_roles_for

            permitted = set(
                approver_roles_for("mission", mission.unit_id, mission.category)
            )
            holds_post = bool(
                roles.effective_roles(request.user, mission.unit_id) & permitted
            )
            if authority(request.user) not in (ADMIN, UNIT_SAFETY) and not holds_post:
                raise PermissionDenied(
                    "Only the designated Decision Authority may give final approval."
                )
        if mission.created_by_id == request.user.id:
            raise PermissionDenied("You cannot approve a mission you created.")

        before = snapshot(mission, self.audit_fields)
        mission.status = "approved"
        mission.approved_by = request.user
        mission.approved_at = _now()
        # Keep the approval fields the execution gate reads in step, so the
        # legacy decision-authority path and the assigned-approver path agree.
        mission.approval_status = Mission.APPROVAL_APPROVED
        mission.approval_decided_at = _now()
        mission.approval_note = request.data.get("note", "")
        mission.approved_revision = mission.revision
        mission.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "approval_status",
                "approval_decided_at",
                "approval_note",
                "approved_revision",
                "updated_at",
            ]
        )
        from apps.ops import mission_order_workflow

        mission_order_workflow.release_for_signature(
            mission, actor=request.user, request=request
        )
        MissionRouting.objects.create(
            mission=mission,
            stage="decision_authority",
            action="approved",
            actor=request.user,
            note=request.data.get("note", ""),
        )
        log_action(
            actor=request.user,
            action="approve",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after=snapshot(mission, self.audit_fields),
            request=request,
        )
        complete_action(
            related_type="mission",
            related_ids=[mission.id],
            note=f"Approved by {request.user.username}",
            actor=request.user,
        )
        notify(
            mission.created_by,
            title=f"Mission {mission.mission_number} approved",
            message=mission.title,
            level="success",
            category="approval",
            related=("mission", mission.id),
            actor=request.user,
        )
        return Response(serializers.MissionSerializer(mission).data)

    # -- approving authority & approval decisions (spec §2) ---------------

    @action(detail=False, methods=["get"], url_path="approving-authorities")
    def approving_authorities_for_unit(self, request):
        """Eligible authorities for a unit, for the planning form.

        The mission does not exist yet when the Operations Center fills the
        form in, so the same roster is offered by unit here. The rules are the
        unit-level half of the mission endpoint's; the real check happens again
        when the assignment is saved.
        """
        if not can(request.user, roles.CAP_MANAGE_MISSIONS):
            raise PermissionDenied(
                "Only the Operations Center may choose a mission's approving authority."
            )
        raw_unit = request.query_params.get("unit")
        try:
            unit_id = int(raw_unit) if raw_unit else None
        except (TypeError, ValueError):
            raise ValidationError({"unit": "Unit must be numeric."}) from None
        if unit_id is None:
            raise ValidationError({"unit": "Select the unit first."})
        scope = unit_ids_for_filter(request.user)
        if scope is not None and unit_id not in scope:
            raise PermissionDenied("That unit is outside your authorised scope.")
        return Response(
            {
                "unit": unit_id,
                "roles": [
                    {"value": value, "label": label}
                    for value, label in Mission.APPROVING_AUTHORITY_CHOICES
                ],
                "results": approvals.eligible_for_unit(
                    unit_id, creator_id=request.user.id
                ),
            }
        )

    @action(detail=True, methods=["get"], url_path="approving-authorities")
    def approving_authorities(self, request, pk=None):
        """Eligible approving authorities for this mission.

        The roster is derived from live appointments and acting delegations, so
        a post with no current holder simply yields no candidate - the form can
        then explain that instead of offering a stale name.
        """
        mission = self.get_object()
        if not can(request.user, roles.CAP_MANAGE_MISSIONS, mission.unit_id):
            raise PermissionDenied(
                "Only the Operations Center may choose a mission's approving authority."
            )
        return Response(
            {
                "mission": mission.id,
                "roles": [
                    {"value": value, "label": label}
                    for value, label in Mission.APPROVING_AUTHORITY_CHOICES
                ],
                "results": approvals.eligible_authorities(mission),
            }
        )

    @action(detail=True, methods=["post"], url_path="approving-authority")
    def approving_authority(self, request, pk=None):
        """Assign or reassign the person who must approve this mission."""
        mission = self.get_object()
        if not can(request.user, roles.CAP_MANAGE_MISSIONS, mission.unit_id):
            raise PermissionDenied(
                "Only the Operations Center may choose a mission's approving authority."
            )
        try:
            mission = approvals.assign_authority(
                mission,
                role=request.data.get("role") or request.data.get("approving_authority_role"),
                user_id=request.data.get("user_id") or request.data.get("approving_authority"),
                actor=request.user,
                note=request.data.get("note", ""),
                request=request,
            )
        except approvals.ApprovalError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"])
    def approval(self, request, pk=None):
        """Approve, return or reject - the assigned authority's own action.

        Authorisation is the assignment itself, checked inside the service, so
        an Operations Center planner cannot decide on the approver's behalf.
        """
        mission = self.get_object()
        try:
            mission = approvals.record_decision(
                mission,
                decision=request.data.get("decision"),
                note=request.data.get("note", ""),
                actor=request.user,
                request=request,
            )
        except approvals.ApprovalError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"], url_path="mission-order/signatory")
    def mission_order_signatory(self, request, pk=None):
        """Designate the person who signs this mission's Mission Order."""
        from apps.ops import mission_order_workflow

        mission = self.get_object()
        if not can(request.user, roles.CAP_MANAGE_MISSIONS, mission.unit_id):
            raise PermissionDenied(
                "Only the Operations Center may designate a Mission Order signatory."
            )
        try:
            mission = mission_order_workflow.assign_signatory(
                mission,
                user_id=request.data.get("user_id")
                or request.data.get("signatory_id"),
                actor=request.user,
                request=request,
            )
        except mission_order_workflow.MissionOrderError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"], url_path="mission-order/approve")
    def mission_order_approve(self, request, pk=None):
        """The designated signatory signs and approves the Mission Order."""
        from apps.ops import mission_order_workflow

        mission = self.get_object()
        try:
            mission = mission_order_workflow.approve(
                mission,
                actor=request.user,
                note=request.data.get("note", ""),
                request=request,
            )
        except mission_order_workflow.MissionOrderError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=["post"], url_path="dispatch")
    def dispatch_mission(self, request, pk=None):
        """Start the mission - the single guarded path into execution.

        Named ``dispatch_mission`` because ``dispatch`` is DRF's own request
        entry point; overriding it would break every route on this viewset.
        """
        mission = self.get_object()
        if not can(request.user, roles.CAP_MANAGE_MISSIONS, mission.unit_id):
            raise PermissionDenied(
                "Only the Operations Center may dispatch a mission."
            )
        if mission.is_held:
            raise ValidationError(
                {"detail": "This mission is on hold. Release the hold before dispatching."}
            )
        if mission.status == "active":
            raise ValidationError({"detail": "This mission is already in progress."})
        if mission.status in {"completed", "cancelled"}:
            raise ValidationError({"detail": "This mission is closed."})
        try:
            approvals.assert_dispatchable(mission)
        except approvals.ExecutionBlocked as exc:
            log_action(
                actor=request.user,
                action="dispatch_blocked",
                entity_type="mission",
                entity_id=mission.pk,
                after={"blockers": [blocker["code"] for blocker in exc.blockers]},
                request=request,
            )
            raise ValidationError(
                {"detail": str(exc), "blockers": exc.blockers}
            ) from exc

        before = snapshot(mission, self.audit_fields)
        mission.status = "active"
        mission.dispatched_at = _now()
        mission.save(update_fields=["status", "dispatched_at", "updated_at"])

        # Dispatching answers the ready-to-dispatch task in the bell.
        from apps.ops.notifications import complete_action

        complete_action(
            related_type="mission",
            related_ids=[mission.pk],
            note="Mission dispatched",
            actor=request.user,
        )

        # Dispatching is also the moment movement tracking begins: one run per
        # planned vehicle, its driver notified, and the dispatch time held as an
        # unconfirmed ATD until somebody confirms the actual wheel-roll.
        from apps.ops import tracking as tracking_service

        tracking_service.start_for_mission(mission, request.user)

        log_action(
            actor=request.user,
            action="dispatch",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after=snapshot(mission, self.audit_fields),
            request=request,
        )
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    # -- live movement tracking (ETD / ATD / stops / GPS) -----------------

    @action(detail=True, methods=["post"], url_path="air-arrival")
    def air_arrival(self, request, pk=None):
        """Record an air mission's actual arrival and rendezvous readiness."""
        from apps.core import timeutils
        from apps.ops import tracking as tracking_service

        mission = self.get_object()
        if mission.category != Mission.CATEGORY_AIR:
            raise ValidationError(
                {"detail": "Air arrival applies to air missions only."}
            )
        if mission.status != "active":
            raise ValidationError(
                {"detail": "Only an active air mission can record its arrival."}
            )

        raw_at = request.data.get("at")
        arrived_at = parse_datetime(raw_at) if raw_at else _now()
        if arrived_at is None:
            raise ValidationError({"at": "Enter a valid arrival date and time."})
        arrived_at = timeutils.to_manila(arrived_at)

        mission = self.get_object()
        detail = getattr(mission, "air_detail", None)
        if detail is None:
            raise ValidationError(
                {"detail": "This mission has no air operations detail to update."}
            )

        ready = bool(request.data.get("ready", True))
        complete = bool(request.data.get("complete", False))
        if complete:
            ready = True

        before = snapshot(mission, self.audit_fields)
        detail.actual_arrival = arrived_at
        fields = ["actual_arrival"]
        if ready:
            detail.rendezvous_ready_at = arrived_at
            fields.append("rendezvous_ready_at")
        detail.save(update_fields=fields)

        # An arrival is also a location report. The aircraft is now at the
        # mission's destination (the assembly point for a preposition), so the
        # register must stop saying it is still at its old base. This is what
        # lets the dispatch-time 5 km check see the formation together.
        # Both linked assignments and the manual element rows used by the
        # Flight Operations sheet move to the arrival point; otherwise the next
        # dispatch or board departure would still see the old base.
        relocated = _relocate_air_assets(detail)

        if complete:
            tracking_service.complete_mission(
                mission,
                request.user,
                force=True,
                note="Air mission arrived and was closed at the rendezvous.",
            )
            mission.refresh_from_db()

        log_action(
            actor=request.user,
            action="air_arrival",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after={
                "actual_arrival": arrived_at.isoformat(),
                "rendezvous_ready": ready,
                "completed": complete,
                "relocated_assets": relocated,
            },
            request=request,
        )
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"itineraries/(?P<itinerary_id>[^/.]+)/actual",
    )
    def itinerary_actual(self, request, pk=None, itinerary_id=None):
        """Record ATD/ATA and overflight TOT/TOOT without changing ETD/ETA."""
        from apps.core import timeutils

        mission = self.get_object()
        if mission.category != Mission.CATEGORY_AIR:
            raise ValidationError(
                {"detail": "Actual leg times apply to air missions only."}
            )
        event = (request.data.get("event") or "").strip().lower()
        wants_se = event == "se" or "se" in request.data
        wants_sd = event == "sd" or "sd" in request.data
        # The final landing closes the mission at ATA, but the engines are shut
        # down after that landing: SD stays recordable on a flown leg.
        if mission.status in {"completed", "cancelled"} and not (
            mission.status == "completed" and wants_sd
        ):
            raise ValidationError({"detail": "This mission is closed."})
        itinerary = (
            AirMissionItinerary.objects.select_related(
                "aircraft__element__air_detail"
            )
            .filter(
                pk=itinerary_id,
                aircraft__element__air_detail__mission=mission,
            )
            .first()
        )
        if itinerary is None:
            raise ValidationError(
                {"detail": "That itinerary leg does not belong to this mission."}
            )

        before = {
            "atd": itinerary.atd.isoformat() if itinerary.atd else None,
            "ata": itinerary.ata.isoformat() if itinerary.ata else None,
            "tot": itinerary.tot.isoformat() if itinerary.tot else None,
            "toot": itinerary.toot.isoformat() if itinerary.toot else None,
            "se": itinerary.se.isoformat() if itinerary.se else None,
            "sd": itinerary.sd.isoformat() if itinerary.sd else None,
            "status": itinerary.status,
        }
        previous = (
            AirMissionItinerary.objects.filter(
                aircraft=itinerary.aircraft,
                sequence__lt=itinerary.sequence,
            )
            .order_by("-sequence")
            .first()
        )
        origin_is_over = bool(previous and previous.overflight)
        wants_takeoff = event == "takeoff" or "atd" in request.data
        wants_landing = event == "landing" or "ata" in request.data
        wants_tot = event == "tot" or "tot" in request.data
        wants_toot = event == "toot" or "toot" in request.data
        if wants_takeoff:
            # The Daily Operations board is the execution path for air missions.
            # A held mission stays on the board but may not depart, and a
            # dependent mission may not record its actual departure until its
            # predecessor has released it.
            from apps.ops import approvals

            if mission.is_held:
                raise ValidationError(
                    {
                        "detail": (
                            "This mission is on hold. Release the hold before "
                            "recording a takeoff."
                        )
                    }
                )
            if mission.depends_on_id:
                dependency = approvals.dependency_state(mission)
                if dependency.get("enabled") and not dependency.get("satisfied"):
                    raise ValidationError(
                        {
                            "detail": (
                                dependency.get("label")
                                or "The predecessor mission has not released this mission."
                            )
                        }
                    )
            formation = approvals.formation_blockers(mission)
            if formation:
                raise ValidationError(
                    {
                        "detail": " ".join(
                            blocker.get("detail", "") for blocker in formation
                        ),
                        "blockers": formation,
                    }
                )
        if (wants_tot or wants_toot) and not itinerary.overflight:
            raise ValidationError(
                {"detail": "TOT and TOOT apply only to overflight itineraries."}
            )
        if itinerary.overflight and wants_landing:
            raise ValidationError(
                {"detail": "Overflight itineraries do not record ATA."}
            )
        if origin_is_over and wants_takeoff:
            raise ValidationError(
                {
                    "detail": (
                        "No ATD is recorded after an overflight leg; "
                        "record the next arrival on the following landing."
                    )
                }
            )
        if wants_se and origin_is_over:
            raise ValidationError(
                {
                    "detail": (
                        "No engine start is recorded after an overflight leg; "
                        "the aircraft never shut down at the target."
                    )
                }
            )
        if wants_sd and itinerary.overflight:
            raise ValidationError(
                {
                    "detail": (
                        "An overflight leg records TOT, not a shutdown; the "
                        "aircraft flies on without landing."
                    )
                }
            )
        if wants_se and itinerary.ata:
            raise ValidationError(
                {"se": "SE must be recorded before the leg lands."}
            )
        if wants_sd and not itinerary.ata:
            raise ValidationError({"sd": "Record ATA before SD."})
        if wants_takeoff and previous and not previous.ata:
            raise ValidationError(
                {"atd": "Record ATA on the previous leg before ATD."}
            )
        if wants_landing and origin_is_over and not previous.toot:
            raise ValidationError(
                {
                    "ata": (
                        "Record TOOT on the previous overflight leg "
                        "before ATA."
                    )
                }
            )
        if wants_tot and not (
            itinerary.atd or (origin_is_over and previous.toot)
        ):
            raise ValidationError(
                {
                    "tot": (
                        "Record ATD, or TOOT on the previous overflight leg, "
                        "before TOT."
                    )
                }
            )
        if (
            wants_landing
            and not itinerary.overflight
            and not (itinerary.atd or origin_is_over)
        ):
            raise ValidationError(
                {"ata": "Record ATD before ATA."}
            )

        def _parse_time(value, label):
            if value in (None, ""):
                return None
            parsed = parse_datetime(value)
            if parsed is None:
                raise ValidationError(
                    {label: "Enter a valid date and time."}
                )
            return timeutils.to_manila(parsed)

        if event == "takeoff" or "atd" in request.data:
            itinerary.atd = _parse_time(
                request.data.get("atd", request.data.get("at")), "atd"
            ) or _now()
        if event == "landing" or "ata" in request.data:
            itinerary.ata = _parse_time(
                request.data.get("ata", request.data.get("at")), "ata"
            ) or _now()
        if event == "tot" or "tot" in request.data:
            itinerary.tot = _parse_time(
                request.data.get("tot", request.data.get("at")), "tot"
            ) or _now()
        if event == "toot" or "toot" in request.data:
            itinerary.toot = _parse_time(
                request.data.get("toot", request.data.get("at")), "toot"
            ) or _now()
        if wants_se:
            itinerary.se = _parse_time(
                request.data.get("se", request.data.get("at")), "se"
            ) or _now()
        if wants_sd:
            itinerary.sd = _parse_time(
                request.data.get("sd", request.data.get("at")), "sd"
            ) or _now()
        if event == "cancel":
            itinerary.status = AirMissionItinerary.STATUS_CANCELLED
        elif event == "divert":
            itinerary.status = AirMissionItinerary.STATUS_DIVERTED
        elif itinerary.ata:
            itinerary.status = AirMissionItinerary.STATUS_LANDED
        elif itinerary.atd or itinerary.tot or itinerary.toot:
            itinerary.status = AirMissionItinerary.STATUS_AIRBORNE
        else:
            itinerary.status = AirMissionItinerary.STATUS_PLANNED

        if itinerary.atd and itinerary.ata and itinerary.ata < itinerary.atd:
            raise ValidationError(
                {"ata": "ATA must not precede ATD."}
            )
        if itinerary.se and itinerary.atd and itinerary.se > itinerary.atd:
            raise ValidationError({"se": "SE must not follow ATD."})
        if itinerary.sd and itinerary.ata and itinerary.sd < itinerary.ata:
            raise ValidationError({"sd": "SD must not precede ATA."})
        if itinerary.toot and not itinerary.tot:
            raise ValidationError(
                {"toot": "Record TOT before TOOT."}
            )
        if itinerary.atd and itinerary.tot and itinerary.tot < itinerary.atd:
            raise ValidationError(
                {"tot": "TOT must not precede ATD."}
            )
        if itinerary.tot and itinerary.toot and itinerary.toot < itinerary.tot:
            raise ValidationError(
                {"toot": "TOOT must not precede TOT."}
            )
        itinerary.save(
            update_fields=["atd", "ata", "tot", "toot", "se", "sd", "status"]
        )

        # The board is the air execution path: the first ATD starts the mission,
        # and the final landing closes it. No separate dispatch is required.
        status_fields = []
        if itinerary.atd and mission.status not in {"completed", "cancelled"}:
            if mission.status != "active":
                mission.status = "active"
                status_fields.append("status")
            if mission.dispatched_at is None:
                mission.dispatched_at = itinerary.atd
                status_fields.append("dispatched_at")
            if status_fields:
                mission.save(update_fields=[*status_fields, "updated_at"])

        detail = itinerary.aircraft.element.air_detail
        final_at = _air_mission_final_time(detail)
        relocated = []
        if final_at is not None:
            # Only the last leg of every aircraft closes the mission. A landing
            # at an intermediate stop is not the mission's arrival, so it must
            # never reach ``actual_arrival``: dependents read that field as "the
            # predecessor is on the ground at its destination", and the map
            # reads it as "the whole itinerary has been flown".
            detail.actual_arrival = final_at
            detail.rendezvous_ready_at = final_at
            detail.save(update_fields=["actual_arrival", "rendezvous_ready_at"])

        if final_at is not None and mission.status not in {"completed", "cancelled"}:
            relocated = _relocate_air_assets(detail)
            mission.status = "completed"
            mission.completed_at = final_at
            mission.save(update_fields=["status", "completed_at", "updated_at"])

        # The response must show the freshly recorded times, not the prefetched
        # related rows from when the permission-scoped mission was loaded.
        mission.refresh_from_db()

        log_action(
            actor=request.user,
            action="air_itinerary_actual",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after={
                "itinerary": itinerary.pk,
                "atd": itinerary.atd.isoformat() if itinerary.atd else None,
                "ata": itinerary.ata.isoformat() if itinerary.ata else None,
                "tot": itinerary.tot.isoformat() if itinerary.tot else None,
                "toot": itinerary.toot.isoformat() if itinerary.toot else None,
                "se": itinerary.se.isoformat() if itinerary.se else None,
                "sd": itinerary.sd.isoformat() if itinerary.sd else None,
                "status": itinerary.status,
                "mission_status": mission.status,
                "mission_completed_at": (
                    mission.completed_at.isoformat()
                    if mission.completed_at
                    else None
                ),
                "relocated_assets": relocated,
            },
            request=request,
        )
        return Response(
            serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
        )

    @action(detail=False, methods=["get"], url_path="air-simulation")
    def air_simulation(self, request):
        """Read-only, time-based aircraft map for the Air Operations page.

        The response is deliberately derived from the saved flight plan and the
        server clock. It never writes a position, ATD/ATA or mission status, so
        refreshing the browser cannot reset a flight and every authorized user
        sees the same state for the same instant.
        """
        from django.utils.dateparse import parse_date

        from apps.core import timeutils
        from apps.core.scoping import accessible_unit_ids
        from apps.ops import air_simulation

        operation_date = parse_date(request.query_params.get("date") or "")
        if operation_date is None:
            operation_date = timeutils.today()

        missions = list(
            self.get_queryset()
            .filter(category=Mission.CATEGORY_AIR)
            .exclude(status="draft")
            .filter(
                Q(air_detail__operation_date=operation_date)
                | Q(
                    air_detail__operation_date__isnull=True,
                    start_at__date=operation_date,
                )
                # An aircraft already airborne from an earlier sheet remains
                # visible until its simulated arrival; it does not disappear at
                # midnight.
                | Q(status="active")
            )
            .select_related("depends_on", "depends_on__air_detail")
            .order_by("start_at", "id")
        )
        snapshot = air_simulation.snapshot(
            missions,
            allowed_unit_ids=accessible_unit_ids(request.user),
        )
        snapshot["date"] = operation_date.isoformat()
        response = Response(snapshot)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    @action(detail=False, methods=["get"], url_path="air-operations")
    def air_operations(self, request):
        """Daily Flight Operations schedule or actual sheet as a PDF."""
        from django.utils.dateparse import parse_date
        from apps.ops import flight_operations_pdf

        operation_date = parse_date(request.query_params.get("date") or "")
        if operation_date is None:
            raise ValidationError({"date": "Choose the Flight Operations date."})
        mode = (request.query_params.get("mode") or "schedule").lower()
        if mode not in {"schedule", "actual"}:
            raise ValidationError({"mode": "Use schedule or actual."})

        missions = list(
            self.get_queryset()
            .filter(category=Mission.CATEGORY_AIR)
            .filter(
                Q(air_detail__operation_date=operation_date)
                | Q(
                    air_detail__operation_date__isnull=True,
                    start_at__date=operation_date,
                )
            )
            .select_related("unit", "air_detail")
            .prefetch_related(
                "air_detail__elements__aircraft__crew",
                "air_detail__elements__aircraft__itineraries",
            )
            .order_by("air_detail__elements__sequence", "start_at", "id")
        )
        content = flight_operations_pdf.generate_flight_operations_pdf(
            missions, operation_date=operation_date, mode=mode
        )
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="FLIGHT_OPERATIONS_{operation_date:%Y%m%d}.pdf"'
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    @action(detail=True, methods=["get"], url_path="tracking")
    def tracking(self, request, pk=None):
        """Planned route plus the actual movement log, scoped by role."""
        from apps.ops import tracking as tracking_service

        return Response(tracking_service.snapshot(self.get_object(), request.user))

    @action(detail=True, methods=["post"], url_path="tracking/start")
    def tracking_start(self, request, pk=None):
        """Create (or refresh) one runnable movement record per vehicle."""
        from apps.ops import tracking as tracking_service

        mission = self.get_object()
        if not tracking_service.can_manage(request.user, mission):
            raise PermissionDenied(
                "Only the Operations Center may start movement tracking."
            )
        raw_lead = request.data.get("lead_run")
        try:
            lead_run_id = int(raw_lead) if raw_lead not in (None, "") else None
        except (TypeError, ValueError):
            raise ValidationError({"lead_run": "Lead run must be numeric."}) from None
        tracking_service.start_for_mission(
            mission, request.user, lead_run_id=lead_run_id
        )
        log_action(
            actor=request.user,
            action="tracking_start",
            entity_type="mission",
            entity_id=mission.pk,
            after={"runs": mission.vehicle_runs.count()},
            request=request,
        )
        return Response(tracking_service.snapshot(mission, request.user))

    @action(detail=True, methods=["post"], url_path="tracking/lead")
    def tracking_lead(self, request, pk=None):
        """Designate the one vehicle whose driver coordinates the convoy."""
        from apps.ops import tracking as tracking_service
        from apps.ops.models import VehicleRun

        mission = self.get_object()
        if not tracking_service.can_manage(request.user, mission):
            raise PermissionDenied(
                "Only the Operations Center may designate the convoy lead."
            )
        raw_run = request.data.get("run")
        run = (
            VehicleRun.objects.filter(mission=mission, pk=raw_run).first()
            if raw_run not in (None, "")
            else None
        )
        if run is None:
            raise ValidationError({"run": "Choose a vehicle run from this mission."})
        tracking_service.set_lead(mission, run, request.user)
        log_action(
            actor=request.user,
            action="tracking_lead",
            entity_type="mission",
            entity_id=mission.pk,
            after={"run": run.id, "vehicle": run.vehicle.registration_number},
            request=request,
        )
        return Response(tracking_service.snapshot(mission, request.user))

    @action(detail=True, methods=["post"], url_path="tracking/event")
    def tracking_event(self, request, pk=None):
        """Record an actual departure, stop arrival/departure or completion."""
        from apps.ops import tracking as tracking_service
        from apps.ops.models import TripItinerary, VehicleRun

        mission = self.get_object()
        raw_run = request.data.get("run")
        run = (
            VehicleRun.objects.filter(mission=mission, pk=raw_run).first()
            if raw_run not in (None, "")
            else None
        )
        if run is None:
            raise ValidationError({"run": "Choose a vehicle from this mission."})
        if not tracking_service.can_act(request.user, run):
            raise PermissionDenied(
                "Only this vehicle's driver, the convoy lead or the Operations "
                "Center may record its movement."
            )

        kind = (request.data.get("action") or "").strip()
        at = request.data.get("at")
        note = request.data.get("note", "")
        if kind == "depart":
            tracking_service.depart(run, request.user, at=at)
        elif kind in {"arrive_stop", "depart_stop"}:
            raw_stop = request.data.get("stop")
            stop = (
                TripItinerary.objects.filter(trip_id=run.trip_id, pk=raw_stop).first()
                if raw_stop not in (None, "")
                else None
            )
            if stop is None:
                raise ValidationError(
                    {"stop": "Choose a stop from this vehicle's itinerary."}
                )
            if kind == "arrive_stop":
                tracking_service.arrive_stop(
                    run, stop, request.user, at=at, force=bool(request.data.get("force"))
                )
            else:
                tracking_service.depart_stop(run, stop, request.user, at=at)
        elif kind == "complete":
            tracking_service.complete_run(run, request.user, at=at, note=note)
        else:
            raise ValidationError(
                {"action": "Use depart, arrive_stop, depart_stop or complete."}
            )

        log_action(
            actor=request.user,
            action=f"tracking_{kind}",
            entity_type="vehicle_run",
            entity_id=run.id,
            after={"run": run.id, "mission": mission.id},
            request=request,
        )
        return Response(tracking_service.snapshot(mission, request.user))

    @action(detail=True, methods=["post"], url_path="tracking/ping")
    def tracking_ping(self, request, pk=None):
        """Accept a batch of GPS breadcrumbs from the assigned driver's phone."""
        from apps.ops import tracking as tracking_service
        from apps.ops.models import VehicleRun

        mission = self.get_object()
        raw_run = request.data.get("run")
        run = (
            VehicleRun.objects.filter(mission=mission, pk=raw_run).first()
            if raw_run not in (None, "")
            else None
        )
        if run is None:
            raise ValidationError({"run": "Choose a vehicle from this mission."})
        if not tracking_service.can_ping(request.user, run):
            raise PermissionDenied(
                "Only the assigned driver may report this vehicle's position."
            )
        result = tracking_service.record_pings(
            run, request.user, request.data.get("pings")
        )
        return Response(
            {
                "accepted": result["accepted"],
                "rejected": result["rejected"],
                "run": run.id,
                "last_ping_at": run.last_ping_at,
            }
        )

    @action(detail=True, methods=["post"], url_path="tracking/emergency")
    def tracking_emergency(self, request, pk=None):
        """A driver's SOS: logged with the movement and alerted to the board."""
        from apps.ops import tracking as tracking_service
        from apps.ops.models import VehicleRun

        mission = self.get_object()
        raw_run = request.data.get("run")
        run = (
            VehicleRun.objects.filter(mission=mission, pk=raw_run).first()
            if raw_run not in (None, "")
            else None
        )
        if run is None:
            raise ValidationError({"run": "Choose a vehicle from this mission."})
        if not tracking_service.can_act(request.user, run):
            raise PermissionDenied(
                "Only this vehicle's driver, the convoy lead or the Operations "
                "Center may report an emergency."
            )

        rows = tracking_service.send_emergency(
            mission, run, request.user, note=request.data.get("note", "")
        )
        log_action(
            actor=request.user,
            action="tracking_emergency",
            entity_type="vehicle_run",
            entity_id=run.id,
            after={"run": run.id, "mission": mission.id, "notified": len(rows)},
            request=request,
        )
        return Response({"run": run.id, "notified": len(rows)})

    @action(detail=True, methods=["post"], url_path="tracking/abort")
    def tracking_abort(self, request, pk=None):
        """Abort a mission in progress and pin where its vehicles end.

        Only the Operations Center orders an abort, and only while the mission
        is running. The drivers are notified at once with the pinned point; the
        mission keeps being watched until the Operations Center closes it.
        """
        from apps.ops import tracking as tracking_service

        mission = self.get_object()
        if not tracking_service.can_manage(request.user, mission):
            raise PermissionDenied("Only the Operations Center may abort a mission.")
        rows = tracking_service.abort_mission(
            mission,
            request.user,
            destination=request.data.get("destination"),
            reason=request.data.get("reason", ""),
        )
        log_action(
            actor=request.user,
            action="tracking_abort",
            entity_type="mission",
            entity_id=mission.pk,
            after={
                "destination": mission.abort_destination,
                "target": mission.abort_target_name,
                "notified": len(rows),
            },
            request=request,
        )
        return Response(tracking_service.snapshot(mission, request.user))

    @action(detail=True, methods=["post"], url_path="tracking/complete")
    def tracking_complete(self, request, pk=None):
        """Close the mission once every vehicle run is closed (or by override)."""
        from apps.ops import tracking as tracking_service

        mission = self.get_object()
        if not tracking_service.can_manage(request.user, mission):
            raise PermissionDenied("Only the Operations Center may close a mission.")
        before = snapshot(mission, self.audit_fields)
        tracking_service.complete_mission(
            mission,
            request.user,
            force=bool(request.data.get("force")),
            note=request.data.get("note", ""),
        )
        log_action(
            actor=request.user,
            action="tracking_complete",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after=snapshot(mission, self.audit_fields),
            request=request,
        )
        return Response(tracking_service.snapshot(mission, request.user))

    @action(detail=True, methods=["get"], url_path="tracking/log")
    def tracking_log(self, request, pk=None):
        """The movement log as a PDF: ETD/ATD, every stop, who recorded it."""
        from apps.ops import movement_pdf
        from apps.ops import tracking as tracking_service

        mission = self.get_object()  # unit-scoped queryset
        is_driver = mission.vehicle_runs.filter(driver=request.user).exists()
        if not movement_pdf.can_view_movement_log(
            request.user, mission, is_driver=is_driver
        ):
            raise PermissionDenied("You are not allowed to view this movement log.")

        content = movement_pdf.generate_movement_log_pdf(mission)
        download = str(request.query_params.get("download", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        response = HttpResponse(content, content_type="application/pdf")
        disposition = "attachment" if download else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="MOVEMENT_{mission.mission_number}.pdf"'
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        tracking_service.alert_delays(mission, request.user)
        return response

    @action(detail=True, methods=["get"], url_path="mission-order")
    def mission_order(self, request, pk=None):
        """The formal Mission Order for this mission, generated from its record."""
        from apps.ops import mission_order_pdf

        mission = self.get_object()  # unit-scoped queryset
        if mission.category == Mission.CATEGORY_AIR:
            raise ValidationError({"detail": "Mission Orders apply to ground operations only."})
        token = mission_order_pdf.verification_token(mission)
        verification_url = request.build_absolute_uri(
            reverse("mission-order-verify", kwargs={"token": token})
        )
        content = mission_order_pdf.generate_mission_order_pdf(
            mission, verification_url=verification_url
        )
        download = str(request.query_params.get("download", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        response = HttpResponse(content, content_type="application/pdf")
        disposition = "attachment" if download else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="MISSION_ORDER_{mission.mission_number}.pdf"'
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    @action(detail=True, methods=["get"], url_path="brief")
    def brief(self, request, pk=None):
        """One consolidated PDF of the mission, its approvals and its order.

        Reviewers otherwise read the detail page section by section. The brief
        is generated from the same records, so it always matches the page.
        """
        from apps.ops import mission_brief_pdf

        mission = self.get_object()  # unit-scoped queryset
        content = mission_brief_pdf.generate_mission_brief_pdf(
            mission, prepared_by=request.user
        )
        download = str(request.query_params.get("download", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        response = HttpResponse(content, content_type="application/pdf")
        disposition = "attachment" if download else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="MISSION_BRIEF_{mission.mission_number}.pdf"'
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    @action(detail=True, methods=["get", "post"], url_path="vehicles")
    def vehicles(self, request, pk=None):
        """Vehicles planned for this mission, one route per vehicle (spec §5).

        GET  - the current assignment, with each vehicle's itinerary and route.
        POST - replace the assignment. ``{"vehicles": [id, ...]}``; each vehicle
               keeps its own trip, so its itinerary and road route stay its own.
        """
        mission = self.get_object()
        if request.method == "GET":
            payload = serializers.MissionSerializer(
                mission, context=self.get_serializer_context()
            ).data
            return Response(
                {
                    "mission": mission.id,
                    "category": mission.category,
                    "vehicles": payload["vehicles"],
                }
            )

        if not can(request.user, roles.CAP_MANAGE_MISSIONS, mission.unit_id):
            raise PermissionDenied(
                "Only the Operations Center may assign vehicles to a mission."
            )
        if mission.category != Mission.CATEGORY_GROUND:
            raise ValidationError(
                {"detail": "Vehicles are planned for Ground Operations missions."}
            )

        ids = request.data.get("vehicles")
        if not isinstance(ids, list):
            raise ValidationError({"vehicles": "Expected a list of vehicle ids."})
        try:
            ids = [int(value) for value in ids]
        except (TypeError, ValueError):
            raise ValidationError({"vehicles": "Vehicle ids must be numbers."}) from None

        # The form designates one driver per vehicle before adding it. If a
        # caller sends drivers at all, every selected vehicle must have exactly
        # one, that person must already be on this mission's roster, and nobody
        # may be named for two vehicles at once.
        raw_drivers = request.data.get("drivers")
        drivers: dict[int, int] = {}
        if raw_drivers is None and mission.personnel.filter(is_active=True).exists():
            raise ValidationError(
                {
                    "drivers": (
                        "Designate one driver for every selected vehicle before "
                        "assigning vehicles."
                    )
                }
            )
        if raw_drivers is not None:
            if not isinstance(raw_drivers, dict):
                raise ValidationError(
                    {"drivers": "Expected an object mapping vehicle ids to user ids."}
                )
            try:
                drivers = {
                    int(vehicle_id): int(user_id)
                    for vehicle_id, user_id in raw_drivers.items()
                }
            except (TypeError, ValueError):
                raise ValidationError(
                    {"drivers": "Vehicle and driver ids must be numbers."}
                ) from None

            missing = sorted(set(ids) - set(drivers))
            if missing:
                raise ValidationError(
                    {
                        "drivers": (
                            "Designate a driver for every selected vehicle. "
                            f"Missing: {missing}."
                        )
                    }
                )
            extra = sorted(set(drivers) - set(ids))
            if extra:
                raise ValidationError(
                    {"drivers": f"Driver given for unselected vehicle(s): {extra}."}
                )
            if len(set(drivers.values())) != len(drivers):
                raise ValidationError(
                    {
                        "drivers": (
                            "A driver cannot operate more than one vehicle on the "
                            "same trip."
                        )
                    }
                )
            active_drivers = set(
                User.objects.filter(
                    pk__in=drivers.values(), is_active=True
                ).values_list("id", flat=True)
            )
            unknown = sorted(set(drivers.values()) - active_drivers)
            if unknown:
                raise ValidationError(
                    {"drivers": f"Unknown or inactive driver ids: {unknown}."}
                )
            roster = set(
                mission.personnel.filter(is_active=True).values_list(
                    "user_id", flat=True
                )
            )
            not_assigned = sorted(set(drivers.values()) - roster)
            if not_assigned:
                raise ValidationError(
                    {
                        "drivers": (
                            "A driver must be assigned to this mission's personnel "
                            f"first. Not assigned: {not_assigned}."
                        )
                    }
                )

        # Privately owned vehicles: the Operations Center describes the vehicle
        # itself, because it is not in the register. The owner and the driver are
        # people already on this mission's roster, and the owner drives by
        # default unless somebody else is named.
        raw_povs = request.data.get("povs") or []
        if not isinstance(raw_povs, list):
            raise ValidationError(
                {"povs": "Expected a list of privately owned vehicles."}
            )
        roster = set(
            mission.personnel.filter(is_active=True).values_list("user_id", flat=True)
        )
        pov_specs: list[dict] = []
        for index, entry in enumerate(raw_povs, start=1):
            if not isinstance(entry, dict):
                raise ValidationError({"povs": f"POV {index} must be an object."})
            vehicle_type = str(entry.get("vehicle_type") or "").strip()
            make_model = str(entry.get("make_model") or "").strip()
            plate_number = str(entry.get("plate_number") or "").strip()
            fuel_type = str(entry.get("fuel_type") or "").strip()
            if not vehicle_type:
                raise ValidationError(
                    {"povs": f"POV {index} needs a vehicle type."}
                )
            if not make_model:
                raise ValidationError({"povs": f"POV {index} needs a model."})
            try:
                owner_id = int(entry.get("owner"))
            except (TypeError, ValueError):
                raise ValidationError({"povs": f"POV {index} needs an owner."}) from None
            if owner_id not in roster:
                raise ValidationError(
                    {
                        "povs": (
                            f"The owner of POV {index} is not assigned to this "
                            "mission's personnel."
                        )
                    }
                )
            try:
                pov_driver = int(entry.get("driver") or owner_id)
            except (TypeError, ValueError):
                raise ValidationError({"povs": f"POV {index} needs a driver."}) from None
            if pov_driver not in roster:
                raise ValidationError(
                    {
                        "povs": (
                            f"The driver of POV {index} is not assigned to this "
                            "mission's personnel."
                        )
                    }
                )
            pov_specs.append(
                {
                    "client_ref": str(entry.get("client_ref") or f"pov:{index}"),
                    "vehicle_type": vehicle_type[:80],
                    "make_model": make_model[:120],
                    "plate_number": plate_number[:60],
                    "fuel_type": fuel_type[:40],
                    "owner": owner_id,
                    "driver": pov_driver,
                }
            )

        # One person cannot drive two vehicles on the same trip, whether those
        # vehicles come from the register or are POVs.
        every_driver = list(drivers.values()) + [spec["driver"] for spec in pov_specs]
        if len(set(every_driver)) != len(every_driver):
            raise ValidationError(
                {
                    "drivers": (
                        "A driver cannot operate more than one vehicle on the "
                        "same trip."
                    )
                }
            )

        scope = unit_ids_for_filter(request.user)
        vehicles = list(
            Vehicle.objects.select_related("unit").filter(
                id__in=ids, is_active=True
            )
        )
        missing = sorted(set(ids) - {vehicle.id for vehicle in vehicles})
        if missing:
            raise ValidationError(
                {"vehicles": f"Unknown or inactive vehicle(s): {missing}."}
            )
        if scope is not None:
            for vehicle in vehicles:
                if vehicle.unit_id and vehicle.unit_id not in scope:
                    raise ValidationError(
                        {
                            "vehicles": (
                                f"{vehicle.registration_number} belongs to a unit "
                                "outside your authorised scope."
                            )
                        }
                    )
        for vehicle in vehicles:
            if vehicle.status in {"maintenance", "unserviceable"}:
                raise ValidationError(
                    {
                        "vehicles": (
                            f"{vehicle.registration_number} is {vehicle.get_status_display().lower()} "
                            "and cannot be assigned."
                        )
                    }
                )
            conflict = _vehicle_conflict(vehicle, mission)
            if conflict is not None:
                raise ValidationError(
                    {
                        "vehicles": (
                            f"{vehicle.registration_number} is already committed to "
                            f"{conflict.trip_number} for mission "
                            f"{conflict.mission.mission_number if conflict.mission_id else 'another activity'}."
                        )
                    }
                )

        # Re-saving the plan must not mint a second POV row for the same car: an
        # owner, type, model and plate already on this mission is reused. A POV
        # recorded before plates were captured still matches on the first three.
        existing_povs = {
            (
                link.vehicle.owner_id,
                link.vehicle.vehicle_type.strip().lower(),
                link.vehicle.make_model.strip().lower(),
                (link.vehicle.registration_no or "").strip().lower(),
            ): link.vehicle
            for link in VehicleTripVehicle.objects.select_related("vehicle")
            .filter(trip__mission=mission, vehicle__is_pov=True)
            .exclude(trip__status="cancelled")
        }

        pov_pairs = []
        for spec in pov_specs:
            owner = User.objects.filter(pk=spec["owner"]).first()
            match = (
                spec["owner"],
                spec["vehicle_type"].strip().lower(),
                spec["make_model"].strip().lower(),
                spec["plate_number"].strip().lower(),
            )
            vehicle = existing_povs.get(match) or existing_povs.get(match[:3] + ("",))
            if vehicle is not None:
                fields = ["fuel_type", "registration_no", "updated_at"]
                vehicle.fuel_type = spec["fuel_type"]
                vehicle.registration_no = spec["plate_number"]
                # A POV entered before the plate was used as its identifier is
                # moved onto the plate the moment that plate is known.
                number = _pov_registration_number(
                    spec["plate_number"], vehicle.registration_number
                )
                if number != vehicle.registration_number:
                    vehicle.registration_number = number
                    fields.append("registration_number")
                vehicle.save(update_fields=fields)
                drivers[vehicle.id] = spec["driver"]
                pov_pairs.append((spec, vehicle))
                continue
            vehicle = Vehicle.objects.create(
                registration_number=_pov_registration_number(
                    spec["plate_number"]
                ),
                vehicle_type=spec["vehicle_type"],
                make_model=spec["make_model"],
                registration_no=spec["plate_number"],
                fuel_type=spec["fuel_type"],
                unit_id=mission.unit_id,
                is_pov=True,
                owner_id=spec["owner"],
                status="available",
                notes=(
                    f"Privately owned vehicle of {owner.full_name}" if owner else "Privately owned vehicle"
                ),
            )
            drivers[vehicle.id] = spec["driver"]
            vehicles.append(vehicle)
            pov_pairs.append((spec, vehicle))

        before_links = [
            (link.vehicle_id, link.driver_id)
            for trip in mission.trips.exclude(status="cancelled")
            for link in trip.vehicles.all()
        ]
        before = {
            "vehicles": [vehicle_id for vehicle_id, _driver_id in before_links],
            "drivers": {
                str(vehicle_id): driver_id for vehicle_id, driver_id in before_links
            },
        }
        _apply_mission_vehicles(mission, vehicles, request.user, drivers)
        log_action(
            actor=request.user,
            action="assign_mission_vehicles",
            entity_type="mission",
            entity_id=mission.pk,
            before=before,
            after={"vehicles": ids, "drivers": drivers},
            request=request,
        )
        mission.refresh_from_db()
        payload = serializers.MissionSerializer(
            mission, context=self.get_serializer_context()
        ).data
        # Hand the planner each POV's trip so its itinerary can be planned with
        # the same calls an inventory vehicle uses.
        pov_rows = []
        if pov_pairs:
            links = (
                VehicleTripVehicle.objects.filter(
                    vehicle_id__in=[vehicle.id for _spec, vehicle in pov_pairs],
                    trip__mission=mission,
                )
                .exclude(trip__status="cancelled")
                .values("vehicle_id", "trip_id")
            )
            trip_by_vehicle = {row["vehicle_id"]: row["trip_id"] for row in links}
            pov_rows = [
                {
                    "client_ref": spec["client_ref"],
                    "vehicle_id": vehicle.id,
                    "registration_number": vehicle.registration_number,
                    "plate_number": vehicle.registration_no,
                    "trip_id": trip_by_vehicle.get(vehicle.id),
                }
                for spec, vehicle in pov_pairs
            ]

        return Response(
            {
                "mission": mission.id,
                "category": mission.category,
                "vehicles": payload["vehicles"],
                "pov_vehicles": pov_rows,
            }
        )

    @action(detail=True, methods=["post"], url_path="assign-personnel")
    @transaction.atomic
    def assign_personnel(self, request, pk=None):
        """Replace the active personnel roster for a mission."""
        mission = self.get_object()
        entries = request.data.get("personnel", [])
        if not isinstance(entries, list):
            raise ValidationError({"personnel": "Expected a list of assignments."})

        for entry in entries:
            role = (entry.get("role") or "Member").strip()
            custom = (entry.get("custom_role") or "").strip()
            if role == "Specify" and not custom:
                raise ValidationError(
                    {"personnel": "Enter the custom designation when Specify is selected."}
                )
            if role != "Specify":
                custom = ""
            entry["role"] = role
            entry["custom_role"] = custom

        user_ids = [entry.get("user_id") for entry in entries]
        valid_ids = set(
            User.objects.filter(pk__in=[uid for uid in user_ids if uid], is_active=True)
            .values_list("id", flat=True)
        )
        unknown = [uid for uid in user_ids if uid not in valid_ids]
        if unknown:
            raise ValidationError({"personnel": f"Unknown or inactive user ids: {unknown}."})

        mission.personnel.update(is_active=False)
        for entry in entries:
            assignment, _created = MissionPersonnel.objects.update_or_create(
                mission=mission,
                user_id=entry["user_id"],
                defaults={
                    "role": entry.get("role", "Member"),
                    "custom_role": entry.get("custom_role", ""),
                    "is_active": True,
                },
            )
            assignment.save()

        log_action(
            actor=request.user,
            action="assign_personnel",
            entity_type="mission",
            entity_id=mission.pk,
            after={"personnel": entries},
            request=request,
        )
        return Response(serializers.MissionSerializer(mission).data)

    @action(detail=True, methods=["get", "post"])
    def risk(self, request, pk=None):
        """Link a mission to an MRM assessment and assign its evaluator."""
        mission = self.get_object()
        assessment = getattr(mission, "risk_assessment", None)

        if request.method == "GET":
            if assessment is None:
                return Response({"risk_assessment": None})
            return Response(serializers.RiskAssessmentSerializer(assessment).data)

        mrm_form_id = request.data.get("mrm_form_id")
        mrm_form = None
        if mrm_form_id:
            mrm_form = MRMForm.objects.filter(pk=mrm_form_id).first()
            if mrm_form is None:
                raise ValidationError({"mrm_form_id": "MRM form not found."})
            # One MRM governs one mission. Linking the same assessment to a
            # second mission would make approvals ambiguous.
            existing_link = (
                RiskAssessment.objects.filter(mrm_form=mrm_form)
                .exclude(pk=assessment.pk if assessment else None)
                .select_related("mission")
                .first()
            )
            if existing_link:
                raise ValidationError(
                    {
                        "mrm_form_id": (
                            f"MRM {mrm_form.mrm_number} is already linked to mission "
                            f"{existing_link.mission.mission_number}."
                        )
                    }
                )

        evaluator = None
        if request.data.get("evaluator_id") not in (None, ""):
            try:
                evaluator = services.resolve_evaluator(
                    request.data["evaluator_id"], request.user
                )
            except services.EvaluatorError as exc:
                raise ValidationError({"evaluator_id": str(exc)}) from exc

        if assessment is None:
            assessment = RiskAssessment(mission=mission)
        if mrm_form is not None:
            assessment.mrm_form = mrm_form
        if evaluator is not None:
            assessment.evaluator = evaluator
            # The legacy form is the record the evaluator actually reviews, so
            # the assignment must land there too. Without this the MRM could
            # never be submitted (submit checks mrm_forms.reviewed_by).
            if assessment.mrm_form_id:
                form = assessment.mrm_form
                form.reviewed_by = evaluator
                form.reviewer_name = evaluator.full_name
                if not form.original_reviewer_unit_id:
                    form.original_reviewer_unit = evaluator.unit
                form.save(
                    update_fields=[
                        "reviewed_by",
                        "reviewer_name",
                        "original_reviewer_unit",
                    ]
                )

        if assessment.mrm_form_id:
            _apply_risk_calculation(assessment)
        assessment.notes = request.data.get("notes", assessment.notes)
        assessment.save()

        # Keep the mission's own MRM pointer in step so mission lists, reports
        # and the PDF workflow all see the same record.
        if assessment.mrm_form_id and mission.risk_form_id != assessment.mrm_form_id:
            mission.risk_form_id = assessment.mrm_form_id
            mission.save(update_fields=["risk_form", "updated_at"])

        log_action(
            actor=request.user,
            action="assign_risk",
            entity_type="risk_assessment",
            entity_id=assessment.pk,
            after=snapshot(assessment, ["mission_id", "mrm_form_id", "evaluator_id", "status"]),
            request=request,
        )
        if evaluator is not None and assessment.mrm_form_id:
            notify(
                evaluator,
                title=f"MRM {assessment.mrm_form.mrm_number} assigned to you",
                message=f"Mission {mission.mission_number} · {mission.title}",
                category="risk",
                priority="high",
                related=("mrm_form", assessment.mrm_form_id),
                actor=request.user,
                requires_action=False,
            )
        return Response(serializers.RiskAssessmentSerializer(assessment).data)


    @action(detail=True, methods=["post"], url_path="start-mrm")
    def start_mrm(self, request, pk=None):
        """Create and link a draft MRM for a mission.

        The legacy ``mrm_forms`` row is created through the same numbering rule the
        original application used; nothing about existing assessments is rewritten.
        A second attempt returns the existing assessment instead of duplicating it.
        """
        from django.db import IntegrityError

        mission = self.get_object()
        if mission.category == Mission.CATEGORY_AIR:
            raise ValidationError({"detail": "Air operations do not carry an MRM."})
        team = list(mission.mrm_team.filter(is_active=True).select_related("user"))
        if not team:
            raise ValidationError(
                {"detail": "Assign at least one MRM team member before starting the assessment."}
            )
        # The Operations Center names the lead; only that person creates the
        # assessment. Other team members and the planners observe it.
        lead_row = next(
            (
                row
                for row in team
                if row.user_id == request.user.id and row.role == "lead"
            ),
            None,
        )
        if lead_row is None:
            raise PermissionDenied(
                "Only the MRM lead assigned to this mission can start its assessment."
            )
        assessment = getattr(mission, "risk_assessment", None)
        if assessment is not None and assessment.mrm_form_id:
            raise ValidationError(
                {
                    "detail": (
                        f"This mission already has MRM {assessment.mrm_form.mrm_number}."
                    )
                }
            )

        lead = lead_row.user
        # The Operations Center names the evaluator on the mission's MRM team;
        # the assessment starts with that evaluator already in place, so nobody
        # has to pick one again on the risk page.
        reviewer = next((row.user for row in team if row.role == "reviewer"), None)
        form = None
        for _attempt in range(5):
            try:
                with transaction.atomic():
                    form = MRMForm.objects.create(
                        mrm_number=services.next_mrm_number(),
                        activity_mission=mission.title[:255],
                        created_by=lead,
                        creator_name=lead.full_name,
                        safety_objectives="",
                        mission_statement=mission.purpose or "",
                        aircraft_vehicle=(
                            (mission.air_detail.aircraft_type if mission.category == "air" else "")
                            if hasattr(mission, "air_detail")
                            else ""
                        ),
                        environment=mission.location_name or "",
                        original_unit=mission.unit,
                        original_unit_name=mission.unit.name if mission.unit_id else "",
                        original_creator_unit=lead.unit,
                        original_creator_unit_name=lead.unit.name if lead.unit_id else "",
                        reviewed_by=reviewer,
                        reviewer_name=reviewer.full_name if reviewer else "",
                        original_reviewer_unit=reviewer.unit if reviewer else None,
                        original_reviewer_unit_name=(
                            reviewer.unit.name if reviewer and reviewer.unit_id else ""
                        ),
                        date_created=_now(),
                        status="draft",
                        review_status="pending",
                        creator_signed=False,
                    )
                break
            except IntegrityError:
                continue
        if form is None:
            raise ValidationError({"detail": "Could not allocate an MRM number. Try again."})

        assessment = assessment or RiskAssessment(mission=mission)
        assessment.mrm_form = form
        assessment.evaluator = reviewer
        assessment.status = "draft"
        assessment.save()

        mission.risk_form = form
        mission.save(update_fields=["risk_form", "updated_at"])

        log_action(
            actor=request.user,
            action="start_mrm",
            entity_type="mission",
            entity_id=mission.id,
            after={"mrm_form_id": form.id, "mrm_number": form.mrm_number},
            request=request,
        )
        log_action(
            actor=request.user,
            action="create",
            entity_type="mrm_form",
            entity_id=form.id,
            after={"mission_id": mission.id, "mrm_number": form.mrm_number},
            request=request,
        )
        notify(
            lead,
            title=f"MRM {form.mrm_number} started for {mission.mission_number}",
            message="Complete the assessment and submit it for evaluation.",
            category="risk",
            priority="high",
            related=("mrm_form", form.id),
            actor=request.user,
            requires_action=True,
            action_label="Complete and submit the MRM",
            action_url=f"/risk/{form.id}",
        )
        return Response(serializers.RiskAssessmentSerializer(assessment).data)


def _notify_mrm_team(mission, actor, only_ids=None):
    """Ask the assigned MRM team to prepare the mission's assessment.

    The notification is an action item: it stays in the bell until the MRM is
    submitted, which is what ``complete_action`` records later.
    """
    rows = mission.mrm_team.filter(is_active=True).select_related("user")
    if only_ids is not None:
        rows = rows.filter(user_id__in=only_ids)
    team = [row.user for row in rows if row.user_id]
    if not team:
        return []

    category_label = "Air" if mission.category == Mission.CATEGORY_AIR else "Ground"
    message = (
        f"{category_label} operation · {mission.title}"
        + (f" · starts {mission.start_at:%Y-%m-%d %H:%M}" if mission.start_at else "")
    )
    created = []
    for person in team:
        created.append(
            notify(
                person,
                title=f"MRM required for {mission.mission_number}",
                message=message,
                category="risk",
                priority="high" if mission.start_at else "normal",
                related=("mission_mrm", mission.id),
                actor=actor,
                requires_action=True,
                action_label="Prepare the MRM assessment",
                action_url=f"/missions/{mission.id}#mrm",
                due_at=mission.start_at,
            )
        )
    return [row for row in created if row]


def _linked_mission_for_mrm(form):
    return (
        RiskAssessment.objects.filter(mrm_form_id=form.id)
        .select_related("mission")
        .first()
    )


def _has_signature(user) -> bool:
    return bool(user is not None and getattr(user, "signature_filename", ""))


def _signature_required(message: str):
    from rest_framework.exceptions import ValidationError

    return ValidationError({"detail": message, "code": "signature_required"})


def _decision_authority(form):
    from apps.ops import decision_authority

    return decision_authority.resolve(form)


def _route_decision_authority(form):
    """Record the signer this risk band routes to, and return the resolution."""
    from apps.ops import decision_authority

    return decision_authority.assign(form)


def _complete_authority_approval(form, actor, request=None) -> None:
    """Finish the decision: record the sign-off, close actions, notify."""
    form.authority_approved = True
    form.authority_approval_date = _now()
    form.authority_signature_date = _now()
    form.date_approved = _now()
    form.authority_name = actor.full_name
    if not form.authority_assigned_to_id:
        form.authority_assigned_to = actor
    form.save(
        update_fields=[
            "authority_approved",
            "authority_approval_date",
            "authority_signature_date",
            "date_approved",
            "authority_name",
            "authority_assigned_to",
        ]
    )
    log_action(
        actor=actor,
        action="approve",
        entity_type="mrm_form",
        entity_id=form.pk,
        after={"authority_approved": True},
        note="Approved as decision authority",
        request=request,
    )
    record_activity(
        user=actor,
        activity_type="approve_mrm",
        module="mrm",
        description=f"Approved MRM {form.mrm_number}",
        request=request,
        resource_id=form.id,
        resource_type="mrm_form",
    )
    complete_action(
        related_type="mrm_form",
        related_ids=[form.id],
        note=f"Approved by {actor.username}",
        actor=actor,
    )
    if form.created_by_id:
        notify(
            form.created_by,
            title=f"MRM {form.mrm_number} approved",
            message=form.activity_mission,
            level="success",
            category="risk",
            related=("mrm_form", form.id),
            actor=actor,
        )

    link = _linked_mission_for_mrm(form)
    if link:
        link.status = "approved"
        link.approved_by = actor
        link.approved_at = _now()
        link.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        complete_action(
            related_type="mission_mrm",
            related_ids=[link.mission_id],
            note=f"MRM {form.mrm_number} approved",
            actor=actor,
        )
        notify_many(
            [link.mission.created_by]
            + [
                row.user
                for row in link.mission.mrm_team.filter(is_active=True).select_related("user")
            ],
            title=f"MRM approved for mission {link.mission.mission_number}",
            message=link.mission.title,
            level="success",
            category="risk",
            related=("mission", link.mission_id),
            actor=actor,
        )


def _finalize_if_signatures_complete(form, actor):
    """Bands signed by the creator/evaluator close on the last signature.

    Below 66% the decision authority is the creator or the evaluator, so once the
    evaluator has approved the review and the creator has signed, the MRM is
    final - there is no further person to route it to.
    """
    from apps.ops import decision_authority

    authority = decision_authority.resolve(form)
    if authority.band not in (
        decision_authority.CREATOR,
        decision_authority.SUPERVISOR,
    ):
        return authority, False
    if form.authority_approved:
        return authority, False
    if form.review_status != "approved" or not form.evaluator_signed:
        return authority, False
    if not form.creator_signed:
        return authority, False
    _complete_authority_approval(form, authority.user or actor)
    return authority, True


def _notify_decision_authority(form, authority, actor):
    """Tell the designated signer that the assessment is ready for them."""
    from apps.ops.models import Notification

    user = authority.user
    if user is None:
        return
    if user.id in {form.created_by_id, actor.id if actor else None}:
        return
    notify(
        user,
        title=f"MRM {form.mrm_number} awaits your decision",
        message=(
            f"{authority.label} signs this assessment at "
            f"{form.total_percent if form.total_percent is not None else 0:.1f}% risk. "
            f"{form.activity_mission}"
        ),
        category="approval",
        priority="high",
        related=("mrm_form", form.id),
        actor=actor,
        requires_action=True,
        action_label="Review and sign the MRM",
        action_url=f"/risk/{form.id}",
    )


def _notify_creator_to_sign(form, actor):
    if not form.created_by_id or form.creator_signed:
        return
    notify(
        form.created_by,
        title=f"Sign MRM {form.mrm_number}",
        message=(
            "The evaluator approved this assessment and your signature is the last "
            "one required before the decision authority acts."
        ),
        category="risk",
        priority="high",
        related=("mrm_form", form.id),
        actor=actor,
        requires_action=True,
        action_label="Sign the MRM",
        action_url=f"/risk/{form.id}",
    )


def _apply_risk_calculation(assessment: RiskAssessment) -> None:
    """Recompute legacy risk aggregates from the MRM's selected hazards."""
    links = MissionHazards.objects.select_related("hazard").filter(
        mrm_id=assessment.mrm_form_id, selected=True
    )
    inputs = [
        risk_engine.HazardInput(
            after_likelihood=link.hazard.after_likelihood,
            after_severity=link.hazard.after_severity,
            override_likelihood=link.override_likelihood,
            override_severity=link.override_severity,
        )
        for link in links
        if link.hazard_id
    ]
    result = risk_engine.calculate(inputs)
    assessment.max_risk = result.max_risk
    assessment.residual_risk = result.residual_risk
    assessment.total_percent = result.total_percent
    assessment.risk_category = result.risk_category
    assessment.assessed_at = _now()


# ---------------------------------------------------------------------------
# MRM risk register (legacy tables, new API)
# ---------------------------------------------------------------------------


def _covered_dates_q(date_from, date_to) -> Q:
    """The dates an MRM covers: its linked mission window, else when it was raised.

    A mission with no end is treated as open-ended, so an assessment for an
    operation still under way matches a search that starts after it began.
    """
    covered = Q()
    if date_from is not None:
        covered &= (
            Q(
                toms_risk_links__mission__isnull=False,
                toms_risk_links__mission__end_at__date__gte=date_from,
            )
            | Q(
                toms_risk_links__mission__isnull=False,
                toms_risk_links__mission__end_at__isnull=True,
            )
            | Q(toms_risk_links__isnull=True, date_created__date__gte=date_from)
        )
    if date_to is not None:
        covered &= (
            Q(
                toms_risk_links__mission__isnull=False,
                toms_risk_links__mission__start_at__date__lte=date_to,
            )
            | Q(
                toms_risk_links__mission__isnull=False,
                toms_risk_links__mission__start_at__isnull=True,
            )
            | Q(toms_risk_links__isnull=True, date_created__date__lte=date_to)
        )
    return covered


class RiskRegisterViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Read the legacy MRM forms, drive the preserved workflow, and delete.

    Reads are scoped by ``apps.ops.mrm_access``: the Administrator sees every
    unit, command posts and the Unit Safety Officer see their own unit and its
    subordinates, and everyone else sees only the assessments they raised, were
    assigned, or were tagged on. Deletion is narrower still - Administrator, or
    the Unit Safety Officer for an MRM that is not approved.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = serializers.MRMFormSerializer
    queryset = MRMForm.objects.all()
    filterset_fields = ["status", "review_status", "authority_level", "authority_approved"]
    search_fields = ["mrm_number", "activity_mission", "creator_name", "reviewer_name"]
    ordering_fields = ["date_created", "mrm_number", "total_percent"]
    audit_entity = "mrm_form"

    def get_permissions(self):
        # The audit is the Safety Officer's own tool; the register's ordinary
        # read/write rules are unchanged.
        if self.action in {
            "audit",
            "audit_zip",
            "hide_attention",
            "restore_attention",
        }:
            return [IsAuthenticated(), CanViewSafetyAudit()]
        return [IsAuthenticated()]

    def get_queryset(self):
        from django.utils.dateparse import parse_date

        from apps.ops import mrm_access

        queryset = mrm_access.visible_mrms(
            self.request.user,
            MRMForm.objects.select_related(
                "created_by", "reviewed_by", "original_unit", "authority_assigned_to"
            ).prefetch_related("toms_risk_links__mission"),
        )
        user = self.request.user

        scope = self.request.query_params.get("scope")
        if scope == "mine":
            queryset = queryset.filter(created_by=user.id)
        elif scope == "to_review":
            queryset = queryset.filter(reviewed_by=user.id)
        elif scope == "pending_approval":
            queryset = queryset.filter(
                status="reviewed", review_status="approved", authority_approved=False
            )

        unit = self.request.query_params.get("unit") or self.request.query_params.get(
            "original_unit"
        )
        if unit:
            queryset = queryset.filter(original_unit_id=unit)

        date_from = parse_date(self.request.query_params.get("date_from") or "")
        date_to = parse_date(self.request.query_params.get("date_to") or "")
        if date_from is not None or date_to is not None:
            queryset = queryset.filter(_covered_dates_q(date_from, date_to))
        return queryset.distinct()

    @action(detail=False, methods=["get"])
    def units(self, request):
        """The units this caller may filter the register by."""
        from apps.ops import mrm_access

        rows = mrm_access.filter_units(request.user)
        return Response(
            {
                "count": rows.count(),
                "results": [
                    {"id": unit.id, "name": unit.name, "acronym": unit.acronym}
                    for unit in rows
                ],
            }
        )

    @action(detail=False, methods=["get"], url_path="audit")
    def audit(self, request):
        """The Unit Safety Officer's MRM audit summary for a covered period.

        Uses the register queryset, so the list and the charts always cover the
        same rows: the officer's own unit and its subordinates (plus any units
        assigned to them). ``date_from``/``date_to`` use the MRM's covered dates
        exactly as the register does, and ``unit`` narrows to one unit.
        """
        from django.utils.dateparse import parse_date

        from apps.ops import mrm_access

        granularity = (request.query_params.get("granularity") or "month").lower()
        if granularity not in safety_audit.GRANULARITIES:
            raise ValidationError(
                {"granularity": "Use week, month, quarter or year."}
            )

        forms = list(
            self.get_queryset()
            .select_related("original_unit")
            .order_by("mrm_number")
        )
        units = mrm_access.filter_units(request.user)
        scope_ids = sorted(mrm_access.unit_scope_ids(request.user))
        period_start = parse_date(request.query_params.get("date_from") or "")
        period_end = parse_date(request.query_params.get("date_to") or "")
        return Response(
            {
                "scope": {
                    "unit_id": getattr(request.user, "unit_id", None),
                    "unit_name": getattr(getattr(request.user, "unit", None), "name", "")
                    or "",
                    "unit_acronym": getattr(
                        getattr(request.user, "unit", None), "acronym", ""
                    )
                    or "",
                    "unit_ids": scope_ids,
                    "units": [
                        {"id": unit.id, "name": unit.name, "acronym": unit.acronym}
                        for unit in units
                    ],
                },
                "period": {
                    "date_from": request.query_params.get("date_from", ""),
                    "date_to": request.query_params.get("date_to", ""),
                    "granularity": granularity,
                },
                **safety_audit.summary(
                    forms, granularity, period_start, period_end
                ),
            }
        )

    @action(detail=False, methods=["get"], url_path="audit-zip")
    def audit_zip(self, request):
        """Download the covered period's MRM PDFs as one ZIP archive.

        ``ids`` selects specific rows (the table's checkboxes); without it the
        current covered-period/unit filters decide the set. Every id is resolved
        through the safety-scoped queryset, so a foreign id simply cannot enter
        the archive.
        """
        from apps.ops import mrm_access

        raw_ids = (request.query_params.get("ids") or "").strip()
        if raw_ids:
            try:
                ids = [int(value) for value in raw_ids.split(",") if value.strip()]
            except (TypeError, ValueError):
                raise ValidationError({"ids": "Ids must be numeric."})
            if not ids:
                raise ValidationError({"ids": "Select at least one assessment."})
            if len(ids) > 200:
                raise ValidationError(
                    {"ids": "Download at most 200 assessments at a time."}
                )
            forms = list(
                mrm_access.visible_mrms(
                    request.user,
                    MRMForm.objects.select_related("original_unit"),
                )
                .filter(pk__in=ids)
                .order_by("mrm_number")
            )
        else:
            forms = list(
                self.get_queryset()
                .select_related("original_unit")
                .order_by("mrm_number")
            )
            if len(forms) > 500:
                raise ValidationError(
                    {
                        "detail": "Narrow the covered period or unit to at most 500 assessments."
                    }
                )

        if not forms:
            raise ValidationError(
                {"detail": "No assessments match the selected covered period."}
            )

        stamp = _now().strftime("%Y%m%d_%H%M")
        response = HttpResponse(
            safety_audit.build_zip(forms), content_type="application/zip"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="Safety_Audit_{stamp}.zip"'
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    def create(self, request, *args, **kwargs):
        """Raise a draft MRM by hand, with no Operations Center mission.

        The Creator role is the one that raises assessments, so it is the one
        offered this path; an Administrator may use it too. The draft belongs
        to the caller's own unit and names its evaluator up front, but no
        ``RiskAssessment`` or ``Mission.risk_form`` link is created: the
        assessment stands on its own until (and unless) someone links it.
        """
        from django.db import IntegrityError

        if authority(request.user) not in (ADMIN, roles.BASE_CREATOR):
            raise PermissionDenied(
                "Only the Creator role can raise a manual MRM."
            )
        unit = getattr(request.user, "unit", None)
        if unit is None:
            raise ValidationError(
                {"detail": "Your account has no unit, so it cannot raise an MRM."}
            )

        serializer = serializers.ManualMRMCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            # A manual MRM belongs to the creator's unit; an evaluator from a
            # subordinate unit is not eligible for it.
            evaluator = services.resolve_evaluator(
                data["evaluator_id"], request.user, unit_id=unit.id
            )
        except services.EvaluatorError as exc:
            raise ValidationError({"evaluator_id": str(exc)}) from exc

        form = None
        for _attempt in range(5):
            try:
                with transaction.atomic():
                    form = MRMForm.objects.create(
                        mrm_number=services.next_mrm_number(),
                        activity_mission=data["activity_mission"],
                        created_by=request.user,
                        creator_name=request.user.full_name,
                        safety_objectives="",
                        mission_statement=data.get("mission_statement", ""),
                        original_unit=unit,
                        original_unit_name=unit.name,
                        original_creator_unit=unit,
                        original_creator_unit_name=unit.name,
                        reviewed_by=evaluator,
                        reviewer_name=evaluator.full_name,
                        original_reviewer_unit=evaluator.unit,
                        original_reviewer_unit_name=(
                            evaluator.unit.name if evaluator.unit_id else ""
                        ),
                        date_created=timezone.now(),
                        status="draft",
                        review_status="pending",
                        creator_signed=False,
                    )
                break
            except IntegrityError:
                continue
        if form is None:
            raise ValidationError(
                {"detail": "Could not allocate an MRM number. Try again."}
            )

        log_action(
            actor=request.user,
            action="create",
            entity_type="mrm_form",
            entity_id=form.pk,
            after={
                "manual": True,
                "mrm_number": form.mrm_number,
                "activity_mission": form.activity_mission,
                "evaluator_id": evaluator.id,
            },
            request=request,
        )
        return Response(
            serializers.MRMFormSerializer(
                form, context={"request": request}
            ).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        """Delete one MRM (Administrator, or Unit Safety Officer when unapproved)."""
        from apps.ops import mrm_access

        form = self.get_object()
        allowed, reason = mrm_access.can_delete(request.user, form)
        if not allowed:
            raise PermissionDenied(reason)
        mrm_access.delete_mrm(form, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """Delete several MRMs at once, reporting the ones that were refused."""
        from apps.ops import mrm_access

        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValidationError({"ids": "Select at least one MRM to delete."})
        if len(ids) > 200:
            raise ValidationError({"ids": "Delete at most 200 assessments at a time."})

        visible = {row.pk: row for row in self.get_queryset().filter(pk__in=ids)}
        deleted: list[int] = []
        skipped: list[dict] = []
        for raw in ids:
            try:
                pk = int(raw)
            except (TypeError, ValueError):
                skipped.append({"id": raw, "reason": "Not a valid assessment id."})
                continue
            form = visible.get(pk)
            if form is None:
                skipped.append({"id": pk, "reason": "Not found in your scope."})
                continue
            allowed, reason = mrm_access.can_delete(request.user, form)
            if not allowed:
                skipped.append({"id": pk, "reason": reason})
                continue
            mrm_access.delete_mrm(form, actor=request.user, request=request)
            deleted.append(pk)
        return Response({"deleted": deleted, "skipped": skipped})

    @action(detail=True, methods=["post"], url_path="assign-evaluator")
    def assign_evaluator(self, request, pk=None):
        """Assign or correct the evaluator (the fixed MRMS defect).

        The rule is identical to ``routes.mrm.resolve_evaluator`` in the Flask
        app and is enforced here regardless of what the client sends.
        """
        form = self.get_object()
        if form.created_by_id != request.user.id and authority(request.user) not in (
            ADMIN,
            UNIT_SAFETY,
        ):
            raise PermissionDenied(
                "Only the creator, a unit safety officer or an administrator can "
                "assign an evaluator."
            )

        serializer = serializers.EvaluatorAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            evaluator = services.resolve_evaluator(
                serializer.validated_data.get("evaluator_id"), request.user
            )
        except services.EvaluatorError as exc:
            raise ValidationError({"evaluator_id": str(exc)}) from exc

        if form.status == "reviewed" or form.authority_approved:
            raise ValidationError(
                {"detail": "The evaluator cannot be changed after review."}
            )

        before = {"reviewed_by": form.reviewed_by_id, "reviewer_name": form.reviewer_name}
        form.reviewed_by = evaluator
        form.reviewer_name = evaluator.full_name
        form.save(update_fields=["reviewed_by", "reviewer_name"])

        log_action(
            actor=request.user,
            action="assign_evaluator",
            entity_type="mrm_form",
            entity_id=form.pk,
            before=before,
            after={"reviewed_by": evaluator.id, "reviewer_name": evaluator.full_name},
            note=serializer.validated_data.get("note", ""),
            request=request,
        )
        record_activity(
            user=request.user,
            activity_type="edit_mrm",
            module="mrm",
            description=f"Assigned evaluator {evaluator.username} to MRM {form.mrm_number}",
            request=request,
            resource_id=form.id,
            resource_type="mrm_form",
        )
        notify(
            evaluator,
            title=f"MRM {form.mrm_number} assigned to you",
            message=form.activity_mission,
            category="risk",
            related=("mrm_form", form.id),
            actor=request.user,
        )
        return Response(serializers.MRMFormSerializer(form).data)

    @action(detail=False, methods=["get"])
    def evaluators(self, request):
        """Eligible evaluators from the caller's own unit (the manual MRM form).

        A manual MRM is raised for the caller's unit, so an evaluator from a
        subordinate unit is not offered even though the caller can normally
        read that unit.
        """
        user = request.user
        queryset = User.objects.filter(authority=EVALUATOR, is_active=True)
        if getattr(user, "unit_id", None) is None:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(unit_id=user.unit_id)

        # A command post holder is not offered as an evaluator for a manual
        # assessment. Check both ways the system records a designation: an
        # active appointment and the personnel record's designation field.
        from apps.command.models import UnitAppointment

        command_post_holders = UnitAppointment.objects.filter(
            is_active=True,
            role__in=(
                roles.ROLE_COMMANDER,
                roles.ROLE_DEPUTY_COMMANDER,
                roles.ROLE_DIRECTOR_OPERATIONS,
            ),
        ).values_list("user_id", flat=True)
        queryset = queryset.exclude(id__in=command_post_holders)
        queryset = queryset.exclude(
            Q(designation__iexact="Commander")
            | Q(designation__iexact="Deputy Commander")
            | Q(designation__iexact="Director for Operations")
        )
        queryset = queryset.exclude(id=user.id)
        return Response(
            {
                "count": queryset.count(),
                "results": [
                    {
                        "id": u.id,
                        "full_name": u.full_name,
                        "rank": u.rank or "",
                        "designation": u.designation,
                        "unit_id": u.unit_id,
                        "unit_name": u.unit.name if u.unit_id else "",
                    }
                    for u in queryset.order_by("last_name", "first_name")
                ],
            }
        )

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        form = self.get_object()
        if form.created_by_id != request.user.id and authority(request.user) != ADMIN:
            raise PermissionDenied("Only the creator or an administrator can submit this MRM.")
        if not form.reviewed_by_id:
            raise ValidationError({"detail": "Assign an evaluator before submitting."})

        # The submit dialog offers "sign now" or "sign later". Signing now needs
        # an uploaded signature; the client is told so it can send the user to
        # their profile instead of failing silently.
        sign_now = bool(request.data.get("sign"))
        if sign_now and not _has_signature(request.user):
            raise _signature_required(
                "Upload your signature in your profile before signing this MRM."
            )

        updates = ["status", "date_submitted", "review_status"]
        form.status = "submitted"
        form.date_submitted = _now()
        form.review_status = "pending"
        if sign_now:
            form.creator_signed = True
            form.creator_signature_date = _now()
            updates += ["creator_signed", "creator_signature_date"]
        form.save(update_fields=updates)

        # Route the assessment to the signer its risk band calls for, so the
        # record already names the decision authority while it is under review.
        _route_decision_authority(form)
        log_action(
            actor=request.user,
            action="submit",
            entity_type="mrm_form",
            entity_id=form.pk,
            after={"status": form.status},
            note="Submitted for review",
            request=request,
        )
        record_activity(
            user=request.user,
            activity_type="submit_mrm",
            module="mrm",
            description=f"Submitted MRM {form.mrm_number}",
            request=request,
            resource_id=form.id,
            resource_type="mrm_form",
        )
        # The creator's "complete and submit" item is done at submission; the
        # evaluator then receives a new action item below.
        complete_action(
            related_type="mrm_form",
            related_ids=[form.id],
            note="Submitted for evaluation",
            actor=request.user,
        )
        if form.reviewed_by_id:
            notify(
                form.reviewed_by,
                title=f"MRM {form.mrm_number} submitted for review",
                message=form.activity_mission,
                category="risk",
                priority="high",
                related=("mrm_form", form.id),
                actor=request.user,
                requires_action=True,
                action_label="Review assessment",
                action_url=f"/risk/{form.id}",
            )

        # The mission's "prepare the MRM" action item is now done. Reading the
        # notification never does this - the workflow that performed the work
        # records completion.
        link = _linked_mission_for_mrm(form)
        if link:
            link.status = "submitted"
            link.save(update_fields=["status", "updated_at"])
            complete_action(
                related_type="mission_mrm",
                related_ids=[link.mission_id],
                note=f"MRM {form.mrm_number} submitted for evaluation",
                actor=request.user,
            )
        return Response(serializers.MRMFormSerializer(form).data)

    @action(detail=True, methods=["get"])
    def trail(self, request, pk=None):
        """The MRM's own action history, oldest first.

        It is read from the TOWEMS audit log so every submission, signature,
        evaluation and authority decision appears with the person and comment
        that produced it - the same trail the mission page shows.
        """
        form = self.get_object()
        from apps.ops.models import AuditLog

        labels = {
            "create": "Assessment raised",
            "submit": "Submitted for review",
            "sign": "Signed by creator",
            "review": "Evaluation recorded",
            "approve": "Approved by decision authority",
        }
        rows = (
            AuditLog.objects.filter(entity_type="mrm_form", entity_id=str(form.id))
            .order_by("created_at", "id")
        )
        results = []
        for row in rows:
            after = row.after or {}
            review_state = str(after.get("review_status") or "")
            note = row.note or ""
            if not note and review_state:
                note = review_state.replace("_", " ").capitalize()
            actor_name = ""
            if row.actor_id:
                actor = User.objects.filter(pk=row.actor_id).first()
                if actor is not None:
                    actor_name = actor.full_name or actor.username
            action_label = labels.get(
                row.action, row.action.replace("_", " ").capitalize()
            )
            if row.action == "review" and review_state == "needs_revision":
                action_label = "Returned for revision"
            elif row.action == "review" and review_state == "rejected":
                action_label = "Rejected by evaluator"
            results.append(
                {
                    "id": row.id,
                    "action": row.action,
                    "action_label": action_label,
                    "stage_label": "MRM workflow",
                    "actor_name": actor_name or "System",
                    "note": note,
                    "created_at": row.created_at,
                    "is_return": review_state in {"rejected", "needs_revision"},
                }
            )
        return Response({"results": results})

    @action(detail=True, methods=["post"])
    def sign(self, request, pk=None):
        """The creator signs the MRM after submitting it.

        Submitting offers "sign now or later"; this is the "later" path. A
        signature image must already exist in the profile, exactly as when the
        submit dialog signs immediately.
        """
        form = self.get_object()
        if form.created_by_id != request.user.id and authority(request.user) != ADMIN:
            raise PermissionDenied("Only the creator can sign this MRM.")
        if form.authority_approved:
            raise ValidationError({"detail": "This MRM is already approved."})
        if form.status not in {"submitted", "reviewed"}:
            raise ValidationError({"detail": "Submit the MRM before signing it."})
        if not _has_signature(request.user):
            raise _signature_required(
                "Upload your signature in your profile before signing this MRM."
            )

        if not form.creator_signed:
            form.creator_signed = True
            form.creator_signature_date = _now()
            form.save(update_fields=["creator_signed", "creator_signature_date"])
            log_action(
                actor=request.user,
                action="sign",
                entity_type="mrm_form",
                entity_id=form.pk,
                after={"creator_signed": True},
                request=request,
            )
            record_activity(
                user=request.user,
                activity_type="sign_mrm",
                module="mrm",
                description=f"Signed MRM {form.mrm_number}",
                request=request,
                resource_id=form.id,
                resource_type="mrm_form",
            )
            complete_action(
                related_type="mrm_form",
                related_ids=[form.id],
                note="Signed by the creator",
                actor=request.user,
            )

        routed, closed = _finalize_if_signatures_complete(form, request.user)
        if not closed:
            routed = _route_decision_authority(form)
            # Signing at submission is not the authority's cue: the assessment is
            # still with the evaluator. They are told once the review approves.
            if (
                routed.resolved
                and routed.user.id != form.created_by_id
                and form.review_status == "approved"
                and form.evaluator_signed
            ):
                _notify_decision_authority(form, routed, request.user)
        return Response(serializers.MRMFormSerializer(form).data)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        form = self.get_object()
        if form.reviewed_by_id != request.user.id and authority(request.user) != ADMIN:
            raise PermissionDenied("Only the assigned evaluator can review this MRM.")

        decision = request.data.get("decision")
        if decision not in {"approved", "rejected", "needs_revision"}:
            raise ValidationError(
                {"decision": "Use 'approved', 'rejected' or 'needs_revision'."}
            )

        form.review_status = decision
        form.review_notes = request.data.get("notes", form.review_notes)
        form.date_reviewed = _now()
        # Only an approval is a signature. A return is the evaluator declining
        # to sign this version, so it must not leave a signature (or an approval)
        # behind on a record that is about to go back to the creator.
        signed = decision == "approved"
        form.evaluator_signed = signed
        form.evaluator_signature_date = _now() if signed else None
        update_fields = [
            "review_status",
            "review_notes",
            "date_reviewed",
            "evaluator_signed",
            "evaluator_signature_date",
            "status",
        ]
        if decision == "approved":
            form.status = "reviewed"
        elif decision == "needs_revision":
            # A returned assessment goes back to its creator as an editable
            # draft, so the bell's "Correct and resubmit" task can be actioned.
            # The returned version is unsigned: both parties sign the version
            # they actually reviewed.
            form.status = "draft"
            form.creator_signed = False
            form.creator_signature_date = None
            form.authority_approved = False
            form.authority_approval_date = None
            form.authority_signature_date = None
            update_fields += [
                "creator_signed",
                "creator_signature_date",
                "authority_approved",
                "authority_approval_date",
                "authority_signature_date",
            ]
        form.save(update_fields=update_fields)
        log_action(
            actor=request.user,
            action="review",
            entity_type="mrm_form",
            entity_id=form.pk,
            after={"review_status": decision},
            note=(form.review_notes or decision.replace("_", " ")),
            request=request,
        )
        record_activity(
            user=request.user,
            activity_type="review_mrm",
            module="mrm",
            description=f"Reviewed MRM {form.mrm_number}: {decision}",
            request=request,
            resource_id=form.id,
            resource_type="mrm_form",
        )
        complete_action(
            related_type="mrm_form",
            related_ids=[form.id],
            note=f"Evaluator decision: {decision}",
            actor=request.user,
        )
        notify(
            form.created_by,
            title=f"MRM {form.mrm_number} {decision.replace('_', ' ')}",
            message=request.data.get("notes", ""),
            level="success" if decision == "approved" else "warning",
            category="risk",
            related=("mrm_form", form.id),
            actor=request.user,
            priority="high",
            requires_action=decision == "needs_revision",
            action_label="Correct and resubmit" if decision == "needs_revision" else "",
            action_url=f"/risk/{form.id}",
        )
        if decision == "approved":
            from apps.core.roles import approver_roles_for
            from apps.command.services import users_holding_post

            link = _linked_mission_for_mrm(form)
            if link:
                link.status = "under_review"
                link.save(update_fields=["status", "updated_at"])

            # The risk may have changed while under review, so route again.
            routed = _route_decision_authority(form)
            authority, closed = _finalize_if_signatures_complete(form, request.user)
            if not closed:
                _notify_creator_to_sign(form, request.user)
                if routed.resolved:
                    _notify_decision_authority(form, routed, request.user)
                else:
                    # No designated post exists: fall back to the unit's approver
                    # roles so the change request still has a route.
                    notify_many(
                        users_holding_post(
                            form.original_unit_id,
                            *approver_roles_for("mrm", form.original_unit_id),
                        ),
                        title=f"MRM {form.mrm_number} awaiting authority decision",
                        message=form.activity_mission,
                        category="approval",
                        priority="high",
                        related=("mrm_form", form.id),
                        actor=request.user,
                        requires_action=True,
                        action_label="Approve assessment",
                        action_url=f"/risk/{form.id}",
                    )
        else:
            link = _linked_mission_for_mrm(form)
            if link:
                link.status = "rejected"
                link.save(update_fields=["status", "updated_at"])
                notify_many(
                    [row.user for row in link.mission.mrm_team.filter(is_active=True).select_related("user")],
                    title=f"MRM {form.mrm_number} returned for revision",
                    message=request.data.get("notes") or form.activity_mission,
                    level="warning",
                    category="risk",
                    priority="high",
                    related=("mission_mrm", link.mission_id),
                    actor=request.user,
                    requires_action=True,
                    action_label="Correct and resubmit the MRM",
                    action_url=f"/risk/{form.id}",
                )
        return Response(serializers.MRMFormSerializer(form).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        form = self.get_object()
        if authority(request.user) not in (ADMIN, UNIT_SAFETY) and not can(
            request.user, roles.CAP_APPROVE, form.original_unit_id
        ):
            raise PermissionDenied("Only command or unit safety roles may approve an MRM.")
        if not form.is_approvable:
            raise ValidationError(
                {
                    "detail": (
                        "This MRM is not approvable yet: it needs a completed review, "
                        "review approval and both creator and evaluator signatures."
                    )
                }
            )
        # The risk band names who signs. When the unit has that post, only that
        # person may give the final decision - the band is the routing rule.
        routed = _route_decision_authority(form)
        if authority(request.user) != ADMIN:
            if routed.user is not None and routed.user.id != request.user.id:
                raise PermissionDenied(
                    f"This assessment ({routed.label}, "
                    f"{float(form.total_percent or 0):.1f}%) routes to "
                    f"{routed.user.full_name}. Only that Decision Authority may sign it."
                )
            if routed.user is None:
                # Fall back to the configured approver posts when no post exists.
                from apps.core.roles import approver_roles_for, effective_roles

                permitted = set(approver_roles_for("mrm", form.original_unit_id))
                if not (
                    effective_roles(request.user, form.original_unit_id) & permitted
                ):
                    raise PermissionDenied(
                        "You are not the designated Decision Authority for this assessment."
                    )

        # Separation of duties (spec §12C) still applies where the policy does not
        # itself name the signer: the creator and evaluator cannot rubber-stamp
        # their own assessment through a generic approver post.
        if routed.user is None:
            if form.created_by_id == request.user.id:
                raise PermissionDenied("You cannot approve an assessment you created.")
            if form.reviewed_by_id == request.user.id:
                raise PermissionDenied(
                    "The assigned evaluator cannot also act as the approving authority."
                )

        _complete_authority_approval(form, request.user, request)
        return Response(serializers.MRMFormSerializer(form).data)

    # -- assessment editor (hazards, personnel, conditions) ---------------

    @action(detail=False, methods=["get"], url_path="hazards")
    def hazards(self, request):
        """Search this unit's hazard library (the legacy ``hazard_registry``).

        Same scope as the Flask ``/mrm/search_hazards``: the caller's own unit,
        filtered by activity code and description.
        """
        from apps.ops import mrm_editor

        rows = mrm_editor.hazard_library(
            request.user,
            activity_code=(request.query_params.get("activity_code") or "").strip(),
            description=(
                request.query_params.get("q")
                or request.query_params.get("hazard_description")
                or ""
            ).strip(),
            limit=200,
        )
        return Response({"count": len(rows), "results": rows})

    @action(detail=False, methods=["get"], url_path="hazard-codes")
    def hazard_codes(self, request):
        """The activity codes present in this unit's hazard registry.

        The planner picks an activity code first, so the options must be the
        codes their own unit's library actually holds.
        """
        from apps.ops import mrm_editor

        rows = mrm_editor.hazard_activity_codes(request.user)
        return Response({"count": len(rows), "results": rows})

    @action(detail=True, methods=["get"], url_path="forecast")
    def forecast(self, request, pk=None):
        """Forecast the weather for this assessment.

        A mission-linked assessment uses the mission's pinned location and
        itinerary stops, so the condition offered belongs to the places and
        hours the mission covers. A manual MRM has no route or window, so it
        uses the assessed unit's current map marker for the next few hours.
        A failure is reported as ``available: false`` with the reason; nothing
        is guessed.
        """
        from django.utils.dateparse import parse_datetime

        from apps.ops import mrm_editor, weather

        form = self.get_object()
        context = mrm_editor.mission_context(form)
        if context["mission_id"] is None:
            point = mrm_editor.current_location(form)
            if point is None:
                return Response(
                    {
                        "available": False,
                        "detail": (
                            "This assessment is not linked to a mission, and its unit "
                            "has no plotted location. Set the unit's marker in "
                            "Operational Mapping to forecast its current location."
                        ),
                    }
                )
            try:
                # No mission window: the provider answers for the next hours,
                # starting now, at the unit's own position.
                return Response(weather.forecast_for([point]))
            except weather.WeatherError as exc:
                return Response({"available": False, "detail": str(exc)})

        start = parse_datetime(context["window"]["start"] or "")
        end = parse_datetime(context["window"]["end"] or "")
        try:
            result = weather.forecast_for(context["forecast_points"], start, end)
        except weather.WeatherError as exc:
            return Response({"available": False, "detail": str(exc)})
        return Response(result)

    @action(detail=True, methods=["get", "patch"], url_path="details")
    def details(self, request, pk=None):
        """Read or save the body of an assessment.

        Reading is scoped by the viewset queryset. Writing is restricted to the
        people who own the assessment (see ``mrm_editor.can_edit``) and only
        while it is still a draft - an evaluator reviews it, they do not fill
        it in.
        """
        from apps.ops import mrm_editor

        form = self.get_object()
        if request.method == "GET":
            return Response(mrm_editor.payload(form, request.user))

        if not mrm_editor.can_edit(form, request.user):
            raise PermissionDenied(
                "Only the assigned MRM team (or the creator, unit safety officer or "
                "an administrator) may change this assessment, and only while it is "
                "a draft."
            )
        try:
            with transaction.atomic():
                data = mrm_editor.save_details(form, request.data, request.user)
                link = _linked_mission_for_mrm(form)
                if link is not None:
                    _apply_risk_calculation(link)
                    link.save(
                        update_fields=[
                            "max_risk",
                            "residual_risk",
                            "total_percent",
                            "risk_category",
                            "assessed_at",
                        ]
                    )
                log_action(
                    actor=request.user,
                    action="edit_mrm_details",
                    entity_type="mrm_form",
                    entity_id=form.pk,
                    after={
                        "hazards": len(data.get("hazards") or []),
                        "personnel": len(data.get("personnel") or []),
                        "total_percent": (data.get("risk") or {}).get("total_percent"),
                    },
                    request=request,
                )
        except mrm_editor.MrmEditError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(mrm_editor.payload(form, request.user))

    @action(detail=True, methods=["post"])
    def recalculate(self, request, pk=None):
        """Recompute risk aggregates using the preserved legacy algorithm."""
        form = self.get_object()
        if form.authority_approved:
            raise PermissionDenied(
                "An approved MRM cannot be recalculated. Its signed risk figures are final."
            )
        links = MissionHazards.objects.select_related("hazard").filter(
            mrm_id=form.id, selected=True
        )
        inputs = [
            risk_engine.HazardInput(
                after_likelihood=link.hazard.after_likelihood,
                after_severity=link.hazard.after_severity,
                override_likelihood=link.override_likelihood,
                override_severity=link.override_severity,
            )
            for link in links
            if link.hazard_id
        ]
        result = risk_engine.calculate(inputs)
        form.max_risk = result.max_risk
        form.residual_risk = result.residual_risk
        form.total_percent = result.total_percent
        form.authority_level = result.authority_level
        form.risk_category = result.risk_category
        form.save(
            update_fields=[
                "max_risk",
                "residual_risk",
                "total_percent",
                "authority_level",
                "risk_category",
            ]
        )
        log_action(
            actor=request.user,
            action="recalculate_risk",
            entity_type="mrm_form",
            entity_id=form.pk,
            after={
                "total_percent": result.total_percent,
                "authority_level": result.authority_level,
            },
            request=request,
        )
        return Response(serializers.MRMFormSerializer(form).data)

    @action(detail=True, methods=["post"], url_path="hide-attention")
    def hide_attention(self, request, pk=None):
        """Hide this assessment from the caller's dashboard attention list."""
        form = self.get_object()
        dismissal, created = MRMAttentionDismissal.objects.get_or_create(
            user=request.user, mrm_form=form
        )
        if created:
            log_action(
                actor=request.user,
                action="hide_mrm_attention",
                entity_type="mrm_form",
                entity_id=form.pk,
                after={"hidden": True},
                request=request,
            )
        return Response({"hidden": True, "id": form.id, "dismissal_id": dismissal.id})

    @action(detail=True, methods=["post"], url_path="restore-attention")
    def restore_attention(self, request, pk=None):
        """Restore an assessment the caller previously hid from the dashboard."""
        form = self.get_object()
        deleted, _ = MRMAttentionDismissal.objects.filter(
            user=request.user, mrm_form=form
        ).delete()
        if deleted:
            log_action(
                actor=request.user,
                action="restore_mrm_attention",
                entity_type="mrm_form",
                entity_id=form.pk,
                after={"hidden": False},
                request=request,
            )
        return Response({"hidden": False, "id": form.id})

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        """View or download this MRM as a PDF.

        Mirrors the legacy ``serve_approved_pdf`` / ``generate_pdf_content``
        pair: an approved PDF already stored on disk is served verbatim,
        otherwise the document is rendered from the record with the preserved
        layout. ``?download=1`` returns it as an attachment.
        """
        form = self.get_object()  # unit-scoped queryset
        if not pdf_service.can_view_pdf(request.user, form):
            raise PermissionDenied("You are not allowed to view this MRM PDF.")

        download = str(request.query_params.get("download", "")).lower() in {
            "1",
            "true",
            "yes",
        }

        stored = pdf_service.stored_pdf_path(form)
        if stored is not None:
            content = stored.read_bytes()
            filename = stored.name
        else:
            content = pdf_service.generate_mrm_pdf(form)
            filename = f"MRM_{form.mrm_number}.pdf"

        response = HttpResponse(content, content_type="application/pdf")
        disposition = "attachment" if download else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


class InventoryCategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanViewResources, InventoryPermission]
    serializer_class = serializers.InventoryCategorySerializer
    queryset = InventoryCategory.objects.all()
    filterset_fields = ["kind", "is_sensitive"]
    search_fields = ["name", "code"]
    ordering_fields = ["kind", "name", "code"]

    def get_permissions(self):
        """The catalogue is read by everyone who reads the register.

        Adding, renaming or removing a category is the System Administrator's
        call: a Logistics Officer uses the codes they are given and requests a
        new one through their administrator.
        """
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsAuthenticated(), IsAdministrator()]
        return super().get_permissions()

    def destroy(self, request, *args, **kwargs):
        """Refuse to remove a category that still holds items.

        The foreign key is ``PROTECT``, so the database would refuse anyway -
        this turns that into a readable 400 instead of a server error, and names
        what has to move first.
        """
        instance = self.get_object()
        count = instance.items.count()
        if count:
            return Response(
                {
                    "detail": (
                        f"{instance.code} still holds {count} item(s). "
                        "Move or remove them before deleting the category."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class InventoryItemViewSet(AuditedModelViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanViewResources, InventoryPermission]
    serializer_class = serializers.InventoryItemSerializer
    filterset_fields = ["category", "serviceability", "unit", "is_sensitive"]
    search_fields = ["name", "asset_tag", "serial_number", "description", "storage_location"]
    ordering_fields = ["name", "quantity_available", "updated_at"]
    audit_entity = "inventory_item"

    def destroy(self, request, *args, **kwargs):
        """Delete an item, or purge it with its ledger for the seat holder.

        A ledger entry is history, so the ordinary delete refuses an item that
        has movements. ``?purge=true`` is the Super Administrator's override: it
        removes the movements with the item and keeps a snapshot in the audit
        trail.
        """
        if str(request.query_params.get("purge", "")).lower() not in {
            "1",
            "true",
            "yes",
        }:
            return super().destroy(request, *args, **kwargs)

        from apps.accounts.ownership import can_delete_records

        if not can_delete_records(request.user):
            raise PermissionDenied(
                "Only the Super Administrator may purge an inventory item with "
                "its ledger."
            )

        from django.db import transaction

        from apps.core.audit import log_action
        from apps.ops.models import InventoryTransaction

        instance = self.get_object()
        item_id = instance.pk
        label = {
            "name": instance.name,
            "asset_tag": instance.asset_tag,
            "serial_number": instance.serial_number,
            "category": instance.category.code if instance.category_id else "",
            "unit_id": instance.unit_id,
        }
        with transaction.atomic():
            movements = InventoryTransaction.objects.filter(item_id=item_id)
            label["movements"] = movements.count()
            label["quantity_total"] = instance.quantity_total
            movements.delete()
            instance.delete()
            log_action(
                actor=request.user,
                action="delete",
                entity_type="inventory_item",
                entity_id=item_id,
                before=label,
                note="Purged with its ledger by the Super Administrator",
                request=request,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        queryset = InventoryItem.objects.select_related("category", "unit", "custodian")
        unit_ids = unit_ids_for_filter(self.request.user)
        if unit_ids is not None:
            queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        # Controlled items (firearms, ammunition) are visible to the Safety
        # Officer and to the Logistics Officer who now maintains that part of
        # the register, not to the legacy account type alone.
        if not _can_see_sensitive(self.request.user):
            queryset = queryset.filter(is_sensitive=False, category__is_sensitive=False)
        # Asset class (materiel, equipment, AGE, firearms, ammunition, ...).
        # ``filterset_fields`` cannot express the related lookup, so it is
        # applied here where the unit and sensitive scoping already happens.
        # A section tab may span two kinds - the Armory is firearms and
        # ammunition - so the value is a comma list as well as a single kind.
        kinds = [
            value.strip()
            for value in (self.request.query_params.get("kind") or "").split(",")
            if value.strip()
        ]
        if kinds:
            queryset = queryset.filter(category__kind__in=kinds)
        return queryset

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        """Apply an atomic stock movement (issue/return/transfer/...)."""
        item = self.get_object()
        serializer = serializers.StockMovementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            txn = services.record_stock_movement(
                item_id=item.id,
                kind=data["kind"],
                quantity=data["quantity"],
                actor=request.user,
                to_unit_id=data["to_unit"].id if data.get("to_unit") else None,
                mission=data.get("mission"),
                reference=data.get("reference", ""),
                notes=data.get("notes", ""),
            )
        except services.StockError as exc:
            return Response(
                {"detail": str(exc), "errors": {"quantity": str(exc)}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.InventoryError as exc:
            return Response(
                {"detail": str(exc), "errors": {}}, status=status.HTTP_400_BAD_REQUEST
            )

        log_action(
            actor=request.user,
            action=f"inventory_{data['kind']}",
            entity_type="inventory_item",
            entity_id=item.id,
            after={
                "transaction_id": txn.id,
                "quantity": txn.quantity,
                "available_after": item.quantity_available,
            },
            request=request,
        )
        return Response(
            serializers.InventoryTransactionSerializer(txn).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def transactions(self, request, pk=None):
        item = self.get_object()
        page = self.paginate_queryset(item.transactions.all())
        data = serializers.InventoryTransactionSerializer(page or item.transactions.all(), many=True).data
        return Response(data)


class InventoryTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, CanViewResources, InventoryPermission]
    serializer_class = serializers.InventoryTransactionSerializer
    filterset_fields = ["kind", "item", "mission"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = InventoryTransaction.objects.select_related("item", "actor", "mission")
        if not _can_see_sensitive(self.request.user):
            queryset = queryset.filter(item__is_sensitive=False)
        unit_ids = unit_ids_for_filter(self.request.user)
        if unit_ids is not None:
            queryset = queryset.filter(
                Q(item__unit_id__in=unit_ids) | Q(item__unit_id__isnull=True)
            )
        return queryset


class InventoryRequestViewSet(viewsets.ModelViewSet):
    """Inventory changes that need Logistics Officer approval (spec §9C).

    A request is a *proposal*. Stock moves only inside ``decide`` when the
    decision is ``approved``, in the same transaction that records the ledger
    entry, so a pending request can never corrupt balances.
    """

    permission_classes = [IsAuthenticated, CanViewResources, CanRequestInventory]
    filterset_fields = ["status", "kind", "item", "unit"]
    search_fields = ["justification", "item__name", "item__asset_tag"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = InventoryRequest.objects.select_related(
            "item", "unit", "mission", "requested_by", "decided_by"
        )
        unit_ids = unit_ids_for_filter(self.request.user)
        if unit_ids is not None:
            queryset = queryset.filter(
                Q(unit_id__in=unit_ids) | Q(requested_by_id=self.request.user.id)
            )
        if not _can_see_sensitive(self.request.user):
            queryset = queryset.filter(item__is_sensitive=False)
        return queryset

    def get_serializer_class(self):
        return serializers.InventoryRequestSerializer

    def get_permissions(self):
        if self.action == "decide":
            return [IsAuthenticated(), CanApproveInventory()]
        return super().get_permissions()

    def create(self, request):
        serializer = serializers.InventoryRequestWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        item = InventoryItem.objects.filter(pk=data["item_id"]).first()
        if item is None:
            raise ValidationError({"item_id": "Unknown inventory item."})
        unit_ids = unit_ids_for_filter(request.user)
        if unit_ids is not None and item.unit_id not in unit_ids and item.unit_id is not None:
            raise PermissionDenied("That item is outside your authorised unit scope.")
        if item.is_sensitive and not _can_see_sensitive(request.user):
            raise PermissionDenied("You may not request changes to sensitive inventory.")

        mission = None
        if data.get("mission_id"):
            mission = Mission.objects.filter(pk=data["mission_id"]).first()
            if mission is None:
                raise ValidationError({"mission_id": "Unknown mission."})

        inventory_request = InventoryRequest.objects.create(
            item=item,
            kind=data["kind"],
            quantity=data["quantity"],
            unit_id=item.unit_id or request.user.unit_id,
            mission=mission,
            justification=data.get("justification", ""),
            requested_by=request.user,
        )
        log_action(
            actor=request.user,
            action="create_request",
            entity_type="inventory_request",
            entity_id=inventory_request.pk,
            after=snapshot(inventory_request),
            request=request,
        )

        from apps.command.services import responsible_users

        notify_many(
            responsible_users(inventory_request.unit_id, roles.ROLE_LOGISTICS_OFFICER),
            title=f"Inventory {data['kind']} request awaiting your decision",
            message=f"{item.name}: {data['quantity']} {item.unit_of_measure}",
            category="inventory",
            priority="high",
            related=("inventory_request", inventory_request.id),
            actor=request.user,
            requires_action=True,
            action_label="Approve or reject",
            action_url=f"/inventory?request={inventory_request.id}",
        )
        return Response(
            serializers.InventoryRequestSerializer(inventory_request).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def decide(self, request, pk=None):
        inventory_request = self.get_object()
        if inventory_request.status != InventoryRequest.STATUS_PENDING:
            raise ValidationError(
                {"detail": f"This request is already {inventory_request.status}."}
            )

        serializer = serializers.InventoryDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = serializer.validated_data["decision"]
        note = serializer.validated_data.get("note", "")

        item = InventoryItem.objects.select_for_update().get(pk=inventory_request.item_id)
        transaction_row = None

        if decision == "approved":
            try:
                transaction_row = services.record_stock_movement(
                    item_id=item.id,
                    kind=inventory_request.kind,
                    quantity=inventory_request.quantity,
                    actor=request.user,
                    mission=inventory_request.mission,
                    reference=f"REQ-{inventory_request.id}",
                    notes=note,
                )
            except services.StockError as exc:
                raise ValidationError({"detail": str(exc)}) from exc
            except services.InventoryError as exc:
                raise ValidationError({"detail": str(exc)}) from exc

        inventory_request.status = {
            "approved": InventoryRequest.STATUS_APPROVED,
            "rejected": InventoryRequest.STATUS_REJECTED,
            "returned": InventoryRequest.STATUS_RETURNED,
        }[decision]
        inventory_request.decided_by = request.user
        inventory_request.decided_at = _now()
        inventory_request.decision_note = note
        inventory_request.committed_transaction = transaction_row
        inventory_request.save(
            update_fields=[
                "status",
                "decided_by",
                "decided_at",
                "decision_note",
                "committed_transaction",
                "updated_at",
            ]
        )

        log_action(
            actor=request.user,
            action=f"inventory_request_{decision}",
            entity_type="inventory_request",
            entity_id=inventory_request.pk,
            after={
                "status": inventory_request.status,
                "transaction_id": getattr(transaction_row, "id", None),
                "note": note,
            },
            request=request,
        )
        complete_action(
            related_type="inventory_request",
            related_ids=[inventory_request.id],
            note=f"{decision} by {request.user.username}",
            actor=request.user,
        )
        notify(
            inventory_request.requested_by,
            title=f"Inventory request {decision}",
            message=note or f"{item.name}: {inventory_request.quantity}",
            level="success" if decision == "approved" else "warning",
            category="inventory",
            related=("inventory_request", inventory_request.id),
            actor=request.user,
            priority="high",
            requires_action=decision == "returned",
            action_label="Correct and resubmit" if decision == "returned" else "",
            action_url=f"/inventory?request={inventory_request.id}",
        )
        return Response(serializers.InventoryRequestSerializer(inventory_request).data)


# ---------------------------------------------------------------------------
# Vehicles and trips
# ---------------------------------------------------------------------------


class AirAssetViewSet(AuditedModelViewSetMixin, viewsets.ModelViewSet):
    """The air-asset register.

    Every resource-menu account may read the rows in its unit scope. Adding,
    editing and deleting an aircraft is Administrator-only, as is the register's
    source of truth: the Administrator is the only account that records it.
    """

    permission_classes = [IsAuthenticated, CanViewResources]
    serializer_class = serializers.AirAssetSerializer
    filterset_fields = [
        "aircraft_type",
        "model",
        "operational_status",
        "assigned_unit",
        "opcon_unit",
    ]
    search_fields = [
        "model",
        "tail_number",
        "callsign",
        "current_location",
        "opcon_unit__name",
        "opcon_unit__acronym",
    ]
    ordering_fields = ["model", "tail_number", "operational_status", "updated_at"]
    audit_entity = "air_asset"

    def get_permissions(self):
        permissions = [IsAuthenticated, CanViewResources]
        if self.action in {"create", "update", "partial_update", "destroy"}:
            permissions.append(IsAdministrator)
        return [permission() for permission in permissions]

    @action(detail=False, methods=["get"], url_path="conflicts")
    def conflicts(self, request):
        """Report overlapping air missions for the requested aircraft.

        The form calls this before saving so an operator cannot unknowingly
        plan the same airframe into two overlapping sorties. Completed and
        cancelled missions no longer occupy an aircraft.
        """
        from django.utils.dateparse import parse_datetime

        raw_ids = (request.query_params.get("air_asset_ids") or "").split(",")
        try:
            asset_ids = sorted({int(value) for value in raw_ids if value.strip()})
        except ValueError:
            raise ValidationError(
                {"air_asset_ids": "Provide comma-separated aircraft IDs."}
            )
        if not asset_ids:
            return Response({"conflicts": []})

        start = parse_datetime(request.query_params.get("start") or "")
        end = parse_datetime(request.query_params.get("end") or "")
        if start is None or end is None:
            raise ValidationError(
                {"window": "Scheduled departure and arrival are required."}
            )
        if timezone.is_aware(start):
            start = timezone.make_naive(start)
        if timezone.is_aware(end):
            end = timezone.make_naive(end)
        if end <= start:
            raise ValidationError({"window": "Arrival must be after departure."})

        visible_assets = list(self.get_queryset().filter(pk__in=asset_ids))
        visible_asset_ids = [asset.pk for asset in visible_assets]
        queryset = (
            AirMissionAsset.objects.filter(
                air_asset_id__in=visible_asset_ids,
                air_detail__scheduled_departure__isnull=False,
                air_detail__scheduled_arrival__isnull=False,
                air_detail__scheduled_departure__lt=end,
                air_detail__scheduled_arrival__gt=start,
            )
            .exclude(
                air_detail__mission__status__in=["completed", "cancelled"]
            )
            .select_related(
                "air_detail__mission__unit",
            )
            .order_by("air_detail__scheduled_departure", "air_detail__mission_id")
        )

        exclude_mission = request.query_params.get("exclude_mission")
        exclude_mission_id = None
        if exclude_mission:
            try:
                exclude_mission_id = int(exclude_mission)
            except ValueError:
                raise ValidationError({"exclude_mission": "Use a mission ID."})
            queryset = queryset.exclude(
                air_detail__mission_id=exclude_mission_id
            )

        rows = []
        seen: set[tuple[int, int]] = set()
        for assignment in queryset:
            detail = assignment.air_detail
            mission = detail.mission
            key = (assignment.air_asset_id, mission.id)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "air_asset": assignment.air_asset_id,
                    "aircraft_model": assignment.aircraft_model,
                    "tail_number": assignment.tail_number,
                    "is_lead": assignment.is_lead,
                    "mission_id": mission.id,
                    "mission_number": mission.mission_number,
                    "title": mission.title,
                    "status": mission.status,
                    "status_label": mission.get_status_display(),
                    "unit_acronym": mission.unit.acronym or "" if mission.unit else "",
                    "flight_identification": detail.flight_identification,
                    "start_at": detail.scheduled_departure,
                    "end_at": detail.scheduled_arrival,
                }
            )

        # The Flight Operations sheet stores its aircraft as manual element rows.
        # Include those rows too, otherwise the picker would offer an airframe that
        # the serializer will later reject as already assigned.
        manual_queryset = (
            AirMissionAircraft.objects.filter(
                element__air_detail__scheduled_departure__isnull=False,
                element__air_detail__scheduled_arrival__isnull=False,
                element__air_detail__scheduled_departure__lt=end,
                element__air_detail__scheduled_arrival__gt=start,
            )
            .exclude(
                element__air_detail__mission__status__in=["completed", "cancelled"]
            )
            .select_related("element__air_detail__mission__unit")
            .order_by(
                "element__air_detail__scheduled_departure",
                "element__air_detail__mission_id",
            )
        )
        if exclude_mission_id is not None:
            manual_queryset = manual_queryset.exclude(
                element__air_detail__mission_id=exclude_mission_id
            )

        by_identity = {
            (
                (asset.model or "").strip().casefold(),
                (asset.tail_number or "").strip().casefold(),
            ): asset
            for asset in visible_assets
        }
        for aircraft in manual_queryset:
            asset = by_identity.get(
                (
                    (aircraft.aircraft_type or "").strip().casefold(),
                    (aircraft.tail_number or "").strip().casefold(),
                )
            )
            if asset is None:
                continue
            detail = aircraft.element.air_detail
            mission = detail.mission
            key = (asset.pk, mission.id)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "air_asset": asset.pk,
                    "aircraft_model": aircraft.aircraft_type,
                    "tail_number": aircraft.tail_number,
                    "is_lead": aircraft.is_lead,
                    "mission_id": mission.id,
                    "mission_number": mission.mission_number,
                    "title": mission.title,
                    "status": mission.status,
                    "status_label": mission.get_status_display(),
                    "unit_acronym": mission.unit.acronym or "" if mission.unit else "",
                    "flight_identification": detail.flight_identification,
                    "start_at": detail.scheduled_departure,
                    "end_at": detail.scheduled_arrival,
                }
            )
        return Response({"conflicts": rows})

    def get_queryset(self):
        queryset = AirAsset.objects.select_related("opcon_unit")
        unit_ids = unit_ids_for_filter(self.request.user)
        if not can(self.request.user, roles.CAP_SYSTEM_CONFIG) and unit_ids is not None:
            # The aircraft belongs to the unit that will employ it. Aircraft
            # with no OPCON unit stay administrator-only until deployed.
            queryset = queryset.filter(opcon_unit_id__in=unit_ids)
        return queryset


class VehicleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanManageVehicles]
    serializer_class = serializers.VehicleSerializer

    def destroy(self, request, *args, **kwargs):
        """Delete a vehicle; the Super Administrator may erase any of them.

        The register delete already cascades to the trips that name the vehicle,
        so the seat needs no separate path - only the guarantee that it is the
        seat's decision, which ``permissions.is_system_owner`` mirrors in the UI.
        """
        return super().destroy(request, *args, **kwargs)
    filterset_fields = ["status", "unit", "is_active"]
    search_fields = [
        "registration_number",
        "make",
        "model",
        "make_model",
        "vehicle_type",
        "registration_no",
        "official_receipt_no",
    ]

    def get_queryset(self):
        queryset = Vehicle.objects.select_related("unit", "owner")
        # The register's Location column reads each vehicle's last report. One
        # prefetch answers it for the whole page instead of a query per row.
        from apps.command.models import VehiclePosition

        queryset = queryset.prefetch_related(
            Prefetch(
                "positions",
                queryset=VehiclePosition.objects.order_by("-reported_at", "-id"),
                to_attr="recent_positions",
            )
        )
        # The register is the unit's own fleet. A POV belongs to a person and is
        # read through the mission trip that uses it.
        if str(self.request.query_params.get("include_pov", "")).lower() not in {
            "1",
            "true",
            "yes",
        }:
            queryset = queryset.filter(is_pov=False)
        unit_ids = unit_ids_for_filter(self.request.user)
        if unit_ids is not None:
            queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        return queryset


class VehicleTripViewSet(AuditedModelViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanViewResources, CanPlanMovements]
    filterset_fields = ["status", "mission"]
    search_fields = ["trip_number", "title", "purpose"]
    audit_entity = "vehicle_trip"

    def perform_destroy(self, instance):
        # A trip's runs cascade away with it, so its vehicles must be
        # released here: no run is left that could ever clear their
        # "on trip" status, and the truck would stay out for a trip that
        # no longer exists.
        from apps.ops import tracking

        with transaction.atomic():
            tracking.release_trip_vehicles(instance)
            super().perform_destroy(instance)

    def get_queryset(self):
        return VehicleTrip.objects.select_related("mission", "created_by").prefetch_related(
            "itinerary", "vehicles__vehicle", "vehicles__driver"
        )

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return serializers.VehicleTripWriteSerializer
        return serializers.VehicleTripSerializer

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        instance = self.get_queryset().get(pk=response.data["id"])
        response.data = serializers.VehicleTripSerializer(instance).data
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        instance = self.get_queryset().get(pk=response.data["id"])
        response.data = serializers.VehicleTripSerializer(instance).data
        return response

    def perform_create(self, serializer):
        trip = serializer.save(created_by=self.request.user)
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="vehicle_trip",
            entity_id=trip.pk,
            after={"trip_number": trip.trip_number, "mission_id": trip.mission_id},
            request=self.request,
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        trip = self.get_object()
        if authority(request.user) not in (ADMIN, UNIT_SAFETY):
            raise PermissionDenied("Only command or unit safety roles may approve a trip.")
        trip.status = "approved"
        trip.save(update_fields=["status", "updated_at"])
        log_action(
            actor=request.user,
            action="approve",
            entity_type="vehicle_trip",
            entity_id=trip.pk,
            after={"status": trip.status},
            request=request,
        )
        notify(
            trip.created_by,
            title=f"Trip {trip.trip_number} approved",
            message=trip.title,
            level="success",
            category="approval",
            related=("vehicle_trip", trip.id),
            actor=request.user,
        )
        return Response(serializers.VehicleTripSerializer(trip).data)

    @action(detail=True, methods=["post", "put"])
    def itinerary(self, request, pk=None):
        """Append (POST) or replace (PUT) a trip's ordered itinerary.

        PUT takes the complete ordered list - ``{"stops": [...]}`` or a bare
        list - so reordering, editing and removing stops is one atomic write and
        the saved order is exactly what the planner sees. Any saved road route
        is discarded, because it described the old stop order.
        """
        trip = self.get_object()
        payload = request.data.get("stops", request.data) if isinstance(request.data, dict) else request.data
        serializer = serializers.TripItinerarySerializer(data=payload, many=True)
        serializer.is_valid(raise_exception=True)

        if request.method == "PUT":
            before = [
                {"sequence": stop.sequence, "location_name": stop.location_name}
                for stop in trip.itinerary.all()
            ]
            with transaction.atomic():
                trip.itinerary.all().delete()
                created = []
                for index, entry in enumerate(serializer.validated_data, start=1):
                    entry["sequence"] = index
                    created.append(trip.itinerary.create(**entry))
                _clear_route(trip)
            log_action(
                actor=request.user,
                action="replace_itinerary",
                entity_type="vehicle_trip",
                entity_id=trip.pk,
                before={"stops": before},
                after={"stops": len(created)},
                request=request,
            )
            _mark_plan_changed(trip)
            return Response(
                serializers.TripItinerarySerializer(created, many=True).data
            )

        next_sequence = (trip.itinerary.count() or 0) + 1
        created = []
        for index, entry in enumerate(serializer.validated_data):
            entry.setdefault("sequence", next_sequence + index)
            created.append(trip.itinerary.create(**entry))
        _clear_route(trip)
        log_action(
            actor=request.user,
            action="add_itinerary",
            entity_type="vehicle_trip",
            entity_id=trip.pk,
            after={"entries": len(created)},
            request=request,
        )
        _mark_plan_changed(trip)
        return Response(
            serializers.TripItinerarySerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def route(self, request, pk=None):
        """Calculate the planned road route through this trip's stops.

        The route follows the road network in itinerary order. If the engine
        cannot route a leg, the request fails with the reason and no straight
        line is drawn in its place.
        """
        trip = self.get_object()
        stops = [
            {
                "latitude": stop.latitude,
                "longitude": stop.longitude,
                "label": stop.location_name,
            }
            for stop in trip.itinerary.all()
        ]
        located = [stop for stop in stops if stop["latitude"] is not None and stop["longitude"] is not None]
        if len(located) < 2:
            trip.route_status = VehicleTrip.ROUTE_FAILED
            trip.route_error = (
                "At least two itinerary stops with coordinates are needed to "
                "calculate a driving route."
            )
            trip.save(update_fields=["route_status", "route_error", "updated_at"])
            raise ValidationError({"detail": trip.route_error})

        try:
            result = routing.route(stops)
        except routing.RoutingError as exc:
            trip.route_status = VehicleTrip.ROUTE_FAILED
            trip.route_error = str(exc)
            trip.route_geometry = None
            trip.route_legs = None
            trip.route_distance_m = None
            trip.route_duration_s = None
            trip.save(
                update_fields=[
                    "route_status",
                    "route_error",
                    "route_geometry",
                    "route_legs",
                    "route_distance_m",
                    "route_duration_s",
                    "updated_at",
                ]
            )
            log_action(
                actor=request.user,
                action="route_failed",
                entity_type="vehicle_trip",
                entity_id=trip.pk,
                after={"error": str(exc)},
                request=request,
            )
            raise ValidationError({"detail": str(exc)}) from exc

        trip.route_status = VehicleTrip.ROUTE_CALCULATED
        trip.route_geometry = result["geometry"]
        trip.route_legs = result["legs"]
        trip.route_distance_m = result["distance_m"]
        trip.route_duration_s = result["duration_s"]
        trip.route_provider = result["provider"]
        trip.route_calculated_at = _now()
        trip.route_error = ""
        trip.save(
            update_fields=[
                "route_status",
                "route_geometry",
                "route_legs",
                "route_distance_m",
                "route_duration_s",
                "route_provider",
                "route_calculated_at",
                "route_error",
                "updated_at",
            ]
        )
        log_action(
            actor=request.user,
            action="calculate_route",
            entity_type="vehicle_trip",
            entity_id=trip.pk,
            after={
                "distance_m": trip.route_distance_m,
                "duration_s": trip.route_duration_s,
                "provider": trip.route_provider,
                "legs": len(trip.route_legs or []),
            },
            request=request,
        )
        return Response(
            {
                "trip": serializers.VehicleTripSerializer(trip).data,
                "route": {
                    "provider": result["provider"],
                    "geometry": result["geometry"],
                    "legs": result["legs"],
                    "distance_m": result["distance_m"],
                    "duration_s": result["duration_s"],
                    "planned": True,
                    "label": "Planned driving route",
                },
            }
        )


# ---------------------------------------------------------------------------
# GIS / AOR
# ---------------------------------------------------------------------------


class OperationalMapFeatureViewSet(AuditedModelViewSetMixin, viewsets.ModelViewSet):
    """Operational mapping: the unit's saved plot of troops, places and areas.

    Everyone reads within their authorised unit scope, because the picture is
    used for mission planning. Writing is the Operations Center's job (its
    ``maintain_aor`` office capability) or an Administrator's.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = serializers.OperationalMapFeatureSerializer
    filterset_fields = ["feature_type", "unit"]
    search_fields = ["name", "description", "acronym", "address"]
    audit_entity = "operational_map"

    def get_queryset(self):
        queryset = OperationalMapFeature.objects.select_related("unit", "created_by")
        unit_ids = unit_ids_for_filter(self.request.user)
        if unit_ids is not None:
            queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        return queryset

    def _can_edit(self, unit_id=None) -> bool:
        user = self.request.user
        return roles.has_capability(
            user, roles.CAP_MANAGE_AOR
        ) or roles.has_capability(user, roles.CAP_MAINTAIN_AOR, unit_id)

    def _require_editor(self, unit_id=None):
        if not self._can_edit(unit_id):
            raise PermissionDenied(
                "Only the Operations Center or an Administrator may change operational mapping."
            )

    def perform_create(self, serializer):
        unit = serializer.validated_data.get("unit") or getattr(self.request.user, "unit", None)
        self._require_editor(getattr(unit, "id", None))
        serializer.save(created_by=self.request.user, unit=unit)

    def perform_update(self, serializer):
        instance = serializer.instance
        self._require_editor(instance.unit_id)
        target = serializer.validated_data.get("unit", instance.unit)
        if target is not None and target.id != instance.unit_id:
            self._require_editor(target.id)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        self._require_editor(instance.unit_id)
        super().perform_destroy(instance)


class AreaOfResponsibilityViewSet(AuditedModelViewSetMixin, viewsets.ModelViewSet):
    """AORs: everyone reads within their unit scope, only Administrators write.

    Spec §1/§6: creating, assigning, transferring or removing coverage is
    Administrator-only, enforced here regardless of what the UI renders.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = serializers.AreaOfResponsibilitySerializer
    filterset_fields = ["unit", "is_active"]
    search_fields = ["name", "code", "description"]
    audit_entity = "aor"

    def get_queryset(self):
        queryset = AreaOfResponsibility.objects.select_related(
            "unit", "unit__parent", "admin_area"
        )
        unit_ids = unit_ids_for_filter(self.request.user)
        if unit_ids is not None:
            queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        return queryset

    def get_permissions(self):
        if self.action in {
            "create",
            "destroy",
            "assign",
            "remove",
            "overlaps",
        }:
            return [IsAuthenticated(), IsAdministrator()]
        # Editing, archiving and restoring are decided per record below: the
        # Administrator may act anywhere, the Operations Center office only on
        # its own unit.
        return [IsAuthenticated()]

    def _require_maintainer(self, aor):
        """Allow the Administrator, or the owning unit's Operations Center.

        Returns True when the caller holds the narrower ``maintain_aor``
        capability only, which is what keeps a maintainer from moving a record
        to another unit or redrawing its boundary.
        """
        user = self.request.user
        if roles.has_capability(user, roles.CAP_MANAGE_AOR, aor.unit_id):
            return False
        if roles.has_capability(user, roles.CAP_MAINTAIN_AOR, aor.unit_id):
            return True
        raise PermissionDenied(
            "Only an Administrator, or the Operations Center of the AOR's own unit, "
            "may change this area."
        )

    def perform_update(self, serializer):
        aor = serializer.instance
        maintain_only = self._require_maintainer(aor)
        if maintain_only:
            # Keep the descriptive fields a unit office is trusted with and
            # drop everything that defines the coverage itself.
            for field in ("unit", "geometry", "code", "admin_area"):
                serializer.validated_data.pop(field, None)
        super().perform_update(serializer)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        """Take an AOR out of active coverage (Administrator, Operations Center)."""
        aor = self.get_object()
        self._require_maintainer(aor)
        aor_service.archive_area(
            actor=request.user,
            aor=aor,
            reason=str(request.data.get("reason") or ""),
            request=request,
        )
        return Response(
            {
                "id": aor.id,
                "is_active": False,
                "detail": f"{aor.name} archived. It stays on record and can be restored.",
            }
        )

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Return an archived AOR to active coverage (Administrator, Operations Center)."""
        aor = self.get_object()
        self._require_maintainer(aor)
        try:
            aor_service.restore_area(actor=request.user, aor=aor, request=request)
        except aor_service.AORError as exc:
            return Response(exc.as_dict(), status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "id": aor.id,
                "is_active": True,
                "detail": f"{aor.name} restored to active coverage.",
            }
        )

    def perform_create(self, serializer):
        aor = serializer.save(created_by=self.request.user)
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="aor",
            entity_id=aor.pk,
            after={"code": aor.code, "name": aor.name},
            request=self.request,
        )

    def destroy(self, request, *args, **kwargs):
        """Permanently delete one AOR record (Super Administrator only).

        Archive (``/archive/``) is the reversible action and stays with the
        Administrator and the unit's Operations Center; erasing the record is
        the seat holder's decision. The full row, geometry included, is written
        to the audit trail first so the boundary is still recoverable.
        """
        from apps.accounts.ownership import can_delete_records

        if not can_delete_records(request.user):
            raise PermissionDenied(
                "Only the Super Administrator may permanently delete an AOR "
                "record. Archive it instead."
            )
        aor = self.get_object()
        aor_service.delete_area(actor=request.user, aor=aor, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="assign")
    def assign(self, request):
        """Assign or transfer administrative areas to a unit (Administrator)."""
        serializer = serializers.AORAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        unit = data["unit"]

        scope = unit_ids_for_filter(request.user)
        if scope is not None and unit.id not in scope:
            raise PermissionDenied("That unit is outside your authorised scope.")

        try:
            if data.get("remove_psgcs"):
                removed = aor_service.remove_areas(
                    actor=request.user,
                    unit=unit,
                    psgcs=data["remove_psgcs"],
                    request=request,
                )
            else:
                removed = []
            if data.get("psgcs"):
                result = aor_service.assign_areas(
                    actor=request.user,
                    unit=unit,
                    psgcs=data["psgcs"],
                    confirm_transfer=data.get("confirm_transfer", False),
                    # No colour supplied: the unit's own colour is applied.
                    color=data.get("color") or None,
                    request=request,
                )
            else:
                result = {
                    "unit": {"id": unit.id, "name": unit.name, "acronym": unit.acronym},
                    "assigned": [],
                    "transferred": [],
                    "unchanged": [],
                }
                if removed:
                    result["removed"] = [
                        {"id": row.id, "name": row.name, "psgc": row.admin_area_id}
                        for row in removed
                    ]
        except aor_service.AORError as exc:
            return Response(exc.as_dict(), status=status.HTTP_400_BAD_REQUEST)

        result["removed"] = [
            {"id": row.id, "name": row.name, "psgc": row.admin_area_id} for row in removed
        ]
        return Response(result)

    @action(detail=True, methods=["post"])
    def remove(self, request, pk=None):
        """Remove one AOR (soft delete, audited)."""
        aor = self.get_object()
        aor_service.remove_area(actor=request.user, aor=aor, request=request)
        return Response(
            {
                "id": aor.id,
                "is_active": False,
                "detail": f"{aor.name} removed from the active coverage.",
            }
        )

    @action(detail=False, methods=["get"], url_path="coverage")
    def coverage(self, request):
        """Coverage by unit, automatically rolled up to mother units.

        A subordinate unit's areas are part of its mother unit's coverage - the
        same ground, seen from the parent - so no duplicate AOR rows exist and
        the roll-up is derived from the unit hierarchy on every request. Moving
        an area between units, deactivating it or ending a unit's coverage
        therefore updates the parent everywhere at once.

        ``areas``/``aor_count``/``coverage_km2`` keep their original meaning
        (what the unit holds itself), so existing callers are unaffected; the
        roll-up is added as ``inherited_*`` and ``total_*``, plus
        ``effective_geometry`` (the union of own and subordinate polygons) when
        ``?geometry=1`` is passed.
        """
        queryset = self.get_queryset().filter(is_active=True).select_related(
            "unit", "admin_area"
        )
        unit_filter = request.query_params.get("unit")
        if unit_filter:
            try:
                queryset = queryset.filter(unit_id=int(unit_filter))
            except (TypeError, ValueError):
                raise ValidationError({"unit": "Unit must be numeric."})

        include_geometry = (request.query_params.get("geometry") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        scope = unit_ids_for_filter(request.user)

        parents = dict(Unit.objects.values_list("id", "parent_id"))
        labels = {
            row[0]: (row[1], row[2])
            for row in Unit.objects.values_list("id", "name", "acronym")
        }

        # unit id -> {"own": [...], "inherited": [...]}; id 0 = unassigned.
        effective: dict[int, dict] = {}

        def bucket(unit_id: int) -> dict:
            return effective.setdefault(unit_id, {"own": [], "inherited": []})

        for row in queryset.order_by("unit_id", "name"):
            unit_id = row.unit_id or 0
            bucket(unit_id)["own"].append(row)

            # Walk up the hierarchy: every ancestor inside the caller's scope
            # carries these areas as inherited coverage.
            seen = set()
            parent_id = parents.get(unit_id) if unit_id else None
            while parent_id and parent_id not in seen:
                if scope is not None and parent_id not in scope:
                    break
                seen.add(parent_id)
                bucket(parent_id)["inherited"].append(row)
                parent_id = parents.get(parent_id)

        def area_payload(row):
            return {
                "aor_id": row.id,
                "psgc": row.admin_area_id,
                "name": row.admin_area.name if row.admin_area_id else row.name,
                "level": getattr(row.admin_area, "level", "") or "manual",
                "parent_name": getattr(row.admin_area, "parent_name", "") or "",
                "region_name": getattr(row.admin_area, "region_name", "") or "",
                "area_km2": getattr(row.admin_area, "area_km2", None),
                "unit_acronym": getattr(row.unit, "acronym", "") if row.unit_id else "",
                "color": row.color,
            }

        def km2(rows):
            return sum(float(getattr(row.admin_area, "area_km2", 0.0) or 0.0) for row in rows)

        results = []
        for unit_id, parts in effective.items():
            own_rows, inherited_rows = parts["own"], parts["inherited"]
            name, acronym = labels.get(unit_id, ("Unassigned", "")) if unit_id else (
                "Unassigned",
                "",
            )
            parent_id = parents.get(unit_id) if unit_id else None
            entry = {
                "unit_id": unit_id or None,
                "unit_name": name if unit_id else "Unassigned",
                "unit_acronym": acronym,
                "parent_unit_id": parent_id,
                "parent_acronym": (labels.get(parent_id, ("", ""))[1] if parent_id else ""),
                "areas": [area_payload(row) for row in own_rows],
                "aor_count": len(own_rows),
                "coverage_km2": round(km2(own_rows), 2),
                "inherited_areas": [area_payload(row) for row in inherited_rows],
                "inherited_count": len(inherited_rows),
                "inherited_from": sorted(
                    {
                        getattr(row.unit, "acronym", "")
                        for row in inherited_rows
                        if row.unit_id
                    }
                ),
                "total_aor_count": len(own_rows) + len(inherited_rows),
                "total_coverage_km2": round(km2(own_rows + inherited_rows), 2),
            }
            if include_geometry:
                entry["effective_geometry"] = geo.union_geometries(
                    [row.geometry for row in own_rows + inherited_rows], simplify=0.002
                )
            results.append(entry)

        return Response(
            {
                "count": len(results),
                "results": sorted(
                    results, key=lambda item: item["unit_acronym"] or "zz"
                ),
            }
        )

    @action(detail=False, methods=["get"], url_path="overlaps")
    def overlaps(self, request):
        """Report existing overlapping assignments; never changes them."""
        scope = unit_ids_for_filter(request.user)
        found = aor_service.list_overlaps(set(scope) if scope is not None else None)
        return Response({"count": len(found), "results": found})

    @action(detail=False, methods=["get"], url_path="at-point")
    def at_point(self, request):
        """Return every AOR whose polygon contains the given point."""
        try:
            lat = float(request.query_params["latitude"])
            lon = float(request.query_params["longitude"])
        except (KeyError, TypeError, ValueError):
            raise ValidationError(
                {"detail": "latitude and longitude query parameters are required."}
            )

        matches = []
        for aor in self.get_queryset().filter(is_active=True):
            try:
                if geo.point_in_geometry(lon, lat, aor.geometry):
                    matches.append(aor)
            except geo.GeometryError:
                continue
        return Response(
            {
                "count": len(matches),
                "results": serializers.AreaOfResponsibilitySerializer(matches, many=True).data,
            }
        )

    @action(detail=True, methods=["post"])
    def contains(self, request, pk=None):
        aor = self.get_object()
        serializer = serializers.PointQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        inside = geo.point_in_geometry(
            serializer.validated_data["longitude"],
            serializer.validated_data["latitude"],
            aor.geometry,
        )
        return Response({"aor": aor.id, "contains_point": inside})


class AdministrativeAreaViewSet(viewsets.ReadOnlyModelViewSet):
    """Searchable official province/city boundaries (spec §2 and §3).

    Read access for every authenticated user: administrative geography is
    public reference data. Operational records stay behind their own scoped
    endpoints.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = serializers.AdministrativeAreaSerializer
    lookup_field = "psgc"
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        queryset = AdministrativeArea.objects.all()
        query = (self.request.query_params.get("q") or "").strip().lower()
        level = (self.request.query_params.get("level") or "").strip().lower()
        region = self.request.query_params.get("region")
        parent = self.request.query_params.get("parent")

        if level and level != "all":
            if level not in {
                AdministrativeArea.LEVEL_PROVINCE,
                AdministrativeArea.LEVEL_DISTRICT,
                AdministrativeArea.LEVEL_CITY,
                AdministrativeArea.LEVEL_MUNICIPALITY,
            }:
                raise ValidationError({"level": "Unknown administrative level."})
            queryset = queryset.filter(level=level)
        if region:
            queryset = queryset.filter(region_psgc=region)
        if parent:
            queryset = queryset.filter(parent_psgc=parent)
        if query:
            from django.db.models import Case, IntegerField, Value, When

            queryset = queryset.filter(Q(search_text__contains=query) | Q(name__icontains=query))
            queryset = queryset.annotate(
                _rank=Case(
                    When(name__istartswith=query, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            ).order_by("_rank", "name")
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return serializers.AdministrativeAreaGeometrySerializer
        return serializers.AdministrativeAreaSerializer

    @action(detail=False, methods=["get"], url_path="at")
    def at(self, request):
        """Official area(s) containing a point (map location picker).

        Answers "where did the operator just click?" from the bundled PSGC
        boundaries: the smallest containing area is returned first (city or
        municipality, then its province, then the region) so the mission form
        can name the location without calling an external geocoder.
        """
        from apps.core import geo as geo_utils

        try:
            latitude = float(request.query_params.get("lat", ""))
            longitude = float(request.query_params.get("lon", ""))
        except (TypeError, ValueError):
            raise ValidationError({"lat": "Numeric lat and lon are required."})
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValidationError({"lat": "Coordinates are out of range."})

        candidates = AdministrativeArea.objects.filter(
            min_lat__lte=latitude,
            max_lat__gte=latitude,
            min_lon__lte=longitude,
            max_lon__gte=longitude,
        )
        matches = []
        for area in candidates:
            if geo_utils.point_in_geometry(longitude, latitude, area.geometry):
                matches.append(area)
        matches.sort(key=lambda area: area.area_km2 or float("inf"))
        return Response(
            {
                "count": len(matches),
                "latitude": latitude,
                "longitude": longitude,
                "results": [
                    {
                        "psgc": area.psgc,
                        "name": area.name,
                        "level": area.level,
                        "level_label": area.get_level_display(),
                        "parent_name": area.parent_name,
                        "region_name": area.region_name,
                    }
                    for area in matches
                ],
            }
        )

    def list(self, request, *args, **kwargs):
        try:
            limit = int(request.query_params.get("limit", 25))
        except (TypeError, ValueError):
            raise ValidationError({"limit": "Limit must be numeric."})
        limit = max(1, min(limit, 200))
        queryset = self.filter_queryset(self.get_queryset())
        rows = list(queryset[:limit])
        return Response(
            {
                "count": len(rows),
                "limit": limit,
                "results": serializers.AdministrativeAreaSerializer(rows, many=True).data,
            }
        )

    @action(detail=False, methods=["get"])
    def boundaries(self, request):
        """GeoJSON FeatureCollection for the map's boundary layer."""
        level = (request.query_params.get("level") or "province").strip().lower()
        psgc_param = (request.query_params.get("psgc") or "").strip()
        queryset = AdministrativeArea.objects.all()
        if psgc_param:
            try:
                codes = [int(code) for code in psgc_param.split(",") if code.strip()]
            except ValueError:
                raise ValidationError({"psgc": "Codes must be numeric."})
            queryset = queryset.filter(psgc__in=codes)
        elif level == "all":
            pass
        else:
            levels = {
                "province": [AdministrativeArea.LEVEL_PROVINCE],
                "district": [AdministrativeArea.LEVEL_DISTRICT],
                "city": [AdministrativeArea.LEVEL_CITY],
                "municipality": [AdministrativeArea.LEVEL_MUNICIPALITY],
                "local": [
                    AdministrativeArea.LEVEL_CITY,
                    AdministrativeArea.LEVEL_MUNICIPALITY,
                ],
            }.get(level)
            if levels is None:
                raise ValidationError(
                    {"level": "Use province, district, city, municipality, local or all."}
                )
            queryset = queryset.filter(level__in=levels)

        assigned = {
            row.admin_area_id: row.unit.acronym if row.unit_id else ""
            for row in AreaOfResponsibility.objects.filter(
                is_active=True, admin_area__isnull=False
            ).select_related("unit")
        }
        features = [
            {
                "type": "Feature",
                "properties": {
                    "psgc": area.psgc,
                    "name": area.name,
                    "level": area.level,
                    "parent_name": area.parent_name,
                    "region_name": area.region_name,
                    "assigned_unit": assigned.get(area.psgc, ""),
                },
                "geometry": area.geometry,
            }
            for area in queryset.exclude(geometry__isnull=True)
        ]
        return Response({"type": "FeatureCollection", "features": features})


class MapConfigView(APIView):
    """Basemap providers and operational layer catalogue (spec §7/§8)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(map_config.map_config_payload())


class AirEtaEstimateView(APIView):
    """Distance and estimated flight time for the air mission form.

    The form needs an ETA as soon as a planner plots a leg, but the browser
    should not re-implement the MGRS conversion used by the rest of TOWEMS.
    Each leg is measured from the MGRS grids embedded in its location text.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = serializers.AirEtaEstimateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        speed_knots = serializer.validated_data["cruising_speed_knots"]
        speed_kmh = speed_knots * 1.852
        legs = []
        for leg in serializer.validated_data["legs"]:
            origin = leg.get("origin", "")
            destination = leg.get("destination", "")
            origin_mgrs = formation_rules.mgrs_from_location(origin)
            destination_mgrs = formation_rules.mgrs_from_location(destination)
            origin_coords = formation_rules.coordinates_from_location(origin)
            destination_coords = formation_rules.coordinates_from_location(destination)
            if not origin_coords or not destination_coords:
                legs.append(
                    {
                        "resolved": False,
                        "origin_mgrs": origin_mgrs,
                        "destination_mgrs": destination_mgrs,
                        "distance_km": None,
                        "duration_minutes": None,
                        "reason": "A valid MGRS grid is required on both points.",
                    }
                )
                continue
            distance = formation_rules.distance_km(origin_coords, destination_coords)
            legs.append(
                {
                    "resolved": True,
                    "origin_mgrs": origin_mgrs,
                    "destination_mgrs": destination_mgrs,
                    "distance_km": round(distance, 3),
                    "duration_minutes": round(distance / speed_kmh * 60, 2),
                    "reason": "",
                }
            )
        return Response({"cruising_speed_knots": speed_knots, "legs": legs})


class MGRSConvertView(APIView):
    """Bidirectional MGRS conversion used by the map and coordinate entry."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = serializers.MGRSConvertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            {
                "latitude": data["resolved_latitude"],
                "longitude": data["resolved_longitude"],
                "mgrs": data["resolved_mgrs"],
            }
        )


class GroundLiveMapView(APIView):
    """In-progress ground missions with their vehicles' driver feeds.

    A working picture, not a register: a mission appears while it is active and
    the coordinates come from the driver's phone. Accounts without monitoring
    authority receive an empty picture rather than another unit's movement.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.ops import tracking as tracking_service

        return Response(tracking_service.live_board(request.user))


class OperationalPictureView(APIView):
    """Everything the dashboard map draws, in one unit-scoped payload.

    Scope is computed from the authenticated user (their unit plus authorised
    subordinates). A ``unit`` query parameter may *narrow* the view but can never
    widen it beyond that scope, so tampering with the request cannot reach an
    unrelated unit.

    Contract on positions: every coordinate carries its own timestamp and its
    source. Nothing here is live tracking; the UI labels last-known positions.
    """

    permission_classes = [IsAuthenticated]

    ALL_LAYERS = {
        "aor",
        "missions",
        "air",
        "ground",
        "units",
        "vehicles",
        "trips",
        "hazards",
        "fuel",
    }

    def get(self, request):
        from apps.command.models import HazardLocation, VehiclePosition
        from apps.core import roles as role_codes
        from apps.core.scoping import hidden_unit_ids
        from apps.legacy.models import HazardRegistry

        requested_layers = {
            layer.strip()
            for layer in (request.query_params.get("layers") or "").split(",")
            if layer.strip()
        }
        layers = requested_layers & self.ALL_LAYERS or set(self.ALL_LAYERS)

        scope_ids = unit_ids_for_filter(request.user)
        scope = set(scope_ids or [])

        narrow_to = request.query_params.get("unit")
        if narrow_to:
            try:
                narrow_id = int(narrow_to)
            except (TypeError, ValueError):
                raise ValidationError({"unit": "Unit must be numeric."})
            if narrow_id not in scope:
                raise PermissionDenied("That unit is outside your authorised scope.")
            scope = {narrow_id}

        from apps.legacy.models import Unit

        # An Operations Center watches the whole area picture: every active
        # unit's boundary and location pin is drawn, so a neighbouring area is
        # never a blank corner. Missions, mobility, air assets, fuel and
        # hazards keep the caller's own scope - widening this is a map read,
        # not access to another unit's operations.
        boundary_scope = scope
        if (
            not narrow_to
            and role_codes.ROLE_OPERATIONS_CENTER
            in role_codes.effective_roles(request.user)
        ):
            boundary_scope = set(
                Unit.objects.filter(is_active=True).values_list("id", flat=True)
            ) - hidden_unit_ids(request.user)

        units = list(
            Unit.objects.filter(id__in=scope).values("id", "name", "acronym", "parent_id")
        )
        # When the caller narrows to one unit, the header must name *that* unit,
        # not their home unit - otherwise the map claims one scope and shows another.
        scope_unit = getattr(request.user, "unit", None)
        if narrow_to:
            scope_unit = Unit.objects.filter(pk=narrow_id).first() or scope_unit

        missions = services.visible_missions(request.user).select_related(
            "unit", "risk_assessment"
        ).filter(unit_id__in=scope)
        # A completed mission is history, not part of the operational picture: its
        # pin leaves the dashboard map with its trace. The record is untouched -
        # the Missions page still lists it, and that is where an audit reads it.
        missions = missions.exclude(status="completed")
        if request.query_params.get("category"):
            missions = missions.filter(category=request.query_params["category"])
        if request.query_params.get("status"):
            missions = missions.filter(status=request.query_params["status"])
        if request.query_params.get("from"):
            missions = missions.filter(start_at__gte=request.query_params["from"])
        if request.query_params.get("to"):
            missions = missions.filter(start_at__lte=request.query_params["to"])
        missions = missions[:500]

        mission_points = []
        for mission in missions:
            if mission.latitude is None or mission.longitude is None:
                continue
            risk = getattr(mission, "risk_assessment", None)
            mission_points.append(
                {
                    "id": mission.id,
                    "mission_number": mission.mission_number,
                    "title": mission.title,
                    "category": mission.category,
                    "status": mission.status,
                    "unit_id": mission.unit_id,
                    "unit_acronym": mission.unit.acronym if mission.unit_id else "",
                    "latitude": mission.latitude,
                    "longitude": mission.longitude,
                    "mgrs": mission.mgrs,
                    "location_name": mission.location_name,
                    "start_at": mission.start_at,
                    "risk_category": getattr(risk, "risk_category", "") or "",
                }
            )

        aors_payload = []
        if "aor" in layers:
            aor_qs = AreaOfResponsibility.objects.filter(is_active=True).filter(
                Q(unit_id__in=boundary_scope) | Q(unit_id__isnull=True)
            )
            aors_payload = [
                {
                    "id": aor.id,
                    "name": aor.name,
                    "code": aor.code,
                    "color": aor.color,
                    "unit_id": aor.unit_id,
                    "unit_acronym": aor.unit.acronym if aor.unit_id else "",
                    "geometry": aor.geometry,
                    "centroid_lat": aor.centroid_lat,
                    "centroid_lon": aor.centroid_lon,
                }
                for aor in aor_qs
            ]

        positions_payload = []
        vehicles_without_position = 0
        if "vehicles" in layers:
            # Mobility means the unit's establishment, exactly as the Mobility
            # Assets register lists it. A POV is a person's car: once its trip
            # ends its last reported point is a stale dot with no mission behind
            # it, so it is never drawn here. Its trip stays on the record.
            vehicles = Vehicle.objects.filter(is_active=True, is_pov=False).filter(
                Q(unit_id__in=scope) | Q(unit_id__isnull=True)
            )
            latest: dict[int, VehiclePosition] = {}
            for row in VehiclePosition.objects.filter(vehicle__in=vehicles).order_by(
                "vehicle_id", "-reported_at"
            ):
                latest.setdefault(row.vehicle_id, row)
            vehicles_without_position = vehicles.count() - len(latest)
            for vehicle in vehicles:
                row = latest.get(vehicle.id)
                if row is None:
                    continue
                age_seconds = max(0, (_now() - row.reported_at).total_seconds())
                positions_payload.append(
                    {
                        "vehicle_id": vehicle.id,
                        "unit_id": vehicle.unit_id,
                        "registration_number": vehicle.registration_number,
                        "display_name": vehicle.display_name,
                        "vehicle_type": vehicle.vehicle_type,
                        "status": vehicle.status,
                        "unit_acronym": vehicle.unit.acronym if vehicle.unit_id else "",
                        "latitude": row.latitude,
                        "longitude": row.longitude,
                        "mgrs": row.mgrs,
                        "reported_at": row.reported_at,
                        "source": row.source,
                        "age_seconds": int(age_seconds),
                        "is_stale": age_seconds > 24 * 3600,
                        "is_live": False,
                    }
                )

        trips_payload = []
        if "trips" in layers:
            trips = (
                VehicleTrip.objects.filter(mission__unit_id__in=scope)
                .exclude(status__in=["cancelled"])
                # A completed mission leaves no trace on the operational map:
                # its route is history, and drawing it beside live work buries
                # what an operator is actually watching. Its pin is excluded
                # above for the same reason; the record lives on the Missions
                # page, which is where an audit reads it.
                .exclude(mission__status__in=["completed", "cancelled"])
                .select_related("mission")
                .prefetch_related("itinerary")
            )
            # The route layer obeys the same mission filters as the pins, so a
            # map narrowed to one category or status never shows a route that
            # belongs to a mission it has filtered out.
            if request.query_params.get("category"):
                trips = trips.filter(mission__category=request.query_params["category"])
            if request.query_params.get("status"):
                trips = trips.filter(mission__status=request.query_params["status"])
            if request.query_params.get("from"):
                trips = trips.filter(mission__start_at__gte=request.query_params["from"])
            if request.query_params.get("to"):
                trips = trips.filter(mission__start_at__lte=request.query_params["to"])
            trips = trips[:200]
            for trip in trips:
                points = [
                    {
                        "sequence": stop.sequence,
                        "location_name": stop.location_name,
                        "latitude": stop.latitude,
                        "longitude": stop.longitude,
                        "mgrs": stop.mgrs,
                        "planned_departure": stop.planned_departure,
                    }
                    for stop in trip.itinerary.all()
                    if stop.latitude is not None and stop.longitude is not None
                ]
                if len(points) < 2:
                    continue
                trips_payload.append(
                    {
                        "trip_id": trip.id,
                        "trip_number": trip.trip_number,
                        "title": trip.title,
                        "status": trip.status,
                        # The mission the route belongs to, so the map can decide
                        # whether it is still worth drawing.
                        "mission_id": trip.mission_id,
                        "mission_number": trip.mission.mission_number if trip.mission_id else "",
                        "mission_status": trip.mission.status if trip.mission_id else "",
                        "points": points,
                    }
                )

        fuel_payload = []
        fuel_without_position = 0
        if "fuel" in layers:
            from apps.ops.inventory_views import fuel_positions

            fuel_payload, fuel_without_position = fuel_positions(scope)

        hazards_payload = []
        hazards_without_location = 0
        if "hazards" in layers:
            locations = HazardLocation.objects.select_related("hazard", "unit").filter(
                Q(unit_id__in=scope) | Q(unit_id__isnull=True)
            )
            hazards_payload = [
                {
                    "hazard_id": row.hazard_id,
                    "description": getattr(row.hazard, "hazard_description", "")[:200],
                    "before_risk_rating": getattr(row.hazard, "before_risk_rating", ""),
                    "after_risk_rating": getattr(row.hazard, "after_risk_rating", ""),
                    "latitude": row.latitude,
                    "longitude": row.longitude,
                    "mgrs": row.mgrs,
                    "radius_metres": row.radius_metres,
                    "unit_acronym": row.unit.acronym if row.unit_id else "",
                }
                for row in locations[:1000]
            ]
            hazards_without_location = max(
                0, HazardRegistry.objects.count() - len(hazards_payload)
            )

        # Unit pins: the unit's own coordinate, drawn with its uploaded seal.
        units_payload = []
        if "units" in layers:
            units_payload = unit_map.markers_with_coordinates(
                Unit.objects.filter(id__in=boundary_scope, is_active=True)
            )

        center = None
        zoom = 6
        centroids = [
            (aor["centroid_lat"], aor["centroid_lon"])
            for aor in aors_payload
            if aor["centroid_lat"] is not None and aor["centroid_lon"] is not None
        ]
        if centroids:
            center = {
                "latitude": sum(c[0] for c in centroids) / len(centroids),
                "longitude": sum(c[1] for c in centroids) / len(centroids),
            }
            zoom = 9
        elif mission_points:
            center = {
                "latitude": sum(p["latitude"] for p in mission_points) / len(mission_points),
                "longitude": sum(p["longitude"] for p in mission_points)
                / len(mission_points),
            }
            zoom = 8
        elif units_payload:
            center = {
                "latitude": sum(p["latitude"] for p in units_payload) / len(units_payload),
                "longitude": sum(p["longitude"] for p in units_payload)
                / len(units_payload),
            }
            zoom = 8

        return Response(
            {
                "scope": {
                    "unit_id": getattr(scope_unit, "id", None),
                    "unit_name": getattr(scope_unit, "name", "") or "",
                    "unit_acronym": getattr(scope_unit, "acronym", "") or "",
                    "unit_ids": sorted(scope),
                    "units": units,
                    "unit_count": len(units),
                    "subordinate_count": max(0, len(units) - 1),
                },
                "center": center,
                "zoom": zoom,
                "aors": aors_payload,
                "missions": mission_points,
                "air": [p for p in mission_points if p["category"] == Mission.CATEGORY_AIR],
                "ground": [
                    p for p in mission_points if p["category"] == Mission.CATEGORY_GROUND
                ],
                "vehicle_positions": positions_payload,
                "vehicles_without_position": max(0, vehicles_without_position),
                "trip_routes": trips_payload,
                "hazards": hazards_payload,
                "hazards_without_location": hazards_without_location,
                "fuel_positions": fuel_payload,
                "fuel_without_position": fuel_without_position,
                "units": units_payload,
                "counts": {
                    "missions": len(mission_points),
                    "missions_missing_coordinates": sum(
                        1 for m in missions if m.latitude is None
                    ),
                    "aors": len(aors_payload),
                    "vehicle_positions": len(positions_payload),
                    "trip_routes": len(trips_payload),
                    "hazards": len(hazards_payload),
                    "units": len(units_payload),
                    "fuel_positions": len(fuel_payload),
                },
                "layers": sorted(layers),
                # Which boundaries the map is showing: the caller's own scope,
                # or (for an Operations Center) every active unit.
                "boundary_scope": (
                    "all_units" if boundary_scope != scope else "unit"
                ),
                "generated_at": _now(),
            }
        )


def _joined_label(parts) -> str:
    """Comma-join non-empty parts once each, keeping their order."""
    kept: list[str] = []
    for part in parts:
        value = (part or "").strip()
        if value and value.casefold() not in {item.casefold() for item in kept}:
            kept.append(value)
    return ", ".join(kept)


class PinLocationView(APIView):
    """Name a map pin for the location picker.

    The administrative area is the province the pin falls in plus the unit AOR
    that covers it ("Agusan del Sur - TOG10 AOR"). The location is the barangay
    or establishment at the pin with its city and province, so the operator sees
    where the point actually is rather than only which city contains it.

    PSGC boundaries answer offline; the barangay/establishment detail asks the
    configured geocoder and degrades to the boundary names when it is switched
    off or unreachable.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.core import geo as geo_utils
        from apps.ops import places as place_service

        try:
            latitude = float(request.query_params.get("lat", ""))
            longitude = float(request.query_params.get("lon", ""))
        except (TypeError, ValueError):
            raise ValidationError({"lat": "Numeric lat and lon are required."})
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValidationError({"lat": "Coordinates are out of range."})

        candidates = AdministrativeArea.objects.filter(
            min_lat__lte=latitude,
            max_lat__gte=latitude,
            min_lon__lte=longitude,
            max_lon__gte=longitude,
        )
        matches = [
            area
            for area in candidates
            if geo_utils.point_in_geometry(longitude, latitude, area.geometry)
        ]
        matches.sort(key=lambda area: area.area_km2 or float("inf"))
        city = next(
            (
                area
                for area in matches
                if area.level
                in {
                    AdministrativeArea.LEVEL_CITY,
                    AdministrativeArea.LEVEL_MUNICIPALITY,
                    AdministrativeArea.LEVEL_DISTRICT,
                }
            ),
            None,
        )
        province = next(
            (area for area in matches if area.level == AdministrativeArea.LEVEL_PROVINCE),
            None,
        )
        city_name = city.name if city else ""
        province_name = province.name if province else (city.parent_name if city else "")

        aor = None
        for row in AreaOfResponsibility.objects.filter(is_active=True).select_related("unit"):
            try:
                if geo_utils.point_in_geometry(longitude, latitude, row.geometry):
                    aor = row
                    break
            except geo_utils.GeometryError:
                continue

        # The picker passes the planning unit, so the answer can say whether
        # this point leaves the unit's own AOR and which unit must be told.
        handoff = None
        raw_unit = (request.query_params.get("unit") or "").strip()
        if raw_unit.isdigit():
            from apps.legacy.models import Unit

            caller_unit = Unit.objects.filter(pk=int(raw_unit)).first()
            if caller_unit is not None:
                handoff = aor_handoff.destination(caller_unit, latitude, longitude)

        if aor is None:
            admin_label = (
                f"{province_name} - No AOR assigned" if province_name else "No AOR assigned"
            )
        else:
            owner = (
                f"{aor.unit.acronym} AOR"
                if aor.unit_id and aor.unit is not None
                else (aor.name or "AOR")
            )
            admin_label = f"{province_name} - {owner}" if province_name else owner

        detail = place_service.reverse(latitude, longitude)
        # The geocoder sometimes reports the region where the province belongs
        # ("Northern Mindanao" for Cagayan de Oro). PSGC knows the province, so
        # prefer it whenever the geocoder only named the region.
        reverse_province = detail.get("province") or ""
        region_name = (city.region_name if city else "") or ""
        named_region = bool(reverse_province) and (
            (
                bool(region_name)
                and (
                    reverse_province.casefold() in region_name.casefold()
                    or region_name.casefold() in reverse_province.casefold()
                )
            )
            # A pin just outside a simplified city polygon has no city row to
            # compare with, so any name the dataset uses for a region counts.
            or AdministrativeArea.objects.filter(
                region_name__iexact=reverse_province
            ).exists()
        )
        if province_name and (not reverse_province or named_region):
            location_province = province_name
        else:
            location_province = reverse_province or province_name

        location_label = _joined_label(
            [
                detail.get("establishment"),
                detail.get("barangay"),
                detail.get("city") or city_name,
                location_province,
            ]
        ) or _joined_label([city_name, province_name])

        return Response(
            {
                "latitude": latitude,
                "longitude": longitude,
                "administrative_area": {
                    "province": province_name,
                    "city": city_name,
                    "label": admin_label,
                    "aor": None
                    if aor is None
                    else {
                        "id": aor.id,
                        "name": aor.name,
                        "unit_id": aor.unit_id,
                        "unit_acronym": aor.unit.acronym if aor.unit_id else "",
                        "unit_name": aor.unit.name if aor.unit_id else "",
                    },
                },
                "location": {
                    "barangay": detail.get("barangay", ""),
                    "establishment": detail.get("establishment", ""),
                    "city": detail.get("city") or city_name,
                    "province": location_province,
                    "label": location_label,
                },
                "aor_handoff": handoff,
                "provider": detail.get("source") or "PSGC",
                "note": detail.get("note", ""),
            }
        )


class PlaceSearchView(APIView):
    """Search businesses, buildings and facilities for the map picker.

    Backed by Google Places when a key is configured, otherwise OpenStreetMap
    Nominatim, so the map picker can resolve "SM City Butuan" and not only a
    province name. The provider key never reaches the browser: the request is
    made server-side. Any provider failure returns an empty list with a note,
    and the picker keeps its offline boundary/TOWEMS-record search.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        limit = request.query_params.get("limit") or places.DEFAULT_LIMIT
        try:
            limit = max(1, min(int(limit), 20))
        except (TypeError, ValueError):
            limit = places.DEFAULT_LIMIT

        payload = places.search(query, limit=limit)
        return Response(
            {
                "provider": payload["provider"],
                "count": len(payload["results"]),
                "results": payload["results"],
                "note": payload.get("note", ""),
            }
        )


class GeocodeSearchView(APIView):
    """Placeholder-free location search for the map.

    TOWEMS does not ship a geocoding provider by default (that would be an
    external service and a data-sharing decision). Instead this endpoint
    searches the locations already known to TOWEMS: AOR names/centroids and
    mission/trip itinerary names, so map search works offline and leaks nothing.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        if len(query) < 2:
            return Response({"count": 0, "results": []})

        results = []
        for aor in AreaOfResponsibility.objects.filter(name__icontains=query)[:10]:
            results.append(
                {
                    "label": f"AOR: {aor.name}",
                    "latitude": aor.centroid_lat,
                    "longitude": aor.centroid_lon,
                    "source": "aor",
                }
            )
        for mission in Mission.objects.filter(location_name__icontains=query)[:10]:
            if mission.latitude is not None:
                results.append(
                    {
                        "label": f"Mission: {mission.mission_number} {mission.location_name}",
                        "latitude": mission.latitude,
                        "longitude": mission.longitude,
                        "source": "mission",
                    }
                )
        return Response({"count": len(results), "results": results})


# ---------------------------------------------------------------------------
# Notifications & audit
# ---------------------------------------------------------------------------


class AirfieldViewSet(viewsets.ReadOnlyModelViewSet):
    """Airports and air strips: public reference data for the operational map.

    An airfield is infrastructure, not an operational record, so every signed-in
    account reads the same register. Nothing here is unit-scoped: a strip in a
    neighbouring province is exactly what a planner needs to know about.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = serializers.AirfieldSerializer
    filterset_fields = ["kind", "province", "is_active"]
    search_fields = ["code", "name", "municipality", "province"]
    ordering_fields = ["name", "code", "runway_length_m"]
    ordering = ["name"]

    def get_queryset(self):
        return Airfield.objects.filter(is_active=True)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """The recipient's notification inbox.

    Reading is decoupled from doing. ``mark-read`` changes ``is_read`` only; a
    notification that ``requires_action`` keeps ``action_status`` open until the
    owning workflow records completion, so refreshing, dismissing or logging out
    can never clear an outstanding task.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = serializers.NotificationSerializer
    filterset_fields = [
        "is_read",
        "level",
        "category",
        "requires_action",
        "action_status",
        "priority",
    ]
    search_fields = ["title", "message"]
    ordering = ["-created_at", "-id"]

    #: Related types whose completion is driven by a workflow, not by the bell.
    WORKFLOW_BACKED = {
        "instruction",
        "mission",
        "mrm_form",
        "inventory_request",
        "unit_change_request",
    }

    def get_queryset(self):
        queryset = Notification.objects.select_related("created_by").filter(
            recipient_id=self.request.user.id
        )
        scope = self.request.query_params.get("scope")
        if scope == "action":
            queryset = queryset.filter(
                requires_action=True,
                action_status__in=[Notification.ACTION_OPEN, Notification.ACTION_IN_PROGRESS],
            )
        elif scope == "history":
            queryset = queryset.filter(
                Q(requires_action=False)
                | Q(action_status=Notification.ACTION_COMPLETED)
            )
        elif scope == "unread":
            queryset = queryset.filter(is_read=False)
        return queryset

    @action(detail=False, methods=["get"])
    def summary(self, request):
        base = Notification.objects.filter(recipient_id=request.user.id)
        return Response(
            {
                "unread": base.filter(is_read=False).count(),
                "pending_actions": base.filter(
                    requires_action=True,
                    action_status__in=[
                        Notification.ACTION_OPEN,
                        Notification.ACTION_IN_PROGRESS,
                    ],
                ).count(),
                "open": base.filter(action_status=Notification.ACTION_OPEN).count(),
                "in_progress": base.filter(
                    action_status=Notification.ACTION_IN_PROGRESS
                ).count(),
                "completed": base.filter(
                    action_status=Notification.ACTION_COMPLETED
                ).count(),
                "total": base.count(),
            }
        )

    @action(detail=False, methods=["get"])
    def pulse(self, request):
        """A cheap "is anything new?" check the bell asks while a screen is open.

        Re-reading the inbox every few seconds is wasteful, so the bell asks for
        three numbers and - only when it says which id it saw last - the new
        urgent items themselves. Those are what a desk must be told about
        without waiting for somebody to click the bell. An emergency alert is
        one of them, so it arrives within one poll interval.
        """
        base = Notification.objects.filter(recipient_id=request.user.id)
        latest_id = base.order_by("-id").values_list("id", flat=True).first() or 0
        try:
            since = int(request.query_params.get("since") or 0)
        except (TypeError, ValueError):
            since = 0

        urgent = []
        if since:
            rows = (
                base.filter(id__gt=since, priority__in=["urgent", "high"])
                .exclude(is_read=True)
                .order_by("id")[:5]
            )
        else:
            # A console that opens after the alarm still has to hear it. An
            # unread emergency from the last quarter of an hour is repeated; a
            # merely high-priority item is not, so re-entry does not nag.
            from datetime import timedelta

            from django.utils import timezone

            rows = base.filter(
                priority="urgent",
                is_read=False,
                created_at__gte=timezone.now() - timedelta(minutes=15),
            ).order_by("id")[:5]
        if rows:
            urgent = [
                {
                    "id": row.id,
                    "title": row.title,
                    "message": row.message,
                    "level": row.level,
                    "priority": row.priority,
                    "action_url": row.action_url,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

        return Response(
            {
                "latest_id": latest_id,
                "unread": base.filter(is_read=False).count(),
                "pending_actions": base.filter(
                    requires_action=True,
                    action_status__in=[
                        Notification.ACTION_OPEN,
                        Notification.ACTION_IN_PROGRESS,
                    ],
                ).count(),
                "urgent": urgent,
            }
        )

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = _now()
            notification.save(update_fields=["is_read", "read_at"])
        return Response(serializers.NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        # Note: this clears the *unread* badge only. Pending actions stay pending.
        updated = self.get_queryset().filter(is_read=False).update(
            is_read=True, read_at=_now()
        )
        return Response({"updated": updated})

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        """Record that the recipient has seen the item and accepted it.

        Acknowledgement is stored separately from completion and never closes a
        task on its own.
        """
        notification = self.get_object()
        if not notification.requires_action:
            raise ValidationError({"detail": "This notification needs no action."})
        if notification.acknowledged_at is None:
            notification.acknowledged_at = _now()
            notification.acknowledged_by = request.user
            notification.is_read = True
            notification.read_at = notification.read_at or _now()
            if notification.action_status == Notification.ACTION_OPEN:
                notification.action_status = Notification.ACTION_IN_PROGRESS
            notification.save(
                update_fields=[
                    "acknowledged_at",
                    "acknowledged_by",
                    "is_read",
                    "read_at",
                    "action_status",
                ]
            )

        if notification.related_type == "instruction" and notification.related_id:
            from apps.command.services import acknowledge_instruction
            from apps.command.models import Instruction

            instruction = Instruction.objects.filter(pk=notification.related_id).first()
            if instruction is not None:
                try:
                    acknowledge_instruction(
                        instruction, user=request.user, note=request.data.get("note", "")
                    )
                except Exception:  # pragma: no cover - service raises CommandError only
                    pass

        log_action(
            actor=request.user,
            action="acknowledge_notification",
            entity_type="notification",
            entity_id=notification.pk,
            request=request,
        )
        return Response(serializers.NotificationSerializer(notification).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Close a notification whose action is *not* workflow-backed.

        For instructions, missions, MRMs and inventory requests the recipient
        must act through that workflow - the bell forwards them there instead of
        pretending the work is done.
        """
        notification = self.get_object()
        if not notification.requires_action:
            raise ValidationError({"detail": "This notification needs no action."})
        if notification.action_status == Notification.ACTION_COMPLETED:
            return Response(serializers.NotificationSerializer(notification).data)

        if notification.related_type in self.WORKFLOW_BACKED:
            raise ValidationError(
                {
                    "detail": (
                        "This action must be completed in its own workflow. "
                        f"Open {notification.action_url or 'the related record'} and "
                        "carry out the required step there."
                    )
                }
            )

        notification.action_status = Notification.ACTION_COMPLETED
        notification.completed_at = _now()
        notification.completed_by = request.user
        notification.completion_note = (request.data.get("note") or "")[:2000]
        notification.is_read = True
        notification.read_at = notification.read_at or _now()
        notification.save(
            update_fields=[
                "action_status",
                "completed_at",
                "completed_by",
                "completion_note",
                "is_read",
                "read_at",
            ]
        )
        log_action(
            actor=request.user,
            action="complete_notification",
            entity_type="notification",
            entity_id=notification.pk,
            note=notification.completion_note,
            request=request,
        )
        return Response(serializers.NotificationSerializer(notification).data)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsAdministrator]
    serializer_class = serializers.AuditLogSerializer
    filterset_fields = ["action", "entity_type", "actor"]
    search_fields = ["entity_id", "note", "correlation_id"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return AuditLog.objects.select_related("actor")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class ReportView(APIView):
    permission_classes = [IsAuthenticated]

    REPORTS = {
        "missions",
        "risk",
        "inventory",
        "personnel",
        "trips",
        "units",
        "aor",
    }

    def get(self, request, report: str):
        if report not in self.REPORTS:
            raise ValidationError({"detail": f"Unknown report {report!r}."})

        rows = getattr(self, f"_report_{report}")(request)
        if request.query_params.get("export") == "csv":
            return self._as_csv(report, rows)
        return Response({"report": report, "count": len(rows), "results": rows})

    # -- builders ---------------------------------------------------------

    def _report_missions(self, request):
        queryset = Mission.objects.select_related("unit", "risk_form")
        unit_ids = unit_ids_for_filter(request.user)
        if unit_ids is not None:
            queryset = queryset.filter(unit_id__in=unit_ids)
        if request.query_params.get("category"):
            queryset = queryset.filter(category=request.query_params["category"])
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        return [
            {
                "mission_number": m.mission_number,
                "category": m.category,
                "title": m.title,
                "unit": m.unit.acronym if m.unit_id else "",
                "status": m.status,
                "start_at": m.start_at,
                "location": m.location_name,
                "risk_category": m.risk_assessment.risk_category
                if hasattr(m, "risk_assessment")
                else "",
            }
            for m in queryset[:2000]
        ]

    def _report_risk(self, request):
        queryset = MRMForm.objects.all()
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        return [
            {
                "mrm_number": f.mrm_number,
                "activity_mission": f.activity_mission,
                "status": f.status,
                "review_status": f.review_status,
                "authority_level": f.authority_level,
                "risk_category": f.risk_category,
                "total_percent": f.total_percent,
                "evaluator": f.reviewer_name or "",
                "date_created": f.date_created,
                "authority_approved": f.authority_approved,
            }
            for f in queryset[:2000]
        ]

    def _report_inventory(self, request):
        queryset = InventoryItem.objects.select_related("category", "unit")
        if not _can_see_sensitive(request.user):
            queryset = queryset.filter(is_sensitive=False, category__is_sensitive=False)
        unit_ids = unit_ids_for_filter(request.user)
        if unit_ids is not None:
            queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        return [
            {
                "name": i.name,
                "category": i.category.name,
                "asset_tag": i.asset_tag or "",
                "serial_number": i.serial_number,
                "unit": i.unit.acronym if i.unit_id else "",
                "storage_location": i.storage_location,
                "quantity_total": i.quantity_total,
                "quantity_available": i.quantity_available,
                "serviceability": i.serviceability,
            }
            for i in queryset[:2000]
        ]

    def _report_personnel(self, request):
        queryset = User.objects.select_related("unit").filter(is_active=True)
        unit_ids = unit_ids_for_filter(request.user)
        if unit_ids is not None:
            queryset = queryset.filter(unit_id__in=unit_ids)
        return [
            {
                "serial_number": u.serial_number,
                "full_name": u.full_name,
                "rank": u.rank or "",
                "designation": u.designation or "",
                "authority": u.authority,
                "category": u.category,
                "unit": u.unit.acronym if u.unit_id else "",
            }
            for u in queryset[:2000]
        ]

    def _report_trips(self, request):
        queryset = VehicleTrip.objects.select_related("mission").prefetch_related(
            "itinerary", "vehicles"
        )
        return [
            {
                "trip_number": t.trip_number,
                "title": t.title,
                "status": t.status,
                "mission": t.mission.mission_number if t.mission_id else "",
                "departure_at": t.departure_at,
                "return_at": t.return_at,
                "vehicles": t.vehicles.count(),
                "itinerary_stops": t.itinerary.count(),
            }
            for t in queryset[:2000]
        ]

    def _report_units(self, request):
        return [
            {
                "name": u.name,
                "acronym": u.acronym,
                "category": u.category,
                "parent": u.parent.acronym if u.parent_id else "",
                "commanding_officer": u.commanding_officer or "",
                "is_active": u.is_active,
            }
            for u in Unit.objects.select_related("parent").all()[:2000]
        ]

    def _report_aor(self, request):
        queryset = AreaOfResponsibility.objects.select_related("unit")
        unit_ids = unit_ids_for_filter(request.user)
        if unit_ids is not None:
            queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
        return [
            {
                "code": a.code,
                "name": a.name,
                "unit": a.unit.acronym if a.unit_id else "",
                "centroid_lat": a.centroid_lat,
                "centroid_lon": a.centroid_lon,
                "is_active": a.is_active,
            }
            for a in queryset[:2000]
        ]

    # -- export -----------------------------------------------------------

    def _as_csv(self, name: str, rows: list[dict]) -> HttpResponse:
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="toms_{name}.csv"'
        if not rows:
            return response
        writer = csv.DictWriter(response, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value)
                    for key, value in row.items()
                }
            )
        return response


class HealthView(APIView):
    """Unauthenticated liveness probe - deliberately reveals nothing."""

    permission_classes: list = []
    authentication_classes: list = []

    def get(self, request):
        return Response({"status": "ok"})
