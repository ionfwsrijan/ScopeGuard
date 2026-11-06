"""The SQLite ledger: persistence, invalidation rules, and the audit hash chain."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scopeguard.errors import NotFoundError, StateError
from scopeguard.models import (
    Approval,
    Decision,
    Engagement,
    Evidence,
    Packet,
    RequestType,
    Verdict,
)
from scopeguard.receipt import (
    Keyring,
    build_receipt,
    countersign,
    expected_phrase,
    packet_hash,
)
from scopeguard.store import Store

MOMENT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def engagement(*, engagement_id: str = "test-2026") -> Engagement:
    return Engagement(
        id=engagement_id,
        client="Test Client",
        requested_by="Dana",
        contract_value=120000,
        delivery_cost_ratio_pct=58,
        requests=[
            {
                "id": "MOBILE",
                "type": RequestType.NEW_WORK.value,
                "title": "Mobile",
                "requested_text": "Build it.",
                "requested_value": {"ga_date": "2026-12-15"},
            }
        ],
    )


def evidence() -> Evidence:
    return Evidence(
        id="EV-MOBILE-1",
        request_id="MOBILE",
        kind="estimate",
        source="estimate.pdf",
        observed_at=MOMENT.date(),
        summary="320 hours.",
        payload={"hours": 320, "rate": 150},
    )


def packet(*, version: int = 1, hash_value: str | None = None) -> Packet:
    instance = Packet(
        engagement_id="test-2026",
        version=version,
        client="Test Client",
        currency="USD",
        decisions=[
            Decision(
                request_id="MOBILE",
                verdict=Verdict.NARROW,
                rationale="because",
                priced_text="Beta only.",
                amount=30000.0,
                conditions=["Beta only."],
                required_roles=["ACCOUNT_LEAD"],
            )
        ],
        status="challenged",
        packet_hash="",
        created_at=MOMENT,
    )
    if hash_value:
        instance.packet_hash = hash_value
    else:
        instance.packet_hash = packet_hash(instance.body())
    return instance


def approval(pkt: Packet, *, role: str = "ACCOUNT_LEAD") -> Approval:
    return Approval(
        id=f"apr_{pkt.version}_{role}",
        engagement_id=pkt.engagement_id,
        packet_id=f"{pkt.engagement_id}-v{pkt.version}",
        packet_hash=pkt.packet_hash,
        request_id="MOBILE",
        role=role,
        actor="Dana",
        phrase=expected_phrase(pkt.engagement_id, "MOBILE", role),
        created_at=MOMENT,
    )


def receipt(pkt: Packet, keyring: Keyring, *, chain_prev: str | None = None):
    return build_receipt(
        packet=pkt,
        approvals=[approval(pkt)],
        policy_id="p@v1",
        issued_at=MOMENT,
        expires_at=MOMENT + timedelta(days=30),
        keyring=keyring,
        chain_prev=chain_prev,
    )


# ---------------------------------------------------------------- engagements
def test_engagement_round_trip_and_listing(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        loaded = store.load_engagement("test-2026")
        assert loaded.client == "Test Client"
        assert store.engagement_exists("test-2026") is True
        assert store.engagement_exists("nope") is False
        assert [item.id for item in store.list_engagements()] == ["test-2026"]


def test_upserting_an_engagement_updates_the_client(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        updated = engagement().model_copy(update={"client": "New Client"})
        store.save_engagement(updated)
        assert store.load_engagement("test-2026").client == "New Client"


def test_missing_engagement_has_an_actionable_hint(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store, pytest.raises(NotFoundError) as info:
        store.load_engagement("missing")
    assert "ingest" in (info.value.hint or "")


# ------------------------------------------------------------------- evidence
def test_evidence_is_replaced_atomically(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        store.replace_evidence("test-2026", [evidence()])
        assert [item.id for item in store.list_evidence("test-2026")] == ["EV-MOBILE-1"]
        store.replace_evidence("test-2026", [])
        assert store.list_evidence("test-2026") == []


# -------------------------------------------------------------------- packets
def test_packets_are_versioned_and_listed_newest_first(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        store.save_packet(packet(version=1))
        store.save_packet(packet(version=2))
        assert store.latest_packet("test-2026").version == 2
        assert [item.version for item in store.list_packets("test-2026")] == [2, 1]
        assert store.count_packets("test-2026") == 2
        assert store.load_packet("test-2026-v1").version == 1


def test_a_packet_version_cannot_be_overwritten(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        store.save_packet(packet(version=1))
        with pytest.raises(StateError):
            store.save_packet(packet(version=1))


def test_missing_packet_has_a_pricing_hint(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store, pytest.raises(NotFoundError) as info:
        store.latest_packet("test-2026")
    assert "assess" in (info.value.hint or "")


def test_updating_a_packet_persists_status_and_hash(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        pkt = packet(version=1)
        store.save_packet(pkt)
        pkt.status = "sealed"
        pkt.packet_hash = "new-hash"
        store.update_packet(pkt)
        loaded = store.latest_packet("test-2026")
        assert loaded.status == "sealed"
        assert loaded.packet_hash == "new-hash"


# ------------------------------------------------------------------ approvals
def test_approvals_are_idempotent_and_filterable(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        pkt = packet()
        store.save_packet(pkt)
        item = approval(pkt)
        store.add_approval(item)
        store.add_approval(item)
        assert len(store.list_approvals("test-2026")) == 1
        assert len(store.list_approvals("test-2026", status="valid")) == 1
        assert store.list_approvals("test-2026", status="stale") == []
        assert [entry.id for entry in store.approvals_for_packet(pkt)] == [item.id]


def test_invalidate_approvals_keeps_the_current_hash(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        first = packet(version=1)
        second = packet(version=2)
        store.save_packet(first)
        store.save_packet(second)
        store.add_approval(approval(first))
        store.add_approval(approval(second))
        assert (
            store.invalidate_approvals("test-2026", keep_hash=second.packet_hash, reason="revised")
            == 1
        )
        statuses = {item.packet_hash: item.status for item in store.list_approvals("test-2026")}
        assert statuses[first.packet_hash] == "stale"
        assert statuses[second.packet_hash] == "valid"


def test_invalidate_request_role_only_touches_other_hashes(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        pkt = packet()
        store.save_packet(pkt)
        store.add_approval(approval(pkt))
        store.invalidate_request_role(
            "test-2026", "MOBILE", "ACCOUNT_LEAD", packet_hash="different", reason="new"
        )
        assert store.list_approvals("test-2026")[0].status == "stale"


def test_invalidate_request_role_is_case_insensitive(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        pkt = packet()
        store.save_packet(pkt)
        store.add_approval(approval(pkt))
        store.invalidate_request_role(
            "test-2026", "mobile", "account_lead", packet_hash="different", reason="new"
        )
        assert store.list_approvals("test-2026")[0].status == "stale"


# ------------------------------------------------------------------- receipts
def test_receipts_round_trip_and_chain(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        pkt = packet()
        store.save_packet(pkt)
        keyring = Keyring.generate()
        first = receipt(pkt, keyring)
        store.save_receipt(first)
        assert store.load_receipt(first.body.receipt_id).body.total == 30000.0
        assert store.latest_receipt("test-2026").body.receipt_id == first.body.receipt_id
        assert [item.body.receipt_id for item in store.list_receipts("test-2026")] == [
            first.body.receipt_id
        ]
        assert store.list_receipts() == [first]
        assert store.previous_receipt_hash("test-2026") == first.body_hash


def test_a_sealed_body_cannot_change_but_an_acceptance_can_be_added(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        pkt = packet()
        store.save_packet(pkt)
        keyring = Keyring.generate()
        original = receipt(pkt, keyring)
        store.save_receipt(original)

        accepted = countersign(original, Keyring.generate(), "Jordan Reyes")
        store.save_receipt(accepted)
        assert store.load_receipt(original.body.receipt_id).acceptance is not None

        tampered = original.model_copy(deep=True)
        tampered.body.total = 1.0
        with pytest.raises(StateError):
            store.save_receipt(tampered)


def test_missing_receipt_has_a_seal_hint(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store, pytest.raises(NotFoundError) as info:
        store.latest_receipt("test-2026")
    assert "seal" in (info.value.hint or "")


def test_previous_receipt_hash_is_none_for_the_first_change_order(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        assert store.previous_receipt_hash("test-2026") is None


# ---------------------------------------------------------------------- audit
def test_audit_chain_is_hash_linked_and_verifiable(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.audit("first", "tester", {"a": 1})
        store.audit("second", "tester", {"b": 2})
        events = store.list_audit()
        assert events[0]["prev_hash"] == "GENESIS"
        assert events[1]["prev_hash"] == events[0]["hash"]
        ok, detail = store.verify_audit()
        assert ok is True
        assert "2 events" in detail
        assert [item["kind"] for item in store.audit_events()] == ["first", "second"]


def test_editing_an_audit_row_breaks_the_chain(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.audit("first", "tester", {"a": 1})
        store.conn.execute("UPDATE audit SET detail=? WHERE seq=1", ('{"a":2}',))
        store.conn.commit()
        ok, detail = store.verify_audit()
        assert ok is False
        assert "row hash" in detail


def test_breaking_a_previous_hash_link_is_detected(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.audit("first", "tester", {"a": 1})
        store.audit("second", "tester", {"b": 2})
        store.conn.execute("UPDATE audit SET prev_hash='BROKEN' WHERE seq=2")
        store.conn.commit()
        ok, detail = store.verify_audit()
        assert ok is False
        assert "previous hash link" in detail


def test_an_empty_audit_chain_is_valid(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        assert store.verify_audit() == (True, "audit chain intact (0 events)")


# ---------------------------------------------------------------- room bindings
def test_room_bindings_round_trip_and_update(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        assert store.rooms("test-2026") == (None, None)
        store.bind_rooms("test-2026", "room-scope", "room-delivery")
        assert store.rooms("test-2026") == ("room-scope", "room-delivery")
        store.bind_rooms("test-2026", "room-scope-2", None)
        assert store.rooms("test-2026") == ("room-scope-2", None)


# ----------------------------------------------------------------------- meta
def test_meta_values_upsert(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        assert store.get_meta("schema") is None
        store.set_meta("schema", "1")
        store.set_meta("schema", "2")
        assert store.get_meta("schema") == "2"


def test_stats_count_every_table(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite3") as store:
        store.save_engagement(engagement())
        store.replace_evidence("test-2026", [evidence()])
        pkt = packet()
        store.save_packet(pkt)
        store.add_approval(approval(pkt))
        store.save_receipt(receipt(pkt, Keyring.generate()))
        store.audit("test", "tester", {})
        assert store.stats() == {
            "engagements": 1,
            "evidence": 1,
            "packets": 1,
            "approvals": 1,
            "receipts": 1,
            "audit": 1,
        }
