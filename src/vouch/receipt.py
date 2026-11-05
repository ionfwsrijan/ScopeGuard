"""Canonical JSON, approval phrases, and Ed25519 receipts.

The receipt is the product's trust boundary. Verification never trusts the
database row it came from: it recomputes every hash from the serialized body.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel

from vouch.models import (
    Approval,
    ApprovalStatus,
    Packet,
    Receipt,
    ReceiptApproval,
    ReceiptBody,
    ReceiptDecision,
    VerificationCheck,
    VerificationResult,
)

PHRASE_RE = re.compile(
    r"APPROVE\s+(?P<deal>[A-Z0-9][A-Z0-9-]{1,63})\s+"
    r"(?P<claim>[A-Z][A-Z0-9-]{1,23})\s+AS\s+(?P<role>[A-Z][A-Z0-9_]{1,31})",
    re.IGNORECASE,
)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return value


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(
        _to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def packet_hash(packet: Packet | Any) -> str:
    body = packet.body() if isinstance(packet, Packet) else packet
    return canonical_hash(body)


def expected_phrase(deal_id: str, claim_id: str, role: str) -> str:
    return f"APPROVE {deal_id.upper()} {claim_id.upper()} AS {role.upper()}"


def parse_approval_phrase(text: str, deal_id: str) -> tuple[str, str] | None:
    """Find exactly one well-formed approval for this deal in free text.

    Returns ``(claim_id, role)``. The sender identity is checked separately;
    a phrase in a bot's summary is not an approval.
    """
    matches = [
        match
        for match in PHRASE_RE.finditer(text)
        if match.group("deal").upper() == deal_id.upper()
    ]
    if len(matches) != 1:
        return None
    match = matches[0]
    return match.group("claim").upper(), match.group("role").upper()


def phrases_match(text: str, deal_id: str, claim_id: str, role: str) -> bool:
    return expected_phrase(deal_id, claim_id, role) in text.upper()


@dataclass(frozen=True)
class Keyring:
    """An Ed25519 key pair used to sign receipts."""

    private_key: Ed25519PrivateKey
    path: Path | None = None

    @property
    def public_key_b64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def sign(self, message: bytes) -> str:
        return base64.urlsafe_b64encode(self.private_key.sign(message)).decode("ascii")

    @classmethod
    def generate(cls, path: Path | None = None) -> Keyring:
        key = cls(Ed25519PrivateKey.generate(), path)
        if path is not None:
            key.save(path)
        return key

    @classmethod
    def load(cls, path: Path) -> Keyring:
        data = path.read_bytes()
        private = serialization.load_pem_private_key(data, password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 private key")
        return cls(private, path)

    @classmethod
    def load_or_create(cls, path: Path) -> Keyring:
        if path.exists():
            return cls.load(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls.generate(path)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.write_bytes(data)
        with contextlib.suppress(OSError):  # pragma: no cover - Windows best effort
            path.chmod(0o600)


def public_key_from_b64(value: str) -> Ed25519PublicKey:
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    return Ed25519PublicKey.from_public_bytes(raw)


def _receipt_id(
    deal_id: str, packet_digest: str, issued_at: datetime, chain_prev: str | None
) -> str:
    seed = {
        "deal_id": deal_id,
        "packet_hash": packet_digest,
        "issued_at": issued_at.astimezone(UTC).replace(microsecond=0).isoformat(),
        "chain_prev": chain_prev,
    }
    return "vch_" + canonical_hash(seed)[:24]


def build_receipt(
    *,
    packet: Packet,
    approvals: list[Approval],
    policy_id: str,
    issued_at: datetime,
    expires_at: datetime,
    keyring: Keyring,
    chain_prev: str | None = None,
) -> Receipt:
    """Build and sign a receipt from a packet whose gate has already passed."""
    valid = [
        item
        for item in approvals
        if item.status == ApprovalStatus.VALID.value and item.packet_hash == packet.packet_hash
    ]
    commitments: list[ReceiptDecision] = []
    declined: list[str] = []
    for decision in packet.decisions:
        if decision.is_commitment:
            commitments.append(
                ReceiptDecision(
                    claim_id=decision.claim_id,
                    verdict=decision.verdict,
                    commitment=decision.approved_text,
                    conditions=list(decision.conditions),
                )
            )
        else:
            declined.append(decision.claim_id)

    body = ReceiptBody(
        receipt_id=_receipt_id(packet.deal_id, packet.packet_hash, issued_at, chain_prev),
        deal_id=packet.deal_id,
        account=packet.account,
        packet_id=f"{packet.deal_id}-v{packet.version}",
        packet_version=packet.version,
        packet_hash=packet.packet_hash,
        policy_id=policy_id,
        issued_at=issued_at,
        expires_at=expires_at,
        commitments=commitments,
        declined=declined,
        approvals=[
            ReceiptApproval(
                claim_id=item.claim_id,
                role=item.role,
                actor=item.actor,
                phrase=item.phrase,
                approved_at=item.created_at,
            )
            for item in sorted(valid, key=lambda item: (item.claim_id, item.role, item.actor))
        ],
        chain_prev=chain_prev,
    )
    digest = canonical_hash(body)
    signature = keyring.sign(digest.encode("ascii"))
    return Receipt(
        body=body,
        body_hash=digest,
        signature=signature,
        public_key=keyring.public_key_b64,
    )


def receipt_public_key(receipt: Receipt) -> Ed25519PublicKey:
    return public_key_from_b64(receipt.public_key)


def verify_receipt(
    receipt: Receipt,
    *,
    expected_public_key: str | None = None,
    now: datetime | None = None,
    require_unexpired: bool = False,
) -> VerificationResult:
    checks: list[VerificationCheck] = []
    moment = (now or datetime.now(UTC)).astimezone(UTC)

    def add(code: str, ok: bool, detail: str) -> None:
        checks.append(VerificationCheck(code=code, ok=ok, detail=detail))

    add("algorithm", receipt.algorithm == "Ed25519", f"algorithm={receipt.algorithm}")
    recomputed = canonical_hash(receipt.body)
    add(
        "body_hash",
        recomputed == receipt.body_hash,
        "body hash matches the serialized receipt body"
        if recomputed == receipt.body_hash
        else f"body hash mismatch: computed {recomputed[:16]}..., stored {receipt.body_hash[:16]}...",
    )
    add(
        "receipt_id",
        receipt.body.receipt_id
        == _receipt_id(
            receipt.body.deal_id,
            receipt.body.packet_hash,
            receipt.body.issued_at,
            receipt.body.chain_prev,
        ),
        "receipt id is derived from the signed body",
    )

    try:
        key = receipt_public_key(receipt)
        key.verify(
            base64.urlsafe_b64decode(receipt.signature.encode("ascii")),
            receipt.body_hash.encode("ascii"),
        )
        signature_ok = True
        detail = "Ed25519 signature verifies against the embedded public key"
    except (InvalidSignature, ValueError, TypeError) as exc:
        signature_ok = False
        detail = f"signature invalid: {exc}"
    add("signature", signature_ok, detail)

    if expected_public_key is not None:
        add(
            "pinned_key",
            expected_public_key == receipt.public_key,
            "receipt key matches the pinned key"
            if expected_public_key == receipt.public_key
            else "receipt key does not match the pinned key",
        )

    pairs = [(item.claim_id, item.role) for item in receipt.body.approvals]
    add("no_duplicate_approvals", len(pairs) == len(set(pairs)), "one approval per claim and role")
    covered = {item.claim_id for item in receipt.body.approvals}
    missing = [item.claim_id for item in receipt.body.commitments if item.claim_id not in covered]
    add(
        "commitments_approved",
        not missing,
        "every commitment has at least one recorded approval"
        if not missing
        else f"commitments without an approval: {', '.join(missing)}",
    )
    add(
        "approval_binding",
        all(
            len(item.phrase.split()) >= 4
            and item.phrase.split()[1].upper() == receipt.body.deal_id.upper()
            for item in receipt.body.approvals
        )
        if receipt.body.approvals
        else False,
        "approval phrases name this deal",
    )
    if require_unexpired:
        add(
            "unexpired",
            moment <= receipt.body.expires_at,
            f"expires at {receipt.body.expires_at.isoformat()}",
        )
    else:
        add("unexpired", True, "expiry not enforced in this verification mode")

    ok = all(check.ok for check in checks)
    failures = [check.code for check in checks if not check.ok]
    summary = (
        "Receipt is authentic and internally consistent."
        if ok
        else "Receipt failed verification: " + ", ".join(failures)
    )
    return VerificationResult(
        ok=ok,
        receipt_id=receipt.body.receipt_id,
        checks=checks,
        summary=summary,
    )
