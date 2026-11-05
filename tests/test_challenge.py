"""The Examiner and the human gate: adversarial checks and exact consent."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from conftest import DEMO_DATE, DEMO_MOMENT, approve_all, prepare_v2
from vouch.challenge import apply_revisions, challenge_packet
from vouch.fixtures import load_deal, load_evidence
from vouch.models import (
    Approval,
    Decision,
    Finding,
    Packet,
    PacketStatus,
    Severity,
    Verdict,
)
from vouch.policy import Policy
from vouch.receipt import canonical_hash, expected_phrase
from vouch.service import VouchService


def fixture_parts(repo_root: Path) -> tuple[object, object, Policy]:
    deal = load_deal(repo_root / "fixtures" / "acme" / "request.yaml")
    evidence = load_evidence(repo_root / "fixtures" / "acme" / "evidence.yaml", deal)
    policy = Policy.load(repo_root / "policies" / "deal_policy_v1.yaml")
    return deal, evidence, policy


def codes(findings: list[Finding]) -> list[str]:
    return [item.code for item in findings]


# ------------------------------------------------------------------- challenge
def test_clean_v2_packet_has_no_findings(service: VouchService) -> None:
    _, _, packet = prepare_v2(service)
    assert packet.findings == []
    assert packet.status == PacketStatus.CHALLENGED


def test_v1_packet_is_blocked_on_discount_economics(service: VouchService) -> None:
    service.ingest_fixture("acme")
    packet = service.assess("acme-2026")
    findings = challenge_packet(
        packet=packet,
        deal=service.deal("acme-2026"),
        evidence=service.evidence("acme-2026"),
        policy=service.policy,
        as_of=DEMO_DATE,
    )
    assert codes(findings) == ["discount_autonomy", "margin_floor"]
    assert all(item.severity == Severity.BLOCK for item in findings)
    assert {item.claim_id for item in findings} == {"DISC"}


def test_examiner_remediation_values_are_machine_readable(service: VouchService) -> None:
    service.ingest_fixture("acme")
    packet = service.assess("acme-2026")
    findings = challenge_packet(
        packet=packet,
        deal=service.deal("acme-2026"),
        evidence=service.evidence("acme-2026"),
        policy=service.policy,
        as_of=DEMO_DATE,
    )
    by_code = {item.code: item for item in findings}
    assert by_code["discount_autonomy"].remediation == {"kind": "narrow_discount", "value": 15}
    # 55 / (1 - 0.33) = 82.089... revenue, so the margin ceiling is 17.91%.
    assert by_code["margin_floor"].remediation == {"kind": "narrow_discount", "value": 17.91}


def test_stale_evidence_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    decision = Decision(
        claim_id="SSO",
        verdict=Verdict.NARROW,
        rationale="narrow",
        approved_text="Beta.",
        evidence_ids=["EV-SSO-1"],
        required_roles=["AE", "ENG_LEAD"],
        conditions=["Only beta."],
    )
    packet = Packet(
        deal_id=deal.id,
        version=1,
        account=deal.account,
        currency=deal.currency,
        decisions=[
            decision,
            _decision_for(deal, "SLA"),
            _decision_for(deal, "DISC"),
            _decision_for(deal, "SOC2"),
        ],
        packet_hash="0" * 64,
        created_at=DEMO_MOMENT,
    )
    evidence.items[0].observed_at = date(2026, 1, 1)
    evidence.items[0].valid_until = date(2027, 12, 31)
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "evidence_stale" in codes(findings)


def test_expired_evidence_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    evidence.items[0].valid_until = date(2026, 10, 1)
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "evidence_expired" in codes(findings)


def test_commitment_without_evidence_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    packet.decisions[0].evidence_ids = []
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "commitment_without_evidence" in codes(findings)


def test_approval_matrix_mismatch_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    packet.decisions[0].required_roles = ["AE"]
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "approval_matrix_mismatch" in codes(findings)


def test_unassessed_claim_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    packet.decisions = [item for item in packet.decisions if item.claim_id != "SSO"]
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "claim_unassessed" in codes(findings)
    assert "missing_decision" in codes(findings)


def test_empty_packet_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    packet.decisions = []
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "empty_packet" in codes(findings)


def test_security_overclaim_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    soc2 = packet.decision("SOC2")
    soc2.approved_text = "SOC 2 Type II certified."
    soc2.conditions = ["Reference the report date."]
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "security_overclaim" in codes(findings)


def test_security_in_progress_needs_report_caveat(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    soc2 = packet.decision("SOC2")
    soc2.approved_text = "SOC 2 Type II: in progress."
    soc2.conditions = []
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "security_missing_report_caveat" in codes(findings)


def test_commitment_without_conditions_warns(service: VouchService) -> None:
    deal, _, packet = prepare_v2(service)
    security_claims = {claim.id for claim in deal.claims if claim.type == "security"}
    for decision in packet.decisions:
        if decision.claim_id not in security_claims:
            decision.conditions = []
    findings = challenge_packet(
        packet=packet,
        deal=service.deal("acme-2026"),
        evidence=service.evidence("acme-2026"),
        policy=service.policy,
        as_of=DEMO_DATE,
    )
    assert findings
    assert all(item.severity == Severity.WARN for item in findings)
    assert set(codes(findings)) == {"commitment_without_conditions"}


def test_sla_over_policy_blocks(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    sla = packet.decision("SLA")
    sla.verdict = Verdict.APPROVE
    sla.approved_value["uptime_pct"] = 99.99
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    assert "sla_over_policy" in codes(findings)


def test_findings_are_sorted_block_first(repo_root: Path) -> None:
    deal, evidence, policy = fixture_parts(repo_root)
    packet = _packet_from_fixture(repo_root)
    packet.decisions[0].conditions = []
    packet.decisions[0].evidence_ids = []
    findings = challenge_packet(
        packet=packet, deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE
    )
    severities = [item.severity for item in findings]
    assert severities == sorted(
        severities, key=lambda item: {"block": 0, "warn": 1, "note": 2}[item]
    )


# ------------------------------------------------------------------- revisions
def test_revisions_narrow_the_discount_and_append_a_condition(service: VouchService) -> None:
    service.ingest_fixture("acme")
    v1 = service.assess("acme-2026")
    findings = challenge_packet(
        packet=v1,
        deal=service.deal("acme-2026"),
        evidence=service.evidence("acme-2026"),
        policy=service.policy,
        as_of=DEMO_DATE,
    )
    decisions = apply_revisions(
        packet=v1, deal=service.deal("acme-2026"), policy=service.policy, findings=findings
    )
    disc = next(item for item in decisions if item.claim_id == "DISC")
    assert disc.verdict == Verdict.NARROW
    assert disc.approved_value["discount_pct"] == 15
    assert "15.00%" in disc.approved_text
    assert any("CFO exception" in item for item in disc.conditions)
    assert any("Examiner" in disc.rationale for item in [disc.rationale])


def test_revision_narrows_sla() -> None:
    decision = Decision(
        claim_id="SLA",
        verdict=Verdict.APPROVE,
        rationale="approved",
        approved_text="99.99% monthly uptime SLA.",
        approved_value={"uptime_pct": 99.99},
        conditions=["Credits."],
        required_roles=["AE"],
    )
    packet = Packet(
        deal_id="acme-2026",
        version=1,
        account="Acme",
        currency="USD",
        decisions=[decision],
        packet_hash="0" * 64,
        created_at=DEMO_MOMENT,
    )
    deal = _deal_stub()
    finding = Finding(
        id="EX-001",
        severity=Severity.BLOCK,
        claim_id="SLA",
        code="sla_over_policy",
        message="too high",
        remediation={"kind": "narrow_sla", "value": 99.90},
    )
    revised = apply_revisions(packet=packet, deal=deal, policy=_policy_stub(), findings=[finding])
    assert revised[0].verdict == Verdict.NARROW
    assert revised[0].approved_value["uptime_pct"] == 99.9
    assert "99.90%" in revised[0].approved_text
    assert "EX-001" in revised[0].rationale


def test_revision_adds_a_condition() -> None:
    decision = Decision(
        claim_id="SOC2",
        verdict=Verdict.NARROW,
        rationale="narrow",
        approved_text="SOC 2 in progress.",
        conditions=[],
        required_roles=["AE"],
    )
    packet = Packet(
        deal_id="acme-2026",
        version=1,
        account="Acme",
        currency="USD",
        decisions=[decision],
        packet_hash="0" * 64,
        created_at=DEMO_MOMENT,
    )
    finding = Finding(
        id="EX-001",
        severity=Severity.BLOCK,
        claim_id="SOC2",
        code="security_missing_report_caveat",
        message="add caveat",
        remediation={
            "kind": "add_condition",
            "condition": "No certification claim before the report.",
        },
    )
    revised = apply_revisions(
        packet=packet, deal=_deal_stub(), policy=_policy_stub(), findings=[finding]
    )
    assert revised[0].conditions == ["No certification claim before the report."]


def test_revision_reworks_a_security_overclaim() -> None:
    decision = Decision(
        claim_id="SOC2",
        verdict=Verdict.APPROVE,
        rationale="ok",
        approved_text="SOC 2 Type II certified.",
        conditions=["Reference the report date."],
        required_roles=["AE"],
    )
    packet = Packet(
        deal_id="acme-2026",
        version=1,
        account="Acme",
        currency="USD",
        decisions=[decision],
        packet_hash="0" * 64,
        created_at=DEMO_MOMENT,
    )
    finding = Finding(
        id="EX-001",
        severity=Severity.BLOCK,
        claim_id="SOC2",
        code="security_overclaim",
        message="overclaim",
        remediation={"kind": "reword_security"},
    )
    revised = apply_revisions(
        packet=packet, deal=_deal_stub(), policy=_policy_stub(), findings=[finding]
    )
    assert revised[0].verdict == Verdict.NARROW
    assert "in progress" in revised[0].approved_text
    assert "EX-001" in revised[0].rationale


def test_revision_does_not_mutate_the_original_packet(service: VouchService) -> None:
    service.ingest_fixture("acme")
    v1 = service.assess("acme-2026")
    before = canonical_hash([item.model_dump(mode="json") for item in v1.decisions])
    findings = challenge_packet(
        packet=v1,
        deal=service.deal("acme-2026"),
        evidence=service.evidence("acme-2026"),
        policy=service.policy,
        as_of=DEMO_DATE,
    )
    apply_revisions(
        packet=v1, deal=service.deal("acme-2026"), policy=service.policy, findings=findings
    )
    after = canonical_hash([item.model_dump(mode="json") for item in v1.decisions])
    assert before == after


def test_revision_is_a_no_op_without_remediations() -> None:
    decision = Decision(
        claim_id="SSO",
        verdict=Verdict.NARROW,
        rationale="keep",
        approved_text="Beta.",
        conditions=["Beta only."],
        required_roles=["AE"],
    )
    packet = Packet(
        deal_id="acme-2026",
        version=1,
        account="Acme",
        currency="USD",
        decisions=[decision],
        packet_hash="0" * 64,
        created_at=DEMO_MOMENT,
    )
    finding = Finding(
        id="EX-001",
        severity=Severity.BLOCK,
        claim_id="SSO",
        code="manual",
        message="nothing automatic",
    )
    revised = apply_revisions(
        packet=packet, deal=_deal_stub(), policy=_policy_stub(), findings=[finding]
    )
    assert revised[0].model_dump() == decision.model_dump()


# ------------------------------------------------------------------------ gate
def test_gate_requires_a_challenged_packet(service: VouchService) -> None:
    service.ingest_fixture("acme")
    service.assess("acme-2026")
    gate = service.gate("acme-2026")
    statuses = [item.code for item in gate.failures]
    assert "status" in statuses
    assert gate.ok is False


def test_gate_blocks_on_unresolved_findings(service: VouchService) -> None:
    service.ingest_fixture("acme")
    service.assess("acme-2026")
    service.challenge("acme-2026")
    gate = service.gate("acme-2026")
    assert gate.ok is False
    assert "no_blocking_findings" in [item.code for item in gate.failures]


def test_gate_lists_pending_claim_roles(service: VouchService) -> None:
    prepare_v2(service)
    gate = service.gate("acme-2026")
    assert gate.ok is False
    assert set(gate.pending) == {
        "SSO AE",
        "SSO ENG_LEAD",
        "SLA AE",
        "SLA ENG_LEAD",
        "DISC AE",
        "DISC VP_SALES",
        "SOC2 AE",
        "SOC2 LEGAL",
    }


def test_gate_passes_with_eight_exact_approvals(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    gate = service.gate("acme-2026")
    assert gate.ok is True
    assert gate.pending == []
    assert all(item.ok for item in gate.checks)


def test_gate_rejects_an_actor_who_does_not_hold_the_role(service: VouchService) -> None:
    prepare_v2(service)
    packet = service.store.latest_packet("acme-2026")
    approval = Approval(
        id="apr_forged",
        deal_id="acme-2026",
        packet_id=f"acme-2026-v{packet.version}",
        packet_hash=packet.packet_hash,
        claim_id="SSO",
        role="ENG_LEAD",
        actor="Priya",
        phrase=expected_phrase("acme-2026", "SSO", "ENG_LEAD"),
        created_at=DEMO_MOMENT,
    )
    service.store.add_approval(approval)
    gate = service.gate("acme-2026")
    assert gate.ok is False
    assert "SSO ENG_LEAD" in gate.pending
    assert any(item.code == "actor_role:SSO ENG_LEAD" for item in gate.checks if not item.ok)


def test_gate_rejects_a_non_exact_phrase(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    packet = service.store.latest_packet("acme-2026")
    original = next(
        item
        for item in service.store.approvals_for_packet(packet)
        if item.claim_id == "SSO" and item.role == "AE"
    )
    with service.store.conn:
        service.store.conn.execute(
            "UPDATE approvals SET status='stale', invalidated_reason='test' WHERE id=?",
            (original.id,),
        )
    replacement = original.model_copy(
        update={
            "id": "apr_bad_phrase",
            "phrase": "APPROVE ACME-2026 SSO AS AE please",
        }
    )
    service.store.add_approval(replacement)
    gate = service.gate("acme-2026")
    assert gate.ok is False
    assert any(item.code == "phrase:SSO AE" for item in gate.checks if not item.ok)


def test_gate_ignores_stale_approvals(service: VouchService) -> None:
    service.ingest_fixture("acme")
    service.assess("acme-2026")
    service.approve("acme-2026", "DISC", "AE", "Priya")
    service.approve("acme-2026", "DISC", "VP_SALES", "Maya")
    service.challenge("acme-2026")
    service.revise("acme-2026")
    service.challenge("acme-2026")
    gate = service.gate("acme-2026")
    assert not service.store.approvals_for_packet(service.store.latest_packet("acme-2026"))
    assert any(
        item.code == "stale_approvals_ignored" and "2 stale" in item.detail for item in gate.checks
    )
    assert "DISC AE" in gate.pending


def test_gate_is_pure(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    first = service.gate("acme-2026")
    second = service.gate("acme-2026")
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------- helpers
def _packet_from_fixture(repo_root: Path) -> Packet:
    deal = load_deal(repo_root / "fixtures" / "acme" / "request.yaml")
    evidence = load_evidence(repo_root / "fixtures" / "acme" / "evidence.yaml", deal)
    from vouch.assess import assess_deal

    policy = Policy.load(repo_root / "policies" / "deal_policy_v1.yaml")
    decisions = assess_deal(deal=deal, evidence=evidence, policy=policy, as_of=DEMO_DATE)
    return Packet(
        deal_id=deal.id,
        version=1,
        account=deal.account,
        currency=deal.currency,
        decisions=decisions,
        packet_hash="0" * 64,
        created_at=DEMO_MOMENT,
    )


def _decision_for(deal: object, claim_id: str) -> Decision:
    claim = next(item for item in deal.claims if item.id == claim_id)  # type: ignore[attr-defined]
    return Decision(
        claim_id=claim.id,
        verdict=Verdict.NARROW,
        rationale="narrow",
        approved_text=f"{claim.id} narrow.",
        conditions=["A condition."],
        required_roles=["AE"],
    )


def _deal_stub() -> object:
    from vouch.models import Claim, ClaimType, Deal

    return Deal(
        id="acme-2026",
        account="Acme",
        requested_by="Priya",
        annual_list_price=240000,
        cost_ratio_pct=55,
        claims=[
            Claim(
                id="DISC",
                type=ClaimType.DISCOUNT,
                title="discount",
                requested_text="discount",
            )
        ],
    )


def _policy_stub() -> Policy:
    return Policy(
        id="p",
        version=1,
        discount={"max_pct": 20, "autonomy_pct": 15, "margin_floor_pct": 33},
        sla={"max_uptime_pct": 99.95, "standard_uptime_pct": 99.9, "min_observed_uptime_pct": 99.9},
        approval_matrix={
            "feature": ["AE"],
            "sla": ["AE"],
            "discount": ["AE"],
            "security": ["AE"],
        },
    )
