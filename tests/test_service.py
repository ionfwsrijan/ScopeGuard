"""The orchestration layer: one workflow shared by CLI, API, demo, and bots."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from conftest import (
    ALL_APPROVALS,
    DEAL_ID,
    DEMO_DATE,
    DEMO_MOMENT,
    approve_all,
    prepare_v2,
)
from vouch.errors import (
    ApprovalError,
    ConfigError,
    GateError,
    NotFoundError,
    StateError,
)
from vouch.fixtures import load_messages
from vouch.models import ApprovalStatus, PacketStatus, Verdict
from vouch.receipt import canonical_hash
from vouch.service import VouchPaths, VouchService, as_of_clock, fixed_clock


# ----------------------------------------------------------------------- setup
def test_service_opens_policy_and_approvers(service: VouchService) -> None:
    assert service.policy.id == "deal-policy-v1"
    assert service.approvers.deal_id == DEAL_ID
    assert service.keyring.public_key_b64
    assert service.paths.key.exists()


def test_fixed_clock_is_timezone_aware() -> None:
    clock = fixed_clock(DEMO_MOMENT)
    assert clock() == DEMO_MOMENT
    assert clock().tzinfo is not None


def test_as_of_clock_uses_noon_utc() -> None:
    clock = as_of_clock(DEMO_DATE)
    assert clock().hour == 12
    assert clock().date() == DEMO_DATE


def test_service_is_a_context_manager(paths: VouchPaths) -> None:
    with VouchService.open(paths, now=fixed_clock(DEMO_MOMENT)) as service:
        assert service.store.stats()["deals"] == 0


# ---------------------------------------------------------------------- intake
def test_ingest_fixture_records_deal_and_evidence(service: VouchService) -> None:
    deal, evidence = service.ingest_fixture("acme")
    assert deal.id == DEAL_ID
    assert len(evidence.items) == 4
    assert service.deal(DEAL_ID).account == "Acme Corp"
    assert len(service.evidence(DEAL_ID).items) == 4
    assert service.store.stats()["deals"] == 1
    assert service.store.audit_events().__next__()["kind"] == "deal.ingested"


def test_ingest_rejects_a_mismatched_approver_map(tmp_path: Path, repo_root: Path) -> None:
    approvers = tmp_path / "approvers.yaml"
    approvers.write_text("deal_id: other-deal\nroles:\n  AE: [Priya]\n", encoding="utf-8")
    paths = VouchPaths.discover(root=repo_root, home=tmp_path / "home", approvers=approvers)
    with VouchService.open(paths, now=fixed_clock(DEMO_MOMENT)) as service:
        with pytest.raises(ConfigError) as info:
            service.ingest_fixture("acme")
        assert "does not match" in info.value.message


def test_unknown_deal_is_not_found(service: VouchService) -> None:
    with pytest.raises(NotFoundError):
        service.deal("nope")


# ---------------------------------------------------------------------- assess
def test_assess_builds_a_deterministic_v1(service: VouchService, ingested: object) -> None:
    packet = service.assess(DEAL_ID, note="first")
    assert packet.version == 1
    assert packet.status == PacketStatus.DRAFT
    assert packet.previous_hash is None
    assert packet.packet_hash == packet.packet_hash
    assert [item.claim_id for item in packet.decisions] == ["SSO", "SLA", "DISC", "SOC2"]
    assert packet.decision("DISC").verdict == Verdict.APPROVE
    assert packet.decision("SSO").verdict == Verdict.NARROW
    event = service.store.list_audit()[-1]
    assert event["kind"] == "packet.assessed"


def test_assess_twice_creates_v2_and_invalidates_old_approvals(service: VouchService) -> None:
    service.ingest_fixture("acme")
    v1 = service.assess(DEAL_ID)
    service.approve(DEAL_ID, "DISC", "AE", "Priya")
    v2 = service.assess(DEAL_ID, note="reassess")
    assert v2.version == 2
    assert v2.previous_hash == v1.packet_hash
    assert v2.packet_hash != v1.packet_hash
    stale = service.approvals(DEAL_ID)
    assert stale[0].status == ApprovalStatus.STALE
    assert stale[0].packet_hash == v1.packet_hash
    assert not service.store.approvals_for_packet(v2)


def test_assess_is_reproducible_for_same_inputs(service: VouchService) -> None:
    service.ingest_fixture("acme")
    first = service.assess(DEAL_ID)
    service.store.conn.execute("DELETE FROM packets")
    service.store.conn.commit()
    second = service.assess(DEAL_ID)
    assert first.packet_hash == second.packet_hash


# ------------------------------------------------------------------- challenge
def test_challenge_blocks_the_initial_discount(service: VouchService) -> None:
    service.ingest_fixture("acme")
    service.assess(DEAL_ID)
    packet = service.challenge(DEAL_ID)
    assert packet.status == PacketStatus.BLOCKED
    assert packet.has_blocks()
    assert {item.code for item in packet.findings} == {"discount_autonomy", "margin_floor"}


def test_revise_requires_blocking_findings(service: VouchService) -> None:
    service.ingest_fixture("acme")
    service.assess(DEAL_ID)
    with pytest.raises(StateError):
        service.revise(DEAL_ID)


def test_revise_produces_v2_and_stales_approvals(service: VouchService) -> None:
    service.ingest_fixture("acme")
    v1 = service.assess(DEAL_ID)
    service.approve(DEAL_ID, "DISC", "AE", "Priya")
    service.challenge(DEAL_ID)
    v2 = service.revise(DEAL_ID, note="fix discount")
    assert v2.version == 2
    assert v2.packet_hash != v1.packet_hash
    assert v2.decision("DISC").approved_value["discount_pct"] == 15
    assert service.approvals(DEAL_ID)[0].status == ApprovalStatus.STALE


def test_revise_twice_without_new_findings_is_refused(service: VouchService) -> None:
    service.ingest_fixture("acme")
    service.assess(DEAL_ID)
    service.challenge(DEAL_ID)
    service.revise(DEAL_ID)
    service.challenge(DEAL_ID)
    with pytest.raises(StateError):
        service.revise(DEAL_ID)


def test_challenge_rejects_a_sealed_packet(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    service.seal(DEAL_ID)
    with pytest.raises(StateError):
        service.challenge(DEAL_ID)


# -------------------------------------------------------------------- approvals
def test_approve_records_the_exact_expected_phrase(service: VouchService) -> None:
    prepare_v2(service)
    approval = service.approve(DEAL_ID, "SSO", "AE", "Priya")
    assert approval.phrase == "APPROVE ACME-2026 SSO AS AE"
    assert approval.packet_hash == service.store.latest_packet(DEAL_ID).packet_hash
    assert approval.status == ApprovalStatus.VALID


def test_approve_accepts_a_matching_phrase(service: VouchService) -> None:
    prepare_v2(service)
    approval = service.approve(
        DEAL_ID,
        "SSO",
        "AE",
        "Priya",
        phrase="I read it. APPROVE ACME-2026 SSO AS AE",
    )
    assert approval.claim_id == "SSO"


def test_approve_rejects_a_wrong_phrase(service: VouchService) -> None:
    prepare_v2(service)
    with pytest.raises(ApprovalError) as info:
        service.approve(DEAL_ID, "SSO", "AE", "Priya", phrase="APPROVE ACME-2026 SSO AS ENG_LEAD")
    assert "does not match" in info.value.message


def test_approve_rejects_a_role_that_is_not_required(service: VouchService) -> None:
    prepare_v2(service)
    with pytest.raises(ApprovalError) as info:
        service.approve(DEAL_ID, "SSO", "VP_SALES", "Maya")
    assert "not a required approver" in info.value.message


def test_approve_rejects_an_actor_without_the_role(service: VouchService) -> None:
    prepare_v2(service)
    with pytest.raises(ApprovalError) as info:
        service.approve(DEAL_ID, "SSO", "AE", "Maya")
    assert "does not hold AE" in info.value.message


def test_approve_rejects_an_unknown_claim(service: VouchService) -> None:
    prepare_v2(service)
    with pytest.raises(NotFoundError) as info:
        service.approve(DEAL_ID, "NOPE", "AE", "Priya")
    assert "claim not found" in info.value.message


def test_approve_is_idempotent_for_the_same_actor(service: VouchService) -> None:
    prepare_v2(service)
    first = service.approve(DEAL_ID, "SSO", "AE", "Priya")
    second = service.approve(DEAL_ID, "SSO", "AE", "Priya")
    assert first.id == second.id
    assert len(service.store.approvals_for_packet(service.store.latest_packet(DEAL_ID))) == 1


def test_one_role_one_approval(service: VouchService) -> None:
    prepare_v2(service)
    service.approve(DEAL_ID, "SSO", "AE", "Priya")
    # The same actor is idempotent; a different actor cannot take an occupied role.
    again = service.approve(DEAL_ID, "SSO", "AE", "Priya")
    assert again.actor == "Priya"
    with pytest.raises(ApprovalError):
        service.approve(DEAL_ID, "SSO", "AE", "Maya")


def test_approve_rejects_a_sealed_packet(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    service.seal(DEAL_ID)
    with pytest.raises(StateError):
        service.approve(DEAL_ID, "SSO", "AE", "Priya")


# ------------------------------------------------------------------------- sync
def test_sync_records_ten_messages_and_ignores_chatter(
    service: VouchService, repo_root: Path
) -> None:
    prepare_v2(service)
    messages = load_messages(repo_root / "fixtures" / "acme" / "switch_messages.yaml")
    result = service.sync_messages(DEAL_ID, messages, room_id="room-demo")
    assert result.scanned == 14
    assert result.recorded == 10
    assert result.ignored == 4
    assert result.errors == []
    gate = service.gate(DEAL_ID)
    assert gate.ok is True
    assert len(service.store.approvals_for_packet(service.store.latest_packet(DEAL_ID))) == 8


def test_sync_records_an_error_for_an_unauthorized_sender(service: VouchService) -> None:
    prepare_v2(service)
    from vouch.models import SwitchMessage

    messages = [
        SwitchMessage(
            id="m1",
            room_id="room-demo",
            sender_name="Mallory",
            body="APPROVE ACME-2026 SSO AS AE",
        )
    ]
    result = service.sync_messages(DEAL_ID, messages)
    assert result.recorded == 0
    assert result.errors and "does not hold AE" in result.errors[0]


def test_sync_ignores_a_message_with_two_phrases(service: VouchService) -> None:
    prepare_v2(service)
    from vouch.models import SwitchMessage

    result = service.sync_messages(
        DEAL_ID,
        [
            SwitchMessage(
                id="m1",
                room_id="room-demo",
                sender_name="Priya",
                body="APPROVE ACME-2026 SSO AS AE and APPROVE ACME-2026 SLA AS AE",
            )
        ],
    )
    assert result.recorded == 0
    assert result.ignored == 1


def test_sync_audits_every_seen_phrase(service: VouchService, repo_root: Path) -> None:
    prepare_v2(service)
    messages = load_messages(repo_root / "fixtures" / "acme" / "switch_messages.yaml")
    service.sync_messages(DEAL_ID, messages)
    kinds = [item["kind"] for item in service.store.list_audit()]
    assert kinds.count("switch.approval_seen") == 10
    # Ten phrases reference eight distinct claim/role pairs; repeats are idempotent.
    assert kinds.count("approval.recorded") == 8


# -------------------------------------------------------------------------- gate
def test_status_exposes_the_whole_case(service: VouchService) -> None:
    prepare_v2(service)
    status = service.status(DEAL_ID)
    assert status["deal"].id == DEAL_ID  # type: ignore[union-attr]
    assert status["packet"].version == 2  # type: ignore[union-attr]
    assert status["evidence_count"] == 4
    assert status["packet_count"] == 2
    assert status["receipt"] is None
    assert status["gate"].ok is False  # type: ignore[union-attr]
    assert len(status["approvals"]) == 0  # type: ignore[arg-type]


def test_decisions_are_keyed_by_claim(service: VouchService) -> None:
    prepare_v2(service)
    decisions = service.decisions(DEAL_ID)
    assert set(decisions) == {"SSO", "SLA", "DISC", "SOC2"}
    assert decisions["DISC"].approved_value["discount_pct"] == 15


# -------------------------------------------------------------------------- seal
def test_seal_requires_a_clear_gate(service: VouchService) -> None:
    prepare_v2(service)
    with pytest.raises(GateError) as info:
        service.seal(DEAL_ID)
    assert "cannot be sealed" in info.value.message
    assert info.value.hint and "status" in info.value.hint


def test_seal_issues_a_receipt_and_freezes_the_packet(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID, valid_days=10)
    assert receipt.body.receipt_id.startswith("vch_")
    assert receipt.body.policy_id == "deal-policy-v1@v1"
    assert receipt.body.expires_at - receipt.body.issued_at == timedelta(days=10)
    assert service.store.latest_packet(DEAL_ID).status == PacketStatus.SEALED
    assert service.store.latest_receipt(DEAL_ID).body.receipt_id == receipt.body.receipt_id
    assert receipt.body.chain_prev is None


def test_sealing_twice_is_refused(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    service.seal(DEAL_ID)
    with pytest.raises(StateError):
        service.seal(DEAL_ID)


# ------------------------------------------------------------------------ verify
def test_verify_accepts_a_local_receipt(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    result = service.verify(receipt, require_unexpired=True, pinned_key=True)
    assert result.ok is True
    codes = [item.code for item in result.checks]
    assert "pinned_key" in codes
    assert "packet_rehash" in codes
    assert "commitments_match_packet" in codes
    assert "recorded" in codes


def test_verify_rejects_a_tampered_receipt(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    receipt.body.commitments[0].commitment = "SSO generally available tomorrow."
    result = service.verify(receipt)
    assert result.ok is False
    assert "body_hash" in result.summary


def test_verify_rejects_a_tampered_local_packet(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    packet = service.store.latest_packet(DEAL_ID)
    packet.decisions[0].approved_text = "SSO GA tomorrow."
    service.store.update_packet(packet)
    result = service.verify(receipt)
    assert result.ok is False
    assert any(item.code == "packet_rehash" and not item.ok for item in result.checks)


def test_verify_without_a_local_packet_uses_the_signed_body(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    with service.store.conn:
        service.store.conn.execute("DELETE FROM packets")
    result = service.verify(receipt)
    assert result.ok is True
    assert any(item.code == "packet_present" for item in result.checks)


def test_verify_latest(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    loaded, result = service.verify_latest(DEAL_ID)
    assert loaded.body.receipt_id == receipt.body.receipt_id
    assert result.ok is True


def test_verify_does_not_require_recording(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    with service.store.conn:
        service.store.conn.execute("DELETE FROM receipts")
    result = service.verify(receipt)
    assert result.ok is True
    assert not any(item.code == "recorded" for item in result.checks)


# ----------------------------------------------------------------------- handoff
def test_handoff_contains_only_approved_language(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    text = service.handoff_text(DEAL_ID)
    assert receipt.body.receipt_id in text
    assert "SSO: beta available by 2026-12-15" in text
    assert "15.00% discount" in text
    assert "20%" not in text.split("Not committed")[0]
    assert "99.95%" not in text
    assert "Only the commitments above leave this room." in text


def test_handoff_lists_declined_claims(service: VouchService) -> None:
    prepare_v2(service)
    packet = service.store.latest_packet(DEAL_ID)
    packet.decisions[0].verdict = Verdict.REFUSE
    packet.packet_hash = canonical_hash(packet.body())
    service.store.update_packet(packet)
    approve_all(service)
    receipt = service.seal(DEAL_ID)
    assert "SSO" in receipt.body.declined
    text = service.handoff_text(DEAL_ID)
    assert "**Not committed**" in text


# ------------------------------------------------------------------------- audit
def test_full_flow_keeps_the_audit_chain_intact(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    service.seal(DEAL_ID)
    ok, detail = service.store.verify_audit()
    assert ok is True
    assert "events" in detail
    kinds = [item["kind"] for item in service.store.list_audit()]
    assert kinds[:3] == ["deal.ingested", "packet.assessed", "packet.challenged"]
    assert "receipt.sealed" in kinds


def test_all_approval_roles_are_covered_exactly_once(service: VouchService) -> None:
    prepare_v2(service)
    approve_all(service)
    pairs = [
        (item.claim_id, item.role)
        for item in service.store.approvals_for_packet(service.store.latest_packet(DEAL_ID))
    ]
    assert sorted(pairs) == sorted((claim, role) for _, claim, role in ALL_APPROVALS)
