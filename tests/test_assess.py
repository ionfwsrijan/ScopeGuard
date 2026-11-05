"""The Actuary: deterministic, evidence-bound decisions."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from vouch.assess import assess_deal
from vouch.models import (
    Claim,
    ClaimType,
    Deal,
    Evidence,
    EvidenceBook,
    Verdict,
)
from vouch.policy import Policy


def policy(repo_root: Path) -> Policy:
    return Policy.load(repo_root / "policies" / "deal_policy_v1.yaml")


def claim(
    claim_id: str,
    claim_type: ClaimType,
    requested: dict[str, Any] | None = None,
    *,
    due: date | None = date(2026, 12, 31),
    title: str | None = None,
) -> Claim:
    return Claim(
        id=claim_id,
        type=claim_type,
        title=title or claim_id,
        requested_text=f"{claim_id} requested.",
        requested_value=requested or {},
        due=due,
    )


def evidence(
    claim_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    observed_at: date = date(2026, 10, 24),
) -> Evidence:
    return Evidence(
        id=f"EV-{claim_id}-1",
        claim_id=claim_id,
        kind=kind,
        source="test source",
        observed_at=observed_at,
        summary="test evidence",
        payload=payload,
    )


def run(
    policy_value: Policy,
    claims: list[Claim],
    items: list[Evidence],
) -> dict[str, Any]:
    deal = Deal(
        id="acme-2026",
        account="Acme Corp",
        requested_by="Priya",
        annual_list_price=240000,
        cost_ratio_pct=55,
        claims=claims,
    )
    decisions = assess_deal(
        deal=deal,
        evidence=EvidenceBook(deal_id=deal.id, items=items),
        policy=policy_value,
        as_of=date(2026, 11, 4),
    )
    assert [item.claim_id for item in decisions] == [item.id for item in claims]
    return {item.claim_id: item for item in decisions}


# --------------------------------------------------------------------- feature
def test_feature_approves_when_roadmap_meets_requested_ga(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"})],
        [evidence("SSO", "roadmap", {"ga_date": "2026-11-30"})],
    )["SSO"]
    assert decision.verdict == Verdict.APPROVE
    assert "2026-12-31" in decision.approved_text
    assert decision.evidence_ids == ["EV-SSO-1"]


def test_feature_narrows_to_beta_when_ga_is_later(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"}, title="SSO")],
        [
            evidence(
                "SSO",
                "roadmap",
                {"beta_date": "2026-12-15", "ga_date": "2027-03-31", "ga_quarter": "Q1 2027"},
            )
        ],
    )["SSO"]
    assert decision.verdict == Verdict.NARROW
    assert "beta available by 2026-12-15" in decision.approved_text
    assert "Q1 2027" in decision.approved_text
    assert decision.approved_value["ga_committed"] is False
    assert any("beta" in item for item in decision.conditions)


def test_feature_derives_quarter_when_payload_omits_it(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"})],
        [evidence("SSO", "roadmap", {"beta_date": "2026-12-15", "ga_date": "2027-02-15"})],
    )["SSO"]
    assert decision.approved_value["ga_quarter"] == "Q1 2027"


def test_feature_refuses_beta_when_policy_disallows_it(repo_root: Path) -> None:
    base = policy(repo_root)
    modified = base.model_copy(
        update={"feature": base.feature.model_copy(update={"allow_beta": False})}
    )
    decision = run(
        modified,
        [claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"})],
        [evidence("SSO", "roadmap", {"beta_date": "2026-12-15", "ga_date": "2027-03-31"})],
    )["SSO"]
    assert decision.verdict == Verdict.REFUSE
    assert any("allow_beta=false" in ref for ref in decision.policy_refs)


def test_feature_escalates_when_no_date_is_supportable(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"})],
        [evidence("SSO", "roadmap", {})],
    )["SSO"]
    assert decision.verdict == Verdict.ESCALATE
    assert "product-owned target" in decision.approved_text.lower()


def test_feature_refuses_unsupportable_roadmap_when_disabled(repo_root: Path) -> None:
    base = policy(repo_root)
    modified = base.model_copy(
        update={"feature": base.feature.model_copy(update={"allow_roadmap": False})}
    )
    decision = run(
        modified,
        [claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"})],
        [evidence("SSO", "roadmap", {})],
    )["SSO"]
    assert decision.verdict == Verdict.REFUSE


def test_feature_without_evidence_escalates(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SSO", ClaimType.FEATURE)],
        [],
    )["SSO"]
    assert decision.verdict == Verdict.ESCALATE
    assert decision.evidence_ids == []
    assert decision.required_roles == []


def test_feature_uses_the_latest_roadmap(repo_root: Path) -> None:
    older = evidence("SSO", "roadmap", {"ga_date": "2027-06-30"}, observed_at=date(2026, 9, 1))
    older = older.model_copy(update={"id": "EV-SSO-OLD"})
    newer = evidence("SSO", "roadmap", {"ga_date": "2026-11-30"}, observed_at=date(2026, 10, 24))
    decision = run(
        policy(repo_root),
        [claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"})],
        [older, newer],
    )["SSO"]
    assert decision.verdict == Verdict.APPROVE
    assert decision.evidence_ids == ["EV-SSO-1"]


# ------------------------------------------------------------------------- SLA
def test_sla_approves_a_supportable_request(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SLA", ClaimType.SLA, {"uptime_pct": 99.90})],
        [evidence("SLA", "monitoring", {"uptime_pct": 99.95})],
    )["SLA"]
    assert decision.verdict == Verdict.APPROVE
    assert "99.90%" in decision.approved_text
    assert decision.evidence_ids == ["EV-SLA-1"]


def test_sla_narrows_to_the_standard(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SLA", ClaimType.SLA, {"uptime_pct": 99.95})],
        [evidence("SLA", "monitoring", {"uptime_pct": 99.92})],
    )["SLA"]
    assert decision.verdict == Verdict.NARROW
    assert "99.90%" in decision.approved_text
    assert decision.approved_value["requested_pct"] == 99.95
    assert any("credits" in item for item in decision.conditions)
    assert any("maintenance" in item for item in decision.conditions)


def test_sla_escalates_below_observed_floor(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SLA", ClaimType.SLA, {"uptime_pct": 99.9})],
        [evidence("SLA", "monitoring", {"uptime_pct": 99.8})],
    )["SLA"]
    assert decision.verdict == Verdict.ESCALATE
    assert "below the policy floor" in decision.rationale


def test_sla_uses_policy_standard_when_request_omits_value(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SLA", ClaimType.SLA, {})],
        [evidence("SLA", "monitoring", {"uptime_pct": 99.99})],
    )["SLA"]
    assert decision.verdict == Verdict.APPROVE
    assert "99.90%" in decision.approved_text


def test_sla_without_evidence_escalates(repo_root: Path) -> None:
    decision = run(policy(repo_root), [claim("SLA", ClaimType.SLA)], [])["SLA"]
    assert decision.verdict == Verdict.ESCALATE


# -------------------------------------------------------------------- discount
def test_discount_approves_inside_the_ceiling(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("DISC", ClaimType.DISCOUNT, {"discount_pct": 20, "annual_list_price": 240000})],
        [evidence("DISC", "pricing", {"annual_list_price": 240000})],
    )["DISC"]
    assert decision.verdict == Verdict.APPROVE
    assert "20.00%" in decision.approved_text
    assert decision.approved_value["discount_pct"] == 20
    assert decision.approved_value["net_price_pct"] == 80.0
    assert decision.evidence_ids == ["EV-DISC-1"]


def test_discount_narrows_above_the_ceiling(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("DISC", ClaimType.DISCOUNT, {"discount_pct": 25, "annual_list_price": 240000})],
        [evidence("DISC", "pricing", {"annual_list_price": 240000})],
    )["DISC"]
    assert decision.verdict == Verdict.NARROW
    assert "20.00%" in decision.approved_text
    assert "policy.discount.over_cap" in decision.policy_refs


def test_discount_without_evidence_escalates(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("DISC", ClaimType.DISCOUNT, {"discount_pct": 20})],
        [],
    )["DISC"]
    assert decision.verdict == Verdict.ESCALATE


def test_zero_discount_needs_no_pricing_evidence(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("DISC", ClaimType.DISCOUNT, {"discount_pct": 0, "annual_list_price": 240000})],
        [],
    )["DISC"]
    assert decision.verdict == Verdict.APPROVE
    assert decision.evidence_ids == []


def test_discount_conditions_are_always_present(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("DISC", ClaimType.DISCOUNT, {"discount_pct": 10, "annual_list_price": 240000})],
        [evidence("DISC", "pricing", {})],
    )["DISC"]
    assert "Quote valid for 30 days." in decision.conditions


# -------------------------------------------------------------------- security
def test_security_approves_certification(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SOC2", ClaimType.SECURITY, {"status": "certified"})],
        [evidence("SOC2", "security_report", {"status": "certified", "report_date": "2026-10-01"})],
    )["SOC2"]
    assert decision.verdict == Verdict.APPROVE
    assert "SOC2 requested." in decision.approved_text


def test_security_narrows_to_in_progress(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SOC2", ClaimType.SECURITY, {"status": "certified"}, title="SOC 2 Type II")],
        [
            evidence(
                "SOC2",
                "security_report",
                {"status": "in_progress", "report_date": "2027-01-15"},
            )
        ],
    )["SOC2"]
    assert decision.verdict == Verdict.NARROW
    assert "in progress" in decision.approved_text
    assert "2027-01-15" in decision.approved_text
    assert "Not certified." in decision.approved_text
    assert any("Do not describe" in item for item in decision.conditions)


def test_security_escalates_when_in_progress_has_no_report_date(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SOC2", ClaimType.SECURITY, {"status": "certified"})],
        [evidence("SOC2", "security_report", {"status": "in_progress"})],
    )["SOC2"]
    assert decision.verdict == Verdict.ESCALATE
    assert "report date" in decision.rationale.lower()


def test_security_rejects_unknown_status(repo_root: Path) -> None:
    decision = run(
        policy(repo_root),
        [claim("SOC2", ClaimType.SECURITY, {"status": "certified"})],
        [evidence("SOC2", "security_report", {"status": "self-attested"})],
    )["SOC2"]
    assert decision.verdict == Verdict.ESCALATE
    assert "not an admissible security posture" in decision.rationale


def test_security_without_evidence_escalates(repo_root: Path) -> None:
    decision = run(policy(repo_root), [claim("SOC2", ClaimType.SECURITY)], [])["SOC2"]
    assert decision.verdict == Verdict.ESCALATE


# --------------------------------------------------------------------- general
def test_assessment_is_deterministic(repo_root: Path) -> None:
    claims = [
        claim("SSO", ClaimType.FEATURE, {"ga_date": "2026-12-31"}),
        claim("SLA", ClaimType.SLA, {"uptime_pct": 99.95}),
        claim("DISC", ClaimType.DISCOUNT, {"discount_pct": 20, "annual_list_price": 240000}),
        claim("SOC2", ClaimType.SECURITY, {"status": "certified"}),
    ]
    items = [
        evidence("SSO", "roadmap", {"beta_date": "2026-12-15", "ga_date": "2027-03-31"}),
        evidence("SLA", "monitoring", {"uptime_pct": 99.92}),
        evidence("DISC", "pricing", {"annual_list_price": 240000}),
        evidence("SOC2", "security_report", {"status": "in_progress", "report_date": "2027-01-15"}),
    ]
    first = run(policy(repo_root), claims, items)
    second = run(policy(repo_root), claims, items)
    assert {key: value.model_dump() for key, value in first.items()} == {
        key: value.model_dump() for key, value in second.items()
    }


def test_policy_roles_are_attached_to_every_assessed_claim(repo_root: Path) -> None:
    decisions = run(
        policy(repo_root),
        [
            claim("SSO", ClaimType.FEATURE),
            claim("DISC", ClaimType.DISCOUNT),
        ],
        [],
    )
    assert decisions["SSO"].required_roles == []
    assert decisions["DISC"].required_roles == ["AE", "VP_SALES"]


def test_repository_fixture_matches_the_demo_story(repo_root: Path) -> None:
    from vouch.fixtures import load_deal, load_evidence

    deal = load_deal(repo_root / "fixtures" / "acme" / "request.yaml")
    book = load_evidence(repo_root / "fixtures" / "acme" / "evidence.yaml", deal)
    decisions = {
        item.claim_id: item
        for item in assess_deal(
            deal=deal, evidence=book, policy=policy(repo_root), as_of=date(2026, 11, 4)
        )
    }
    assert decisions["SSO"].verdict == Verdict.NARROW
    assert decisions["SLA"].verdict == Verdict.NARROW
    assert decisions["DISC"].verdict == Verdict.APPROVE
    assert decisions["SOC2"].verdict == Verdict.NARROW
