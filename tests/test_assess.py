"""The Pricer: deterministic, evidence-bound decisions for four request types."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scopeguard.assess import assess_engagement
from scopeguard.models import (
    Engagement,
    Evidence,
    EvidenceBook,
    RequestType,
    Verdict,
)
from scopeguard.policy import NewWorkRule, Policy, TimelineRule


def policy(repo_root: Path) -> Policy:
    return Policy.load(repo_root / "policies" / "scope_policy_v1.yaml")


def request(
    request_id: str,
    request_type: str,
    requested: dict[str, Any] | None = None,
    *,
    due: date | None = date(2026, 12, 15),
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "id": request_id,
        "type": request_type,
        "title": title or request_id.title(),
        "requested_text": f"{request_id} requested.",
        "requested_value": requested or {},
        "due": due,
    }


def evidence(
    request_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    evidence_id: str | None = None,
    observed_at: date = date(2026, 11, 1),
    valid_until: date | None = None,
) -> Evidence:
    return Evidence(
        id=evidence_id or f"EV-{request_id}-1",
        request_id=request_id,
        kind=kind,
        source="test source",
        observed_at=observed_at,
        valid_until=valid_until,
        summary="test evidence",
        payload=payload,
    )


def run(
    requests: list[dict[str, Any]],
    items: list[Evidence],
    *,
    policy_value: Policy,
    cost_ratio: float = 58,
    as_of: date = date(2026, 11, 4),
) -> dict[str, Any]:
    engagement = Engagement(
        id="test-2026",
        client="Test Client",
        requested_by="Dana",
        contract_value=120000,
        delivery_cost_ratio_pct=cost_ratio,
        requests=requests,
    )
    decisions = assess_engagement(
        engagement=engagement,
        evidence=EvidenceBook(engagement_id=engagement.id, items=items),
        policy=policy_value,
        as_of=as_of,
    )
    assert [item.request_id for item in decisions] == [item["id"] for item in requests]
    return {item.request_id: item for item in decisions}


# ------------------------------------------------------------------ new work
def test_new_work_approves_when_the_estimate_supports_ga(repo_root: Path) -> None:
    decisions = run(
        [request("MOBILE", "new_work", {"ga_date": "2026-12-31"})],
        [evidence("MOBILE", "estimate", {"hours": 320, "rate": 150, "ga_date": "2026-11-30"})],
        policy_value=policy(repo_root),
    )
    decision = decisions["MOBILE"]
    assert decision.verdict == Verdict.APPROVE
    assert decision.amount == 48000.0
    assert "2026-12-31" in decision.priced_text
    assert decision.approved_value["kind"] == "ga"
    assert decision.evidence_ids == ["EV-MOBILE-1"]


def test_new_work_narrows_to_beta_when_ga_is_later(repo_root: Path) -> None:
    decisions = run(
        [request("MOBILE", "new_work", {"ga_date": "2026-12-15"})],
        [
            evidence(
                "MOBILE",
                "estimate",
                {
                    "hours": 320,
                    "beta_hours": 200,
                    "rate": 150,
                    "beta_date": "2026-12-01",
                    "ga_date": "2027-03-15",
                    "ga_quarter": "Q1 2027",
                },
            )
        ],
        policy_value=policy(repo_root),
    )
    decision = decisions["MOBILE"]
    assert decision.verdict == Verdict.NARROW
    assert decision.amount == 30000.0
    assert "beta by 2026-12-01" in decision.priced_text
    assert "Q1 2027" in decision.priced_text
    assert decision.approved_value["ga_committed"] is False
    assert any("beta" in condition.casefold() for condition in decision.conditions)


def test_new_work_derives_the_ga_quarter_when_payload_omits_it(repo_root: Path) -> None:
    decisions = run(
        [request("MOBILE", "new_work", {"ga_date": "2026-12-15"})],
        [
            evidence(
                "MOBILE",
                "estimate",
                {
                    "hours": 320,
                    "beta_hours": 200,
                    "rate": 150,
                    "beta_date": "2026-12-01",
                    "ga_date": "2027-02-15",
                },
            )
        ],
        policy_value=policy(repo_root),
    )
    assert "Q1 2027" in decisions["MOBILE"].priced_text


def test_new_work_without_evidence_escalates_instead_of_inventing_a_price(repo_root: Path) -> None:
    decision = run([request("MOBILE", "new_work")], [], policy_value=policy(repo_root))["MOBILE"]
    assert decision.verdict == Verdict.ESCALATE
    assert decision.amount is None
    assert decision.is_commitment is False
    assert decision.conditions


def test_new_work_refuses_beta_when_policy_disables_it(repo_root: Path) -> None:
    value = policy(repo_root).model_copy(
        update={"new_work": NewWorkRule(allow_beta=False, allow_ga_estimate=True)}
    )
    decision = run(
        [request("MOBILE", "new_work", {"ga_date": "2026-12-15"})],
        [
            evidence(
                "MOBILE",
                "estimate",
                {"hours": 320, "rate": 150, "beta_date": "2026-12-01", "ga_date": "2027-03-15"},
            )
        ],
        policy_value=value,
    )["MOBILE"]
    assert decision.verdict == Verdict.REFUSE
    assert decision.amount is None


def test_new_work_refuses_unsupported_dates_when_estimates_are_disabled(repo_root: Path) -> None:
    value = policy(repo_root).model_copy(
        update={"new_work": NewWorkRule(allow_beta=False, allow_ga_estimate=False)}
    )
    decision = run(
        [request("MOBILE", "new_work", {"ga_date": "2026-12-15"})],
        [evidence("MOBILE", "estimate", {"hours": 320, "rate": 150})],
        policy_value=value,
    )["MOBILE"]
    assert decision.verdict == Verdict.REFUSE


def test_new_work_escalates_when_no_date_is_supportable(repo_root: Path) -> None:
    decision = run(
        [request("MOBILE", "new_work", {"ga_date": "2026-11-01"})],
        [
            evidence(
                "MOBILE",
                "estimate",
                {"hours": 320, "rate": 150, "beta_date": "2026-12-01", "ga_date": "2027-03-15"},
            )
        ],
        policy_value=policy(repo_root),
    )["MOBILE"]
    assert decision.verdict == Verdict.ESCALATE
    assert decision.amount is None


def test_new_work_uses_the_latest_estimate(repo_root: Path) -> None:
    decisions = run(
        [request("MOBILE", "new_work", {"ga_date": "2026-12-31"})],
        [
            evidence(
                "MOBILE",
                "estimate",
                {"hours": 100, "rate": 150, "ga_date": "2026-11-30"},
                evidence_id="EV-OLD",
                observed_at=date(2026, 10, 1),
            ),
            evidence(
                "MOBILE",
                "estimate",
                {"hours": 200, "rate": 150, "ga_date": "2026-12-15"},
                evidence_id="EV-NEW",
                observed_at=date(2026, 11, 2),
            ),
        ],
        policy_value=policy(repo_root),
    )
    assert decisions["MOBILE"].amount == 30000.0
    assert decisions["MOBILE"].evidence_ids == ["EV-NEW"]


# ------------------------------------------------------------------ timeline
def test_timeline_approves_when_capacity_supports_the_request(repo_root: Path) -> None:
    decision = run(
        [request("RUSH", "timeline", {"weeks_saved": 1})],
        [
            evidence(
                "RUSH",
                "capacity",
                {
                    "rush_hours": 64,
                    "rate": 150,
                    "premium_pct": 25,
                    "confidence_pct": 88,
                    "supported_weeks": 1,
                },
            )
        ],
        policy_value=policy(repo_root),
    )["RUSH"]
    assert decision.verdict == Verdict.APPROVE
    assert decision.amount == 12000.0
    assert decision.approved_value["weeks_saved"] == 1


def test_timeline_narrows_to_the_supported_weeks(repo_root: Path) -> None:
    decision = run(
        [request("RUSH", "timeline", {"weeks_saved": 2})],
        [
            evidence(
                "RUSH",
                "capacity",
                {
                    "rush_hours": 64,
                    "rate": 150,
                    "premium_pct": 25,
                    "confidence_pct": 88,
                    "supported_weeks": 1,
                },
            )
        ],
        policy_value=policy(repo_root),
    )["RUSH"]
    assert decision.verdict == Verdict.NARROW
    assert decision.approved_value["weeks_saved"] == 1
    assert decision.approved_value["requested_weeks"] == 2
    assert "1.0 week(s)" in decision.priced_text


def test_timeline_escalates_below_the_confidence_floor(repo_root: Path) -> None:
    decision = run(
        [request("RUSH", "timeline", {"weeks_saved": 1})],
        [
            evidence(
                "RUSH",
                "capacity",
                {"rush_hours": 64, "rate": 150, "confidence_pct": 60, "supported_weeks": 1},
            )
        ],
        policy_value=policy(repo_root),
    )["RUSH"]
    assert decision.verdict == Verdict.ESCALATE
    assert decision.amount is None


def test_timeline_refuses_when_no_weeks_are_compressible(repo_root: Path) -> None:
    decision = run(
        [request("RUSH", "timeline", {"weeks_saved": 1})],
        [
            evidence(
                "RUSH",
                "capacity",
                {"rush_hours": 64, "rate": 150, "confidence_pct": 88, "supported_weeks": 0},
            )
        ],
        policy_value=policy(repo_root),
    )["RUSH"]
    assert decision.verdict == Verdict.REFUSE


def test_timeline_without_capacity_evidence_escalates(repo_root: Path) -> None:
    decision = run([request("RUSH", "timeline")], [], policy_value=policy(repo_root))["RUSH"]
    assert decision.verdict == Verdict.ESCALATE
    assert decision.amount is None


def test_timeline_uses_the_policy_premium_when_the_plan_omits_it(repo_root: Path) -> None:
    decision = run(
        [request("RUSH", "timeline", {"weeks_saved": 1})],
        [
            evidence(
                "RUSH",
                "capacity",
                {"rush_hours": 64, "rate": 150, "confidence_pct": 88, "supported_weeks": 1},
            )
        ],
        policy_value=policy(repo_root),
    )["RUSH"]
    assert decision.amount == 12000.0
    assert decision.approved_value["premium_pct"] == 25


def test_timeline_respects_the_policy_cap_even_when_capacity_is_available(repo_root: Path) -> None:
    value = policy(repo_root).model_copy(
        update={
            "timeline": TimelineRule(
                max_weeks_saved=1,
                rush_premium_pct=25,
                min_confidence_pct=80,
                require_capacity_evidence=True,
            )
        }
    )
    decision = run(
        [request("RUSH", "timeline", {"weeks_saved": 3})],
        [
            evidence(
                "RUSH",
                "capacity",
                {"rush_hours": 64, "rate": 150, "confidence_pct": 95, "supported_weeks": 5},
            )
        ],
        policy_value=value,
    )["RUSH"]
    assert decision.verdict == Verdict.NARROW
    assert decision.approved_value["weeks_saved"] == 1


# ---------------------------------------------------------------------- rate
def test_rate_applies_the_requested_discount_against_positive_line_items(repo_root: Path) -> None:
    decisions = run(
        [
            request("MOBILE", "new_work", {"ga_date": "2026-12-15"}),
            request("RATE", "rate", {"discount_pct": 20}),
        ],
        [
            evidence("MOBILE", "estimate", {"hours": 200, "rate": 150, "ga_date": "2026-11-30"}),
            evidence("RATE", "rate_card", {"hourly_rate": 150}),
        ],
        policy_value=policy(repo_root),
    )
    rate = decisions["RATE"]
    assert rate.verdict == Verdict.APPROVE
    assert rate.amount == -6000.0
    assert rate.approved_value["base_amount"] == 30000.0
    assert rate.approved_value["margin_pct"] == 27.5


def test_rate_narrows_above_the_headline_ceiling(repo_root: Path) -> None:
    decisions = run(
        [
            request("MOBILE", "new_work", {"ga_date": "2026-12-15"}),
            request("RATE", "rate", {"discount_pct": 25}),
        ],
        [
            evidence("MOBILE", "estimate", {"hours": 200, "rate": 150, "ga_date": "2026-11-30"}),
            evidence("RATE", "rate_card", {"hourly_rate": 150}),
        ],
        policy_value=policy(repo_root),
    )
    assert decisions["RATE"].verdict == Verdict.NARROW
    assert decisions["RATE"].approved_value["discount_pct"] == 20
    assert decisions["RATE"].amount == -6000.0


def test_rate_without_a_rate_card_escalates(repo_root: Path) -> None:
    decision = run(
        [request("RATE", "rate", {"discount_pct": 20})],
        [],
        policy_value=policy(repo_root),
    )["RATE"]
    assert decision.verdict == Verdict.ESCALATE
    assert decision.amount is None


def test_rate_of_zero_is_a_no_op_discount(repo_root: Path) -> None:
    decisions = run(
        [
            request("MOBILE", "new_work", {"ga_date": "2026-12-15"}),
            request("RATE", "rate", {"discount_pct": 0}),
        ],
        [
            evidence("MOBILE", "estimate", {"beta_hours": 200, "rate": 150}),
            evidence("RATE", "rate_card", {"hourly_rate": 150}),
        ],
        policy_value=policy(repo_root),
    )
    assert decisions["RATE"].verdict == Verdict.APPROVE
    assert decisions["RATE"].amount == 0.0


# --------------------------------------------------------------------- terms
def test_terms_refuses_unlimited_liability_and_caps_at_policy(repo_root: Path) -> None:
    decision = run(
        [request("TERMS", "terms", {"unlimited_liability": True})],
        [evidence("TERMS", "contract", {"review_hours": 40, "rate": 150})],
        policy_value=policy(repo_root),
    )["TERMS"]
    assert decision.verdict == Verdict.NARROW
    assert decision.amount == 6000.0
    assert decision.approved_value["liability_multiple"] == 2
    assert decision.approved_value["unlimited_liability"] is False
    assert "refused" in decision.priced_text
    assert any("legal review" in condition.casefold() for condition in decision.conditions)


def test_terms_narrows_a_multiple_above_the_policy_cap(repo_root: Path) -> None:
    decision = run(
        [request("TERMS", "terms", {"liability_multiple": 5})],
        [evidence("TERMS", "contract", {"review_hours": 40, "rate": 150})],
        policy_value=policy(repo_root),
    )["TERMS"]
    assert decision.verdict == Verdict.NARROW
    assert decision.approved_value["liability_multiple"] == 2


def test_terms_approves_a_request_inside_policy(repo_root: Path) -> None:
    decision = run(
        [request("TERMS", "terms", {"liability_multiple": 2})],
        [evidence("TERMS", "contract", {"review_hours": 40, "rate": 150})],
        policy_value=policy(repo_root),
    )["TERMS"]
    assert decision.verdict == Verdict.APPROVE
    assert decision.amount == 6000.0


def test_terms_without_contract_evidence_escalates(repo_root: Path) -> None:
    decision = run(
        [request("TERMS", "terms", {"unlimited_liability": True})],
        [],
        policy_value=policy(repo_root),
    )["TERMS"]
    assert decision.verdict == Verdict.ESCALATE
    assert decision.amount is None


# ----------------------------------------------------------------- aggregate
def test_the_northwind_fixture_prices_v1_at_38400(repo_root: Path) -> None:
    from scopeguard.fixtures import load_engagement, load_evidence

    engagement = load_engagement(repo_root / "fixtures" / "northwind" / "engagement.yaml")
    book = load_evidence(repo_root / "fixtures" / "northwind" / "evidence.yaml", engagement)
    decisions = assess_engagement(
        engagement=engagement,
        evidence=book,
        policy=policy(repo_root),
        as_of=date(2026, 11, 4),
    )
    by_id = {item.request_id: item for item in decisions}
    assert by_id["MOBILE"].verdict == Verdict.NARROW
    assert by_id["MOBILE"].amount == 30000.0
    assert by_id["RUSH"].verdict == Verdict.NARROW
    assert by_id["RUSH"].amount == 12000.0
    assert by_id["RATE"].verdict == Verdict.APPROVE
    assert by_id["RATE"].amount == -9600.0
    assert by_id["TERMS"].verdict == Verdict.NARROW
    assert by_id["TERMS"].amount == 6000.0
    subtotal = sum(item.amount or 0 for item in decisions if (item.amount or 0) > 0)
    total = sum(item.amount or 0 for item in decisions)
    assert subtotal == 48000.0
    assert total == 38400.0
    assert len(book.items) == 4


def test_pricing_is_deterministic(repo_root: Path) -> None:
    from scopeguard.fixtures import load_engagement, load_evidence

    engagement = load_engagement(repo_root / "fixtures" / "northwind" / "engagement.yaml")
    book = load_evidence(repo_root / "fixtures" / "northwind" / "evidence.yaml", engagement)
    value = policy(repo_root)
    first = assess_engagement(engagement=engagement, evidence=book, policy=value)
    second = assess_engagement(engagement=engagement, evidence=book, policy=value)
    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]


def test_rate_is_computed_after_all_positive_line_items_even_if_declared_first(
    repo_root: Path,
) -> None:
    decisions = run(
        [
            request("RATE", "rate", {"discount_pct": 10}),
            request("TERMS", "terms", {"liability_multiple": 1}),
        ],
        [
            evidence("RATE", "rate_card", {"hourly_rate": 150}),
            evidence("TERMS", "contract", {"review_hours": 40, "rate": 150}),
        ],
        policy_value=policy(repo_root),
    )
    assert decisions["RATE"].approved_value["base_amount"] == 6000.0
    assert decisions["RATE"].amount == -600.0


@pytest.mark.parametrize(
    ("cost_ratio", "discount", "margin"),
    [(58, 12, 34.09), (58, 20, 27.5), (40, 20, 50.0)],
)
def test_rate_margin_is_computed_from_the_engagement_cost_ratio(
    repo_root: Path, cost_ratio: float, discount: float, margin: float
) -> None:
    decisions = run(
        [
            request("MOBILE", "new_work", {"ga_date": "2026-12-15"}),
            request("RATE", "rate", {"discount_pct": discount}),
        ],
        [
            evidence("MOBILE", "estimate", {"beta_hours": 200, "rate": 150}),
            evidence("RATE", "rate_card", {"hourly_rate": 150}),
        ],
        policy_value=policy(repo_root),
        cost_ratio=cost_ratio,
    )
    assert decisions["RATE"].approved_value["margin_pct"] == margin


def test_new_work_type_enum_is_accepted_from_the_model(repo_root: Path) -> None:
    decision = run(
        [request("MOBILE", RequestType.NEW_WORK.value, {"ga_date": "2026-12-31"})],
        [evidence("MOBILE", "estimate", {"hours": 100, "rate": 150, "ga_date": "2026-12-01"})],
        policy_value=policy(repo_root),
    )["MOBILE"]
    assert decision.verdict == Verdict.APPROVE
