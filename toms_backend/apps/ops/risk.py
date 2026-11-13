"""MRM risk calculations - exact behavioural port of the legacy implementation.

Source of truth: ``routes/mrm.py::calculate_mission_risks`` and
``routes/mrm.py::get_risk_category`` in the Flask application. The arithmetic
is reproduced verbatim, including the two *different* threshold ladders:

* ``authority_level``:  <=50 Operator, <=65 Supervisor,
  <=84 Squadron Commander, else Director for Operations
* ``risk_category``:    <=50 Low, <=75 Medium, else High

These were not unified on purpose - 292 historical forms carry values produced
by exactly this logic, so changing it would silently rewrite the approval
authority of past missions.

Rating strings ("4B") are the concatenation of likelihood and severity letter;
they are NOT numeric products. That convention is persisted in 1,923
hazard_registry rows and must not change.
"""

from __future__ import annotations

from dataclasses import dataclass

SEVERITY_WEIGHTS = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
RED_RISKS = {"5A", "5B", "5C", "4A", "4B", "3A"}
YELLOW_RISKS = {
    "5D", "5E", "4C", "4D", "4E", "3B", "3C", "3D", "2A", "2B", "2C", "1A",
}


@dataclass(frozen=True)
class HazardInput:
    """One selected hazard's effective risk values."""

    after_likelihood: int
    after_severity: str
    override_likelihood: int | None = None
    override_severity: str | None = None


@dataclass(frozen=True)
class RiskResult:
    max_risk: int
    residual_risk: int
    total_percent: float
    authority_level: str
    risk_category: str


def rating_string(likelihood, severity) -> str:
    if likelihood and severity:
        return f"{likelihood}{severity}"
    return "N/A"


def rating_color(rating: str) -> str:
    if rating in RED_RISKS:
        return "danger"
    if rating in YELLOW_RISKS:
        return "warning"
    return "success"


def risk_category(total_percent: float) -> str:
    if total_percent <= 50:
        return "Low"
    if total_percent <= 75:
        return "Medium"
    return "High"


def authority_level(total_percent: float) -> str:
    if total_percent <= 50:
        return "Operator"
    if total_percent <= 65:
        return "Supervisor"
    if total_percent <= 84:
        return "Squadron Commander"
    return "Director for Operations"


def calculate(hazards: list[HazardInput]) -> RiskResult:
    """Compute mission risk exactly as the legacy application does."""
    selected = list(hazards)
    max_risk = len(selected) * 3
    if max_risk == 0:
        max_risk = 1

    residual_risk = 0
    for hazard in selected:
        if hazard.override_likelihood is not None and hazard.override_severity is not None:
            likelihood = hazard.override_likelihood
            severity = hazard.override_severity
        else:
            likelihood = hazard.after_likelihood
            severity = hazard.after_severity

        severity_weight = SEVERITY_WEIGHTS.get(str(severity).upper(), 1)
        try:
            raw_score = int(likelihood) * severity_weight
        except (TypeError, ValueError):
            raw_score = severity_weight

        if raw_score >= 15:
            residual_risk += 3
        elif raw_score >= 6:
            residual_risk += 2
        else:
            residual_risk += 1

    total_percent = (residual_risk / max_risk) * 100
    return RiskResult(
        max_risk=max_risk,
        residual_risk=residual_risk,
        total_percent=total_percent,
        authority_level=authority_level(total_percent),
        risk_category=risk_category(total_percent),
    )
