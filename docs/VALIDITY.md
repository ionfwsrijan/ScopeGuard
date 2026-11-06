# Validity

This document exists because "it works in the demo" is not a claim about
production. It separates what is real, what is simulated, and what is
deliberately out of scope.

## What is real

| Component | Status | Evidence |
| --- | --- | --- |
| Pricing engine | Real, deterministic | `src/scopeguard/assess.py`, 304 tests |
| Red Team and revisions | Real, deterministic | `src/scopeguard/challenge.py`, tests for every finding code |
| Signature phrase parsing | Real, strict | `src/scopeguard/receipt.py`, parser tests |
| Hash-bound signature invalidation | Real | `store.invalidate_approvals`, revision tests |
| Canonical change order + Ed25519 | Real | `receipt.py`, tamper tests |
| Client countersignature | Real | `receipt.countersign`, acceptance tests |
| Change-order verification | Real, offline | `service.verify`, API `/verify`, CLI `verify` |
| Audit hash chain | Real | `store.verify_audit`, tamper tests |
| SQLite ledger | Real | `store.py` |
| Switch HTTP client | Real | `switch/client.py`, mocked-transport tests |
| Headless Clerk | Real | `switch/clerk.py`, in-memory room tests |
| Switch group template | Real format, validated by hand | `templates/scopeguard.template.yaml` |
| CLI | Real | `cli.py`, CLI tests |
| FastAPI UI and JSON API | Real | `api/app.py`, route tests |
| Demo | Real flow, simulated transport | `demo.py` |

The bundled flow signs a **two-party change order**. The issuer signs the
canonical body hash with an Ed25519 key and embeds the public key. The client
signs `canonical_json({"body_hash", "issuer", "public_key"})` with a distinct
key. If either the body or the issuer's signature changes, verification fails.

## What is simulated in the bundled demo

The demo substitutes **one thing**: the transport. `FakeSwitch` implements the
same two-method surface the Clerk uses (`read_context`, `post_message`) and
scripts human messages from a fixture. Everything downstream is the real code:

- the same `ScopeService`;
- the same policy and evidence fixtures;
- the same pricing and Red Team challenge;
- the same signature parser and invalidation;
- the same gate;
- the same change-order sealing and countersignature;
- the same verification checks;
- the same handoff text.

This is why the packet hashes, change-order id, and body hash in the transcript
are stable. The demo is a reproducible integration test with a scripted
conversation, not a separate implementation.

The demo also freezes the clock at `2026-11-04T09:00:00Z` and generates a
temporary signing key. Both are intentional for reproducibility. A real
deployment uses the system clock and persistent keys under the Scope Guard home
directory.

## What has not been done in this repository

Be precise about these:

1. **No live hosted Switch room is checked in.** The template and client are
   implemented against the documented protocol and the upstream reference. The
   demo does not require a Switch account. If the judges run only
   `scopeguard demo`, they exercise the whole decision path without the hosted
   transport.
2. **No production LLM prompt evaluation.** The five agents' instructions are in
   the template. The deterministic core does not call a model. Agent quality
   depends on the provider chosen in Console. The signature path does not.
3. **No HSM or key custody service.** The Ed25519 private keys are written to
   the Scope Guard home directory with best-effort `0600` permissions. That is
   appropriate for a laptop demo, not for a regulated issuer. Production would
   use a KMS or HSM and a published key registry.
4. **No multi-tenant identity.** The local UI is read-only and intended for
   `127.0.0.1`. It does not authenticate users. It should not be exposed to the
   internet as-is.
5. **SQLite, not Postgres.** Fine for a team and a demo; not for high write
   concurrency across regions.
6. **No legal enforceability.** A Scope Guard change order is evidence of
   internal authorization, client acceptance, and content integrity. It is not a
   contract and does not replace counsel.
7. **No email/Slack bridge beyond Switch.** Switch is the room. If a team mirrors
   a room to Slack or Teams through Switch's bridges, Scope Guard inherits that;
   it does not implement a second bridge.
8. **No automated evidence provenance.** A source is recorded as a named owner
   and a document reference. Scope Guard does not verify that the document is
   authentic or that the owner told the truth.

## Threat model

### In scope

| Threat | Control |
| --- | --- |
| A commitment is edited after approval | Signature bound to packet hash; change order rehashes the packet |
| A change order is edited after issue | Body hash + id derivation + Ed25519 |
| A client acceptance is transplanted | Acceptance signs this issuer's body hash, issuer name, and public key |
| History is rewritten | Append-only audit hash chain |
| An agent signs on a human's behalf | Only named humans in the approver map can satisfy the gate; agents have no signature path |
| A human signs a version they did not see | Phrase is bound to the current hash; stale signatures are ignored |
| Someone replays an old signature | The old hash does not match the current packet |
| A model invents a number | Decisions cite evidence ids and policy refs; the Red Team blocks unsupported claims |
| Delivery receives an unapproved promise | Handoff is generated only from the countersigned change order |
| Wrong person claims a role | Explicit role map, case-insensitive person match, one role per request |
| A tampered change order is presented as valid | `/verify` and `scopeguard verify` fail closed |

### Out of scope

- A compromised signing host can sign anything. Key custody is the boundary.
- A compromised Switch account can post as that agent. Switch auth is the
  boundary.
- A human can lie about evidence. Scope Guard records the source and the owner;
  it does not audit the source of truth.
- Collusion among all required approvers is indistinguishable from approval.
  Scope Guard makes consent explicit and attributable; it does not make people
  honest.
- Denial of service, rate limiting, and network hardening are not implemented in
  the local UI.
- A client key stored on the issuer's machine is a demo convenience. In a real
  deployment the client holds and uses its own key.

## Verification checks

`verify_receipt` runs ten issuer-side checks:

| Check | Proves |
| --- | --- |
| `algorithm` | The issuer uses Ed25519 |
| `body_hash` | The serialized body was not edited |
| `receipt_id` | The id is derived from the signed body fields |
| `signature` | The issuer signature matches the embedded key |
| `no_duplicate_signatures` | One signature per request and role |
| `line_items_signed` | Every line item has a recorded signature |
| `signature_binding` | Every phrase starts with `SIGN` and names this engagement |
| `every_line_item_priced` | Every amount is finite |
| `amounts_balance` | Subtotal, discount, and total agree |
| `unexpired` | Expiry is either enforced or explicitly not enforced |

Acceptance adds `acceptance_algorithm`, `acceptance_key_distinct`, and
`acceptance`. Pinned verification adds `pinned_key` and `pinned_client_key`.
A local ledger adds `packet_rehash`, `packet_version`,
`line_items_match_packet`, and `recorded`. A standalone verifier without the
packet adds `packet_present` instead of the three packet checks. The bundled
demo shows **19 passing checks** with both keys pinned and the ledger present.

## Reproducibility

The demo is designed to be byte-stable for the decision and artifact logic:

- clock frozen at `2026-11-04T09:00:00Z`;
- fixture order fixed;
- canonical JSON with sorted keys;
- SHA-256 and Ed25519;
- no network calls.

The change-order id is:

```
"co_" + SHA-256({engagement_id, packet_hash, issued_at, chain_prev})[:24]
```

It does not depend on the signature, so two runs with the same inputs produce
the same packet hashes, body hash, and change-order id. The signature differs
when the key differs, which is correct. `--reset` deletes the demo home, so a
clean run generates fresh keys and a fresh signature but the same public
artifacts.

To confirm:

```bash
scopeguard demo --reset --no-write
scopeguard demo --reset --no-write
```

The transcript's packet hashes, body hash, and change-order id are identical
across runs.

## Test strategy

304 tests, grouped by boundary:

| Area | What is asserted |
| --- | --- |
| Models | Validation, canonicalization, enum values |
| Policy | Loader, defaults, role matrix, invalid documents, margin math |
| Pricing | Every request type, every verdict, policy refs, evidence ids |
| Red Team | Every finding code, remediation, revision, stale invalidation |
| Gate | Missing roles, stale signatures, blocking findings, sealed status |
| Change order | Canonical JSON, id derivation, signature, countersignature, tamper, pinning, expiry |
| Store | Persistence, uniqueness, audit chain, tamper detection |
| Service | End-to-end orchestration, sync, seal, countersign, verify, handoff |
| Switch client | Paths, auth header, payloads, context flattening, HTTP errors |
| Clerk | Exact phrases, wrong roles, stale packets, seal, acceptance, handoff, idempotence |
| API | Every route, 404s, verify success/failure, audit |
| CLI | Each command, JSON output, error codes |
| Demo | Twelve steps, stable hashes, countersignature, failed tamper, intact audit |

Run:

```bash
.venv/Scripts/python -m pytest -q      # Windows
.venv/bin/python -m pytest -q          # macOS/Linux
ruff check .
ruff format --check .
mypy
```

## Honest one-line summary

**Scope Guard is a real deterministic pricing, approval, and two-party
change-order system with a real Switch client and template; the bundled demo
simulates only the chat transport so it can be reproduced offline.** That is
the claim. Nothing above overstates it.
