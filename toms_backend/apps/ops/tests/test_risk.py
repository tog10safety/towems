"""Risk-engine parity tests.

The expected numbers are computed independently from the legacy algorithm
(routes/mrm.py::calculate_mission_risks) so a regression in the port is caught.
"""

from __future__ import annotations

import pytest

from apps.ops import risk

pytestmark = pytest.mark.django_db


def h(after_l, after_s, ov_l=None, ov_s=None):
    return risk.HazardInput(
        after_likelihood=after_l,
        after_severity=after_s,
        override_likelihood=ov_l,
        override_severity=ov_s,
    )


def test_no_hazards_is_lowest_risk_but_not_zero_denominator():
    result = risk.calculate([])
    assert result.max_risk == 1
    assert result.residual_risk == 0
    assert result.total_percent == 0
    assert result.authority_level == "Operator"
    assert result.risk_category == "Low"


def test_single_catastrophic_frequent_hazard_requires_director():
    # 5 x A(5) = 25 >= 15 -> residual 3 of max 3 -> 100%
    result = risk.calculate([h(5, "A")])
    assert result.max_risk == 3
    assert result.residual_risk == 3
    assert result.total_percent == 100
    assert result.authority_level == "Director for Operations"
    assert result.risk_category == "High"


def test_two_medium_hazards_use_higher_authority_thresholds():
    # 3 x C(3) = 9 >= 6 -> 2 each; residual 4 of max 6 -> 66.67%
    result = risk.calculate([h(3, "C"), h(3, "C")])
    assert result.max_risk == 6
    assert result.residual_risk == 4
    assert round(result.total_percent, 2) == 66.67
    # authority ladder is 50/65/84, risk category is 50/75
    assert result.authority_level == "Squadron Commander"
    assert result.risk_category == "Medium"


def test_low_hazards_operator_authority():
    # 1 x E(1) = 1 < 6 -> residual 1 of max 3 -> 33.3%
    result = risk.calculate([h(1, "E")])
    assert round(result.total_percent, 2) == 33.33
    assert result.authority_level == "Operator"
    assert result.risk_category == "Low"


def test_override_values_take_precedence_over_after_values():
    # Override pushes a benign hazard into the top band.
    result = risk.calculate([h(1, "E", 5, "A")])
    assert result.residual_risk == 3
    assert result.total_percent == 100


def test_override_requires_both_values():
    # Only one override present -> legacy behaviour falls back to after_* values.
    result_both_missing = risk.calculate([h(1, "E")])
    result_partial = risk.calculate([h(1, "E", 5, None)])
    assert result_partial.residual_risk == result_both_missing.residual_risk


def test_rating_string_is_concatenation_not_product():
    assert risk.rating_string(4, "B") == "4B"
    assert risk.rating_string(None, "B") == "N/A"
    assert risk.rating_color("4B") == "danger"
    assert risk.rating_color("3B") == "warning"
    assert risk.rating_color("1E") == "success"


def test_all_nine_bands_are_classified():
    danger = {"5A", "5B", "5C", "4A", "4B", "3A"}
    for likelihood in range(1, 6):
        for severity in "ABCDE":
            token = f"{likelihood}{severity}"
            expected = "danger" if token in danger else None
            color = risk.rating_color(token)
            if expected:
                assert color == "danger", token
            else:
                assert color in {"warning", "success"}, token
