# Architecture

Scope Guard is a small system with one large invariant:

> **Nothing leaves the change-order room unless it is the exact thing named
> humans signed, and the two-party change order proves it.**

Everything below exists to make that sentence true even when an LLM is
enthusiastic, a human is in a hurry, or a packet changes after the first
signature.

## Layers

```
┌───────────────────────────────────────────────────────────────────────────┐
│ Conversation              Switch rooms: scope-northwind → delivery-...    │
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
│ State                     SQLite: engagements, evidence, packets,         │
│                           approvals, change orders, audit, room bindings  │
└───────────────────────────────────────────────────────────────────────────┘
```

The deterministic core does not import FastAPI, Rich, httpx, or any model SDK.
It can run in a test, a CLI, a job, or behind a room agent.

## Domain flow

```
fixtures/northwind/engagement.yaml ─┐
fixtures/northwind/evidence.yaml   ─┼─► ingest ─► engagement + evidence
policies/scope_policy_v1.yaml      ─┘

engagement + evidence + policy ─► assess ─► Packet v1 (hash H1)
                                               │
                   humans sign H1 too early    │
                                               ├─► challenge ─► findings
                                               │      block ⇒ revise
                                               ▼
                                        Packet v2 (hash H2)
                                               │
                  Switch messages ─► sync ─► signatures bound to H2
                                               │
                                          gate(H2)
                                               │
                                               ▼
                                            seal ─► change order (signs H2)
                                               │
                              client countersigns the body hash
                                               │
                                            verify (up to 19 checks)
                                               │
                                            handoff → delivery room
```

For the bundled Northwind run, `H1` is `ed19403e…`, `H2` is `2d3f8963…`, and the
change order is `co_5d7602a73cba86de6db28804`.

## Typed model

All persisted data is a Pydantic model in `src/scopeguard/models.py`.

| Model | Meaning |
| --- | --- |
| `Engagement` | Client, fixed fee, delivery cost ratio, and `ScopeRequest`s |
| `ScopeRequest` | One ask: `new_work`, `timeline`, `rate`, or `terms` |
| `Evidence` | Dated, sourced support for a request; has a content digest |
| `EvidenceBook` | All evidence for an engagement, validated for referential integrity |
| `Decision` | Verdict + priced language + amount + conditions + roles |
| `Finding` | Red Team finding with severity, code, and machine-readable remediation |
| `Packet` | Versioned decisions, findings, status, hash, timestamps |
| `PacketBody` | Only the part humans sign; this is what is hashed |
| `Approval` | Actor, role, request, exact phrase, packet hash, status |
| `ReceiptBody` | Line items, declined asks, signatures, expiry, chain link |
| `Receipt` | Body + body hash + Ed25519 issuer signature + optional acceptance |
| `Acceptance` | The client's countersignature over the issuer's body hash |
| `GateResult` | Named checks and pending request/role pairs |
| `VerificationResult` | Named checks and a human-readable summary |

### Why `PacketBody` is separate

`Packet` carries findings, status, and timestamps. `PacketBody` carries only the
commitments. The packet hash is computed over the body, so:

- The Red Team adding a finding does **not** invalidate a signature.
- Changing a price, a condition, or the version **does**.

This is intentional. You sign the promise, not the reviewer's notes.

## Deterministic pricing

`assess.py` is a set of pure functions. For each request type:

- **`new_work`** reads an `estimate`. If GA fits the requested date, it prices
  the full estimate. If only beta fits, it narrows to the beta, prices the beta
  hours, and says GA is targeted but not included. No feasible date escalates.
- **`timeline`** reads a `capacity` plan. It approves at most the requested
  weeks, the supported weeks, and the policy cap. Confidence below the floor
  escalates; no compressible weeks refuses. The rush hours carry the premium.
- **`rate`** reads a `rate_card`. It applies the requested discount to the sum
  of positive line items, hard-capped at the policy maximum. The autonomy and
  margin floors belong to the Red Team, not the Pricer.
- **`terms`** reads a `contract`. It refuses unlimited liability, caps mutual
  liability at the policy multiple, prices the legal review, and keeps the
  security addendum bounded.

Every decision records `policy_refs` and `evidence_ids`. There is no free-text
number in a decision that cannot be traced to an input.

The rate line is computed after every positive line item, regardless of where
the rate request appears in the fixture. A discount cannot accidentally apply
to a subtotal that is still being assembled.

## The Red Team

`challenge.py` assumes the packet is wrong. It checks, among other things:

| Code | Severity | Blocks when |
| --- | --- | --- |
| `request_unpriced` | block | A request has no decision |
| `approval_matrix_mismatch` | block | Required roles no longer match policy |
| `commitment_without_evidence` | block | A commitment has no linked evidence |
| `evidence_stale` | block | Evidence is older than the policy window |
| `evidence_expired` | block | Evidence is past `valid_until` |
| `unpriced_commitment` | block | A commitment has no amount |
| `commitment_without_conditions` | warn | A narrow commitment lost its boundaries |
| `unexpected_credit` | block | A negative amount appears outside the rate line |
| `rate_over_policy` | block | Discount exceeds the headline ceiling |
| `rate_autonomy` | block | Discount exceeds the autonomy ceiling |
| `margin_floor` | block | Resulting gross margin is below the floor |
| `base_amount_mismatch` | block | The discounted subtotal disagrees with the line items |
| `rate_amount_mismatch` | block | The discount amount is not the stated percentage |
| `timeline_over_policy` | block | Acceleration exceeds the policy cap |
| `timeline_confidence` | block | Acceleration is committed below the confidence floor |
| `rush_not_priced` | block | Acceleration has no positive rush line item |
| `new_work_overclaim` | block | Beta-only work is described as GA |
| `beta_missing_condition` | warn | A beta line item does not name its boundary |
| `terms_over_policy` | block | Liability exceeds the policy multiple |
| `terms_unlimited_language` | block | Unlimited liability is described without refusal |
| `terms_missing_legal_review` | block | Terms have no legal-review condition |
| `missing_decision` | block | A request has no decision anywhere |
| `empty_packet` | block | The packet contains no decisions |

`revise` applies the smallest correction the Red Team proposes, creates a new
packet with `previous_hash`, and invalidates every signature bound to the prior
hash. Stale signatures remain in the ledger for audit, but cannot satisfy the
gate.

## Signature gate

`gate_packet` produces named checks:

- `status` — the packet is `challenged` or `sealed`, not a draft or blocked.
- `no_blocking_findings` — the Red Team left no `block` finding.
- `every_line_item_priced` — every commitment has an amount.
- `signatures` — every required request/role pair has a valid signature on this
  hash.
- `signature_count` — at least one distinct pair was satisfied.
- `stale_signatures_ignored` — stale signatures are reported and excluded.
- `actor_role:<request> <role>` — dynamic failure when the signer no longer
  holds the role.
- `phrase:<request> <role>` — dynamic failure when the recorded phrase does not
  match the canonical phrase.

A valid signature requires all of:

1. The sender exists in `fixtures/northwind/approvers.yaml` and holds the role.
2. The message body contains exactly one phrase:
   `SIGN <ENGAGEMENT> <REQUEST> AS <ROLE>`.
3. The request and role are required by the current packet.
4. The signature binds to the **current** packet hash.
5. There is no duplicate for the same request and role.

The parser is deliberately strict. "I approve the discount" is chatter. A
phrase in a bot's summary is not a signature.

## Change order and verification

`receipt.py`:

1. Serializes `ReceiptBody` with canonical JSON (sorted keys, no whitespace,
   UTF-8, JSON-safe types).
2. Computes `body_hash = SHA-256(canonical_json(body))`.
3. Derives `receipt_id = "co_" + SHA-256({engagement_id, packet_hash,
   issued_at, chain_prev})[:24]`.
4. Signs the body hash with Ed25519.
5. Embeds the issuer's public key so the artifact is self-contained.
6. Links `chain_prev` to the previous change order for the engagement.
7. Optionally records a client acceptance: a second Ed25519 signature over
   `canonical_json({"body_hash", "issuer", "public_key"})`.

The countersignature is deliberately over the issuer's **body hash**, not over
the client's copy of the body. If the body changes, the hash changes and the
acceptance fails. The client key must be distinct from the issuer key.

Verification never trusts the database row it loaded. It recomputes every hash
from the serialized body and then optionally cross-checks the local ledger:

| Check | What it proves |
| --- | --- |
| `algorithm` | The issuer uses the expected algorithm |
| `body_hash` | The body was not edited |
| `receipt_id` | The id is derived from the signed body |
| `signature` | The issuer signature matches the embedded key |
| `pinned_key` | Optional: the issuer key matches a trusted local key |
| `no_duplicate_signatures` | One signature per request and role |
| `line_items_signed` | Every line item has a signature |
| `signature_binding` | Every phrase starts with `SIGN` and names this engagement |
| `every_line_item_priced` | Every amount is finite |
| `amounts_balance` | Subtotal, discount, and total agree |
| `unexpired` | Optional: the change order has not expired |
| `acceptance_present` | Optional: the client countersigned |
| `acceptance_algorithm` | The client uses Ed25519 |
| `acceptance_key_distinct` | The client key is not the issuer key |
| `acceptance` | The client signature covers this body hash |
| `pinned_client_key` | Optional: the client key matches a trusted local key |
| `packet_rehash` | The local packet still hashes to the recorded packet hash |
| `packet_version` | The change order binds the packet version it claims |
| `line_items_match_packet` | The change order line items match the packet |
| `recorded` | The change order exists in the local ledger |

`packet_present` is added when a standalone verifier does not have the local
packet. That is not a failure: a self-contained change order can be verified by
someone who does not have the issuer's database.

## Audit chain

Every state transition appends an event:

```
hash_i = SHA-256(prev_hash_i || canonical_json({kind, actor, detail, at}))
```

`verify_audit` recomputes the full chain. Editing, deleting, or reordering an
event breaks it. This is separate from the change-order chain: the audit chain
covers operations; the change-order chain covers issued promises.

## Concurrency and storage

SQLite with WAL-friendly defaults, one connection per service instance. FastAPI
opens a connection per request and closes it in a dependency. The ledger is
local by default; `--home` (or `SCOPE_HOME`) points it at a shared directory
when a team wants one ledger.

Signatures are unique on `(engagement_id, packet_hash, request_id, role)`. The
service also checks duplicates before insert and returns a clean error. A
sealed body cannot change; a client acceptance may be added once.

## Failure modes the design handles

| Failure | Control |
| --- | --- |
| Model invents a date or amount | Evidence is required; the Red Team blocks unsupported or unpriced commitments |
| Model signs its own work | Agents have no signature path; only named humans in the approver map count |
| Human signs too early | The gate counts only current-hash signatures |
| Packet changes after signature | Hash-bound signatures become stale |
| Someone edits the change order | Body hash, id derivation, and signature fail |
| Someone edits history | Audit chain fails |
| Sales sends an unsigned promise to delivery | Delivery only receives the countersigned handoff |
| Replay of an old signature | The old hash does not satisfy the gate |
| Wrong person claims a role | Explicit role map, case-insensitive person match |
| A tampered change order is presented as valid | `/verify` and `scopeguard verify` fail closed |

## What is intentionally not here

Scope Guard is not a CRM, a billing engine, a legal contract, or a
general-purpose workflow builder. It does not call an LLM in the deterministic
path, and it does not store model prompts as evidence. See
[VALIDITY.md](VALIDITY.md).
