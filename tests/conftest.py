"""Shared fixtures for the Vouch test suite.

Every test gets an isolated ledger and signing key in ``tmp_path``. The clock is
frozen at the demo moment so packet hashes and receipt ids are reproducible
within a test, while still being random across test runs because the key is new.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vouch.models import Deal, EvidenceBook, Packet, Receipt
from vouch.service import VouchPaths, VouchService, fixed_clock

ROOT = Path(__file__).resolve().parents[1]
DEMO_MOMENT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
DEMO_DATE = DEMO_MOMENT.date()
DEAL_ID = "acme-2026"

ALL_APPROVALS: list[tuple[str, str, str]] = [
    ("Priya", "SSO", "AE"),
    ("Sam", "SSO", "ENG_LEAD"),
    ("Priya", "SLA", "AE"),
    ("Sam", "SLA", "ENG_LEAD"),
    ("Priya", "DISC", "AE"),
    ("Maya", "DISC", "VP_SALES"),
    ("Priya", "SOC2", "AE"),
    ("Alex", "SOC2", "LEGAL"),
]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def paths(tmp_path: Path) -> VouchPaths:
    return VouchPaths.discover(root=ROOT, home=tmp_path / "home")


@pytest.fixture
def service(paths: VouchPaths) -> Iterator[VouchService]:
    instance = VouchService.open(paths, now=fixed_clock(DEMO_MOMENT))
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def ingested(service: VouchService) -> tuple[Deal, EvidenceBook]:
    return service.ingest_fixture("acme")


def approve_all(service: VouchService, deal_id: str = DEAL_ID) -> None:
    """Record the eight valid approvals the demo asks humans for."""
    for actor, claim_id, role in ALL_APPROVALS:
        service.approve(deal_id, claim_id, role, actor)


def prepare_v2(service: VouchService, deal_id: str = DEAL_ID) -> tuple[Deal, Packet, Packet]:
    """Ingest, assess v1, challenge, revise, and re-challenge.

    Returns ``(deal, v1, v2)`` where v2 is the challenged, block-free packet.
    """
    deal, _ = service.ingest_fixture("acme")
    v1 = service.assess(deal_id)
    service.challenge(deal_id)
    v2 = service.revise(deal_id)
    checked = service.challenge(deal_id)
    assert checked.packet_hash == v2.packet_hash
    return deal, v1, checked


def sealed(service: VouchService, deal_id: str = DEAL_ID) -> Receipt:
    """Drive the full workflow and return a sealed receipt."""
    prepare_v2(service, deal_id)
    approve_all(service, deal_id)
    return service.seal(deal_id)
