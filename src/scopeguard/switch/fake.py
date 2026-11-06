"""A deterministic in-memory Switch room for demos, tests, and dry runs.

This is not a replacement for Switch. It implements only the transport
surface the workflow uses, so the same service code can be exercised offline.
The README and docs label it honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from scopeguard.models import SwitchMessage


@dataclass
class FakeRoom:
    id: str
    name: str
    instruction: str = ""
    messages: list[SwitchMessage] = field(default_factory=list)
    next_sequence: int = 1

    def add(
        self,
        sender_name: str,
        body: str,
        *,
        sender: str | None = None,
        at: datetime | None = None,
    ) -> SwitchMessage:
        moment = at or datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
        message = SwitchMessage(
            id=f"{self.id}-m{self.next_sequence}",
            room_id=self.id,
            sender=sender or f"user-{sender_name.lower().replace(' ', '-')}",
            sender_name=sender_name,
            body=body,
            timestamp_ms=int(moment.timestamp() * 1000),
        )
        self.next_sequence += 1
        self.messages.append(message)
        return message

    def context(self, limit: int = 100) -> dict[str, Any]:
        entries = [
            {
                "id": item.id,
                "kind": "message",
                "sender": item.sender,
                "sender_name": item.sender_name,
                "body": item.body,
                "timestamp": item.timestamp_ms,
                "attachments": [],
            }
            for item in self.messages[-limit:]
        ]
        return {
            "threads": [{"root": entry, "replies": []} for entry in entries],
            "truncated": False,
            "oldest_timestamp": entries[0]["timestamp"] if entries else None,
        }


class FakeSwitch:
    """Two linked rooms, a handful of humans, and the agents as participants."""

    def __init__(self) -> None:
        self.rooms: dict[str, FakeRoom] = {}

    def create_room(self, room_id: str, name: str, instruction: str = "") -> FakeRoom:
        room = FakeRoom(id=room_id, name=name, instruction=instruction)
        self.rooms[room_id] = room
        return room

    def room(self, room_id: str) -> FakeRoom:
        if room_id not in self.rooms:
            self.create_room(room_id, room_id.lstrip("#"))
        return self.rooms[room_id]

    def post(self, room_id: str, agent: str, body: str) -> SwitchMessage:
        return self.room(room_id).add(agent, body, sender=f"agent-{agent.lower()}")

    def post_message(self, room_id: str, content: str, agent: str = "Clerk") -> dict[str, object]:
        message = self.post(room_id, agent, content)
        return {"ok": True, "event_id": message.id}

    def human(self, room_id: str, name: str, body: str) -> SwitchMessage:
        return self.room(room_id).add(name, body, sender=f"human-{name.lower()}")

    def read_context(self, room_id: str, limit: int = 100) -> dict[str, Any]:
        return self.room(room_id).context(limit=limit)

    def messages(self, room_id: str, *, since: int = 0) -> list[SwitchMessage]:
        return [item for item in self.room(room_id).messages if item.timestamp_ms >= since]
