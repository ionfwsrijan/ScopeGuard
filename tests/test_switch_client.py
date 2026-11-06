"""The real Switch HTTP client: auth, operations, errors, and message flattening."""

from __future__ import annotations

import json

import httpx
import pytest

from scopeguard.errors import SwitchError
from scopeguard.switch.client import SwitchClient, SwitchConfig, messages_from_context

CONFIG = SwitchConfig(
    endpoint="https://switch.example.test",
    token="sw_live_1234567890",
    agent_id="agent_clerk",
)


def make_client(handler: object) -> SwitchClient:
    return SwitchClient(CONFIG, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


# ----------------------------------------------------------------------- config
def test_config_reads_the_injected_environment() -> None:
    config = SwitchConfig.from_env(
        {
            "SWITCH_API_ENDPOINT": "https://switch.example.test/",
            "SWITCH_API_TOKEN": "token",
            "SWITCH_AGENT_ID": "agent_1",
        }
    )
    assert config.endpoint == "https://switch.example.test"
    assert config.token == "token"
    assert config.agent_id == "agent_1"


def test_config_falls_back_to_switch_api_url() -> None:
    config = SwitchConfig.from_env(
        {
            "SWITCH_API_URL": "http://localhost:8080",
            "SWITCH_API_TOKEN": "token",
            "SWITCH_AGENT_ID": "agent_1",
        }
    )
    assert config.endpoint == "http://localhost:8080"


def test_config_lists_every_missing_variable() -> None:
    with pytest.raises(SwitchError) as info:
        SwitchConfig.from_env({})
    assert "SWITCH_API_ENDPOINT" in info.value.message
    assert "SWITCH_API_TOKEN" in info.value.message
    assert "SWITCH_AGENT_ID" in info.value.message
    assert info.value.hint and "Console" in info.value.hint


def test_config_masks_tokens() -> None:
    assert CONFIG.masked_token == "sw_l...7890"
    assert SwitchConfig("e", "short", "a").masked_token == "***"


# ------------------------------------------------------------------- operations
def test_check_reads_the_operation_catalogue() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"operations": {"read_context": {}, "post_message": {}, "list_rooms": {}}},
        )

    with make_client(handler) as client:
        result = client.check()

    assert result == {
        "ok": True,
        "agent_id": "agent_clerk",
        "endpoint": "https://switch.example.test",
        "operations": 3,
        "has_read_context": True,
        "has_post_message": True,
    }
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/agents/agent_clerk/ops"
    assert seen[0].headers["authorization"] == "Bearer sw_live_1234567890"
    assert seen[0].headers["user-agent"] == "scopeguard/1.0"


def test_read_context_posts_arguments_and_room_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["room"] = request.headers.get("x-switch-room-id")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"threads": []})

    with make_client(handler) as client:
        payload = client.read_context("room-1", limit=25, since="2026-11-04T00:00:00Z")

    assert payload == {"threads": []}
    assert captured == {
        "path": "/agents/agent_clerk/ops/read_context",
        "room": None,
        "body": {"room_id": "room-1", "limit": 25, "since": "2026-11-04T00:00:00Z"},
    }


def test_call_op_forwards_an_explicit_room_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["room"] = request.headers.get("x-switch-room-id")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    with make_client(handler) as client:
        client.call_op("list_rooms", {"limit": 1}, room_id="room-9")

    assert captured == {"room": "room-9", "body": {"limit": 1}}


def test_post_message_uses_the_direct_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "event_id": "evt_1"})

    with make_client(handler) as client:
        result = client.post_message("room-1", "hello")

    assert result == {"ok": True, "event_id": "evt_1"}
    assert captured == {
        "path": "/agents/agent_clerk/message",
        "body": {"room_id": "room-1", "content": "hello"},
    }


def test_list_rooms_accepts_both_shapes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ops/list_rooms"):
            return httpx.Response(200, json={"rooms": [{"id": "a"}]})
        return httpx.Response(404)

    with make_client(handler) as client:
        assert client.list_rooms() == [{"id": "a"}]

    def bare(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "b"}])

    with make_client(bare) as client:
        assert client.list_rooms() == [{"id": "b"}]


# ----------------------------------------------------------------------- errors
def test_http_error_includes_the_switch_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "agent is not in the room"})

    with make_client(handler) as client, pytest.raises(SwitchError) as info:
        client.read_context("room-1")
    assert "HTTP 403" in info.value.message
    assert "agent is not in the room" in info.value.message
    assert info.value.hint and "agent key" in info.value.hint


def test_http_error_handles_a_non_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with make_client(handler) as client, pytest.raises(SwitchError) as info:
        client.list_ops()
    assert "bad gateway" in info.value.message


def test_transport_failure_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with make_client(handler) as client, pytest.raises(SwitchError) as info:
        client.list_ops()
    assert "Switch request failed" in info.value.message


def test_empty_success_is_an_empty_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with make_client(handler) as client:
        assert client.post_message("room-1", "hello") == {}


def test_an_injected_client_is_not_closed_by_switch_client() -> None:
    http = httpx.Client(
        base_url=CONFIG.endpoint, transport=httpx.MockTransport(lambda r: httpx.Response(204))
    )
    client = SwitchClient(CONFIG, client=http)
    client.close()
    # The caller owns the injected client; it must still be usable.
    assert not http.is_closed
    http.close()


# ------------------------------------------------------------- message flattening
def test_messages_from_context_flattens_threads_in_time_order() -> None:
    payload = {
        "threads": [
            {
                "root": {
                    "id": "m2",
                    "sender": "user-priya",
                    "sender_name": "Priya",
                    "body": "second",
                    "timestamp": 2000,
                },
                "replies": [
                    {
                        "id": "m3",
                        "sender": "agent-clerk",
                        "sender_name": "Clerk",
                        "body": "third",
                        "timestamp": 3000,
                    }
                ],
            },
            {
                "root": {
                    "id": "m1",
                    "sender": "user-sam",
                    "sender_name": "Sam",
                    "body": "first",
                    "timestamp": 1000,
                }
            },
        ]
    }
    messages = messages_from_context(payload, "room-1")
    assert [item.id for item in messages] == ["m1", "m2", "m3"]
    assert all(item.room_id == "room-1" for item in messages)
    assert messages[2].sender_name == "Clerk"


def test_messages_from_context_tolerates_missing_fields() -> None:
    messages = messages_from_context(
        {"threads": [{"root": {"body": "hello"}}, "not a thread"]}, "room-1"
    )
    assert len(messages) == 1
    assert messages[0].sender_name == "unknown"
    assert messages[0].id == ""


def test_messages_from_context_rejects_a_malformed_thread_list() -> None:
    with pytest.raises(SwitchError):
        messages_from_context({"threads": "not-a-list"}, "room-1")
