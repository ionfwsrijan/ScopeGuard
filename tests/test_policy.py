"""Policy loading and the arithmetic the Examiner relies on."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vouch.errors import ConfigError
from vouch.models import ClaimType
from vouch.policy import Policy


def write(tmp_path: Path, text: str, name: str = "policy.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


VALID = """
id: deal-policy-v1
version: 1
discount: {max_pct: 20, autonomy_pct: 15, margin_floor_pct: 33}
sla: {max_uptime_pct: 99.95, standard_uptime_pct: 99.90, min_observed_uptime_pct: 99.90}
approval_matrix:
  feature: [AE, ENG_LEAD]
  sla: [AE, ENG_LEAD]
  discount: [AE, VP_SALES]
  security: [AE, LEGAL]
"""


def test_repository_policy_loads(repo_root: Path) -> None:
    policy = Policy.load(repo_root / "policies" / "deal_policy_v1.yaml")
    assert policy.id == "deal-policy-v1"
    assert policy.version == 1
    assert policy.discount.max_pct == 20
    assert policy.discount.autonomy_pct == 15
    assert policy.discount.margin_floor_pct == 33
    assert policy.sla.standard_uptime_pct == 99.9
    assert policy.max_evidence_age_days == 90
    assert policy.security.allowed_statuses == ["certified", "in_progress"]


def test_required_roles_are_read_from_the_matrix(tmp_path: Path) -> None:
    policy = Policy.load(write(tmp_path, VALID))
    assert policy.required_roles(ClaimType.FEATURE) == ["AE", "ENG_LEAD"]
    assert policy.required_roles("discount") == ["AE", "VP_SALES"]
    assert policy.required_roles("security") == ["AE", "LEGAL"]


def test_missing_policy_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        Policy.load(tmp_path / "nope.yaml")
    assert "not found" in info.value.message


def test_invalid_yaml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        Policy.load(write(tmp_path, "discount: [unclosed"))
    assert "invalid policy YAML" in info.value.message


def test_non_mapping_yaml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        Policy.load(write(tmp_path, "- just\n- a list\n"))
    assert "mapping" in info.value.message


def test_autonomy_cannot_exceed_max(tmp_path: Path) -> None:
    text = VALID.replace("autonomy_pct: 15", "autonomy_pct: 25")
    with pytest.raises(ConfigError) as info:
        Policy.load(write(tmp_path, text))
    assert "autonomy_pct cannot exceed max_pct" in info.value.message


def test_standard_uptime_cannot_exceed_max(tmp_path: Path) -> None:
    text = VALID.replace("standard_uptime_pct: 99.90", "standard_uptime_pct: 99.99")
    with pytest.raises(ConfigError) as info:
        Policy.load(write(tmp_path, text))
    assert "standard_uptime_pct cannot exceed max_uptime_pct" in info.value.message


def test_every_claim_type_needs_roles(tmp_path: Path) -> None:
    text = VALID.replace("  security: [AE, LEGAL]\n", "")
    with pytest.raises(ConfigError) as info:
        Policy.load(write(tmp_path, text))
    assert "security" in info.value.message


def test_unknown_policy_field_is_rejected(tmp_path: Path) -> None:
    text = VALID + "\nsurprise: true\n"
    with pytest.raises(ConfigError):
        Policy.load(write(tmp_path, text))


def test_percentage_bounds_are_enforced(tmp_path: Path) -> None:
    text = VALID.replace("max_pct: 20", "max_pct: 120")
    with pytest.raises(ConfigError):
        Policy.load(write(tmp_path, text))


def test_margin_arithmetic_matches_the_demo(tmp_path: Path) -> None:
    policy = Policy.load(write(tmp_path, VALID))
    # Revenue = 100 - discount; cost stays at 55% of list.
    assert policy.margin_pct(0, 55) == 45.0
    assert policy.margin_pct(15, 55) == 35.29
    assert policy.margin_pct(20, 55) == 31.25
    assert policy.margin_pct(17.91, 55) == 33.0
    assert policy.margin_pct(100, 55) == -100.0


def test_margin_is_negative_when_cost_exceeds_revenue(tmp_path: Path) -> None:
    policy = Policy.load(write(tmp_path, VALID))
    assert policy.margin_pct(50, 60) == -20.0


def test_policy_defaults_for_optional_sections(tmp_path: Path) -> None:
    text = """
id: p
version: 2
discount: {max_pct: 10, autonomy_pct: 5, margin_floor_pct: 20}
sla: {max_uptime_pct: 99.9, standard_uptime_pct: 99.5, min_observed_uptime_pct: 99.0}
approval_matrix:
  feature: [AE]
  sla: [AE]
  discount: [AE]
  security: [AE]
"""
    policy = Policy.load(write(tmp_path, text))
    assert policy.currency == "USD"
    assert policy.max_evidence_age_days == 90
    assert policy.feature.allow_beta is True
    assert policy.security.require_report_date_for_in_progress is True


def test_policy_model_is_not_mutated_by_assessment_usage(tmp_path: Path) -> None:
    policy = Policy.load(write(tmp_path, VALID))
    before = policy.model_dump()
    policy.margin_pct(20, 55)
    policy.required_roles("discount")
    assert policy.model_dump() == before


def test_validation_error_is_not_leaked_from_load(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Policy.load(write(tmp_path, "id: p\nversion: 0\n"))
    with pytest.raises(ValidationError):
        Policy(
            id="p",
            version=1,
            discount={"max_pct": 1, "autonomy_pct": 1, "margin_floor_pct": 1},
            sla={"max_uptime_pct": 1, "standard_uptime_pct": 1, "min_observed_uptime_pct": 1},
            approval_matrix={},
        )
