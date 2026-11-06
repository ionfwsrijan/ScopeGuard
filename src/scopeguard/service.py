"""The Scope Guard service: one orchestration layer shared by CLI, API, demo and bots.

Keeping the workflow here means the offline demo and the real Switch path
execute the same code. Switch is transport and conversation, not a second
implementation.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TypedDict

from scopeguard.assess import assess_engagement
from scopeguard.challenge import apply_revisions, challenge_packet, gate_packet
from scopeguard.clock import utcnow
from scopeguard.errors import ApprovalError, ConfigError, GateError, NotFoundError, StateError
from scopeguard.fixtures import load_approvers, load_engagement, load_evidence
from scopeguard.models import (
    Approval,
    ApprovalStatus,
    Approvers,
    Decision,
    Engagement,
    EvidenceBook,
    GateResult,
    Packet,
    PacketStatus,
    Receipt,
    SwitchMessage,
    SyncResult,
    VerificationCheck,
    VerificationResult,
)
from scopeguard.policy import Policy
from scopeguard.receipt import (
    Keyring,
    build_receipt,
    canonical_hash,
    expected_phrase,
    packet_hash,
    parse_approval_phrase,
    verify_receipt,
)
from scopeguard.receipt import (
    countersign as countersign_receipt,
)
from scopeguard.store import Store


@dataclass(frozen=True)
class ScopePaths:
    root: Path
    home: Path
    policy: Path
    fixtures: Path
    approvers: Path

    @property
    def db(self) -> Path:
        return self.home / "ledger.sqlite3"

    @property
    def key(self) -> Path:
        return self.home / "keys" / "ed25519.pem"

    @property
    def client_key(self) -> Path:
        return self.home / "keys" / "client-ed25519.pem"

    @property
    def receipts(self) -> Path:
        return self.home / "receipts"

    @classmethod
    def discover(
        cls,
        root: Path | None = None,
        home: Path | None = None,
        *,
        fixture: str = "northwind",
        policy: Path | None = None,
        approvers: Path | None = None,
    ) -> ScopePaths:
        base = (root or Path.cwd()).resolve()
        configured = home or os.environ.get("SCOPE_HOME")
        runtime = (Path(configured) if configured else Path.home() / ".scopeguard").resolve()
        fixtures_dir = base / "fixtures" / fixture
        return cls(
            root=base,
            home=runtime,
            policy=policy or base / "policies" / "scope_policy_v1.yaml",
            fixtures=fixtures_dir,
            approvers=approvers or fixtures_dir / "approvers.yaml",
        )

    def ensure(self) -> None:
        for folder in (self.home, self.receipts, self.key.parent):
            folder.mkdir(parents=True, exist_ok=True)


class StatusView(TypedDict):
    """Everything one screen needs about an engagement, with concrete types."""

    engagement: Engagement
    packet: Packet
    approvals: list[Approval]
    gate: GateResult
    receipt: Receipt | None
    evidence_count: int
    packet_count: int


def fixed_clock(moment: datetime) -> Callable[[], datetime]:
    frozen = moment.astimezone(UTC).replace(microsecond=0)

    def clock() -> datetime:
        return frozen

    return clock


def as_of_clock(value: date | None) -> Callable[[], datetime]:
    if value is None:
        return utcnow
    return fixed_clock(datetime.combine(value, time(12, 0), tzinfo=UTC))


class ScopeService:
    def __init__(
        self,
        *,
        store: Store,
        policy: Policy,
        approvers: Approvers,
        paths: ScopePaths,
        now: Callable[[], datetime] = utcnow,
        keyring: Keyring | None = None,
        client_keyring: Keyring | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self.approvers = approvers
        self.paths = paths
        self.now = now
        self._keyring = keyring
        self._client_keyring = client_keyring

    # --------------------------------------------------------------- setup
    @classmethod
    def open(
        cls,
        paths: ScopePaths,
        *,
        now: Callable[[], datetime] = utcnow,
        keyring: Keyring | None = None,
        client_keyring: Keyring | None = None,
    ) -> ScopeService:
        policy = Policy.load(paths.policy)
        approvers = load_approvers(paths.approvers)
        store = Store(paths.db)
        return cls(
            store=store,
            policy=policy,
            approvers=approvers,
            paths=paths,
            now=now,
            keyring=keyring,
            client_keyring=client_keyring,
        )

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> ScopeService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def keyring(self) -> Keyring:
        if self._keyring is None:
            self.paths.ensure()
            self._keyring = Keyring.load_or_create(self.paths.key)
        return self._keyring

    @property
    def client_keyring(self) -> Keyring:
        if self._client_keyring is None:
            self.paths.ensure()
            self._client_keyring = Keyring.load_or_create(self.paths.client_key)
        return self._client_keyring

    # --------------------------------------------------------------- intake
    def ingest_fixture(self, name: str = "northwind") -> tuple[Engagement, EvidenceBook]:
        fixture_dir = self.paths.root / "fixtures" / name
        engagement = load_engagement(fixture_dir / "engagement.yaml")
        evidence = load_evidence(fixture_dir / "evidence.yaml", engagement)
        if engagement.id != self.approvers.engagement_id:
            raise ConfigError(
                f"engagement {engagement.id!r} does not match approver map "
                f"{self.approvers.engagement_id!r}"
            )
        self.store.save_engagement(engagement, now=self.now())
        self.store.replace_evidence(engagement.id, evidence.items)
        self.store.audit(
            "engagement.ingested",
            "scopeguard",
            {
                "engagement_id": engagement.id,
                "requests": len(engagement.requests),
                "evidence": len(evidence.items),
                "fixture": name,
            },
        )
        return engagement, evidence

    def engagement(self, engagement_id: str) -> Engagement:
        return self.store.load_engagement(engagement_id)

    def evidence(self, engagement_id: str) -> EvidenceBook:
        return EvidenceBook(
            engagement_id=engagement_id, items=self.store.list_evidence(engagement_id)
        )

    # ---------------------------------------------------------------- assess
    def assess(self, engagement_id: str, *, note: str | None = None) -> Packet:
        engagement = self.store.load_engagement(engagement_id)
        evidence = self.evidence(engagement_id)
        previous = self.store.list_packets(engagement_id)
        version = (previous[0].version + 1) if previous else 1
        moment = self.now()
        decisions = assess_engagement(
            engagement=engagement, evidence=evidence, policy=self.policy, as_of=moment.date()
        )
        packet = Packet(
            engagement_id=engagement.id,
            version=version,
            client=engagement.client,
            currency=engagement.currency,
            decisions=decisions,
            status=PacketStatus.DRAFT,
            packet_hash="",
            previous_hash=previous[0].packet_hash if previous else None,
            created_at=moment,
        )
        packet.packet_hash = packet_hash(packet.body())
        self.store.save_packet(packet)
        invalidated = self.store.invalidate_approvals(
            engagement.id,
            keep_hash=packet.packet_hash,
            reason=f"packet v{version} replaced earlier commitments",
        )
        self.store.audit(
            "packet.priced",
            "pricer",
            {
                "engagement_id": engagement.id,
                "version": version,
                "packet_hash": packet.packet_hash,
                "subtotal": packet.subtotal,
                "discount": packet.discount_total,
                "total": packet.total,
                "decisions": {item.request_id: item.verdict for item in decisions},
                "invalidated_approvals": invalidated,
                "note": note,
            },
        )
        return packet

    # ------------------------------------------------------------- challenge
    def challenge(self, engagement_id: str) -> Packet:
        packet = self.store.latest_packet(engagement_id)
        if packet.status == PacketStatus.SEALED.value:
            raise StateError("packet is already sealed")
        engagement = self.store.load_engagement(engagement_id)
        evidence = self.evidence(engagement_id)
        findings = challenge_packet(
            packet=packet,
            engagement=engagement,
            evidence=evidence,
            policy=self.policy,
            as_of=self.now().date(),
        )
        blocks = sum(1 for item in findings if item.severity == "block")
        packet.findings = findings
        packet.status = PacketStatus.BLOCKED if blocks else PacketStatus.CHALLENGED
        packet.challenged_at = self.now()
        self.store.update_packet(packet)
        self.store.audit(
            "packet.challenged",
            "red_team",
            {
                "engagement_id": engagement_id,
                "version": packet.version,
                "packet_hash": packet.packet_hash,
                "blocks": blocks,
                "warnings": sum(1 for item in findings if item.severity == "warn"),
                "findings": [item.code for item in findings],
            },
        )
        return packet

    def revise(self, engagement_id: str, *, note: str | None = None) -> Packet:
        packet = self.store.latest_packet(engagement_id)
        if not packet.has_blocks():
            raise StateError("nothing to revise: the current packet has no blocking findings")
        engagement = self.store.load_engagement(engagement_id)
        decisions = apply_revisions(
            packet=packet,
            engagement=engagement,
            policy=self.policy,
            findings=packet.findings,
        )
        if canonical_hash([item.model_dump(mode="json") for item in decisions]) == canonical_hash(
            [item.model_dump(mode="json") for item in packet.decisions]
        ):
            raise StateError(
                "Red Team findings have no automatic remediation; a human must edit the ask"
            )
        moment = self.now()
        revised = Packet(
            engagement_id=engagement.id,
            version=packet.version + 1,
            client=engagement.client,
            currency=engagement.currency,
            decisions=decisions,
            status=PacketStatus.DRAFT,
            packet_hash="",
            previous_hash=packet.packet_hash,
            created_at=moment,
        )
        revised.packet_hash = packet_hash(revised.body())
        self.store.save_packet(revised)
        invalidated = self.store.invalidate_approvals(
            engagement.id,
            keep_hash=revised.packet_hash,
            reason=f"packet revised to v{revised.version} after Red Team findings",
        )
        self.store.audit(
            "packet.revised",
            "clerk",
            {
                "engagement_id": engagement.id,
                "from_version": packet.version,
                "to_version": revised.version,
                "old_hash": packet.packet_hash,
                "new_hash": revised.packet_hash,
                "invalidated_approvals": invalidated,
                "note": note,
            },
        )
        return revised

    # -------------------------------------------------------------- approvals
    def approve(
        self,
        engagement_id: str,
        request_id: str,
        role: str,
        actor: str,
        *,
        phrase: str | None = None,
        source: str = "cli",
    ) -> Approval:
        packet = self.store.latest_packet(engagement_id)
        if packet.status == PacketStatus.SEALED.value:
            raise StateError("packet is already sealed")
        engagement = self.store.load_engagement(engagement_id)
        try:
            request = engagement.request(request_id)
        except KeyError as exc:
            raise NotFoundError(f"request not found: {request_id}") from exc
        try:
            decision = packet.decision(request.id)
        except KeyError as exc:
            raise NotFoundError(f"request has no decision in packet v{packet.version}") from exc
        wanted_role = role.upper()
        if wanted_role not in decision.required_roles:
            raise ApprovalError(
                f"{wanted_role} is not a required approver for {request.id}",
                hint=f"Required roles: {', '.join(decision.required_roles) or 'none'}.",
            )
        if not self.approvers.can_hold(actor, wanted_role):
            existing = [
                item
                for item in self.store.approvals_for_packet(packet)
                if item.request_id == request.id and item.role == wanted_role
            ]
            if existing and existing[0].actor.casefold() != actor.casefold():
                raise ApprovalError(
                    f"{request.id} / {wanted_role} already approved by {existing[0].actor}",
                    hint="One role, one approval. Change the approver map if the wrong "
                    "person holds it.",
                )
            raise ApprovalError(
                f"{actor} does not hold {wanted_role} in the approver map",
                hint=f"{wanted_role} is held by: "
                f"{', '.join(self.approvers.people(wanted_role)) or 'nobody'}.",
            )
        expected = expected_phrase(engagement.id, request.id, wanted_role)
        if phrase is not None:
            parsed = parse_approval_phrase(phrase, engagement.id)
            if parsed != (request.id, wanted_role.upper()) or expected not in phrase.upper():
                raise ApprovalError(
                    f"approval phrase does not match: {phrase!r}",
                    hint=f"Expected exactly: {expected}",
                )
        existing = [
            item
            for item in self.store.approvals_for_packet(packet)
            if item.request_id == request.id and item.role == wanted_role
        ]
        if existing:
            if existing[0].actor.casefold() == actor.casefold():
                return existing[0]
            raise ApprovalError(
                f"{request.id} / {wanted_role} already approved by {existing[0].actor}",
                hint="One role, one approval. Change the approver map if the wrong person holds it.",
            )

        self.store.invalidate_request_role(
            engagement.id,
            request.id,
            wanted_role,
            packet_hash=packet.packet_hash,
            reason=f"{request.id} {wanted_role} re-approved for packet v{packet.version}",
        )
        approval = Approval(
            id="apr_"
            + canonical_hash(
                {
                    "engagement": engagement.id,
                    "request": request.id,
                    "role": wanted_role,
                    "actor": actor,
                    "hash": packet.packet_hash,
                }
            )[:20],
            engagement_id=engagement.id,
            packet_id=f"{engagement.id}-v{packet.version}",
            packet_hash=packet.packet_hash,
            request_id=request.id,
            role=wanted_role,
            actor=actor,
            phrase=expected,
            created_at=self.now(),
        )
        self.store.add_approval(approval)
        self.store.audit(
            "approval.recorded",
            actor,
            {
                "engagement_id": engagement.id,
                "request_id": request.id,
                "role": wanted_role,
                "packet_hash": packet.packet_hash,
                "version": packet.version,
                "phrase": expected,
                "source": source,
            },
        )
        return approval

    def approvals(self, engagement_id: str, *, include_stale: bool = True) -> list[Approval]:
        return self.store.list_approvals(
            engagement_id, status=None if include_stale else ApprovalStatus.VALID.value
        )

    def sync_messages(
        self, engagement_id: str, messages: list[SwitchMessage], *, room_id: str | None = None
    ) -> SyncResult:
        result = SyncResult(scanned=len(messages))
        for message in messages:
            parsed = parse_approval_phrase(message.body, engagement_id)
            if parsed is None:
                result.ignored += 1
                continue
            request_id, role = parsed
            self.store.audit(
                "switch.approval_seen",
                message.sender_name,
                {
                    "engagement_id": engagement_id,
                    "room_id": room_id or message.room_id,
                    "request_id": request_id,
                    "role": role,
                    "message_id": message.id,
                },
            )
            try:
                approval = self.approve(
                    engagement_id,
                    request_id,
                    role,
                    message.sender_name,
                    phrase=message.body,
                    source=f"switch:{room_id or message.room_id}",
                )
            except (ApprovalError, NotFoundError) as exc:
                result.errors.append(f"{message.sender_name}: {exc}")
                continue
            result.recorded += 1
            result.approvals.append(approval)
        return result

    # ----------------------------------------------------------------- gate
    def gate(self, engagement_id: str) -> GateResult:
        packet = self.store.latest_packet(engagement_id)
        engagement = self.store.load_engagement(engagement_id)
        return gate_packet(
            packet=packet,
            approvals=self.store.list_approvals(engagement_id),
            approvers=self.approvers,
            policy=self.policy,
            engagement=engagement,
        )

    def pending(self, engagement_id: str) -> list[str]:
        return self.gate(engagement_id).pending

    # ----------------------------------------------------------------- seal
    def seal(self, engagement_id: str, *, valid_days: int = 30) -> Receipt:
        packet = self.store.latest_packet(engagement_id)
        if packet.status == PacketStatus.SEALED.value:
            raise StateError("packet is already sealed", hint="A sealed packet is immutable.")
        gate = self.gate(engagement_id)
        if not gate.ok:
            failures = "; ".join(check.detail for check in gate.failures)
            raise GateError(
                f"change order cannot be sealed: {failures}",
                hint="Run `scopeguard status --engagement <engagement>` to see pending approvals.",
            )
        moment = self.now()
        receipt = build_receipt(
            packet=packet,
            approvals=self.store.approvals_for_packet(packet),
            policy_id=f"{self.policy.id}@v{self.policy.version}",
            issued_at=moment,
            expires_at=moment + timedelta(days=valid_days),
            keyring=self.keyring,
            chain_prev=self.store.previous_receipt_hash(engagement_id),
        )
        self.store.save_receipt(receipt)
        packet.status = PacketStatus.SEALED
        packet.sealed_at = moment
        self.store.update_packet(packet)
        self.store.audit(
            "change_order.sealed",
            "clerk",
            {
                "engagement_id": engagement_id,
                "receipt_id": receipt.body.receipt_id,
                "packet_hash": packet.packet_hash,
                "body_hash": receipt.body_hash,
                "line_items": len(receipt.body.line_items),
                "approvals": len(receipt.body.approvals),
                "total": receipt.body.total,
            },
        )
        return receipt

    # ---------------------------------------------------------- countersign
    def countersign(
        self,
        engagement_id: str,
        issuer: str,
        *,
        keyring: Keyring | None = None,
        receipt_id: str | None = None,
    ) -> Receipt:
        """Add a named client's acceptance to the latest sealed change order."""
        receipt = (
            self.store.load_receipt(receipt_id)
            if receipt_id
            else self.store.latest_receipt(engagement_id)
        )
        updated = countersign_receipt(receipt, keyring or self.client_keyring, issuer)
        self.store.save_receipt(updated)
        self.store.audit(
            "change_order.countersigned",
            issuer,
            {
                "engagement_id": receipt.body.engagement_id,
                "receipt_id": updated.body.receipt_id,
                "body_hash": updated.body_hash,
                "client_public_key": updated.acceptance.public_key if updated.acceptance else None,
            },
        )
        return updated

    # ---------------------------------------------------------------- verify
    def verify(
        self,
        receipt: Receipt,
        *,
        require_unexpired: bool = False,
        require_acceptance: bool = False,
        pinned_key: bool = False,
        pinned_client_key: bool = False,
    ) -> VerificationResult:
        expected = self.keyring.public_key_b64 if pinned_key else None
        expected_client = self.client_keyring.public_key_b64 if pinned_client_key else None
        result = verify_receipt(
            receipt,
            expected_public_key=expected,
            expected_counter_public_key=expected_client,
            now=self.now(),
            require_unexpired=require_unexpired,
            require_acceptance=require_acceptance,
        )
        try:
            packet = self.store.load_packet(receipt.body.packet_id)
        except NotFoundError:
            packet = None
        if packet is None:
            result.checks.append(
                VerificationCheck(
                    code="packet_present",
                    ok=True,
                    detail="packet not present locally; change order verified from its signed body",
                )
            )
        else:
            recomputed = packet_hash(packet.body())
            result.checks.append(
                VerificationCheck(
                    code="packet_rehash",
                    ok=recomputed == receipt.body.packet_hash,
                    detail="local packet rehashes to the change order's packet hash"
                    if recomputed == receipt.body.packet_hash
                    else "local packet does not match the change order",
                )
            )
            result.checks.append(
                VerificationCheck(
                    code="packet_version",
                    ok=packet.version == receipt.body.packet_version,
                    detail=f"change order binds packet v{receipt.body.packet_version}",
                )
            )
            packet_decisions = {item.request_id: item for item in packet.decisions}
            receipt_requests = {item.request_id for item in receipt.body.line_items}
            same_commitments = {
                request_id
                for request_id, decision in packet_decisions.items()
                if decision.is_commitment
            } == receipt_requests
            result.checks.append(
                VerificationCheck(
                    code="line_items_match_packet",
                    ok=same_commitments,
                    detail="change order line items match the local packet",
                )
            )
        recorded = {
            item.body.receipt_id for item in self.store.list_receipts(receipt.body.engagement_id)
        }
        if receipt.body.receipt_id in recorded:
            result.checks.append(
                VerificationCheck(
                    code="recorded",
                    ok=True,
                    detail="change order is recorded in the local ledger",
                )
            )
        result.ok = all(check.ok for check in result.checks)
        if not result.ok:
            result.summary = "Change order failed verification: " + ", ".join(
                check.code for check in result.checks if not check.ok
            )
        elif receipt.acceptance is not None and result.ok:
            result.summary = "Change order is authentic, internally consistent, and accepted."
        return result

    def verify_latest(
        self, engagement_id: str, **kwargs: bool
    ) -> tuple[Receipt, VerificationResult]:
        receipt = self.store.latest_receipt(engagement_id)
        return receipt, self.verify(receipt, **kwargs)

    # -------------------------------------------------------------- handoff
    def handoff_text(self, engagement_id: str) -> str:
        receipt = self.store.latest_receipt(engagement_id)
        accepted = (
            f"Countersigned by {receipt.acceptance.issuer}"
            if receipt.acceptance is not None
            else "Awaiting client countersignature"
        )
        lines = [
            f"**Scope Guard handoff: {receipt.body.client} ({receipt.body.engagement_id})**",
            "",
            f"Change order: `{receipt.body.receipt_id}`",
            f"Packet: v{receipt.body.packet_version} / `{receipt.body.packet_hash[:16]}...`",
            f"Policy: `{receipt.body.policy_id}`",
            f"Status: {accepted}",
            f"Total: **{receipt.body.total:,.2f} {receipt.body.currency}** "
            f"(subtotal {receipt.body.subtotal:,.2f}, "
            f"discount {receipt.body.discount_total:,.2f})",
            "",
            "**Signed line items**",
        ]
        for item in receipt.body.line_items:
            lines.append(
                f"- **{item.request_id}** - {item.scope} "
                f"({item.amount:+,.2f} {receipt.body.currency})"
            )
            for condition in item.conditions:
                lines.append(f"  - Condition: {condition}")
        if receipt.body.declined:
            lines.append("")
            lines.append("**Not committed**")
            for request_id in receipt.body.declined:
                lines.append(f"- {request_id}")
        lines += [
            "",
            "Only the line items above leave this room. Anything not listed is not approved.",
            f"Verify: `scopeguard verify --receipt {receipt.body.receipt_id}`",
        ]
        return "\n".join(lines)

    # -------------------------------------------------------------- summary
    def status(self, engagement_id: str) -> StatusView:
        engagement = self.store.load_engagement(engagement_id)
        packet = self.store.latest_packet(engagement_id)
        approvals = self.store.list_approvals(engagement_id)
        gate = self.gate(engagement_id)
        receipts = self.store.list_receipts(engagement_id)
        return {
            "engagement": engagement,
            "packet": packet,
            "approvals": approvals,
            "gate": gate,
            "receipt": receipts[0] if receipts else None,
            "evidence_count": len(self.store.list_evidence(engagement_id)),
            "packet_count": self.store.count_packets(engagement_id),
        }

    def decisions(self, engagement_id: str) -> dict[str, Decision]:
        packet = self.store.latest_packet(engagement_id)
        return {item.request_id: item for item in packet.decisions}
