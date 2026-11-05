"""The deterministic Clerk and the in-memory Switch room it drives."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from conftest import DEAL_ID, approve_all, prepare_v2
from vouch.errors import NotFoundError
from vouch.models import ApprovalStatus
from vouch.service import VouchService
from vouch.switch.clerk import Clerk
from vouch.switch.fake import FakeSwitch

SALES = "room-acme-deal"
DELIVERY = "room-acme-delivery"


class FakeClient:
    """The exact two-method surface Clerk needs, backed by FakeSwitch."""

    def __init__(self, switch: FakeSwitch) -> None:
        self.switch = switch

    def read_context(self, room_id: str, limit: int = 100) -> dict[str, Any]:
        return self.switch.read_context(room_id, limit=limit)

    def post_message(self, room_id: str, content: str) -> dict[str, Any]:
        return self.switch.post_message(room_id, content)


@pytest.fixture
def switch() -> FakeSwitch:
    value = FakeSwitch()
    value.create_room(SALES, "#deal-acme")
    value.create_room(DELIVERY, "#delivery-acme")
    return value


@pytest.fixture
def clerk(service: VouchService, switch: FakeSwitch) -> Clerk:
    prepare_v2(service)
    return Clerk(
        service=service,
        client=FakeClient(switch),  # type: ignore[arg-type]
        deal_id=DEAL_ID,
        sales_room_id=SALES,
        delivery_room_id=DELIVERY,
        sleep=lambda _: None,
    )


# --------------------------------------------------------------------- fake room
def test_fake_room_assigns_ids_timestamps_and_senders(switch: FakeSwitch) -> None:
    room = switch.room(SALES)
    first = switch.human(SALES, "Priya", "hello")
    second = switch.post(SALES, "Clerk", "hi")
    assert first.id == "room-acme-deal-m1"
    assert second.id == "room-acme-deal-m2"
    assert first.sender == "human-priya"
    assert second.sender == "agent-clerk"
    assert first.timestamp == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert room.next_sequence == 3


def test_fake_room_context_is_a_thread_per_message(switch: FakeSwitch) -> None:
    switch.human(SALES, "Priya", "one")
    switch.human(SALES, "Sam", "two")
    payload = switch.read_context(SALES)
    assert [item["root"]["body"] for item in payload["threads"]] == ["one", "two"]
    assert payload["truncated"] is False


def test_fake_room_context_honours_a_limit(switch: FakeSwitch) -> None:
    for index in range(5):
        switch.human(SALES, "Priya", str(index))
    payload = switch.read_context(SALES, limit=2)
    assert [item["root"]["body"] for item in payload["threads"]] == ["3", "4"]


def test_fake_switch_messages_can_be_filtered_since(switch: FakeSwitch) -> None:
    switch.room(SALES).add("Priya", "old", at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    latest = switch.room(SALES).add("Priya", "new", at=datetime(2026, 11, 4, 12, 1, tzinfo=UTC))
    assert [item.body for item in switch.messages(SALES, since=latest.timestamp_ms)] == ["new"]


# --------------------------------------------------------------------- clerk run
def test_clerk_records_exactly_the_approval_messages(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SALES, "Priya", "APPROVE ACME-2026 SSO AS AE")
    switch.human(SALES, "Sam", "APPROVE ACME-2026 SSO AS ENG_LEAD")
    switch.human(SALES, "Priya", "this is not an approval")
    result = clerk.run_once()
    assert result == {"scanned": 3, "recorded": 2, "ignored": 1}
    packet = clerk.service.store.latest_packet(DEAL_ID)
    assert len(clerk.service.store.approvals_for_packet(packet)) == 2
    # The Clerk posts a progress line back into the same room.
    assert any("Recorded 2 approval" in item.body for item in switch.messages(SALES))


def test_clerk_is_idempotent_across_polls(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SALES, "Priya", "APPROVE ACME-2026 SSO AS AE")
    assert clerk.run_once()["recorded"] == 1
    second = clerk.run_once()
    assert second["recorded"] == 0
    assert second["scanned"] == 1  # one new Clerk status message, not a phrase
    assert (
        len(clerk.service.store.approvals_for_packet(clerk.service.store.latest_packet(DEAL_ID)))
        == 1
    )


def test_clerk_answers_status_chatter(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SALES, "Maya", "VOUCH STATUS")
    clerk.run_once()
    bodies = [item.body for item in switch.messages(SALES)]
    assert any("Vouch status" in body for body in bodies)
    assert any("Packet v2" in body for body in bodies)


def test_clerk_seal_is_refused_until_the_gate_is_clear(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SALES, "Maya", "VOUCH SEAL")
    clerk.run_once()
    assert any("Cannot seal" in item.body for item in switch.messages(SALES))
    with pytest.raises(NotFoundError):
        clerk.service.store.latest_receipt(DEAL_ID)


def test_clerk_seals_and_posts_the_handoff(clerk: Clerk, switch: FakeSwitch) -> None:
    approve_all(clerk.service)
    switch.human(SALES, "Maya", "VOUCH SEAL")
    clerk.run_once()
    receipt = clerk.service.store.latest_receipt(DEAL_ID)
    assert receipt.body.receipt_id.startswith("vch_")
    sales = [item.body for item in switch.messages(SALES)]
    delivery = [item.body for item in switch.messages(DELIVERY)]
    assert any("Sealed." in body for body in sales)
    assert any("Receipt" in body for body in delivery)
    assert any("Only the commitments above leave this room." in body for body in delivery)


def test_clerk_handoff_before_sealing_is_clear(clerk: Clerk, switch: FakeSwitch) -> None:
    assert clerk.handoff_text().startswith("No handoff yet")
    switch.human(SALES, "Maya", "VOUCH HANDOFF")
    clerk.run_once()
    assert any("No handoff yet" in item.body for item in switch.messages(SALES))


def test_clerk_run_loops_the_requested_number_of_times(clerk: Clerk) -> None:
    calls: list[float] = []
    clerk.sleep = calls.append
    clerk.run(iterations=3)
    assert calls == [clerk.interval, clerk.interval]


def test_seen_ids_are_not_reprocessed(clerk: Clerk, switch: FakeSwitch) -> None:
    switch.human(SALES, "Priya", "APPROVE ACME-2026 SSO AS AE")
    first = clerk.run_once()
    clerk.seen.clear()
    second = clerk.run_once()
    assert first["recorded"] == 1
    # Clearing the seen set re-scans the original approval, but the approval
    # layer itself is idempotent for the same actor.
    assert second["recorded"] == 1
    assert (
        len(
            [
                item
                for item in clerk.service.store.approvals_for_packet(
                    clerk.service.store.latest_packet(DEAL_ID)
                )
                if item.claim_id == "SSO" and item.role == "AE"
            ]
        )
        == 1
    )
    assert all(
        item.status == ApprovalStatus.VALID
        for item in clerk.service.store.approvals_for_packet(
            clerk.service.store.latest_packet(DEAL_ID)
        )
    )
