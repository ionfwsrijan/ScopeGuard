"""SQLite ledger with an append-only, hash-chained audit log.

The database is deliberately plain SQLite. The integrity story does not depend
on the database: packets are hashable, change orders are signed, and the audit
log is chained. SQLite is the local working copy, not the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scopeguard.clock import iso
from scopeguard.errors import NotFoundError, StateError
from scopeguard.models import (
    Approval,
    ApprovalStatus,
    Engagement,
    Evidence,
    Packet,
    Receipt,
)
from scopeguard.receipt import canonical_hash

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS engagements (
    id TEXT PRIMARY KEY,
    client TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    engagement_id TEXT NOT NULL,
    id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (engagement_id, id),
    FOREIGN KEY (engagement_id) REFERENCES engagements(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS packets (
    id TEXT PRIMARY KEY,
    engagement_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    packet_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (engagement_id, version),
    FOREIGN KEY (engagement_id) REFERENCES engagements(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    engagement_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    request_id TEXT NOT NULL,
    role TEXT NOT NULL,
    actor TEXT NOT NULL,
    phrase TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    invalidated_reason TEXT,
    FOREIGN KEY (engagement_id) REFERENCES engagements(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS approvals_engagement ON approvals(engagement_id, status);
CREATE INDEX IF NOT EXISTS approvals_hash ON approvals(packet_hash);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    engagement_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (engagement_id) REFERENCES engagements(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS receipts_engagement ON receipts(engagement_id, created_at);

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
    engagement_id TEXT PRIMARY KEY,
    scope_room_id TEXT,
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

    # ----------------------------------------------------------- engagements
    def save_engagement(self, engagement: Engagement, *, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        payload = engagement.model_dump_json()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO engagements(id, client, payload, created_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET client=excluded.client, payload=excluded.payload
                """,
                (engagement.id, engagement.client, payload, iso(moment)),
            )

    def load_engagement(self, engagement_id: str) -> Engagement:
        row = self.conn.execute(
            "SELECT payload FROM engagements WHERE id=?", (engagement_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"engagement not found: {engagement_id}",
                hint="Run `scopeguard ingest --fixture northwind`.",
            )
        return Engagement.model_validate_json(row["payload"])

    def list_engagements(self) -> list[Engagement]:
        rows = self.conn.execute("SELECT payload FROM engagements ORDER BY id").fetchall()
        return [Engagement.model_validate_json(row["payload"]) for row in rows]

    def engagement_exists(self, engagement_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM engagements WHERE id=?", (engagement_id,)).fetchone()
        return row is not None

    # -------------------------------------------------------------- evidence
    def replace_evidence(self, engagement_id: str, items: list[Evidence]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM evidence WHERE engagement_id=?", (engagement_id,))
            self.conn.executemany(
                "INSERT INTO evidence(engagement_id, id, request_id, payload) VALUES (?, ?, ?, ?)",
                [
                    (engagement_id, item.id, item.request_id, item.model_dump_json())
                    for item in items
                ],
            )

    def list_evidence(self, engagement_id: str) -> list[Evidence]:
        rows = self.conn.execute(
            "SELECT payload FROM evidence WHERE engagement_id=? ORDER BY id", (engagement_id,)
        ).fetchall()
        return [Evidence.model_validate_json(row["payload"]) for row in rows]

    # --------------------------------------------------------------- packets
    def save_packet(self, packet: Packet) -> None:
        existing = self.conn.execute(
            "SELECT 1 FROM packets WHERE id=?", (f"{packet.engagement_id}-v{packet.version}",)
        ).fetchone()
        if existing is not None:
            raise StateError(
                f"packet version already exists: {packet.engagement_id} v{packet.version}"
            )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO packets(id, engagement_id, version, packet_hash, status, payload,
                                    created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{packet.engagement_id}-v{packet.version}",
                    packet.engagement_id,
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

    def latest_packet(self, engagement_id: str) -> Packet:
        row = self.conn.execute(
            "SELECT payload FROM packets WHERE engagement_id=? ORDER BY version DESC LIMIT 1",
            (engagement_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"no packet for {engagement_id}",
                hint="Run `scopeguard assess --engagement <engagement>` first.",
            )
        return self._packet_from_row(row)

    def list_packets(self, engagement_id: str) -> list[Packet]:
        rows = self.conn.execute(
            "SELECT payload FROM packets WHERE engagement_id=? ORDER BY version DESC",
            (engagement_id,),
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
                    f"{packet.engagement_id}-v{packet.version}",
                ),
            )

    def count_packets(self, engagement_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM packets WHERE engagement_id=?", (engagement_id,)
        ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------- approvals
    def add_approval(self, approval: Approval) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO approvals(id, engagement_id, packet_id, packet_hash, request_id, role,
                                      actor, phrase, created_at, status, invalidated_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    approval.id,
                    approval.engagement_id,
                    approval.packet_id,
                    approval.packet_hash,
                    approval.request_id,
                    approval.role,
                    approval.actor,
                    approval.phrase,
                    iso(approval.created_at),
                    approval.status,
                    approval.invalidated_reason,
                ),
            )

    def list_approvals(self, engagement_id: str, *, status: str | None = None) -> list[Approval]:
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM approvals WHERE engagement_id=? ORDER BY created_at, id",
                (engagement_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM approvals WHERE engagement_id=? AND status=? "
                "ORDER BY created_at, id",
                (engagement_id, status),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def approvals_for_packet(self, packet: Packet) -> list[Approval]:
        rows = self.conn.execute(
            "SELECT * FROM approvals WHERE packet_hash=? AND status=? ORDER BY request_id, role",
            (packet.packet_hash, ApprovalStatus.VALID.value),
        ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def _approval_from_row(self, row: sqlite3.Row) -> Approval:
        return Approval(
            id=row["id"],
            engagement_id=row["engagement_id"],
            packet_id=row["packet_id"],
            packet_hash=row["packet_hash"],
            request_id=row["request_id"],
            role=row["role"],
            actor=row["actor"],
            phrase=row["phrase"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
            status=row["status"],
            invalidated_reason=row["invalidated_reason"],
        )

    def invalidate_approvals(
        self, engagement_id: str, *, keep_hash: str, reason: str, now: datetime | None = None
    ) -> int:
        del now
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE approvals
                   SET status=?, invalidated_reason=?
                 WHERE engagement_id=? AND packet_hash<>? AND status=?
                """,
                (
                    ApprovalStatus.STALE.value,
                    reason,
                    engagement_id,
                    keep_hash,
                    ApprovalStatus.VALID.value,
                ),
            )
        return cursor.rowcount

    def invalidate_request_role(
        self, engagement_id: str, request_id: str, role: str, *, packet_hash: str, reason: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE approvals SET status=?, invalidated_reason=?
                 WHERE engagement_id=? AND request_id=? AND role=? AND packet_hash<>? AND status=?
                """,
                (
                    ApprovalStatus.STALE.value,
                    reason,
                    engagement_id,
                    request_id.upper(),
                    role.upper(),
                    packet_hash,
                    ApprovalStatus.VALID.value,
                ),
            )

    # -------------------------------------------------------------- receipts
    def save_receipt(self, receipt: Receipt) -> None:
        """Insert or update a change order.

        The only permitted update is adding a client countersignature; the body
        and body hash cannot change because they are covered by the issuer's
        signature.
        """
        existing = self.conn.execute(
            "SELECT payload FROM receipts WHERE id=?", (receipt.body.receipt_id,)
        ).fetchone()
        if existing is not None:
            old = Receipt.model_validate_json(existing["payload"])
            recomputed = canonical_hash(receipt.body)
            if (
                old.body_hash != receipt.body_hash
                or recomputed != receipt.body_hash
                or old.body.model_dump(mode="json") != receipt.body.model_dump(mode="json")
            ):
                raise StateError("a sealed change order body cannot change")
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO receipts(id, engagement_id, packet_id, body_hash, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload
                """,
                (
                    receipt.body.receipt_id,
                    receipt.body.engagement_id,
                    receipt.body.packet_id,
                    receipt.body_hash,
                    receipt.model_dump_json(),
                    iso(receipt.body.issued_at),
                ),
            )

    def load_receipt(self, receipt_id: str) -> Receipt:
        row = self.conn.execute("SELECT payload FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"change order not found: {receipt_id}")
        return Receipt.model_validate_json(row["payload"])

    def latest_receipt(self, engagement_id: str) -> Receipt:
        row = self.conn.execute(
            "SELECT payload FROM receipts WHERE engagement_id=? ORDER BY created_at DESC LIMIT 1",
            (engagement_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"no change order for {engagement_id}",
                hint="Run `scopeguard seal --engagement <engagement>` after the gate passes.",
            )
        return Receipt.model_validate_json(row["payload"])

    def list_receipts(self, engagement_id: str | None = None) -> list[Receipt]:
        if engagement_id:
            rows = self.conn.execute(
                "SELECT payload FROM receipts WHERE engagement_id=? ORDER BY created_at DESC",
                (engagement_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT payload FROM receipts ORDER BY created_at DESC"
            ).fetchall()
        return [Receipt.model_validate_json(row["payload"]) for row in rows]

    def previous_receipt_hash(self, engagement_id: str) -> str | None:
        receipts = self.list_receipts(engagement_id)
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
                "INSERT INTO audit(at, kind, actor, detail, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
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
        rows = self.list_audit()
        for row in rows:
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
        return True, f"audit chain intact ({len(rows)} events)"

    # ------------------------------------------------------------- room links
    def bind_rooms(
        self, engagement_id: str, scope_room_id: str | None, delivery_room_id: str | None
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO room_bindings(engagement_id, scope_room_id, delivery_room_id,
                                          updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(engagement_id) DO UPDATE SET
                    scope_room_id=excluded.scope_room_id,
                    delivery_room_id=excluded.delivery_room_id,
                    updated_at=excluded.updated_at
                """,
                (engagement_id, scope_room_id, delivery_room_id, iso(datetime.now(UTC))),
            )

    def rooms(self, engagement_id: str) -> tuple[str | None, str | None]:
        row = self.conn.execute(
            "SELECT scope_room_id, delivery_room_id FROM room_bindings WHERE engagement_id=?",
            (engagement_id,),
        ).fetchone()
        if row is None:
            return None, None
        return row["scope_room_id"], row["delivery_room_id"]

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else row["value"]

    def stats(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "engagements": count("engagements"),
            "evidence": count("evidence"),
            "packets": count("packets"),
            "approvals": count("approvals"),
            "receipts": count("receipts"),
            "audit": count("audit"),
        }
