"""The FastAPI surface: read-only case views, receipt verification, and JSON API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helpers import DEMO_MOMENT, accepted
from scopeguard.api.app import create_app
from scopeguard.service import ScopeService, fixed_clock


@pytest.fixture
def client(repo_root: Path, tmp_path: Path) -> TestClient:
    app = create_app(root=repo_root, home=tmp_path / "home")
    service = ScopeService.open(app.state.paths, now=fixed_clock(DEMO_MOMENT))
    try:
        accepted(service)
    finally:
        service.close()
    return TestClient(app)


def receipt_payload(client: TestClient) -> dict[str, object]:
    payload = client.get("/api/engagements/northwind-2026").json()
    return payload["receipt"]


# ---------------------------------------------------------------------- pages
def test_index_lists_the_case(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Scope Guard" in response.text
    assert "Northwind Health" in response.text
    assert "countersigned" in response.text


def test_engagement_page_renders_packet_receipt_and_handoff(client: TestClient) -> None:
    response = client.get("/engagements/northwind-2026")
    assert response.status_code == 200
    assert "Northwind Health" in response.text
    assert "Priced packet v2" in response.text
    assert "Only the line items above leave this room." in response.text
    assert "42,240.00" in response.text


def test_receipt_page_and_latest_alias(client: TestClient) -> None:
    receipt = receipt_payload(client)
    receipt_id = receipt["body"]["receipt_id"]  # type: ignore[index]
    direct = client.get(f"/change-orders/{receipt_id}")
    latest = client.get("/engagements/northwind-2026/change-order")
    assert direct.status_code == latest.status_code == 200
    assert receipt_id in direct.text
    assert receipt_id in latest.text
    assert "authentic" in direct.text.lower()


def test_verify_page_can_verify_a_pasted_receipt(client: TestClient) -> None:
    receipt = receipt_payload(client)
    response = client.post("/verify", data={"receipt": json.dumps(receipt)})
    assert response.status_code == 200
    assert "verified" in response.text.lower() or "authentic" in response.text.lower()


def test_verify_page_reports_empty_and_invalid_input(client: TestClient) -> None:
    empty = client.post("/verify", data={"receipt": ""})
    assert empty.status_code == 200
    assert "Paste a change-order JSON document" in empty.text
    invalid = client.post("/verify", data={"receipt": "not json"})
    assert invalid.status_code == 200
    assert "not a valid Scope Guard change order" in invalid.text


def test_verify_permalink_renders_the_form(client: TestClient) -> None:
    response = client.get("/verify")
    assert response.status_code == 200
    assert "<textarea" in response.text


# ------------------------------------------------------------------------ API
def test_health_reports_audit_and_stats(client: TestClient) -> None:
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert payload["audit_ok"] is True
    assert payload["stats"]["engagements"] == 1
    assert payload["stats"]["receipts"] == 1


def test_engagements_api(client: TestClient) -> None:
    rows = client.get("/api/engagements").json()
    assert len(rows) == 1
    assert rows[0]["engagement"]["id"] == "northwind-2026"
    assert rows[0]["packets"] == 2
    assert rows[0]["change_orders"] == 1


def test_engagement_api_exposes_gate_receipt_and_handoff(client: TestClient) -> None:
    payload = client.get("/api/engagements/northwind-2026").json()
    assert payload["packet"]["version"] == 2
    assert payload["gate"]["pending"] == []
    assert payload["gate"]["ok"] is True
    assert len(payload["approvals"]) == 8
    assert payload["receipt"]["body"]["packet_version"] == 2
    assert payload["receipt"]["acceptance"]["issuer"] == "Jordan Reyes"
    assert "42,240.00" in payload["handoff"]


def test_receipt_and_verification_api(client: TestClient) -> None:
    receipt = receipt_payload(client)
    receipt_id = receipt["body"]["receipt_id"]  # type: ignore[index]
    fetched = client.get(f"/api/change-orders/{receipt_id}").json()
    assert fetched["body"]["receipt_id"] == receipt_id
    result = client.get(f"/api/change-orders/{receipt_id}/verify").json()
    assert result["ok"] is True
    assert result["receipt_id"] == receipt_id


def test_verify_api_accepts_a_valid_document(client: TestClient) -> None:
    response = client.post("/api/verify", json=receipt_payload(client))
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_verify_api_rejects_a_tampered_document(client: TestClient) -> None:
    receipt = receipt_payload(client)
    receipt["body"]["line_items"][0]["scope"] = "Native mobile app generally available tomorrow."  # type: ignore[index]
    response = client.post("/api/verify", json=receipt)
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_verify_api_rejects_an_invalid_document(client: TestClient) -> None:
    response = client.post("/api/verify", json={"body": {}})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid change order"


def test_audit_api_returns_a_valid_chain(client: TestClient) -> None:
    payload = client.get("/api/audit").json()
    assert payload["ok"] is True
    assert payload["events"]
    assert payload["events"][0]["kind"] == "engagement.ingested"


def test_unknown_resources_are_404(client: TestClient) -> None:
    assert client.get("/engagements/nope").status_code == 404
    assert client.get("/change-orders/co_nope").status_code == 404
    assert client.get("/engagements/nope/change-order").status_code == 404
    assert client.get("/api/engagements/nope").status_code == 404
    assert client.get("/api/change-orders/co_nope").status_code == 404
    assert client.get("/api/change-orders/co_nope/verify").status_code == 404


def test_static_assets_are_served(client: TestClient) -> None:
    css = client.get("/static/scopeguard.css")
    assert css.status_code == 200
    assert "--ink" in css.text
