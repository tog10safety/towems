"""Command structure, delegations, office/sections and instruction APIs."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import User
from apps.command import offices, serializers, services
from apps.command.models import (
    ActingAppointment,
    ApprovalRule,
    HazardLocation,
    Instruction,
    MissionRouting,
    OfficeSection,
    SectionAssignment,
    UnitAppointment,
    VehiclePosition,
)
from apps.core.audit import log_action, snapshot
from apps.core.permissions import (
    IsAdministrator,
    CanApproveInventory,
    CanIssueInstructions,
    CanManageActingAuthority,
    CanManageAppointments,
    CanManageSections,
    CanViewResources,
    IsAuthenticated65,
    authority,
)
from apps.core.scoping import accessible_unit_ids, own_unit_tree_ids


def scoped_units(request) -> set[int]:
    """Unit ids the caller may touch in the *new* TOWEMS modules.

    Unlike the legacy-compatible ``unit_ids_for_filter`` (which returns ``None``
    for an unscoped account), a new module with no scope returns an empty set:
    absence of a unit is not permission to see the organisation.
    """
    return accessible_unit_ids(request.user)


def require_unit(request, unit_id) -> None:
    if unit_id is None:
        raise ValidationError({"unit_id": "A unit is required."})
    if int(unit_id) not in scoped_units(request):
        raise PermissionDenied("That unit is outside your authorised scope.")


def instruction_unit_ids(user) -> set[int]:
    """Units the caller may issue an instruction to: own unit and subordinates.

    Narrower than ``accessible_unit_ids`` on purpose - that one reaches every
    active unit for the Administrator, while an instruction is a command act
    that stays inside the issuing account's own chain.
    """
    return own_unit_tree_ids(user)


def _resolve_user(user_id) -> User:
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise ValidationError({"user_id": "Unknown person."})
    return user


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------


class UnitAppointmentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated, CanManageAppointments]
    serializer_class = serializers.UnitAppointmentSerializer
    filterset_fields = ["unit", "role", "is_active", "user"]
    search_fields = ["user__username", "user__last_name", "role"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        return (
            UnitAppointment.objects.select_related("unit", "user")
            .filter(unit_id__in=ids)
            .order_by("unit_id", "role")
        )

    def create(self, request):
        serializer = serializers.UnitAppointmentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        require_unit(request, data["unit_id"])

        from apps.legacy.models import Unit

        unit = Unit.objects.filter(pk=data["unit_id"]).first()
        if unit is None:
            raise ValidationError({"unit_id": "Unknown unit."})

        try:
            appointment = services.create_appointment(
                unit=unit,
                user=_resolve_user(data["user_id"]),
                role=data["role"],
                actor=request.user,
                notes=data.get("notes", ""),
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        # Explicit keys: the audit record must name the affected unit and post
        # without the reader having to resolve foreign keys.
        log_action(
            actor=request.user,
            action="create_appointment",
            entity_type="appointment",
            entity_id=appointment.pk,
            after={
                "role": appointment.role,
                "role_label": appointment.get_role_display(),
                "unit_id": appointment.unit_id,
                "user_id": appointment.user_id,
                "is_active": True,
            },
            note=appointment.notes or "",
            request=request,
        )
        return Response(
            serializers.UnitAppointmentSerializer(appointment).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, pk=None):
        appointment = self.get_object()
        try:
            services.end_appointment(
                appointment, actor=request.user, reason=request.data.get("reason", "")
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(serializers.UnitAppointmentSerializer(appointment).data)


class ActingAppointmentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated, CanManageActingAuthority]
    serializer_class = serializers.ActingAppointmentSerializer
    filterset_fields = ["unit", "acting_role", "is_active", "user"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        return (
            ActingAppointment.objects.select_related("unit", "user", "appointed_by")
            .filter(unit_id__in=ids)
            .order_by("-starts_at")
        )

    def create(self, request):
        serializer = serializers.ActingAppointmentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        require_unit(request, data["unit_id"])

        from apps.legacy.models import Unit

        unit = Unit.objects.filter(pk=data["unit_id"]).first()
        if unit is None:
            raise ValidationError({"unit_id": "Unknown unit."})

        try:
            acting = services.create_acting_appointment(
                unit=unit,
                user=_resolve_user(data["user_id"]),
                acting_role=data["acting_role"],
                appointed_by=request.user,
                reason=data["reason"],
                starts_at=data["starts_at"],
                ends_at=data["ends_at"],
                capabilities=data.get("delegated_capabilities") or [],
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        log_action(
            actor=request.user,
            action="create_acting_appointment",
            entity_type="acting_appointment",
            entity_id=acting.pk,
            after=snapshot(acting),
            request=request,
        )
        return Response(
            serializers.ActingAppointmentSerializer(acting).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        acting = self.get_object()
        try:
            services.revoke_acting_appointment(
                acting, actor=request.user, reason=request.data.get("reason", "")
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(serializers.ActingAppointmentSerializer(acting).data)


# ---------------------------------------------------------------------------
# Offices / sections
# ---------------------------------------------------------------------------


class OfficeSectionViewSet(viewsets.ModelViewSet):
    """Offices and sections.

    Reading is open to every authenticated account (personnel pickers and the
    mission form need the list); creating, changing, deleting and re-seeding
    require ``manage_sections``, which only the Administrator and the Admin
    Officer posts hold. Scope is always the caller's authorised units.
    """

    permission_classes = [IsAuthenticated, CanViewResources, CanManageSections]
    serializer_class = serializers.OfficeSectionSerializer
    filterset_fields = ["unit", "kind", "is_active", "parent", "is_custom", "code"]
    search_fields = ["name", "code"]
    ordering_fields = ["unit_id", "name", "kind"]
    ordering = ["unit_id", "kind", "name"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        return OfficeSection.objects.select_related("unit", "parent").filter(unit_id__in=ids)

    def perform_create(self, serializer):
        unit = serializer.validated_data.get("unit")
        require_unit(self.request, getattr(unit, "id", None))
        instance = serializer.save(created_by=self.request.user)
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="office_section",
            entity_id=instance.pk,
            after=snapshot(instance),
            request=self.request,
        )

    def perform_update(self, serializer):
        unit = serializer.validated_data.get("unit") or serializer.instance.unit
        require_unit(self.request, unit.id)
        before = snapshot(serializer.instance)
        instance = serializer.save()
        log_action(
            actor=self.request.user,
            action="update",
            entity_type="office_section",
            entity_id=instance.pk,
            before=before,
            after=snapshot(instance),
            request=self.request,
        )

    def perform_destroy(self, instance):
        """Delete the office/section, as its manager instructs.

        Personnel assignments to the row are membership records for this
        office, so they are removed with it; child offices keep their own rows
        and are detached (``parent`` is ``SET_NULL``). The audit entry is
        written after the delete commits and carries the office's fields plus
        the assignments that went with it, so the removal stays traceable once
        the row is gone.        Deleting the Operations Center office ends the unit's routing authority until a successor office is designated.
        """
        from apps.accounts.ownership import can_delete_records

        if not can_delete_records(self.request.user):
            raise PermissionDenied(
                "Only an Administrator may permanently delete an office "
                "or section. Deactivate it instead."
            )
        removed_assignments = list(
            SectionAssignment.objects.filter(section=instance).values(
                "id", "user_id", "assignment_type", "is_active"
            )
        )
        before = snapshot(instance)
        before["section_assignments"] = removed_assignments
        entity_id = instance.pk

        with transaction.atomic():
            SectionAssignment.objects.filter(section=instance).delete()
            instance.delete()

        log_action(
            actor=self.request.user,
            action="delete",
            entity_type="office_section",
            entity_id=entity_id,
            before=before,
            after={},
            request=self.request,
        )

    @action(detail=False, methods=["get"])
    def catalogue(self, request):
        """The authoritative standard office list (never hard-coded clientside)."""
        return Response(
            {
                "catalogue": offices.catalogue_payload(),
                "total": len(offices.DEFAULT_OFFICE_CATALOGUE),
            }
        )

    @action(detail=False, methods=["post"], url_path="seed-defaults")
    def seed_defaults(self, request):
        """Create the standard offices for a unit (idempotent).

        ``retire_others`` also deactivates pre-catalogue entries and ends their
        active assignments - it never deletes a row, so history survives.
        """
        unit_id = request.data.get("unit")
        require_unit(request, unit_id)
        from apps.legacy.models import Unit

        unit = Unit.objects.filter(pk=unit_id).first()
        if unit is None:
            raise ValidationError({"unit": "Unknown unit."})

        result = offices.seed_default_offices(
            unit,
            actor=request.user,
            retire_others=bool(request.data.get("retire_others", False)),
        )
        log_action(
            actor=request.user,
            action="seed_default_offices",
            entity_type="office_section",
            entity_id=unit.id,
            after={
                "created": [row.code for row in result["created"]],
                "retired": result["retired"],
                "assignments_ended": result["assignments_ended"],
            },
            request=request,
        )
        return Response(
            {
                "unit": unit.id,
                "created": [row.code for row in result["created"]],
                "retired": result["retired"],
                "assignments_ended": result["assignments_ended"],
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="reset-catalogue",
        permission_classes=[IsAuthenticated, IsAdministrator],
    )
    def reset_catalogue(self, request):
        """Clear the office table and seed the standard list for every unit.

        Administrator only. The office rows and their personnel assignments are
        replaced; missions, MRM forms and audit history keep their stored text
        labels, so nothing else is lost. The whole reset is written to the audit
        trail as one entry carrying what was removed, because the rows
        themselves no longer exist afterwards.
        """
        from apps.legacy.models import Unit

        removed = list(OfficeSection.objects.values("id", "unit_id", "code", "name"))
        assignments_removed = SectionAssignment.objects.count()
        created: list[str] = []
        units_seeded = 0

        with transaction.atomic():
            SectionAssignment.objects.all().delete()
            OfficeSection.objects.all().delete()
            for unit in Unit.objects.all().only("id"):
                result = offices.seed_default_offices(unit, actor=request.user)
                created.extend(f"{unit.id}:{row.code}" for row in result["created"])
                units_seeded += 1

        log_action(
            actor=request.user,
            action="reset_office_catalogue",
            entity_type="office_section",
            entity_id=0,
            before={
                "offices": removed,
                "assignments_removed": assignments_removed,
            },
            after={"units_seeded": units_seeded, "created": len(created)},
            request=request,
        )
        return Response(
            {
                "removed": len(removed),
                "assignments_removed": assignments_removed,
                "units_seeded": units_seeded,
                "created": len(created),
            }
        )


class SectionAssignmentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated, CanViewResources, CanManageSections]
    serializer_class = serializers.SectionAssignmentSerializer
    filterset_fields = ["assignment_type", "is_active", "section", "user"]
    search_fields = ["user__username", "user__last_name", "section__name"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        queryset = SectionAssignment.objects.select_related(
            "user", "section", "section__unit", "assigned_by"
        ).filter(section__unit_id__in=ids)
        unit = self.request.query_params.get("unit")
        if unit:
            queryset = queryset.filter(section__unit_id=unit)
        return queryset.order_by("section__unit_id", "user__last_name")

    def create(self, request):
        serializer = serializers.SectionAssignmentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        section = OfficeSection.objects.filter(pk=data["section_id"]).first()
        if section is None:
            raise ValidationError({"section_id": "Unknown office/section."})
        require_unit(request, section.unit_id)

        person = _resolve_user(data["user_id"])
        if person.unit_id not in scoped_units(request):
            raise PermissionDenied("That person is outside your authorised scope.")

        try:
            assignment = services.assign_section(
                user=person,
                section=section,
                assignment_type=data["assignment_type"],
                actor=request.user,
                notes=data.get("notes", ""),
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        log_action(
            actor=request.user,
            action="assign_section",
            entity_type="section_assignment",
            entity_id=assignment.pk,
            after=snapshot(assignment),
            request=request,
        )
        return Response(
            serializers.SectionAssignmentSerializer(assignment).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, pk=None):
        assignment = self.get_object()
        try:
            services.end_section_assignment(assignment, actor=request.user)
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        log_action(
            actor=request.user,
            action="end_section_assignment",
            entity_type="section_assignment",
            entity_id=assignment.pk,
            request=request,
        )
        return Response(serializers.SectionAssignmentSerializer(assignment).data)


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------


class InstructionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, CanIssueInstructions]
    serializer_class = serializers.InstructionSerializer
    filterset_fields = ["status", "priority", "unit", "mission"]
    search_fields = ["subject", "body"]
    ordering = ["-created_at"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        user = self.request.user
        queryset = Instruction.objects.select_related("unit", "sender", "mission").prefetch_related(
            "recipients__user", "recipients__attachments"
        )
        visibility = Q(unit_id__in=ids) | Q(sender_id=user.id) | Q(recipients__user_id=user.id)
        queryset = queryset.filter(visibility).distinct()

        scope = self.request.query_params.get("scope")
        # "All visible" - a list request with no scope - is the command view:
        # every instruction in scope, not just the caller's own. Command and the
        # Operations Center hold it; everyone else keeps the scoped tabs (their
        # action items and the instructions they issued). Detail requests have no
        # scope either, so this applies to ``list`` only - a recipient must still
        # be able to open and action the record addressed to them.
        if not scope and self.action == "list":
            from apps.core import roles as role_defs
            from apps.core.permissions import can

            if not can(user, role_defs.CAP_VIEW_ALL_INSTRUCTIONS):
                raise PermissionDenied(
                    "The all-visible instruction view is reserved for command and the "
                    "Operations Center."
                )
        if scope == "mine":
            queryset = queryset.filter(recipients__user_id=user.id)
        elif scope == "issued":
            queryset = queryset.filter(sender_id=user.id)
        elif scope == "outstanding":
            queryset = queryset.filter(
                recipients__user_id=user.id,
                recipients__completed_at__isnull=True,
            ).exclude(status__in=[Instruction.STATUS_VERIFIED, Instruction.STATUS_CANCELLED])
        return queryset

    @action(detail=False, methods=["get"])
    def units(self, request):
        """The units this caller may issue to: their own unit and subordinates."""
        from apps.legacy.models import Unit

        ids = instruction_unit_ids(request.user)
        rows = Unit.objects.filter(id__in=ids, is_active=True).order_by("name")
        return Response(
            {
                "count": rows.count(),
                "results": [
                    {"id": unit.id, "name": unit.name, "acronym": unit.acronym}
                    for unit in rows
                ],
            }
        )

    def create(self, request):
        serializer = serializers.InstructionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        allowed = instruction_unit_ids(request.user)
        if not allowed:
            raise PermissionDenied(
                "Your account has no unit; instructions cannot be issued from it."
            )
        if int(data["unit_id"]) not in allowed:
            raise PermissionDenied(
                "Instructions may only be issued to your own unit and its subordinate units."
            )

        from apps.legacy.models import Unit
        from apps.ops.models import Mission

        unit = Unit.objects.filter(pk=data["unit_id"]).first()
        if unit is None:
            raise ValidationError({"unit_id": "Unknown unit."})

        mission = None
        if data.get("mission_id"):
            mission = Mission.objects.filter(pk=data["mission_id"]).first()
            if mission is None:
                raise ValidationError({"mission_id": "Unknown mission."})

        recipients = list(User.objects.filter(id__in=data["recipient_ids"]))
        for person in recipients:
            if person.unit_id not in allowed:
                raise PermissionDenied(
                    f"{person.username} is outside your authorised unit scope."
                )

        try:
            instruction = services.issue_instruction(
                unit=unit,
                sender=request.user,
                subject=data["subject"],
                body=data["body"],
                recipients=recipients,
                mission=mission,
                related=("instruction", ""),
                priority=data["priority"],
                requires_acknowledgement=data["requires_acknowledgement"],
                requires_completion=data["requires_completion"],
                due_at=data.get("due_at"),
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        log_action(
            actor=request.user,
            action="issue_instruction",
            entity_type="instruction",
            entity_id=instruction.pk,
            after=snapshot(instruction),
            request=request,
        )
        return Response(
            serializers.InstructionSerializer(instruction).data,
            status=status.HTTP_201_CREATED,
        )

    def _require_recipient(self, instruction, request):
        """Only an addressed recipient may act on an instruction."""
        if not instruction.recipients.filter(user_id=request.user.id).exists():
            raise PermissionDenied("You are not a recipient of this instruction.")

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        instruction = self.get_object()
        self._require_recipient(instruction, request)
        try:
            services.acknowledge_instruction(
                instruction, user=request.user, note=request.data.get("note", "")
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        instruction.refresh_from_db()
        return Response(serializers.InstructionSerializer(instruction).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Mark the caller's part complete, with optional PDF/image evidence."""
        instruction = self.get_object()
        self._require_recipient(instruction, request)
        try:
            prepared = services.prepare_instruction_attachments(
                request.FILES.getlist("attachments")
            )
            services.complete_instruction(
                instruction,
                user=request.user,
                note=request.data.get("note", ""),
                attachments=prepared,
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        instruction.refresh_from_db()
        return Response(serializers.InstructionSerializer(instruction).data)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"attachments/(?P<attachment_id>[0-9]+)",
    )
    def attachment(self, request, pk=None, attachment_id=None):
        """Stream a completion attachment to anyone who can see the instruction."""
        from apps.command.models import InstructionAttachment

        instruction = self.get_object()  # the queryset already applies visibility
        attachment = InstructionAttachment.objects.filter(
            id=attachment_id, recipient__instruction=instruction
        ).first()
        if attachment is None:
            raise Http404("Attachment not found.")
        path = services.instruction_attachment_path(attachment.file_name)
        if path is None:
            raise Http404("The attachment file is missing.")
        return FileResponse(
            open(path, "rb"),
            content_type=attachment.content_type or "application/octet-stream",
            as_attachment=False,
            filename=attachment.original_name or attachment.file_name,
        )

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        instruction = self.get_object()
        if instruction.sender_id != request.user.id and authority(request.user) != "Administrator":
            raise PermissionDenied("Only the issuing post or an administrator can verify.")
        try:
            services.verify_instruction(
                instruction, user=request.user, note=request.data.get("note", "")
            )
        except services.CommandError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        instruction.refresh_from_db()
        return Response(serializers.InstructionSerializer(instruction).data)


# ---------------------------------------------------------------------------
# Approval rules, hazard georeferences, vehicle positions
# ---------------------------------------------------------------------------


class ApprovalRuleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsAdministrator]
    serializer_class = serializers.ApprovalRuleSerializer
    queryset = ApprovalRule.objects.select_related("unit").all()
    filterset_fields = ["entity", "category", "unit", "is_active"]

    def perform_create(self, serializer):
        instance = serializer.save()
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="approval_rule",
            entity_id=instance.pk,
            after=snapshot(instance),
            request=self.request,
        )


class HazardLocationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated65]
    serializer_class = serializers.HazardLocationSerializer
    filterset_fields = ["unit", "hazard"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        return HazardLocation.objects.select_related("hazard", "unit").filter(
            Q(unit_id__in=ids) | Q(unit_id__isnull=True)
        )

    def perform_create(self, serializer):
        unit = serializer.validated_data.get("unit")
        if unit is not None:
            require_unit(self.request, unit.id)
        instance = serializer.save(created_by=self.request.user)
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="hazard_location",
            entity_id=instance.pk,
            after=snapshot(instance),
            request=self.request,
        )


class VehiclePositionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated65]
    serializer_class = serializers.VehiclePositionSerializer
    filterset_fields = ["vehicle", "source"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        return VehiclePosition.objects.select_related("vehicle").filter(
            Q(vehicle__unit_id__in=ids) | Q(vehicle__unit_id__isnull=True)
        )

    def perform_create(self, serializer):
        instance = serializer.save(reported_by=self.request.user)
        log_action(
            actor=self.request.user,
            action="create",
            entity_type="vehicle_position",
            entity_id=instance.pk,
            after=snapshot(instance),
            request=self.request,
        )


class MissionRoutingViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Read-only routing history; entries are written by the mission workflow."""

    permission_classes = [IsAuthenticated65]
    serializer_class = serializers.MissionRoutingSerializer
    filterset_fields = ["mission", "stage", "action"]

    def get_queryset(self):
        ids = scoped_units(self.request)
        return MissionRouting.objects.select_related("actor").filter(
            mission__unit_id__in=ids
        )
