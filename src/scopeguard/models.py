"""Typed domain model for Scope Guard.

Scope Guard turns a client's "one more thing" into a priced, human-approved
change order. The models are intentionally boring and serializable: a change
order is only as trustworthy as its canonicalization, so nothing here relies on
Python object identity or dictionary insertion order.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)


class RequestType(str, Enum):
    """The four ways scope creep shows up on a services engagement."""

    NEW_WORK = "new_work"
    TIMELINE = "timeline"
    RATE = "rate"
    TERMS = "terms"


class Verdict(str, Enum):
    APPROVE = "approve"
    NARROW = "narrow"
    REFUSE = "refuse"
    ESCALATE = "escalate"


class Severity(str, Enum):
    BLOCK = "block"
    WARN = "warn"
    NOTE = "note"

    @property
    def rank(self) -> int:
        return {"block": 0, "warn": 1, "note": 2}[self.value]


class ApprovalStatus(str, Enum):
    VALID = "valid"
    STALE = "stale"
    SUPERSEDED = "superseded"


class PacketStatus(str, Enum):
    DRAFT = "draft"
    BLOCKED = "blocked"
    CHALLENGED = "challenged"
    SEALED = "sealed"


class ScopeRequest(Model):
    """One client ask, captured as a typed request rather than free text."""

    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{1,23}$")
    type: RequestType
    title: str = Field(min_length=3, max_length=160)
    requested_text: str = Field(min_length=3, max_length=2000)
    requested_value: dict[str, Any] = Field(default_factory=dict)
    due: date | None = None
    owner: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _upper(cls, value: str) -> str:
        return str(value).upper()


class Engagement(Model):
    """A fixed-fee services engagement with a client and a margin."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    client: str = Field(min_length=1, max_length=120)
    requested_by: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    contract_value: float = Field(gt=0)
    delivery_cost_ratio_pct: float = Field(ge=0, le=100)
    requests: list[ScopeRequest] = Field(min_length=1)
    context: str = ""
    tags: list[str] = Field(default_factory=list)

    def request(self, request_id: str) -> ScopeRequest:
        wanted = request_id.upper()
        for item in self.requests:
            if item.id == wanted:
                return item
        raise KeyError(request_id)

    @property
    def engagement_token(self) -> str:
        return self.id.upper()


class Evidence(Model):
    """A dated, source-owned fact that supports (or undermines) a request."""

    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{1,31}$")
    request_id: str
    kind: Literal["estimate", "capacity", "rate_card", "contract", "sow", "note"]
    source: str = Field(min_length=1, max_length=500)
    observed_at: date
    valid_until: date | None = None
    summary: str = Field(min_length=3, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    digest: str = ""

    @field_validator("id", "request_id", mode="before")
    @classmethod
    def _upper(cls, value: str) -> str:
        return str(value).upper()

    def computed_digest(self) -> str:
        from scopeguard.receipt import canonical_hash

        body = self.model_dump(mode="json", exclude={"digest"})
        return canonical_hash(body)


class EvidenceBook(Model):
    """All evidence for an engagement, validated for referential integrity."""

    engagement_id: str
    items: list[Evidence] = Field(default_factory=list)

    def for_request(self, request_id: str) -> list[Evidence]:
        wanted = request_id.upper()
        return [item for item in self.items if item.request_id == wanted]

    def by_id(self, evidence_id: str) -> Evidence:
        for item in self.items:
            if item.id == evidence_id.upper():
                return item
        raise KeyError(evidence_id)


class Decision(Model):
    """The Pricer's deterministic answer for one request."""

    request_id: str
    verdict: Verdict
    rationale: str
    priced_text: str
    amount: float | None = None
    approved_value: dict[str, Any] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)

    @property
    def is_commitment(self) -> bool:
        return self.verdict in (Verdict.APPROVE.value, Verdict.NARROW.value)


class Finding(Model):
    id: str
    severity: Severity
    request_id: str | None = None
    code: str
    message: str
    remediation: dict[str, Any] = Field(default_factory=dict)


class PacketBody(Model):
    """The part of a packet that humans sign.

    Findings, status and timestamps deliberately sit outside the hash: the Red
    Team's challenge must not invalidate a signature by itself. A change to a
    price, a condition, or the version does.
    """

    engagement_id: str
    version: int = Field(ge=1)
    client: str
    currency: str
    decisions: list[Decision]
    previous_hash: str | None = None


class Packet(Model):
    engagement_id: str
    version: int
    client: str
    currency: str
    decisions: list[Decision]
    findings: list[Finding] = Field(default_factory=list)
    status: PacketStatus = PacketStatus.DRAFT
    packet_hash: str
    previous_hash: str | None = None
    created_at: datetime
    challenged_at: datetime | None = None
    sealed_at: datetime | None = None

    @field_validator("created_at", "challenged_at", "sealed_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    def body(self) -> PacketBody:
        return PacketBody(
            engagement_id=self.engagement_id,
            version=self.version,
            client=self.client,
            currency=self.currency,
            decisions=self.decisions,
            previous_hash=self.previous_hash,
        )

    def decision(self, request_id: str) -> Decision:
        wanted = request_id.upper()
        for item in self.decisions:
            if item.request_id == wanted:
                return item
        raise KeyError(request_id)

    def has_blocks(self) -> bool:
        return any(item.severity == Severity.BLOCK.value for item in self.findings)

    @property
    def subtotal(self) -> float:
        return round(
            sum(item.amount or 0.0 for item in self.decisions if (item.amount or 0) > 0), 2
        )

    @property
    def discount_total(self) -> float:
        return round(
            sum(item.amount or 0.0 for item in self.decisions if (item.amount or 0) < 0), 2
        )

    @property
    def total(self) -> float:
        return round(self.subtotal + self.discount_total, 2)


class Approvers(Model):
    """Role -> human mapping.

    A role may be held by several people. Switch room membership is not a
    signature by itself; the phrase and the packet hash are the signature.
    """

    engagement_id: str
    roles: dict[str, list[str]]

    def people(self, role: str) -> list[str]:
        wanted = role.strip().casefold()
        for name, people in self.roles.items():
            if name.strip().casefold() == wanted:
                return people
        return []

    def role_for(self, actor: str) -> str | None:
        wanted = actor.strip().casefold()
        for role, people in self.roles.items():
            if any(person.casefold() == wanted for person in people):
                return role.upper()
        return None

    def can_hold(self, actor: str, role: str) -> bool:
        return any(person.casefold() == actor.strip().casefold() for person in self.people(role))

    def all_roles(self) -> list[str]:
        return sorted(self.roles)


class Approval(Model):
    id: str
    engagement_id: str
    packet_id: str
    packet_hash: str
    request_id: str
    role: str
    actor: str
    phrase: str
    created_at: datetime
    status: ApprovalStatus = ApprovalStatus.VALID
    invalidated_reason: str | None = None

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return _utc(value)


class ReceiptApproval(Model):
    request_id: str
    role: str
    actor: str
    phrase: str
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return _utc(value)


class ReceiptLineItem(Model):
    request_id: str
    kind: str
    verdict: Verdict
    scope: str
    amount: float
    conditions: list[str] = Field(default_factory=list)


class Acceptance(Model):
    """A client's countersignature over the issuer's signed body hash."""

    issuer: str = Field(min_length=1, max_length=200)
    public_key: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    algorithm: str = "Ed25519"


class ReceiptBody(Model):
    receipt_id: str
    engagement_id: str
    client: str
    packet_id: str
    packet_version: int
    packet_hash: str
    policy_id: str
    issued_at: datetime
    expires_at: datetime
    currency: str = "USD"
    line_items: list[ReceiptLineItem]
    declined: list[str] = Field(default_factory=list)
    approvals: list[ReceiptApproval]
    subtotal: float
    discount_total: float
    total: float
    chain_prev: str | None = None
    issuer: str = "Scope Guard"

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return _utc(value)


class Receipt(Model):
    """A signed change order, optionally countersigned by the client."""

    body: ReceiptBody
    body_hash: str
    signature: str
    public_key: str
    algorithm: str = "Ed25519"
    acceptance: Acceptance | None = None


class GateCheck(Model):
    code: str
    ok: bool
    detail: str


class GateResult(Model):
    ok: bool
    checks: list[GateCheck]
    pending: list[str] = Field(default_factory=list)

    @property
    def failures(self) -> list[GateCheck]:
        return [item for item in self.checks if not item.ok]


class VerificationCheck(Model):
    code: str
    ok: bool
    detail: str


class VerificationResult(Model):
    ok: bool
    receipt_id: str
    checks: list[VerificationCheck]
    summary: str


class SwitchMessage(Model):
    id: str
    room_id: str = ""
    sender: str = ""
    sender_name: str
    body: str
    timestamp_ms: int = 0

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=UTC)


class SyncResult(Model):
    scanned: int = 0
    recorded: int = 0
    ignored: int = 0
    errors: list[str] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
