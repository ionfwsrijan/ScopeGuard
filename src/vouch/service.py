"""The Vouch service: one orchestration layer shared by CLI, API, demo and bots.

Keeping the workflow here means the offline demo and the real Switch path
execute the same code. Switch is transport and conversation, not a second
implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TypedDict

from vouch.assess import assess_deal
from vouch.challenge import apply_revisions, challenge_packet, gate_packet
from vouch.clock import utcnow
from vouch.errors import ApprovalError, ConfigError, GateError, NotFoundError, StateError
from vouch.fixtures import load_approvers, load_deal, load_evidence
from vouch.models import (
    Approval,
    ApprovalStatus,
    Approvers,
    Deal,
    Decision,
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
from vouch.policy import Policy
from vouch.receipt import (
    Keyring,
    build_receipt,
    canonical_hash,
    expected_phrase,
    packet_hash,
    parse_approval_phrase,
    verify_receipt,
)
from vouch.store import Store


@dataclass(frozen=True)
class VouchPaths:
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
    def receipts(self) -> Path:
        return self.home / "receipts"

    @classmethod
    def discover(
        cls,
        root: Path | None = None,
        home: Path | None = None,
        *,
        fixture: str = "acme",
        policy: Path | None = None,
        approvers: Path | None = None,
    ) -> VouchPaths:
        base = (root or Path.cwd()).resolve()
        runtime = (home or Path.home() / ".vouch").resolve()
        fixtures_dir = base / "fixtures" / fixture
        return cls(
            root=base,
            home=runtime,
            policy=policy or base / "policies" / "deal_policy_v1.yaml",
            fixtures=fixtures_dir,
            approvers=approvers or fixtures_dir / "approvers.yaml",
        )

    def ensure(self) -> None:
        for folder in (self.home, self.receipts, self.key.parent):
            folder.mkdir(parents=True, exist_ok=True)


class StatusView(TypedDict):
    """Everything one screen needs about a deal, with concrete types."""

    deal: Deal
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


class VouchService:
    def __init__(
        self,
        *,
        store: Store,
        policy: Policy,
        approvers: Approvers,
        paths: VouchPaths,
        now: Callable[[], datetime] = utcnow,
        keyring: Keyring | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self.approvers = approvers
        self.paths = paths
        self.now = now
        self._keyring = keyring

    # --------------------------------------------------------------- setup
    @classmethod
    def open(
        cls,
        paths: VouchPaths,
        *,
        now: Callable[[], datetime] = utcnow,
        keyring: Keyring | None = None,
    ) -> VouchService:
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
        )

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> VouchService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def keyring(self) -> Keyring:
        if self._keyring is None:
            self.paths.ensure()
            self._keyring = Keyring.load_or_create(self.paths.key)
        return self._keyring

    # --------------------------------------------------------------- intake
    def ingest_fixture(self, name: str = "acme") -> tuple[Deal, EvidenceBook]:
        fixture_dir = self.paths.root / "fixtures" / name
        deal = load_deal(fixture_dir / "request.yaml")
        evidence = load_evidence(fixture_dir / "evidence.yaml", deal)
        if deal.id != self.approvers.deal_id:
            raise ConfigError(
                f"deal {deal.id!r} does not match approver map {self.approvers.deal_id!r}"
            )
        self.store.save_deal(deal, now=self.now())
        self.store.replace_evidence(deal.id, evidence.items)
        self.store.audit(
            "deal.ingested",
            "vouch",
            {
                "deal_id": deal.id,
                "claims": len(deal.claims),
                "evidence": len(evidence.items),
                "fixture": name,
            },
        )
        return deal, evidence

    def deal(self, deal_id: str) -> Deal:
        return self.store.load_deal(deal_id)

    def evidence(self, deal_id: str) -> EvidenceBook:
        return EvidenceBook(deal_id=deal_id, items=self.store.list_evidence(deal_id))

    # ---------------------------------------------------------------- assess
    def assess(self, deal_id: str, *, note: str | None = None) -> Packet:
        deal = self.store.load_deal(deal_id)
        evidence = self.evidence(deal_id)
        previous = self.store.list_packets(deal_id)
        version = (previous[0].version + 1) if previous else 1
        moment = self.now()
        decisions = assess_deal(
            deal=deal, evidence=evidence, policy=self.policy, as_of=moment.date()
        )
        packet = Packet(
            deal_id=deal.id,
            version=version,
            account=deal.account,
            currency=deal.currency,
            decisions=decisions,
            status=PacketStatus.DRAFT,
            packet_hash="",
            previous_hash=previous[0].packet_hash if previous else None,
            created_at=moment,
        )
        packet.packet_hash = packet_hash(packet.body())
        self.store.save_packet(packet)
        invalidated = self.store.invalidate_approvals(
            deal.id,
            keep_hash=packet.packet_hash,
            reason=f"packet v{version} replaced earlier commitments",
        )
        self.store.audit(
            "packet.assessed",
            "actuary",
            {
                "deal_id": deal.id,
                "version": version,
                "packet_hash": packet.packet_hash,
                "decisions": {item.claim_id: item.verdict for item in decisions},
                "invalidated_approvals": invalidated,
                "note": note,
            },
        )
        return packet

    # ------------------------------------------------------------- challenge
    def challenge(self, deal_id: str) -> Packet:
        packet = self.store.latest_packet(deal_id)
        if packet.status == PacketStatus.SEALED.value:
            raise StateError("packet is already sealed")
        deal = self.store.load_deal(deal_id)
        evidence = self.evidence(deal_id)
        findings = challenge_packet(
            packet=packet,
            deal=deal,
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
            "examiner",
            {
                "deal_id": deal_id,
                "version": packet.version,
                "packet_hash": packet.packet_hash,
                "blocks": blocks,
                "warnings": sum(1 for item in findings if item.severity == "warn"),
                "findings": [item.code for item in findings],
            },
        )
        return packet

    def revise(self, deal_id: str, *, note: str | None = None) -> Packet:
        packet = self.store.latest_packet(deal_id)
        if not packet.has_blocks():
            raise StateError("nothing to revise: the current packet has no blocking findings")
        deal = self.store.load_deal(deal_id)
        decisions = apply_revisions(
            packet=packet, deal=deal, policy=self.policy, findings=packet.findings
        )
        if canonical_hash([item.model_dump(mode="json") for item in decisions]) == canonical_hash(
            [item.model_dump(mode="json") for item in packet.decisions]
        ):
            raise StateError(
                "Examiner findings have no automatic remediation; a human must edit the ask"
            )
        moment = self.now()
        revised = Packet(
            deal_id=deal.id,
            version=packet.version + 1,
            account=deal.account,
            currency=deal.currency,
            decisions=decisions,
            status=PacketStatus.DRAFT,
            packet_hash="",
            previous_hash=packet.packet_hash,
            created_at=moment,
        )
        revised.packet_hash = packet_hash(revised.body())
        self.store.save_packet(revised)
        invalidated = self.store.invalidate_approvals(
            deal.id,
            keep_hash=revised.packet_hash,
            reason=f"packet revised to v{revised.version} after Examiner findings",
        )
        self.store.audit(
            "packet.revised",
            "clerk",
            {
                "deal_id": deal.id,
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
        deal_id: str,
        claim_id: str,
        role: str,
        actor: str,
        *,
        phrase: str | None = None,
        source: str = "cli",
    ) -> Approval:
        packet = self.store.latest_packet(deal_id)
        if packet.status == PacketStatus.SEALED.value:
            raise StateError("packet is already sealed")
        deal = self.store.load_deal(deal_id)
        try:
            claim = deal.claim(claim_id)
        except KeyError as exc:
            raise NotFoundError(f"claim not found: {claim_id}") from exc
        try:
            decision = packet.decision(claim.id)
        except KeyError as exc:
            raise NotFoundError(f"claim has no decision in packet v{packet.version}") from exc
        wanted_role = role.upper()
        if wanted_role not in decision.required_roles:
            raise ApprovalError(
                f"{wanted_role} is not a required approver for {claim.id}",
                hint=f"Required roles: {', '.join(decision.required_roles) or 'none'}.",
            )
        if not self.approvers.can_hold(actor, wanted_role):
            raise ApprovalError(
                f"{actor} does not hold {wanted_role} in the approver map",
                hint=f"{wanted_role} is held by: {', '.join(self.approvers.people(wanted_role)) or 'nobody'}.",
            )
        expected = expected_phrase(deal.id, claim.id, wanted_role)
        if phrase is not None:
            parsed = parse_approval_phrase(phrase, deal.id)
            if parsed != (claim.id, wanted_role.upper()) or expected not in phrase.upper():
                raise ApprovalError(
                    f"approval phrase does not match: {phrase!r}",
                    hint=f"Expected exactly: {expected}",
                )
        existing = [
            item
            for item in self.store.approvals_for_packet(packet)
            if item.claim_id == claim.id and item.role == wanted_role
        ]
        if existing:
            if existing[0].actor.casefold() == actor.casefold():
                return existing[0]
            raise ApprovalError(
                f"{claim.id} / {wanted_role} already approved by {existing[0].actor}",
                hint="One role, one approval. Change the approver map if the wrong person holds the role.",
            )

        self.store.invalidate_claim_role(
            deal.id,
            claim.id,
            wanted_role,
            packet_hash=packet.packet_hash,
            reason=f"{claim.id} {wanted_role} re-approved for packet v{packet.version}",
        )
        approval = Approval(
            id="apr_"
            + canonical_hash(
                {
                    "deal": deal.id,
                    "claim": claim.id,
                    "role": wanted_role,
                    "actor": actor,
                    "hash": packet.packet_hash,
                }
            )[:20],
            deal_id=deal.id,
            packet_id=f"{deal.id}-v{packet.version}",
            packet_hash=packet.packet_hash,
            claim_id=claim.id,
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
                "deal_id": deal.id,
                "claim_id": claim.id,
                "role": wanted_role,
                "packet_hash": packet.packet_hash,
                "version": packet.version,
                "phrase": expected,
                "source": source,
            },
        )
        return approval

    def approvals(self, deal_id: str, *, include_stale: bool = True) -> list[Approval]:
        return self.store.list_approvals(
            deal_id, status=None if include_stale else ApprovalStatus.VALID.value
        )

    def sync_messages(
        self, deal_id: str, messages: list[SwitchMessage], *, room_id: str | None = None
    ) -> SyncResult:
        result = SyncResult(scanned=len(messages))
        for message in messages:
            parsed = parse_approval_phrase(message.body, deal_id)
            if parsed is None:
                result.ignored += 1
                continue
            claim_id, role = parsed
            self.store.audit(
                "switch.approval_seen",
                message.sender_name,
                {
                    "deal_id": deal_id,
                    "room_id": room_id or message.room_id,
                    "claim_id": claim_id,
                    "role": role,
                    "message_id": message.id,
                },
            )
            try:
                approval = self.approve(
                    deal_id,
                    claim_id,
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
    def gate(self, deal_id: str) -> GateResult:
        packet = self.store.latest_packet(deal_id)
        deal = self.store.load_deal(deal_id)
        return gate_packet(
            packet=packet,
            approvals=self.store.list_approvals(deal_id),
            approvers=self.approvers,
            policy=self.policy,
            deal=deal,
        )

    def pending(self, deal_id: str) -> list[str]:
        return self.gate(deal_id).pending

    # ----------------------------------------------------------------- seal
    def seal(self, deal_id: str, *, valid_days: int = 30) -> Receipt:
        packet = self.store.latest_packet(deal_id)
        if packet.status == PacketStatus.SEALED.value:
            raise StateError("packet is already sealed", hint="A sealed packet is immutable.")
        gate = self.gate(deal_id)
        if not gate.ok:
            failures = "; ".join(check.detail for check in gate.failures)
            raise GateError(
                f"packet cannot be sealed: {failures}",
                hint="Run `vouch status --deal <deal>` to see pending approvals.",
            )
        moment = self.now()
        receipt = build_receipt(
            packet=packet,
            approvals=self.store.approvals_for_packet(packet),
            policy_id=f"{self.policy.id}@v{self.policy.version}",
            issued_at=moment,
            expires_at=moment + timedelta(days=valid_days),
            keyring=self.keyring,
            chain_prev=self.store.previous_receipt_hash(deal_id),
        )
        self.store.save_receipt(receipt)
        packet.status = PacketStatus.SEALED
        packet.sealed_at = moment
        self.store.update_packet(packet)
        self.store.audit(
            "receipt.sealed",
            "clerk",
            {
                "deal_id": deal_id,
                "receipt_id": receipt.body.receipt_id,
                "packet_hash": packet.packet_hash,
                "body_hash": receipt.body_hash,
                "commitments": len(receipt.body.commitments),
                "approvals": len(receipt.body.approvals),
            },
        )
        return receipt

    # ---------------------------------------------------------------- verify
    def verify(
        self,
        receipt: Receipt,
        *,
        require_unexpired: bool = False,
        pinned_key: bool = False,
    ) -> VerificationResult:
        expected = self.keyring.public_key_b64 if pinned_key else None
        result = verify_receipt(
            receipt,
            expected_public_key=expected,
            now=self.now(),
            require_unexpired=require_unexpired,
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
                    detail="packet not present locally; receipt verified from its signed body",
                )
            )
        else:
            recomputed = packet_hash(packet.body())
            result.checks.append(
                VerificationCheck(
                    code="packet_rehash",
                    ok=recomputed == receipt.body.packet_hash,
                    detail="local packet rehashes to the receipt's packet hash"
                    if recomputed == receipt.body.packet_hash
                    else "local packet does not match the receipt",
                )
            )
            result.checks.append(
                VerificationCheck(
                    code="packet_version",
                    ok=packet.version == receipt.body.packet_version,
                    detail=f"receipt binds packet v{receipt.body.packet_version}",
                )
            )
            packet_decisions = {item.claim_id: item for item in packet.decisions}
            receipt_claims = {item.claim_id for item in receipt.body.commitments}
            same_commitments = {
                claim_id
                for claim_id, decision in packet_decisions.items()
                if decision.is_commitment
            } == receipt_claims
            result.checks.append(
                VerificationCheck(
                    code="commitments_match_packet",
                    ok=same_commitments,
                    detail="receipt commitments match the local packet",
                )
            )
        if receipt.body.receipt_id in {
            item.body.receipt_id for item in self.store.list_receipts(receipt.body.deal_id)
        }:
            result.checks.append(
                VerificationCheck(
                    code="recorded",
                    ok=True,
                    detail="receipt is recorded in the local ledger",
                )
            )
        result.ok = all(check.ok for check in result.checks)
        if not result.ok:
            result.summary = "Receipt failed verification: " + ", ".join(
                check.code for check in result.checks if not check.ok
            )
        return result

    def verify_latest(self, deal_id: str, **kwargs: bool) -> tuple[Receipt, VerificationResult]:
        receipt = self.store.latest_receipt(deal_id)
        return receipt, self.verify(receipt, **kwargs)

    # -------------------------------------------------------------- handoff
    def handoff_text(self, deal_id: str) -> str:
        receipt = self.store.latest_receipt(deal_id)
        lines = [
            f"**Vouch handoff: {receipt.body.account} ({receipt.body.deal_id})**",
            "",
            f"Receipt: `{receipt.body.receipt_id}`",
            f"Packet: v{receipt.body.packet_version} / `{receipt.body.packet_hash[:16]}...`",
            f"Policy: `{receipt.body.policy_id}`",
            "",
            "**Approved commitments**",
        ]
        for item in receipt.body.commitments:
            lines.append(f"- **{item.claim_id}** - {item.commitment}")
            for condition in item.conditions:
                lines.append(f"  - Condition: {condition}")
        if receipt.body.declined:
            lines.append("")
            lines.append("**Not committed**")
            for claim_id in receipt.body.declined:
                lines.append(f"- {claim_id}")
        lines += [
            "",
            "Only the commitments above leave this room. Anything not listed is not approved.",
            f"Verify: `vouch verify --receipt {receipt.body.receipt_id}`",
        ]
        return "\n".join(lines)

    # -------------------------------------------------------------- summary
    def status(self, deal_id: str) -> StatusView:
        deal = self.store.load_deal(deal_id)
        packet = self.store.latest_packet(deal_id)
        approvals = self.store.list_approvals(deal_id)
        gate = self.gate(deal_id)
        receipts = self.store.list_receipts(deal_id)
        return {
            "deal": deal,
            "packet": packet,
            "approvals": approvals,
            "gate": gate,
            "receipt": receipts[0] if receipts else None,
            "evidence_count": len(self.store.list_evidence(deal_id)),
            "packet_count": self.store.count_packets(deal_id),
        }

    def decisions(self, deal_id: str) -> dict[str, Decision]:
        packet = self.store.latest_packet(deal_id)
        return {item.claim_id: item for item in packet.decisions}
