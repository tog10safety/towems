"""The MRM assessment editor: hazard library, hazards, personnel, conditions.

These cover the parts the app did not have before: an MRM could be created and
moved through its workflow, but nothing could fill it in.
"""

import json

import pytest

from apps.legacy.models import HazardRegistry, MRMForm, MRMPersonnel, MissionHazards


def make_hazard(unit, *, code="1.1", description="Terrain", after=(3, "C"), before=(5, "A")):
    return HazardRegistry.objects.create(
        unit=unit.name,
        unit_id=unit,
        activity_code=code,
        hazard_description=description,
        before_likelihood=before[0],
        before_severity=before[1],
        before_risk_rating=f"{before[0]}{before[1]}",
        mitigations="Crew briefing\nRadio check",
        after_likelihood=after[0],
        after_severity=after[1],
        after_risk_rating=f"{after[0]}{after[1]}",
        created_by=unit.created_by if hasattr(unit, "created_by") else None,
    )


def make_hazards(unit, creator, count=8, *, after=(2, "E"), code="1.1"):
    """A full set of library rows: an assessment needs at least eight."""
    rows = []
    for index in range(count):
        rows.append(
            HazardRegistry.objects.create(
                unit=unit.name,
                unit_id=unit,
                activity_code=code,
                hazard_description=f"Terrain {index + 1}",
                before_likelihood=5,
                before_severity="A",
                before_risk_rating="5A",
                mitigations="Crew briefing",
                after_likelihood=after[0],
                after_severity=after[1],
                after_risk_rating=f"{after[0]}{after[1]}",
                created_by=creator,
            )
        )
    return rows


@pytest.fixture
def library(db, unit, creator):
    hazard = HazardRegistry.objects.create(
        unit="ALPHA",
        unit_id=unit,
        activity_code="1.1",
        hazard_description="Rough terrain on approach",
        before_likelihood=5,
        before_severity="A",
        before_risk_rating="5A",
        mitigations="Crew briefing\nRadio check",
        after_likelihood=3,
        after_severity="C",
        after_risk_rating="3C",
        created_by=creator,
    )
    (unit.refresh_from_db() if hasattr(unit, "refresh_from_db") else None)
    return hazard


def test_hazard_library_is_scoped_to_the_callers_unit(
    as_user, creator, unit, other_unit, mrm_form
):
    mine = HazardRegistry.objects.create(
        unit="ALPHA",
        unit_id=unit,
        activity_code="2.1",
        hazard_description="Own unit hazard",
        before_likelihood=4,
        before_severity="B",
        before_risk_rating="4B",
        mitigations="Brief",
        after_likelihood=2,
        after_severity="D",
        after_risk_rating="2D",
        created_by=creator,
    )
    HazardRegistry.objects.create(
        unit="BRAVO",
        unit_id=other_unit,
        activity_code="9.9",
        hazard_description="Someone else's hazard",
        before_likelihood=5,
        before_severity="A",
        before_risk_rating="5A",
        mitigations="—",
        after_likelihood=5,
        after_severity="A",
        after_risk_rating="5A",
        created_by=creator,
    )

    response = as_user(creator).get("/api/risk-register/hazards/?q=Own")
    assert response.status_code == 200, response.content
    ids = [row["hazard_id"] for row in response.json()["results"]]
    assert ids == [mine.id]

    # The other unit's library is invisible even when searched for directly.
    response = as_user(creator).get("/api/risk-register/hazards/?q=Someone")
    assert response.json()["results"] == []


def test_creator_saves_hazards_and_risk_is_recomputed(as_user, creator, unit, mrm_form, library):
    others = make_hazards(unit, creator, 7, after=(2, "E"))
    entries = [
        {
            "hazard_id": library.id,
            "override_likelihood": 2,
            "override_severity": "E",
            "additional_mitigations": "Extra lookout",
        }
    ] + [
        {
            "hazard_id": hazard.id,
            "override_likelihood": 2,
            "override_severity": "E",
        }
        for hazard in others
    ]
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"hazards": entries},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()

    link = MissionHazards.objects.get(mrm_id=mrm_form.id, hazard_id=library.id)
    assert link.selected is True
    assert link.override_likelihood == 2
    assert link.additional_mitigations == "Extra lookout"

    # Eight hazards, each 2 x weight(E)=1 -> 1 point of 3 -> 8 of 24 -> 33.3%
    mrm_form.refresh_from_db()
    assert mrm_form.max_risk == 24
    assert mrm_form.residual_risk == 8
    assert round(mrm_form.total_percent, 1) == pytest.approx(33.3, abs=0.1)
    assert mrm_form.authority_level == "Operator"
    assert body["risk"]["authority_level"] == "Operator"


def test_an_unchecked_imsafe_item_blocks_the_save(as_user, creator, mrm_form):
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"imsafe": {"I": True, "M": True, "S": False, "A": True, "F": True, "E": True}},
        format="json",
    )
    assert response.status_code == 400
    assert "I'M SAFE" in str(response.json())

    # Nothing was written: the checklist is still whatever it was before.
    mrm_form.refresh_from_db()
    stored = json.loads(mrm_form.imsafes) if mrm_form.imsafes else {}
    assert stored.get("S") is not True


def test_fewer_than_eight_hazards_cannot_be_saved(
    as_user, creator, unit, mrm_form, library
):
    """A short hazard list is refused instead of stored."""
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {
            "hazards": [
                {"hazard_id": hazard.id}
                for hazard in [library, *make_hazards(unit, creator, 3)]
            ]
        },
        format="json",
    )
    assert response.status_code == 400
    assert "at least 8 hazards" in str(response.json())
    assert not MissionHazards.objects.filter(mrm_id=mrm_form.id).exists()


def test_environment_and_imsafe_round_trip(as_user, creator, mrm_form):
    payload = {
        "environment": {
            "weather": "Rainy / Wet",
            "lighting": "Nighttime (Poor visibility)",
            "workspace": "Slippery / Uneven surface",
            "operational_tempo": "High tempo / Increased workload",
        },
        "imsafe": {"I": True, "M": True, "S": True, "A": True, "F": True, "E": True},
    }
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/", payload, format="json"
    )
    assert response.status_code == 200, response.content

    mrm_form.refresh_from_db()
    # The sentence is what the legacy screens print.
    assert mrm_form.environment.startswith("Rainy / wet. Nighttime (poor visibility).")
    assert all(json.loads(mrm_form.imsafes).values())

    body = response.json()
    # The four legacy choices still compose into the one sentence the form shows.
    assert body["environment"].startswith("Rainy / wet.")
    assert "High tempo / increased workload" in body["environment"]
    assert body["imsafe"]["S"] is True


def test_environment_is_free_text(as_user, creator, mrm_form):
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"environment": "Rainy and wet, poor visibility. Overnight. Slippery trail. High tempo."},
        format="json",
    )

    assert response.status_code == 200, response.content
    mrm_form.refresh_from_db()
    assert mrm_form.environment.startswith("Rainy and wet, poor visibility.")
    assert response.json()["environment"].startswith("Rainy and wet, poor visibility.")


def test_evaluator_cannot_write_the_assessment(as_user, evaluator, mrm_form):
    # Assigned, so the assessment is readable to them; editing stays the
    # creator's alone.
    mrm_form.reviewed_by = evaluator
    mrm_form.save(update_fields=["reviewed_by"])

    response = as_user(evaluator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"form": {"safety_objectives": "Changed by the evaluator"}},
        format="json",
    )
    assert response.status_code == 403
    mrm_form.refresh_from_db()
    assert mrm_form.safety_objectives == "Safe operations"


def test_assessment_is_read_only_once_submitted(as_user, creator, mrm_form):
    mrm_form.status = "submitted"
    mrm_form.save(update_fields=["status"])
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"form": {"safety_objectives": "Too late"}},
        format="json",
    )
    assert response.status_code == 403


def test_hazard_from_another_unit_is_refused(as_user, creator, unit, other_unit, mrm_form):
    foreign = HazardRegistry.objects.create(
        unit="BRAVO",
        unit_id=other_unit,
        activity_code="9.9",
        hazard_description="Not ours",
        before_likelihood=5,
        before_severity="A",
        before_risk_rating="5A",
        mitigations="—",
        after_likelihood=5,
        after_severity="A",
        after_risk_rating="5A",
        created_by=creator,
    )
    entries = [
        {"hazard_id": hazard.id} for hazard in make_hazards(unit, creator, 7)
    ] + [{"hazard_id": foreign.id}]
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"hazards": entries},
        format="json",
    )
    assert response.status_code == 400
    assert not MissionHazards.objects.filter(mrm_id=mrm_form.id).exists()


def test_personnel_tagging_replaces_the_roster(as_user, creator, safety, unit, mrm_form):
    first = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"personnel": [{"user_id": safety.id, "role": "Mission Lead"}]},
        format="json",
    )
    assert first.status_code == 200, first.content
    assert MRMPersonnel.objects.filter(mrm_id=mrm_form.id, is_active=True).count() == 1

    second = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"personnel": []},
        format="json",
    )
    assert second.status_code == 200, second.content
    assert MRMPersonnel.objects.filter(mrm_id=mrm_form.id, is_active=True).count() == 0
    # The row is kept as history, not deleted.
    assert MRMPersonnel.objects.filter(mrm_id=mrm_form.id).count() == 1


def test_required_fields_are_checked_when_present(as_user, creator, mrm_form):
    response = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {"form": {"safety_objectives": "   "}},
        format="json",
    )
    assert response.status_code == 400
    mrm_form.refresh_from_db()
    assert mrm_form.safety_objectives == "Safe operations"


def test_details_can_be_read_by_the_viewer(as_user, creator, mrm_form, library):
    response = as_user(creator).get(f"/api/risk-register/{mrm_form.id}/details/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["can_edit"] is True
    # Environmental conditions are free text now; only the I'M SAFE prompts remain
    # a fixed list the form has to offer.
    assert isinstance(body["environment"], str)
    assert body["options"]["imsafe"][0]["key"] == "I"
    assert body["hazards"] == []


def test_assessment_summary_reflects_saved_hazards(as_user, creator, unit, mrm_form, library):
    """The page's Assessment card reads the same numbers the editor just saved."""
    hazards = [library, *make_hazards(unit, creator, 7, after=(2, "D"))]
    saved = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {
            "environment": "Rainy and wet. Overnight.",
            "hazards": [
                {
                    "hazard_id": hazard.id,
                    "override_likelihood": 2,
                    "override_severity": "D",
                    "additional_mitigations": "Extra brief",
                }
                for hazard in hazards
            ],
        },
        format="json",
    )
    assert saved.status_code == 200, saved.content
    edited = saved.json()["risk"]

    summary = as_user(creator).get(f"/api/risk-register/{mrm_form.id}/").json()
    # Eight hazards at 2 x D score one point each: 8 of 24 -> 33.3% -> Operator.
    assert summary["max_risk"] == edited["max_risk"] == 24
    assert summary["residual_risk"] == edited["residual_risk"] == 8
    assert summary["total_percent"] == pytest.approx(edited["total_percent"], abs=0.05)
    assert summary["total_percent"] == pytest.approx(33.3, abs=0.1)
    assert summary["authority_level"] == edited["authority_level"]
    assert summary["risk_category"] == edited["risk_category"]
