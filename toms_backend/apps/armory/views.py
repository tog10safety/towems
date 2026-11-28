"""Armory API endpoints."""

from __future__ import annotations

import datetime as dt

from django.db import transaction as db_transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core import roles, timeutils
from apps.core.audit import snapshot
from apps.core.permissions import can
from apps.core.scoping import accessible_unit_ids
from apps.legacy.models import Unit
from apps.ops.models import Mission, MissionPersonnel

from . import reports, services
from .models import (
    Ammunition,
    ArmoryTransaction,
    ArmoryTransactionLine,
    Firearm,
    FirearmInspection,
    FirearmSerialChange,
    PersonnelFirearmAssignment,
)
from .permissions import (
    ArmoryInventoryPermission,
    ArmoryReadOnlyPermission,
    CanManageArmory,
    CanProcessArmory,
    CanViewArmory,
)
from .serializers import (
    AmmunitionSerializer,
    ArmoryTransactionSerializer,
    AssignmentSerializer,
    AssignmentWriteSerializer,
    FirearmSerializer,
    FirearmSummarySerializer,
    InspectionSerializer,
    InspectionWriteSerializer,
    IssueSerializer,
    MissionOptionSerializer,
    ReturnSerializer,
    SerialChangeSerializer,
    SerialCorrectionSerializer,
    StockAdjustmentSerializer,
    TransactionLineSerializer,
    TransferSerializer,
    UserSummarySerializer,
)


def _validation(exc: services.ArmoryError) -> ValidationError:
    return ValidationError({"detail": str(exc)})


def _apply_date_range(queryset, params, field: str = "transaction_at"):
    start = parse_date(params.get("start") or "")
    end = parse_date(params.get("end") or "")
    if start:
        queryset = queryset.filter(**{f"{field}__date__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__date__lte": end})
    return queryset


def _firearm_queryset(user):
    return services.scoped_firearms(user)


class FirearmViewSet(viewsets.ModelViewSet):
    """The controlled firearm register."""

    serializer_class = FirearmSerializer
    permission_classes = [ArmoryInventoryPermission]
    filterset_fields = []
    search_fields = []

    def get_queryset(self):
        queryset = _firearm_queryset(self.request.user)
        params = self.request.query_params
        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(firearm_id__icontains=search)
                | Q(serial_number__icontains=search)
                | Q(butt_number__icontains=search)
                | Q(manufacturer__icontains=search)
                | Q(model__icontains=search)
                | Q(caliber__icontains=search)
                | Q(assigned_personnel__first_name__icontains=search)
                | Q(assigned_personnel__last_name__icontains=search)
            )
        for param, field in (
            ("type", "firearm_type"),
            ("condition", "condition_status"),
            ("custody", "custody_status"),
        ):
            value = (params.get(param) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        unit = (params.get("unit") or "").strip()
        if unit.isdigit():
            queryset = queryset.filter(assigned_unit_id=int(unit))
        if params.get("available") in {"1", "true", "yes"}:
            queryset = queryset.filter(
                custody_status=Firearm.CUSTODY_STORED,
                condition_status=Firearm.CONDITION_SERVICEABLE,
            )
        return queryset.order_by("firearm_type", "butt_number", "firearm_id")

    def perform_create(self, serializer):
        instance = serializer.save()
        services._audit(
            action="register_firearm",
            entity_type="armory_firearm",
            entity_id=instance.pk,
            actor=self.request.user,
            before={},
            after=snapshot(
                instance,
                [
                    "firearm_id",
                    "serial_number",
                    "butt_number",
                    "firearm_type",
                    "manufacturer",
                    "model",
                    "caliber",
                    "assigned_unit_id",
                    "condition_status",
                ],
            ),
            reference=instance.firearm_id,
            note="Firearm registered.",
            request=self.request,
        )

    def perform_update(self, serializer):
        before = snapshot(
            serializer.instance,
            [
                "firearm_id",
                "serial_number",
                "butt_number",
                "firearm_type",
                "manufacturer",
                "model",
                "caliber",
                "condition_status",
                "storage_location",
                "assigned_unit_id",
            ],
        )
        instance = serializer.save()
        services._audit(
            action="update_firearm",
            entity_type="armory_firearm",
            entity_id=instance.pk,
            actor=self.request.user,
            before=before,
            after=snapshot(
                instance,
                [
                    "firearm_id",
                    "serial_number",
                    "butt_number",
                    "firearm_type",
                    "manufacturer",
                    "model",
                    "caliber",
                    "condition_status",
                    "storage_location",
                    "assigned_unit_id",
                ],
            ),
            reference=instance.firearm_id,
            note="Firearm register updated.",
            request=self.request,
        )

    def perform_destroy(self, instance):
        if (
            instance.transaction_lines.exists()
            or instance.assignments.exists()
            or instance.inspections.exists()
            or instance.serial_changes.exists()
        ):
            raise ValidationError(
                {
                    "detail": (
                        "This firearm has assignment, inspection, serial or transaction "
                        "history and cannot be deleted. Retire it by setting its custody "
                        "status to retired instead."
                    )
                }
            )
        before = snapshot(instance, ["firearm_id", "serial_number", "butt_number"])
        entity_id = instance.pk
        reference = instance.firearm_id
        instance.delete()
        services._audit(
            action="delete_firearm",
            entity_type="armory_firearm",
            entity_id=entity_id,
            actor=self.request.user,
            before=before,
            after={},
            reference=reference,
            note="Unused firearm registration deleted.",
            request=self.request,
        )

    @action(detail=True, methods=["get"])
    def inspections(self, request, pk=None):
        firearm = self.get_object()
        rows = firearm.inspections.select_related("inspected_by").all()
        return Response(InspectionSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"])
    def inspect(self, request, pk=None):
        firearm = self.get_object()
        payload = InspectionWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        sync_uuid = payload.validated_data.get("client_uuid")
        if sync_uuid:
            existing = FirearmInspection.objects.filter(
                sync_uuid=sync_uuid
            ).first()
            if existing is not None:
                if existing.firearm_id != firearm.id:
                    raise ValidationError(
                        {"detail": "That client UUID belongs to another firearm."}
                    )
                return Response(
                    {
                        "firearm": FirearmSerializer(
                            firearm, context={"request": request}
                        ).data,
                        "inspection": InspectionSerializer(existing).data,
                    }
                )
        try:
            inspection = services.inspect_firearm(
                firearm=firearm,
                condition_status=payload.validated_data["condition_status"],
                remarks=payload.validated_data.get("remarks", ""),
                inspected_at=payload.validated_data.get("inspected_at"),
                next_inspection_date=payload.validated_data.get("next_inspection_date"),
                sync_uuid=sync_uuid,
                actor=request.user,
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        firearm.refresh_from_db()
        return Response(
            {
                "firearm": FirearmSerializer(firearm, context={"request": request}).data,
                "inspection": InspectionSerializer(inspection).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="serial-changes")
    def serial_changes(self, request, pk=None):
        firearm = self.get_object()
        return Response(
            SerialChangeSerializer(
                firearm.serial_changes.select_related("changed_by"), many=True
            ).data
        )

    @action(detail=True, methods=["post"], url_path="correct-serial")
    def correct_serial(self, request, pk=None):
        firearm = self.get_object()
        payload = SerialCorrectionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            firearm = services.correct_firearm_serial(
                firearm=firearm,
                serial_number=payload.validated_data["serial_number"],
                reason=payload.validated_data["reason"],
                actor=request.user,
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        change = firearm.serial_changes.select_related("changed_by").first()
        return Response(
            {
                "firearm": FirearmSerializer(firearm, context={"request": request}).data,
                "change": SerialChangeSerializer(change).data if change else None,
            }
        )

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        firearm = self.get_object()
        payload = TransferSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        unit = get_object_or_404(Unit, pk=payload.validated_data["assigned_unit"])
        try:
            transaction = services.transfer_firearm(
                firearm=firearm,
                assigned_unit=unit,
                storage_location=payload.validated_data.get("storage_location", "Armory"),
                reason=payload.validated_data["reason"],
                supporting_documents=payload.validated_data.get(
                    "supporting_documents", ""
                ),
                actor=request.user,
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        transaction = services.scoped_transactions(request.user).get(pk=transaction.pk)
        return Response(
            ArmoryTransactionSerializer(transaction, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AmmunitionViewSet(viewsets.ModelViewSet):
    """Ammunition lots and their live stock balance."""

    serializer_class = AmmunitionSerializer
    permission_classes = [ArmoryInventoryPermission]

    def get_queryset(self):
        queryset = services.scoped_ammunition(self.request.user)
        params = self.request.query_params
        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(ammunition_id__icontains=search)
                | Q(nomenclature__icontains=search)
                | Q(caliber__icontains=search)
                | Q(ammunition_type__icontains=search)
                | Q(lot_number__icontains=search)
                | Q(storage_location__icontains=search)
            )
        caliber = (params.get("caliber") or "").strip()
        if caliber:
            queryset = queryset.filter(caliber__iexact=caliber)
        condition = (params.get("serviceability") or "").strip()
        if condition:
            queryset = queryset.filter(serviceability=condition)
        unit = (params.get("unit") or "").strip()
        if unit.isdigit():
            queryset = queryset.filter(owning_unit_id=int(unit))
        if params.get("available") in {"1", "true", "yes"}:
            queryset = queryset.filter(
                serviceability=Ammunition.CONDITION_SERVICEABLE,
                stock_balance__gt=0,
            ).filter(
                Q(expiration_date__isnull=True)
                | Q(expiration_date__gte=services.timeutils.today())
            )
        return queryset.order_by("caliber", "nomenclature", "lot_number")

    def perform_create(self, serializer):
        instance = serializer.save()
        services._audit(
            action="register_ammunition",
            entity_type="armory_ammunition",
            entity_id=instance.pk,
            actor=self.request.user,
            before={},
            after=snapshot(
                instance,
                [
                    "ammunition_id",
                    "nomenclature",
                    "caliber",
                    "lot_number",
                    "quantity_received",
                    "stock_balance",
                    "owning_unit_id",
                ],
            ),
            reference=instance.ammunition_id,
            note="Ammunition lot registered.",
            request=self.request,
        )

    def perform_update(self, serializer):
        before = snapshot(
            serializer.instance,
            [
                "ammunition_id",
                "nomenclature",
                "caliber",
                "lot_number",
                "serviceability",
                "storage_location",
                "expiration_date",
            ],
        )
        instance = serializer.save()
        services._audit(
            action="update_ammunition",
            entity_type="armory_ammunition",
            entity_id=instance.pk,
            actor=self.request.user,
            before=before,
            after=snapshot(
                instance,
                [
                    "ammunition_id",
                    "nomenclature",
                    "caliber",
                    "lot_number",
                    "serviceability",
                    "storage_location",
                    "expiration_date",
                ],
            ),
            reference=instance.ammunition_id,
            note="Ammunition lot updated.",
            request=self.request,
        )

    @action(detail=True, methods=["post"], url_path="adjust")
    def adjust(self, request, pk=None):
        ammunition = self.get_object()
        payload = StockAdjustmentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        sync_uuid = payload.validated_data.get("client_uuid")
        if sync_uuid:
            existing = services.scoped_transactions(request.user).filter(
                sync_uuid=sync_uuid
            ).first()
            if existing is not None:
                ammunition.refresh_from_db()
                return Response(
                    {
                        "ammunition": AmmunitionSerializer(
                            ammunition, context={"request": request}
                        ).data,
                        "transaction": ArmoryTransactionSerializer(
                            existing, context={"request": request}
                        ).data,
                    }
                )
        try:
            transaction = services.adjust_ammunition_stock(
                ammunition=ammunition,
                new_balance=payload.validated_data["stock_balance"],
                reason=payload.validated_data["reason"],
                supporting_documents=payload.validated_data.get(
                    "supporting_documents", ""
                ),
                sync_uuid=sync_uuid,
                actor=request.user,
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        ammunition.refresh_from_db()
        return Response(
            {
                "ammunition": AmmunitionSerializer(
                    ammunition, context={"request": request}
                ).data,
                "transaction": ArmoryTransactionSerializer(
                    transaction, context={"request": request}
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Personnel designation history; writes go through assign/release actions."""

    serializer_class = AssignmentSerializer
    permission_classes = [ArmoryReadOnlyPermission]

    def get_queryset(self):
        queryset = services.scoped_assignments(self.request.user)
        params = self.request.query_params
        active = params.get("active")
        if active == "all":
            pass
        elif active in {"0", "false", "no"}:
            queryset = queryset.filter(is_active=False)
        else:
            queryset = queryset.filter(is_active=True)
        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(user__first_name__icontains=search)
                | Q(user__middle_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(user__rank__icontains=search)
                | Q(user__serial_number__icontains=search)
                | Q(firearm__firearm_id__icontains=search)
                | Q(firearm__butt_number__icontains=search)
                | Q(firearm__serial_number__icontains=search)
            )
        user_id = (params.get("personnel") or "").strip()
        if user_id.isdigit():
            queryset = queryset.filter(user_id=int(user_id))
        return queryset

    def get_permissions(self):
        if self.action in {"assign", "release"}:
            return [IsAuthenticated(), CanManageArmory()]
        return [IsAuthenticated(), ArmoryReadOnlyPermission()]

    @action(detail=False, methods=["post"])
    def assign(self, request):
        payload = AssignmentWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = get_object_or_404(
            User.objects.select_related("unit"), pk=payload.validated_data["personnel"]
        )
        sync_uuid = payload.validated_data.get("client_uuid")
        if sync_uuid:
            existing = services.scoped_assignments(request.user).filter(
                sync_uuid=sync_uuid
            ).order_by("assignment_type")
            if existing.exists():
                return Response(AssignmentSerializer(existing, many=True).data)
        try:
            rows = services.assign_designated_firearms(
                user=user,
                rifle_firearm_id=payload.validated_data.get("rifle_firearm_id"),
                pistol_firearm_id=payload.validated_data.get("pistol_firearm_id"),
                notes=payload.validated_data.get("notes", ""),
                sync_uuid=sync_uuid,
                actor=request.user,
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        rows = (
            PersonnelFirearmAssignment.objects.select_related(
                "user", "user__unit", "firearm", "firearm__assigned_unit"
            )
            .filter(pk__in=[row.pk for row in rows])
            .order_by("assignment_type")
        )
        return Response(
            AssignmentSerializer(rows, many=True).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        assignment = self.get_object()
        try:
            assignment = services.release_assignment(
                assignment=assignment,
                actor=request.user,
                reason=request.data.get("reason", ""),
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        return Response(AssignmentSerializer(assignment).data)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """The immutable accountability transaction register."""

    serializer_class = ArmoryTransactionSerializer
    permission_classes = [ArmoryReadOnlyPermission]

    def get_queryset(self):
        queryset = services.scoped_transactions(self.request.user)
        params = self.request.query_params
        txn_type = (params.get("type") or "").strip()
        if txn_type:
            queryset = queryset.filter(transaction_type=txn_type)
        status_value = (params.get("status") or "").strip()
        if status_value:
            queryset = queryset.filter(status=status_value)
        mission = (params.get("mission") or "").strip()
        if mission.isdigit():
            queryset = queryset.filter(mission_id=int(mission))
        personnel = (params.get("personnel") or "").strip()
        if personnel.isdigit():
            queryset = queryset.filter(personnel_id=int(personnel))
        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(reference_number__icontains=search)
                | Q(mission_order_reference__icontains=search)
                | Q(mission__mission_number__icontains=search)
                | Q(mission__title__icontains=search)
                | Q(personnel_name__icontains=search)
                | Q(rank__icontains=search)
                | Q(unit_name__icontains=search)
                | Q(lines__firearm_id_snapshot__icontains=search)
                | Q(lines__butt_number_snapshot__icontains=search)
                | Q(lines__serial_number_snapshot__icontains=search)
                | Q(lines__ammunition_name__icontains=search)
                | Q(lines__lot_number__icontains=search)
            ).distinct()
        queryset = _apply_date_range(queryset, params)
        return queryset


class DashboardView(APIView):
    permission_classes = [IsAuthenticated, CanViewArmory]

    def get(self, request):
        data = services.dashboard(request.user)
        data["recent_transactions"] = ArmoryTransactionSerializer(
            data["recent_transactions"], many=True, context={"request": request}
        ).data
        return Response(data)


class MyArmoryView(APIView):
    """A person's own designation and receipts, independent of register rights."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        assignments = (
            PersonnelFirearmAssignment.objects.select_related(
                "firearm", "firearm__assigned_unit"
            )
            .filter(user=request.user, is_active=True)
            .order_by("assignment_type")
        )
        transactions = (
            ArmoryTransaction.objects.select_related(
                "mission", "personnel", "unit", "releasing_armorer",
                "receiving_personnel", "approving_authority", "return_of",
            )
            .prefetch_related("lines", "lines__firearm", "lines__ammunition")
            .filter(
                Q(personnel=request.user) | Q(receiving_personnel=request.user)
            )
            .order_by("-transaction_at")[:50]
        )
        return Response(
            {
                "assignments": AssignmentSerializer(assignments, many=True).data,
                "transactions": ArmoryTransactionSerializer(
                    transactions, many=True, context={"request": request}
                ).data,
                "open_issues": [
                    row
                    for row in ArmoryTransactionSerializer(
                        transactions, many=True, context={"request": request}
                    ).data
                    if row["transaction_type"] == "issuance" and row["status"] == "open"
                ],
                "can_view_register": can(request.user, roles.CAP_VIEW_ARMORY),
            }
        )


class PersonnelLookupView(APIView):
    """Search existing TOWEMS personnel for a designation."""

    permission_classes = [IsAuthenticated, CanProcessArmory]

    def get(self, request):
        ids = services.user_unit_ids(request.user)
        if not ids:
            return Response({"count": 0, "results": []})
        queryset = User.objects.select_related("unit").filter(
            unit_id__in=ids, is_active=True
        )
        search = (request.query_params.get("search") or "").strip()
        if search:
            # Every term must match at least one searchable field. This lets
            # "Barri 903278", "SSg Barri" and "TOG10 Barri" work even when the
            # terms land in different columns, while still matching across the
            # whole scoped roster before the response is capped.
            for term in search.split():
                queryset = queryset.filter(
                    Q(first_name__icontains=term)
                    | Q(middle_name__icontains=term)
                    | Q(last_name__icontains=term)
                    | Q(rank__icontains=term)
                    | Q(serial_number__icontains=term)
                    | Q(unit__name__icontains=term)
                    | Q(unit__acronym__icontains=term)
                )
        total = queryset.count()
        try:
            limit = int(
                request.query_params.get("limit")
                or request.query_params.get("page_size")
                or 100
            )
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 200))
        people = list(queryset.order_by("last_name", "first_name", "id")[:limit])
        assignments = (
            PersonnelFirearmAssignment.objects.select_related("firearm")
            .filter(is_active=True, user_id__in=[row.id for row in people])
        )
        by_user: dict[int, dict[str, PersonnelFirearmAssignment]] = {}
        for row in assignments:
            by_user.setdefault(row.user_id, {})[row.assignment_type] = row
        results = []
        for person in people:
            item = UserSummarySerializer(person).data
            item["assignments"] = [
                {
                    "assignment_type": key,
                    "firearm_id": row.firearm.firearm_id,
                    "butt_number": row.firearm.butt_number,
                }
                for key, row in by_user.get(person.id, {}).items()
            ]
            results.append(item)
        return Response({"count": total, "results": results})


class ApprovedMissionView(APIView):
    """Approved mission orders the armorer may draw from."""

    permission_classes = [IsAuthenticated, CanProcessArmory]

    def get(self, request):
        ids = services.user_unit_ids(request.user)
        if not ids:
            return Response({"count": 0, "results": []})
        queryset = Mission.objects.select_related("unit").filter(
            unit_id__in=ids,
            status__in=["approved", "active"],
            approval_status=Mission.APPROVAL_APPROVED,
            approved_revision=F("revision"),
            mission_order_status=Mission.MISSION_ORDER_APPROVED,
            mission_order_signed_revision=F("revision"),
            is_held=False,
        )
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(mission_number__icontains=search)
                | Q(title__icontains=search)
                | Q(location_name__icontains=search)
            )
        queryset = queryset.order_by("-start_at", "-id")[:100]
        return Response(
            {
                "count": len(queryset),
                "results": MissionOptionSerializer(queryset, many=True).data,
            }
        )


class ArmorySyncSnapshotView(APIView):
    """Scoped armory data for an offline desktop client.

    The snapshot is read-only and deliberately contains no credentials. The
    desktop stores the central ids alongside its own rows so a later upload can
    name the exact personnel, firearms and mission records.
    """

    permission_classes = [IsAuthenticated, CanProcessArmory]

    def get(self, request):
        ids = services.user_unit_ids(request.user)
        if not ids:
            return Response(
                {
                    "schema": 1,
                    "server_time": timeutils.now().isoformat(),
                    "units": [],
                    "personnel": [],
                    "firearms": [],
                    "ammunition": [],
                    "assignments": [],
                    "missions": [],
                }
            )
        units = list(
            Unit.objects.filter(id__in=ids, is_active=True)
            .order_by("name")
            .values("id", "name", "acronym", "parent_id")
        )
        personnel = (
            User.objects.select_related("unit")
            .filter(unit_id__in=ids, is_active=True)
            .order_by("last_name", "first_name", "id")
        )
        firearms = services.scoped_firearms(request.user).order_by(
            "firearm_type", "butt_number", "id"
        )
        ammunition = services.scoped_ammunition(request.user).order_by(
            "caliber", "nomenclature", "lot_number", "id"
        )
        assignments = services.scoped_assignments(request.user).order_by(
            "-is_active", "user_id", "assignment_type", "-assigned_at"
        )
        missions = list(
            Mission.objects.select_related("unit")
            .filter(unit_id__in=ids)
            .order_by("-start_at", "-id")[:500]
        )
        mission_rows = {row["id"]: row for row in MissionOptionSerializer(missions, many=True).data}
        rosters: dict[int, list[dict]] = {}
        for row in MissionPersonnel.objects.filter(
            mission_id__in=[mission.id for mission in missions], is_active=True
        ).values("mission_id", "user_id", "role", "custom_role"):
            rosters.setdefault(row["mission_id"], []).append(
                {
                    "personnel": row["user_id"],
                    "role": row["role"],
                    "custom_role": row["custom_role"] or "",
                }
            )
        for mission in missions:
            mission_rows[mission.id]["order_ready"] = not services.mission_order_error(mission)
            mission_rows[mission.id]["personnel"] = rosters.get(mission.id, [])
        return Response(
            {
                "schema": 1,
                "server_time": timeutils.now().isoformat(),
                "units": units,
                "personnel": UserSummarySerializer(personnel, many=True).data,
                "firearms": FirearmSerializer(
                    firearms, many=True, context={"request": request}
                ).data,
                "ammunition": AmmunitionSerializer(
                    ammunition, many=True, context={"request": request}
                ).data,
                "assignments": AssignmentSerializer(assignments, many=True).data,
                "missions": list(mission_rows.values()),
            }
        )


class MissionEligibilityView(APIView):
    permission_classes = [IsAuthenticated, CanProcessArmory]

    def get(self, request):
        mission_id = (request.query_params.get("mission") or "").strip()
        if not mission_id:
            # No Mission Order selected: offer the scoped people who hold
            # active designations for a standalone withdrawal.
            return Response(
                {
                    "mission": None,
                    "order_approved": True,
                    "order_error": "",
                    "personnel": services.eligible_personnel(
                        actor=request.user
                    ),
                }
            )
        if not mission_id.isdigit():
            raise ValidationError({"mission": "Choose an approved mission."})
        mission = (
            Mission.objects.select_related("unit", "mission_order_signed_by", "approved_by")
            .filter(pk=int(mission_id))
            .first()
        )
        if mission is None or mission.unit_id not in services.user_unit_ids(request.user):
            raise Http404
        order_error = services.mission_order_error(mission)
        return Response(
            {
                "mission": MissionOptionSerializer(mission).data,
                "order_approved": not order_error,
                "order_error": order_error,
                "personnel": services.eligible_personnel(mission),
            }
        )


class IssueView(APIView):
    permission_classes = [IsAuthenticated, CanProcessArmory]

    def post(self, request):
        payload = IssueSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        sync_uuid = payload.validated_data.get("client_uuid")
        if sync_uuid:
            existing = services.scoped_transactions(request.user).filter(
                sync_uuid=sync_uuid
            ).first()
            if existing is not None:
                return Response(
                    ArmoryTransactionSerializer(
                        existing, context={"request": request}
                    ).data
                )
        mission = None
        mission_id = payload.validated_data.get("mission")
        if mission_id:
            mission = (
                Mission.objects.select_related(
                    "unit", "mission_order_signed_by", "approved_by"
                )
                .filter(pk=mission_id)
                .first()
            )
            if (
                mission is None
                or mission.unit_id not in services.user_unit_ids(request.user)
            ):
                raise NotFound("Mission not found in your armory scope.")
        personnel = User.objects.select_related("unit").filter(
            pk=payload.validated_data["personnel"]
        ).first()
        if personnel is None:
            raise NotFound("Personnel record not found.")
        try:
            txn = services.issue_firearms(
                mission=mission,
                personnel=personnel,
                firearms=payload.validated_data["firearms"],
                ammunition=payload.validated_data.get("ammunition", []),
                identity_verified=payload.validated_data["identity_verified"],
                acknowledged=payload.validated_data["acknowledged"],
                acknowledgement_name=payload.validated_data["acknowledgement_name"],
                purpose=payload.validated_data.get("purpose", ""),
                inspection_remarks=payload.validated_data.get("inspection_remarks", ""),
                supporting_documents=payload.validated_data.get("supporting_documents", ""),
                sync_uuid=sync_uuid,
                actor=request.user,
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        txn = services.scoped_transactions(request.user).get(pk=txn.pk)
        return Response(
            ArmoryTransactionSerializer(txn, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ReturnView(APIView):
    permission_classes = [IsAuthenticated, CanProcessArmory]

    def post(self, request):
        payload = ReturnSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        issuance = (
            ArmoryTransaction.objects.select_related("mission", "personnel", "unit")
            .filter(
                pk=payload.validated_data["issuance"],
                transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
            )
            .first()
        )
        if issuance is None or issuance.unit_id not in services.user_unit_ids(request.user):
            raise NotFound("Issuance not found in your armory scope.")
        sync_uuid = payload.validated_data.get("client_uuid")
        if sync_uuid:
            existing = services.scoped_transactions(request.user).filter(
                sync_uuid=sync_uuid
            ).first()
            if existing is not None:
                return Response(
                    ArmoryTransactionSerializer(
                        existing, context={"request": request}
                    ).data
                )
        try:
            txn = services.return_firearms(
                issuance=issuance,
                firearms=payload.validated_data["firearms"],
                ammunition=payload.validated_data.get("ammunition", []),
                identity_verified=payload.validated_data["identity_verified"],
                acknowledged=payload.validated_data["acknowledged"],
                acknowledgement_name=payload.validated_data["acknowledgement_name"],
                inspection_remarks=payload.validated_data.get("inspection_remarks", ""),
                discrepancy_notes=payload.validated_data.get("discrepancy_notes", ""),
                supporting_documents=payload.validated_data.get("supporting_documents", ""),
                sync_uuid=sync_uuid,
                actor=request.user,
                request=request,
            )
        except services.ArmoryError as exc:
            raise _validation(exc)
        txn = services.scoped_transactions(request.user).get(pk=txn.pk)
        return Response(
            ArmoryTransactionSerializer(txn, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ReceiptView(APIView):
    """Download the electronic issuance/deposit receipt as a PDF."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        transaction = (
            ArmoryTransaction.objects.select_related(
                "mission", "personnel", "unit", "releasing_armorer",
                "receiving_personnel", "approving_authority", "return_of",
            )
            .prefetch_related("lines", "lines__firearm", "lines__ammunition")
            .filter(pk=pk)
            .first()
        )
        if transaction is None:
            raise Http404
        owns = transaction.personnel_id == request.user.id or transaction.receiving_personnel_id == request.user.id
        allowed = can(request.user, roles.CAP_VIEW_ARMORY)
        if not (owns or allowed):
            raise Http404
        if not owns and transaction.unit_id not in services.user_unit_ids(request.user):
            raise Http404
        return reports.receipt_pdf_response(transaction, request.user)


class ReportView(APIView):
    permission_classes = [IsAuthenticated, CanViewArmory]

    def get(self, request, report_slug):
        export = (request.query_params.get("export") or "pdf").lower()
        if export not in {"pdf", "xlsx"}:
            raise ValidationError({"export": "Choose pdf or xlsx."})
        try:
            return reports.build_report_response(
                report_slug, user=request.user, params=request.query_params, export=export
            )
        except KeyError:
            raise Http404("Unknown armory report.")
        except services.ArmoryError as exc:
            raise _validation(exc)
