"""The Clerk: a deterministic, headless Switch participant.

It polls a room it is a member of, records exact signature phrases, reports the
gate, seals when the humans have finished, records the client's acceptance, and
posts the signed change order into the linked delivery room. It never invents an
approval and never decides a commercial question.

A managed LLM agent can do the same job conversationally. This class exists so
the last mile is reproducible and testable, and so a team can run Scope Guard
without an LLM in the approval path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from scopeguard.errors import GateError, ScopeError, StateError
from scopeguard.models import SwitchMessage
from scopeguard.service import ScopeService
from scopeguard.switch.client import messages_from_context


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
    service: ScopeService
    client: RoomClient
    engagement_id: str
    scope_room_id: str
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
        payload = self.client.read_context(self.scope_room_id, limit=100)
        messages = self._unseen(messages_from_context(payload, self.scope_room_id))
        resolved = self.service.sync_messages(
            self.engagement_id, messages, room_id=self.scope_room_id
        )
        commands = [item for item in messages if item.sender_name != self.agent_name]
        for message in commands:
            text = message.body.strip()
            upper = text.upper()
            if upper.startswith("SCOPE ACCEPT"):
                issuer = text[len("SCOPE ACCEPT") :].strip().strip(":") or message.sender_name
                self.client.post_message(self.scope_room_id, self.accept_text(issuer))
            elif upper.startswith("SCOPE SEAL") or upper == "SCOPE HANDOFF":
                if upper.startswith("SCOPE HANDOFF"):
                    self.client.post_message(self.scope_room_id, self.handoff_text())
                else:
                    self.client.post_message(self.scope_room_id, self.seal_text())
            elif upper.startswith("SCOPE STATUS") or upper.startswith("@CLERK STATUS"):
                self.client.post_message(self.scope_room_id, self.status_text())
        if resolved.recorded:
            pending = self.service.pending(self.engagement_id)
            packet = self.service.store.latest_packet(self.engagement_id)
            line = (
                f"Recorded {resolved.recorded} signature(s) for packet hash "
                f"{packet.packet_hash[:12]}."
            )
            if pending:
                line += " Still pending: " + ", ".join(pending) + "."
            else:
                line += " The gate is clear. Say `SCOPE SEAL` and I will issue the change order."
            self.client.post_message(self.scope_room_id, line)
        for error in resolved.errors:
            self.client.post_message(self.scope_room_id, f"Ignored a signature: {error}")
        return {
            "scanned": resolved.scanned,
            "recorded": resolved.recorded,
            "ignored": resolved.ignored,
        }

    def status_text(self) -> str:
        status = self.service.status(self.engagement_id)
        packet = status["packet"]
        gate = status["gate"]
        approvals = status["approvals"]
        valid = [item for item in approvals if item.status == "valid"]
        stale = [item for item in approvals if item.status == "stale"]
        lines = [
            f"**Scope Guard status - {self.engagement_id}**",
            f"Packet v{packet.version} `{packet.packet_hash[:16]}...` - **{packet.status}**",
            f"Subtotal {packet.subtotal:,.2f} {packet.currency}; "
            f"discount {packet.discount_total:,.2f}; total {packet.total:,.2f}.",
            "",
        ]
        for decision in packet.decisions:
            lines.append(f"- `{decision.request_id}` {decision.verdict}: {decision.priced_text}")
        lines += [
            "",
            f"Signatures: {len(valid)} valid, {len(stale)} stale.",
        ]
        if gate.pending:
            lines.append("Pending: " + ", ".join(gate.pending))
        else:
            lines.append("Gate: **clear**." if gate.ok else "Gate: blocked.")
        receipt = status["receipt"]
        if receipt is not None:
            accepted = (
                receipt.acceptance.issuer if receipt.acceptance is not None else "awaiting client"
            )
            lines.append(f"Change order: `{receipt.body.receipt_id}` ({accepted})")
        return "\n".join(lines)

    def seal_text(self) -> str:
        try:
            receipt = self.service.seal(self.engagement_id)
        except GateError as exc:
            return f"**Cannot seal.** {exc.message}\n\nPending: " + ", ".join(
                self.service.pending(self.engagement_id)
            )
        except StateError as exc:
            return f"**No new change order.** {exc.message}"
        lines = [
            f"**Sealed.** Change order `{receipt.body.receipt_id}`",
            f"Total **{receipt.body.total:,.2f} {receipt.body.currency}** "
            f"(subtotal {receipt.body.subtotal:,.2f}, "
            f"discount {receipt.body.discount_total:,.2f}).",
            f"Body hash `{receipt.body_hash[:24]}...`",
            f"Verify locally: `scopeguard verify --receipt {receipt.body.receipt_id}`",
            "Client, reply `SCOPE ACCEPT <your name>` to countersign. "
            "Nothing crosses into delivery until the client accepts.",
        ]
        return "\n".join(lines)

    def accept_text(self, issuer: str) -> str:
        try:
            receipt = self.service.countersign(self.engagement_id, issuer)
        except ScopeError as exc:
            return f"**Acceptance not recorded.** {exc.message}"
        lines = [
            f"**Accepted by {receipt.acceptance.issuer if receipt.acceptance else issuer}.** "
            f"Change order `{receipt.body.receipt_id}` is now two-party signed.",
        ]
        if self.delivery_room_id:
            self.client.post_message(self.delivery_room_id, self.handoff_text())
            lines.append(f"Signed change order handed to {self.delivery_room_id}.")
        else:
            lines.append("No delivery room is linked; the handoff is ready to post.")
        return "\n".join(lines)

    def handoff_text(self) -> str:
        try:
            text = self.service.handoff_text(self.engagement_id)
        except ScopeError as exc:
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
