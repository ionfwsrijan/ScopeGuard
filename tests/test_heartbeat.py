"""Tests for the optional Render free-tier keep-alive."""

from __future__ import annotations

import httpx
import pytest

from scopeguard.heartbeat import (
    HEARTBEAT_INTERVAL_SECONDS,
    ping_once,
    render_health_url,
)


def test_render_health_url_requires_explicit_opt_in() -> None:
    assert render_health_url({"RENDER_EXTERNAL_URL": "https://scopeguard.onrender.com"}) is None


def test_render_health_url_uses_render_service_url() -> None:
    assert (
        render_health_url(
            {
                "ENABLE_HEARTBEAT": "true",
                "RENDER_EXTERNAL_URL": "https://scopeguard.onrender.com/",
            }
        )
        == "https://scopeguard.onrender.com/api/health"
    )


def test_render_health_url_is_disabled_without_render_url() -> None:
    assert render_health_url({"ENABLE_HEARTBEAT": "true"}) is None


def test_heartbeat_interval_stays_inside_render_idle_window() -> None:
    assert HEARTBEAT_INTERVAL_SECONDS == 12 * 60
    assert HEARTBEAT_INTERVAL_SECONDS < 15 * 60


@pytest.mark.asyncio
async def test_ping_once_checks_health_response() -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "audit_ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ping_once(client, "https://scopeguard.onrender.com/api/health")

    assert result is True
    assert requested == ["https://scopeguard.onrender.com/api/health"]


@pytest.mark.asyncio
async def test_ping_once_reports_unhealthy_response() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        result = await ping_once(client, "https://scopeguard.onrender.com/api/health")

    assert result is False
