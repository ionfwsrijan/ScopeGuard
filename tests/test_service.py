"""The service layer: the same workflow the CLI, API, demo, and Clerk execute."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from helpers import CLIENT_SIGNER, ENGAGEMENT_ID, accepted, prepare_v2, sealed, sign_all
from scopeguard.errors import (
    ApprovalError,
    ConfigError,
    GateError,
    NotFoundError,
    StateError,
)
from scopeguard.models import Approvers, PacketStatus, SwitchMessage
from scopeguard.service import ScopeService


# --------------------------------------------------------------------- intake
def test_ingest_loads_four_requests_and_four_evidence_items(ingested) -> None:
    engagement, evidence = ingested
    assert engagement.id == ENGAGEMENT_ID
    assert [item.id for item in engagement.requests] == ["MOBILE", "RUSH", "RATE", "TERMS"]
    assert len(evidence.items) == 4
    assert all(item.digest for item in evidence.items)
    assert {item.request_id for item in evidence.items} == {"MOBILE", "RUSH", "RATE", "TERMS"}


def test_ingest_rejects_a_mismatched_approver_map(service: ScopeService) -> None:
    service.approvers = Approvers(engagement_id="other-2026", roles={"ACCOUNT_LEAD": ["Dana"]})
    with pytest.raises(ConfigError):
        service.ingest_fixture("northwind")


def test_ingest_audits_the_engagement(service: ScopeService) -> None:
    service.ingest_fixture("northwind")
    kinds = [item["kind"] for item in service.store.list_audit()]
    assert "engagement.ingested" in kinds


# --------------------------------------------------------------------- assess
def test_assess_prices_v1_and_persists_a_draft_packet(service: ScopeService, ingested) -> None:
    engagement, _ = ingested
    packet = service.assess(engagement.id)
    assert packet.version == 1
    assert packet.status == PacketStatus.DRAFT.value
    assert packet.subtotal == 48000.0
    assert packet.discount_total == -9600.0
    assert packet.total == 38400.0
    assert len(packet.decisions) == 4
    assert service.store.latest_packet(engagement.id).packet_hash == packet.packet_hash


def test_assess_is_deterministic_across_identical_ledgers(tmp_path: Path, repo_root: Path) -> None:
    from scopeguard.service import ScopePaths, fixed_clock

    hashes = []
    for name in ("a", "b"):
        paths = ScopePaths.discover(root=repo_root, home=tmp_path / name)
        with ScopeService.open(
            paths, now=fixed_clock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
        ) as service:
            service.ingest_fixture("northwind")
            hashes.append(service.assess(ENGAGEMENT_ID).packet_hash)
    assert hashes[0] == hashes[1]


def test_scope_home_env_sets_the_runtime_directory(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopeguard.service import ScopePaths

    shared = tmp_path / "shared-ledger"
    monkeypatch.setenv("SCOPE_HOME", str(shared))
    paths = ScopePaths.discover(root=repo_root)
    assert paths.home == shared.resolve()


def test_assessing_again_creates_a_new_version_and_invalidates_old_signatures(
    service: ScopeService,
) -> None:
    service.ingest_fixture("northwind")
    first = service.assess(ENGAGEMENT_ID)
    service.approve(ENGAGEMENT_ID, "MOBILE", "ACCOUNT_LEAD", "Dana")
    second = service.assess(ENGAGEMENT_ID)
    assert second.version == 2
    assert second.previous_hash == first.packet_hash
    stale = service.approvals(ENGAGEMENT_ID)
    assert len(stale) == 1
    assert stale[0].status == "stale"


# ---------------------------------------------------------- challenge/revise
def test_challenge_blocks_the_20_percent_packet(service: ScopeService, ingested) -> None:
    engagement, _ = ingested
    service.assess(engagement.id)
    packet = service.challenge(engagement.id)
    assert packet.status == PacketStatus.BLOCKED.value
    assert packet.has_blocks() is True
    codes = {item.code for item in packet.findings}
    assert {"rate_autonomy", "margin_floor"} <= codes
    assert service.store.latest_packet(engagement.id).status == PacketStatus.BLOCKED.value


def test_revise_requires_blocking_findings(service: ScopeService, ingested) -> None:
    engagement, _ = ingested
    service.assess(engagement.id)
    with pytest.raises(StateError):
        service.revise(engagement.id)


def test_revise_narrows_the_discount_and_reprices_the_total(service: ScopeService) -> None:
    _engagement, v1, v2 = prepare_v2(service)
    assert v2.version == 2
    assert v2.previous_hash == v1.packet_hash
    assert v2.total == 42240.0
    assert v2.decision("RATE").approved_value["discount_pct"] == 12
    assert v2.decision("RATE").amount == -5760.0
    assert v2.has_blocks() is False


def test_revise_invalidates_every_signature_on_the_old_hash(service: ScopeService) -> None:
    service.ingest_fixture("northwind")
    v1 = service.assess(ENGAGEMENT_ID)
    first = service.approve(ENGAGEMENT_ID, "RATE", "ACCOUNT_LEAD", "Dana")
    service.challenge(ENGAGEMENT_ID)
    v2 = service.revise(ENGAGEMENT_ID)
    stale = next(item for item in service.approvals(ENGAGEMENT_ID) if item.id == first.id)
    assert stale.status == "stale"
    assert stale.packet_hash == v1.packet_hash
    assert stale.packet_hash != v2.packet_hash


def test_challenge_refuses_a_sealed_packet(service: ScopeService) -> None:
    sealed(service)
    with pytest.raises(StateError):
        service.challenge(ENGAGEMENT_ID)


# ------------------------------------------------------------------ approvals
def test_a_required_role_can_sign_with_a_named_human(service: ScopeService) -> None:
    prepare_v2(service)
    approval = service.approve(ENGAGEMENT_ID, "MOBILE", "ACCOUNT_LEAD", "Dana")
    assert approval.role == "ACCOUNT_LEAD"
    assert approval.actor == "Dana"
    assert approval.phrase == "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD"
    assert approval.status == "valid"
    assert service.store.approvals_for_packet(service.store.latest_packet(ENGAGEMENT_ID)) == [
        approval
    ]


def test_a_role_that_is_not_required_is_rejected(service: ScopeService) -> None:
    prepare_v2(service)
    with pytest.raises(ApprovalError) as info:
        service.approve(ENGAGEMENT_ID, "MOBILE", "FINANCE", "Rae")
    assert "not a required approver" in info.value.message


def test_an_actor_who_does_not_hold_the_role_is_rejected(service: ScopeService) -> None:
    prepare_v2(service)
    with pytest.raises(ApprovalError) as info:
        service.approve(ENGAGEMENT_ID, "MOBILE", "ACCOUNT_LEAD", "Rae")
    assert "does not hold" in info.value.message
    assert "Dana" in (info.value.hint or "")


def test_a_wrong_phrase_is_rejected(service: ScopeService) -> None:
    prepare_v2(service)
    with pytest.raises(ApprovalError) as info:
        service.approve(
            ENGAGEMENT_ID,
            "MOBILE",
            "ACCOUNT_LEAD",
            "Dana",
            phrase="SIGN NORTHWIND-2026 RATE AS FINANCE",
        )
    assert "does not match" in info.value.message


def test_a_correct_phrase_is_accepted(service: ScopeService) -> None:
    prepare_v2(service)
    approval = service.approve(
        ENGAGEMENT_ID,
        "MOBILE",
        "ACCOUNT_LEAD",
        "Dana",
        phrase="SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD",
    )
    assert approval.phrase == "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD"


def test_the_same_signer_is_idempotent(service: ScopeService) -> None:
    prepare_v2(service)
    first = service.approve(ENGAGEMENT_ID, "MOBILE", "ACCOUNT_LEAD", "Dana")
    second = service.approve(ENGAGEMENT_ID, "MOBILE", "ACCOUNT_LEAD", "dana")
    assert first.id == second.id
    assert len(service.approvals(ENGAGEMENT_ID)) == 1


def test_a_different_signer_cannot_take_the_same_role(service: ScopeService) -> None:
    service.ingest_fixture("northwind")
    service.assess(ENGAGEMENT_ID)
    service.challenge(ENGAGEMENT_ID)
    service.revise(ENGAGEMENT_ID)
    service.approve(ENGAGEMENT_ID, "TERMS", "LEGAL", "Iris")
    with pytest.raises(ApprovalError) as info:
        service.approve(ENGAGEMENT_ID, "TERMS", "LEGAL", "Dana")
    assert "already approved" in info.value.message


def test_approving_an_unknown_request_is_not_found(service: ScopeService) -> None:
    prepare_v2(service)
    with pytest.raises(NotFoundError):
        service.approve(ENGAGEMENT_ID, "NOPE", "ACCOUNT_LEAD", "Dana")


def test_a_sealed_packet_cannot_be_signed(service: ScopeService) -> None:
    sealed(service)
    with pytest.raises(StateError):
        service.approve(ENGAGEMENT_ID, "MOBILE", "ACCOUNT_LEAD", "Dana")


def test_sync_messages_records_phrases_and_reports_errors(service: ScopeService) -> None:
    prepare_v2(service)
    messages = [
        SwitchMessage(
            id="m1",
            room_id="room-northwind-scope",
            sender_name="Dana",
            body="SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD",
        ),
        SwitchMessage(
            id="m2",
            room_id="room-northwind-scope",
            sender_name="Rae",
            body="SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD",
        ),
        SwitchMessage(
            id="m3", room_id="room-northwind-scope", sender_name="Clerk", body="Looks good to me"
        ),
    ]
    result = service.sync_messages(ENGAGEMENT_ID, messages, room_id="room-northwind-scope")
    assert result.scanned == 3
    assert result.recorded == 1
    assert result.ignored == 1
    assert len(result.approvals) == 1
    assert len(result.errors) == 1
    assert "Rae" in result.errors[0]


def test_sync_messages_records_switch_provenance_in_the_audit(service: ScopeService) -> None:
    prepare_v2(service)
    messages = [
        SwitchMessage(
            id="m1",
            room_id="room-northwind-scope",
            sender_name="Dana",
            body="SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD",
        )
    ]
    service.sync_messages(ENGAGEMENT_ID, messages, room_id="room-northwind-scope")
    event = next(item for item in service.store.list_audit() if item["kind"] == "approval.recorded")
    assert json.loads(event["detail"])["source"] == "switch:room-northwind-scope"


# ----------------------------------------------------------------------- gate
def test_gate_lists_eight_pending_signatures_before_approval(service: ScopeService) -> None:
    prepare_v2(service)
    gate = service.gate(ENGAGEMENT_ID)
    assert gate.ok is False
    assert len(gate.pending) == 8
    assert "MOBILE ACCOUNT_LEAD" in gate.pending
    assert service.pending(ENGAGEMENT_ID) == gate.pending


def test_gate_opens_after_every_required_signature(service: ScopeService) -> None:
    prepare_v2(service)
    sign_all(service)
    gate = service.gate(ENGAGEMENT_ID)
    assert gate.ok is True
    assert gate.pending == []


# ----------------------------------------------------------------------- seal
def test_seal_refuses_an_incomplete_gate(service: ScopeService) -> None:
    prepare_v2(service)
    with pytest.raises(GateError) as info:
        service.seal(ENGAGEMENT_ID)
    assert "cannot be sealed" in info.value.message
    assert "status" in (info.value.hint or "") or "pending" in (info.value.hint or "")


def test_seal_builds_a_two_party_ready_change_order(service: ScopeService) -> None:
    receipt = sealed(service)
    assert receipt.body.receipt_id.startswith("co_")
    assert receipt.body.engagement_id == ENGAGEMENT_ID
    assert receipt.body.client == "Northwind Health"
    assert receipt.body.packet_version == 2
    assert receipt.body.subtotal == 48000.0
    assert receipt.body.discount_total == -5760.0
    assert receipt.body.total == 42240.0
    assert len(receipt.body.line_items) == 4
    assert len(receipt.body.approvals) == 8
    assert receipt.body.declined == []
    assert receipt.acceptance is None


def test_sealing_twice_is_refused(service: ScopeService) -> None:
    sealed(service)
    with pytest.raises(StateError):
        service.seal(ENGAGEMENT_ID)


def test_the_sealed_packet_status_and_timestamp_are_recorded(service: ScopeService) -> None:
    sealed(service)
    packet = service.store.latest_packet(ENGAGEMENT_ID)
    assert packet.status == PacketStatus.SEALED.value
    assert packet.sealed_at is not None


# ---------------------------------------------------------------- countersign
def test_countersignature_makes_the_change_order_two_party(service: ScopeService) -> None:
    receipt = sealed(service)
    accepted_receipt = service.countersign(ENGAGEMENT_ID, CLIENT_SIGNER)
    assert receipt.acceptance is None
    assert accepted_receipt.acceptance is not None
    assert accepted_receipt.acceptance.issuer == CLIENT_SIGNER
    assert accepted_receipt.body_hash == receipt.body_hash
    stored = service.store.latest_receipt(ENGAGEMENT_ID)
    assert stored.acceptance is not None
    result = service.verify(stored, require_acceptance=True, pinned_client_key=True)
    assert result.ok is True


def test_countersigning_before_sealing_is_not_found(service: ScopeService) -> None:
    with pytest.raises(NotFoundError):
        service.countersign(ENGAGEMENT_ID, CLIENT_SIGNER)


def test_a_second_countersignature_is_refused(service: ScopeService) -> None:
    accepted(service)
    with pytest.raises(ValueError):
        service.countersign(ENGAGEMENT_ID, "Someone Else")


# --------------------------------------------------------------------- verify
def test_a_sealed_change_order_verifies_with_the_pinned_key(service: ScopeService) -> None:
    receipt = accepted(service)
    result = service.verify(
        receipt,
        require_unexpired=True,
        require_acceptance=True,
        pinned_key=True,
        pinned_client_key=True,
    )
    assert result.ok is True
    assert "accepted" in result.summary


def test_acceptance_is_required_when_requested(service: ScopeService) -> None:
    receipt = sealed(service)
    result = service.verify(receipt, require_acceptance=True)
    assert result.ok is False
    assert "acceptance" in " ".join(check.code for check in result.checks if not check.ok)


def test_tampering_with_a_signed_line_item_fails_verification(service: ScopeService) -> None:
    receipt = accepted(service)
    receipt.body.line_items[0].scope = "GA by 2026-12-15."
    result = service.verify(receipt)
    assert result.ok is False
    assert "body_hash" in " ".join(check.code for check in result.checks if not check.ok)


def test_verification_rehashes_the_local_packet(service: ScopeService) -> None:
    receipt = sealed(service)
    result = service.verify(receipt)
    assert "packet_rehash" in [check.code for check in result.checks]
    assert next(check for check in result.checks if check.code == "packet_rehash").ok is True


def test_verification_fails_when_the_local_packet_was_edited(service: ScopeService) -> None:
    receipt = sealed(service)
    packet = service.store.latest_packet(ENGAGEMENT_ID)
    packet.decision("RATE").amount = -1.0
    service.store.update_packet(packet)
    result = service.verify(receipt)
    assert result.ok is False
    assert next(check for check in result.checks if check.code == "packet_rehash").ok is False


def test_verify_latest_returns_the_receipt_and_result(service: ScopeService) -> None:
    receipt = accepted(service)
    loaded, result = service.verify_latest(ENGAGEMENT_ID, require_acceptance=True)
    assert loaded.body.receipt_id == receipt.body.receipt_id
    assert result.ok is True


# -------------------------------------------------------------------- handoff
def test_handoff_lists_only_signed_line_items(service: ScopeService) -> None:
    receipt = accepted(service)
    text = service.handoff_text(ENGAGEMENT_ID)
    assert receipt.body.receipt_id in text
    assert "Countersigned by Jordan Reyes" in text
    assert "MOBILE" in text and "RATE" in text
    assert "42,240.00" in text
    assert "-5,760.00" in text
    assert "20.00%" not in text
    assert "Only the line items above leave this room." in text


def test_handoff_says_awaiting_client_before_acceptance(service: ScopeService) -> None:
    sealed(service)
    assert "Awaiting client countersignature" in service.handoff_text(ENGAGEMENT_ID)


def test_handoff_lists_declined_requests(service: ScopeService) -> None:
    prepare_v2(service)
    packet = service.store.latest_packet(ENGAGEMENT_ID)
    packet.decisions[0].verdict = "escalate"
    packet.decisions[0].amount = None
    packet.packet_hash = __import__("scopeguard.receipt", fromlist=["packet_hash"]).packet_hash(
        packet.body()
    )
    service.store.update_packet(packet)
    sign_all(service)
    service.seal(ENGAGEMENT_ID)
    text = service.handoff_text(ENGAGEMENT_ID)
    assert "Not committed" in text
    assert "MOBILE" in text


# --------------------------------------------------------------------- status
def test_status_returns_everything_a_screen_needs(service: ScopeService) -> None:
    prepare_v2(service)
    view = service.status(ENGAGEMENT_ID)
    assert view["engagement"].id == ENGAGEMENT_ID
    assert view["packet"].version == 2
    assert view["packet_count"] == 2
    assert view["evidence_count"] == 4
    assert view["receipt"] is None
    assert view["gate"].ok is False


def test_decisions_returns_a_map_of_the_latest_packet(service: ScopeService) -> None:
    prepare_v2(service)
    decisions = service.decisions(ENGAGEMENT_ID)
    assert set(decisions) == {"MOBILE", "RUSH", "RATE", "TERMS"}
    assert decisions["RATE"].approved_value["discount_pct"] == 12


def test_the_full_workflow_leaves_an_intact_audit_chain(service: ScopeService) -> None:
    accepted(service)
    ok, detail = service.store.verify_audit()
    assert ok is True
    assert "audit chain intact" in detail
    kinds = {item["kind"] for item in service.store.list_audit()}
    assert {
        "engagement.ingested",
        "packet.priced",
        "packet.challenged",
        "packet.revised",
        "approval.recorded",
        "change_order.sealed",
        "change_order.countersigned",
    } <= kinds
