"""The Red Team: adversarial findings, policy remediation, and the human gate."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from scopeguard.assess import assess_engagement
from scopeguard.challenge import apply_revisions, challenge_packet, gate_packet
from scopeguard.models import (
    Approval,
    Approvers,
    Decision,
    Engagement,
    Evidence,
    EvidenceBook,
    Finding,
    Packet,
    PacketStatus,
    RequestType,
    Severity,
    Verdict,
)
from scopeguard.policy import Policy
from scopeguard.receipt import expected_phrase, packet_hash

MOMENT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
AS_OF = date(2026, 11, 4)


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


def engagement(requests: list[dict[str, Any]], *, cost_ratio: float = 58) -> Engagement:
    return Engagement(
        id="test-2026",
        client="Test Client",
        requested_by="Dana",
        contract_value=120000,
        delivery_cost_ratio_pct=cost_ratio,
        requests=requests,
    )


def make_packet(
    model: Engagement,
    decisions: list[Decision],
    *,
    status: str = PacketStatus.DRAFT.value,
    findings: list[Finding] | None = None,
    version: int = 1,
) -> Packet:
    packet = Packet(
        engagement_id=model.id,
        version=version,
        client=model.client,
        currency=model.currency,
        decisions=decisions,
        findings=findings or [],
        status=status,
        packet_hash="",
        created_at=MOMENT,
    )
    packet.packet_hash = packet_hash(packet.body())
    return packet


def priced_packet(
    model: Engagement,
    book: EvidenceBook,
    value: Policy,
) -> Packet:
    decisions = assess_engagement(engagement=model, evidence=book, policy=value, as_of=AS_OF)
    return make_packet(model, decisions, status=PacketStatus.CHALLENGED.value)


def findings_for(
    model: Engagement, book: EvidenceBook, value: Policy, packet: Packet
) -> list[Finding]:
    return challenge_packet(
        packet=packet, engagement=model, evidence=book, policy=value, as_of=AS_OF
    )


def northwind(repo_root: Path) -> tuple[Engagement, EvidenceBook, Policy]:
    from scopeguard.fixtures import load_engagement, load_evidence

    model = load_engagement(repo_root / "fixtures" / "northwind" / "engagement.yaml")
    book = load_evidence(repo_root / "fixtures" / "northwind" / "evidence.yaml", model)
    return model, book, policy(repo_root)


def decision(
    request_id: str,
    *,
    verdict: str = Verdict.NARROW.value,
    amount: float | None = 1000.0,
    kind: str = "MOBILE",
    approved: dict[str, Any] | None = None,
    conditions: list[str] | None = None,
    priced_text: str = "Narrow commitment.",
    evidence_ids: list[str] | None = None,
    required_roles: list[str] | None = None,
) -> Decision:
    return Decision(
        request_id=request_id,
        verdict=verdict,
        rationale="because",
        priced_text=priced_text,
        amount=amount,
        approved_value=approved or {"kind": kind},
        conditions=conditions if conditions is not None else ["A condition."],
        evidence_ids=evidence_ids or [f"EV-{request_id}-1"],
        required_roles=required_roles or ["ACCOUNT_LEAD"],
        policy_refs=["policy.test"],
    )


def codes(findings: list[Finding]) -> list[str]:
    return [item.code for item in findings]


# -------------------------------------------------------------------- baseline
def test_v1_is_blocked_on_rate_autonomy_and_margin_floor(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    findings = findings_for(model, book, value, packet)
    assert "rate_autonomy" in codes(findings)
    assert "margin_floor" in codes(findings)
    assert packet.has_blocks() is False  # findings are not attached to the packet yet
    assert all(item.severity == Severity.BLOCK.value for item in findings)


def test_a_revised_packet_passes_the_red_team(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    first = findings_for(model, book, value, packet)
    revised = apply_revisions(packet=packet, engagement=model, policy=value, findings=first)
    revised_packet = make_packet(model, revised, status=PacketStatus.CHALLENGED.value, version=2)
    second = findings_for(model, book, value, revised_packet)
    assert [item for item in second if item.severity == Severity.BLOCK.value] == []
    rate = revised_packet.decision("RATE")
    assert rate.approved_value["discount_pct"] == 12
    assert rate.amount == -5760.0


def test_findings_are_ordered_by_severity_and_numbered(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-11-01"})])
    book = EvidenceBook(engagement_id=model.id, items=[])
    packet = make_packet(model, [decision("MOBILE", amount=None, evidence_ids=[])])
    findings = findings_for(model, book, policy(repo_root), packet)
    ids = [item.id for item in findings]
    assert ids == sorted(ids)
    assert ids[0] == "RT-001"
    ranks = [Severity(item.severity).rank for item in findings]
    assert ranks == sorted(ranks)


# -------------------------------------------------------------------- evidence
def test_stale_evidence_blocks_the_packet(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence(
                "MOBILE",
                "estimate",
                {"hours": 100, "rate": 150, "ga_date": "2026-11-30"},
                observed_at=date(2026, 1, 1),
            )
        ],
    )
    packet = priced_packet(model, book, policy(repo_root))
    findings = findings_for(model, book, policy(repo_root), packet)
    assert "evidence_stale" in codes(findings)
    stale = next(item for item in findings if item.code == "evidence_stale")
    assert stale.remediation == {"kind": "refresh_evidence", "evidence_id": "EV-MOBILE-1"}


def test_expired_evidence_blocks_the_packet(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence(
                "MOBILE",
                "estimate",
                {"hours": 100, "rate": 150, "ga_date": "2026-11-30"},
                valid_until=date(2026, 10, 1),
            )
        ],
    )
    packet = priced_packet(model, book, policy(repo_root))
    assert "evidence_expired" in codes(findings_for(model, book, policy(repo_root), packet))


def test_a_commitment_without_evidence_blocks(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(engagement_id=model.id, items=[])
    packet = make_packet(model, [decision("MOBILE", evidence_ids=[])])
    assert "commitment_without_evidence" in codes(
        findings_for(model, book, policy(repo_root), packet)
    )


# -------------------------------------------------------------------- coverage
def test_an_unpriced_request_blocks(repo_root: Path) -> None:
    model = engagement(
        [
            request("MOBILE", "new_work", {"ga_date": "2026-12-31"}),
            request("TERMS", "terms", {"liability_multiple": 1}),
        ]
    )
    book = EvidenceBook(
        engagement_id=model.id,
        items=[evidence("MOBILE", "estimate", {"hours": 10, "rate": 150, "ga_date": "2026-11-30"})],
    )
    packet = make_packet(model, [decision("MOBILE")])
    findings = findings_for(model, book, policy(repo_root), packet)
    assert "request_unpriced" in codes(findings)


def test_an_empty_packet_blocks(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work")])
    packet = make_packet(model, [])
    assert "empty_packet" in codes(
        findings_for(model, EvidenceBook(engagement_id=model.id), policy(repo_root), packet)
    )


def test_an_approval_matrix_mismatch_blocks(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence("MOBILE", "estimate", {"hours": 100, "rate": 150, "ga_date": "2026-11-30"})
        ],
    )
    packet = make_packet(model, [decision("MOBILE", required_roles=["NOPE"])])
    assert "approval_matrix_mismatch" in codes(findings_for(model, book, policy(repo_root), packet))


def test_an_unpriced_commitment_blocks(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence("MOBILE", "estimate", {"hours": 100, "rate": 150, "ga_date": "2026-11-30"})
        ],
    )
    packet = make_packet(model, [decision("MOBILE", amount=None)])
    assert "unpriced_commitment" in codes(findings_for(model, book, policy(repo_root), packet))


def test_missing_conditions_warn_but_do_not_block(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence("MOBILE", "estimate", {"hours": 100, "rate": 150, "ga_date": "2026-11-30"})
        ],
    )
    packet = make_packet(
        model,
        [
            decision(
                "MOBILE",
                conditions=[],
                required_roles=["ACCOUNT_LEAD", "DELIVERY_LEAD"],
            )
        ],
    )
    findings = findings_for(model, book, policy(repo_root), packet)
    assert "commitment_without_conditions" in codes(findings)
    assert all(item.severity == Severity.WARN.value for item in findings)


def test_a_negative_amount_outside_the_rate_line_blocks(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence("MOBILE", "estimate", {"hours": 100, "rate": 150, "ga_date": "2026-11-30"})
        ],
    )
    packet = make_packet(model, [decision("MOBILE", amount=-100.0)])
    assert "unexpected_credit" in codes(findings_for(model, book, policy(repo_root), packet))


# -------------------------------------------------------------------- new work
def test_a_beta_commitment_that_overclaims_ga_blocks(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence("MOBILE", "estimate", {"hours": 100, "rate": 150, "ga_date": "2026-11-30"})
        ],
    )
    packet = make_packet(
        model,
        [
            decision(
                "MOBILE",
                kind="beta",
                approved={"kind": "beta", "beta_date": "2026-12-01", "ga_quarter": "Q1 2027"},
                priced_text="Native mobile app, generally available 2026-12-15.",
                conditions=["A condition."],
            )
        ],
    )
    assert "new_work_overclaim" in codes(findings_for(model, book, policy(repo_root), packet))


def test_beta_without_a_beta_condition_warns(repo_root: Path) -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    book = EvidenceBook(
        engagement_id=model.id,
        items=[
            evidence("MOBILE", "estimate", {"hours": 100, "rate": 150, "ga_date": "2026-11-30"})
        ],
    )
    packet = make_packet(
        model,
        [
            decision(
                "MOBILE",
                kind="beta",
                approved={"kind": "beta"},
                priced_text="Beta by 2026-12-01; GA targeted Q1 2027, not included.",
                conditions=["A condition."],
            )
        ],
    )
    assert "beta_missing_condition" in codes(findings_for(model, book, policy(repo_root), packet))


# --------------------------------------------------------------------- rate
def test_rate_over_the_headline_ceiling_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    rate = packet.decision("RATE")
    rate.approved_value["discount_pct"] = 25
    rate.amount = -12000.0
    packet.packet_hash = packet_hash(packet.body())
    assert "rate_over_policy" in codes(findings_for(model, book, value, packet))


def test_rate_base_mismatch_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("RATE").approved_value["base_amount"] = 1.0
    packet.packet_hash = packet_hash(packet.body())
    assert "base_amount_mismatch" in codes(findings_for(model, book, value, packet))


def test_rate_amount_mismatch_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("RATE").amount = -1.0
    packet.packet_hash = packet_hash(packet.body())
    assert "rate_amount_mismatch" in codes(findings_for(model, book, value, packet))


# ------------------------------------------------------------------ timeline
def test_timeline_over_the_policy_cap_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("RUSH").approved_value["weeks_saved"] = 5
    packet.packet_hash = packet_hash(packet.body())
    assert "timeline_over_policy" in codes(findings_for(model, book, value, packet))


def test_timeline_confidence_below_floor_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("RUSH").approved_value["confidence_pct"] = 50
    packet.packet_hash = packet_hash(packet.body())
    assert "timeline_confidence" in codes(findings_for(model, book, value, packet))


def test_acceleration_without_a_positive_rush_line_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("RUSH").amount = 0.0
    packet.packet_hash = packet_hash(packet.body())
    assert "rush_not_priced" in codes(findings_for(model, book, value, packet))


# --------------------------------------------------------------------- terms
def test_terms_above_the_liability_cap_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("TERMS").approved_value["liability_multiple"] = 5
    packet.packet_hash = packet_hash(packet.body())
    assert "terms_over_policy" in codes(findings_for(model, book, value, packet))


def test_unlimited_liability_language_without_refusal_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("TERMS").priced_text = "Unlimited liability is accepted."
    packet.packet_hash = packet_hash(packet.body())
    assert "terms_unlimited_language" in codes(findings_for(model, book, value, packet))


def test_missing_legal_review_blocks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    packet.decision("TERMS").conditions = ["Something else."]
    packet.packet_hash = packet_hash(packet.body())
    assert "terms_missing_legal_review" in codes(findings_for(model, book, value, packet))


# ---------------------------------------------------------------- remediation
def test_narrow_rate_remediation_rewrites_amount_condition_and_rationale(
    repo_root: Path,
) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    findings = findings_for(model, book, value, packet)
    revised = apply_revisions(packet=packet, engagement=model, policy=value, findings=findings)
    rate = next(item for item in revised if item.request_id == "RATE")
    assert rate.verdict == Verdict.NARROW
    assert rate.approved_value["discount_pct"] == 12
    assert rate.amount == -5760.0
    assert rate.approved_value["margin_pct"] == 34.09
    assert any("12.00%" in condition for condition in rate.conditions)
    assert "Revised after Red Team finding" in rate.rationale


def test_recompute_rate_remediation_reprices_the_line(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    finding = Finding(
        id="RT-999",
        severity=Severity.BLOCK,
        request_id="RATE",
        code="rate_amount_mismatch",
        message="wrong",
        remediation={"kind": "recompute_rate"},
    )
    revised = apply_revisions(packet=packet, engagement=model, policy=value, findings=[finding])
    rate = next(item for item in revised if item.request_id == "RATE")
    assert rate.amount == -9600.0
    assert rate.approved_value["base_amount"] == 48000.0


def test_narrow_timeline_remediation_rewrites_weeks(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    finding = Finding(
        id="RT-999",
        severity=Severity.BLOCK,
        request_id="RUSH",
        code="timeline_over_policy",
        message="too many weeks",
        remediation={"kind": "narrow_timeline", "value": 1},
    )
    revised = apply_revisions(packet=packet, engagement=model, policy=value, findings=[finding])
    rush = next(item for item in revised if item.request_id == "RUSH")
    assert rush.verdict == Verdict.NARROW
    assert rush.approved_value["weeks_saved"] == 1
    assert "1.0 week(s)" in rush.priced_text


def test_narrow_terms_remediation_refuses_unlimited_liability(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    finding = Finding(
        id="RT-999",
        severity=Severity.BLOCK,
        request_id="TERMS",
        code="terms_over_policy",
        message="too much liability",
        remediation={"kind": "narrow_terms"},
    )
    revised = apply_revisions(packet=packet, engagement=model, policy=value, findings=[finding])
    terms = next(item for item in revised if item.request_id == "TERMS")
    assert terms.approved_value["liability_multiple"] == 2
    assert terms.approved_value["unlimited_liability"] is False
    assert "unlimited liability is refused" in terms.priced_text


def test_reword_new_work_remediation_forces_beta_language(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    finding = Finding(
        id="RT-999",
        severity=Severity.BLOCK,
        request_id="MOBILE",
        code="new_work_overclaim",
        message="overclaim",
        remediation={"kind": "reword_new_work"},
    )
    revised = apply_revisions(packet=packet, engagement=model, policy=value, findings=[finding])
    mobile = next(item for item in revised if item.request_id == "MOBILE")
    assert mobile.verdict == Verdict.NARROW
    assert mobile.approved_value["ga_committed"] is False
    assert "not included" in mobile.priced_text
    assert any("say beta" in condition for condition in mobile.conditions)


def test_add_condition_remediation_is_idempotent(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    finding = Finding(
        id="RT-999",
        severity=Severity.BLOCK,
        request_id="TERMS",
        code="terms_missing_legal_review",
        message="missing review",
        remediation={
            "kind": "add_condition",
            "condition": "Legal review is required before signature.",
        },
    )
    first = apply_revisions(packet=packet, engagement=model, policy=value, findings=[finding])
    second = apply_revisions(
        packet=packet, engagement=model, policy=value, findings=[finding, finding]
    )
    terms = next(item for item in first if item.request_id == "TERMS")
    assert terms.conditions.count("Legal review is required before signature.") == 1
    assert second == first


def test_warn_findings_are_not_auto_remediated(repo_root: Path) -> None:
    model, book, value = northwind(repo_root)
    packet = priced_packet(model, book, value)
    finding = Finding(
        id="RT-999",
        severity=Severity.WARN,
        request_id="RATE",
        code="something",
        message="warn only",
        remediation={"kind": "narrow_rate", "value": 1},
    )
    revised = apply_revisions(packet=packet, engagement=model, policy=value, findings=[finding])
    assert [item.model_dump() for item in revised] == [
        item.model_dump() for item in packet.decisions
    ]


# --------------------------------------------------------------------- gate
def approvers() -> Approvers:
    return Approvers(
        engagement_id="test-2026",
        roles={"ACCOUNT_LEAD": ["Dana"], "DELIVERY_LEAD": ["Marco"]},
    )


def clean_packet() -> Packet:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    item = decision("MOBILE", required_roles=["ACCOUNT_LEAD", "DELIVERY_LEAD"])
    return make_packet(model, [item], status=PacketStatus.CHALLENGED.value)


def approval(
    packet: Packet,
    role: str,
    actor: str,
    *,
    request_id: str = "MOBILE",
    phrase: str | None = None,
    status: str = "valid",
) -> Approval:
    return Approval(
        id=f"apr_{role}_{actor}",
        engagement_id=packet.engagement_id,
        packet_id=f"{packet.engagement_id}-v{packet.version}",
        packet_hash=packet.packet_hash,
        request_id=request_id,
        role=role,
        actor=actor,
        phrase=phrase or expected_phrase(packet.engagement_id, request_id, role),
        created_at=MOMENT,
        status=status,
    )


def gate(packet: Packet, approvals: list[Approval]) -> Any:
    return gate_packet(
        packet=packet,
        approvals=approvals,
        approvers=approvers(),
        policy=policy(Path(__file__).resolve().parents[1]),
        engagement=engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})]),
    )


def test_gate_is_closed_until_every_required_role_signs() -> None:
    packet = clean_packet()
    result = gate(packet, [approval(packet, "ACCOUNT_LEAD", "Dana")])
    assert result.ok is False
    assert result.pending == ["MOBILE DELIVERY_LEAD"]
    assert "signatures" in [item.code for item in result.failures]


def test_gate_opens_when_every_required_role_signs_the_same_hash() -> None:
    packet = clean_packet()
    result = gate(
        packet,
        [
            approval(packet, "ACCOUNT_LEAD", "Dana"),
            approval(packet, "DELIVERY_LEAD", "Marco"),
        ],
    )
    assert result.ok is True
    assert result.pending == []
    assert all(item.ok for item in result.checks)


def test_gate_rejects_a_draft_packet_even_with_signatures() -> None:
    packet = clean_packet()
    packet.status = PacketStatus.DRAFT.value
    result = gate(
        packet,
        [
            approval(packet, "ACCOUNT_LEAD", "Dana"),
            approval(packet, "DELIVERY_LEAD", "Marco"),
        ],
    )
    assert result.ok is False
    assert next(item for item in result.failures if item.code == "status")


def test_gate_rejects_blocking_findings() -> None:
    packet = clean_packet()
    packet.findings = [
        Finding(
            id="RT-001",
            severity=Severity.BLOCK,
            request_id="MOBILE",
            code="rate_autonomy",
            message="blocked",
        )
    ]
    result = gate(
        packet,
        [
            approval(packet, "ACCOUNT_LEAD", "Dana"),
            approval(packet, "DELIVERY_LEAD", "Marco"),
        ],
    )
    assert result.ok is False
    assert next(item for item in result.failures if item.code == "no_blocking_findings")


def test_stale_signatures_never_count_toward_the_gate() -> None:
    packet = clean_packet()
    result = gate(
        packet,
        [
            approval(packet, "ACCOUNT_LEAD", "Dana", status="stale"),
            approval(packet, "DELIVERY_LEAD", "Marco", status="stale"),
        ],
    )
    assert result.ok is False
    assert len(result.pending) == 2
    assert (
        "2 stale signature(s) ignored"
        in next(item for item in result.checks if item.code == "stale_signatures_ignored").detail
    )


def test_a_signature_bound_to_another_hash_is_ignored() -> None:
    packet = clean_packet()
    stale = approval(packet, "ACCOUNT_LEAD", "Dana")
    stale.packet_hash = "another-hash"
    result = gate(packet, [stale, approval(packet, "DELIVERY_LEAD", "Marco")])
    assert result.ok is False
    assert result.pending == ["MOBILE ACCOUNT_LEAD"]


def test_a_person_who_does_not_hold_the_role_fails_the_gate() -> None:
    packet = clean_packet()
    result = gate(
        packet,
        [
            approval(packet, "ACCOUNT_LEAD", "Marco"),
            approval(packet, "DELIVERY_LEAD", "Dana"),
        ],
    )
    assert result.ok is False
    assert any(item.code.startswith("actor_role:") for item in result.failures)


def test_a_wrong_signature_phrase_fails_the_gate() -> None:
    packet = clean_packet()
    result = gate(
        packet,
        [
            approval(packet, "ACCOUNT_LEAD", "Dana", phrase="SIGN WRONG MOBILE AS ACCOUNT_LEAD"),
            approval(packet, "DELIVERY_LEAD", "Marco"),
        ],
    )
    assert result.ok is False
    assert any(item.code.startswith("phrase:") for item in result.failures)


def test_gate_reports_unpriced_commitments() -> None:
    model = engagement([request("MOBILE", "new_work", {"ga_date": "2026-12-31"})])
    packet = make_packet(
        model,
        [decision("MOBILE", amount=None, required_roles=["ACCOUNT_LEAD"])],
        status=PacketStatus.CHALLENGED.value,
    )
    result = gate_packet(
        packet=packet,
        approvals=[approval(packet, "ACCOUNT_LEAD", "Dana")],
        approvers=approvers(),
        policy=policy(Path(__file__).resolve().parents[1]),
        engagement=model,
    )
    assert result.ok is False
    assert next(item for item in result.failures if item.code == "every_line_item_priced")


def test_gate_signature_count_is_informational_and_grows() -> None:
    packet = clean_packet()
    result = gate(packet, [approval(packet, "ACCOUNT_LEAD", "Dana")])
    check = next(item for item in result.checks if item.code == "signature_count")
    assert check.ok is True
    assert "1 distinct request/role" in check.detail


def test_non_commitment_decisions_do_not_require_signatures() -> None:
    model = engagement(
        [
            request("MOBILE", "new_work", {"ga_date": "2026-12-31"}),
            request("RATE", "rate", {"discount_pct": 10}),
        ]
    )
    packet = make_packet(
        model,
        [
            decision("MOBILE", required_roles=["ACCOUNT_LEAD"]),
            decision(
                "RATE",
                verdict=Verdict.ESCALATE.value,
                amount=None,
                required_roles=["ACCOUNT_LEAD", "FINANCE"],
                conditions=["Attach evidence."],
            ),
        ],
        status=PacketStatus.CHALLENGED.value,
    )
    result = gate_packet(
        packet=packet,
        approvals=[approval(packet, "ACCOUNT_LEAD", "Dana")],
        approvers=approvers(),
        policy=policy(Path(__file__).resolve().parents[1]),
        engagement=model,
    )
    assert result.ok is True
    assert result.pending == []


def test_request_type_values_are_used_consistently() -> None:
    assert RequestType.RATE.value == "rate"
    assert datetime(2026, 11, 4, tzinfo=UTC).date() == date(2026, 11, 4)
