"""SQLite ledger with an append-only, hash-chained audit log.

The database is deliberately plain SQLite. The integrity story does not depend
on the database: packets are hashable, receipts are signed, and the audit log
is chained. SQLite is the local working copy, not the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vouch.clock import iso
from vouch.errors import NotFoundError, StateError
from vouch.models import (
    Approval,
    ApprovalStatus,
    Deal,
    Evidence,
    Packet,
    Receipt,
)
from vouch.receipt import canonical_hash

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    deal_id TEXT NOT NULL,
    id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (deal_id, id),
    FOREIGN KEY (deal_id) REFERENCES deals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS packets (
    id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    packet_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (deal_id, version),
    FOREIGN KEY (deal_id) REFERENCES deals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    role TEXT NOT NULL,
    actor TEXT NOT NULL,
    phrase TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    invalidated_reason TEXT,
    FOREIGN KEY (deal_id) REFERENCES deals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS approvals_deal ON approvals(deal_id, status);
CREATE INDEX IF NOT EXISTS approvals_hash ON approvals(packet_hash);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (deal_id) REFERENCES deals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS receipts_deal ON receipts(deal_id, created_at);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS room_bindings (
    deal_id TEXT PRIMARY KEY,
    sales_room_id TEXT,
    delivery_room_id TEXT,
    updated_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path, *, create: bool = True) -> None:
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI runs sync dependencies and endpoints on a worker thread. Each
        # request gets its own connection, so cross-thread use within a single
        # request is the only sharing that happens.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if create:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- deals
    def save_deal(self, deal: Deal, *, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        payload = deal.model_dump_json()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO deals(id, account, payload, created_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET account=excluded.account, payload=excluded.payload
                """,
                (deal.id, deal.account, payload, iso(moment)),
            )

    def load_deal(self, deal_id: str) -> Deal:
        row = self.conn.execute("SELECT payload FROM deals WHERE id=?", (deal_id,)).fetchone()
        if row is None:
            raise NotFoundError(
                f"deal not found: {deal_id}", hint="Run `vouch ingest --fixture acme`."
            )
        return Deal.model_validate_json(row["payload"])

    def list_deals(self) -> list[Deal]:
        rows = self.conn.execute("SELECT payload FROM deals ORDER BY id").fetchall()
        return [Deal.model_validate_json(row["payload"]) for row in rows]

    def deal_exists(self, deal_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM deals WHERE id=?", (deal_id,)).fetchone()
        return row is not None

    # ------------------------------------------------------------- evidence
    def replace_evidence(self, deal_id: str, items: list[Evidence]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM evidence WHERE deal_id=?", (deal_id,))
            self.conn.executemany(
                "INSERT INTO evidence(deal_id, id, claim_id, payload) VALUES (?, ?, ?, ?)",
                [(deal_id, item.id, item.claim_id, item.model_dump_json()) for item in items],
            )

    def list_evidence(self, deal_id: str) -> list[Evidence]:
        rows = self.conn.execute(
            "SELECT payload FROM evidence WHERE deal_id=? ORDER BY id", (deal_id,)
        ).fetchall()
        return [Evidence.model_validate_json(row["payload"]) for row in rows]

    # -------------------------------------------------------------- packets
    def save_packet(self, packet: Packet) -> None:
        existing = self.conn.execute(
            "SELECT 1 FROM packets WHERE id=?", (f"{packet.deal_id}-v{packet.version}",)
        ).fetchone()
        if existing is not None:
            raise StateError(f"packet version already exists: {packet.deal_id} v{packet.version}")
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO packets(id, deal_id, version, packet_hash, status, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{packet.deal_id}-v{packet.version}",
                    packet.deal_id,
                    packet.version,
                    packet.packet_hash,
                    packet.status,
                    packet.model_dump_json(),
                    iso(packet.created_at),
                ),
            )

    def _packet_from_row(self, row: sqlite3.Row) -> Packet:
        return Packet.model_validate_json(row["payload"])

    def load_packet(self, packet_id: str) -> Packet:
        row = self.conn.execute("SELECT payload FROM packets WHERE id=?", (packet_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"packet not found: {packet_id}")
        return self._packet_from_row(row)

    def latest_packet(self, deal_id: str) -> Packet:
        row = self.conn.execute(
            "SELECT payload FROM packets WHERE deal_id=? ORDER BY version DESC LIMIT 1",
            (deal_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"no packet for {deal_id}", hint="Run `vouch assess --deal <deal>` first."
            )
        return self._packet_from_row(row)

    def list_packets(self, deal_id: str) -> list[Packet]:
        rows = self.conn.execute(
            "SELECT payload FROM packets WHERE deal_id=? ORDER BY version DESC", (deal_id,)
        ).fetchall()
        return [self._packet_from_row(row) for row in rows]

    def update_packet(self, packet: Packet) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE packets SET status=?, packet_hash=?, payload=? WHERE id=?",
                (
                    packet.status,
                    packet.packet_hash,
                    packet.model_dump_json(),
                    f"{packet.deal_id}-v{packet.version}",
                ),
            )

    def count_packets(self, deal_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM packets WHERE deal_id=?", (deal_id,)
        ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------ approvals
    def add_approval(self, approval: Approval) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO approvals(id, deal_id, packet_id, packet_hash, claim_id, role, actor,
                                      phrase, created_at, status, invalidated_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    approval.id,
                    approval.deal_id,
                    approval.packet_id,
                    approval.packet_hash,
                    approval.claim_id,
                    approval.role,
                    approval.actor,
                    approval.phrase,
                    iso(approval.created_at),
                    approval.status,
                    approval.invalidated_reason,
                ),
            )

    def list_approvals(self, deal_id: str, *, status: str | None = None) -> list[Approval]:
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM approvals WHERE deal_id=? ORDER BY created_at, id", (deal_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM approvals WHERE deal_id=? AND status=? ORDER BY created_at, id",
                (deal_id, status),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def approvals_for_packet(self, packet: Packet) -> list[Approval]:
        rows = self.conn.execute(
            "SELECT * FROM approvals WHERE packet_hash=? AND status=? ORDER BY claim_id, role",
            (packet.packet_hash, ApprovalStatus.VALID.value),
        ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def _approval_from_row(self, row: sqlite3.Row) -> Approval:
        return Approval(
            id=row["id"],
            deal_id=row["deal_id"],
            packet_id=row["packet_id"],
            packet_hash=row["packet_hash"],
            claim_id=row["claim_id"],
            role=row["role"],
            actor=row["actor"],
            phrase=row["phrase"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
            status=row["status"],
            invalidated_reason=row["invalidated_reason"],
        )

    def invalidate_approvals(
        self, deal_id: str, *, keep_hash: str, reason: str, now: datetime | None = None
    ) -> int:
        del now
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE approvals
                   SET status=?, invalidated_reason=?
                 WHERE deal_id=? AND packet_hash<>? AND status=?
                """,
                (
                    ApprovalStatus.STALE.value,
                    reason,
                    deal_id,
                    keep_hash,
                    ApprovalStatus.VALID.value,
                ),
            )
        return cursor.rowcount

    def invalidate_claim_role(
        self, deal_id: str, claim_id: str, role: str, *, packet_hash: str, reason: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE approvals SET status=?, invalidated_reason=?
                 WHERE deal_id=? AND claim_id=? AND role=? AND packet_hash<>? AND status=?
                """,
                (
                    ApprovalStatus.STALE.value,
                    reason,
                    deal_id,
                    claim_id.upper(),
                    role.upper(),
                    packet_hash,
                    ApprovalStatus.VALID.value,
                ),
            )

    # -------------------------------------------------------------- receipts
    def save_receipt(self, receipt: Receipt) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO receipts(id, deal_id, packet_id, body_hash, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.body.receipt_id,
                    receipt.body.deal_id,
                    receipt.body.packet_id,
                    receipt.body_hash,
                    receipt.model_dump_json(),
                    iso(receipt.body.issued_at),
                ),
            )

    def load_receipt(self, receipt_id: str) -> Receipt:
        row = self.conn.execute("SELECT payload FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"receipt not found: {receipt_id}")
        return Receipt.model_validate_json(row["payload"])

    def latest_receipt(self, deal_id: str) -> Receipt:
        row = self.conn.execute(
            "SELECT payload FROM receipts WHERE deal_id=? ORDER BY created_at DESC LIMIT 1",
            (deal_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"no receipt for {deal_id}",
                hint="Run `vouch seal --deal <deal>` after the gate passes.",
            )
        return Receipt.model_validate_json(row["payload"])

    def list_receipts(self, deal_id: str | None = None) -> list[Receipt]:
        if deal_id:
            rows = self.conn.execute(
                "SELECT payload FROM receipts WHERE deal_id=? ORDER BY created_at DESC", (deal_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT payload FROM receipts ORDER BY created_at DESC"
            ).fetchall()
        return [Receipt.model_validate_json(row["payload"]) for row in rows]

    def previous_receipt_hash(self, deal_id: str) -> str | None:
        receipts = self.list_receipts(deal_id)
        if not receipts:
            return None
        return receipts[0].body_hash

    # ----------------------------------------------------------------- audit
    def audit(self, kind: str, actor: str, detail: dict[str, Any]) -> None:
        row = self.conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else "GENESIS"
        moment = datetime.now(UTC)
        material = {
            "at": iso(moment),
            "kind": kind,
            "actor": actor,
            "detail": detail,
            "prev_hash": prev,
        }
        digest = canonical_hash(material)
        with self.conn:
            self.conn.execute(
                "INSERT INTO audit(at, kind, actor, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    material["at"],
                    kind,
                    actor,
                    json.dumps(detail, sort_keys=True, separators=(",", ":")),
                    prev,
                    digest,
                ),
            )

    def list_audit(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM audit ORDER BY seq").fetchall()
        return [dict(row) for row in rows]

    def audit_events(self) -> Iterator[dict[str, Any]]:
        yield from self.list_audit()

    def verify_audit(self) -> tuple[bool, str]:
        previous = "GENESIS"
        for row in self.list_audit():
            material = {
                "at": row["at"],
                "kind": row["kind"],
                "actor": row["actor"],
                "detail": json.loads(row["detail"]),
                "prev_hash": previous,
            }
            expected = canonical_hash(material)
            if row["prev_hash"] != previous:
                return False, f"audit seq {row['seq']}: previous hash link is broken"
            if row["hash"] != expected:
                return False, f"audit seq {row['seq']}: row hash does not match its contents"
            previous = row["hash"]
        return True, f"audit chain intact ({len(self.list_audit())} events)"

    # ------------------------------------------------------------- room links
    def bind_rooms(
        self, deal_id: str, sales_room_id: str | None, delivery_room_id: str | None
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO room_bindings(deal_id, sales_room_id, delivery_room_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(deal_id) DO UPDATE SET
                    sales_room_id=excluded.sales_room_id,
                    delivery_room_id=excluded.delivery_room_id,
                    updated_at=excluded.updated_at
                """,
                (deal_id, sales_room_id, delivery_room_id, iso(datetime.now(UTC))),
            )

    def rooms(self, deal_id: str) -> tuple[str | None, str | None]:
        row = self.conn.execute(
            "SELECT sales_room_id, delivery_room_id FROM room_bindings WHERE deal_id=?",
            (deal_id,),
        ).fetchone()
        if row is None:
            return None, None
        return row["sales_room_id"], row["delivery_room_id"]

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else row["value"]

    def stats(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "deals": count("deals"),
            "evidence": count("evidence"),
            "packets": count("packets"),
            "approvals": count("approvals"),
            "receipts": count("receipts"),
            "audit": count("audit"),
        }
