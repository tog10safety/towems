"""Serializers for every TOWEMS module plus read models for legacy tables."""

from __future__ import annotations

import datetime as dt
import re

from django.db import transaction
from rest_framework import serializers

from apps.accounts.models import User
from apps.core import geo, timeutils
from apps.core.mgrs import MGRSError, from_mgrs, to_mgrs, validate_mgrs
from apps.legacy.models import MRMForm, Unit
from apps.ops import formation_rules, services
from apps.ops.models import (
    AdministrativeArea,
    AirAsset,
    Airfield,
    AirMissionAircraft,
    AirMissionAsset,
    AirMissionCrew,
    AirMissionDetail,
    AirMissionElement,
    AirMissionItinerary,
    AreaOfResponsibility,
    AuditLog,
    GroundMissionDetail,
    InventoryCategory,
    InventoryItem,
    InventoryRequest,
    InventoryTransaction,
    Mission,
    MissionOperationalArea,
    MissionPersonnel,
    MissionRiskTeam,
    MovementEvent,
    Notification,
    OperationalMapFeature,
    RiskAssessment,
    TrackingPing,
    TripItinerary,
    Vehicle,
    VehicleRun,
    VehicleTrip,
    VehicleTripVehicle,
)


# ---------------------------------------------------------------------------
# Shared validation
# ---------------------------------------------------------------------------


def validate_coordinates(lat, lon, mgrs_value, *, fill_mgrs: bool = True):
    """Validate a coordinate triple and optionally derive MGRS.

    Returns ``(lat, lon, mgrs)``. MGRS input is authoritative when supplied;
    otherwise a 5-digit MGRS string is derived from lat/lon so the map and the
    textual record never disagree.
    """
    if mgrs_value:
        try:
            mgrs_value = validate_mgrs(mgrs_value)
        except MGRSError as exc:
            raise serializers.ValidationError({"mgrs": str(exc)}) from exc

    if lat is None and lon is None:
        return lat, lon, mgrs_value or ""

    if lat is None or lon is None:
        raise serializers.ValidationError(
            {"latitude": "Provide both latitude and longitude, or neither."}
        )
    if not -90 <= float(lat) <= 90:
        raise serializers.ValidationError({"latitude": "Latitude must be -90..90."})
    if not -180 <= float(lon) <= 180:
        raise serializers.ValidationError({"longitude": "Longitude must be -180..180."})

    if not mgrs_value and fill_mgrs:
        try:
            mgrs_value = to_mgrs(float(lat), float(lon), 5)
        except MGRSError:
            mgrs_value = ""
    return float(lat), float(lon), mgrs_value or ""


class UnitSerializer(serializers.ModelSerializer):
    hierarchy = serializers.SerializerMethodField()
    parent_acronym = serializers.CharField(source="parent.acronym", read_only=True)
    #: The colour the unit's AORs are drawn in (see ``apps.ops.aor.unit_color``).
    aor_color = serializers.SerializerMethodField()
    #: Where the unit sits on the map (additive ``command_unit_map_marker``).
    map_latitude = serializers.SerializerMethodField()
    map_longitude = serializers.SerializerMethodField()
    map_mgrs = serializers.SerializerMethodField()
    map_place_name = serializers.SerializerMethodField()
    map_logo_url = serializers.SerializerMethodField()
    class Meta:
        model = Unit
        fields = [
            "id",
            "name",
            "acronym",
            "category",
            "location",
            "parent",
            "parent_acronym",
            "commanding_officer",
            "date_created",
            "is_active",
            "hierarchy",
            "aor_color",
            "map_latitude",
            "map_longitude",
            "map_mgrs",
            "map_place_name",
            "map_logo_url",
        ]

    def get_hierarchy(self, obj) -> str:
        return obj.get_hierarchy_display()

    def get_aor_color(self, obj) -> str:
        from apps.ops import aor as aor_service

        return aor_service.unit_color(obj)

    # -- map marker (select_related("map_marker") keeps this to one query) ----

    def _marker(self, obj):
        try:
            return obj.map_marker
        except Exception:
            return None

    def get_map_latitude(self, obj):
        marker = self._marker(obj)
        return marker.latitude if marker else None

    def get_map_longitude(self, obj):
        marker = self._marker(obj)
        return marker.longitude if marker else None

    def get_map_mgrs(self, obj) -> str:
        marker = self._marker(obj)
        return marker.mgrs if marker else ""

    def get_map_place_name(self, obj) -> str:
        marker = self._marker(obj)
        return marker.place_name if marker else ""

    def get_map_logo_url(self, obj) -> str:
        marker = self._marker(obj)
        if not marker or not marker.logo_filename:
            return ""
        return f"/api/units/{obj.id}/marker-logo/?v={marker.logo_filename}"


class UnitWriteSerializer(serializers.ModelSerializer):
    """Create/edit a unit in the legacy ``units`` table.

    Only the descriptive columns are writable; ``id``, ``date_created`` and
    ``is_active`` are managed by the server (``is_active`` through the
    deactivate/activate actions, so a status change is always audited with a
    reason). No column is added or renamed - the table keeps its existing shape.
    """

    class Meta:
        model = Unit
        fields = [
            "id",
            "name",
            "acronym",
            "category",
            "location",
            "parent",
            "commanding_officer",
        ]

    def validate_name(self, value: str) -> str:
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("A unit name is required.")
        clash = Unit.objects.filter(name__iexact=name)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A unit with that name already exists.")
        return name

    def validate_acronym(self, value: str) -> str:
        acronym = (value or "").strip().upper()
        if not acronym:
            raise serializers.ValidationError("A unit acronym is required.")
        return acronym

    def validate_location(self, value: str) -> str:
        location = (value or "").strip()
        if not location:
            raise serializers.ValidationError("A location is required.")
        return location

    def validate_parent(self, value):
        if value is None:
            return value
        if self.instance is not None:
            if value.pk == self.instance.pk:
                raise serializers.ValidationError("A unit cannot be its own parent.")
            # Walk up from the proposed parent: reaching this unit would make a
            # cycle, which would make the hierarchy and unit scope unfollowable.
            node = value
            seen: set[int] = set()
            while node is not None and node.pk not in seen:
                if node.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        "That unit is subordinate to this one; the hierarchy would loop."
                    )
                seen.add(node.pk)
                node = node.parent
        if value.is_active is False:
            raise serializers.ValidationError("Choose an active parent unit.")
        return value


class MRMFormSerializer(serializers.ModelSerializer):
    """Read-only view of a legacy MRM assessment (risk module)."""

    creator_name = serializers.SerializerMethodField()
    evaluator_name = serializers.SerializerMethodField()
    risk_color = serializers.SerializerMethodField()
    is_approvable = serializers.BooleanField(read_only=True)
    unit_name = serializers.CharField(source="original_unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="original_unit.acronym", read_only=True)
    mission = serializers.SerializerMethodField()
    mission_number = serializers.SerializerMethodField()
    mission_evaluator = serializers.SerializerMethodField()
    mission_evaluator_name = serializers.SerializerMethodField()
    decision_authority = serializers.SerializerMethodField()
    decision_authority_label = serializers.SerializerMethodField()
    decision_authority_name = serializers.SerializerMethodField()
    decision_authority_reason = serializers.SerializerMethodField()
    decision_authority_resolved = serializers.SerializerMethodField()
    creator_has_signature = serializers.SerializerMethodField()
    current_user_has_signature = serializers.SerializerMethodField()
    can_sign = serializers.SerializerMethodField()
    can_approve_authority = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    date_covered_start = serializers.SerializerMethodField()
    date_covered_end = serializers.SerializerMethodField()

    class Meta:
        model = MRMForm
        fields = [
            "id",
            "mrm_number",
            "activity_mission",
            "status",
            "review_status",
            "authority_level",
            "authority_approved",
            "authority_name",
            "creator_signed",
            "evaluator_signed",
            "max_risk",
            "residual_risk",
            "total_percent",
            "risk_category",
            "reviewed_by",
            "created_by",
            "evaluator_name",
            "creator_name",
            "unit_name",
            "unit_acronym",
            "original_unit",
            "original_unit_name",
            "mission",
            "mission_number",
            "mission_evaluator",
            "mission_evaluator_name",
            "decision_authority",
            "decision_authority_label",
            "decision_authority_name",
            "decision_authority_reason",
            "decision_authority_resolved",
            "creator_has_signature",
            "current_user_has_signature",
            "can_sign",
            "can_approve_authority",
            "can_edit",
            "can_delete",
            "date_covered_start",
            "date_covered_end",
            "date_created",
            "date_submitted",
            "date_reviewed",
            "date_approved",
            "approved_pdf_filename",
            "is_approvable",
            "risk_color",
        ]

    def get_creator_name(self, obj) -> str:
        if obj.creator_name:
            return obj.creator_name
        user = User.objects.filter(pk=obj.created_by_id).first()
        return user.full_name if user else ""

    def get_evaluator_name(self, obj) -> str:
        if obj.reviewer_name:
            return obj.reviewer_name
        if not obj.reviewed_by_id:
            return ""
        user = User.objects.filter(pk=obj.reviewed_by_id).first()
        return user.full_name if user else ""

    def _link(self, obj):
        """This MRM's mission link, resolved once per row.

        The register prefetches ``toms_risk_links__mission`` so a page of 25
        rows does not run 25 more queries for the mission window.
        """
        cache = getattr(self, "_link_cache", None)
        if cache is None:
            cache = {}
            self._link_cache = cache
        if obj.pk not in cache:
            rows = list(obj.toms_risk_links.all())
            cache[obj.pk] = rows[0] if rows else None
        return cache[obj.pk]

    def get_mission(self, obj):
        link = self._link(obj)
        return link.mission_id if link else None

    def get_mission_number(self, obj) -> str:
        link = self._link(obj)
        return link.mission.mission_number if link else ""

    def _covered_window(self, obj):
        """The days this assessment covers.

        A mission-linked assessment covers the mission's own window; one raised
        by hand in Risk Management covers the day it was raised, so it is never
        left without a date.
        """
        link = self._link(obj)
        if link is not None:
            return link.mission.start_at, link.mission.end_at
        return obj.date_created, obj.date_created

    def get_date_covered_start(self, obj):
        return self._covered_window(obj)[0]

    def get_date_covered_end(self, obj):
        return self._covered_window(obj)[1]

    def _mission_reviewer(self, obj):
        """The evaluator the Operations Center named on the mission's MRM team."""
        from apps.ops.models import MissionRiskTeam

        link = self._link(obj)
        if link is None:
            return None
        row = (
            MissionRiskTeam.objects.filter(
                mission_id=link.mission_id,
                role=MissionRiskTeam.ROLE_REVIEWER,
                is_active=True,
            )
            .select_related("user")
            .first()
        )
        return row.user if row else None

    def get_mission_evaluator(self, obj):
        reviewer = self._mission_reviewer(obj)
        return reviewer.id if reviewer else None

    def get_mission_evaluator_name(self, obj) -> str:
        reviewer = self._mission_reviewer(obj)
        return reviewer.full_name if reviewer else ""

    # -- decision authority (who signs this risk band) ---------------------

    def _resolution(self, obj):
        cache = getattr(self, "_resolution_cache", None)
        if cache is None:
            cache = {}
            self._resolution_cache = cache
        if obj.pk not in cache:
            from apps.ops import decision_authority

            unit_cache = getattr(self, "_authority_unit_cache", None)
            if unit_cache is None:
                unit_cache = {}
                self._authority_unit_cache = unit_cache
            cache[obj.pk] = decision_authority.resolve(obj, cache=unit_cache)
        return cache[obj.pk]

    def get_decision_authority(self, obj):
        return self._resolution(obj).band

    def get_decision_authority_label(self, obj) -> str:
        return self._resolution(obj).label

    def get_decision_authority_name(self, obj) -> str:
        user = self._resolution(obj).user
        return user.full_name if user else ""

    def get_decision_authority_reason(self, obj) -> str:
        return self._resolution(obj).reason

    def get_decision_authority_resolved(self, obj) -> bool:
        return self._resolution(obj).resolved

    def _request_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_creator_has_signature(self, obj) -> bool:
        if not obj.created_by_id:
            return False
        return bool(
            User.objects.filter(pk=obj.created_by_id)
            .values_list("signature_filename", flat=True)
            .first()
        )

    def get_current_user_has_signature(self, obj) -> bool:
        user = self._request_user()
        return bool(user and getattr(user, "signature_filename", ""))

    def get_can_sign(self, obj) -> bool:
        user = self._request_user()
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return bool(
            obj.created_by_id == user.id
            and not obj.creator_signed
            and not obj.authority_approved
            and obj.status in {"submitted", "reviewed"}
        )

    def get_can_edit(self, obj) -> bool:
        """Drafts stay editable for their creator and the MRM team.

        The register uses this to offer an edit action, and the editor itself
        enforces the same rule on save.
        """
        from apps.ops import mrm_editor

        user = self._request_user()
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return mrm_editor.can_edit(obj, user)

    def get_can_delete(self, obj) -> bool:
        from apps.ops import mrm_access

        return mrm_access.can_delete(self._request_user(), obj)[0]

    def get_can_approve_authority(self, obj) -> bool:
        """Whether this caller is the signer the risk band routes to.

        The button follows the same rule the approve endpoint enforces, so the
        screen never offers a signature the server would refuse.
        """
        from apps.core import roles

        user = self._request_user()
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if obj.authority_approved or not obj.is_approvable:
            return False
        if getattr(user, "authority", "") == roles.BASE_ADMIN:
            return True
        resolution = self._resolution(obj)
        if resolution.user is not None:
            return resolution.user.id == user.id
        from apps.core.roles import approver_roles_for, effective_roles

        permitted = set(approver_roles_for("mrm", obj.original_unit_id))
        return bool(effective_roles(user, obj.original_unit_id) & permitted)

    def get_risk_color(self, obj) -> str:
        if obj.total_percent is None:
            return "secondary"
        return {"Low": "success", "Medium": "warning", "High": "danger"}.get(
            obj.risk_category or "", "secondary"
        )


class EvaluatorAssignmentSerializer(serializers.Serializer):
    evaluator_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ManualMRMCreateSerializer(serializers.Serializer):
    """Input for an MRM raised by hand, without an Operations Center mission.

    The rest of the legacy form (hazards, IMSAFE, conditions) is completed in
    the assessment editor after the draft exists, exactly as for a mission-led
    MRM. The evaluator is chosen up front because submission requires one.
    """

    activity_mission = serializers.CharField(max_length=255)
    # The purpose is part of raising the record: a draft with no scope is what
    # the evaluator would otherwise have to chase before they can review it.
    mission_statement = serializers.CharField(max_length=2000)
    evaluator_id = serializers.IntegerField()


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------


class MissionPersonnelSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    rank = serializers.CharField(source="user.rank", read_only=True)
    serial_number = serializers.CharField(source="user.serial_number", read_only=True)
    display_role = serializers.SerializerMethodField()

    class Meta:
        model = MissionPersonnel
        fields = [
            "id",
            "user",
            "user_name",
            "rank",
            "serial_number",
            "role",
            "custom_role",
            "display_role",
            "is_active",
        ]
        read_only_fields = ["id"]

    def get_display_role(self, obj) -> str:
        return obj.custom_role if obj.role == "Specify" and obj.custom_role else obj.role


class MissionOperationalAreaSerializer(serializers.ModelSerializer):
    """An extra operational area on a ground mission (the primary one is on
    ``ground_detail.operational_area``)."""

    class Meta:
        model = MissionOperationalArea
        fields = ["id", "sequence", "name", "latitude", "longitude", "mgrs"]
        read_only_fields = ["id"]

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Give the additional area a name.")
        return name


class GroundDetailSerializer(serializers.ModelSerializer):
    upr_unit_name = serializers.CharField(source="upr_unit.name", read_only=True)

    class Meta:
        model = GroundMissionDetail
        fields = [
            "estimated_start",
            "estimated_end",
            "upr_unit",
            "upr_unit_name",
            "osp_section",
            "operational_area",
            "ground_risk_notes",
        ]

    def validate(self, attrs):
        start = attrs.get("estimated_start", getattr(self.instance, "estimated_start", None))
        end = attrs.get("estimated_end", getattr(self.instance, "estimated_end", None))
        if start and end and end < start:
            raise serializers.ValidationError(
                {"estimated_end": "Estimated end must not precede the start."}
            )
        return attrs


class AirMissionAssetSerializer(serializers.ModelSerializer):
    element_role_label = serializers.CharField(
        source="get_element_role_display", read_only=True
    )

    class Meta:
        model = AirMissionAsset
        fields = [
            "id",
            "air_asset",
            "sequence",
            "is_lead",
            "element_role",
            "element_role_label",
            "pilot_in_command",
            "co_pilot",
            "aircraft_model",
            "tail_number",
            "callsign",
        ]
        read_only_fields = fields


class AirMissionCrewSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = AirMissionCrew
        fields = ["id", "sequence", "role", "role_label", "name"]
        read_only_fields = ["id"]
        extra_kwargs = {"sequence": {"required": False}}


class AirMissionItinerarySerializer(serializers.ModelSerializer):
    route_label = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    point_role_label = serializers.CharField(
        source="get_point_role_display", read_only=True
    )
    display_mission = serializers.SerializerMethodField()

    class Meta:
        model = AirMissionItinerary
        fields = [
            "id",
            "sequence",
            "etd",
            "eta",
            "atd",
            "ata",
            "tot",
            "toot",
            "se",
            "sd",
            "status",
            "status_label",
            "origin",
            "destination",
            "route",
            "route_label",
            "mission",
            "display_mission",
            "point_role",
            "point_role_label",
            "overflight",
            "return_to_origin",
            "remarks",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {"sequence": {"required": False}}

    def get_display_mission(self, obj) -> str:
        return obj.mission or getattr(obj.aircraft.element, "mission", "")

    def validate(self, attrs):
        etd = attrs.get("etd", getattr(self.instance, "etd", None))
        eta = attrs.get("eta", getattr(self.instance, "eta", None))
        if etd and eta and eta <= etd:
            raise serializers.ValidationError(
                {"eta": "ETA must be after the ETD."}
            )
        return attrs


class AirMissionAircraftSerializer(serializers.ModelSerializer):
    crew = AirMissionCrewSerializer(many=True, required=False)
    itineraries = AirMissionItinerarySerializer(many=True, required=False)
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = AirMissionAircraft
        fields = [
            "id",
            "sequence",
            "aircraft_type",
            "tail_number",
            "callsign",
            "is_lead",
            "display_name",
            "crew",
            "itineraries",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {"sequence": {"required": False}}


class AirMissionElementSerializer(serializers.ModelSerializer):
    aircraft = AirMissionAircraftSerializer(many=True, required=False)

    class Meta:
        model = AirMissionElement
        fields = ["id", "sequence", "mission", "remarks", "aircraft"]
        read_only_fields = ["id"]
        extra_kwargs = {"sequence": {"required": False}}


class AirDetailSerializer(serializers.ModelSerializer):
    supporting_unit_labels = serializers.SerializerMethodField()
    air_asset_label = serializers.SerializerMethodField()
    air_asset_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=AirAsset.objects.all(),
        write_only=True,
        required=False,
    )
    aircraft_crew = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False
    )
    assigned_aircraft = AirMissionAssetSerializer(many=True, read_only=True)
    elements = AirMissionElementSerializer(many=True, required=False)
    mission_configuration_label = serializers.CharField(
        source="get_mission_configuration_display", read_only=True
    )

    class Meta:
        model = AirMissionDetail
        read_only_fields = ["actual_arrival", "rendezvous_ready_at"]
        fields = [
            "operation_date",
            "mission_configuration",
            "mission_configuration_label",
            "aircraft_count",
            "assigned_aircraft",
            "elements",
            "air_asset_ids",
            "aircraft_crew",
            "air_asset",
            "air_asset_label",
            "aircraft_type",
            "aircraft_tail_number",
            "flight_identification",
            "departure_location",
            "destination_location",
            "route",
            "scheduled_departure",
            "scheduled_arrival",
            "actual_arrival",
            "rendezvous_ready_at",
            "supporting_units",
            "supporting_unit_labels",
        ]

    def get_air_asset_label(self, obj) -> str:
        asset = obj.air_asset
        if asset is None:
            return ""
        parts = [asset.model, asset.tail_number, asset.callsign]
        return " · ".join(part for part in parts if part)

    def get_supporting_unit_labels(self, obj) -> list[str]:
        return [
            f"{unit.acronym} — {unit.name}" if unit.acronym else unit.name
            for unit in obj.supporting_units.all()
        ]

    def validate_air_asset(self, value):
        self._validate_asset_scope(value)
        return value

    def validate_air_asset_ids(self, value):
        for asset in value:
            self._validate_asset_scope(asset)
        ids = [asset.pk for asset in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError(
                "An aircraft can only be assigned once in a formation."
            )
        return value

    def _validate_asset_scope(self, value):
        if value is None:
            return
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return

        from apps.core import roles
        from apps.core.scoping import accessible_unit_ids

        if roles.has_capability(user, roles.CAP_SYSTEM_CONFIG):
            return
        allowed = accessible_unit_ids(user)
        if value.opcon_unit_id is None or value.opcon_unit_id not in allowed:
            raise serializers.ValidationError(
                "That air asset is outside your authorised unit scope."
            )

    def validate_supporting_units(self, value):
        for feature in value:
            if feature.feature_type != "friendly_troop":
                raise serializers.ValidationError(
                    "Only friendly troops plotted in Operational Mapping can be supported."
                )
        return value

    def validate(self, attrs):
        operation_date = attrs.get("operation_date")
        if (
            operation_date
            and self.instance is None
            and operation_date < dt.date.today()
        ):
            raise serializers.ValidationError(
                {"operation_date": "Operations date cannot be in the past."}
            )

        elements = attrs.get("elements")
        if elements is not None:
            if not elements:
                raise serializers.ValidationError(
                    {"elements": "Add at least one Flight Operations element."}
                )
            aircraft_rows = [
                aircraft
                for element in elements
                for aircraft in (element.get("aircraft") or [])
            ]
            if not aircraft_rows:
                raise serializers.ValidationError(
                    {"elements": "Each element needs at least one aircraft."}
                )
            itineraries = [
                itinerary
                for aircraft in aircraft_rows
                for itinerary in (aircraft.get("itineraries") or [])
            ]
            if not itineraries:
                raise serializers.ValidationError(
                    {"elements": "Add at least one itinerary leg."}
                )

            seen_aircraft = set()
            duplicate_aircraft = []
            for aircraft in aircraft_rows:
                model = (aircraft.get("aircraft_type") or "").strip()
                tail = (aircraft.get("tail_number") or "").strip()
                if not tail:
                    continue
                key = tail.casefold()
                if key in seen_aircraft:
                    duplicate_aircraft.append(" ".join(part for part in (model, tail) if part))
                seen_aircraft.add(key)
            if duplicate_aircraft:
                raise serializers.ValidationError(
                    {
                        "elements": (
                            "The same aircraft can only appear once on a Flight "
                            "Operations sheet: "
                            + ", ".join(dict.fromkeys(duplicate_aircraft))
                            + "."
                        )
                    }
                )

            first_aircraft = aircraft_rows[0]
            first_leg = next(
                (
                    itinerary
                    for aircraft in aircraft_rows
                    for itinerary in (aircraft.get("itineraries") or [])
                    if itinerary.get("origin") or itinerary.get("route")
                ),
                itineraries[0],
            )
            last_legs = [
                itinerary
                for aircraft in aircraft_rows
                for itinerary in (aircraft.get("itineraries") or [])
            ]
            last_leg = last_legs[-1] if last_legs else first_leg

            attrs["aircraft_count"] = max(1, len(aircraft_rows))
            attrs["mission_configuration"] = (
                "single" if len(aircraft_rows) == 1 else "element"
            )
            attrs["aircraft_type"] = (first_aircraft.get("aircraft_type") or "")[:120]
            attrs["aircraft_tail_number"] = (
                first_aircraft.get("tail_number") or ""
            )[:60]
            attrs["flight_identification"] = (
                first_aircraft.get("callsign")
                or first_aircraft.get("tail_number")
                or ""
            )[:60]
            attrs["departure_location"] = (
                first_leg.get("origin") or first_leg.get("route") or ""
            )[:255]
            attrs["destination_location"] = (
                last_leg.get("destination")
                or last_leg.get("route")
                or ""
            )[:255]
            attrs["route"] = "\n".join(
                row.get("route")
                or " – ".join(
                    part for part in (row.get("origin"), row.get("destination")) if part
                )
                for row in itineraries
                if row.get("route") or row.get("origin") or row.get("destination")
            )
            planned_departures = [row.get("etd") for row in itineraries if row.get("etd")]
            planned_arrivals = [row.get("eta") for row in itineraries if row.get("eta")]
            if planned_departures:
                attrs["scheduled_departure"] = min(planned_departures)
                if not attrs.get("operation_date"):
                    attrs["operation_date"] = min(planned_departures).date()
            if planned_arrivals:
                attrs["scheduled_arrival"] = max(planned_arrivals)
                if planned_departures and min(planned_arrivals) <= min(planned_departures):
                    raise serializers.ValidationError(
                        {"scheduled_arrival": "Every ETA must be after the ETD."}
                    )
            attrs["air_asset"] = None
            return attrs

        configuration = attrs.get(
            "mission_configuration",
            getattr(self.instance, "mission_configuration", "single"),
        )
        if "mission_configuration" in attrs and "aircraft_count" not in attrs:
            attrs["aircraft_count"] = AirMissionDetail.default_aircraft_count(
                configuration
            )
        aircraft_count = attrs.get(
            "aircraft_count", getattr(self.instance, "aircraft_count", 1)
        )
        low, high = AirMissionDetail.configuration_bounds(configuration)
        if not low <= aircraft_count <= high:
            label = dict(AirMissionDetail.CONFIGURATION_CHOICES).get(
                configuration, configuration
            )
            raise serializers.ValidationError(
                {
                    "aircraft_count": (
                        f"{label} requires between {low} and {high} aircraft."
                    )
                }
            )

        selected = attrs.get("air_asset_ids")
        if selected is not None and len(selected) != aircraft_count:
            raise serializers.ValidationError(
                {
                    "air_asset_ids": (
                        f"Select exactly {aircraft_count} aircraft for this "
                        "configuration."
                    )
                }
            )

        lead = attrs.get("air_asset", getattr(self.instance, "air_asset", None))
        if selected:
            selected_ids = {asset.pk for asset in selected}
            if lead is None:
                lead = selected[0]
                attrs["air_asset"] = lead
            elif lead.pk not in selected_ids:
                raise serializers.ValidationError(
                    {"air_asset": "The lead aircraft must be one of the selected aircraft."}
                )

        if lead is not None:
            # The register is the identity of the airframe: a linked mission
            # cannot disagree with it about type or tail number.
            attrs["aircraft_type"] = (lead.model or "")[:120]
            attrs["aircraft_tail_number"] = (lead.tail_number or "")[:60]
            if not (attrs.get("flight_identification") or "").strip() and lead.callsign:
                attrs["flight_identification"] = lead.callsign[:60]
        dep = attrs.get("scheduled_departure", getattr(self.instance, "scheduled_departure", None))
        arr = attrs.get("scheduled_arrival", getattr(self.instance, "scheduled_arrival", None))
        if dep and arr and arr <= dep:
            raise serializers.ValidationError(
                {"scheduled_arrival": "Arrival must be after departure."}
            )
        return attrs


class TripItinerarySerializer(serializers.ModelSerializer):
    class Meta:
        model = TripItinerary
        fields = [
            "id",
            "sequence",
            "location_name",
            "latitude",
            "longitude",
            "mgrs",
            "planned_departure",
            "planned_arrival",
            "purpose",
            "remarks",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {"sequence": {"required": False}}

    def validate(self, attrs):
        lat, lon, mgrs_value = validate_coordinates(
            attrs.get("latitude", getattr(self.instance, "latitude", None)),
            attrs.get("longitude", getattr(self.instance, "longitude", None)),
            attrs.get("mgrs", getattr(self.instance, "mgrs", "")),
        )
        attrs["latitude"], attrs["longitude"], attrs["mgrs"] = lat, lon, mgrs_value
        return attrs


class VehicleSerializer(serializers.ModelSerializer):
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    owner_name = serializers.CharField(source="owner.full_name", read_only=True, default="")
    display_name = serializers.CharField(read_only=True)
    last_position = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields = [
            "id",
            "registration_number",
            "wheels",
            "make",
            "model",
            "registration_no",
            "official_receipt_no",
            "display_name",
            "vehicle_type",
            "make_model",
            "unit",
            "unit_name",
            "unit_acronym",
            "status",
            "last_position",
            "capacity",
            "is_active",
            "notes",
            "is_pov",
            "owner",
            "owner_name",
            "fuel_type",
        ]
        # A POV is created by the mission that uses it, never typed into the
        # register by hand.
        read_only_fields = ["is_pov"]

    def get_last_position(self, obj):
        """Where the vehicle was last reported, and how old that report is.

        This is a report, never live tracking: the reader gets the MGRS grid,
        the instant it was reported and ``is_stale`` so an old position cannot
        pass for a current one. A vehicle that has never reported returns None.
        """
        rows = getattr(obj, "recent_positions", None)
        if rows is None:
            rows = list(obj.positions.all()[:1])
        if not rows:
            return None
        row = rows[0]
        age = max(0, (timeutils.now() - row.reported_at).total_seconds())
        return {
            "mgrs": row.mgrs,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "reported_at": row.reported_at,
            "source": row.source,
            "source_display": row.get_source_display(),
            "note": row.note,
            "age_seconds": int(age),
            "is_stale": age > 24 * 3600,
        }

    @staticmethod
    def _mirror(data):
        """Keep the older combined columns in step with make/model."""
        if "model" in data:
            data["vehicle_type"] = (data["model"] or "")[:80]
        if "make" in data:
            data["make_model"] = (data["make"] or "")[:120]

    def create(self, validated_data):
        self._mirror(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._mirror(validated_data)
        return super().update(instance, validated_data)


class TripVehicleSerializer(serializers.ModelSerializer):
    registration_number = serializers.CharField(source="vehicle.registration_number", read_only=True)
    display_name = serializers.CharField(source="vehicle.display_name", read_only=True, default="")
    vehicle_type = serializers.CharField(source="vehicle.vehicle_type", read_only=True)
    make_model = serializers.CharField(source="vehicle.make_model", read_only=True, default="")
    fuel_type = serializers.CharField(source="vehicle.fuel_type", read_only=True, default="")
    is_pov = serializers.BooleanField(source="vehicle.is_pov", read_only=True, default=False)
    owner_name = serializers.CharField(
        source="vehicle.owner.full_name", read_only=True, default=""
    )
    driver_name = serializers.CharField(source="driver.full_name", read_only=True)

    class Meta:
        model = VehicleTripVehicle
        fields = [
            "id",
            "vehicle",
            "registration_number",
            "display_name",
            "vehicle_type",
            "make_model",
            "fuel_type",
            "is_pov",
            "owner_name",
            "driver",
            "driver_name",
            "notes",
        ]


class VehicleTripSerializer(serializers.ModelSerializer):
    itinerary = TripItinerarySerializer(many=True, read_only=True)
    vehicles = TripVehicleSerializer(many=True, read_only=True)
    mission_number = serializers.CharField(source="mission.mission_number", read_only=True)

    class Meta:
        model = VehicleTrip
        fields = [
            "id",
            "trip_number",
            "title",
            "purpose",
            "mission",
            "mission_number",
            "status",
            "departure_at",
            "return_at",
            "remarks",
            "created_by",
            "itinerary",
            "vehicles",
            "route_status",
            "route_geometry",
            "route_legs",
            "route_distance_m",
            "route_duration_s",
            "route_provider",
            "route_calculated_at",
            "route_error",
            "created_at",
            "updated_at",
        ]
        # The route is the routing engine's output; it is never typed in by a
        # client and never derived from a straight line between stops.
        read_only_fields = [
            "id",
            "trip_number",
            "created_by",
            "route_status",
            "route_geometry",
            "route_legs",
            "route_distance_m",
            "route_duration_s",
            "route_provider",
            "route_calculated_at",
            "route_error",
        ]


class MovementEventSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    stop_sequence = serializers.SerializerMethodField()
    stop_name = serializers.SerializerMethodField()
    recorded_by_name = serializers.CharField(
        source="recorded_by.full_name", read_only=True, default=""
    )

    class Meta:
        model = MovementEvent
        fields = [
            "id",
            "kind",
            "kind_label",
            "stop",
            "stop_sequence",
            "stop_name",
            "planned_at",
            "actual_at",
            "source",
            "source_label",
            "recorded_by",
            "recorded_by_name",
            "latitude",
            "longitude",
            "note",
            "created_at",
        ]

    def get_stop_sequence(self, obj):
        return obj.stop.sequence if obj.stop_id else None

    def get_stop_name(self, obj):
        return obj.stop.location_name if obj.stop_id else ""


class TrackingPingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingPing
        fields = [
            "id",
            "latitude",
            "longitude",
            "accuracy_m",
            "speed_mps",
            "heading_deg",
            "recorded_at",
            "received_at",
            "source",
        ]


def _tap_position(event):
    """Where the phone was when the driver tapped, as the event recorded it."""
    if event is None or event.latitude is None or event.longitude is None:
        return None
    from apps.core.mgrs import to_mgrs

    try:
        grid = to_mgrs(event.latitude, event.longitude)
    except Exception:  # pragma: no cover - a bad coordinate is not worth a 500
        grid = ""
    return {"latitude": event.latitude, "longitude": event.longitude, "mgrs": grid}


class VehicleRunSerializer(serializers.ModelSerializer):
    trip_number = serializers.CharField(source="trip.trip_number", read_only=True)
    trip_status = serializers.CharField(source="trip.status", read_only=True)
    registration_number = serializers.CharField(
        source="vehicle.registration_number", read_only=True
    )
    vehicle_type = serializers.CharField(source="vehicle.vehicle_type", read_only=True)
    driver_name = serializers.CharField(
        source="driver.full_name", read_only=True, default=""
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    atd_source_label = serializers.CharField(
        source="get_atd_source_display", read_only=True, default=""
    )
    events = MovementEventSerializer(many=True, read_only=True)
    stops = serializers.SerializerMethodField()
    recent_pings = serializers.SerializerMethodField()
    is_live = serializers.SerializerMethodField()
    route = serializers.SerializerMethodField()

    class Meta:
        model = VehicleRun
        fields = [
            "id",
            "mission",
            "trip",
            "trip_number",
            "trip_status",
            "vehicle",
            "registration_number",
            "vehicle_type",
            "driver",
            "driver_name",
            "is_lead",
            "status",
            "status_label",
            "etd",
            "atd",
            "atd_source",
            "atd_source_label",
            "completed_at",
            "last_ping_at",
            "last_latitude",
            "last_longitude",
            "last_accuracy_m",
            "is_live",
            "notes",
            "events",
            "stops",
            "recent_pings",
            "route",
        ]

    def get_route(self, obj):
        """The trip's planned road route, for the driver's own map.

        A driver may not be allowed to open the whole mission record, so the
        route they actually have to drive is served with their run.
        """
        trip = obj.trip
        return {
            "status": trip.route_status,
            "geometry": trip.route_geometry,
            "distance_m": trip.route_distance_m,
            "duration_s": trip.route_duration_s,
            "provider": trip.route_provider,
        }

    def _events_for(self, obj):
        # ``events`` is prefetched by the snapshot query; falling back keeps the
        # serializer usable when a single run is returned by an action.
        cached = getattr(obj, "_prefetched_objects_cache", {}).get("events")
        return list(cached) if cached is not None else list(obj.events.all())

    def get_stops(self, obj):
        events = self._events_for(obj)
        stops = list(obj.trip.itinerary.order_by("sequence"))
        origin_departure = (
            obj.atd
            if obj.atd is not None and obj.atd_source != VehicleRun.ATD_AUTO_DISPATCH
            else None
        )
        rows = []
        for index, stop in enumerate(stops):
            arrivals = [
                event
                for event in events
                if event.stop_id == stop.id
                and event.kind
                in {MovementEvent.KIND_ARRIVE_STOP, MovementEvent.KIND_ARRIVE_DESTINATION}
            ]
            departures = [
                event
                for event in events
                if event.stop_id == stop.id and event.kind == MovementEvent.KIND_DEPART_STOP
            ]
            arrival = arrivals[-1] if arrivals else None
            departure = departures[-1] if departures else None
            # Stop 1 is the origin. Once the wheel-roll is confirmed, its
            # departure is the run's ATD; it never becomes an arrival stop.
            origin_departure_at = (
                origin_departure if index == 0 and departure is None else None
            )
            departed_at = (
                departure.actual_at if departure else origin_departure_at
            )
            rows.append(
                {
                    "id": stop.id,
                    "sequence": stop.sequence,
                    "location_name": stop.location_name,
                    "latitude": stop.latitude,
                    "longitude": stop.longitude,
                    "mgrs": stop.mgrs,
                    "planned_arrival": stop.planned_arrival,
                    "planned_departure": stop.planned_departure,
                    "arrived_at": arrival.actual_at if arrival else None,
                    "departed_at": departed_at,
                    # The movement log is read for where the driver said they
                    # were, so the tap's own position travels with the stop.
                    "arrived_position": _tap_position(arrival),
                    "departed_position": _tap_position(departure),
                    "arrival_source": arrival.source if arrival else "",
                    "departure_source": (
                        departure.source
                        if departure
                        else obj.atd_source
                        if origin_departure_at is not None
                        else ""
                    ),
                    "status": (
                        "departed"
                        if departed_at is not None
                        else "arrived"
                        if arrival
                        else "pending"
                    ),
                }
            )
        return rows

    def get_recent_pings(self, obj):
        rows = list(obj.pings.order_by("-received_at", "-id")[:240])
        rows.reverse()
        return TrackingPingSerializer(rows, many=True).data

    def get_is_live(self, obj):
        if obj.status != VehicleRun.STATUS_ACTIVE or obj.last_ping_at is None:
            return False
        from apps.core import timeutils

        return timeutils.now() - obj.last_ping_at <= dt.timedelta(seconds=120)


class VehicleTripWriteSerializer(serializers.ModelSerializer):
    itinerary = TripItinerarySerializer(many=True, required=False)
    vehicle_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False
    )

    class Meta:
        model = VehicleTrip
        fields = [
            "id",
            "title",
            "purpose",
            "mission",
            "status",
            "departure_at",
            "return_at",
            "remarks",
            "itinerary",
            "vehicle_ids",
        ]

    def validate_vehicle_ids(self, value):
        if not value:
            return value
        found = set(
            Vehicle.objects.filter(pk__in=value, is_active=True).values_list("id", flat=True)
        )
        missing = [vid for vid in value if vid not in found]
        if missing:
            raise serializers.ValidationError(
                f"Unknown or inactive vehicle id(s): {missing}."
            )
        return value

    def validate_itinerary(self, value):
        sequences = [entry.get("sequence") for entry in value if entry.get("sequence")]
        if len(sequences) != len(set(sequences)):
            raise serializers.ValidationError("Itinerary sequence numbers must be unique.")
        return value

    def validate(self, attrs):
        dep = attrs.get("departure_at", getattr(self.instance, "departure_at", None))
        ret = attrs.get("return_at", getattr(self.instance, "return_at", None))
        if dep and ret and ret < dep:
            raise serializers.ValidationError(
                {"return_at": "Return time must not precede departure."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        itinerary = validated_data.pop("itinerary", [])
        vehicle_ids = validated_data.pop("vehicle_ids", [])
        trip = services.create_trip_with_number(**validated_data)
        for index, entry in enumerate(itinerary, start=1):
            entry["sequence"] = entry.get("sequence") or index
            TripItinerary.objects.create(trip=trip, **entry)
        for vehicle_id in vehicle_ids:
            VehicleTripVehicle.objects.create(trip=trip, vehicle_id=vehicle_id)
        return trip

    @transaction.atomic
    def update(self, instance, validated_data):
        itinerary = validated_data.pop("itinerary", None)
        vehicle_ids = validated_data.pop("vehicle_ids", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if itinerary is not None:
            instance.itinerary.all().delete()
            for index, entry in enumerate(itinerary, start=1):
                entry["sequence"] = entry.get("sequence") or index
                TripItinerary.objects.create(trip=instance, **entry)

        if vehicle_ids is not None:
            instance.vehicles.all().delete()
            for vehicle_id in vehicle_ids:
                VehicleTripVehicle.objects.create(trip=instance, vehicle_id=vehicle_id)
        return instance


MRM_STATUS_LABELS = {
    "not_created": "Not yet created",
    "pending": "Pending",
    "in_progress": "In progress",
    "submitted": "Submitted for evaluation",
    "approved": "Approved",
    "returned": "Returned for revision",
}


def mrm_state(mission) -> dict:
    """Derived MRM state for a mission.

    The legacy ``mrm_forms`` row stays the record of truth; this only translates
    its workflow columns (plus the mission link) into the statuses the mission
    screen shows. Nothing is stored twice.
    """
    from django.utils import timezone

    assessment = getattr(mission, "risk_assessment", None)
    form = getattr(assessment, "mrm_form", None) if assessment else None

    team_assigned = any(row.is_active for row in mission.mrm_team.all())
    if assessment is None:
        # A team but no assessment means the work is assigned and pending.
        status = "pending" if team_assigned else "not_created"
    elif form is None:
        status = "pending"
    elif form.authority_approved:
        status = "approved"
    elif form.review_status in {"needs_revision", "rejected"}:
        status = "returned"
    elif form.status in {"submitted", "reviewed"}:
        status = "submitted"
    else:
        status = "in_progress"

    # Air operations monitor the flight itself and carry no MRM. Ground
    # operations keep the assessment requirement.
    requires_mrm = mission.category != Mission.CATEGORY_AIR and mission.status != "cancelled" and bool(
        mission.start_at or mission.status in {"planned", "approved", "active"}
    )
    now = timezone.now()
    overdue = bool(
        requires_mrm
        and status != "approved"
        and mission.start_at is not None
        and mission.start_at < now
    )
    due_soon = bool(
        requires_mrm
        and status != "approved"
        and mission.start_at is not None
        and not overdue
        and mission.start_at <= now + dt.timedelta(hours=24)
    )

    return {
        "status": status,
        "label": MRM_STATUS_LABELS[status],
        "requires_mrm": requires_mrm,
        "overdue": overdue,
        "due_soon": due_soon,
        "pending_action": requires_mrm and status not in {"approved"},
        "mrm_form": form.id if form else None,
        "mrm_number": form.mrm_number if form else "",
        "evaluator": form.reviewed_by_id if form else None,
        "evaluator_name": form.reviewer_name if form else "",
        "action_url": f"/missions/{mission.id}#mrm",
    }


class MissionRiskTeamSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="user.full_name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    rank = serializers.CharField(source="user.rank", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = MissionRiskTeam
        fields = [
            "id",
            "user",
            "name",
            "username",
            "rank",
            "role",
            "role_label",
            "is_active",
            "assigned_at",
        ]


class MissionSerializer(serializers.ModelSerializer):
    """Read representation, including category-specific detail."""

    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True)
    personnel = MissionPersonnelSerializer(many=True, read_only=True)
    ground_detail = GroundDetailSerializer(read_only=True)
    air_detail = AirDetailSerializer(read_only=True)
    operational_areas = MissionOperationalAreaSerializer(many=True, read_only=True)
    trips = VehicleTripSerializer(many=True, read_only=True)
    risk_form_number = serializers.CharField(source="risk_form.mrm_number", read_only=True)
    risk_assessment = serializers.SerializerMethodField()
    mrm = serializers.SerializerMethodField()
    mrm_team = serializers.SerializerMethodField()
    routing = serializers.SerializerMethodField()
    routing_stage = serializers.SerializerMethodField()
    route_plan = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()
    approval = serializers.SerializerMethodField()
    execution = serializers.SerializerMethodField()
    mission_order = serializers.SerializerMethodField()
    closing = serializers.SerializerMethodField()
    abort = serializers.SerializerMethodField()
    vehicles = serializers.SerializerMethodField()
    dependency = serializers.SerializerMethodField()
    is_airborne = serializers.SerializerMethodField()
    timing_warnings = serializers.SerializerMethodField()
    air_fuel = serializers.SerializerMethodField()

    class Meta:
        model = Mission
        fields = [
            "id",
            "mission_number",
            "category",
            "title",
            "purpose",
            "unit",
            "unit_name",
            "unit_acronym",
            "office_section",
            "status",
            "is_held",
            "held_at",
            "is_airborne",
            "timing_warnings",
            "air_fuel",
            "depends_on",
            "dependency_release",
            "dependency_buffer_minutes",
            "dependency",
            "start_at",
            "end_at",
            "location_name",
            "latitude",
            "longitude",
            "mgrs",
            "risk_form",
            "risk_form_number",
            "risk_assessment",
            "mrm",
            "mrm_team",
            "created_by",
            "created_by_name",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "remarks",
            "personnel",
            "ground_detail",
            "air_detail",
            "operational_areas",
            "trips",
            "routing",
            "routing_stage",
            "route_plan",
            "can_approve",
            "approval",
            "execution",
            "mission_order",
            "closing",
            "abort",
            "vehicles",
            "created_at",
            "updated_at",
        ]

    def get_dependency(self, obj):
        from apps.ops import approvals

        return approvals.dependency_state(obj)

    def get_is_airborne(self, obj) -> bool:
        return obj.air_is_airborne()

    def get_timing_warnings(self, obj) -> list[dict]:
        from apps.ops import timing

        return timing.mission_warnings(obj)

    def get_air_fuel(self, obj):
        from apps.ops import air_fuel

        return air_fuel.mission_payload(obj)

    def get_mrm(self, obj):
        return mrm_state(obj)

    def get_mrm_team(self, obj):
        rows = [row for row in obj.mrm_team.all() if row.is_active]
        return MissionRiskTeamSerializer(rows, many=True).data

    def get_routing(self, obj):
        """Every routing action in order, ready for the trail table.

        Labels and the actor's name are resolved here so the page does not have
        to duplicate the stage and action vocabularies.
        """
        from apps.command.models import MissionRouting

        stage_labels = dict(MissionRouting.STAGE_CHOICES)
        action_labels = dict(MissionRouting.ACTION_CHOICES)
        rows = []
        for row in obj.routing.select_related("actor").all():
            actor = row.actor if row.actor_id else None
            rows.append(
                {
                    "id": row.id,
                    "stage": row.stage,
                    "stage_label": stage_labels.get(row.stage, row.stage),
                    "action": row.action,
                    "action_label": action_labels.get(row.action, row.action),
                    "actor": getattr(actor, "username", "") or "",
                    "actor_name": (
                        (getattr(actor, "full_name", "") or getattr(actor, "username", ""))
                        if actor
                        else ""
                    ),
                    "note": row.note,
                    "created_at": row.created_at,
                }
            )
        return rows

    def get_routing_stage(self, obj) -> str:
        """Where the mission currently sits in the configured approval chain."""
        from apps.ops import approvals

        if obj.approval_status == Mission.APPROVAL_APPROVED:
            # Mission approval is complete; the Mission Order signature is a
            # separate post-approval state reported in ``mission_order``.
            return "approved"
        plan = approvals.route_plan(obj)
        if plan["current_stage"]:
            return plan["current_stage"]
        if plan["complete"]:
            return "decision_authority"
        return "draft"

    def get_route_plan(self, obj) -> dict:
        from apps.ops import approvals

        return approvals.route_plan(obj)

    def get_approval(self, obj) -> dict:
        from apps.ops import approvals

        data = approvals.approval_display(obj)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        plan = approvals.route_plan(obj)
        current_assignee = plan["current_assignee_id"]
        # At the end of the route the designated signer may act even though the
        # current-step helper has no remaining intermediate post.
        assigned_here = current_assignee == getattr(user, "id", None) or (
            plan["complete"] and obj.approving_authority_id == getattr(user, "id", None)
        )
        data["can_decide"] = bool(
            user is not None
            and getattr(user, "is_authenticated", False)
            and assigned_here
            and plan["started"]
            and obj.approval_status == Mission.APPROVAL_PENDING
            and obj.status not in {"completed", "cancelled"}
        )
        data["eligible"] = approvals.eligible_authorities(obj)
        return data

    def get_execution(self, obj) -> dict:
        from apps.ops import approvals

        return approvals.readiness(obj)

    def get_mission_order(self, obj) -> dict:
        """The Mission Order's planning, signature and approval state."""
        from apps.ops import mission_order_workflow

        request = self.context.get("request")
        return mission_order_workflow.state(obj, getattr(request, "user", None))

    def get_abort(self, obj):
        """The abort order on this mission, or None while it is still running."""
        from apps.ops import tracking

        return tracking.abort_payload(obj)

    def get_closing(self, obj) -> dict:
        """Whether the Operations Center may end this mission now, and why.

        A mission in progress becomes endable when its planned end has passed
        or every vehicle trip it planned is completed - the two points at which
        the plan itself says the work is over. ``open_runs`` reports the runs
        that closing would cancel, so the confirmation can be explicit about
        the override instead of hiding it.
        """
        closed = {"available": False, "reasons": [], "reason": "", "open_runs": 0}
        if obj.status != "active":
            return closed

        reasons: list[str] = []
        labels: list[str] = []
        if obj.end_at is not None and obj.end_at <= timeutils.now():
            reasons.append("planned_end_passed")
            labels.append(
                f"The estimated end, {timeutils.format_datetime(obj.end_at)}, has passed."
            )

        trips = [trip for trip in obj.trips.all() if trip.status != "cancelled"]
        if trips and all(trip.status == "completed" for trip in trips):
            reasons.append("trips_completed")
            labels.append(
                "All vehicle trips are completed."
                if len(trips) > 1
                else "The vehicle trip is completed."
            )

        if not reasons:
            return closed

        open_runs = obj.vehicle_runs.filter(
            status__in=[VehicleRun.STATUS_PENDING, VehicleRun.STATUS_ACTIVE]
        ).count()
        return {
            "available": True,
            "reasons": reasons,
            "reason": " ".join(labels),
            "open_runs": open_runs,
        }

    def get_vehicles(self, obj) -> list:
        """Vehicles planned for this mission, one entry per planned route.

        Each vehicle gets its own trip, so two vehicles with different
        itineraries are never flattened into one shared route.
        """
        rows = []
        for trip in obj.trips.all():
            # A cancelled trip is history, not a planned route.
            if trip.status == "cancelled":
                continue
            itinerary = [
                {
                    "id": stop.id,
                    "sequence": stop.sequence,
                    "location_name": stop.location_name,
                    "latitude": stop.latitude,
                    "longitude": stop.longitude,
                    "mgrs": stop.mgrs,
                    "planned_departure": stop.planned_departure,
                    "planned_arrival": stop.planned_arrival,
                }
                for stop in trip.itinerary.all()
            ]
            for link in trip.vehicles.all():
                vehicle = link.vehicle
                rows.append(
                    {
                        "trip_id": trip.id,
                        "trip_number": trip.trip_number,
                        "trip_status": trip.status,
                        "vehicle_id": vehicle.id,
                        "display_name": vehicle.display_name,
                        "make_model": vehicle.make_model,
                        "fuel_type": vehicle.fuel_type,
                        "is_pov": vehicle.is_pov,
                        "owner_name": vehicle.owner.full_name if vehicle.owner_id else "",
                        "registration_number": vehicle.registration_number,
                        "plate_number": vehicle.registration_no,
                        "vehicle_type": vehicle.vehicle_type,
                        "capacity": vehicle.capacity,
                        "vehicle_status": vehicle.status,
                        "driver_id": link.driver_id,
                        "driver_name": link.driver.full_name if link.driver_id else "",
                        "itinerary": itinerary,
                        "route_status": trip.route_status,
                        "route_geometry": trip.route_geometry,
                        "route_legs": trip.route_legs,
                        "route_distance_m": trip.route_distance_m,
                        "route_duration_s": trip.route_duration_s,
                        "route_provider": trip.route_provider,
                        "route_calculated_at": trip.route_calculated_at,
                        "route_error": trip.route_error,
                    }
                )
        return rows

    def get_can_approve(self, obj) -> bool:
        request = self.context.get("request")
        if request is None:
            return False
        from apps.core.roles import approver_roles_for

        rows = list(obj.routing.all())
        if not any(
            row.stage == "operations_officer" and row.action == "approved" for row in rows
        ):
            return False
        permitted = set(approver_roles_for("mission", obj.unit_id, obj.category))
        from apps.core.roles import effective_roles

        return bool(effective_roles(request.user, obj.unit_id) & permitted)

    def get_risk_assessment(self, obj):
        assessment = getattr(obj, "risk_assessment", None)
        if not assessment:
            return None
        from apps.ops import decision_authority

        form = getattr(assessment, "mrm_form", None)
        creator_name = ""
        authority_label = ""
        authority_level = ""
        authority_name = ""
        authority_id = None
        creator_signed = False
        evaluator_signed = False
        authority_approved = False
        if form is not None:
            creator_name = form.creator_name or ""
            if not creator_name and form.created_by_id:
                creator = User.objects.filter(pk=form.created_by_id).first()
                creator_name = creator.full_name if creator else ""
            resolution = decision_authority.resolve(form)
            authority_label = resolution.label
            authority_level = form.authority_level or ""
            authority_name = resolution.user.full_name if resolution.user else ""
            authority_id = resolution.user.id if resolution.user else None
            creator_signed = bool(form.creator_signed)
            evaluator_signed = bool(form.evaluator_signed)
            authority_approved = bool(form.authority_approved)
        return {
            "id": assessment.id,
            "status": assessment.status,
            "evaluator_id": assessment.evaluator_id,
            "evaluator_name": assessment.evaluator.full_name if assessment.evaluator_id else "",
            "creator_name": creator_name,
            "decision_authority_label": authority_label,
            "decision_authority_id": authority_id,
            "authority_level": authority_level,
            "decision_authority_name": authority_name,
            "creator_signed": creator_signed,
            "evaluator_signed": evaluator_signed,
            "authority_approved": authority_approved,
            "risk_category": assessment.risk_category,
            "total_percent": assessment.total_percent,
        }


MRM_TEAM_ROLES = {choice[0] for choice in MissionRiskTeam.ROLE_CHOICES}


def validate_mrm_team(entries, unit, mission=None):
    """Validate MRM team entries against unit, activity and capability rules.

    ``mission`` is supplied when the team is assigned to an existing mission.
    It carries two extra rules from the planning workflow: the MRM *creator*
    (role ``lead``) must be one of the personnel actually involved in that
    mission, and the *evaluator* (role ``reviewer``) must belong to the unit
    the mission belongs to and hold evaluation authority there.
    """
    from apps.core import roles

    from apps.accounts.models import User

    if not entries:
        return []
    if unit is None:
        raise serializers.ValidationError(
            {"mrm_team": "Select the responsible unit before assigning an MRM team."}
        )

    cleaned = []
    seen: set[int] = set()
    for entry in entries:
        raw_id = entry.get("user") or entry.get("user_id")
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            raise serializers.ValidationError(
                {"mrm_team": "Each MRM team row needs a valid personnel id."}
            )
        if user_id in seen:
            raise serializers.ValidationError(
                {"mrm_team": "The same person cannot be assigned twice to the MRM team."}
            )
        seen.add(user_id)

        person = User.objects.filter(pk=user_id).first()
        if person is None:
            raise serializers.ValidationError(
                {"mrm_team": f"Personnel record {user_id} does not exist."}
            )
        if not person.is_active:
            raise serializers.ValidationError(
                {"mrm_team": f"{person.full_name or person.username} is inactive and cannot be assigned."}
            )
        role = entry.get("role") or MissionRiskTeam.ROLE_ASSESSOR
        if role not in MRM_TEAM_ROLES:
            raise serializers.ValidationError(
                {"mrm_team": f"Unknown MRM role {role!r}."}
            )

        # The evaluator is unit-specific: the mission's own unit supplies the
        # person who evaluates its assessment.
        if role == MissionRiskTeam.ROLE_REVIEWER:
            if person.unit_id != unit.id:
                raise serializers.ValidationError(
                    {
                        "mrm_team": (
                            f"{person.full_name or person.username} is not assigned to "
                            f"{unit.acronym or unit.name}, so cannot be its MRM evaluator."
                        )
                    }
                )
            if not roles.has_capability(person, roles.CAP_EVALUATE, unit.id):
                raise serializers.ValidationError(
                    {
                        "mrm_team": (
                            f"{person.full_name or person.username} does not hold MRM "
                            "evaluation authority in this unit."
                        )
                    }
                )
        elif not roles.can_prepare_mrm(person, unit.id):
            raise serializers.ValidationError(
                {
                    "mrm_team": (
                        f"{person.full_name or person.username} is not authorised to prepare "
                        f"an MRM for {unit.acronym or unit.name}."
                    )
                }
            )

        cleaned.append({"user": user_id, "role": role})

    if mission is not None:
        leads = [row for row in cleaned if row["role"] == MissionRiskTeam.ROLE_LEAD]
        for row in leads:
            involved = MissionPersonnel.objects.filter(
                mission=mission, user_id=row["user"], is_active=True
            ).exists()
            if not involved:
                person = User.objects.filter(pk=row["user"]).first()
                raise serializers.ValidationError(
                    {
                        "mrm_team": (
                            f"{getattr(person, 'full_name', None) or row['user']} is not "
                            "assigned to this mission, so cannot be its MRM creator. "
                            "Select the MRM creator from the personnel involved."
                        )
                    }
                )

    for role, label in (
        (MissionRiskTeam.ROLE_LEAD, "MRM creator"),
        (MissionRiskTeam.ROLE_REVIEWER, "MRM evaluator"),
    ):
        if sum(1 for row in cleaned if row["role"] == role) > 1:
            raise serializers.ValidationError(
                {"mrm_team": f"Assign one {label} per assessment."}
            )

    return cleaned


class MissionWriteSerializer(serializers.ModelSerializer):
    """Create/update with strict Air vs Ground separation."""

    ground_detail = GroundDetailSerializer(required=False)
    air_detail = AirDetailSerializer(required=False)
    #: Additional operational areas beyond the mission's primary one.
    operational_areas = MissionOperationalAreaSerializer(many=True, required=False)
    #: MRM team, written as ``[{"user": id, "role": "lead"}]``. Not a model
    #: field: it is applied to ``MissionRiskTeam`` rows after validation.
    mrm_team = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )
    #: Explicit override used while revising: drop the already-approved MRM
    #: link so the Operations Center can raise a fresh assessment.
    force_mrm_unlink = serializers.BooleanField(
        required=False, default=False, write_only=True
    )
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    mgrs = serializers.CharField(required=False, allow_blank=True)
    #: Air missions take their unit from the planner's own account, so the
    #: field is optional on the wire and filled in ``validate``.
    unit = serializers.PrimaryKeyRelatedField(
        queryset=Unit.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Mission
        fields = [
            "id",
            "category",
            "title",
            "purpose",
            "unit",
            "office_section",
            "status",
            "start_at",
            "end_at",
            "location_name",
            "latitude",
            "longitude",
            "mgrs",
            "risk_form",
            "depends_on",
            "dependency_release",
            "dependency_buffer_minutes",
            "remarks",
            "ground_detail",
            "air_detail",
            "operational_areas",
            "mrm_team",
            "force_mrm_unlink",
        ]

    # -- field validation -------------------------------------------------

    def validate_unit(self, value):
        if value is not None and not value.is_active:
            raise serializers.ValidationError("The selected unit is inactive.")
        # Unit scope is a server rule, not a form rule: a caller cannot plan a
        # mission for a unit outside their authorised hierarchy by posting the
        # id directly. Without this the created row would fall outside the
        # caller's visible queryset and the response could not be rendered.
        user = getattr(self.context.get("request"), "user", None)
        if value is not None and user is not None and getattr(user, "is_authenticated", False):
            from apps.core.scoping import accessible_unit_ids

            allowed = accessible_unit_ids(user)
            if allowed and value.id not in allowed:
                raise serializers.ValidationError(
                    "That unit is outside your authorised scope."
                )
        return value

    def validate_risk_form(self, value):
        if value is None:
            return value
        if not MRMForm.objects.filter(pk=value.pk).exists():
            raise serializers.ValidationError("The selected MRM form does not exist.")
        # One assessment describes one mission; letting two missions share a
        # form would make each one's risk status describe the other's activity.
        holders = Mission.objects.filter(risk_form=value)
        if self.instance is not None:
            holders = holders.exclude(pk=self.instance.pk)
        other = holders.first()
        if other is not None:
            raise serializers.ValidationError(
                f"MRM {value.mrm_number} is already linked to mission "
                f"{other.mission_number or other.title}. Each mission needs its own "
                "assessment."
            )
        return value

    def validate_mgrs(self, value):
        if not value:
            return value
        try:
            return validate_mgrs(value)
        except MGRSError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_status(self, value):
        """The execution gate, enforced on the generic write path too.

        Without this a caller could ``PATCH {"status": "active"}`` and launch a
        mission whose MRM or approval is incomplete. Approving is likewise not a
        status a planner may simply set: it is a decision recorded by the
        assigned approving authority through their own endpoint.
        """
        from apps.ops import approvals

        instance = self.instance
        if instance is None:
            if value in {"active", "completed"}:
                raise serializers.ValidationError(
                    "A mission cannot be created already executed or completed."
                )
            if value == "approved":
                raise serializers.ValidationError(
                    "Approval is recorded by the assigned approving authority, not set "
                    "directly on the mission."
                )
            return value

        if value == instance.status:
            return value
        if value == "active" and instance.status != "active":
            try:
                approvals.assert_dispatchable(instance)
            except approvals.ExecutionBlocked as exc:
                raise serializers.ValidationError(
                    f"{exc} Outstanding: "
                    + "; ".join(b["label"] for b in exc.blockers)
                ) from exc
        if value == "approved" and instance.approval_status != Mission.APPROVAL_APPROVED:
            raise serializers.ValidationError(
                "Approval is recorded by the assigned approving authority, not set "
                "directly on the mission."
            )
        return value

    def validate(self, attrs):
        category = attrs.get("category", getattr(self.instance, "category", None))
        has_ground = "ground_detail" in attrs
        has_air = "air_detail" in attrs

        if category == Mission.CATEGORY_GROUND and has_air:
            raise serializers.ValidationError(
                {"air_detail": "Air fields cannot be supplied for a ground operation."}
            )
        if category == Mission.CATEGORY_AIR and has_ground:
            raise serializers.ValidationError(
                {"ground_detail": "Ground fields cannot be supplied for an air operation."}
            )
        if category == Mission.CATEGORY_AIR and "operational_areas" in attrs:
            raise serializers.ValidationError(
                {
                    "operational_areas": (
                        "Additional operational areas belong to ground operations."
                    )
                }
            )

        if self.instance is None:
            if category == Mission.CATEGORY_AIR:
                # No UPR picker on the air form: the flight belongs to the
                # planner's own unit and the Operations Center is responsible
                # for it. Both are server rules, not form suggestions.
                if attrs.get("unit") is None:
                    request = self.context.get("request")
                    own_unit = getattr(getattr(request, "user", None), "unit", None)
                    if own_unit is None:
                        raise serializers.ValidationError(
                            {
                                "unit": (
                                    "Your account has no unit, so this air mission "
                                    "cannot be attributed to one."
                                )
                            }
                        )
                    attrs["unit"] = own_unit
                if not (attrs.get("office_section") or "").strip():
                    attrs["office_section"] = "Operations Center"

            if category == Mission.CATEGORY_GROUND:
                if attrs.get("unit") is None:
                    raise serializers.ValidationError(
                        {"unit": "Select the unit primary responsible."}
                    )
                detail = attrs.get("ground_detail") or {}
                missing = [
                    field
                    for field in ("estimated_start", "estimated_end")
                    if not detail.get(field)
                ]
                if missing:
                    raise serializers.ValidationError(
                        {
                            "ground_detail": (
                                "Ground operations require an estimated start and end "
                                f"({', '.join(missing)} missing)."
                            )
                        }
                    )
            if category == Mission.CATEGORY_AIR:
                detail = attrs.get("air_detail") or {}
                missing = [
                    field
                    for field in ("aircraft_type", "flight_identification")
                    if not detail.get(field)
                ]
                if missing:
                    raise serializers.ValidationError(
                        {
                            "air_detail": (
                                "Air operations require aircraft type and flight "
                                f"identification ({', '.join(missing)} missing)."
                            )
                        }
                    )

        # The planning form collects estimated times only; the mission header
        # keeps start_at/end_at for ordering, reporting and map date filters,
        # derived from those estimates so the two can never disagree.
        if category == Mission.CATEGORY_GROUND:
            detail = attrs.get("ground_detail")
            if detail is not None:
                if attrs.get("start_at") is None and detail.get("estimated_start"):
                    attrs["start_at"] = detail["estimated_start"]
                if attrs.get("end_at") is None and detail.get("estimated_end"):
                    attrs["end_at"] = detail["estimated_end"]
        elif category == Mission.CATEGORY_AIR:
            detail = attrs.get("air_detail")
            if detail is not None:
                if attrs.get("start_at") is None and detail.get("scheduled_departure"):
                    attrs["start_at"] = detail["scheduled_departure"]
                if attrs.get("end_at") is None and detail.get("scheduled_arrival"):
                    attrs["end_at"] = detail["scheduled_arrival"]

        start = attrs.get("start_at", getattr(self.instance, "start_at", None))
        end = attrs.get("end_at", getattr(self.instance, "end_at", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end_at": "End must not precede start."})

        lat, lon, mgrs_value = validate_coordinates(
            attrs.get("latitude", getattr(self.instance, "latitude", None)),
            attrs.get("longitude", getattr(self.instance, "longitude", None)),
            attrs.get("mgrs", getattr(self.instance, "mgrs", "")),
        )
        attrs["latitude"], attrs["longitude"], attrs["mgrs"] = lat, lon, mgrs_value

        if self.instance is not None and "risk_form" in attrs:
            assessment = getattr(self.instance, "risk_assessment", None)
            approved_form = getattr(assessment, "mrm_form", None) if assessment else None
            if approved_form is not None and approved_form.authority_approved:
                # Unlinking needs the explicit override; silently dropping the
                # approved assessment is still refused.
                forced = bool(attrs.get("force_mrm_unlink")) and attrs.get("risk_form") is None
                if not forced and (
                    attrs["risk_form"] is None or attrs["risk_form"].pk != approved_form.pk
                ):
                    raise serializers.ValidationError(
                        {
                            "risk_form": (
                                "This mission's MRM is already approved and cannot be "
                                "replaced by another assessment."
                            )
                        }
                    )

        if "mrm_team" in attrs:
            unit = attrs.get("unit") or getattr(self.instance, "unit", None)
            if self.instance is None:
                # The creator must be one of the mission's personnel, and those
                # are recorded after the mission exists. Accepting a team here
                # would let an assessment be assigned to somebody the mission
                # never lists.
                raise serializers.ValidationError(
                    {
                        "mrm_team": (
                            "Record the mission and its personnel first, then assign the "
                            "MRM creator and evaluator."
                        )
                    }
                )
            attrs["mrm_team"] = validate_mrm_team(
                attrs["mrm_team"], unit, mission=self.instance
            )
            assessment = (
                getattr(self.instance, "risk_assessment", None) if self.instance else None
            )
            form = getattr(assessment, "mrm_form", None) if assessment else None
            if form is not None and form.authority_approved:
                raise serializers.ValidationError(
                    {
                        "mrm_team": (
                            "This mission's MRM is already approved. Reassigning the team "
                            "would rewrite an approved assessment's accountability."
                        )
                    }
                )

        mission_category = attrs.get(
            "category", getattr(self.instance, "category", None)
        )
        dependency = attrs.get(
            "depends_on", getattr(self.instance, "depends_on", None)
        )
        release = attrs.get(
            "dependency_release",
            getattr(self.instance, "dependency_release", Mission.DEPENDENCY_COMPLETION),
        )
        buffer = attrs.get(
            "dependency_buffer_minutes",
            getattr(self.instance, "dependency_buffer_minutes", 30),
        )
        try:
            buffer = int(buffer)
        except (TypeError, ValueError):
            raise serializers.ValidationError(
                {"dependency_buffer_minutes": "Use a whole number of minutes."}
            ) from None
        if not 0 <= buffer <= 240:
            raise serializers.ValidationError(
                {"dependency_buffer_minutes": "Use a buffer between 0 and 240 minutes."}
            )
        if release not in {choice[0] for choice in Mission.DEPENDENCY_RELEASE_CHOICES}:
            raise serializers.ValidationError(
                {"dependency_release": "Choose how the predecessor releases this mission."}
            )
        if dependency is not None:
            if self.instance is not None and dependency.pk == self.instance.pk:
                raise serializers.ValidationError(
                    {"depends_on": "A mission cannot depend on itself."}
                )
            if dependency.status == "cancelled":
                raise serializers.ValidationError(
                    {"depends_on": "That mission was cancelled and cannot release this one."}
                )
            if mission_category and dependency.category != mission_category:
                raise serializers.ValidationError(
                    {"depends_on": "The predecessor must be the same mission category."}
                )
            mission_unit = attrs.get("unit") or getattr(self.instance, "unit", None)
            if mission_unit is not None and dependency.unit_id != mission_unit.id:
                raise serializers.ValidationError(
                    {"depends_on": "The predecessor must belong to the same unit."}
                )
            if release == Mission.DEPENDENCY_RENDEZVOUS_READY and mission_category != Mission.CATEGORY_AIR:
                raise serializers.ValidationError(
                    {
                        "dependency_release": (
                            "Rendezvous-ready release applies to air missions only."
                        )
                    }
                )
            seen: set[int] = set()
            node = dependency
            while node is not None:
                if node.pk in seen:
                    raise serializers.ValidationError(
                        {"depends_on": "That dependency would create a loop."}
                    )
                if self.instance is not None and node.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        {"depends_on": "That dependency would create a loop."}
                    )
                seen.add(node.pk)
                node = node.depends_on
            if mission_category == Mission.CATEGORY_AIR and "air_detail" in attrs:
                selected = attrs["air_detail"].get("air_asset_ids")
                predecessor_detail = getattr(dependency, "air_detail", None)
                if selected is not None and predecessor_detail is not None:
                    predecessor_assets = set(
                        predecessor_detail.assigned_aircraft.exclude(
                            air_asset__isnull=True
                        ).values_list("air_asset_id", flat=True)
                    )
                    selected_ids = {asset.pk for asset in selected}
                    if predecessor_assets - selected_ids:
                        raise serializers.ValidationError(
                            {
                                "air_detail": (
                                    "The formation must include the aircraft flown by "
                                    "the preposition mission."
                                )
                            }
                        )
        if mission_category == Mission.CATEGORY_AIR and (
            "air_detail" in attrs
            or "depends_on" in attrs
            or "dependency_release" in attrs
            or "dependency_buffer_minutes" in attrs
        ):
            self._validate_air_formation(attrs, dependency, release, buffer)
        return attrs

    def _validate_air_formation(self, attrs, dependency, release, buffer):
        """Enforce the 5 km Element rule and its prepositioning dependency.

        An Element is valid only when every aircraft can be tied to an Air
        Asset with a recorded location and all of them are within 5 km. When
        they are farther apart, the mission must depend on a prepositioning
        mission that brings the separated aircraft to the joint origin, and
        the joint ETD must equal that aircraft's arrival time.
        """
        detail_data = attrs.get("air_detail")
        instance = self.instance
        detail = getattr(instance, "air_detail", None) if instance else None
        if detail_data is None and detail is None:
            return

        from apps.core import roles
        from apps.core.scoping import accessible_unit_ids

        request = self.context.get("request")
        user = getattr(request, "user", None)
        allowed_unit_ids = None
        if user is not None and not roles.has_capability(
            user, roles.CAP_SYSTEM_CONFIG
        ):
            allowed_unit_ids = accessible_unit_ids(user)

        analysis = (
            formation_rules.analyze_payload(
                detail_data, allowed_unit_ids=allowed_unit_ids
            )
            if detail_data is not None
            else formation_rules.analyze_instance(
                detail, allowed_unit_ids=allowed_unit_ids
            )
        )
        messages: list[str] = []

        if analysis["outside_scope"]:
            messages.append(
                "These aircraft are outside your authorised unit scope: "
                + ", ".join(analysis["outside_scope"])
                + "."
            )

        if (
            analysis["unresolved"]
            and detail_data is not None
            and detail_data.get("elements") is not None
        ):
            messages.append(
                "Each aircraft must be selected from the unit's Air Assets "
                "register so its location can be checked: "
                + ", ".join(analysis["unresolved"])
                + "."
            )

        if analysis["missing_locations"]:
            messages.append(
                "Record a current MGRS location for these aircraft before "
                "forming an Element: "
                + ", ".join(analysis["missing_locations"])
                + "."
            )

        unavailable = formation_rules.unavailable_assets(analysis["assets"])
        if unavailable:
            messages.append(
                "These aircraft are not available for planning: "
                + ", ".join(
                    formation_rules.asset_label(asset) for asset in unavailable
                )
                + "."
            )

        def detail_value(name):
            if detail_data is not None and detail_data.get(name) is not None:
                return detail_data[name]
            return getattr(detail, name, None) if detail else None

        start = detail_value("scheduled_departure")
        end = detail_value("scheduled_arrival")
        conflicts = formation_rules.asset_conflicts(
            analysis["asset_ids"],
            start,
            end,
            exclude_mission_id=getattr(instance, "pk", None),
        )
        if conflicts:
            messages.append(
                "These aircraft are already assigned to an overlapping "
                "mission: "
                + "; ".join(
                    f"{row['tail_number']} on {row['mission_number']}"
                    for row in conflicts
                )
                + "."
            )

        if not (analysis["checked"] and analysis["separated"]):
            if messages:
                raise serializers.ValidationError({"air_detail": messages})
            return

        separation = analysis["max_distance_km"]
        separation_message = (
            "The selected aircraft are more than "
            f"{formation_rules.COLOCATION_RADIUS_KM:g} km apart"
            + (
                f" (furthest {separation:.1f} km)"
                if separation is not None
                else ""
            )
            + ". They cannot form an Element until a prepositioning mission "
            "brings them to the joint origin."
        )

        formation_messages: list[str] = []
        predecessor_detail = (
            getattr(dependency, "air_detail", None) if dependency else None
        )
        if dependency is None:
            formation_messages.append(
                "The aircraft are more than 5 km apart. Link the "
                "prepositioning mission as this mission's dependency."
            )
        else:
            if release != Mission.DEPENDENCY_COMPLETION:
                formation_messages.append(
                    "Release this mission only when the prepositioning mission "
                    "is completed. Its ATD stays on hold until then."
                )
            if buffer != 0:
                formation_messages.append(
                    "Use a zero-minute buffer: a separated Element's ETD must "
                    "match the incoming aircraft's arrival time exactly."
                )
            if predecessor_detail is None:
                formation_messages.append(
                    "The linked prepositioning mission has no air operations "
                    "detail to supply an arrival time."
                )
            else:
                incoming_eta = (
                    predecessor_detail.actual_arrival
                    or predecessor_detail.rendezvous_ready_at
                    or predecessor_detail.scheduled_arrival
                )
                if incoming_eta is None:
                    formation_messages.append(
                        "Record the incoming aircraft's estimated arrival time "
                        "on the prepositioning mission."
                    )
                elif start is None:
                    formation_messages.append(
                        "Set the joint mission's ETD from the incoming "
                        "aircraft's arrival time."
                    )
                elif start != incoming_eta:
                    formation_messages.append(
                        "The joint ETD must exactly match the incoming "
                        f"aircraft's arrival time ({incoming_eta:%d %b %Y, %H%M}H)."
                    )

                successor_origin = detail_value("departure_location") or ""
                predecessor_destination = (
                    predecessor_detail.destination_location or ""
                )
                if not formation_rules.same_place(
                    successor_origin, predecessor_destination
                ):
                    formation_messages.append(
                        "The prepositioning mission must end at the joint "
                        "mission's origin (the assembly location)."
                    )

                predecessor_ids = {
                    asset.pk
                    for asset in formation_rules.assets_from_detail(
                        predecessor_detail
                    )
                }
                selected_ids = set(analysis["asset_ids"])
                incoming_ids = set(analysis["incoming_asset_ids"])
                if not predecessor_ids:
                    formation_messages.append(
                        "The linked prepositioning mission has no aircraft "
                        "assigned to it."
                    )
                elif incoming_ids - predecessor_ids:
                    formation_messages.append(
                        "The prepositioning mission must fly the aircraft that "
                        "are separated from the joint origin."
                    )
                elif predecessor_ids - selected_ids:
                    formation_messages.append(
                        "The formation must include every aircraft flown by "
                        "the prepositioning mission."
                    )

        if formation_messages:
            messages.extend(formation_messages)
            messages.append(separation_message)
        if messages:
            raise serializers.ValidationError({"air_detail": messages})

    # -- persistence ------------------------------------------------------

    def _apply_mrm_team(self, mission, entries):
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        keep: list[int] = []
        for entry in entries:
            row, _created = MissionRiskTeam.objects.update_or_create(
                mission=mission,
                user_id=entry["user"],
                defaults={
                    "role": entry["role"],
                    "is_active": True,
                    "assigned_by": actor,
                },
            )
            keep.append(row.user_id)
        # Removing a person ends their assignment instead of deleting history.
        MissionRiskTeam.objects.filter(mission=mission).exclude(user_id__in=keep).update(
            is_active=False
        )

    @staticmethod
    def _apply_operational_areas(mission, rows):
        """Replace a mission's additional operational areas in one step."""
        MissionOperationalArea.objects.filter(mission=mission).delete()
        for index, row in enumerate(rows, start=1):
            MissionOperationalArea.objects.create(
                mission=mission,
                sequence=index,
                name=row["name"],
                latitude=row.get("latitude"),
                longitude=row.get("longitude"),
                mgrs=row.get("mgrs") or "",
            )

    @staticmethod
    def _apply_air_assets(detail, assets, crew=None):
        """Replace a mission's ordered formation rows in one step."""
        detail.assigned_aircraft.all().delete()
        if not assets:
            return
        crew_by_id = {}
        for row in crew or []:
            try:
                crew_by_id[int(row.get("air_asset"))] = row
            except (TypeError, ValueError):
                continue
        lead_id = detail.air_asset_id or assets[0].pk
        if detail.air_asset_id is None:
            detail.air_asset = assets[0]
            detail.save(update_fields=["air_asset"])
        AirMissionAsset.objects.bulk_create(
            [
                AirMissionAsset(
                    air_detail=detail,
                    air_asset=asset,
                    sequence=index,
                    is_lead=asset.pk == lead_id,
                    element_role=(
                        "element_lead" if asset.pk == lead_id else "wing_man"
                    ),
                    pilot_in_command=(
                        crew_by_id.get(asset.pk, {}).get("pilot_in_command") or ""
                    )[:140],
                    co_pilot=(
                        crew_by_id.get(asset.pk, {}).get("co_pilot") or ""
                    )[:140],
                    aircraft_model=asset.model,
                    tail_number=asset.tail_number,
                    callsign=asset.callsign,
                )
                for index, asset in enumerate(assets, start=1)
            ]
        )

    @staticmethod
    def _apply_air_elements(detail, elements):
        """Replace the daily sheet's manual elements, aircraft and legs."""
        detail.elements.all().delete()
        detail.assigned_aircraft.all().delete()
        detail.supporting_units.clear()
        for element_index, element_data in enumerate(elements, start=1):
            aircraft_rows = element_data.pop("aircraft", []) or []
            element = AirMissionElement.objects.create(
                air_detail=detail,
                sequence=element_index,
                mission=element_data.get("mission") or "",
                remarks=element_data.get("remarks") or "",
            )
            for aircraft_index, aircraft_data in enumerate(aircraft_rows, start=1):
                crew_rows = aircraft_data.pop("crew", []) or []
                itinerary_rows = aircraft_data.pop("itineraries", []) or []
                aircraft = AirMissionAircraft.objects.create(
                    element=element,
                    sequence=aircraft_index,
                    aircraft_type=(aircraft_data.get("aircraft_type") or "")[:120],
                    tail_number=(aircraft_data.get("tail_number") or "")[:60],
                    callsign=(aircraft_data.get("callsign") or "")[:60],
                    is_lead=bool(aircraft_data.get("is_lead", aircraft_index == 1)),
                )
                for crew_index, crew_data in enumerate(crew_rows, start=1):
                    if not (crew_data.get("name") or "").strip():
                        continue
                    AirMissionCrew.objects.create(
                        aircraft=aircraft,
                        sequence=crew_index,
                        role=crew_data.get("role") or AirMissionCrew.ROLE_PIC,
                        name=(crew_data.get("name") or "")[:140],
                    )
                for leg_index, leg_data in enumerate(itinerary_rows, start=1):
                    AirMissionItinerary.objects.create(
                        aircraft=aircraft,
                        sequence=leg_index,
                        etd=leg_data.get("etd"),
                        eta=leg_data.get("eta"),
                        atd=leg_data.get("atd"),
                        ata=leg_data.get("ata"),
                        tot=leg_data.get("tot"),
                        toot=leg_data.get("toot"),
                        status=leg_data.get("status") or AirMissionItinerary.STATUS_PLANNED,
                        origin=(leg_data.get("origin") or "")[:255],
                        destination=(leg_data.get("destination") or "")[:255],
                        route=leg_data.get("route") or "",
                        mission=leg_data.get("mission") or "",
                        point_role=leg_data.get("point_role") or "",
                        overflight=bool(leg_data.get("overflight")),
                        return_to_origin=bool(leg_data.get("return_to_origin")),
                        remarks=leg_data.get("remarks") or "",
                    )

    @classmethod
    def _sync_air_asset_lead(cls, detail):
        """Keep the legacy lead pointer and the formation's flag in step."""
        rows = detail.assigned_aircraft.all()
        if not rows.exists():
            if detail.air_asset_id:
                cls._apply_air_assets(detail, [detail.air_asset])
            return
        rows.update(is_lead=False, element_role="wing_man")
        rows.filter(air_asset_id=detail.air_asset_id).update(
            is_lead=True, element_role="element_lead"
        )

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop("force_mrm_unlink", None)
        ground = validated_data.pop("ground_detail", None)
        air = validated_data.pop("air_detail", None)
        team = validated_data.pop("mrm_team", None)
        areas = validated_data.pop("operational_areas", None)
        # Saving the planning form creates a planned mission, not a draft. The
        # draft state is reserved for a plan returned for correction.
        validated_data.setdefault("status", "planned")
        mission = services.create_mission_with_number(**validated_data)
        if ground is not None:
            GroundMissionDetail.objects.create(mission=mission, **ground)
        if air is not None:
            support_units = air.pop("supporting_units", None)
            air_assets = air.pop("air_asset_ids", None)
            air_crew = air.pop("aircraft_crew", None)
            elements = air.pop("elements", None)
            detail = AirMissionDetail.objects.create(mission=mission, **air)
            if support_units is not None:
                detail.supporting_units.set(support_units)
            if air_assets is not None:
                self._apply_air_assets(detail, air_assets, air_crew)
            elif detail.air_asset_id:
                self._apply_air_assets(detail, [detail.air_asset], air_crew)
            if elements is not None:
                self._apply_air_elements(detail, elements)
        if areas:
            self._apply_operational_areas(mission, areas)
        if team:
            self._apply_mrm_team(mission, team)
        self._sync_risk_link(mission)
        return mission

    @transaction.atomic
    def update(self, instance, validated_data):
        force_unlink = bool(validated_data.pop("force_mrm_unlink", False))
        ground = validated_data.pop("ground_detail", None)
        air = validated_data.pop("air_detail", None)
        team = validated_data.pop("mrm_team", None)
        areas = validated_data.pop("operational_areas", None)

        # Remember what the approval actually covered before touching anything.
        material_before = {
            field: getattr(instance, field, None) for field in Mission.MATERIAL_FIELDS
        }
        for field, value in validated_data.items():
            setattr(instance, field, value)

        changed = [
            field
            for field, previous in material_before.items()
            if getattr(instance, field, None) != previous
        ]
        # Personnel, areas and category detail describe what will actually
        # happen, so a change to any of them also invalidates an approval.
        if team is not None or areas is not None or ground is not None or air is not None:
            changed.append("details")
        if changed:
            # A material change makes any existing approval stale; the revision
            # counter is what the execution gate compares against.
            instance.revision = (instance.revision or 0) + 1
            instance.save(update_fields=list(validated_data) + ["revision", "updated_at"])
            if instance.approval_status == Mission.APPROVAL_APPROVED:
                from apps.ops import mission_order_workflow

                mission_order_workflow.reset(instance)
        else:
            instance.save()
        if team is not None:
            self._apply_mrm_team(instance, team)
        if areas is not None:
            self._apply_operational_areas(instance, areas)

        if force_unlink:
            # The assessment row is the mission's live MRM link; the MRM form
            # itself is kept for the record. A new assessment can then be
            # started from the mission.
            RiskAssessment.objects.filter(mission=instance).delete()

        if ground is not None:
            GroundMissionDetail.objects.update_or_create(
                mission=instance, defaults=ground
            )
        if air is not None:
            support_units = air.pop("supporting_units", None)
            air_assets = air.pop("air_asset_ids", None)
            air_crew = air.pop("aircraft_crew", None)
            elements = air.pop("elements", None)
            detail, _created = AirMissionDetail.objects.update_or_create(
                mission=instance, defaults=air
            )
            if support_units is not None:
                detail.supporting_units.set(support_units)
            if air_assets is not None:
                self._apply_air_assets(detail, air_assets, air_crew)
            else:
                self._sync_air_asset_lead(detail)
            if elements is not None:
                self._apply_air_elements(detail, elements)
        self._sync_risk_link(instance)
        return instance

    @staticmethod
    def _sync_risk_link(mission):
        """Keep the mission and its MRM link table in step.

        Selecting an existing MRM on the mission form must create the same
        ``RiskAssessment`` relation that ``start-mrm`` creates, otherwise the
        mission would show "not created" while pointing at a real assessment.
        """
        if not mission.risk_form_id:
            return
        assessment, created = RiskAssessment.objects.get_or_create(
            mission=mission, defaults={"mrm_form_id": mission.risk_form_id}
        )
        if not created and assessment.mrm_form_id != mission.risk_form_id:
            assessment.mrm_form_id = mission.risk_form_id
            assessment.save(update_fields=["mrm_form", "updated_at"])


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


class InventoryCategorySerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = InventoryCategory
        fields = ["id", "name", "code", "kind", "kind_label", "is_sensitive", "parent"]


class InventoryItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_kind = serializers.CharField(source="category.kind", read_only=True)
    category_kind_label = serializers.CharField(
        source="category.get_kind_display", read_only=True
    )
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    custodian_name = serializers.CharField(source="custodian.full_name", read_only=True)
    fuel_grade_label = serializers.CharField(
        source="get_fuel_grade_display", read_only=True
    )
    fuel_form_label = serializers.CharField(
        source="get_fuel_form_display", read_only=True
    )
    #: Nothing is inferred from a name: a row that is not fuel reports null, so
    #: the register never shows a litre figure for materiel.
    litres_available = serializers.SerializerMethodField()
    litres_total = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "category",
            "category_name",
            "category_kind",
            "category_kind_label",
            "name",
            "description",
            "asset_tag",
            "serial_number",
            "unit",
            "unit_name",
            "unit_acronym",
            "custodian",
            "custodian_name",
            "storage_location",
            "quantity_total",
            "quantity_available",
            "unit_of_measure",
            "fuel_grade",
            "fuel_grade_label",
            "fuel_form",
            "fuel_form_label",
            "drum_capacity_litres",
            "date_issued",
            "testing_date",
            "expiry_date",
            "da_number",
            "litres_available",
            "litres_total",
            "serviceability",
            "is_sensitive",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "quantity_available", "created_at", "updated_at"]

    def create(self, validated_data):
        """A bulk aviation tank is one record per airport, fed by deliveries.

        Adding POL-01 bulk stock at a location that already holds the same
        grade tops up that tank instead of opening a second row, and writes
        the delivery to the ledger under its DA number. Aircraft refuelling
        then issues from the same balance, so the tank's history is one trail.
        """
        from django.db import transaction

        from apps.ops.models import InventoryTransaction

        category = validated_data.get("category")
        grade = validated_data.get("fuel_grade", "")
        location = (validated_data.get("storage_location") or "").strip()
        aviation_bulk = (
            category is not None
            and category.kind == "fuel"
            and category.code == "POL-01"
            and validated_data.get("fuel_form") == "bulk"
            and bool(location)
        )
        da_number = (validated_data.get("da_number") or "").strip()
        request = self.context.get("request")
        actor = getattr(request, "user", None)

        if aviation_bulk:
            with transaction.atomic():
                existing = (
                    InventoryItem.objects.select_for_update()
                    .filter(
                        category=category,
                        fuel_grade=grade,
                        fuel_form="bulk",
                        storage_location=location,
                        unit=validated_data.get("unit"),
                    )
                    .order_by("id")
                    .first()
                )
                if existing is not None:
                    quantity = int(validated_data.get("quantity_total") or 0)
                    if quantity > 0:
                        existing.quantity_total += quantity
                        existing.quantity_available += quantity
                        if da_number:
                            existing.da_number = da_number
                        existing.save(
                            update_fields=[
                                "quantity_total",
                                "quantity_available",
                                "da_number",
                                "updated_at",
                            ]
                        )
                        InventoryTransaction.objects.create(
                            item=existing,
                            kind="receipt",
                            quantity=quantity,
                            actor=actor,
                            to_unit=existing.unit,
                            reference=da_number,
                            notes=validated_data.get("notes", ""),
                        )
                    return existing

        instance = super().create(validated_data)
        # The tank's opening balance is a delivery too, so its DA number is the
        # first entry in the same trail the later top-ups join.
        if aviation_bulk and instance.quantity_total and da_number:
            InventoryTransaction.objects.create(
                item=instance,
                kind="receipt",
                quantity=instance.quantity_total,
                actor=actor,
                to_unit=instance.unit,
                reference=da_number,
                notes="Opening bulk stock",
            )
        return instance

    def get_litres_available(self, obj):
        if not obj.is_fuel:
            return None
        return obj.litres(obj.quantity_available)

    def get_litres_total(self, obj):
        if not obj.is_fuel:
            return None
        return obj.litres(obj.quantity_total)

    def validate(self, attrs):
        total = attrs.get("quantity_total", getattr(self.instance, "quantity_total", 0))
        if total is not None and total < 0:
            raise serializers.ValidationError(
                {"quantity_total": "Total quantity cannot be negative."}
            )
        category = attrs.get("category", getattr(self.instance, "category", None))
        if category is not None and category.is_sensitive:
            attrs["is_sensitive"] = True
        if self.instance is None:
            attrs.setdefault("quantity_available", total or 0)

        # Fuel is either sealed drums (counted as drums, each with a stated
        # capacity) or a bulk tank (already litres). A half-configured fuel row
        # would quietly report zero litres, so it is refused instead.
        grade = attrs.get("fuel_grade", getattr(self.instance, "fuel_grade", ""))
        form = attrs.get("fuel_form", getattr(self.instance, "fuel_form", ""))
        capacity = attrs.get(
            "drum_capacity_litres",
            getattr(self.instance, "drum_capacity_litres", None),
        )
        # The rules live in apps.ops.fuel, so the spreadsheet import applies the
        # identical checks to the identical fields.
        from apps.ops.fuel import validate_fuel, validate_pol_dates

        # The category decides which grades are valid (POL-01 is AVGas or Jet
        # A1, POL-02 is diesel or gasoline) and the other POL categories carry
        # no grade at all.
        fuel_errors = validate_fuel(grade, form, capacity, category)
        if fuel_errors:
            raise serializers.ValidationError(fuel_errors)

        # The issue, testing and expiry dates date a POL batch; they cannot be
        # filed on a materiel row, and an expiry before the issue is refused.
        dates = {
            "date_issued": attrs.get(
                "date_issued", getattr(self.instance, "date_issued", None)
            ),
            "testing_date": attrs.get(
                "testing_date", getattr(self.instance, "testing_date", None)
            ),
            "expiry_date": attrs.get(
                "expiry_date", getattr(self.instance, "expiry_date", None)
            ),
        }
        date_errors = validate_pol_dates(dates, category)
        if date_errors:
            raise serializers.ValidationError(date_errors)

        # A delivery advice belongs to a bulk aviation delivery; a drum or a
        # lubricant has no DA number to quote.
        da_number = (
            attrs.get("da_number", getattr(self.instance, "da_number", "")) or ""
        ).strip()
        aviation_bulk = (
            category is not None
            and category.kind == "fuel"
            and category.code == "POL-01"
            and form == "bulk"
        )
        if da_number and not aviation_bulk:
            raise serializers.ValidationError(
                {"da_number": "A DA number applies to POL-01 bulk stock only."}
            )
        if form != "drum":
            attrs["drum_capacity_litres"] = None

        # An accountable item belongs to a unit the caller actually holds: the
        # Logistics Officer records materiel for their own unit and its
        # subordinates, never for a neighbouring unit. The Administrator's
        # scope is every active unit, so the same rule leaves them unrestricted.
        # Import applies the identical rule through ``accessible_unit_ids``.
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            from apps.core.scoping import accessible_unit_ids

            allowed = accessible_unit_ids(user)
            if allowed:
                unit = attrs.get("unit", getattr(self.instance, "unit", None))
                if unit is None:
                    own_unit = getattr(user, "unit", None)
                    if own_unit is not None:
                        attrs["unit"] = own_unit
                elif unit.id not in allowed:
                    raise serializers.ValidationError(
                        {
                            "unit": (
                                "Choose your own unit or one of its subordinate "
                                "units. Another unit's stores are recorded by its "
                                "own Logistics Officer."
                            )
                        }
                    )

        # Drums are held at the unit's own POL dump, so the location follows
        # from the unit rather than being typed in. An explicit location is
        # still respected (a detachment may store its drums elsewhere).
        if attrs.get("fuel_form", getattr(self.instance, "fuel_form", "")) == "drum":
            location = attrs.get(
                "storage_location", getattr(self.instance, "storage_location", "")
            )
            if not str(location or "").strip():
                unit = attrs.get("unit", getattr(self.instance, "unit", None))
                label = (unit.acronym or unit.name) if unit is not None else ""
                if label:
                    attrs["storage_location"] = f"{label} POL Dump"
        return attrs


_MGRS_IN_TEXT = re.compile(
    r"\b\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z][A-HJ-NP-V]\d{2,10}\b",
    re.IGNORECASE,
)


def _mgrs_from_location(value: str) -> str:
    """The last valid MGRS grid embedded in a recorded location string."""
    for candidate in reversed(_MGRS_IN_TEXT.findall(value or "")):
        try:
            return validate_mgrs(candidate)
        except MGRSError:
            continue
    return ""


class AirAssetSerializer(serializers.ModelSerializer):
    """An air-asset register row, with the labels the UI displays."""

    aircraft_type_label = serializers.CharField(
        source="get_aircraft_type_display", read_only=True
    )
    assigned_unit_label = serializers.CharField(
        source="get_assigned_unit_display", read_only=True
    )
    operational_status_label = serializers.CharField(
        source="get_operational_status_display", read_only=True
    )
    firepower_capability_labels = serializers.SerializerMethodField()
    munitions_category_labels = serializers.SerializerMethodField()
    category_labels = serializers.SerializerMethodField()
    #: The MGRS grid embedded in ``current_location``, resolved for the map.
    map_location = serializers.SerializerMethodField()
    opcon_unit_name = serializers.CharField(source="opcon_unit.name", read_only=True)
    opcon_unit_acronym = serializers.CharField(
        source="opcon_unit.acronym", read_only=True
    )

    class Meta:
        model = AirAsset
        fields = [
            "id",
            "aircraft_type",
            "aircraft_type_label",
            "categories",
            "category_labels",
            "map_location",
            "model",
            "tail_number",
            "callsign",
            "cruising_speed_knots",
            "current_location",
            "assigned_unit",
            "assigned_unit_label",
            "opcon_unit",
            "opcon_unit_name",
            "opcon_unit_acronym",
            "operational_status",
            "operational_status_label",
            "firepower_capabilities",
            "firepower_capability_labels",
            "munitions_categories",
            "munitions_category_labels",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        # The custom validate() below reports the composite identity clash on
        # tail_number; the autogenerated validator reports it as a non-field
        # error, which the form cannot attach to the field.
        validators = []

    def get_category_labels(self, obj):
        labels = dict(AirAsset.CATEGORY_CHOICES)
        return [labels.get(value, value) for value in (obj.categories or [])]

    def get_map_location(self, obj):
        mgrs = _mgrs_from_location(obj.current_location)
        if not mgrs:
            return None
        try:
            latitude, longitude = from_mgrs(mgrs)
        except MGRSError:
            return None
        return {
            "mgrs": mgrs,
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
        }

    def get_firepower_capability_labels(self, obj):
        labels = dict(AirAsset.FIREPOWER_CAPABILITY_CHOICES)
        return [
            labels.get(value, value) for value in (obj.firepower_capabilities or [])
        ]

    def get_munitions_category_labels(self, obj):
        labels = dict(AirAsset.MUNITIONS_CATEGORY_CHOICES)
        return [
            labels.get(value, value) for value in (obj.munitions_categories or [])
        ]

    @staticmethod
    def _clean_choice_list(value, choices, select_message, unknown_message):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(select_message)
        valid = {key for key, _label in choices}
        cleaned = []
        for item in value:
            key = str(item)
            if key not in valid:
                raise serializers.ValidationError(unknown_message.format(key))
            if key not in cleaned:
                cleaned.append(key)
        return cleaned

    def validate_categories(self, value):
        return self._clean_choice_list(
            value,
            AirAsset.CATEGORY_CHOICES,
            "Select aircraft categories from the list.",
            "Unknown aircraft category: {}.",
        )

    def validate_firepower_capabilities(self, value):
        return self._clean_choice_list(
            value,
            AirAsset.FIREPOWER_CAPABILITY_CHOICES,
            "Select firepower capabilities from the list.",
            "Unknown firepower capability: {}.",
        )

    def validate_munitions_categories(self, value):
        return self._clean_choice_list(
            value,
            AirAsset.MUNITIONS_CATEGORY_CHOICES,
            "Select air munitions categories from the list.",
            "Unknown air munitions category: {}.",
        )

    def validate_current_location(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        aircraft_type = attrs.get(
            "aircraft_type", getattr(self.instance, "aircraft_type", "")
        )
        model = attrs.get("model", getattr(self.instance, "model", ""))
        tail_number = attrs.get(
            "tail_number", getattr(self.instance, "tail_number", "")
        )
        if aircraft_type and model and tail_number:
            clash = AirAsset.objects.filter(
                aircraft_type=aircraft_type,
                model=model,
                tail_number=tail_number,
            )
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {
                        "tail_number": (
                            "An air asset with this aircraft type, model, and tail "
                            "number already exists."
                        )
                    }
                )

        categories = attrs.get(
            "categories", getattr(self.instance, "categories", [])
        )
        if not categories:
            raise serializers.ValidationError(
                {"categories": "Choose at least one aircraft category."}
            )
        return attrs


class AirfieldSerializer(serializers.ModelSerializer):
    """One airport or air strip, as the operational map reads it."""

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    location = serializers.SerializerMethodField()
    grid_coordinate = serializers.CharField(source="mgrs", read_only=True)

    class Meta:
        model = Airfield
        fields = [
            "id",
            "code",
            "name",
            "kind",
            "kind_label",
            "municipality",
            "province",
            "location",
            "latitude",
            "longitude",
            "grid_coordinate",
            "runway_length_m",
            "runway_width_m",
            "runway_orientation",
            "elevation_m",
            "surface",
            "operator",
            "notes",
            "is_active",
        ]

    def get_location(self, obj):
        return ", ".join(part for part in (obj.municipality, obj.province) if part)


class InventoryTransactionSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)
    mission_number = serializers.CharField(source="mission.mission_number", read_only=True)

    class Meta:
        model = InventoryTransaction
        fields = [
            "id",
            "item",
            "item_name",
            "kind",
            "quantity",
            "actor",
            "actor_name",
            "from_unit",
            "to_unit",
            "mission",
            "mission_number",
            "reference",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class StockMovementSerializer(serializers.Serializer):
    """Write payload for a stock movement (applied by the service layer)."""

    item = serializers.PrimaryKeyRelatedField(queryset=InventoryItem.objects.all())
    kind = serializers.ChoiceField(choices=[c[0] for c in InventoryTransaction.KIND_CHOICES])
    quantity = serializers.IntegerField(min_value=0)
    to_unit = serializers.PrimaryKeyRelatedField(queryset=Unit.objects.all(), required=False, allow_null=True)
    mission = serializers.PrimaryKeyRelatedField(queryset=Mission.objects.all(), required=False, allow_null=True)
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["kind"] == "transfer" and not attrs.get("to_unit"):
            raise serializers.ValidationError(
                {"to_unit": "A transfer requires a destination unit."}
            )
        return attrs


# ---------------------------------------------------------------------------
# GIS
# ---------------------------------------------------------------------------


class AdministrativeAreaSerializer(serializers.ModelSerializer):
    """Search/metadata payload; geometry is only sent when asked for."""

    level_label = serializers.CharField(source="get_level_display", read_only=True)
    bbox = serializers.SerializerMethodField()

    class Meta:
        model = AdministrativeArea
        fields = [
            "psgc",
            "name",
            "level",
            "level_label",
            "parent_psgc",
            "parent_name",
            "region_name",
            "area_km2",
            "centroid_lon",
            "centroid_lat",
            "bbox",
        ]

    def get_bbox(self, obj):
        if None in (obj.min_lon, obj.min_lat, obj.max_lon, obj.max_lat):
            return None
        return [
            [obj.min_lat, obj.min_lon],
            [obj.max_lat, obj.max_lon],
        ]


class AdministrativeAreaGeometrySerializer(AdministrativeAreaSerializer):
    class Meta(AdministrativeAreaSerializer.Meta):
        fields = [*AdministrativeAreaSerializer.Meta.fields, "geometry"]


class AORAssignSerializer(serializers.Serializer):
    """One Administrator save: areas to add and/or areas to remove."""

    unit = serializers.PrimaryKeyRelatedField(queryset=Unit.objects.all())
    psgcs = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, default=list
    )
    remove_psgcs = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, default=list
    )
    confirm_transfer = serializers.BooleanField(required=False, default=False)
    color = serializers.CharField(required=False, allow_blank=True, max_length=20)

    def validate(self, attrs):
        if not attrs.get("psgcs") and not attrs.get("remove_psgcs"):
            raise serializers.ValidationError(
                {"psgcs": "Select at least one area to add or remove."}
            )
        attrs["psgcs"] = list(dict.fromkeys(attrs.get("psgcs") or []))
        attrs["remove_psgcs"] = list(dict.fromkeys(attrs.get("remove_psgcs") or []))
        return attrs


class AreaOfResponsibilitySerializer(serializers.ModelSerializer):
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    #: The mother unit this area rolls up to (coverage is inherited upwards).
    unit_parent_acronym = serializers.CharField(source="unit.parent.acronym", read_only=True)
    unit_parent_name = serializers.CharField(source="unit.parent.name", read_only=True)
    admin_psgc = serializers.IntegerField(source="admin_area_id", read_only=True)
    admin_area_name = serializers.CharField(source="admin_area.name", read_only=True)
    admin_area_level = serializers.CharField(source="admin_area.level", read_only=True)
    geometry = serializers.JSONField()

    class Meta:
        model = AreaOfResponsibility
        fields = [
            "id",
            "name",
            "code",
            "unit",
            "unit_name",
            "unit_acronym",
            "unit_parent_acronym",
            "unit_parent_name",
            "admin_psgc",
            "admin_area_name",
            "admin_area_level",
            "geometry",
            "color",
            "description",
            "min_lon",
            "min_lat",
            "max_lon",
            "max_lat",
            "centroid_lon",
            "centroid_lat",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "admin_psgc",
            "admin_area_name",
            "admin_area_level",
            "min_lon",
            "min_lat",
            "max_lon",
            "max_lat",
            "centroid_lon",
            "centroid_lat",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def validate_geometry(self, value):
        try:
            return geo.validate_geometry(value)
        except geo.GeometryError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def _apply_derived(self, instance):
        geometry = instance.geometry
        box = geo.bounding_box(geometry)
        centre = geo.centroid(geometry)
        instance.min_lon = box["min_lon"]
        instance.min_lat = box["min_lat"]
        instance.max_lon = box["max_lon"]
        instance.max_lat = box["max_lat"]
        instance.centroid_lon = centre[0]
        instance.centroid_lat = centre[1]
        instance.save(
            update_fields=[
                "min_lon",
                "min_lat",
                "max_lon",
                "max_lat",
                "centroid_lon",
                "centroid_lat",
            ]
        )

    def validate(self, attrs):
        """Refuse a manual polygon that would overlap another unit's AOR.

        Administrative-area assignments go through ``/aor/assign/`` (which owns
        the transfer rules); this keeps the older draw-on-the-map path from
        quietly creating overlapping coverage.
        """
        from apps.ops import aor as aor_service

        # Only the two fields that define coverage can create a conflict. An
        # edit that just renames an area or changes its colour must not be
        # refused because of an overlap that already existed.
        if self.instance is not None and "geometry" not in attrs and "unit" not in attrs:
            return attrs

        geometry = attrs.get("geometry", getattr(self.instance, "geometry", None))
        unit = attrs.get("unit", getattr(self.instance, "unit", None))
        if geometry is None or unit is None:
            return attrs
        conflicts = aor_service.find_conflicts(
            unit_id=unit.id,
            geometry=geometry,
            name=attrs.get("name", getattr(self.instance, "name", "New AOR")),
            exclude_ids=[self.instance.id] if self.instance is not None else None,
        )
        if conflicts:
            raise serializers.ValidationError(
                {
                    "geometry": (
                        "This area overlaps "
                        + ", ".join(
                            sorted(
                                {
                                    f"{row['aor_name']} ({row['unit_acronym'] or row['unit_name']})"
                                    for row in conflicts
                                }
                            )
                        )
                        + ". Different units cannot share territory."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        # A drawn boundary without an explicit colour takes the owning unit's
        # colour, so it matches that unit's administrative-area coverage.
        if not validated_data.get("color"):
            from apps.ops import aor as aor_service

            validated_data["color"] = aor_service.unit_color(validated_data.get("unit"))
        instance = super().create(validated_data)
        self._apply_derived(instance)
        return instance

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        self._apply_derived(instance)
        return instance


def _feature_centre(geometry: dict | None) -> tuple[float | None, float | None]:
    """(longitude, latitude) of a point, or the centroid of a polygon."""
    if not geometry:
        return None, None
    if geometry.get("type") == "Point":
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) >= 2:
            return float(coordinates[0]), float(coordinates[1])
        return None, None
    try:
        longitude, latitude = geo.centroid(geometry)
        return longitude, latitude
    except geo.GeometryError:
        return None, None


class OperationalMapFeatureSerializer(serializers.ModelSerializer):
    """A saved operational-map feature: troops, places and areas."""

    feature_type_label = serializers.CharField(
        source="get_feature_type_display", read_only=True
    )
    service_category_label = serializers.CharField(
        source="get_service_category_display", read_only=True
    )
    unit_category_label = serializers.CharField(
        source="get_unit_category_display", read_only=True
    )
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True)
    geometry = serializers.JSONField()
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()

    TYPE_COLORS = {
        "friendly_troop": "#3fb950",
        "frequent_location": "#f0b429",
        "operational_area": "#4f9cf9",
    }

    class Meta:
        model = OperationalMapFeature
        fields = [
            "id",
            "name",
            "feature_type",
            "feature_type_label",
            "service_category",
            "service_category_label",
            "unit_category",
            "unit_category_label",
            "acronym",
            "address",
            "mgrs",
            "contact_number",
            "description",
            "geometry",
            "color",
            "unit",
            "unit_name",
            "unit_acronym",
            "latitude",
            "longitude",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "feature_type_label",
            "service_category_label",
            "unit_category_label",
            "mgrs",
            "unit_name",
            "unit_acronym",
            "latitude",
            "longitude",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]

    def get_latitude(self, obj):
        return _feature_centre(obj.geometry)[1]

    def get_longitude(self, obj):
        return _feature_centre(obj.geometry)[0]

    def _sync_mgrs(self, instance):
        """Keep a plotted point's grid reference in step with its coordinates."""
        if instance.feature_type not in OperationalMapFeature.POINT_TYPES:
            return instance
        longitude, latitude = _feature_centre(instance.geometry)
        if latitude is None or longitude is None:
            return instance
        try:
            grid = to_mgrs(float(latitude), float(longitude), 5)
        except MGRSError:
            return instance
        if grid and grid != instance.mgrs:
            OperationalMapFeature.objects.filter(pk=instance.pk).update(mgrs=grid)
            instance.mgrs = grid
        return instance

    def validate(self, attrs):
        feature_type = attrs.get("feature_type", getattr(self.instance, "feature_type", None))
        geometry = attrs.get("geometry", getattr(self.instance, "geometry", None))
        if not geometry:
            raise serializers.ValidationError({"geometry": "Plot the feature on the map first."})

        if feature_type in OperationalMapFeature.POINT_TYPES:
            if not isinstance(geometry, dict) or geometry.get("type") != "Point":
                raise serializers.ValidationError(
                    {"geometry": "Troops and locations are plotted as a single point."}
                )
            coordinates = geometry.get("coordinates") or []
            try:
                longitude = float(coordinates[0])
                latitude = float(coordinates[1])
            except (TypeError, ValueError, IndexError):
                raise serializers.ValidationError(
                    {"geometry": "The point needs a longitude and a latitude."}
                ) from None
            if not -180.0 <= longitude <= 180.0:
                raise serializers.ValidationError(
                    {"geometry": f"Longitude {longitude} is outside -180..180."}
                )
            if not -90.0 <= latitude <= 90.0:
                raise serializers.ValidationError(
                    {"geometry": f"Latitude {latitude} is outside -90..90."}
                )
            attrs["geometry"] = {"type": "Point", "coordinates": [longitude, latitude]}
        else:
            try:
                attrs["geometry"] = geo.validate_geometry(geometry)
            except geo.GeometryError as exc:
                raise serializers.ValidationError({"geometry": str(exc)}) from exc
        return attrs

    def create(self, validated_data):
        if not validated_data.get("color"):
            validated_data["color"] = self.TYPE_COLORS.get(
                validated_data.get("feature_type"), "#4f9cf9"
            )
        return self._sync_mgrs(super().create(validated_data))

    def update(self, instance, validated_data):
        return self._sync_mgrs(super().update(instance, validated_data))


class PointQuerySerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)


class AirEtaLegSerializer(serializers.Serializer):
    """One air leg whose endpoints may carry an MGRS grid."""

    origin = serializers.CharField(allow_blank=True, required=False)
    destination = serializers.CharField(allow_blank=True, required=False)


class AirEtaEstimateSerializer(serializers.Serializer):
    """Input for the air-form's distance and ETE estimate."""

    cruising_speed_knots = serializers.FloatField(min_value=1, max_value=2000)
    legs = AirEtaLegSerializer(many=True, allow_empty=False, max_length=100)


class MGRSConvertSerializer(serializers.Serializer):
    """Either supply lat/lon (-> MGRS) or mgrs (-> lat/lon)."""

    latitude = serializers.FloatField(required=False, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, min_value=-180, max_value=180)
    mgrs = serializers.CharField(required=False, allow_blank=True)
    precision = serializers.IntegerField(required=False, min_value=1, max_value=5, default=5)

    def validate(self, attrs):
        if attrs.get("mgrs"):
            try:
                lat, lon = from_mgrs(attrs["mgrs"])
            except MGRSError as exc:
                raise serializers.ValidationError({"mgrs": str(exc)}) from exc
            attrs["resolved_latitude"] = lat
            attrs["resolved_longitude"] = lon
            attrs["resolved_mgrs"] = validate_mgrs(attrs["mgrs"])
        elif attrs.get("latitude") is not None and attrs.get("longitude") is not None:
            mgrs_value = to_mgrs(attrs["latitude"], attrs["longitude"], attrs.get("precision", 5))
            attrs["resolved_mgrs"] = mgrs_value
            attrs["resolved_latitude"] = attrs["latitude"]
            attrs["resolved_longitude"] = attrs["longitude"]
        else:
            raise serializers.ValidationError(
                "Supply either MGRS, or both latitude and longitude."
            )
        return attrs


# ---------------------------------------------------------------------------
# Risk / notifications / audit
# ---------------------------------------------------------------------------


class RiskAssessmentSerializer(serializers.ModelSerializer):
    evaluator_name = serializers.CharField(source="evaluator.full_name", read_only=True)
    mrm_number = serializers.CharField(source="mrm_form.mrm_number", read_only=True)
    mission_number = serializers.CharField(source="mission.mission_number", read_only=True)

    class Meta:
        model = RiskAssessment
        fields = [
            "id",
            "mission",
            "mission_number",
            "mrm_form",
            "mrm_number",
            "evaluator",
            "evaluator_name",
            "status",
            "max_risk",
            "residual_risk",
            "total_percent",
            "risk_category",
            "assessed_at",
            "approved_by",
            "approved_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "evaluator",
            "status",
            "max_risk",
            "residual_risk",
            "total_percent",
            "risk_category",
            "assessed_at",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
        ]


class NotificationSerializer(serializers.ModelSerializer):
    sender = serializers.CharField(source="created_by.username", read_only=True, default="")
    sender_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    is_pending_action = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "level",
            "category",
            "is_read",
            "read_at",
            "related_type",
            "related_id",
            "created_at",
            # enhancement fields
            "priority",
            "requires_action",
            "action_status",
            "action_label",
            "action_url",
            "due_at",
            "acknowledged_at",
            "completed_at",
            "completion_note",
            "sender",
            "sender_name",
            "is_pending_action",
        ]
        read_only_fields = fields


class InventoryRequestSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    asset_tag = serializers.CharField(source="item.asset_tag", read_only=True, default="")
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True, default="")
    mission_number = serializers.CharField(
        source="mission.mission_number", read_only=True, default=""
    )
    requested_by_username = serializers.CharField(
        source="requested_by.username", read_only=True, default=""
    )
    decided_by_username = serializers.CharField(
        source="decided_by.username", read_only=True, default=""
    )

    class Meta:
        model = InventoryRequest
        fields = [
            "id",
            "item",
            "item_name",
            "asset_tag",
            "kind",
            "quantity",
            "unit",
            "unit_acronym",
            "mission",
            "mission_number",
            "justification",
            "requested_by",
            "requested_by_username",
            "status",
            "decided_by",
            "decided_by_username",
            "decided_at",
            "decision_note",
            "committed_transaction",
            "created_at",
        ]
        read_only_fields = fields


class InventoryRequestWriteSerializer(serializers.Serializer):
    item_id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=[choice[0] for choice in InventoryRequest.KIND_CHOICES])
    quantity = serializers.IntegerField(min_value=1)
    mission_id = serializers.IntegerField(required=False, allow_null=True)
    justification = serializers.CharField(required=False, allow_blank=True, default="")


class InventoryDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected", "returned"])
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "actor_name",
            "action",
            "entity_type",
            "entity_id",
            "before",
            "after",
            "note",
            "ip_address",
            "correlation_id",
            "created_at",
        ]
        read_only_fields = fields
