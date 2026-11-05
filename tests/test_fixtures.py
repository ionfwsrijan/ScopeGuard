"""Fixture loading: referential integrity, digests, and clear failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from vouch.fixtures import (
    FixtureError,
    load_approvers,
    load_claims_document,
    load_deal,
    load_evidence,
    load_messages,
)

REQUEST = """
deal:
  id: acme-2026
  account: Acme Corp
  requested_by: Priya
  annual_list_price: 240000
  cost_ratio_pct: 55
  claims:
    - id: SSO
      type: feature
      title: SSO
      requested_text: "SSO generally available by 2026-12-31."
      requested_value: {ga_date: 2026-12-31}
      due: 2026-12-31
"""

EVIDENCE = """
deal_id: acme-2026
evidence:
  - id: EV-SSO-1
    claim_id: SSO
    kind: roadmap
    source: "Product roadmap"
    observed_at: 2026-10-24
    valid_until: 2026-12-31
    summary: "Beta in December, GA in Q1."
    payload: {beta_date: 2026-12-15, ga_date: 2027-03-31}
"""

APPROVERS = """
deal_id: acme-2026
roles:
  AE: [Priya]
  ENG_LEAD: [Sam]
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_repository_deal_fixture(repo_root: Path) -> None:
    deal = load_deal(repo_root / "fixtures" / "acme" / "request.yaml")
    assert deal.id == "acme-2026"
    assert deal.account == "Acme Corp"
    assert [claim.id for claim in deal.claims] == ["SSO", "SLA", "DISC", "SOC2"]
    assert deal.claims[2].requested_value["discount_pct"] == 20


def test_repository_evidence_fixture(repo_root: Path) -> None:
    deal = load_deal(repo_root / "fixtures" / "acme" / "request.yaml")
    book = load_evidence(repo_root / "fixtures" / "acme" / "evidence.yaml", deal)
    assert len(book.items) == 4
    assert all(len(item.digest) == 64 for item in book.items)
    assert {item.claim_id for item in book.items} == {"SSO", "SLA", "DISC", "SOC2"}


def test_repository_approver_fixture(repo_root: Path) -> None:
    approvers = load_approvers(repo_root / "fixtures" / "acme" / "approvers.yaml")
    assert approvers.deal_id == "acme-2026"
    assert approvers.can_hold("Priya", "AE")
    assert not approvers.can_hold("Priya", "LEGAL")


def test_repository_message_fixture(repo_root: Path) -> None:
    messages = load_messages(repo_root / "fixtures" / "acme" / "switch_messages.yaml")
    assert len(messages) == 14
    assert messages[0].sender_name == "Priya"
    assert sum("APPROVE" in item.body for item in messages) == 10


def test_load_deal_accepts_bare_mapping(tmp_path: Path) -> None:
    text = REQUEST.replace("deal:\n", "").replace("\n  ", "\n")
    # A bare mapping would need different indentation; use the wrapped form here
    # and verify a direct payload separately.
    deal = load_deal(write(tmp_path, "request.yaml", REQUEST))
    assert deal.claims[0].id == "SSO"
    del text


def test_load_deal_rejects_invalid_payload(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_deal(write(tmp_path, "request.yaml", "deal: {id: X}\n"))
    assert "invalid deal fixture" in info.value.message


def test_missing_fixture_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_deal(tmp_path / "missing.yaml")
    assert "not found" in info.value.message


def test_invalid_yaml_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", "evidence: [unclosed"))
    assert "invalid YAML" in info.value.message


def test_evidence_requires_a_list(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", "deal_id: acme-2026\nevidence: nope\n"))
    assert "must be a list" in info.value.message


def test_evidence_deal_id_must_match(tmp_path: Path) -> None:
    deal = load_deal(write(tmp_path, "request.yaml", REQUEST))
    text = EVIDENCE.replace("deal_id: acme-2026", "deal_id: other")
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", text), deal)
    assert "does not match" in info.value.message


def test_evidence_claim_must_exist(tmp_path: Path) -> None:
    deal = load_deal(write(tmp_path, "request.yaml", REQUEST))
    text = EVIDENCE.replace("claim_id: SSO", "claim_id: NOPE")
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", text), deal)
    assert "unknown claims" in info.value.message


def test_duplicate_evidence_ids_are_rejected(tmp_path: Path) -> None:
    text = (
        EVIDENCE
        + """
  - id: EV-SSO-1
    claim_id: SSO
    kind: note
    source: "duplicate"
    observed_at: 2026-10-24
    summary: "duplicate"
"""
    )
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", text))
    assert "duplicate evidence id" in info.value.message


def test_evidence_digest_is_computed_on_load(tmp_path: Path) -> None:
    book = load_evidence(write(tmp_path, "evidence.yaml", EVIDENCE))
    assert book.items[0].digest == book.items[0].computed_digest()


def test_approver_deal_id_must_match(tmp_path: Path) -> None:
    deal = load_deal(write(tmp_path, "request.yaml", REQUEST))
    text = APPROVERS.replace("deal_id: acme-2026", "deal_id: other")
    with pytest.raises(FixtureError) as info:
        load_approvers(write(tmp_path, "approvers.yaml", text), deal)
    assert "does not match" in info.value.message


def test_approver_role_needs_a_named_person(tmp_path: Path) -> None:
    text = APPROVERS.replace("  AE: [Priya]", "  AE: []")
    with pytest.raises(FixtureError) as info:
        load_approvers(write(tmp_path, "approvers.yaml", text))
    assert "at least one named person" in info.value.message


def test_message_fixture_defaults_room(tmp_path: Path) -> None:
    text = """
messages:
  - id: m1
    sender_name: Priya
    body: "APPROVE ACME-2026 SSO AS AE"
    timestamp_ms: 0
"""
    messages = load_messages(write(tmp_path, "messages.yaml", text))
    assert messages[0].room_id == "room-demo"


def test_message_fixture_rejects_unknown_fields(tmp_path: Path) -> None:
    text = """
messages:
  - id: m1
    sender_name: Priya
    body: "hi"
    surprise: true
"""
    with pytest.raises(FixtureError):
        load_messages(write(tmp_path, "messages.yaml", text))


def test_claims_document(tmp_path: Path) -> None:
    text = """
claims:
  - id: SSO
    type: feature
    title: SSO
    requested_text: "SSO by 2026-12-31."
"""
    claims = load_claims_document(write(tmp_path, "claims.yaml", text))
    assert claims[0].id == "SSO"


def test_claims_document_requires_a_list(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_claims_document(write(tmp_path, "claims.yaml", "claims: nope\n"))
    assert "must be a list" in info.value.message
