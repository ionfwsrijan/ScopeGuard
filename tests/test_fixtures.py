"""Fixture loading: referential integrity, digests, and clear failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from scopeguard.fixtures import (
    FixtureError,
    load_approvers,
    load_engagement,
    load_evidence,
    load_messages,
    load_requests_document,
)

ENGAGEMENT = """
engagement:
  id: acme-2026
  client: Acme Corp
  requested_by: Dana
  contract_value: 120000
  delivery_cost_ratio_pct: 58
  requests:
    - id: MOBILE
      type: new_work
      title: Native mobile app
      requested_text: "A native mobile app by launch."
      requested_value: {ga_date: 2026-12-15}
      due: 2026-12-15
    - id: RATE
      type: rate
      title: Discount
      requested_text: "20% off."
      requested_value: {discount_pct: 20}
"""

BARE_ENGAGEMENT = """
id: acme-2026
client: Acme Corp
requested_by: Dana
contract_value: 120000
delivery_cost_ratio_pct: 58
requests:
  - id: MOBILE
    type: new_work
    title: Native mobile app
    requested_text: "A native mobile app by launch."
"""

EVIDENCE = """
engagement_id: acme-2026
evidence:
  - id: EV-MOBILE-1
    request_id: MOBILE
    kind: estimate
    source: "Delivery estimate"
    observed_at: 2026-11-01
    valid_until: 2026-12-15
    summary: "Beta in December, GA in Q1."
    payload: {hours: 320, rate: 150}
"""

APPROVERS = """
engagement_id: acme-2026
roles:
  ACCOUNT_LEAD: [Dana]
  DELIVERY_LEAD: [Marco]
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- repository
def test_repository_engagement_fixture(repo_root: Path) -> None:
    engagement = load_engagement(repo_root / "fixtures" / "northwind" / "engagement.yaml")
    assert engagement.id == "northwind-2026"
    assert engagement.client == "Northwind Health"
    assert [request.id for request in engagement.requests] == [
        "MOBILE",
        "RUSH",
        "RATE",
        "TERMS",
    ]
    assert engagement.request("rate").requested_value["discount_pct"] == 20
    assert engagement.request("terms").requested_value["unlimited_liability"] is True


def test_repository_evidence_fixture(repo_root: Path) -> None:
    engagement = load_engagement(repo_root / "fixtures" / "northwind" / "engagement.yaml")
    book = load_evidence(repo_root / "fixtures" / "northwind" / "evidence.yaml", engagement)
    assert len(book.items) == 4
    assert all(len(item.digest) == 64 for item in book.items)
    assert {item.request_id for item in book.items} == {"MOBILE", "RUSH", "RATE", "TERMS"}


def test_repository_approver_fixture(repo_root: Path) -> None:
    approvers = load_approvers(repo_root / "fixtures" / "northwind" / "approvers.yaml")
    assert approvers.engagement_id == "northwind-2026"
    assert approvers.can_hold("Dana", "ACCOUNT_LEAD")
    assert not approvers.can_hold("Dana", "LEGAL")
    assert approvers.role_for("iris") == "LEGAL"
    assert approvers.all_roles() == ["ACCOUNT_LEAD", "DELIVERY_LEAD", "FINANCE", "LEGAL"]


def test_repository_message_fixture(repo_root: Path) -> None:
    messages = load_messages(repo_root / "fixtures" / "northwind" / "switch_messages.yaml")
    assert len(messages) == 15
    assert messages[0].sender_name == "Dana"
    assert str(messages[0].room_id) == "room-northwind-scope"
    assert sum("SIGN" in item.body for item in messages) == 10
    assert messages[-1].body == "SCOPE SEAL"


# ------------------------------------------------------------------- loading
def test_load_engagement_accepts_a_wrapped_or_bare_mapping(tmp_path: Path) -> None:
    wrapped = load_engagement(write(tmp_path, "wrapped.yaml", ENGAGEMENT))
    bare = load_engagement(write(tmp_path, "bare.yaml", BARE_ENGAGEMENT))
    assert wrapped.requests[0].id == bare.requests[0].id == "MOBILE"
    assert wrapped.client == bare.client == "Acme Corp"


def test_load_engagement_rejects_invalid_payload(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_engagement(write(tmp_path, "engagement.yaml", "engagement: {id: X}\n"))
    assert "invalid engagement fixture" in info.value.message


def test_missing_fixture_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_engagement(tmp_path / "missing.yaml")
    assert "not found" in info.value.message


def test_invalid_yaml_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", "evidence: [unclosed"))
    assert "invalid YAML" in info.value.message


def test_non_mapping_yaml_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_engagement(write(tmp_path, "engagement.yaml", "- one\n- two\n"))
    assert "mapping" in info.value.message


# ------------------------------------------------------------------ evidence
def test_evidence_requires_a_list(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_evidence(
            write(tmp_path, "evidence.yaml", "engagement_id: acme-2026\nevidence: nope\n")
        )
    assert "must be a list" in info.value.message


def test_evidence_engagement_id_must_match(tmp_path: Path) -> None:
    engagement = load_engagement(write(tmp_path, "engagement.yaml", ENGAGEMENT))
    text = EVIDENCE.replace("engagement_id: acme-2026", "engagement_id: other")
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", text), engagement)
    assert "does not match" in info.value.message


def test_evidence_request_must_exist(tmp_path: Path) -> None:
    engagement = load_engagement(write(tmp_path, "engagement.yaml", ENGAGEMENT))
    text = EVIDENCE.replace("request_id: MOBILE", "request_id: NOPE")
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", text), engagement)
    assert "unknown requests" in info.value.message


def test_duplicate_evidence_ids_are_rejected(tmp_path: Path) -> None:
    text = (
        EVIDENCE
        + """  - id: EV-MOBILE-1
    request_id: MOBILE
    kind: note
    source: "duplicate"
    observed_at: 2026-11-01
    summary: "duplicate"
"""
    )
    with pytest.raises(FixtureError) as info:
        load_evidence(write(tmp_path, "evidence.yaml", text))
    assert "duplicate evidence id" in info.value.message


def test_evidence_digest_is_computed_on_load(tmp_path: Path) -> None:
    book = load_evidence(write(tmp_path, "evidence.yaml", EVIDENCE))
    assert book.items[0].digest == book.items[0].computed_digest()


# ------------------------------------------------------------------ approvers
def test_approver_engagement_id_must_match(tmp_path: Path) -> None:
    engagement = load_engagement(write(tmp_path, "engagement.yaml", ENGAGEMENT))
    text = APPROVERS.replace("engagement_id: acme-2026", "engagement_id: other")
    with pytest.raises(FixtureError) as info:
        load_approvers(write(tmp_path, "approvers.yaml", text), engagement)
    assert "does not match" in info.value.message


def test_approver_role_needs_a_named_person(tmp_path: Path) -> None:
    text = APPROVERS.replace("ACCOUNT_LEAD: [Dana]", "ACCOUNT_LEAD: []")
    with pytest.raises(FixtureError) as info:
        load_approvers(write(tmp_path, "approvers.yaml", text))
    assert "at least one named person" in info.value.message


# ------------------------------------------------------------------- messages
def test_message_fixture_defaults_room(tmp_path: Path) -> None:
    text = """
messages:
  - id: m1
    sender_name: Dana
    body: "SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD"
    timestamp_ms: 0
"""
    messages = load_messages(write(tmp_path, "messages.yaml", text))
    assert messages[0].room_id == "room-demo"


def test_message_fixture_rejects_unknown_fields(tmp_path: Path) -> None:
    text = """
messages:
  - id: m1
    sender_name: Dana
    body: "hello"
    surprise: true
"""
    with pytest.raises(FixtureError):
        load_messages(write(tmp_path, "messages.yaml", text))


# ------------------------------------------------------------------- requests
def test_requests_document(tmp_path: Path) -> None:
    text = """
requests:
  - id: MOBILE
    type: new_work
    title: Native mobile app
    requested_text: "Native mobile app by launch."
"""
    requests = load_requests_document(write(tmp_path, "requests.yaml", text))
    assert requests[0].id == "MOBILE"


def test_requests_document_requires_a_list(tmp_path: Path) -> None:
    with pytest.raises(FixtureError) as info:
        load_requests_document(write(tmp_path, "requests.yaml", "requests: nope\n"))
    assert "must be a list" in info.value.message
