"""Read-only FastAPI surface for the ledger and change-order verifier.

The UI is intentionally read-only: signatures belong in Switch, not in a web
form that could be replayed. The one action here is verification, which is pure
and can be done by anyone holding a change order.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from scopeguard import __version__
from scopeguard.errors import NotFoundError, ScopeError
from scopeguard.models import Receipt
from scopeguard.service import ScopePaths, ScopeService
from scopeguard.switch.client import SwitchClient, SwitchConfig

PACKAGE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE / "templates"))


def _verdict_class(value: str) -> str:
    return {"approve": "ok", "narrow": "warn", "refuse": "bad", "escalate": "bad"}.get(value, "")


def _severity_class(value: str) -> str:
    return {"block": "bad", "warn": "warn", "note": "muted"}.get(value, "")


def _short(value: str | None, length: int = 14) -> str:
    return (value or "")[:length]


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _signed(value: Any) -> str:
    try:
        return f"{float(value):+,.2f}"
    except (TypeError, ValueError):
        return str(value)


TEMPLATES.env.filters["verdict_class"] = _verdict_class
TEMPLATES.env.filters["severity_class"] = _severity_class
TEMPLATES.env.filters["short"] = _short
TEMPLATES.env.filters["money"] = _money
TEMPLATES.env.filters["signed"] = _signed


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

    def case_view(service: ScopeService, engagement_id: str) -> dict[str, Any]:
        status = service.status(engagement_id)
        packet = status["packet"]
        receipt = status["receipt"]
        verification = None
        if receipt is not None:
            verification = service.verify(receipt)
        return {
            "engagement": status["engagement"],
            "packet": packet,
            "approvals": status["approvals"],
            "gate": status["gate"],
            "receipt": receipt,
            "verification": verification,
            "evidence_count": status["evidence_count"],
            "packet_count": status["packet_count"],
            "pending": status["gate"].pending,
            "valid_approvals": [item for item in status["approvals"] if item.status == "valid"],
            "stale_approvals": [item for item in status["approvals"] if item.status == "stale"],
            "blocks": [item for item in packet.findings if item.severity == "block"],
            "warnings": [item for item in packet.findings if item.severity == "warn"],
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, service: ScopeService = Depends(get_service)) -> HTMLResponse:
        cases: list[dict[str, Any]] = []
        for engagement in service.store.list_engagements():
            try:
                cases.append(case_view(service, engagement.id))
            except NotFoundError:
                cases.append(
                    {
                        "engagement": engagement,
                        "packet": None,
                        "receipt": None,
                        "gate": None,
                        "pending": [],
                        "blocks": [],
                        "warnings": [],
                        "valid_approvals": [],
                        "stale_approvals": [],
                        "verification": None,
                        "evidence_count": len(service.store.list_evidence(engagement.id)),
                        "packet_count": 0,
                    }
                )
        audit_ok, audit_detail = service.store.verify_audit()
        receipts = service.store.list_receipts()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "cases": cases,
                "receipts": receipts[:4],
                "audit_ok": audit_ok,
                "audit_detail": audit_detail,
                "stats": service.store.stats(),
                "version": __version__,
            },
        )

    @app.get("/engagements/{engagement_id}", response_class=HTMLResponse)
    def engagement_page(
        request: Request, engagement_id: str, service: ScopeService = Depends(get_service)
    ) -> HTMLResponse:
        try:
            case = case_view(service, engagement_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        audit = service.store.list_audit()[-12:]
        handoff = None
        if case["receipt"] is not None:
            handoff = service.handoff_text(engagement_id)
        return TEMPLATES.TemplateResponse(
            request,
            "engagement.html",
            {"case": case, "audit": audit, "handoff": handoff, "version": __version__},
        )

    @app.get("/change-orders/{receipt_id}", response_class=HTMLResponse)
    def receipt_page(
        request: Request, receipt_id: str, service: ScopeService = Depends(get_service)
    ) -> HTMLResponse:
        try:
            receipt = service.store.load_receipt(receipt_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        result = service.verify(receipt)
        return TEMPLATES.TemplateResponse(
            request,
            "receipt.html",
            {"receipt": receipt, "result": result, "version": __version__},
        )

    @app.get("/engagements/{engagement_id}/change-order", response_class=HTMLResponse)
    def latest_receipt_page(
        request: Request, engagement_id: str, service: ScopeService = Depends(get_service)
    ) -> HTMLResponse:
        try:
            receipt = service.store.latest_receipt(engagement_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        result = service.verify(receipt)
        return TEMPLATES.TemplateResponse(
            request,
            "receipt.html",
            {"receipt": receipt, "result": result, "version": __version__},
        )

    @app.get("/verify", response_class=HTMLResponse)
    def verify_form(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "verify.html",
            {"result": None, "receipt": None, "error": None, "version": __version__},
        )

    @app.post("/verify", response_class=HTMLResponse)
    async def verify_submit(
        request: Request, service: ScopeService = Depends(get_service)
    ) -> HTMLResponse:
        form = await request.form()
        raw = str(form.get("receipt") or "").strip()
        receipt = None
        result = None
        error = None
        if not raw:
            error = "Paste a change-order JSON document."
        else:
            try:
                receipt = Receipt.model_validate_json(raw)
                result = service.verify(receipt)
            except (ValidationError, ValueError) as exc:
                error = f"That is not a valid Scope Guard change order: {exc}"
        return TEMPLATES.TemplateResponse(
            request,
            "verify.html",
            {
                "result": result,
                "receipt": receipt,
                "error": error,
                "raw": raw,
                "version": __version__,
            },
        )

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
        return {
            "engagement": status["engagement"].model_dump(mode="json"),
            "packet": status["packet"].model_dump(mode="json"),
            "approvals": [item.model_dump(mode="json") for item in status["approvals"]],
            "gate": status["gate"].model_dump(mode="json"),
            "receipt": status["receipt"].model_dump(mode="json") if status["receipt"] else None,
            "handoff": service.handoff_text(engagement_id) if status["receipt"] else None,
        }

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

    return app


def _env_switch() -> dict[str, Any]:
    try:
        config = SwitchConfig.from_env()
        with SwitchClient(config) as client:
            return client.check()
    except ScopeError as exc:
        return {"ok": False, "error": exc.message}


app = create_app()
