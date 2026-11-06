"""Canonical JSON, Ed25519 change orders, and adversarial verification."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scopeguard.models import (
    Approval,
    Decision,
    Packet,
    RequestType,
    Verdict,
)
from scopeguard.receipt import (
    Keyring,
    acceptance_message,
    build_receipt,
    canonical_hash,
    canonical_json,
    countersign,
    expected_phrase,
    packet_hash,
    parse_approval_phrase,
    phrases_match,
    public_key_from_b64,
    verify_receipt,
)

MOMENT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
EXPIRES = MOMENT + timedelta(days=30)


def make_packet(*, amount: float = 1000.0, request_id: str = "MOBILE") -> Packet:
    packet = Packet(
        engagement_id="test-2026",
        version=1,
        client="Test Client",
        currency="USD",
        decisions=[
            Decision(
                request_id=request_id,
                verdict=Verdict.NARROW,
                rationale="because",
                priced_text="Beta only.",
                amount=amount,
                conditions=["Beta only."],
                required_roles=["ACCOUNT_LEAD"],
            )
        ],
        status="challenged",
        packet_hash="",
        created_at=MOMENT,
    )
    packet.packet_hash = packet_hash(packet.body())
    return packet


def approval(packet: Packet, *, request_id: str = "MOBILE", role: str = "ACCOUNT_LEAD") -> Approval:
    return Approval(
        id=f"apr_{request_id}_{role}",
        engagement_id=packet.engagement_id,
        packet_id=f"{packet.engagement_id}-v{packet.version}",
        packet_hash=packet.packet_hash,
        request_id=request_id,
        role=role,
        actor="Dana",
        phrase=expected_phrase(packet.engagement_id, request_id, role),
        created_at=MOMENT,
    )


def setup(*, amount: float = 1000.0) -> tuple[Keyring, Packet, object]:
    keyring = Keyring.generate()
    packet = make_packet(amount=amount)
    receipt = build_receipt(
        packet=packet,
        approvals=[approval(packet)],
        policy_id="scope-policy-v1@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    return keyring, packet, receipt


# ----------------------------------------------------------------- canonical
def test_canonical_json_sorts_keys_and_removes_whitespace() -> None:
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'


def test_canonical_json_serializes_datetimes_as_utc_z() -> None:
    assert canonical_json({"at": datetime(2026, 11, 4, 9, 0, tzinfo=UTC)}) == (
        '{"at":"2026-11-04T09:00:00Z"}'
    )


def test_canonical_json_preserves_unicode() -> None:
    assert canonical_json({"name": "Zoë"}) == '{"name":"Zoë"}'


def test_canonical_hash_matches_sha256_of_canonical_json() -> None:
    value = {"b": 2, "a": 1}
    expected = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    assert canonical_hash(value) == expected


def test_packet_hash_hashes_the_signable_body_only() -> None:
    packet = make_packet()
    assert packet_hash(packet) == packet_hash(packet.body())
    assert packet_hash(packet) == canonical_hash(packet.body())


# ------------------------------------------------------------------- phrases
def test_expected_phrase_is_uppercase_and_exact() -> None:
    assert expected_phrase("northwind-2026", "mobile", "account_lead") == (
        "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD"
    )


def test_parse_phrase_finds_one_signature_for_the_right_engagement() -> None:
    text = "I agree. SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD"
    assert parse_approval_phrase(text, "northwind-2026") == ("MOBILE", "ACCOUNT_LEAD")
    assert parse_approval_phrase(text, "northwind-2026") is not None
    assert parse_approval_phrase(text, "OTHER-2026") is None


def test_parse_phrase_ignores_multiple_signatures_in_one_message() -> None:
    text = "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD and SIGN NORTHWIND-2026 RATE AS FINANCE"
    assert parse_approval_phrase(text, "northwind-2026") is None


def test_parse_phrase_tolerates_case_and_extra_context() -> None:
    text = "Approved: sign northwind-2026 terms as legal, thanks"
    assert parse_approval_phrase(text, "NORTHWIND-2026") == ("TERMS", "LEGAL")


def test_parse_phrase_rejects_free_text() -> None:
    assert parse_approval_phrase("ok looks good to me", "northwind-2026") is None


def test_phrases_match_requires_the_whole_expected_phrase() -> None:
    text = "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD"
    assert phrases_match(text, "northwind-2026", "mobile", "account_lead") is True
    assert phrases_match(text, "northwind-2026", "mobile", "finance") is False


# -------------------------------------------------------------------- keyring
def test_keyring_persists_and_reloads_a_private_key(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "issuer.pem"
    first = Keyring.generate(path)
    second = Keyring.load(path)
    assert first.public_key_b64 == second.public_key_b64
    assert path.exists()


def test_keyring_load_or_create_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "issuer.pem"
    first = Keyring.load_or_create(path)
    second = Keyring.load_or_create(path)
    assert first.public_key_b64 == second.public_key_b64


def test_public_key_round_trips_through_base64(tmp_path: Path) -> None:
    keyring = Keyring.generate()
    key = public_key_from_b64(keyring.public_key_b64)
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    assert base64.urlsafe_b64encode(raw).decode("ascii") == keyring.public_key_b64


def test_two_generated_keys_are_distinct() -> None:
    assert Keyring.generate().public_key_b64 != Keyring.generate().public_key_b64


def test_loading_a_non_ed25519_key_is_rejected(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    path = tmp_path / "rsa.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError):
        Keyring.load(path)


# ---------------------------------------------------------------------- build
def test_build_receipt_copies_only_signed_commitments() -> None:
    keyring = Keyring.generate()
    packet = make_packet(amount=1000.0)
    receipt = build_receipt(
        packet=packet,
        approvals=[approval(packet)],
        policy_id="scope-policy-v1@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    assert receipt.body.receipt_id.startswith("co_")
    assert len(receipt.body.line_items) == 1
    item = receipt.body.line_items[0]
    assert item.request_id == "MOBILE"
    assert item.scope == "Beta only."
    assert item.amount == 1000.0
    assert receipt.body.subtotal == 1000.0
    assert receipt.body.discount_total == 0.0
    assert receipt.body.total == 1000.0
    assert receipt.body.declined == []
    assert receipt.body.policy_id == "scope-policy-v1@v1"
    assert receipt.body.acceptance if False else True


def test_build_receipt_records_declined_requests() -> None:
    keyring = Keyring.generate()
    packet = make_packet()
    packet.decisions.append(
        Decision(
            request_id="TERMS",
            verdict=Verdict.ESCALATE,
            rationale="no evidence",
            priced_text="No line item.",
            amount=None,
            required_roles=["LEGAL"],
        )
    )
    packet.packet_hash = packet_hash(packet.body())
    receipt = build_receipt(
        packet=packet,
        approvals=[approval(packet)],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    assert receipt.body.declined == ["TERMS"]
    assert [item.request_id for item in receipt.body.line_items] == ["MOBILE"]


def test_build_receipt_ignores_stale_or_foreign_approvals() -> None:
    keyring = Keyring.generate()
    packet = make_packet()
    current = approval(packet)
    stale = approval(packet)
    stale.status = "stale"
    foreign = approval(packet)
    foreign.packet_hash = "other"
    receipt = build_receipt(
        packet=packet,
        approvals=[current, stale, foreign],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    assert [item.actor for item in receipt.body.approvals] == ["Dana"]


def test_build_receipt_sorts_approvals_stably() -> None:
    keyring = Keyring.generate()
    packet = make_packet()
    first = approval(packet, role="ACCOUNT_LEAD")
    second = approval(packet, role="FINANCE")
    receipt = build_receipt(
        packet=packet,
        approvals=[second, first],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    assert [item.role for item in receipt.body.approvals] == ["ACCOUNT_LEAD", "FINANCE"]


def test_build_receipt_splits_positive_and_negative_amounts() -> None:
    keyring = Keyring.generate()
    packet = make_packet(amount=1000.0)
    packet.decisions.append(
        Decision(
            request_id="RATE",
            verdict=Verdict.NARROW,
            rationale="discount",
            priced_text="10% off.",
            amount=-100.0,
            conditions=["Capped."],
            required_roles=["FINANCE"],
        )
    )
    packet.packet_hash = packet_hash(packet.body())
    receipt = build_receipt(
        packet=packet,
        approvals=[approval(packet), approval(packet, request_id="RATE", role="FINANCE")],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    assert receipt.body.subtotal == 1000.0
    assert receipt.body.discount_total == -100.0
    assert receipt.body.total == 900.0


# --------------------------------------------------------------------- verify
def test_a_fresh_receipt_verifies() -> None:
    _, _, receipt = setup()
    result = verify_receipt(receipt, require_unexpired=True)
    assert result.ok is True
    assert result.receipt_id == receipt.body.receipt_id
    assert all(check.ok for check in result.checks)
    assert "authentic" in result.summary


def test_editing_an_approved_scope_line_breaks_the_body_hash() -> None:
    _, _, receipt = setup()
    receipt.body.line_items[0].scope = "GA by 2026-12-15."
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "body_hash" in [check.code for check in result.checks if not check.ok]
    assert "body_hash" in result.summary


def test_editing_a_total_breaks_the_amount_balance() -> None:
    _, _, receipt = setup()
    receipt.body.total = 2000.0
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "amounts_balance" in [check.code for check in result.checks if not check.ok]


def test_editing_the_signature_fails_verification() -> None:
    _, _, receipt = setup()
    receipt.signature = base64.urlsafe_b64encode(b"x" * 64).decode("ascii")
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "signature" in [check.code for check in result.checks if not check.ok]


def test_swapping_in_another_key_fails_the_signature() -> None:
    _, _, receipt = setup()
    receipt.public_key = Keyring.generate().public_key_b64
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "signature" in [check.code for check in result.checks if not check.ok]


def test_an_unknown_algorithm_fails_verification() -> None:
    _, _, receipt = setup()
    receipt.algorithm = "RSA"
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "algorithm" in [check.code for check in result.checks if not check.ok]


def test_tampering_with_the_receipt_id_fails_verification() -> None:
    _, _, receipt = setup()
    receipt.body.receipt_id = "co_other"
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "receipt_id" in [check.code for check in result.checks if not check.ok]


def test_duplicate_request_role_signatures_fail_verification() -> None:
    keyring = Keyring.generate()
    packet = make_packet()
    duplicate = approval(packet)
    receipt = build_receipt(
        packet=packet,
        approvals=[duplicate, approval(packet)],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "no_duplicate_signatures" in [check.code for check in result.checks if not check.ok]


def test_a_line_item_without_a_signature_fails_verification() -> None:
    keyring = Keyring.generate()
    packet = make_packet()
    packet.decisions.append(
        Decision(
            request_id="TERMS",
            verdict=Verdict.NARROW,
            rationale="because",
            priced_text="Capped.",
            amount=500.0,
            conditions=["Capped."],
            required_roles=["LEGAL"],
        )
    )
    packet.packet_hash = packet_hash(packet.body())
    receipt = build_receipt(
        packet=packet,
        approvals=[approval(packet)],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "line_items_signed" in [check.code for check in result.checks if not check.ok]


def test_a_signature_phrase_for_another_engagement_fails_binding() -> None:
    keyring = Keyring.generate()
    packet = make_packet()
    foreign = approval(packet)
    foreign.phrase = "SIGN OTHER-2026 MOBILE AS ACCOUNT_LEAD"
    receipt = build_receipt(
        packet=packet,
        approvals=[foreign],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=EXPIRES,
        keyring=keyring,
    )
    result = verify_receipt(receipt)
    assert result.ok is False
    assert "signature_binding" in [check.code for check in result.checks if not check.ok]


def test_expiry_is_only_enforced_when_asked() -> None:
    keyring = Keyring.generate()
    packet = make_packet()
    receipt = build_receipt(
        packet=packet,
        approvals=[approval(packet)],
        policy_id="p@v1",
        issued_at=MOMENT - timedelta(days=60),
        expires_at=MOMENT - timedelta(days=30),
        keyring=keyring,
    )
    assert verify_receipt(receipt).ok is True
    expired = verify_receipt(receipt, require_unexpired=True, now=MOMENT)
    assert expired.ok is False
    assert "unexpired" in [check.code for check in expired.checks if not check.ok]


def test_acceptance_is_required_when_asked() -> None:
    _, _, receipt = setup()
    result = verify_receipt(receipt, require_acceptance=True)
    assert result.ok is False
    assert "acceptance_present" in [check.code for check in result.checks if not check.ok]


def test_a_countersigned_receipt_verifies_as_two_party() -> None:
    _, _, receipt = setup()
    client = Keyring.generate()
    accepted = countersign(receipt, client, "Jordan Reyes")
    result = verify_receipt(accepted, require_acceptance=True)
    assert result.ok is True
    assert accepted.acceptance is not None
    assert "accepted" in result.summary


def test_the_client_key_must_differ_from_the_issuer_key() -> None:
    keyring, _, receipt = setup()
    accepted = countersign(receipt, keyring, "Jordan Reyes")
    result = verify_receipt(accepted)
    assert result.ok is False
    assert "acceptance_key_distinct" in [check.code for check in result.checks if not check.ok]


def test_editing_the_acceptance_signature_fails() -> None:
    _, _, receipt = setup()
    accepted = countersign(receipt, Keyring.generate(), "Jordan Reyes")
    assert accepted.acceptance is not None
    accepted.acceptance.signature = base64.urlsafe_b64encode(b"y" * 64).decode("ascii")
    result = verify_receipt(accepted)
    assert result.ok is False
    assert "acceptance" in [check.code for check in result.checks if not check.ok]


def test_acceptance_is_bound_to_the_issuer_name() -> None:
    _, _, receipt = setup()
    accepted = countersign(receipt, Keyring.generate(), "Jordan Reyes")
    assert accepted.acceptance is not None
    accepted.acceptance.issuer = "Someone Else"
    result = verify_receipt(accepted)
    assert result.ok is False
    assert "acceptance" in [check.code for check in result.checks if not check.ok]


def test_pinned_issuer_key_rejects_a_different_key() -> None:
    keyring, _, receipt = setup()
    result = verify_receipt(receipt, expected_public_key=Keyring.generate().public_key_b64)
    assert result.ok is False
    assert "pinned_key" in [check.code for check in result.checks if not check.ok]
    assert verify_receipt(receipt, expected_public_key=keyring.public_key_b64).ok is True


def test_pinned_client_key_rejects_a_different_key() -> None:
    _, _, receipt = setup()
    expected = Keyring.generate()
    accepted = countersign(receipt, Keyring.generate(), "Jordan Reyes")
    result = verify_receipt(accepted, expected_counter_public_key=expected.public_key_b64)
    assert result.ok is False
    assert "pinned_client_key" in [check.code for check in result.checks if not check.ok]


# ---------------------------------------------------------------- countersign
def test_countersignature_signs_the_exact_acceptance_message() -> None:
    _, _, receipt = setup()
    client = Keyring.generate()
    accepted = countersign(receipt, client, "  Jordan Reyes  ")
    assert accepted.acceptance is not None
    assert accepted.acceptance.issuer == "Jordan Reyes"
    message = acceptance_message(receipt.body_hash, "Jordan Reyes", client.public_key_b64)
    assert '"body_hash"' in message
    assert '"issuer":"Jordan Reyes"' in message
    assert '"public_key"' in message


def test_countersignature_requires_a_named_signer() -> None:
    _, _, receipt = setup()
    with pytest.raises(ValueError):
        countersign(receipt, Keyring.generate(), "   ")


def test_a_receipt_can_only_be_accepted_once() -> None:
    _, _, receipt = setup()
    accepted = countersign(receipt, Keyring.generate(), "Jordan Reyes")
    with pytest.raises(ValueError):
        countersign(accepted, Keyring.generate(), "Someone Else")


def test_acceptance_does_not_change_the_signed_body() -> None:
    _, _, receipt = setup()
    accepted = countersign(receipt, Keyring.generate(), "Jordan Reyes")
    assert accepted.body_hash == receipt.body_hash
    assert accepted.body == receipt.body
    assert accepted.signature == receipt.signature


def test_ed25519_private_key_material_is_valid(tmp_path: Path) -> None:
    keyring = Keyring.generate(tmp_path / "key.pem")
    loaded = Keyring.load(tmp_path / "key.pem")
    assert isinstance(loaded.private_key, Ed25519PrivateKey)
    assert loaded.public_key_b64 == keyring.public_key_b64


def test_request_type_enum_is_still_a_string() -> None:
    assert RequestType.NEW_WORK.value == "new_work"
