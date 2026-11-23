"""Editing an MRM assessment: hazard library, selected hazards, tagged personnel.

Ported from the Flask application (``routes/mrm.py``) so a TOWEMS assessment is
the same record the legacy screens read and the legacy PDF prints:

* the selected hazards live in ``mission_hazards``, one row per hazard, with the
  after-mitigation override and the additional mitigations the planner adds;
* the I'M SAFE answers live in ``mrm_forms.imsafes`` as JSON;
* the environment lives in ``mrm_forms.environment`` as the single sentence
  "Weather. Lighting. Workspace. Tempo." the legacy app generated;
* the tagged personnel live in ``mrm_personnel``.

Risk arithmetic is *not* re-implemented here - ``apps.ops.risk`` already carries
the exact legacy algorithm, including its two different threshold ladders.
"""

from __future__ import annotations

import json

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.core.permissions import ADMIN, UNIT_SAFETY, authority
from apps.legacy.models import (
    HazardRegistry,
    MRMPersonnel,
    MissionHazards,
)
from apps.ops import risk as risk_engine

#: Option lists are copied verbatim from the Flask ``Config``: existing
#: assessments were built from these strings, so changing them would make old
#: records unreadable.
WEATHER_OPTIONS = [
    "Clear skies",
    "Overcast / Cloudy",
    "Rainy / Wet",
    "Windy",
    "Hot / Humid",
    "Cold / Foggy",
]

LIGHTING_OPTIONS = [
    "Daylight",
    "Dusk / Dawn",
    "Nighttime (Fair)",
    "Nighttime (Poor visibility)",
    "Adequate artificial lighting",
    "Poor artificial lighting",
]

WORKSPACE_OPTIONS = [
    "Clean and organized",
    "Congested / Crowded",
    "Slippery / Uneven surface",
    "Hazardous materials present",
    "Adequate ventilation",
    "Poor ventilation",
    "None",
]

TEMPO_OPTIONS = [
    "Normal / Routine operations",
    "High tempo / Increased workload",
    "Emergency / Stressful environment",
]

#: The six I'M SAFE checks, in the legacy order and wording.
IMSAFE_CHECKLIST = [
    {"key": "I", "title": "Illness", "prompt": "Am I free from any Illness?"},
    {"key": "M", "title": "Medication", "prompt": "Am I free from any impairing medication?"},
    {"key": "S", "title": "Stress", "prompt": "Am I free from stress?"},
    {"key": "A", "title": "Alcohol", "prompt": "Am I free from alcohol?"},
    {"key": "F", "title": "Fatigue", "prompt": "Am I well rested and alert?"},
    {"key": "E", "title": "Eating", "prompt": "Am I adequately nourished?"},
]
IMSAFE_KEYS = [item["key"] for item in IMSAFE_CHECKLIST]

#: An assessment identifies at least this many hazards before it can be saved:
#: a shorter list leaves the risks the crew is accepting unstated.
MIN_HAZARDS = 8

ENVIRONMENT_KEYS = ("weather", "lighting", "workspace", "operational_tempo")


class MrmEditError(Exception):
    """A refusal the caller should see, written for humans."""


# ---------------------------------------------------------------------------
# Environment sentence
# ---------------------------------------------------------------------------


def environment_sentence(data: dict) -> str:
    """"Weather. Lighting. Workspace. Tempo." - exactly as the legacy app wrote it."""
    parts = []
    for key in ENVIRONMENT_KEYS:
        value = str(data.get(key) or "").strip()
        if value:
            parts.append(value.capitalize())
    return ". ".join(parts) + "." if parts else "Standard operational environment."


def parse_environment(text: str) -> dict:
    """Split a stored environment sentence back into its four choices."""
    result = {key: "" for key in ENVIRONMENT_KEYS}
    if not text:
        return result
    parts = [part.strip().lower() for part in str(text).split(".") if part.strip()]
    for key, options in (
        ("weather", WEATHER_OPTIONS),
        ("lighting", LIGHTING_OPTIONS),
        ("workspace", WORKSPACE_OPTIONS),
        ("operational_tempo", TEMPO_OPTIONS),
    ):
        for part in parts:
            match = next((option for option in options if option.lower() == part), None)
            if match is None:
                match = next((option for option in options if option.lower() in part), None)
            if match:
                result[key] = match
                break
    return result


# ---------------------------------------------------------------------------
# Hazard library
# ---------------------------------------------------------------------------


def hazard_payload(hazard: HazardRegistry) -> dict:
    rating = hazard.after_risk_rating or risk_engine.rating_string(
        hazard.after_likelihood, hazard.after_severity
    )
    return {
        "hazard_id": hazard.id,
        "activity_code": hazard.activity_code,
        "description": hazard.hazard_description,
        "before_likelihood": hazard.before_likelihood,
        "before_severity": hazard.before_severity,
        "before_rating": hazard.before_risk_rating,
        "mitigations": hazard.mitigations,
        "after_likelihood": hazard.after_likelihood,
        "after_severity": hazard.after_severity,
        "after_rating": rating,
        "rating_color": risk_engine.rating_color(rating),
    }


def hazard_library(user, activity_code: str = "", description: str = "", limit: int = 200):
    """The unit's own hazard library, filtered as the legacy search did.

    Hazards belong to a unit; a planner sees the library of their own unit only,
    which is what the Flask ``/mrm/search_hazards`` endpoint filtered on.
    """
    unit_id = getattr(user, "unit_id", None)
    if not unit_id:
        return []
    queryset = HazardRegistry.objects.filter(unit_id=unit_id)
    if activity_code:
        queryset = queryset.filter(activity_code__icontains=activity_code)
    if description:
        queryset = queryset.filter(hazard_description__icontains=description)
    queryset = queryset.order_by("activity_code", "hazard_description")[: max(1, min(limit, 500))]
    return [hazard_payload(hazard) for hazard in queryset]


def hazard_activity_codes(user) -> list:
    """The activity codes this unit's hazard registry actually contains.

    The planner picks from these codes, so the list is the unit's own library -
    never a generic catalogue of codes nobody can add a hazard to. The codes
    carrying the most hazards come first, so the activity the unit assesses
    most often is the first choice; ties fall back to the code itself.
    """
    unit_id = getattr(user, "unit_id", None)
    if not unit_id:
        return []
    rows = (
        HazardRegistry.objects.filter(unit_id=unit_id)
        .exclude(activity_code__isnull=True)
        .exclude(activity_code="")
        .values("activity_code")
        .annotate(hazard_count=Count("id"))
        .order_by("-hazard_count", "activity_code")
    )
    return [
        {"activity_code": row["activity_code"], "hazard_count": row["hazard_count"]}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Reading an assessment
# ---------------------------------------------------------------------------


def _linked_mission(form):
    link = form.toms_risk_links.select_related("mission").first()
    return link.mission if link else None


def vehicle_label(vehicle) -> str:
    """A vehicle as a planner reads it in a message: model, then registration.

    ``KM450 Nr 12345567`` - the model identifies the type of vehicle, the
    registration identifies the actual one, and both are needed to dispatch it.
    """
    model = (getattr(vehicle, "make_model", "") or "").strip() or (
        getattr(vehicle, "vehicle_type", "") or ""
    ).strip()
    registration = (getattr(vehicle, "registration_number", "") or "").strip()
    if not registration:
        return model
    return f"{model} Nr {registration}".strip() if model else f"Nr {registration}"


def mission_context(form) -> dict:
    """The mission behind an assessment: its vehicles, its itinerary, its hours.

    This is what the assessment copies into the Aircraft/Vehicle field and the
    mission statement, and what the weather forecast is asked about - the same
    itinerary the planner entered, not a second description of the trip.
    """
    mission = _linked_mission(form)
    context = {
        "mission_id": None,
        "mission_number": "",
        "title": "",
        "aircraft_vehicle": "",
        "itinerary_text": "",
        "routes": [],
        "personnel": [],
        "forecast_points": [],
        "window": {"start": None, "end": None},
    }
    if mission is None:
        return context

    context.update(
        {
            "mission_id": mission.id,
            "mission_number": mission.mission_number,
            "title": mission.title,
        }
    )

    # -- what is going: aircraft from the air detail, vehicles from the trips --
    labels: list[str] = []
    try:
        air = mission.air_detail
    except ObjectDoesNotExist:
        air = None
    if air is not None:
        aircraft_type = (air.aircraft_type or "").strip()
        tail_number = (air.aircraft_tail_number or "").strip()
        if aircraft_type and tail_number:
            labels.append(f"{aircraft_type} Nr {tail_number}")
        elif aircraft_type or tail_number:
            labels.append(aircraft_type or f"Nr {tail_number}")

    routes = []
    points = []
    if mission.latitude is not None and mission.longitude is not None:
        points.append(
            {
                "name": mission.location_name or "Mission location",
                "latitude": mission.latitude,
                "longitude": mission.longitude,
            }
        )

    trips = (
        mission.trips.exclude(status="cancelled")
        .prefetch_related("itinerary", "vehicles__vehicle")
        .order_by("departure_at", "id")
    )
    departure_points = []
    return_points = []
    for trip in trips:
        stops = list(trip.itinerary.all())
        trip_vehicles = [
            vehicle_label(link.vehicle)
            for link in trip.vehicles.all()
            if link.vehicle is not None
        ]
        for label in trip_vehicles:
            if label and label not in labels:
                labels.append(label)
        if trip.departure_at:
            departure_points.append(trip.departure_at)
        if trip.return_at:
            return_points.append(trip.return_at)
        for stop in stops:
            if stop.latitude is not None and stop.longitude is not None:
                points.append(
                    {
                        "name": stop.location_name,
                        "latitude": stop.latitude,
                        "longitude": stop.longitude,
                    }
                )
        routes.append(
            {
                "trip_id": trip.id,
                "trip_number": trip.trip_number,
                "title": trip.title,
                "vehicles": trip_vehicles,
                "departure_at": trip.departure_at,
                "return_at": trip.return_at,
                "stops": [
                    {
                        "sequence": stop.sequence,
                        "location_name": stop.location_name,
                        "planned_departure": stop.planned_departure,
                        "planned_arrival": stop.planned_arrival,
                    }
                    for stop in stops
                ],
                "route_text": " → ".join(
                    [stop.location_name for stop in stops if stop.location_name]
                ),
            }
        )

    # -- one itinerary: each trip's own route, kept readable --
    route_texts = [route["route_text"] for route in routes if route["route_text"]]
    if route_texts:
        context["itinerary_text"] = "; ".join(route_texts)
    elif air is not None and (air.departure_location or air.destination_location):
        # A flight's itinerary is its airfields, in order.
        context["itinerary_text"] = " → ".join(
            location
            for location in (air.departure_location, air.destination_location)
            if location
        )
    elif air is not None and air.route:
        context["itinerary_text"] = air.route.strip()
    elif points:
        context["itinerary_text"] = " → ".join(
            dict.fromkeys(point["name"] for point in points)
        )

    context["routes"] = routes
    context["aircraft_vehicle"] = ", ".join(labels)
    # The people the mission actually travels with - the only people the MRM
    # offers for post-mission feedback.
    context["personnel"] = [
        {
            "user_id": row.user_id,
            "full_name": row.user.full_name if row.user else "",
            "serial_number": getattr(row.user, "serial_number", "") if row.user else "",
            "rank": getattr(row.user, "rank", "") or "" if row.user else "",
            "mission_role": row.role or "",
        }
        for row in mission.personnel.filter(is_active=True).select_related("user")
        if row.user is not None
    ]
    context["forecast_points"] = list(
        {
            (point["name"], point["latitude"], point["longitude"]): point
            for point in points
        }.values()
    )

    start = mission.start_at or (min(departure_points) if departure_points else None)
    end = mission.end_at or (max(return_points) if return_points else None)
    context["window"] = {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }
    return context


def current_location(form) -> dict | None:
    """The assessed unit's plotted position, for an MRM with no mission route.

    A manual MRM has no itinerary and no mission window, so the forecast uses
    where the unit currently sits on the operational map. When the unit has no
    marker the caller reports that instead of guessing a location.
    """
    from apps.command.models import UnitMapMarker

    unit = getattr(form, "original_unit", None)
    if unit is None and form.created_by_id:
        unit = getattr(form.created_by, "unit", None)
    if unit is None:
        return None

    marker = UnitMapMarker.objects.filter(unit_id=unit.id).first()
    if marker is None or marker.latitude is None or marker.longitude is None:
        return None

    return {
        "name": marker.place_name or unit.location or unit.name,
        "latitude": marker.latitude,
        "longitude": marker.longitude,
    }


def can_edit(form, user) -> bool:
    """Draft only, and only for the people who own the assessment.

    The creator, the mission's own MRM team, the unit safety officer and an
    Administrator may edit. An evaluator reviews; they do not write.
    """
    if form.status != "draft":
        return False
    if form.created_by_id == user.id:
        return True
    if authority(user) in (ADMIN, UNIT_SAFETY):
        return True
    mission = _linked_mission(form)
    if mission is not None:
        return mission.mrm_team.filter(user=user, is_active=True).exists()
    return False


def selected_hazards(form):
    """Rows of ``mission_hazards`` still marked selected, with their library data."""
    links = (
        MissionHazards.objects.filter(mrm_id=form.id, selected=True)
        .select_related("hazard")
        .order_by("hazard__activity_code", "hazard__hazard_description")
    )
    rows = []
    for link in links:
        hazard = link.hazard
        if hazard is None:
            continue
        payload = hazard_payload(hazard)
        payload.update(
            {
                "override_likelihood": link.override_likelihood,
                "override_severity": link.override_severity,
                "additional_mitigations": link.additional_mitigations or "",
            }
        )
        effective_likelihood = link.override_likelihood or hazard.after_likelihood
        effective_severity = link.override_severity or hazard.after_severity
        effective = risk_engine.rating_string(effective_likelihood, effective_severity)
        payload["effective_rating"] = effective
        payload["effective_color"] = risk_engine.rating_color(effective)
        rows.append(payload)
    return rows


def tagged_personnel(form):
    rows = (
        MRMPersonnel.objects.filter(mrm_id=form.id, is_active=True)
        .select_related("user")
        .order_by("user__last_name", "user__first_name")
    )
    return [
        {
            "user_id": row.user_id,
            "full_name": row.user.full_name if row.user else "",
            "serial_number": getattr(row.user, "serial_number", "") if row.user else "",
            "unit_acronym": getattr(getattr(row.user, "unit", None), "acronym", "")
            if row.user
            else "",
            "role": row.role or "",
        }
        for row in rows
    ]


def imsafe_answers(form) -> dict:
    if not form.imsafes:
        return {key: False for key in IMSAFE_KEYS}
    try:
        stored = json.loads(form.imsafes)
    except (TypeError, ValueError):
        return {key: False for key in IMSAFE_KEYS}
    if not isinstance(stored, dict):
        return {key: False for key in IMSAFE_KEYS}
    return {key: bool(stored.get(key)) for key in IMSAFE_KEYS}


def risk_summary(form) -> dict:
    total = form.total_percent
    return {
        "max_risk": form.max_risk,
        "residual_risk": form.residual_risk,
        "total_percent": total,
        "authority_level": form.authority_level
        or (risk_engine.authority_level(total) if total is not None else ""),
        "risk_category": form.risk_category
        or (risk_engine.risk_category(total) if total is not None else ""),
    }


def payload(form, user) -> dict:
    return {
        "id": form.id,
        "mrm_number": form.mrm_number,
        "status": form.status,
        "review_status": form.review_status,
        "can_edit": can_edit(form, user),
        # The unit whose library and personnel the editor offers.
        "unit_id": form.original_unit_id or getattr(user, "unit_id", None),
        "form": {
            "activity_mission": form.activity_mission or "",
            "safety_objectives": form.safety_objectives or "",
            "mission_statement": form.mission_statement or "",
            "aircraft_vehicle": form.aircraft_vehicle or "",
        },
        "imsafe": imsafe_answers(form),
        # The environmental conditions are written in the planner's own words.
        # Records from the old four-dropdown form still read back as the sentence
        # they were stored as, which is what the legacy screens print.
        "environment": form.environment or "",
        "options": {
            "imsafe": IMSAFE_CHECKLIST,
        },
        "hazards": selected_hazards(form),
        "personnel": tagged_personnel(form),
        "risk": risk_summary(form),
        # The mission this assessment covers; the editor copies its vehicles
        # and itinerary in and asks the forecast about its route and hours.
        "mission": mission_context(form),
    }


# ---------------------------------------------------------------------------
# Saving an assessment
# ---------------------------------------------------------------------------


def _clean_text(value) -> str:
    return str(value if value is not None else "").strip()


def _save_hazards(form, entries, unit_id):
    if not isinstance(entries, list):
        raise MrmEditError("Hazards must be a list.")

    wanted = {}
    for entry in entries:
        try:
            hazard_id = int(entry.get("hazard_id"))
        except (TypeError, ValueError):
            raise MrmEditError("Each hazard needs a numeric hazard_id.") from None
        likelihood = entry.get("override_likelihood")
        severity = entry.get("override_severity")
        if likelihood in ("", None):
            likelihood = None
        else:
            try:
                likelihood = int(likelihood)
            except (TypeError, ValueError):
                raise MrmEditError("After-likelihood must be a number from 1 to 5.") from None
            if not 1 <= likelihood <= 5:
                raise MrmEditError("After-likelihood must be a number from 1 to 5.")
        if severity in ("", None):
            severity = None
        else:
            severity = str(severity).upper()[:1]
            if severity not in risk_engine.SEVERITY_WEIGHTS:
                raise MrmEditError("After-severity must be one of A, B, C, D or E.")
        wanted[hazard_id] = {
            "override_likelihood": likelihood,
            "override_severity": severity,
            "additional_mitigations": _clean_text(entry.get("additional_mitigations")),
        }

    allowed = set(
        HazardRegistry.objects.filter(pk__in=list(wanted) or [0]).values_list("id", flat=True)
    )
    if unit_id:
        allowed &= set(
            HazardRegistry.objects.filter(
                pk__in=list(wanted) or [0], unit_id=unit_id
            ).values_list("id", flat=True)
        )
    unknown = [hazard_id for hazard_id in wanted if hazard_id not in allowed]
    if unknown:
        raise MrmEditError(
            f"Hazards {unknown} are not in this unit's hazard library."
        )

    # Everything not in the payload stops counting, but the row is kept: the
    # selection is history, not a replaceable list.
    MissionHazards.objects.filter(mrm_id=form.id).exclude(
        hazard_id__in=list(wanted) or [0]
    ).update(selected=False)

    for hazard_id, values in wanted.items():
        row = MissionHazards.objects.filter(mrm_id=form.id, hazard_id=hazard_id).first()
        if row is None:
            MissionHazards.objects.create(
                mrm_id=form.id,
                hazard_id=hazard_id,
                selected=True,
                **values,
            )
        else:
            row.selected = True
            row.override_likelihood = values["override_likelihood"]
            row.override_severity = values["override_severity"]
            row.additional_mitigations = values["additional_mitigations"]
            row.save(
                update_fields=[
                    "selected",
                    "override_likelihood",
                    "override_severity",
                    "additional_mitigations",
                ]
            )


def _save_personnel(form, entries, actor, unit_id):
    if not isinstance(entries, list):
        raise MrmEditError("Personnel must be a list.")

    wanted = {}
    for entry in entries:
        try:
            user_id = int(entry.get("user_id"))
        except (TypeError, ValueError):
            raise MrmEditError("Each tagged person needs a numeric user_id.") from None
        wanted[user_id] = _clean_text(entry.get("role"))[:100] or "Crew Member"

    if wanted:
        from apps.accounts.models import User

        people = User.objects.filter(pk__in=list(wanted), is_active=True)
        if unit_id:
            people = people.filter(unit_id=unit_id)
        allowed = set(people.values_list("id", flat=True))
        unknown = [user_id for user_id in wanted if user_id not in allowed]
        if unknown:
            raise MrmEditError(
                f"Personnel {unknown} are not active members of this unit."
            )

    MRMPersonnel.objects.filter(mrm_id=form.id).exclude(
        user_id__in=list(wanted) or [0]
    ).update(is_active=False)

    for user_id, role in wanted.items():
        row = MRMPersonnel.objects.filter(mrm_id=form.id, user_id=user_id).first()
        if row is None:
            MRMPersonnel.objects.create(
                mrm_id=form.id,
                user_id=user_id,
                role=role,
                tagged_by=actor,
                tagged_at=timezone.now(),
                is_active=True,
            )
        else:
            row.role = role
            row.is_active = True
            row.tagged_by = actor
            row.tagged_at = timezone.now()
            row.save(update_fields=["role", "is_active", "tagged_by", "tagged_at"])


def _recalculate(form):
    """Recompute the stored risk from the hazards now selected."""
    hazards = []
    for link in MissionHazards.objects.filter(mrm_id=form.id, selected=True).select_related(
        "hazard"
    ):
        hazard = link.hazard
        if hazard is None:
            continue
        hazards.append(
            risk_engine.HazardInput(
                after_likelihood=hazard.after_likelihood,
                after_severity=hazard.after_severity,
                override_likelihood=link.override_likelihood,
                override_severity=link.override_severity,
            )
        )
    result = risk_engine.calculate(hazards)
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
    return result


def save_details(form, data, actor) -> dict:
    """Apply an assessment edit. Raises :class:`MrmEditError` on a refusal."""
    if not can_edit(form, actor):
        raise MrmEditError(
            "Only the assigned MRM team may change this assessment, and only while it is a draft."
        )

    unit_id = form.original_unit_id or getattr(actor, "unit_id", None)
    updates = {}

    form_fields = data.get("form")
    if isinstance(form_fields, dict):
        for key in ("activity_mission", "safety_objectives", "mission_statement", "aircraft_vehicle"):
            if key in form_fields:
                value = _clean_text(form_fields.get(key))
                if key in ("activity_mission", "safety_objectives") and not value:
                    raise MrmEditError(
                        "Activity/Mission and Safety Objectives are required."
                    )
                updates[key] = value

    environment = data.get("environment")
    if isinstance(environment, dict):
        # Accepted for older clients (and the tests pinning the legacy sentence):
        # the four choices are still composed into one sentence.
        missing = [key for key in ENVIRONMENT_KEYS if not _clean_text(environment.get(key))]
        if missing:
            raise MrmEditError(
                "All environment fields are required: weather, lighting, workspace and tempo."
            )
        updates["environment"] = environment_sentence(environment)
    elif environment is not None:
        updates["environment"] = (
            _clean_text(environment) or "Standard operational environment."
        )

    imsafe = data.get("imsafe")
    if isinstance(imsafe, dict):
        answers = {key: bool(imsafe.get(key)) for key in IMSAFE_KEYS}
        unchecked = [item["title"] for item in IMSAFE_CHECKLIST if not answers[item["key"]]]
        if unchecked:
            raise MrmEditError(
                "Check every I'M SAFE item before saving the assessment "
                f"(outstanding: {', '.join(unchecked)})."
            )
        updates["imsafes"] = json.dumps(answers)

    if isinstance(data.get("hazards"), list):
        selected = {
            row.get("hazard_id")
            for row in data["hazards"]
            if isinstance(row, dict) and row.get("hazard_id")
        }
        if len(selected) < MIN_HAZARDS:
            raise MrmEditError(
                f"Select at least {MIN_HAZARDS} hazards before saving the "
                f"assessment; only {len(selected)} are selected."
            )

    with transaction.atomic():
        if "hazards" in data:
            _save_hazards(form, data.get("hazards"), unit_id)
        if "personnel" in data:
            _save_personnel(form, data.get("personnel"), actor, unit_id)
        if updates:
            for key, value in updates.items():
                setattr(form, key, value)
            form.save(update_fields=list(updates))
        _recalculate(form)

    return payload(form, actor)
