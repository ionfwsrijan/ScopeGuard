"""Shared fixtures for the Scope Guard test suite.

Every test gets an isolated ledger and signing key in ``tmp_path``. The clock is
frozen at the demo moment so packet hashes and change-order ids are reproducible
within a test, while still being fresh across test runs because the keys are new.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers import DEMO_MOMENT, ROOT
from scopeguard.models import Engagement, EvidenceBook
from scopeguard.service import ScopePaths, ScopeService, fixed_clock


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def paths(tmp_path: Path) -> ScopePaths:
    return ScopePaths.discover(root=ROOT, home=tmp_path / "home")


@pytest.fixture
def service(paths: ScopePaths) -> Iterator[ScopeService]:
    instance = ScopeService.open(paths, now=fixed_clock(DEMO_MOMENT))
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def ingested(service: ScopeService) -> tuple[Engagement, EvidenceBook]:
    return service.ingest_fixture("northwind")
