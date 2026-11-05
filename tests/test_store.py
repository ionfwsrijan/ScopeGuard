"""The SQLite ledger, approval invalidation, and the audit hash chain."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from vouch.errors import NotFoundError, StateError
from vouch.models import (
    Approval,
    ApprovalStatus,
    Claim,
    ClaimType,
    Deal,
    Evidence,
    Finding,
    Packet,
    PacketStatus,
    Receipt,
    ReceiptApproval,
    ReceiptBody,
    ReceiptDecision,
    Severity,
    Verdict,
)
from vouch.store import Store

MOMENT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def make_store(tmp_path: Path, name: str = "ledger.sqlite3") -> Store:
    return Store(tmp_path / name)


def deal() -> Deal:
    return Deal(
        id="acme-2026",
        account="Acme Corp",
        requested_by="Priya",
        annual_list_price=240000,
        cost_ratio_pct=55,
        claims=[
            Claim(
                id="SSO",
                type=ClaimType.FEATURE,
                title="SSO",
                requested_text="SSO by 2026-12-31.",
            )
        ],
    )


def evidence() -> Evidence:
    return Evidence(
        id="EV-1",
        claim_id="SSO",
        kind="roadmap",
        source="Roadmap",
        observed_at=date(2026, 10, 24),
        summary="Beta in December.",
    )


def packet(version: int = 1, **overrides: object) -> Packet:
    body: dict[str, object] = {
        "deal_id": "acme-2026",
        "version": version,
        "account": "Acme Corp",
        "currency": "USD",
        "decisions": [
            {
                "claim_id": "SSO",
                "verdict": "narrow",
                "rationale": "beta only",
                "approved_text": "SSO beta.",
                "required_roles": ["AE"],
            }
        ],
        "packet_hash": f"{version:064x}",
        "created_at": MOMENT,
    }
    body.update(overrides)
    return Packet(**body)


def approval(approval_id: str = "apr_1", **overrides: object) -> Approval:
    body: dict[str, object] = {
        "id": approval_id,
        "deal_id": "acme-2026",
        "packet_id": "acme-2026-v1",
        "packet_hash": f"{1:064x}",
        "claim_id": "SSO",
        "role": "AE",
        "actor": "Priya",
        "phrase": "APPROVE ACME-2026 SSO AS AE",
        "created_at": MOMENT,
    }
    body.update(overrides)
    return Approval(**body)


def receipt(
    receipt_id: str = "vch_1", *, created: datetime = MOMENT, **overrides: object
) -> Receipt:
    body = ReceiptBody(
        receipt_id=receipt_id,
        deal_id="acme-2026",
        account="Acme Corp",
        packet_id="acme-2026-v1",
        packet_version=1,
        packet_hash=f"{1:064x}",
        policy_id="p@1",
        issued_at=created,
        expires_at=created,
        commitments=[
            ReceiptDecision(claim_id="SSO", verdict=Verdict.NARROW, commitment="SSO beta.")
        ],
        approvals=[
            ReceiptApproval(
                claim_id="SSO",
                role="AE",
                actor="Priya",
                phrase="APPROVE ACME-2026 SSO AS AE",
                approved_at=created,
            )
        ],
    )
    body = body.model_copy(update=overrides)
    return Receipt(body=body, body_hash="a" * 64, signature="sig", public_key="key")


# ------------------------------------------------------------------------ deals
def test_deal_round_trip(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        assert store.load_deal("acme-2026").account == "Acme Corp"
        assert store.deal_exists("acme-2026")
        assert not store.deal_exists("other")
        assert [item.id for item in store.list_deals()] == ["acme-2026"]


def test_save_deal_is_an_upsert(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        changed = deal().model_copy(update={"account": "Acme Inc"})
        store.save_deal(changed, now=MOMENT)
        assert store.load_deal("acme-2026").account == "Acme Inc"


def test_missing_deal_has_a_hint(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        with pytest.raises(NotFoundError) as info:
            store.load_deal("nope")
        assert "ingest" in (info.value.hint or "")


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    with Store(path) as store:
        store.save_deal(deal(), now=MOMENT)
    with Store(path) as store:
        assert store.load_deal("acme-2026").id == "acme-2026"


# --------------------------------------------------------------------- evidence
def test_evidence_replace_and_list(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.replace_evidence("acme-2026", [evidence()])
        assert [item.id for item in store.list_evidence("acme-2026")] == ["EV-1"]
        store.replace_evidence("acme-2026", [])
        assert store.list_evidence("acme-2026") == []


# ---------------------------------------------------------------------- packets
def test_packet_round_trip_and_ordering(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.save_packet(packet(1))
        store.save_packet(packet(2))
        assert store.load_packet("acme-2026-v1").version == 1
        assert store.latest_packet("acme-2026").version == 2
        assert [item.version for item in store.list_packets("acme-2026")] == [2, 1]
        assert store.count_packets("acme-2026") == 2


def test_duplicate_packet_version_is_rejected(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.save_packet(packet(1))
        with pytest.raises(StateError):
            store.save_packet(packet(1))


def test_update_packet_persists_status_and_findings(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        value = packet(1)
        store.save_packet(value)
        value.status = PacketStatus.BLOCKED
        value.findings = [Finding(id="EX-001", severity=Severity.BLOCK, code="x", message="bad")]
        store.update_packet(value)
        loaded = store.latest_packet("acme-2026")
        assert loaded.status == PacketStatus.BLOCKED
        assert loaded.findings[0].code == "x"


def test_missing_packet_errors(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        with pytest.raises(NotFoundError):
            store.latest_packet("acme-2026")
        with pytest.raises(NotFoundError):
            store.load_packet("acme-2026-v9")


# -------------------------------------------------------------------- approvals
def test_add_and_list_approvals(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.add_approval(approval("apr_1"))
        store.add_approval(approval("apr_2", role="VP_SALES", actor="Maya"))
        assert len(store.list_approvals("acme-2026")) == 2
        assert len(store.list_approvals("acme-2026", status="valid")) == 2
        assert store.list_approvals("acme-2026", status="stale") == []


def test_add_approval_is_idempotent_by_id(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.add_approval(approval("apr_1"))
        store.add_approval(approval("apr_1", actor="Someone else"))
        rows = store.list_approvals("acme-2026")
        assert len(rows) == 1
        assert rows[0].actor == "Priya"


def test_approvals_for_packet_requires_valid_status_and_hash(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        value = packet(1)
        store.save_packet(value)
        store.add_approval(approval("apr_valid", packet_hash=value.packet_hash))
        store.add_approval(approval("apr_other", packet_hash="f" * 64))
        store.add_approval(
            approval("apr_stale", packet_hash=value.packet_hash, status=ApprovalStatus.STALE.value)
        )
        assert [item.id for item in store.approvals_for_packet(value)] == ["apr_valid"]


def test_invalidate_approvals_marks_other_hashes_stale(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.add_approval(approval("apr_old", packet_hash="a" * 64))
        store.add_approval(approval("apr_current", packet_hash="b" * 64))
        changed = store.invalidate_approvals("acme-2026", keep_hash="b" * 64, reason="revision")
        assert changed == 1
        statuses = {item.id: item.status for item in store.list_approvals("acme-2026")}
        assert statuses == {"apr_old": "stale", "apr_current": "valid"}


def test_invalidate_claim_role_is_scoped(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.add_approval(approval("apr_old", packet_hash="a" * 64))
        store.add_approval(approval("apr_other_role", packet_hash="a" * 64, role="LEGAL"))
        store.invalidate_claim_role(
            "acme-2026", "sso", "ae", packet_hash="b" * 64, reason="re-approved"
        )
        statuses = {item.id: item.status for item in store.list_approvals("acme-2026")}
        assert statuses == {"apr_old": "stale", "apr_other_role": "valid"}
        # LEGAL was not named, so it keeps its status even though the hash differs.
        store.add_approval(approval("apr_legal", packet_hash="a" * 64, role="LEGAL", actor="Alex"))
        store.invalidate_claim_role("acme-2026", "SSO", "AE", packet_hash="b" * 64, reason="again")
        statuses = {item.id: item.status for item in store.list_approvals("acme-2026")}
        assert statuses["apr_legal"] == "valid"


# --------------------------------------------------------------------- receipts
def test_receipt_round_trip_and_ordering(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.save_receipt(receipt("vch_1", created=MOMENT))
        later = MOMENT.replace(hour=10)
        store.save_receipt(receipt("vch_2", created=later))
        assert store.load_receipt("vch_1").body.receipt_id == "vch_1"
        assert store.latest_receipt("acme-2026").body.receipt_id == "vch_2"
        assert [item.body.receipt_id for item in store.list_receipts("acme-2026")] == [
            "vch_2",
            "vch_1",
        ]
        assert len(store.list_receipts()) == 2


def test_previous_receipt_hash(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        assert store.previous_receipt_hash("acme-2026") is None
        store.save_receipt(receipt("vch_1"))
        assert store.previous_receipt_hash("acme-2026") == "a" * 64


def test_missing_receipt_errors(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        with pytest.raises(NotFoundError):
            store.load_receipt("vch_nope")
        with pytest.raises(NotFoundError):
            store.latest_receipt("acme-2026")


# ------------------------------------------------------------------------ audit
def test_audit_chain_is_intact(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.audit("one", "vouch", {"a": 1})
        store.audit("two", "vouch", {"b": 2})
        store.audit("three", "vouch", {"c": 3})
        ok, detail = store.verify_audit()
        assert ok is True
        assert "3 events" in detail
        assert [item["kind"] for item in store.list_audit()] == ["one", "two", "three"]
        assert [item["kind"] for item in store.audit_events()] == ["one", "two", "three"]


def test_audit_detects_changed_detail(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.audit("one", "vouch", {"a": 1})
        store.audit("two", "vouch", {"b": 2})
        with store.conn:
            store.conn.execute("UPDATE audit SET detail=? WHERE seq=2", ('{"b":3}',))
        ok, detail = store.verify_audit()
        assert ok is False
        assert "seq 2" in detail


def test_audit_detects_changed_previous_link(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.audit("one", "vouch", {"a": 1})
        store.audit("two", "vouch", {"b": 2})
        with store.conn:
            store.conn.execute("UPDATE audit SET prev_hash=? WHERE seq=2", ("f" * 64,))
        ok, detail = store.verify_audit()
        assert ok is False
        assert "previous hash link is broken" in detail


def test_audit_detects_changed_row_hash(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.audit("one", "vouch", {"a": 1})
        with store.conn:
            store.conn.execute("UPDATE audit SET hash=? WHERE seq=1", ("f" * 64,))
        ok, detail = store.verify_audit()
        assert ok is False
        assert "row hash" in detail


def test_audit_contains_json_detail(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.audit("one", "vouch", {"nested": {"a": [1, 2]}})
        row = store.list_audit()[0]
        assert json.loads(row["detail"]) == {"nested": {"a": [1, 2]}}


# ------------------------------------------------------------------ rooms/meta
def test_room_bindings_upsert(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        assert store.rooms("acme-2026") == (None, None)
        store.bind_rooms("acme-2026", "sales", "delivery")
        assert store.rooms("acme-2026") == ("sales", "delivery")
        store.bind_rooms("acme-2026", "sales-2", None)
        assert store.rooms("acme-2026") == ("sales-2", None)


def test_meta_round_trip(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        assert store.get_meta("x") is None
        store.set_meta("x", "1")
        assert store.get_meta("x") == "1"
        store.set_meta("x", "2")
        assert store.get_meta("x") == "2"


def test_stats(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.save_deal(deal(), now=MOMENT)
        store.replace_evidence("acme-2026", [evidence()])
        store.save_packet(packet(1))
        store.add_approval(approval())
        store.save_receipt(receipt())
        store.audit("x", "vouch", {})
        stats = store.stats()
        assert stats == {
            "deals": 1,
            "evidence": 1,
            "packets": 1,
            "approvals": 1,
            "receipts": 1,
            "audit": 1,
        }
