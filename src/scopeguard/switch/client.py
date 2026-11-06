"""Switch HTTP client for the small set of operations Scope Guard needs.

Switch injects ``SWITCH_API_ENDPOINT``, ``SWITCH_API_TOKEN`` and
``SWITCH_AGENT_ID`` into a managed agent. The same client also works for a
headless Clerk process, as long as it is given that agent's key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from scopeguard.errors import SwitchError
from scopeguard.models import SwitchMessage


@dataclass(frozen=True)
class SwitchConfig:
    endpoint: str
    token: str
    agent_id: str
    timeout: float = 20.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SwitchConfig:
        values = env or os.environ
        endpoint = (values.get("SWITCH_API_ENDPOINT") or values.get("SWITCH_API_URL") or "").rstrip(
            "/"
        )
        token = values.get("SWITCH_API_TOKEN", "")
        agent_id = values.get("SWITCH_AGENT_ID", "")
        missing = [
            name
            for name, value in (
                ("SWITCH_API_ENDPOINT", endpoint),
                ("SWITCH_API_TOKEN", token),
                ("SWITCH_AGENT_ID", agent_id),
            )
            if not value
        ]
        if missing:
            raise SwitchError(
                "missing Switch environment: " + ", ".join(missing),
                hint="Switch Console injects these into a managed agent. See docs/SWITCH.md.",
            )
        return cls(endpoint=endpoint, token=token, agent_id=agent_id)

    @property
    def masked_token(self) -> str:
        if len(self.token) <= 8:
            return "***"
        return self.token[:4] + "..." + self.token[-4:]


class SwitchClient:
    def __init__(
        self,
        config: SwitchConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self.http = client or httpx.Client(
            base_url=config.endpoint,
            timeout=config.timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {config.token}",
                "Content-Type": "application/json",
                "User-Agent": "scopeguard/1.0",
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self.http.close()

    def __enter__(self) -> SwitchClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SwitchError(f"Switch request failed: {exc}") from exc
        if response.status_code >= 400:
            detail: Any
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:500]
            raise SwitchError(
                f"Switch returned HTTP {response.status_code} for {method} {path}: {detail}",
                hint="Check that the agent key belongs to this server and the room id exists.",
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def call_op(
        self,
        operation: str,
        arguments: dict[str, Any] | None = None,
        *,
        room_id: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if room_id:
            headers["X-Switch-Room-Id"] = room_id
        return self._request(
            "POST",
            f"/agents/{self.config.agent_id}/ops/{operation}",
            json=arguments or {},
            headers=headers,
        )

    def list_ops(self) -> dict[str, Any]:
        return self._request("GET", f"/agents/{self.config.agent_id}/ops")

    def check(self) -> dict[str, Any]:
        ops = self.list_ops().get("operations", {})
        return {
            "ok": True,
            "agent_id": self.config.agent_id,
            "endpoint": self.config.endpoint,
            "operations": len(ops),
            "has_read_context": "read_context" in ops,
            "has_post_message": "post_message" in ops,
        }

    def read_context(
        self,
        room_id: str,
        *,
        limit: int = 100,
        since: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"room_id": room_id, "limit": limit}
        if since:
            args["since"] = since
        return self.call_op("read_context", args)

    def post_message(self, room_id: str, content: str) -> dict[str, Any]:
        # This endpoint does not require a live WebSocket, which is what makes
        # a headless deterministic Clerk possible.
        return self._request(
            "POST",
            f"/agents/{self.config.agent_id}/message",
            json={"room_id": room_id, "content": content},
        )

    def list_rooms(self) -> list[dict[str, Any]]:
        result = self.call_op("list_rooms")
        if isinstance(result, list):
            return result
        return list(result.get("rooms", []))


def messages_from_context(payload: dict[str, Any], room_id: str) -> list[SwitchMessage]:
    """Flatten Switch's thread grouping into a stable timeline."""
    messages: list[SwitchMessage] = []
    threads = payload.get("threads", [])
    if not isinstance(threads, list):
        raise SwitchError("read_context returned no thread list")
    for thread in threads:
        entries: list[dict[str, Any]] = []
        root = thread.get("root") if isinstance(thread, dict) else None
        if isinstance(root, dict):
            entries.append(root)
        replies = thread.get("replies") if isinstance(thread, dict) else None
        if isinstance(replies, list):
            entries.extend(item for item in replies if isinstance(item, dict))
        for entry in entries:
            body = str(entry.get("body") or "")
            sender_name = str(entry.get("sender_name") or entry.get("sender") or "unknown")
            messages.append(
                SwitchMessage(
                    id=str(entry.get("id") or ""),
                    room_id=room_id,
                    sender=str(entry.get("sender") or ""),
                    sender_name=sender_name,
                    body=body,
                    timestamp_ms=int(entry.get("timestamp") or 0),
                )
            )
    messages.sort(key=lambda item: (item.timestamp_ms, item.id))
    return messages
