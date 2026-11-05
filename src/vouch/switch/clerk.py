"""The Clerk: a deterministic, headless Switch participant.

It polls a room it is a member of, records exact approval phrases, reports the
gate, seals when the humans have finished, and posts the approved commitments
into the linked delivery room. It never invents an approval and never decides a
commercial question.

A managed LLM agent can do the same job conversationally. This class exists so
the last mile is reproducible and testable, and so a team can run Vouch without
an LLM in the approval path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from vouch.errors import GateError, StateError, VouchError
from vouch.models import SwitchMessage
from vouch.service import VouchService
from vouch.switch.client import messages_from_context


class RoomClient(Protocol):
    """The only two Switch operations the Clerk needs.

    ``SwitchClient`` implements this over HTTP; ``FakeSwitch`` implements it in
    memory. Keeping the protocol narrow means the Clerk is testable without
    mocking an HTTP library.
    """

    def read_context(self, room_id: str, *, limit: int = 100) -> dict[str, Any]: ...

    def post_message(self, room_id: str, content: str) -> dict[str, Any]: ...


@dataclass
class Clerk:
    service: VouchService
    client: RoomClient
    deal_id: str
    sales_room_id: str
    delivery_room_id: str | None = None
    agent_name: str = "Clerk"
    seen: set[str] = field(default_factory=set)
    interval: float = 3.0
    sleep: Callable[[float], None] = time.sleep

    def _unseen(self, messages: list[SwitchMessage]) -> list[SwitchMessage]:
        fresh = [item for item in messages if item.id not in self.seen]
        for item in fresh:
            self.seen.add(item.id)
        return fresh

    def run_once(self) -> dict[str, int]:
        payload = self.client.read_context(self.sales_room_id, limit=100)
        messages = self._unseen(messages_from_context(payload, self.sales_room_id))
        resolved = self.service.sync_messages(self.deal_id, messages, room_id=self.sales_room_id)
        commands = [item for item in messages if item.sender_name != self.agent_name]
        for message in commands:
            text = message.body.casefold()
            if "vouch status" in text or "@clerk status" in text:
                self.client.post_message(self.sales_room_id, self.status_text())
            elif "vouch seal" in text or "@clerk seal" in text:
                self.client.post_message(self.sales_room_id, self.seal_text())
            elif "vouch handoff" in text or "@clerk handoff" in text:
                self.client.post_message(self.sales_room_id, self.handoff_text())
        if resolved.recorded:
            pending = self.service.pending(self.deal_id)
            line = f"Recorded {resolved.recorded} approval(s) for packet hash {self.service.store.latest_packet(self.deal_id).packet_hash[:12]}."
            if pending:
                line += " Still pending: " + ", ".join(pending) + "."
            else:
                line += " The gate is clear. Say `VOUCH SEAL` and I will issue the receipt."
            self.client.post_message(self.sales_room_id, line)
        return {
            "scanned": resolved.scanned,
            "recorded": resolved.recorded,
            "ignored": resolved.ignored,
        }

    def status_text(self) -> str:
        status = self.service.status(self.deal_id)
        packet = status["packet"]
        gate = status["gate"]
        approvals = status["approvals"]
        lines = [
            f"**Vouch status - {self.deal_id}**",
            f"Packet v{packet.version} `{packet.packet_hash[:16]}...` - **{packet.status}**",
            "",
        ]
        for decision in packet.decisions:
            lines.append(f"- `{decision.claim_id}` {decision.verdict}: {decision.approved_text}")
        valid = [item for item in approvals if item.status == "valid"]
        stale = [item for item in approvals if item.status == "stale"]
        lines += [
            "",
            f"Approvals: {len(valid)} valid, {len(stale)} stale.",
        ]
        if gate.pending:
            lines.append("Pending: " + ", ".join(gate.pending))
        else:
            lines.append("Gate: **clear**." if gate.ok else "Gate: blocked.")
        receipt = status["receipt"]
        if receipt is not None:
            lines.append(f"Receipt: `{receipt.body.receipt_id}`")
        return "\n".join(lines)

    def seal_text(self) -> str:
        try:
            receipt = self.service.seal(self.deal_id)
        except GateError as exc:
            return f"**Cannot seal.** {exc.message}\n\nPending: " + ", ".join(
                self.service.pending(self.deal_id)
            )
        except StateError as exc:
            return f"**No new receipt.** {exc.message}"
        lines = [
            f"**Sealed.** Receipt `{receipt.body.receipt_id}`",
            f"Body hash `{receipt.body_hash[:24]}...`",
            f"Verify locally: `vouch verify --receipt {receipt.body.receipt_id}`",
        ]
        if self.delivery_room_id:
            self.client.post_message(self.delivery_room_id, self.handoff_text())
            lines.append(f"Approved commitments handed to {self.delivery_room_id}.")
        return "\n".join(lines)

    def handoff_text(self) -> str:
        try:
            text = self.service.handoff_text(self.deal_id)
        except VouchError as exc:
            return f"No handoff yet: {exc.message}"
        if self.delivery_room_id:
            self.client.post_message(self.delivery_room_id, text)
            return f"Handoff posted to {self.delivery_room_id}."
        return text

    def run(self, *, iterations: int | None = None) -> None:
        count = 0
        while iterations is None or count < iterations:
            self.run_once()
            count += 1
            if iterations is None or count < iterations:
                self.sleep(self.interval)
