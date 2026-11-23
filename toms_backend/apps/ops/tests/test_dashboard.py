"""Dashboard statistics must be database-driven and permission-filtered."""

from __future__ import annotations

import pytest

from apps.ops.models import InventoryCategory, InventoryItem, Vehicle

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(db, unit, creator):
    from apps.ops.tests.test_missions import air_payload, ground_payload

    client = None
    return {"unit": unit, "creator": creator}


def test_dashboard_counts_reflect_records(as_user, planner, safety, unit):
    from apps.ops.tests.test_missions import air_payload, ground_payload

    client = as_user(planner)
    client.post("/api/missions/", ground_payload(unit), format="json")
    client.post("/api/missions/", air_payload(unit), format="json")

    # A test-local code: the standard catalogue is seeded by migration 0011.
    category = InventoryCategory.objects.create(name="Supplies", code="T-SUP")
    InventoryItem.objects.create(
        category=category, name="Water", unit=unit, quantity_total=100, quantity_available=2
    )
    Vehicle.objects.create(registration_number="V-1", vehicle_type="Truck", unit=unit)

    response = as_user(safety).get("/api/dashboard/")
    assert response.status_code == 200
    body = response.json()
    assert body["missions"]["by_category"]["ground"] == 1
    assert body["missions"]["by_category"]["air"] == 1
    assert body["inventory"]["items"] == 1
    assert body["inventory"]["low_stock"] == 1
    assert body["vehicles"]["available"] == 1


def test_dashboard_hides_sensitive_inventory_from_creator(as_user, creator, unit):
    # The seeded controlled category, so this also proves the catalogue's
    # ammunition class is really controlled.
    category = InventoryCategory.objects.get(code="AMMO")
    InventoryItem.objects.create(
        category=category,
        name="Rounds",
        unit=unit,
        quantity_total=500,
        quantity_available=500,
        is_sensitive=True,
    )
    body = as_user(creator).get("/api/dashboard/").json()
    assert body["inventory"]["items"] == 0
    assert body["inventory"]["sensitive_visible"] is False

    body = as_user(creator).get("/api/dashboard/")
    assert body.status_code == 200


def test_dashboard_marks_sensitive_visible_for_safety(as_user, safety):
    body = as_user(safety).get("/api/dashboard/").json()
    assert body["inventory"]["sensitive_visible"] is True


def test_dashboard_requires_authentication(api):
    assert api.get("/api/dashboard/").status_code in (401, 403)


def test_only_live_ground_missions_count_as_outstanding_mrm(
    as_user, safety, creator, unit
):
    from datetime import timedelta

    from django.utils import timezone

    from apps.ops.models import Mission

    def mission(number, category, status, start_at):
        return Mission.objects.create(
            mission_number=number,
            category=category,
            title="Without an MRM",
            purpose="Training",
            unit=unit,
            created_by=creator,
            status=status,
            start_at=start_at,
        )

    # A ground patrol flown two days ago with no risk assessment at all. The gap
    # is real, but the patrol is over, so it is not work the watch can act on.
    mission("MSN-MRM-DONE", "ground", "completed", timezone.now() - timedelta(days=2))
    # Still ahead: this one is genuinely outstanding and not yet overdue.
    mission("MSN-MRM-PLANNED", "ground", "planned", timezone.now() + timedelta(days=1))
    # Air missions are excluded from the MRM requirement, so this sortie must
    # not raise the outstanding count however soon it departs.
    mission("MSN-MRM-AIR", "air", "planned", timezone.now() + timedelta(days=1))

    body = as_user(safety).get("/api/dashboard/").json()["mrm"]

    assert body["missing"] == 1
    assert body["overdue"] == 0
    assert body["pending"] == 0


def test_safety_analytics_is_only_returned_to_a_unit_safety_officer(
    as_user, creator, safety, unit
):
    assert as_user(creator).get("/api/dashboard/").json()["safety"] is None

    body = as_user(safety).get("/api/dashboard/").json()
    assert body["safety"] is not None
    assert body["safety"]["unit"]["id"] == unit.id


def test_safety_analytics_covers_only_the_officers_own_unit(
    as_user, creator, safety, unit
):
    from apps.core.factories import make_unit
    from apps.legacy.models import HazardRegistry, MRMForm
    from apps.ops.models import Mission

    other = make_unit(name="Bravo Safety Wing", acronym="BSW")

    def mrm(number, target, category, percent, approved, review="pending"):
        return MRMForm.objects.create(
            mrm_number=number,
            activity_mission=f"Activity {number}",
            created_by=creator,
            safety_objectives="Safe",
            status="reviewed",
            review_status=review,
            authority_approved=approved,
            original_unit_id=target.id,
            original_unit_name=target.name,
            risk_category=category,
            total_percent=percent,
        )

    mrm("MRM-OWN-OK", unit, "Low", 20.0, True, "approved")
    mrm("MRM-OWN-OPEN", unit, "High", 80.0, False)
    mrm("MRM-OTHER", other, "High", 90.0, False)

    def hazard(target, code, rating, severity):
        return HazardRegistry.objects.create(
            unit=target.name,
            unit_id=target,
            activity_code=code,
            hazard_description=f"Hazard {code}",
            before_likelihood=5 if rating == "5A" else 2,
            before_severity=severity,
            before_risk_rating=rating,
            mitigations="",
            after_likelihood=1,
            after_severity="E",
            after_risk_rating="1E",
            created_by=creator,
        )

    hazard(unit, "Aerodrome", "5A", "A")
    hazard(unit, "Ramp", "2D", "D")
    hazard(other, "Foreign", "5A", "A")

    Mission.objects.create(
        mission_number="MSN-SAFETY-1",
        category="ground",
        title="Own unit patrol",
        purpose="Presence",
        unit=unit,
        created_by=creator,
        status="draft",
    )
    Mission.objects.create(
        mission_number="MSN-SAFETY-2",
        category="ground",
        title="Other unit patrol",
        purpose="Presence",
        unit=other,
        created_by=creator,
        status="draft",
    )

    body = as_user(safety).get("/api/dashboard/").json()["safety"]

    assert body["mrm"]["total"] == 2
    assert body["mrm"]["approved"] == 1
    assert body["mrm"]["awaiting"] == 1
    assert body["mrm"]["returned"] == 0
    assert body["risk"]["low"] == 1
    assert body["risk"]["high"] == 1
    assert body["risk"]["average_percent"] == 20.0
    assert body["hazards"]["total"] == 2
    assert body["hazards"]["red"] == 1
    assert body["hazards"]["green"] == 1
    assert body["hazards"]["activity_codes"] == 2
    assert body["missions"]["total"] == 1
    assert body["missions"]["without_approved_mrm"] == 1
    assert [row["mrm_number"] for row in body["attention"]] == ["MRM-OWN-OPEN"]


def test_safety_watch_ignores_air_and_finished_missions(as_user, safety, creator, unit):
    from datetime import timedelta

    from django.utils import timezone

    from apps.legacy.models import MRMForm
    from apps.ops.models import Mission, RiskAssessment

    def form(number):
        return MRMForm.objects.create(
            mrm_number=number,
            activity_mission=f"Activity {number}",
            created_by=creator,
            safety_objectives="Safe",
            status="submitted",
            review_status="pending",
            authority_approved=False,
            original_unit_id=unit.id,
            original_unit_name=unit.name,
        )

    def mission(number, category, status):
        return Mission.objects.create(
            mission_number=number,
            category=category,
            title="Risk work",
            purpose="Training",
            unit=unit,
            created_by=creator,
            status=status,
            start_at=timezone.now() - timedelta(days=1),
        )

    # A finished ground patrol whose form was never approved: an audit gap, not
    # a queued action. Its form is linked the way the risk workflow links it.
    done = mission("MSN-WATCH-DONE", "ground", "completed")
    RiskAssessment.objects.create(mission=done, mrm_form=form("MRM-DONE"))

    # An air sortie keeps its MRM through the mission's direct form link.
    air = mission("MSN-WATCH-AIR", "air", "planned")
    air.risk_form = form("MRM-AIR")
    air.save(update_fields=["risk_form"])

    # Unattached paperwork still needs its approval.
    form("MRM-STANDALONE")

    body = as_user(safety).get("/api/dashboard/").json()["safety"]

    assert body["missions"]["without_approved_mrm"] == 0
    assert [row["mrm_number"] for row in body["attention"]] == ["MRM-STANDALONE"]


def test_safety_officer_can_hide_and_restore_an_attention_item(
    as_user, safety, creator, unit
):
    from apps.legacy.models import MRMForm
    from apps.ops.models import MRMAttentionDismissal

    form = MRMForm.objects.create(
        mrm_number="MRM-HIDE-1",
        activity_mission="Hide from the safety dashboard",
        created_by=creator,
        safety_objectives="Safe",
        status="draft",
        review_status="pending",
        original_unit_id=unit.id,
        original_unit_name=unit.name,
    )

    body = as_user(safety).get("/api/dashboard/").json()["safety"]
    assert form.id in {row["id"] for row in body["attention"]}
    assert body["attention_hidden_count"] == 0

    hidden = as_user(safety).post(
        f"/api/risk-register/{form.id}/hide-attention/", {}, format="json"
    )
    assert hidden.status_code == 200, hidden.content
    assert hidden.json()["hidden"] is True
    assert MRMAttentionDismissal.objects.filter(user=safety, mrm_form=form).exists()

    body = as_user(safety).get("/api/dashboard/").json()["safety"]
    assert form.id not in {row["id"] for row in body["attention"]}
    assert form.id in {row["id"] for row in body["attention_hidden"]}
    assert body["attention_hidden_count"] == 1

    restored = as_user(safety).post(
        f"/api/risk-register/{form.id}/restore-attention/", {}, format="json"
    )
    assert restored.status_code == 200, restored.content
    assert restored.json()["hidden"] is False
    assert not MRMAttentionDismissal.objects.filter(user=safety, mrm_form=form).exists()

    body = as_user(safety).get("/api/dashboard/").json()["safety"]
    assert form.id in {row["id"] for row in body["attention"]}
    assert body["attention_hidden_count"] == 0


def test_hiding_attention_is_limited_to_safety_officers(as_user, creator, unit):
    from apps.legacy.models import MRMForm

    form = MRMForm.objects.create(
        mrm_number="MRM-HIDE-2",
        activity_mission="Creator cannot hide this",
        created_by=creator,
        safety_objectives="Safe",
        status="draft",
        review_status="pending",
        original_unit_id=unit.id,
        original_unit_name=unit.name,
    )

    response = as_user(creator).post(
        f"/api/risk-register/{form.id}/hide-attention/", {}, format="json"
    )

    assert response.status_code == 403


def test_recent_activity_shows_username_not_full_name(as_user, admin, unit):
    """Spec §2: the feed identifies the account, not the person's full name."""
    from apps.core.factories import make_user
    from apps.ops.models import Mission

    actor = make_user(
        unit=unit, authority="Creator", username="j.delacruz", first_name="Juan", last_name="Dela Cruz"
    )
    Mission.objects.create(
        mission_number="MSN-ACT-1",
        category="ground",
        title="Activity check",
        purpose="x",
        unit=unit,
        created_by=actor,
        status="draft",
    )
    from apps.core.audit import log_action

    entry = Mission.objects.get(mission_number="MSN-ACT-1")
    log_action(
        actor=actor,
        action="create",
        entity_type="mission",
        entity_id=entry.id,
        after={"mission_number": entry.mission_number},
    )

    body = as_user(admin).get("/api/dashboard/").json()
    row = next(item for item in body["recent_activity"] if item["entity_id"] == str(entry.id))
    assert row["actor"] == "j.delacruz"
    assert row["actor"] != "Juan Dela Cruz"
    assert row["actor_name"].endswith("Juan Dela Cruz")


def test_dashboard_personnel_availability(as_user, planner, unit):
    from apps.core.factories import make_user

    assigned = make_user(unit=unit)
    body_before = as_user(planner).get("/api/dashboard/").json()

    from apps.ops.tests.test_missions import ground_payload

    client = as_user(planner)
    mission = client.post("/api/missions/", ground_payload(unit), format="json").json()
    client.post(f"/api/missions/{mission['id']}/approve/", {}, format="json")  # 403, stays draft

    body_after = as_user(planner).get("/api/dashboard/").json()
    assert body_after["personnel"]["total"] >= body_before["personnel"]["total"]


def test_dashboard_lists_the_pol_batches_expiring_first(as_user, safety, unit):
    """Monitoring names the shelf lives that run out, not just a stock count."""
    import datetime as dt

    from django.utils import timezone

    today = timezone.now().date()
    fuel = InventoryCategory.objects.get(code="POL-01")

    def batch(name, *, days, drums, available=None):
        return InventoryItem.objects.create(
            category=fuel,
            name=name,
            unit=unit,
            storage_location="Unit POL Dump",
            quantity_total=drums,
            quantity_available=drums if available is None else available,
            unit_of_measure="drum",
            fuel_grade="jet_a1",
            fuel_form="drum",
            drum_capacity_litres=200,
            expiry_date=today + dt.timedelta(days=days),
        )

    expired = batch("Expired batch", days=-3, drums=4)
    soon = batch("Soon batch", days=5, drums=10)
    later = batch("Later batch", days=90, drums=10)
    drained = batch("Drained batch", days=2, drums=0)

    body = as_user(safety).get("/api/dashboard/").json()["watch"]

    assert body["horizon_days"] == 30
    # Past-due stock is the most urgent, so it leads the list.
    assert body["fuel_expiring_count"] == 2
    rows = body["fuel_expiring"]
    assert [row["id"] for row in rows] == [expired.id, soon.id]
    assert rows[0]["days_left"] == -3
    assert rows[1]["grade"] == "Jet A1"
    assert rows[1]["drums"] == 10
    assert rows[1]["litres"] == 2000
    assert rows[1]["location"] == "Unit POL Dump"

    listed = {row["id"] for row in rows}
    assert later.id not in listed  # beyond the 30-day horizon
    assert drained.id not in listed  # nothing left to draw down

    summary = as_user(safety).get("/api/dashboard/").json()
    # The tiles read the same stock the watch list does: every drum on hand,
    # whether or not it expires this month, summed in litres.
    assert summary["fuel"]["litres_available"] == 4800
    assert summary["fuel"]["drums_available"] == 24
    assert summary["fuel"]["expiring"] == 2


def test_dashboard_vehicle_counts_ignore_personal_vehicles(as_user, safety, unit):
    """The tile counts the unit's fleet, the same rows the register lists."""
    Vehicle.objects.create(
        registration_number="FLT-1", vehicle_type="Truck", unit=unit
    )
    Vehicle.objects.create(
        registration_number="FLT-2",
        vehicle_type="Truck",
        unit=unit,
        status="on_trip",
    )
    # A POV is a person's car: the register hides it, so the tile must too.
    Vehicle.objects.create(
        registration_number="POV-1", vehicle_type="Car", unit=unit, is_pov=True
    )

    body = as_user(safety).get("/api/dashboard/").json()

    assert body["vehicles"]["available"] == 1
    assert body["vehicles"]["on_trip"] == 1
    assert body["vehicles"]["on_trip_details"] == []


def test_dashboard_names_the_vehicle_on_each_running_trip(as_user, safety, unit):
    """The tile says which vehicle is out, and whether it is a POV or a PAFMV."""
    from apps.ops.models import VehicleTrip, VehicleTripVehicle

    fleet = Vehicle.objects.create(
        registration_number="KM450-T1", vehicle_type="KM450", unit=unit
    )
    pov = Vehicle.objects.create(
        registration_number="POV-T1", vehicle_type="Car", unit=unit, is_pov=True
    )
    for index, vehicle in enumerate((fleet, pov), start=1):
        trip = VehicleTrip.objects.create(
            trip_number=f"TRIP-D1-{index}",
            title="Convoy move",
            status="in_progress",
            created_by=safety,
        )
        VehicleTripVehicle.objects.create(trip=trip, vehicle=vehicle)

    details = as_user(safety).get("/api/dashboard/").json()["vehicles"][
        "on_trip_details"
    ]

    assert details == [
        {
            "vehicle_id": fleet.id,
            "registration_number": "KM450-T1",
            "kind": "PAFMV",
            "trip_number": "TRIP-D1-1",
            "trip_title": "Convoy move",
        },
        {
            "vehicle_id": pov.id,
            "registration_number": "POV-T1",
            "kind": "POV",
            "trip_number": "TRIP-D1-2",
            "trip_title": "Convoy move",
        },
    ]
