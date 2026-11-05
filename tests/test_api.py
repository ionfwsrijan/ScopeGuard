"""The FastAPI surface: read-only case views, receipt verification, and JSON API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import DEMO_MOMENT, approve_all, prepare_v2
from vouch.api.app import create_app
from vouch.service import VouchService, fixed_clock


@pytest.fixture
def client(repo_root: Path, tmp_path: Path) -> TestClient:
    app = create_app(root=repo_root, home=tmp_path / "home")
    service = VouchService.open(app.state.paths, now=fixed_clock(DEMO_MOMENT))
    try:
        prepare_v2(service)
        approve_all(service)
        service.seal("acme-2026")
    finally:
        service.close()
    return TestClient(app)


def test_index_lists_the_case(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Vouch" in response.text
    assert "Acme Corp" in response.text


def test_deal_page_renders_packet_and_receipt(client: TestClient) -> None:
    response = client.get("/deals/acme-2026")
    assert response.status_code == 200
    assert "Acme Corp" in response.text
    assert "Examiner" in response.text or "Packet v2" in response.text
    assert "Only the commitments above leave this room." in response.text


def test_receipt_page_and_latest_alias(client: TestClient) -> None:
    receipt = client.get("/api/deals/acme-2026").json()["receipt"]
    receipt_id = receipt["body"]["receipt_id"]
    direct = client.get(f"/receipts/{receipt_id}")
    latest = client.get("/deals/acme-2026/receipt")
    assert direct.status_code == latest.status_code == 200
    assert receipt_id in direct.text
    assert receipt_id in latest.text
    assert "authentic" in direct.text.lower()


def test_verify_page_can_verify_a_pasted_receipt(client: TestClient) -> None:
    receipt = client.get("/api/deals/acme-2026").json()["receipt"]
    import json

    response = client.post("/verify", data={"receipt": json.dumps(receipt)})
    assert response.status_code == 200
    assert "Receipt verified" in response.text or "authentic" in response.text.lower()


def test_verify_page_reports_empty_and_invalid_input(client: TestClient) -> None:
    empty = client.post("/verify", data={"receipt": ""})
    assert empty.status_code == 200
    assert "Paste a receipt" in empty.text
    invalid = client.post("/verify", data={"receipt": "not json"})
    assert invalid.status_code == 200
    assert "not a valid Vouch receipt" in invalid.text


def test_verify_permalink_renders_the_form(client: TestClient) -> None:
    response = client.get("/verify")
    assert response.status_code == 200
    assert "<textarea" in response.text


def test_health_reports_audit_and_stats(client: TestClient) -> None:
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert payload["audit_ok"] is True
    assert payload["stats"]["deals"] == 1
    assert payload["stats"]["receipts"] == 1


def test_deals_api(client: TestClient) -> None:
    rows = client.get("/api/deals").json()
    assert len(rows) == 1
    assert rows[0]["deal"]["id"] == "acme-2026"
    assert rows[0]["packets"] == 2
    assert rows[0]["receipts"] == 1


def test_deal_api_exposes_gate_receipt_and_handoff(client: TestClient) -> None:
    payload = client.get("/api/deals/acme-2026").json()
    assert payload["packet"]["version"] == 2
    assert payload["gate"]["pending"] == []
    assert len(payload["approvals"]) == 8
    assert payload["receipt"]["body"]["packet_version"] == 2
    assert "15.00% discount" in payload["handoff"]


def test_receipt_and_verification_api(client: TestClient) -> None:
    receipt = client.get("/api/deals/acme-2026").json()["receipt"]
    receipt_id = receipt["body"]["receipt_id"]
    assert client.get(f"/api/receipts/{receipt_id}").json()["body"]["receipt_id"] == receipt_id
    result = client.get(f"/api/receipts/{receipt_id}/verify").json()
    assert result["ok"] is True
    assert result["receipt_id"] == receipt_id


def test_verify_api_accepts_a_valid_document(client: TestClient) -> None:
    receipt = client.get("/api/deals/acme-2026").json()["receipt"]
    response = client.post("/api/verify", json=receipt)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_verify_api_rejects_a_tampered_document(client: TestClient) -> None:
    receipt = client.get("/api/deals/acme-2026").json()["receipt"]
    receipt["body"]["commitments"][0]["commitment"] = "SSO generally available tomorrow."
    response = client.post("/api/verify", json=receipt)
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_verify_api_rejects_an_invalid_document(client: TestClient) -> None:
    response = client.post("/api/verify", json={"body": {}})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid receipt"


def test_audit_api_returns_a_valid_chain(client: TestClient) -> None:
    payload = client.get("/api/audit").json()
    assert payload["ok"] is True
    assert payload["events"]
    assert payload["events"][0]["kind"] == "deal.ingested"


def test_unknown_resources_are_404(client: TestClient) -> None:
    assert client.get("/deals/nope").status_code == 404
    assert client.get("/receipts/vch_nope").status_code == 404
    assert client.get("/deals/nope/receipt").status_code == 404
    assert client.get("/api/deals/nope").status_code == 404
    assert client.get("/api/receipts/vch_nope").status_code == 404
    assert client.get("/api/receipts/vch_nope/verify").status_code == 404


def test_static_assets_are_served(client: TestClient) -> None:
    css = client.get("/static/vouch.css")
    assert css.status_code == 200
    assert "--ink" in css.text
