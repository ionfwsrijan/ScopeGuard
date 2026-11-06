"""Opt-in keep-alive pings for a single-service Render demo deployment."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping

import httpx

logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 12 * 60


def render_health_url(env: Mapping[str, str] | None = None) -> str | None:
    """Return the Render health URL only when keep-alive is explicitly enabled."""
    values = os.environ if env is None else env
    if values.get("ENABLE_HEARTBEAT", "").strip().lower() != "true":
        return None
    base_url = values.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/api/health"


async def ping_once(client: httpx.AsyncClient, url: str) -> bool:
    """Ping the health route; return false and log on HTTP/network failures."""
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Render keep-alive ping failed: %s", exc)
        return False
    logger.info("Render keep-alive ping succeeded")
    return True


async def keep_render_awake(
    url: str,
    *,
    interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Send a low-volume health request every 12 minutes until shutdown."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            await asyncio.sleep(interval_seconds)
            await ping_once(client, url)
