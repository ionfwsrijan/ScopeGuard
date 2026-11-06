"""Canonical JSON, signature phrases, signed change orders, and countersignatures.

The change order is the product's trust boundary. Verification never trusts the
database row it came from: it recomputes every hash from the serialized body and
checks both the issuer's signature and, when present, the client's acceptance.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel

from scopeguard.models import (
    Acceptance,
    Approval,
    ApprovalStatus,
    Packet,
    Receipt,
    ReceiptApproval,
    ReceiptBody,
    ReceiptLineItem,
    VerificationCheck,
    VerificationResult,
)

PHRASE_RE = re.compile(
    r"SIGN\s+(?P<engagement>[A-Z0-9][A-Z0-9-]{1,63})\s+"
    r"(?P<request>[A-Z][A-Z0-9-]{1,23})\s+AS\s+(?P<role>[A-Z][A-Z0-9_]{1,31})",
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


def expected_phrase(engagement_id: str, request_id: str, role: str) -> str:
    return f"SIGN {engagement_id.upper()} {request_id.upper()} AS {role.upper()}"


def parse_approval_phrase(text: str, engagement_id: str) -> tuple[str, str] | None:
    """Find exactly one well-formed signature for this engagement in free text.

    Returns ``(request_id, role)``. The sender identity is checked separately;
    a phrase in a bot's summary is not a signature.
    """
    matches = [
        match
        for match in PHRASE_RE.finditer(text)
        if match.group("engagement").upper() == engagement_id.upper()
    ]
    if len(matches) != 1:
        return None
    match = matches[0]
    return match.group("request").upper(), match.group("role").upper()


def phrases_match(text: str, engagement_id: str, request_id: str, role: str) -> bool:
    return expected_phrase(engagement_id, request_id, role) in text.upper()


@dataclass(frozen=True)
class Keyring:
    """An Ed25519 key pair used to sign change orders or acceptances."""

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
    engagement_id: str, packet_digest: str, issued_at: datetime, chain_prev: str | None
) -> str:
    seed = {
        "engagement_id": engagement_id,
        "packet_hash": packet_digest,
        "issued_at": issued_at.astimezone(UTC).replace(microsecond=0).isoformat(),
        "chain_prev": chain_prev,
    }
    return "co_" + canonical_hash(seed)[:24]


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
    """Build and sign a change order from a packet whose gate has already passed."""
    valid = [
        item
        for item in approvals
        if item.status == ApprovalStatus.VALID.value and item.packet_hash == packet.packet_hash
    ]
    line_items: list[ReceiptLineItem] = []
    declined: list[str] = []
    for decision in packet.decisions:
        if decision.is_commitment:
            line_items.append(
                ReceiptLineItem(
                    request_id=decision.request_id,
                    kind=str(decision.approved_value.get("kind") or decision.request_id),
                    verdict=decision.verdict,
                    scope=decision.priced_text,
                    amount=round(decision.amount or 0.0, 2),
                    conditions=list(decision.conditions),
                )
            )
        else:
            declined.append(decision.request_id)

    subtotal = round(sum(item.amount for item in line_items if item.amount > 0), 2)
    discount_total = round(sum(item.amount for item in line_items if item.amount < 0), 2)
    body = ReceiptBody(
        receipt_id=_receipt_id(packet.engagement_id, packet.packet_hash, issued_at, chain_prev),
        engagement_id=packet.engagement_id,
        client=packet.client,
        packet_id=f"{packet.engagement_id}-v{packet.version}",
        packet_version=packet.version,
        packet_hash=packet.packet_hash,
        policy_id=policy_id,
        issued_at=issued_at,
        expires_at=expires_at,
        currency=packet.currency,
        line_items=line_items,
        declined=declined,
        approvals=[
            ReceiptApproval(
                request_id=item.request_id,
                role=item.role,
                actor=item.actor,
                phrase=item.phrase,
                approved_at=item.created_at,
            )
            for item in sorted(valid, key=lambda item: (item.request_id, item.role, item.actor))
        ],
        subtotal=subtotal,
        discount_total=discount_total,
        total=round(subtotal + discount_total, 2),
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


def acceptance_message(body_hash: str, issuer: str, public_key: str) -> str:
    """The exact payload a client signs when accepting a change order."""
    return canonical_json(
        {
            "body_hash": body_hash,
            "issuer": issuer.strip(),
            "public_key": public_key,
        }
    )


def countersign(receipt: Receipt, keyring: Keyring, issuer: str) -> Receipt:
    """Add a client acceptance signature over the issuer's signed body hash."""
    clean = issuer.strip()
    if not clean:
        raise ValueError("a countersignature needs a named client signer")
    if receipt.acceptance is not None:
        raise ValueError("receipt already has a client acceptance")
    public_key = keyring.public_key_b64
    signature = keyring.sign(
        acceptance_message(receipt.body_hash, clean, public_key).encode("utf-8")
    )
    return receipt.model_copy(
        update={
            "acceptance": Acceptance(
                issuer=clean,
                public_key=public_key,
                signature=signature,
            )
        }
    )


def receipt_public_key(receipt: Receipt) -> Ed25519PublicKey:
    return public_key_from_b64(receipt.public_key)


def verify_receipt(
    receipt: Receipt,
    *,
    expected_public_key: str | None = None,
    expected_counter_public_key: str | None = None,
    now: datetime | None = None,
    require_unexpired: bool = False,
    require_acceptance: bool = False,
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
        "body hash matches the serialized change order"
        if recomputed == receipt.body_hash
        else f"body hash mismatch: computed {recomputed[:16]}..., stored {receipt.body_hash[:16]}...",
    )
    add(
        "receipt_id",
        receipt.body.receipt_id
        == _receipt_id(
            receipt.body.engagement_id,
            receipt.body.packet_hash,
            receipt.body.issued_at,
            receipt.body.chain_prev,
        ),
        "change order id is derived from the signed body",
    )

    try:
        key = receipt_public_key(receipt)
        key.verify(
            base64.urlsafe_b64decode(receipt.signature.encode("ascii")),
            receipt.body_hash.encode("ascii"),
        )
        signature_ok = True
        detail = "issuer Ed25519 signature verifies against the embedded public key"
    except (InvalidSignature, ValueError, TypeError) as exc:
        signature_ok = False
        detail = f"signature invalid: {exc}"
    add("signature", signature_ok, detail)

    if expected_public_key is not None:
        add(
            "pinned_key",
            expected_public_key == receipt.public_key,
            "change order key matches the pinned issuer key"
            if expected_public_key == receipt.public_key
            else "change order key does not match the pinned issuer key",
        )

    pairs = [(item.request_id, item.role) for item in receipt.body.approvals]
    add(
        "no_duplicate_signatures",
        len(pairs) == len(set(pairs)),
        "one signature per request and role",
    )
    covered = {item.request_id for item in receipt.body.approvals}
    missing = [
        item.request_id for item in receipt.body.line_items if item.request_id not in covered
    ]
    add(
        "line_items_signed",
        not missing,
        "every line item has at least one recorded signature"
        if not missing
        else f"line items without a signature: {', '.join(missing)}",
    )
    add(
        "signature_binding",
        all(
            len(item.phrase.split()) >= 4
            and item.phrase.split()[0].upper() == "SIGN"
            and item.phrase.split()[1].upper() == receipt.body.engagement_id.upper()
            for item in receipt.body.approvals
        )
        if receipt.body.approvals
        else False,
        "signature phrases name this engagement and start with SIGN",
    )
    priced = [item.request_id for item in receipt.body.line_items if not math.isfinite(item.amount)]
    add(
        "every_line_item_priced",
        not priced,
        "every line item has a finite amount"
        if not priced
        else f"unpriced line items: {', '.join(priced)}",
    )
    positive = round(sum(item.amount for item in receipt.body.line_items if item.amount > 0), 2)
    negative = round(sum(item.amount for item in receipt.body.line_items if item.amount < 0), 2)
    add(
        "amounts_balance",
        round(receipt.body.subtotal, 2) == positive
        and round(receipt.body.discount_total, 2) == negative
        and round(receipt.body.total, 2) == round(positive + negative, 2),
        f"subtotal {receipt.body.subtotal:,.2f} {receipt.body.currency}, "
        f"discount {receipt.body.discount_total:,.2f}, total {receipt.body.total:,.2f}",
    )
    if require_unexpired:
        add(
            "unexpired",
            moment <= receipt.body.expires_at,
            f"expires at {receipt.body.expires_at.isoformat()}",
        )
    else:
        add("unexpired", True, "expiry not enforced in this verification mode")

    if receipt.acceptance is None:
        if require_acceptance:
            add("acceptance_present", False, "client acceptance is required but missing")
    else:
        acceptance = receipt.acceptance
        add(
            "acceptance_algorithm",
            acceptance.algorithm == "Ed25519",
            f"acceptance algorithm={acceptance.algorithm}",
        )
        add(
            "acceptance_key_distinct",
            acceptance.public_key != receipt.public_key,
            "client acceptance key is distinct from the issuer key",
        )
        try:
            client_key = public_key_from_b64(acceptance.public_key)
            client_key.verify(
                base64.urlsafe_b64decode(acceptance.signature.encode("ascii")),
                acceptance_message(
                    receipt.body_hash, acceptance.issuer, acceptance.public_key
                ).encode("utf-8"),
            )
            accepted = True
            detail = f"client acceptance verifies for {acceptance.issuer}"
        except (InvalidSignature, ValueError, TypeError) as exc:
            accepted = False
            detail = f"client acceptance invalid: {exc}"
        add("acceptance", accepted, detail)
        if expected_counter_public_key is not None:
            add(
                "pinned_client_key",
                expected_counter_public_key == acceptance.public_key,
                "client key matches the pinned client key"
                if expected_counter_public_key == acceptance.public_key
                else "client key does not match the pinned client key",
            )

    ok = all(check.ok for check in checks)
    failures = [check.code for check in checks if not check.ok]
    summary = (
        "Change order is authentic, internally consistent, and accepted."
        if ok and receipt.acceptance is not None
        else "Change order is authentic and internally consistent."
        if ok
        else "Change order failed verification: " + ", ".join(failures)
    )
    return VerificationResult(
        ok=ok,
        receipt_id=receipt.body.receipt_id,
        checks=checks,
        summary=summary,
    )
