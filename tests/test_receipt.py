"""Canonicalization, approval phrases, signing, and adversarial verification."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from conftest import DEMO_MOMENT, approve_all, prepare_v2
from vouch.models import (
    Approval,
    ApprovalStatus,
    Packet,
    Receipt,
    Verdict,
)
from vouch.receipt import (
    Keyring,
    build_receipt,
    canonical_hash,
    canonical_json,
    expected_phrase,
    packet_hash,
    parse_approval_phrase,
    phrases_match,
    public_key_from_b64,
    verify_receipt,
)
from vouch.service import VouchService


# -------------------------------------------------------------------- canonical
def test_canonical_json_sorts_keys_and_strips_whitespace() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert canonical_json({"a": [1, {"z": 0, "y": 1}]}) == '{"a":[1,{"y":1,"z":0}]}'


def test_canonical_json_formats_datetimes_as_utc_z() -> None:
    moment = datetime(2026, 11, 4, 10, 0, 0, 123456, tzinfo=timezone(timedelta(hours=1)))
    assert canonical_json({"at": moment}) == '{"at":"2026-11-04T09:00:00Z"}'


def test_canonical_json_keeps_unicode() -> None:
    assert "café" in canonical_json({"name": "café"})


def test_canonical_hash_matches_sha256() -> None:
    expected = hashlib.sha256(b'{"a":1}').hexdigest()
    assert canonical_hash({"a": 1}) == expected


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_packet_hash_is_stable_for_the_same_body(service: VouchService) -> None:
    _, _, packet = prepare_v2(service)
    assert packet_hash(packet) == packet.packet_hash
    assert packet_hash(packet.body()) == packet.packet_hash


# ---------------------------------------------------------------------- phrases
def test_expected_phrase() -> None:
    assert expected_phrase("acme-2026", "sso", "eng_lead") == "APPROVE ACME-2026 SSO AS ENG_LEAD"


def test_parse_approval_phrase_accepts_case_and_surrounding_text() -> None:
    assert parse_approval_phrase(
        "I have reviewed this. approve acme-2026 disc as vp_sales. Thanks.",
        "acme-2026",
    ) == ("DISC", "VP_SALES")


def test_parse_approval_phrase_requires_exactly_one_match() -> None:
    assert (
        parse_approval_phrase(
            "APPROVE ACME-2026 SSO AS AE and APPROVE ACME-2026 SLA AS AE", "acme-2026"
        )
        is None
    )


def test_parse_approval_phrase_ignores_another_deal() -> None:
    assert parse_approval_phrase("APPROVE OTHER-2026 SSO AS AE", "acme-2026") is None


def test_parse_approval_phrase_ignores_malformed_text() -> None:
    assert parse_approval_phrase("APPROVE ACME-2026 SSO", "acme-2026") is None
    assert parse_approval_phrase("", "acme-2026") is None


def test_phrases_match_requires_the_complete_phrase() -> None:
    assert phrases_match("APPROVE ACME-2026 DISC AS AE", "acme-2026", "DISC", "AE")
    assert phrases_match(
        "I read it. APPROVE ACME-2026 DISC AS AE. Thanks.", "acme-2026", "DISC", "AE"
    )
    assert not phrases_match("APPROVE ACME-2026 DISC AS", "acme-2026", "DISC", "AE")
    assert not phrases_match("APPROVE ACME-2026 DISC AS VP_SALES", "acme-2026", "DISC", "AE")


# ----------------------------------------------------------------------- keyring
def test_keyring_generate_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "ed25519.pem"
    original = Keyring.generate(path)
    assert path.exists()
    loaded = Keyring.load(path)
    assert loaded.public_key_b64 == original.public_key_b64
    signature = base64.urlsafe_b64decode(loaded.sign(b"hello").encode("ascii"))
    public_key_from_b64(loaded.public_key_b64).verify(signature, b"hello")


def test_keyring_load_or_create_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "ed25519.pem"
    first = Keyring.load_or_create(path)
    second = Keyring.load_or_create(path)
    assert first.public_key_b64 == second.public_key_b64


def test_keyring_rejects_a_non_ed25519_key(tmp_path: Path) -> None:
    ec_key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "ec.pem"
    path.write_bytes(
        ec_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError):
        Keyring.load(path)


def test_public_key_from_b64_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        public_key_from_b64("not-a-key")


# ------------------------------------------------------------------ build receipt
def _sealed_parts(service: VouchService) -> tuple[Packet, list[Approval], Keyring]:
    _, _, packet = prepare_v2(service)
    approve_all(service)
    approvals = service.store.approvals_for_packet(packet)
    return packet, approvals, service.keyring


def test_build_receipt_contains_only_commitments(service: VouchService) -> None:
    packet, approvals, keyring = _sealed_parts(service)
    receipt = build_receipt(
        packet=packet,
        approvals=approvals,
        policy_id="deal-policy-v1@v1",
        issued_at=DEMO_MOMENT,
        expires_at=DEMO_MOMENT + timedelta(days=30),
        keyring=keyring,
    )
    assert [item.claim_id for item in receipt.body.commitments] == ["SSO", "SLA", "DISC", "SOC2"]
    assert receipt.body.declined == []
    assert len(receipt.body.approvals) == 8
    assert receipt.body.receipt_id.startswith("vch_")
    assert receipt.body.packet_hash == packet.packet_hash
    assert receipt.body.packet_version == packet.version
    assert receipt.body_hash == canonical_hash(receipt.body)


def test_build_receipt_drops_stale_and_other_packet_approvals(service: VouchService) -> None:
    packet, approvals, keyring = _sealed_parts(service)
    stale = approvals[0].model_copy(
        update={"status": ApprovalStatus.STALE.value, "id": "apr_stale"}
    )
    other = approvals[1].model_copy(update={"packet_hash": "f" * 64, "id": "apr_other"})
    receipt = build_receipt(
        packet=packet,
        approvals=[stale, other],
        policy_id="p@1",
        issued_at=DEMO_MOMENT,
        expires_at=DEMO_MOMENT + timedelta(days=30),
        keyring=keyring,
    )
    assert receipt.body.approvals == []
    assert len(receipt.body.commitments) == 4


def test_build_receipt_lists_declined_claims(service: VouchService) -> None:
    packet, approvals, keyring = _sealed_parts(service)
    packet.decisions[0].verdict = Verdict.REFUSE
    receipt = build_receipt(
        packet=packet,
        approvals=approvals,
        policy_id="p@1",
        issued_at=DEMO_MOMENT,
        expires_at=DEMO_MOMENT + timedelta(days=30),
        keyring=keyring,
    )
    assert receipt.body.declined == ["SSO"]
    assert "SSO" not in [item.claim_id for item in receipt.body.commitments]


def test_chain_previous_changes_the_receipt_id(service: VouchService) -> None:
    packet, approvals, keyring = _sealed_parts(service)
    kwargs = dict(
        packet=packet,
        approvals=approvals,
        policy_id="p@1",
        issued_at=DEMO_MOMENT,
        expires_at=DEMO_MOMENT + timedelta(days=30),
        keyring=keyring,
    )
    first = build_receipt(**kwargs)
    second = build_receipt(**kwargs, chain_prev="a" * 64)
    assert first.body.receipt_id != second.body.receipt_id
    assert second.body.chain_prev == "a" * 64


def test_receipt_builder_does_not_trust_approval_phrases(service: VouchService) -> None:
    packet, approvals, keyring = _sealed_parts(service)
    forged = approvals[0].model_copy(update={"phrase": "I approve", "id": "apr_forged"})
    receipt = build_receipt(
        packet=packet,
        approvals=[forged],
        policy_id="p@1",
        issued_at=DEMO_MOMENT,
        expires_at=DEMO_MOMENT + timedelta(days=30),
        keyring=keyring,
    )
    assert receipt.body.approvals[0].phrase == "I approve"
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "approval_binding" and not check.ok for check in result.checks)


# ------------------------------------------------------------------- verification
def _resign(receipt: Receipt, keyring: Keyring) -> Receipt:
    receipt.body_hash = canonical_hash(receipt.body)
    receipt.signature = keyring.sign(receipt.body_hash.encode("ascii"))
    receipt.public_key = keyring.public_key_b64
    return receipt


def test_valid_receipt_passes_every_check(service: VouchService) -> None:
    receipt = service.seal("acme-2026") if False else None  # keep the fixture explicit
    packet, approvals, keyring = _sealed_parts(service)
    receipt = build_receipt(
        packet=packet,
        approvals=approvals,
        policy_id="p@1",
        issued_at=DEMO_MOMENT,
        expires_at=DEMO_MOMENT + timedelta(days=30),
        keyring=keyring,
    )
    result = verify_receipt(receipt, expected_public_key=keyring.public_key_b64, now=DEMO_MOMENT)
    assert result.ok is True
    assert result.summary == "Receipt is authentic and internally consistent."
    assert all(check.ok for check in result.checks)


def test_tampered_commitment_fails_the_body_hash(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.body.commitments[0].commitment = "SSO generally available by 2026-11-30."
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "body_hash" and not check.ok for check in result.checks)


def test_tampered_body_hash_fails(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.body_hash = "f" * 64
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "body_hash" and not check.ok for check in result.checks)


def test_tampered_signature_fails(service: VouchService) -> None:
    receipt = _receipt(service)
    raw = bytearray(base64.urlsafe_b64decode(receipt.signature.encode("ascii")))
    raw[0] ^= 0xFF
    receipt.signature = base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "signature" and not check.ok for check in result.checks)


def test_invalid_signature_base64_fails_cleanly(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.signature = "not base64 !!!"
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "signature" and not check.ok for check in result.checks)


def test_wrong_algorithm_fails(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.algorithm = "RSA"
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "algorithm" and not check.ok for check in result.checks)


def test_pinned_key_mismatch_fails(service: VouchService, tmp_path: Path) -> None:
    receipt = _receipt(service)
    other = Keyring.generate(tmp_path / "other.pem")
    result = verify_receipt(receipt, expected_public_key=other.public_key_b64)
    assert result.ok is False
    assert any(check.code == "pinned_key" and not check.ok for check in result.checks)


def test_expired_receipt_fails_when_required(service: VouchService) -> None:
    receipt = _receipt(service)
    result = verify_receipt(
        receipt,
        now=receipt.body.expires_at + timedelta(seconds=1),
        require_unexpired=True,
    )
    assert result.ok is False
    assert any(check.code == "unexpired" and not check.ok for check in result.checks)


def test_expiry_is_not_enforced_by_default(service: VouchService) -> None:
    receipt = _receipt(service)
    result = verify_receipt(receipt, now=receipt.body.expires_at + timedelta(days=400))
    assert result.ok is True


def test_duplicate_approvals_fail(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.body.approvals.append(receipt.body.approvals[0].model_copy(deep=True))
    _resign(receipt, service.keyring)
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "no_duplicate_approvals" and not check.ok for check in result.checks)


def test_commitment_without_approval_fails(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.body.approvals = [item for item in receipt.body.approvals if item.claim_id != "SSO"]
    _resign(receipt, service.keyring)
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "commitments_approved" and not check.ok for check in result.checks)


def test_empty_approvals_fail_binding(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.body.approvals = []
    _resign(receipt, service.keyring)
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "approval_binding" and not check.ok for check in result.checks)


def test_approval_for_another_deal_fails_binding(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.body.approvals[0].phrase = "APPROVE OTHER-2026 SSO AS AE"
    _resign(receipt, service.keyring)
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "approval_binding" and not check.ok for check in result.checks)


def test_tampered_receipt_id_fails(service: VouchService) -> None:
    receipt = _receipt(service)
    receipt.body.receipt_id = "vch_forged"
    _resign(receipt, service.keyring)
    result = verify_receipt(receipt)
    assert result.ok is False
    assert any(check.code == "receipt_id" and not check.ok for check in result.checks)


def test_receipt_is_rejected_when_extra_fields_are_injected(service: VouchService) -> None:
    receipt = _receipt(service)
    payload = receipt.model_dump(mode="json")
    payload["body"]["commitments"][0]["surprise"] = True
    with pytest.raises(ValidationError):
        Receipt.model_validate(payload)


def _receipt(service: VouchService) -> Receipt:
    packet, approvals, keyring = _sealed_parts(service)
    return build_receipt(
        packet=packet,
        approvals=approvals,
        policy_id="p@1",
        issued_at=DEMO_MOMENT,
        expires_at=DEMO_MOMENT + timedelta(days=30),
        keyring=keyring,
    )
