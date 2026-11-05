"""Typed domain model invariants."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from vouch.models import (
    Approval,
    ApprovalStatus,
    Approvers,
    Claim,
    ClaimType,
    Deal,
    Decision,
    Evidence,
    EvidenceBook,
    Finding,
    GateCheck,
    GateResult,
    Packet,
    PacketStatus,
    Receipt,
    ReceiptApproval,
    ReceiptBody,
    ReceiptDecision,
    Severity,
    SwitchMessage,
    Verdict,
)
from vouch.receipt import canonical_hash


def claim(claim_id: str = "SSO", claim_type: ClaimType = ClaimType.FEATURE) -> Claim:
    return Claim(
        id=claim_id,
        type=claim_type,
        title="SSO",
        requested_text="SSO generally available by 2026-12-31.",
    )


def deal() -> Deal:
    return Deal(
        id="acme-2026",
        account="Acme Corp",
        requested_by="Priya",
        annual_list_price=240000,
        cost_ratio_pct=55,
        claims=[claim()],
    )


def decision(claim_id: str = "SSO", verdict: Verdict = Verdict.NARROW) -> Decision:
    return Decision(
        claim_id=claim_id,
        verdict=verdict,
        rationale="Because the roadmap says beta.",
        approved_text="SSO beta by 2026-12-15.",
        required_roles=["AE", "ENG_LEAD"],
    )


def packet(**overrides: object) -> Packet:
    body = {
        "deal_id": "acme-2026",
        "version": 1,
        "account": "Acme Corp",
        "currency": "USD",
        "decisions": [decision()],
        "packet_hash": "0" * 64,
        "created_at": datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
    }
    body.update(overrides)
    return Packet(**body)


# ------------------------------------------------------------------ claims/deal
def test_claim_id_is_uppercased() -> None:
    assert claim("sso").id == "SSO"


def test_claim_id_pattern_is_enforced() -> None:
    with pytest.raises(ValidationError):
        claim("1")


def test_deal_claim_is_case_insensitive() -> None:
    value = deal()
    assert value.claim("sso").id == "SSO"
    with pytest.raises(KeyError):
        value.claim("NOPE")


def test_deal_requires_at_least_one_claim() -> None:
    with pytest.raises(ValidationError):
        Deal(
            id="acme-2026",
            account="Acme",
            requested_by="Priya",
            annual_list_price=1,
            cost_ratio_pct=10,
            claims=[],
        )


def test_deal_price_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Deal(
            id="acme-2026",
            account="Acme",
            requested_by="Priya",
            annual_list_price=0,
            cost_ratio_pct=10,
            claims=[claim()],
        )


# -------------------------------------------------------------------- evidence
def evidence(**overrides: object) -> Evidence:
    body = {
        "id": "EV-1",
        "claim_id": "SSO",
        "kind": "roadmap",
        "source": "Roadmap",
        "observed_at": date(2026, 10, 24),
        "summary": "Beta in December.",
    }
    body.update(overrides)
    return Evidence(**body)


def test_evidence_id_and_claim_id_are_uppercased() -> None:
    item = evidence(id="ev-1", claim_id="sso")
    assert item.id == "EV-1"
    assert item.claim_id == "SSO"


def test_evidence_digest_is_stable_and_excludes_digest() -> None:
    item = evidence()
    first = item.computed_digest()
    assert first == item.computed_digest()
    stored = item.model_copy(update={"digest": first})
    assert stored.computed_digest() == first
    changed = item.model_copy(update={"summary": "Beta moved."})
    assert changed.computed_digest() != first


def test_evidence_digest_changes_with_every_field() -> None:
    base = evidence().computed_digest()
    variants = [
        evidence(id="EV-2"),
        evidence(claim_id="SLA"),
        evidence(kind="monitoring"),
        evidence(source="Different"),
        evidence(observed_at=date(2026, 10, 25)),
        evidence(valid_until=date(2026, 12, 31)),
        evidence(summary="Different summary"),
        evidence(payload={"a": 1}),
    ]
    assert all(item.computed_digest() != base for item in variants)


def test_evidence_book_lookup_helpers() -> None:
    book = EvidenceBook(
        deal_id="acme-2026", items=[evidence(), evidence(id="EV-2", claim_id="SLA")]
    )
    assert [item.id for item in book.for_claim("sso")] == ["EV-1"]
    assert book.by_id("ev-1").id == "EV-1"
    with pytest.raises(KeyError):
        book.by_id("missing")
    assert book.for_claim("NOPE") == []


# -------------------------------------------------------------------- decision
@pytest.mark.parametrize(
    ("verdict", "is_commitment"),
    [
        (Verdict.APPROVE, True),
        (Verdict.NARROW, True),
        (Verdict.REFUSE, False),
        (Verdict.ESCALATE, False),
    ],
)
def test_decision_commitment_classification(verdict: Verdict, is_commitment: bool) -> None:
    assert decision(verdict=verdict).is_commitment is is_commitment


# ---------------------------------------------------------------------- packet
def test_packet_body_excludes_findings_and_status() -> None:
    first = packet()
    second = first.model_copy(
        update={
            "status": PacketStatus.BLOCKED,
            "findings": [
                Finding(
                    id="EX-001",
                    severity=Severity.BLOCK,
                    code="test",
                    message="a problem",
                )
            ],
        }
    )
    assert canonical_hash(first.body()) == canonical_hash(second.body())


def test_packet_body_changes_with_commitment_language() -> None:
    first = packet()
    changed = first.model_copy(deep=True)
    changed.decisions[0].approved_text = "SSO GA by 2026-11-30."
    assert canonical_hash(first.body()) != canonical_hash(changed.body())


def test_packet_body_changes_with_version() -> None:
    first = packet()
    second = first.model_copy(update={"version": 2})
    assert canonical_hash(first.body()) != canonical_hash(second.body())


def test_packet_hash_material_includes_previous_hash() -> None:
    first = packet()
    second = first.model_copy(update={"previous_hash": "a" * 64})
    assert canonical_hash(first.body()) != canonical_hash(second.body())


def test_packet_decision_lookup_is_case_insensitive() -> None:
    value = packet()
    assert value.decision("sso").claim_id == "SSO"
    with pytest.raises(KeyError):
        value.decision("NOPE")


def test_packet_has_blocks() -> None:
    value = packet()
    assert value.has_blocks() is False
    value.findings = [
        Finding(id="EX-001", severity=Severity.WARN, code="warn", message="minor"),
        Finding(id="EX-002", severity=Severity.BLOCK, code="block", message="major"),
    ]
    assert value.has_blocks() is True


def test_packet_timestamps_are_normalized_to_utc() -> None:
    value = packet(created_at=datetime(2026, 11, 4, 10, 0))
    assert value.created_at.tzinfo is UTC


# ------------------------------------------------------------------- approvers
def test_approvers_role_helpers() -> None:
    approvers = Approvers(
        deal_id="acme-2026",
        roles={"AE": ["Priya"], "LEGAL": ["Alex", "Dana"]},
    )
    assert approvers.people("ae") == ["Priya"]
    assert approvers.people("nope") == []
    assert approvers.role_for("priya") == "AE"
    assert approvers.role_for(" Dana ") == "LEGAL"
    assert approvers.role_for("nobody") is None
    assert approvers.can_hold("priya", "ae")
    assert not approvers.can_hold("priya", "legal")
    assert approvers.all_roles() == ["AE", "LEGAL"]


# -------------------------------------------------------------------- approval
def test_approval_coerces_naive_timestamp() -> None:
    value = Approval(
        id="apr_1",
        deal_id="acme-2026",
        packet_id="acme-2026-v1",
        packet_hash="0" * 64,
        claim_id="SSO",
        role="AE",
        actor="Priya",
        phrase="APPROVE ACME-2026 SSO AS AE",
        created_at=datetime(2026, 11, 4, 9, 0),
    )
    assert value.created_at.tzinfo is UTC
    assert value.status == ApprovalStatus.VALID


# ------------------------------------------------------------------ switch msg
def test_switch_message_timestamp() -> None:
    value = SwitchMessage(
        id="m1",
        room_id="r1",
        sender_name="Priya",
        body="hello",
        timestamp_ms=1793788800000,
    )
    assert value.timestamp.year == 2026
    assert value.timestamp.tzinfo is UTC


# ----------------------------------------------------------------------- gate
def test_gate_result_failures() -> None:
    result = GateResult(
        ok=False,
        checks=[
            GateCheck(code="a", ok=True, detail="fine"),
            GateCheck(code="b", ok=False, detail="broken"),
        ],
    )
    assert [item.code for item in result.failures] == ["b"]


# --------------------------------------------------------------------- receipt
def test_receipt_round_trips_through_json() -> None:
    body = ReceiptBody(
        receipt_id="vch_test",
        deal_id="acme-2026",
        account="Acme Corp",
        packet_id="acme-2026-v2",
        packet_version=2,
        packet_hash="0" * 64,
        policy_id="deal-policy-v1@v1",
        issued_at=datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
        expires_at=datetime(2026, 12, 4, 9, 0, tzinfo=UTC),
        commitments=[
            ReceiptDecision(
                claim_id="SSO",
                verdict=Verdict.NARROW,
                commitment="SSO beta by 2026-12-15.",
                conditions=["Not GA."],
            )
        ],
        approvals=[
            ReceiptApproval(
                claim_id="SSO",
                role="AE",
                actor="Priya",
                phrase="APPROVE ACME-2026 SSO AS AE",
                approved_at=datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
            )
        ],
    )
    receipt = Receipt(
        body=body,
        body_hash="1" * 64,
        signature="sig",
        public_key="key",
    )
    again = Receipt.model_validate_json(receipt.model_dump_json())
    assert again.body.receipt_id == "vch_test"
    assert again.body.commitments[0].conditions == ["Not GA."]
