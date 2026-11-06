"""Policy is deterministic: loading, validation, and margin arithmetic."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from scopeguard.errors import ConfigError
from scopeguard.models import RequestType
from scopeguard.policy import Policy, RateRule


def policy(repo_root: Path) -> Policy:
    return Policy.load(repo_root / "policies" / "scope_policy_v1.yaml")


def test_the_shipped_policy_matches_the_story(repo_root: Path) -> None:
    value = policy(repo_root)
    assert value.id == "scope-policy-v1"
    assert value.version == 1
    assert value.currency == "USD"
    assert value.max_evidence_age_days == 90
    assert value.rate.max_pct == 20
    assert value.rate.autonomy_pct == 12
    assert value.rate.margin_floor_pct == 30
    assert value.timeline.max_weeks_saved == 1
    assert value.timeline.rush_premium_pct == 25
    assert value.timeline.min_confidence_pct == 80
    assert value.terms.max_liability_multiple == 2
    assert value.terms.allow_unlimited_liability is False


def test_approval_matrix_has_every_request_type(repo_root: Path) -> None:
    value = policy(repo_root)
    assert value.required_roles(RequestType.NEW_WORK) == ["ACCOUNT_LEAD", "DELIVERY_LEAD"]
    assert value.required_roles("timeline") == ["ACCOUNT_LEAD", "DELIVERY_LEAD"]
    assert value.required_roles("rate") == ["ACCOUNT_LEAD", "FINANCE"]
    assert value.required_roles("terms") == ["ACCOUNT_LEAD", "LEGAL"]


def test_margin_after_discount_compresses_faster_than_list_minus_cost(repo_root: Path) -> None:
    value = policy(repo_root)
    # 20% off at a 58% cost ratio: (80 - 58) / 80 = 27.50%
    assert value.margin_pct(20, 58) == 27.5
    # 12% off: (88 - 58) / 88 = 34.09%
    assert value.margin_pct(12, 58) == 34.09
    assert value.margin_pct(0, 58) == 42.0


def test_margin_handles_zero_revenue_and_impossible_cost() -> None:
    value = Policy(
        id="p",
        version=1,
        rate=RateRule(max_pct=20, autonomy_pct=10, margin_floor_pct=30),
        timeline={"max_weeks_saved": 1, "rush_premium_pct": 25, "min_confidence_pct": 80},
        terms={"max_liability_multiple": 2},
        approval_matrix={
            "new_work": ["A"],
            "timeline": ["A"],
            "rate": ["A"],
            "terms": ["A"],
        },
    )
    assert value.margin_pct(100, 58) == -100.0
    assert value.margin_pct(50, 100) == -100.0


def test_max_discount_for_margin_solves_the_floor(repo_root: Path) -> None:
    value = policy(repo_root)
    # cost = 58, floor = 30 => revenue = 58 / 0.70 = 82.857 => discount = 17.14
    assert value.max_discount_for_margin(58) == pytest.approx(17.14)
    # At 40% cost, a 30% floor allows 42.86% off.
    assert value.max_discount_for_margin(40) == pytest.approx(42.86)
    # If cost already exceeds the floor revenue, no discount is allowed.
    assert value.max_discount_for_margin(90) == 0.0


def test_max_discount_is_none_when_the_floor_is_unreachable() -> None:
    value = Policy(
        id="p",
        version=1,
        rate=RateRule(max_pct=20, autonomy_pct=10, margin_floor_pct=100),
        timeline={"max_weeks_saved": 1, "rush_premium_pct": 25, "min_confidence_pct": 80},
        terms={"max_liability_multiple": 2},
        approval_matrix={
            "new_work": ["A"],
            "timeline": ["A"],
            "rate": ["A"],
            "terms": ["A"],
        },
    )
    assert value.max_discount_for_margin(58) is None
    assert value.max_discount_for_margin(0) is None


def test_autonomy_cannot_exceed_the_headline_ceiling() -> None:
    with pytest.raises(ValidationError):
        RateRule(max_pct=10, autonomy_pct=20, margin_floor_pct=30)


def test_rate_rule_rejects_impossible_percentages() -> None:
    with pytest.raises(ValidationError):
        RateRule(max_pct=101, autonomy_pct=10, margin_floor_pct=30)
    with pytest.raises(ValidationError):
        RateRule(max_pct=20, autonomy_pct=-1, margin_floor_pct=30)


def test_policy_requires_roles_for_every_request_type() -> None:
    with pytest.raises(ValidationError):
        Policy(
            id="p",
            version=1,
            rate=RateRule(max_pct=20, autonomy_pct=10, margin_floor_pct=30),
            timeline={"max_weeks_saved": 1, "rush_premium_pct": 25, "min_confidence_pct": 80},
            terms={"max_liability_multiple": 2},
            approval_matrix={"new_work": ["A"], "timeline": ["A"], "rate": ["A"]},
        )


def test_missing_policy_raises_a_config_error_with_a_hint(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        Policy.load(tmp_path / "missing.yaml")
    assert info.value.code == "config_error"
    assert "policy not found" in info.value.message
    assert info.value.hint


def test_invalid_yaml_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("rate: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        Policy.load(path)
    assert "invalid policy YAML" in info.value.message


def test_non_mapping_yaml_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        Policy.load(path)
    assert "mapping" in info.value.message


def test_schema_violations_are_wrapped_as_config_errors(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("id: broken\nversion: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        Policy.load(path)
    assert "invalid policy" in info.value.message
