"""Shared constants and workflow helpers for the test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scopeguard.models import Engagement, Packet, Receipt
from scopeguard.receipt import Keyring
from scopeguard.service import ScopeService

ROOT = Path(__file__).resolve().parents[1]
DEMO_MOMENT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
DEMO_DATE = DEMO_MOMENT.date()
ENGAGEMENT_ID = "northwind-2026"
CLIENT_SIGNER = "Jordan Reyes"

ALL_SIGNATURES: list[tuple[str, str, str]] = [
    ("Dana", "MOBILE", "ACCOUNT_LEAD"),
    ("Marco", "MOBILE", "DELIVERY_LEAD"),
    ("Dana", "RUSH", "ACCOUNT_LEAD"),
    ("Marco", "RUSH", "DELIVERY_LEAD"),
    ("Dana", "RATE", "ACCOUNT_LEAD"),
    ("Rae", "RATE", "FINANCE"),
    ("Dana", "TERMS", "ACCOUNT_LEAD"),
    ("Iris", "TERMS", "LEGAL"),
]


def sign_all(service: ScopeService, engagement_id: str = ENGAGEMENT_ID) -> None:
    """Record the eight signatures the demo asks named humans for."""
    for actor, request_id, role in ALL_SIGNATURES:
        service.approve(engagement_id, request_id, role, actor)


def prepare_v2(
    service: ScopeService, engagement_id: str = ENGAGEMENT_ID
) -> tuple[Engagement, Packet, Packet]:
    """Ingest, price v1, challenge, revise, and re-challenge.

    Returns ``(engagement, v1, v2)`` where v2 is the challenged, block-free packet.
    """
    engagement, _ = service.ingest_fixture("northwind")
    v1 = service.assess(engagement_id)
    service.challenge(engagement_id)
    v2 = service.revise(engagement_id)
    checked = service.challenge(engagement_id)
    assert checked.packet_hash == v2.packet_hash
    return engagement, v1, checked


def sealed(service: ScopeService, engagement_id: str = ENGAGEMENT_ID) -> Receipt:
    """Drive the full workflow and return an issuer-signed change order."""
    prepare_v2(service, engagement_id)
    sign_all(service, engagement_id)
    return service.seal(engagement_id)


def accepted(service: ScopeService, engagement_id: str = ENGAGEMENT_ID) -> Receipt:
    """Drive the full workflow and return a two-party signed change order."""
    receipt = sealed(service, engagement_id)
    client = Keyring.generate()
    service.register_client(engagement_id, CLIENT_SIGNER, client.public_key_b64)
    return service.countersign(
        engagement_id,
        CLIENT_SIGNER,
        keyring=client,
        receipt_id=receipt.body.receipt_id,
    )


__all__ = [
    "ALL_SIGNATURES",
    "CLIENT_SIGNER",
    "DEMO_DATE",
    "DEMO_MOMENT",
    "ENGAGEMENT_ID",
    "ROOT",
    "accepted",
    "prepare_v2",
    "sealed",
    "sign_all",
]
