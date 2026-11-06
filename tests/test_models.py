"""The typed domain model: serialization, lookups, and hash-safe packet bodies."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from scopeguard.models import (
    Acceptance,
    Approval,
    Approvers,
    Decision,
    Engagement,
    Evidence,
    EvidenceBook,
    Finding,
    GateCheck,
    GateResult,
    Packet,
    PacketStatus,
    Receipt,
    ReceiptBody,
    ReceiptLineItem,
    RequestType,
    Severity,
    SwitchMessage,
    Verdict,
)
from scopeguard.receipt import canonical_hash, packet_hash

MOMENT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def engagement() -> Engagement:
    return Engagement(
        id="northwind-2026",
        client="Northwind Health",
        requested_by="Dana",
        contract_value=120000,
        delivery_cost_ratio_pct=58,
        requests=[
            {
                "id": "MOBILE",
                "type": "new_work",
                "title": "Native mobile app",
                "requested_text": "A native mobile app by launch.",
            },
            {
                "id": "RATE",
                "type": "rate",
                "title": "Discount",
                "requested_text": "20% off.",
                "requested_value": {"discount_pct": 20},
            },
        ],
    )


def decision(request_id: str = "MOBILE", amount: float | None = 30000.0) -> Decision:
    return Decision(
        request_id=request_id,
        verdict=Verdict.NARROW,
        rationale="because policy",
        priced_text="Beta only.",
        amount=amount,
        conditions=["Beta only."],
        required_roles=["ACCOUNT_LEAD", "DELIVERY_LEAD"],
    )


def packet(**overrides: object) -> Packet:
    body = {
        "engagement_id": "northwind-2026",
        "version": 1,
        "client": "Northwind Health",
        "currency": "USD",
        "decisions": [decision()],
    }
    values = {
        "created_at": MOMENT,
        "packet_hash": "",
        **body,
        **overrides,
    }
    instance = Packet(**values)  # type: ignore[arg-type]
    instance.packet_hash = packet_hash(instance.body())
    return instance


# ---------------------------------------------------------------------- enums
def test_request_types_cover_the_four_scope_creep_shapes() -> None:
    assert {item.value for item in RequestType} == {"new_work", "timeline", "rate", "terms"}


def test_verdicts_and_severities_are_serialized_as_strings() -> None:
    assert Verdict.APPROVE.value == "approve"
    assert Verdict.NARROW.value == "narrow"
    assert Verdict.REFUSE.value == "refuse"
    assert Verdict.ESCALATE.value == "escalate"
    assert Severity.BLOCK.rank < Severity.WARN.rank < Severity.NOTE.rank


# ------------------------------------------------------------------- requests
def test_request_ids_are_normalized_to_uppercase() -> None:
    request = {
        "id": "mobile",
        "type": "new_work",
        "title": "Mobile app",
        "requested_text": "Build it.",
    }
    parsed = Engagement(
        id="acme-2026",
        client="Acme",
        requested_by="Dana",
        contract_value=1000,
        delivery_cost_ratio_pct=50,
        requests=[request],
    )
    assert parsed.requests[0].id == "MOBILE"
    assert parsed.request("mobile").id == "MOBILE"


def test_request_ids_must_start_with_a_letter_and_be_short() -> None:
    with pytest.raises(ValidationError):
        Engagement(
            id="acme-2026",
            client="Acme",
            requested_by="Dana",
            contract_value=1000,
            delivery_cost_ratio_pct=50,
            requests=[
                {
                    "id": "1BAD",
                    "type": "new_work",
                    "title": "Bad",
                    "requested_text": "Bad request.",
                }
            ],
        )


def test_engagement_lookup_raises_keyerror_for_unknown_request() -> None:
    with pytest.raises(KeyError):
        engagement().request("NOPE")


def test_engagement_token_is_uppercase_and_engagement_requires_positive_value() -> None:
    assert engagement().engagement_token == "NORTHWIND-2026"
    with pytest.raises(ValidationError):
        Engagement(
            id="acme-2026",
            client="Acme",
            requested_by="Dana",
            contract_value=0,
            delivery_cost_ratio_pct=50,
            requests=[],
        )


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        Engagement(
            id="acme-2026",
            client="Acme",
            requested_by="Dana",
            contract_value=1000,
            delivery_cost_ratio_pct=50,
            requests=[],
            surprise="no",
        )


# ------------------------------------------------------------------- evidence
def evidence(**overrides: object) -> Evidence:
    values = {
        "id": "EV-MOBILE-1",
        "request_id": "MOBILE",
        "kind": "estimate",
        "source": "estimate.pdf",
        "observed_at": date(2026, 11, 1),
        "summary": "320 hours at 150.",
        "payload": {"hours": 320, "rate": 150},
        **overrides,
    }
    return Evidence(**values)  # type: ignore[arg-type]


def test_evidence_digest_is_stable_and_excludes_itself() -> None:
    item = evidence()
    first = item.computed_digest()
    again = evidence(digest=first).computed_digest()
    assert first == again
    assert len(first) == 64
    assert evidence(summary="different").computed_digest() != first


def test_evidence_book_filters_by_uppercase_request() -> None:
    book = EvidenceBook(
        engagement_id="northwind-2026",
        items=[evidence(), evidence(id="EV-RATE-1", request_id="RATE", kind="rate_card")],
    )
    assert [item.id for item in book.for_request("mobile")] == ["EV-MOBILE-1"]
    assert book.by_id("ev-rate-1").kind == "rate_card"
    with pytest.raises(KeyError):
        book.by_id("EV-NOPE-1")


# ------------------------------------------------------------------- decision
def test_only_approve_and_narrow_are_commitments() -> None:
    for verdict, expected in [
        (Verdict.APPROVE, True),
        (Verdict.NARROW, True),
        (Verdict.REFUSE, False),
        (Verdict.ESCALATE, False),
    ]:
        item = decision()
        item.verdict = verdict
        assert item.is_commitment is expected


# --------------------------------------------------------------------- packet
def test_packet_body_excludes_findings_status_and_timestamps() -> None:
    original = packet()
    changed = packet(
        findings=[
            Finding(
                id="RT-001",
                severity=Severity.BLOCK,
                code="rate_autonomy",
                message="too much",
            )
        ],
        status=PacketStatus.BLOCKED,
        challenged_at=MOMENT,
    )
    assert packet_hash(original.body()) == packet_hash(changed.body())
    assert original.packet_hash == changed.packet_hash
    assert changed.has_blocks() is True


def test_packet_body_changes_when_a_price_changes() -> None:
    original = packet()
    repriced = packet(decisions=[decision(amount=29999.0)])
    assert original.packet_hash != repriced.packet_hash


def test_packet_totals_split_positive_and_negative_line_items() -> None:
    instance = packet(
        decisions=[
            decision("MOBILE", 30000.0),
            decision("RUSH", 12000.0),
            decision("RATE", -9600.0),
            decision("TERMS", None),
        ]
    )
    assert instance.subtotal == 42000.0
    assert instance.discount_total == -9600.0
    assert instance.total == 32400.0


def test_packet_decision_lookup_is_case_insensitive() -> None:
    assert packet().decision("mobile").request_id == "MOBILE"
    with pytest.raises(KeyError):
        packet().decision("TERMS")


def test_packet_timestamps_normalize_to_utc() -> None:
    instance = packet(created_at=datetime(2026, 11, 4, 10, 0))
    assert instance.created_at.tzinfo is UTC
    assert instance.created_at.hour == 10


# ------------------------------------------------------------------ approvers
def test_approvers_are_case_insensitive_and_role_aware() -> None:
    approvers = Approvers(
        engagement_id="northwind-2026",
        roles={"account_lead": ["Dana"], "finance": ["Rae"]},
    )
    assert approvers.people("ACCOUNT_LEAD") == ["Dana"]
    assert approvers.role_for(" dana ") == "ACCOUNT_LEAD"
    assert approvers.role_for("nobody") is None
    assert approvers.can_hold("DANA", "account_lead") is True
    assert approvers.can_hold("Rae", "ACCOUNT_LEAD") is False
    assert approvers.all_roles() == ["account_lead", "finance"]


# --------------------------------------------------------------------- gate
def test_gate_result_exposes_failures() -> None:
    result = GateResult(
        ok=False,
        checks=[
            GateCheck(code="status", ok=False, detail="draft"),
            GateCheck(code="signatures", ok=True, detail="signed"),
        ],
        pending=["MOBILE ACCOUNT_LEAD"],
    )
    assert [item.code for item in result.failures] == ["status"]
    assert result.pending == ["MOBILE ACCOUNT_LEAD"]


# ------------------------------------------------------------------- receipt
def test_receipt_body_and_acceptance_round_trip() -> None:
    body = ReceiptBody(
        receipt_id="co_test",
        engagement_id="northwind-2026",
        client="Northwind Health",
        packet_id="northwind-2026-v2",
        packet_version=2,
        packet_hash="abc",
        policy_id="scope-policy-v1@v1",
        issued_at=MOMENT,
        expires_at=MOMENT,
        line_items=[
            ReceiptLineItem(
                request_id="MOBILE",
                kind="MOBILE",
                verdict=Verdict.NARROW,
                scope="Beta only.",
                amount=30000.0,
            )
        ],
        approvals=[],
        subtotal=30000.0,
        discount_total=0.0,
        total=30000.0,
    )
    receipt = Receipt(
        body=body,
        body_hash=canonical_hash(body),
        signature="sig",
        public_key="key",
        acceptance=Acceptance(
            issuer="Jordan",
            public_key="ckey",
            signature="csig",
            receipt_id=body.receipt_id,
            body_hash=canonical_hash(body),
            accepted_at=datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
        ),
    )
    assert receipt.body.total == 30000.0
    assert receipt.acceptance is not None
    assert receipt.acceptance.issuer == "Jordan"


def test_switch_message_timestamp_is_utc() -> None:
    message = SwitchMessage(
        id="m1",
        room_id="room",
        sender_name="Dana",
        body="hello",
        timestamp_ms=1793875200000,
    )
    assert message.timestamp.tzinfo is UTC
    assert message.timestamp.year == 2026


def test_approval_timestamps_normalize_to_utc() -> None:
    approval = Approval(
        id="apr_1",
        engagement_id="northwind-2026",
        packet_id="northwind-2026-v2",
        packet_hash="abc",
        request_id="MOBILE",
        role="ACCOUNT_LEAD",
        actor="Dana",
        phrase="SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD",
        created_at=datetime(2026, 11, 4, 9, 0),
    )
    assert approval.created_at.tzinfo is UTC
