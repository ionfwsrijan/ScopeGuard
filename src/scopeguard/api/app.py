"""Read-only FastAPI surface for the ledger and change-order verifier.

The UI is intentionally read-only: signatures belong in Switch, not in a web
form that could be replayed. The one action here is verification, which is pure
and can be done by anyone holding a change order.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from scopeguard import __version__
from scopeguard.errors import NotFoundError, ScopeError
from scopeguard.heartbeat import keep_render_awake, render_health_url
from scopeguard.models import Receipt
from scopeguard.service import ScopePaths, ScopeService
from scopeguard.switch.client import SwitchClient, SwitchConfig

PACKAGE = Path(__file__).resolve().parent
FRONTEND_INDEX = PACKAGE / "static" / "app" / "index.html"
logger = logging.getLogger(__name__)


def frontend_page() -> FileResponse:
    if not FRONTEND_INDEX.is_file():
        raise HTTPException(
            status_code=503,
            detail="React frontend is not built. Run `npm --prefix frontend run build` first.",
        )
    return FileResponse(str(FRONTEND_INDEX), media_type="text/html")


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the optional Render free-tier keep-alive task and stop it cleanly."""
    enabled = os.environ.get("ENABLE_HEARTBEAT", "").strip().lower() == "true"
    health_url = render_health_url()
    heartbeat_task: asyncio.Task[None] | None = None
    if health_url:
        heartbeat_task = asyncio.create_task(keep_render_awake(health_url))
        logger.info("Render keep-alive enabled (12-minute interval)")
    elif enabled:
        logger.warning("ENABLE_HEARTBEAT is true but RENDER_EXTERNAL_URL is missing")
    try:
        yield
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


def create_app(
    *,
    root: Path | None = None,
    home: Path | None = None,
    fixture: str = "northwind",
) -> FastAPI:
    app = FastAPI(
        title="Scope Guard",
        version=__version__,
        description="Change-order ledger and two-party signature verifier.",
        lifespan=_lifespan,
    )
    paths = ScopePaths.discover(root=root, home=home, fixture=fixture)
    paths.ensure()
    app.state.paths = paths
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")

    def get_service() -> Iterator[ScopeService]:
        service = ScopeService.open(paths)
        try:
            yield service
        finally:
            service.close()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> FileResponse:
        return frontend_page()

    @app.get("/engagements/{engagement_id}", response_class=HTMLResponse)
    def engagement_page(
        engagement_id: str, service: ScopeService = Depends(get_service)
    ) -> FileResponse:
        try:
            service.status(engagement_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return frontend_page()

    @app.get("/change-orders/{receipt_id}", response_class=HTMLResponse)
    def receipt_page(
        receipt_id: str, service: ScopeService = Depends(get_service)
    ) -> FileResponse:
        try:
            service.store.load_receipt(receipt_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return frontend_page()

    @app.get("/engagements/{engagement_id}/change-order", response_class=HTMLResponse)
    def latest_receipt_page(
        engagement_id: str, service: ScopeService = Depends(get_service)
    ) -> FileResponse:
        try:
            service.store.latest_receipt(engagement_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return frontend_page()

    @app.get("/verify", response_class=HTMLResponse)
    def verify_form() -> FileResponse:
        return frontend_page()

    @app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    def demo_page() -> FileResponse:
        return frontend_page()

    @app.get("/demo-video", include_in_schema=False)
    def demo_video() -> FileResponse:
        video = Path.cwd() / "demo" / "video" / "scopeguard-demo-youtube.mp4"
        if not video.is_file():
            raise HTTPException(status_code=404, detail="Demo video is unavailable.")
        return FileResponse(str(video), media_type="video/mp4")

    @app.post("/verify", response_class=HTMLResponse, include_in_schema=False)
    def legacy_verify_submit() -> FileResponse:
        return frontend_page()

    # ------------------------------------------------------------------ API
    @app.get("/api/health")
    def health(service: ScopeService = Depends(get_service)) -> dict[str, Any]:
        audit_ok, audit_detail = service.store.verify_audit()
        return {
            "ok": True,
            "version": __version__,
            "audit_ok": audit_ok,
            "audit": audit_detail,
            "stats": service.store.stats(),
        }

    @app.get("/api/engagements")
    def api_engagements(service: ScopeService = Depends(get_service)) -> list[dict[str, Any]]:
        return [
            {
                "engagement": engagement.model_dump(mode="json"),
                "packets": service.store.count_packets(engagement.id),
                "change_orders": len(service.store.list_receipts(engagement.id)),
            }
            for engagement in service.store.list_engagements()
        ]

    @app.get("/api/engagements/{engagement_id}")
    def api_engagement(
        engagement_id: str, service: ScopeService = Depends(get_service)
    ) -> dict[str, Any]:
        try:
            status = service.status(engagement_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        result = service.verify(status["receipt"]) if status["receipt"] else None
        return {
            "engagement": status["engagement"].model_dump(mode="json"),
            "packet": status["packet"].model_dump(mode="json"),
            "approvals": [item.model_dump(mode="json") for item in status["approvals"]],
            "gate": status["gate"].model_dump(mode="json"),
            "receipt": status["receipt"].model_dump(mode="json") if status["receipt"] else None,
            "verification": result.model_dump(mode="json") if result else None,
            "evidence_count": status["evidence_count"],
            "packet_count": status["packet_count"],
            "handoff": service.handoff_text(engagement_id) if status["receipt"] else None,
        }

    @app.get("/api/change-orders")
    def api_receipts(service: ScopeService = Depends(get_service)) -> list[dict[str, Any]]:
        return [receipt.model_dump(mode="json") for receipt in service.store.list_receipts()]

    @app.get("/api/change-orders/{receipt_id}")
    def api_receipt(
        receipt_id: str, service: ScopeService = Depends(get_service)
    ) -> dict[str, Any]:
        try:
            receipt = service.store.load_receipt(receipt_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return receipt.model_dump(mode="json")

    @app.get("/api/change-orders/{receipt_id}/verify")
    def api_verify(receipt_id: str, service: ScopeService = Depends(get_service)) -> dict[str, Any]:
        try:
            receipt = service.store.load_receipt(receipt_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return service.verify(receipt).model_dump(mode="json")

    @app.post("/api/verify")
    def api_verify_body(
        payload: dict[str, Any], service: ScopeService = Depends(get_service)
    ) -> JSONResponse:
        try:
            receipt = Receipt.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "error": "invalid change order",
                    "detail": json.loads(exc.json()),
                },
            )
        result = service.verify(receipt)
        return JSONResponse(
            status_code=200 if result.ok else 400, content=result.model_dump(mode="json")
        )

    @app.get("/api/audit")
    def api_audit(service: ScopeService = Depends(get_service)) -> dict[str, Any]:
        ok, detail = service.store.verify_audit()
        return {"ok": ok, "detail": detail, "events": service.store.list_audit()}

    @app.exception_handler(ScopeError)
    def scopeguard_error(request: Request, exc: ScopeError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": exc.code, "message": exc.message, "hint": exc.hint},
        )

    @app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
    def frontend_route(path: str) -> FileResponse:
        if path.startswith(("api/", "static/")):
            raise HTTPException(status_code=404, detail="Not found")
        return frontend_page()

    return app


def _env_switch() -> dict[str, Any]:
    try:
        config = SwitchConfig.from_env()
        with SwitchClient(config) as client:
            return client.check()
    except ScopeError as exc:
        return {"ok": False, "error": exc.message}


app = create_app()
