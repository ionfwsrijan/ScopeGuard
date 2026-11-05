# Architecture

Vouch is a small system with one large invariant:

> **Nothing leaves the underwriting room unless it is the exact thing named
> humans approved, and the receipt proves it.**

Everything below exists to make that sentence true even when an LLM is
enthusiastic, a human is in a hurry, or a packet changes after the first
approval.

## Layers

```
┌───────────────────────────────────────────────────────────────────────────┐
│ Conversation              Switch rooms: deal-acme → delivery-acme        │
│                           five agents, named humans, one link            │
├───────────────────────────────────────────────────────────────────────────┤
│ Orchestration             service.py (CLI, API, demo, Clerk all call it) │
├───────────────────────────────────────────────────────────────────────────┤
│ Decision core             assess.py · challenge.py · policy.py            │
│                           pure functions over typed models                │
├───────────────────────────────────────────────────────────────────────────┤
│ Trust artifacts           receipt.py · store.py                          │
│                           canonical JSON · SHA-256 · Ed25519 · audit chain │
├───────────────────────────────────────────────────────────────────────────┤
│ State                     SQLite: deals, evidence, packets, approvals,    │
│                           receipts, audit, room_bindings                 │
└───────────────────────────────────────────────────────────────────────────┘
```

The deterministic core does not import FastAPI, Rich, httpx, or any model SDK.
It can run in a test, a CLI, a job, or behind a room agent.

## Domain flow

```
fixtures/acme/request.yaml ──┐
fixtures/acme/evidence.yaml ─┼─► ingest ─► deals + evidence (content-addressed)
policies/deal_policy_v1.yaml ┘

deal + evidence + policy ──► assess ──► Packet v1 (hash H1)
                                          │
                                          ├─► challenge ──► findings
                                          │      block ⇒ revise
                                          │
                                          ▼
                                   Packet v2 (hash H2)
                                          │
             Switch messages ──► sync ──► approvals bound to H2
                                          │
                                     gate(H2)
                                          │
                                          ▼
                                       seal ──► Receipt (signs H2 + approvals)
                                          │
                                          ├─► verify (12 checks)
                                          └─► handoff → delivery room
```

## Typed model

All persisted data is a Pydantic model in `src/vouch/models.py`.

| Model | Meaning |
| --- | --- |
| `Deal` | Account, currency, list price, cost ratio, and `Claim`s |
| `Claim` | One ask: `feature`, `sla`, `discount`, `security` |
| `Evidence` | Dated, sourced support for a claim; has a content digest |
| `Decision` | Verdict + exact approved language + conditions + roles |
| `Packet` | Versioned set of decisions, findings, status, hash |
| `PacketBody` | Only the part humans approve; this is what is hashed |
| `Approval` | Actor, role, claim, exact phrase, packet hash, status |
| `ReceiptBody` | Commitments, declined asks, approvals, expiry, chain link |
| `Receipt` | Body + body hash + Ed25519 signature + public key |
| `GateResult` | Named checks and pending claim/role pairs |

### Why `PacketBody` is separate

`Packet` carries findings, status, and timestamps. `PacketBody` carries only the
commitments. The packet hash is computed over the body, so:

- The Examiner adding a finding does **not** invalidate approvals.
- Changing a commitment, a condition, or the version **does** invalidate them.

This is intentional. You approve the promise, not the reviewer's notes.

## Deterministic assessment

`assess.py` is a set of pure functions. For each claim type:

- **feature** — reads roadmap evidence. If GA is later than requested, it
  narrows to the evidenced beta date and explicitly refuses to commit to GA.
- **sla** — compares requested uptime with the policy maximum and observed
  uptime. It narrows to the lower of policy standard and observation, and adds
  credits and maintenance conditions.
- **discount** — computes the margin at the requested discount. Above the
  headline ceiling it refuses; above autonomy or below the margin floor it
  approves with conditions that the Examiner will later block.
- **security** — accepts `certified` or `in_progress`, but an in-progress claim
  must name a report date and may not use the word "certified".

Every decision records `policy_refs` and `evidence_ids`. There is no free-text
number in a decision that cannot be traced to an input.

## The Examiner

`challenge.py` assumes the packet is wrong. It currently checks:

| Code | Blocks when |
| --- | --- |
| `no_evidence` | A commitment has no evidence |
| `stale_evidence` | Evidence is expired or older than policy allows |
| `unsupported_ga` | A GA date is not supported by roadmap evidence |
| `sla_above_policy` | Requested uptime exceeds the policy maximum |
| `sla_above_observed` | Approved uptime exceeds observed uptime without a caveat |
| `discount_autonomy` | Discount exceeds the autonomy ceiling without an exception |
| `margin_floor` | Resulting gross margin is below the floor |
| `security_overclaim` | "Certified" language without a certificate |
| `missing_report_date` | In-progress security claim has no report date |
| `unassessed_claim` | A deal claim has no decision |
| `matrix_mismatch` | Required roles do not match policy |

`revise` applies the smallest correction the Examiner proposes, creates a new
packet with `previous_hash`, and invalidates every approval bound to the prior
hash. Stale approvals remain in the ledger for audit, but cannot satisfy the
gate.

## Approval gate

`gate_packet` produces named checks:

- `status` — the packet is not already sealed or superseded.
- `no_blocking_findings` — the Examiner left no `block` finding.
- `approvals` — every required claim/role pair has a valid approval.
- `approval_count` — the count of distinct valid pairs.
- `stale_approvals_ignored` — stale approvals do not bind this hash.

A valid approval requires all of:

1. The sender exists in `fixtures/acme/approvers.yaml` and holds the role.
2. The message body matches `APPROVE <DEAL> <CLAIM> AS <ROLE>`.
3. The claim and role are required by the current packet.
4. The approval is bound to the **current** packet hash.
5. There is no duplicate for the same claim and role.

The phrase parser is deliberately strict. "I approve the discount" is chatter.

## Receipt and verification

`receipt.py`:

1. Serializes `ReceiptBody` with canonical JSON (sorted keys, no whitespace,
   UTF-8, JSON-safe types).
2. Computes `body_hash = SHA-256(canonical_json)`.
3. Derives `receipt_id = "vch_" + first 24 hex chars of body_hash`.
4. Signs `body_hash` with Ed25519.
5. Embeds the public key so the receipt is self-contained.
6. Links `chain_prev` to the previous receipt hash for the deal.

Verification never trusts the database row it loaded. It recomputes every hash
from the serialized body and then optionally cross-checks the local ledger:

| Check | What it proves |
| --- | --- |
| `algorithm` | The receipt uses the expected algorithm |
| `body_hash` | The body was not edited |
| `receipt_id` | The id is derived from the signed body |
| `signature` | The signature matches the embedded key |
| `no_duplicate_approvals` | One approval per claim/role |
| `commitments_approved` | Every commitment has an approval |
| `approval_binding` | Every phrase names this deal |
| `unexpired` | Expiry is enforced when requested |
| `packet_rehash` | The local packet still hashes to the receipt's hash |
| `packet_version` | The receipt binds the packet version it claims |
| `commitments_match_packet` | Receipt text matches the packet exactly |
| `recorded` | The receipt exists in the local ledger |

`--pinned-key` additionally requires the embedded key to equal the local signing
key. Without pinning, possession of a receipt and its embedded key is enough to
verify integrity — which is the point of a self-contained artifact.

## Audit chain

Every state transition appends an event:

```
hash_i = SHA-256(prev_hash_i || canonical_json({kind, actor, detail, at}))
```

`verify_audit` recomputes the full chain. Editing, deleting, or reordering an
event breaks it. This is separate from the receipt chain: the audit chain covers
operations; the receipt chain covers issued promises.

## Concurrency and storage

SQLite with WAL-friendly defaults, one connection per service instance. FastAPI
opens a connection per request and closes it in a dependency. The ledger is
local by default; `--home` points it at a shared directory when a team wants one
ledger.

Approvals are inserted with a uniqueness constraint on
`(deal_id, packet_hash, claim_id, role)`. The service also checks duplicates
before insert and returns a clean error.

## Failure modes the design handles

| Failure | Control |
| --- | --- |
| Model invents a GA date | Evidence is required; Examiner blocks unsupported GA |
| Model approves its own work | Agents cannot approve; only named humans can |
| Human approves too early | Gate counts only current-hash approvals |
| Packet changes after approval | Hash-bound approvals become stale |
| Someone edits the receipt | Signature and body hash fail |
| Someone edits history | Audit chain fails |
| Sales sends an unapproved promise to delivery | Delivery only receives the sealed handoff |
| Replay of an old approval | Old hash does not satisfy the gate |
| Wrong person claims a role | Approver map is explicit and case-insensitive by person |

## What is intentionally not here

Vouch is not an contract system, a CRM, a billing engine, or a general-purpose
workflow builder. It does not call an LLM in the deterministic path, and it does
not store model prompts as evidence. See [VALIDITY.md](VALIDITY.md).
