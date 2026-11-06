"""Fixture loading with referential integrity checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from scopeguard.errors import ConfigError
from scopeguard.models import (
    Approvers,
    Engagement,
    Evidence,
    EvidenceBook,
    ScopeRequest,
    SwitchMessage,
)


class FixtureError(ConfigError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngagementFixture(_Model):
    engagement: Engagement


class EvidenceFixture(_Model):
    engagement_id: str
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


def load_engagement(path: Path) -> Engagement:
    raw = _yaml(path)
    if "engagement" in raw:
        raw = raw["engagement"]
    try:
        return Engagement.model_validate(raw)
    except Exception as exc:
        raise FixtureError(f"invalid engagement fixture {path.name}: {exc}") from exc


def load_evidence(path: Path, engagement: Engagement | None = None) -> EvidenceBook:
    raw = _yaml(path)
    if "engagement_id" in raw:
        engagement_id = str(raw["engagement_id"])
        items_raw = raw.get("evidence", [])
    else:  # a bare list is convenient for hand-written evidence
        engagement_id = engagement.id if engagement else str(raw.get("engagement_id", "unknown"))
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
    if engagement is not None:
        if engagement_id != engagement.id:
            raise FixtureError(
                f"evidence engagement_id {engagement_id!r} does not match {engagement.id!r}"
            )
        request_ids = {request.id for request in engagement.requests}
        unknown = sorted({item.request_id for item in items} - request_ids)
        if unknown:
            raise FixtureError(f"evidence references unknown requests: {', '.join(unknown)}")
    return EvidenceBook(engagement_id=engagement_id, items=items)


def load_approvers(path: Path, engagement: Engagement | None = None) -> Approvers:
    raw = _yaml(path)
    try:
        approvers = Approvers.model_validate(raw)
    except Exception as exc:
        raise FixtureError(f"invalid approvers fixture {path.name}: {exc}") from exc
    if engagement is not None and approvers.engagement_id != engagement.id:
        raise FixtureError(
            f"approvers engagement_id {approvers.engagement_id!r} does not match {engagement.id!r}"
        )
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


def load_requests_document(path: Path) -> list[ScopeRequest]:
    """Load a small requests-only YAML file for live intake."""
    raw = _yaml(path)
    items = raw.get("requests", raw)
    if not isinstance(items, list):
        raise FixtureError("requests must be a list")
    return [ScopeRequest.model_validate(item) for item in items]
