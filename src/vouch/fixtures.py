"""Fixture loading with referential integrity checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from vouch.errors import ConfigError
from vouch.models import Approvers, Claim, Deal, Evidence, EvidenceBook, SwitchMessage


class FixtureError(ConfigError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestFixture(_Model):
    deal: Deal


class EvidenceFixture(_Model):
    deal_id: str
    evidence: list[Evidence] = Field(default_factory=list)


class MessageFixture(_Model):
    room_id: str = "room-demo"
    messages: list[SwitchMessage] = Field(default_factory=list)


def _yaml(path: Path) -> Any:
    if not path.exists():
        raise FixtureError(f"fixture not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FixtureError(f"invalid YAML in {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise FixtureError(f"{path.name} must contain a YAML mapping")
    return raw


def load_deal(path: Path) -> Deal:
    raw = _yaml(path)
    if "deal" in raw:
        raw = raw["deal"]
    try:
        return Deal.model_validate(raw)
    except Exception as exc:
        raise FixtureError(f"invalid deal fixture {path.name}: {exc}") from exc


def load_evidence(path: Path, deal: Deal | None = None) -> EvidenceBook:
    raw = _yaml(path)
    if "deal_id" in raw:
        deal_id = str(raw["deal_id"])
        items_raw = raw.get("evidence", [])
    else:  # a bare list is convenient for hand-written evidence
        deal_id = deal.id if deal else str(raw.get("deal_id", "unknown"))
        items_raw = raw
    if not isinstance(items_raw, list):
        raise FixtureError("evidence must be a list")
    items: list[Evidence] = []
    seen: set[str] = set()
    for entry in items_raw:
        try:
            item = Evidence.model_validate(entry)
        except Exception as exc:
            raise FixtureError(f"invalid evidence entry: {exc}") from exc
        if item.id in seen:
            raise FixtureError(f"duplicate evidence id: {item.id}")
        seen.add(item.id)
        item = item.model_copy(update={"digest": item.computed_digest()})
        items.append(item)
    if deal is not None:
        if deal_id != deal.id:
            raise FixtureError(f"evidence deal_id {deal_id!r} does not match {deal.id!r}")
        claim_ids = {claim.id for claim in deal.claims}
        unknown = sorted({item.claim_id for item in items} - claim_ids)
        if unknown:
            raise FixtureError(f"evidence references unknown claims: {', '.join(unknown)}")
    return EvidenceBook(deal_id=deal_id, items=items)


def load_approvers(path: Path, deal: Deal | None = None) -> Approvers:
    raw = _yaml(path)
    try:
        approvers = Approvers.model_validate(raw)
    except Exception as exc:
        raise FixtureError(f"invalid approvers fixture {path.name}: {exc}") from exc
    if deal is not None and approvers.deal_id != deal.id:
        raise FixtureError(f"approvers deal_id {approvers.deal_id!r} does not match {deal.id!r}")
    for role, people in approvers.roles.items():
        if not role:
            raise FixtureError("approver role cannot be empty")
        if not people or any(not person.strip() for person in people):
            raise FixtureError(f"role {role} needs at least one named person")
    return approvers


def load_messages(path: Path) -> list[SwitchMessage]:
    raw = _yaml(path)
    try:
        fixture = MessageFixture.model_validate(raw)
    except Exception as exc:
        raise FixtureError(f"invalid message fixture {path.name}: {exc}") from exc
    for message in fixture.messages:
        if not message.room_id:
            message.room_id = fixture.room_id
    return fixture.messages


def load_claims_document(path: Path) -> list[Claim]:
    """Load a small claims-only YAML file for live intake."""
    raw = _yaml(path)
    items = raw.get("claims", raw)
    if not isinstance(items, list):
        raise FixtureError("claims must be a list")
    return [Claim.model_validate(item) for item in items]
