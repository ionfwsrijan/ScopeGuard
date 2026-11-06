"""The deterministic Clerk and the in-memory Switch room it drives."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from helpers import CLIENT_SIGNER, ENGAGEMENT_ID, sign_all
from scopeguard.errors import NotFoundError
from scopeguard.models import ApprovalStatus
from scopeguard.receipt import Keyring, acceptance_token, build_acceptance
from scopeguard.service import ScopeService
from scopeguard.switch.clerk import Clerk
from scopeguard.switch.fake import FakeSwitch

SCOPE = "room-northwind-scope"
DELIVERY = "room-northwind-delivery"


@pytest.fixture
def switch() -> FakeSwitch:
    value = FakeSwitch()
    value.create_room(SCOPE, "#scope-northwind")
    value.create_room(DELIVERY, "#delivery-northwind")
    return value


@pytest.fixture
def clerk(service: ScopeService, switch: FakeSwitch) -> Clerk:
    from helpers import prepare_v2

    prepare_v2(service)
    return Clerk(
        service=service,
        client=switch,
        engagement_id=ENGAGEMENT_ID,
        scope_room_id=SCOPE,
        delivery_room_id=DELIVERY,
        sleep=lambda _: None,
    )


# --------------------------------------------------------------------- fake room
def test_fake_room_assigns_ids_timestamps_and_senders(switch: FakeSwitch) -> None:
    room = switch.room(SCOPE)
    first = switch.human(SCOPE, "Dana", "hello")
    second = switch.post(SCOPE, "Clerk", "hi")
    assert first.id == "room-northwind-scope-m1"
    assert second.id == "room-northwind-scope-m2"
    assert first.sender == "human-dana"
    assert second.sender == "agent-clerk"
    assert first.timestamp == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert room.next_sequence == 3


def test_fake_room_context_is_a_thread_per_message(switch: FakeSwitch) -> None:
    switch.human(SCOPE, "Dana", "one")
    switch.human(SCOPE, "Marco", "two")
    payload = switch.read_context(SCOPE)
    assert [item["root"]["body"] for item in payload["threads"]] == ["one", "two"]
    assert payload["truncated"] is False


def test_fake_room_context_honours_a_limit(switch: FakeSwitch) -> None:
    for index in range(5):
        switch.human(SCOPE, "Dana", str(index))
    payload = switch.read_context(SCOPE, limit=2)
    assert [item["root"]["body"] for item in payload["threads"]] == ["3", "4"]


def test_fake_switch_messages_can_be_filtered_since(switch: FakeSwitch) -> None:
    switch.room(SCOPE).add("Dana", "old", at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    latest = switch.room(SCOPE).add("Dana", "new", at=datetime(2026, 11, 4, 12, 1, tzinfo=UTC))
    assert [item.body for item in switch.messages(SCOPE, since=latest.timestamp_ms)] == ["new"]


# ---------------------------------------------------------------------- clerk run
def test_clerk_records_exactly_the_approval_messages(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SCOPE, "Dana", "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD")
    switch.human(SCOPE, "Marco", "SIGN NORTHWIND-2026 MOBILE AS DELIVERY_LEAD")
    switch.human(SCOPE, "Dana", "this is not an approval")
    result = clerk.run_once()
    assert result == {"scanned": 3, "recorded": 2, "ignored": 1}
    packet = clerk.service.store.latest_packet(ENGAGEMENT_ID)
    assert len(clerk.service.store.approvals_for_packet(packet)) == 2
    assert any("Recorded 2 signature(s)" in item.body for item in switch.messages(SCOPE))


def test_clerk_is_idempotent_across_polls(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SCOPE, "Dana", "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD")
    assert clerk.run_once()["recorded"] == 1
    second = clerk.run_once()
    assert second["recorded"] == 0
    assert second["scanned"] == 1  # one new Clerk status message, not a phrase
    packet = clerk.service.store.latest_packet(ENGAGEMENT_ID)
    assert len(clerk.service.store.approvals_for_packet(packet)) == 1


def test_clerk_answers_status_chatter(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SCOPE, "Dana", "SCOPE STATUS")
    clerk.run_once()
    bodies = [item.body for item in switch.messages(SCOPE)]
    assert any("Scope Guard status" in body for body in bodies)
    assert any("Packet v2" in body for body in bodies)


def test_clerk_seal_is_refused_until_the_gate_is_clear(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SCOPE, "Dana", "SCOPE SEAL")
    clerk.run_once()
    assert any("Cannot seal" in item.body for item in switch.messages(SCOPE))
    with pytest.raises(NotFoundError):
        clerk.service.store.latest_receipt(ENGAGEMENT_ID)


def test_clerk_seals_accepts_and_posts_the_handoff(clerk: Clerk, switch: FakeSwitch) -> None:
    sign_all(clerk.service)
    switch.human(SCOPE, "Dana", "SCOPE SEAL")
    clerk.run_once()
    receipt = clerk.service.store.latest_receipt(ENGAGEMENT_ID)
    assert receipt.body.receipt_id.startswith("co_")
    assert any("Sealed." in item.body for item in switch.messages(SCOPE))

    client = Keyring.generate()
    clerk.service.register_client(ENGAGEMENT_ID, CLIENT_SIGNER, client.public_key_b64)
    artifact = build_acceptance(receipt, client, CLIENT_SIGNER)
    switch.human(SCOPE, "Jordan", f"SCOPE ACCEPT {acceptance_token(artifact)}")
    clerk.run_once()
    accepted_receipt = clerk.service.store.latest_receipt(ENGAGEMENT_ID)
    assert accepted_receipt.acceptance is not None
    assert accepted_receipt.acceptance.issuer == CLIENT_SIGNER
    assert accepted_receipt.acceptance.receipt_id == receipt.body.receipt_id

    sales = [item.body for item in switch.messages(SCOPE)]
    delivery = [item.body for item in switch.messages(DELIVERY)]
    assert any("Accepted by Jordan Reyes" in body for body in sales)
    assert any("client key" in body for body in sales)
    assert any("Only the line items above leave this room." in body for body in delivery)
    assert any(receipt.body.receipt_id in body for body in delivery)


def test_clerk_handoff_before_sealing_is_clear(clerk: Clerk, switch: FakeSwitch) -> None:
    assert clerk.handoff_text().startswith("No handoff yet")
    switch.human(SCOPE, "Dana", "SCOPE HANDOFF")
    clerk.run_once()
    assert any("No handoff yet" in item.body for item in switch.messages(SCOPE))


def test_clerk_run_loops_the_requested_number_of_times(clerk: Clerk) -> None:
    calls: list[float] = []
    clerk.sleep = calls.append  # type: ignore[method-assign]
    clerk.run(iterations=3)
    assert calls == [clerk.interval, clerk.interval]


def test_seen_ids_are_not_reprocessed(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SCOPE, "Dana", "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD")
    first = clerk.run_once()
    clerk.seen.clear()
    second = clerk.run_once()
    assert first["recorded"] == 1
    # Clearing the seen set re-scans the original approval; the approval layer
    # itself is idempotent for the same actor.
    assert second["recorded"] == 1
    packet = clerk.service.store.latest_packet(ENGAGEMENT_ID)
    approvals = [
        item
        for item in clerk.service.store.approvals_for_packet(packet)
        if item.request_id == "MOBILE" and item.role == "ACCOUNT_LEAD"
    ]
    assert len(approvals) == 1
    assert approvals[0].status == ApprovalStatus.VALID.value


def test_clerk_status_view_reports_a_sealed_change_order(clerk: Clerk, switch: FakeSwitch) -> None:
    sign_all(clerk.service)
    clerk.service.seal(ENGAGEMENT_ID)
    text = clerk.status_text()
    assert "Change order:" in text
    assert "awaiting client" in text
    assert "Gate: **clear**." in text


def test_clerk_rejects_a_name_without_a_client_signature(clerk: Clerk, switch: FakeSwitch) -> None:
    sign_all(clerk.service)
    switch.human(SCOPE, "Dana", "SCOPE SEAL")
    clerk.run_once()
    switch.human(SCOPE, "Jordan", "SCOPE ACCEPT Jordan Reyes")
    result = clerk.run_once()
    assert result["recorded"] == 0
    bodies = [item.body for item in switch.messages(SCOPE)]
    assert any("A name alone is not a signature" in body for body in bodies)
    assert clerk.service.store.latest_receipt(ENGAGEMENT_ID).acceptance is None


def test_clerk_accept_without_a_sealed_change_order_is_reported(
    clerk: Clerk, switch: FakeSwitch
) -> None:
    switch.human(SCOPE, "Jordan", "SCOPE ACCEPT sgaccept1.bad")
    result = clerk.run_once()
    assert result["recorded"] == 0
    assert any("Acceptance not recorded" in item.body for item in switch.messages(SCOPE))
