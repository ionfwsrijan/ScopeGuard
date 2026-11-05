# Validity

This document exists because "it works in the demo" is not a claim about
production. It separates what is real, what is simulated, and what is
deliberately out of scope.

## What is real

| Component | Status | Evidence |
| --- | --- | --- |
| Policy engine | Real, deterministic | `src/vouch/assess.py`, 280 tests |
| Examiner and revisions | Real, deterministic | `src/vouch/challenge.py`, tests for every finding code |
| Approval phrase parsing | Real, strict | `src/vouch/receipt.py`, parser tests |
| Hash-bound approval invalidation | Real | `store.invalidate_approvals`, revision tests |
| Canonical receipt + Ed25519 | Real | `receipt.py`, tamper tests |
| Receipt verification (12 checks) | Real, offline | `service.verify`, API `/verify` |
| Audit hash chain | Real | `store.verify_audit`, tamper tests |
| SQLite ledger | Real | `store.py` |
| Switch HTTP client | Real | `switch/client.py`, mocked-transport tests |
| Headless Clerk | Real | `switch/clerk.py`, in-memory room tests |
| Switch group template | Real format, validated by hand | `templates/vouch.template.yaml` |
| CLI | Real | `cli.py`, CLI tests |
| FastAPI UI and JSON API | Real | `api/app.py`, route tests |
| Demo | Real flow, simulated transport | `demo.py` |

## What is simulated in the bundled demo

The demo substitutes **one thing**: the transport. `FakeSwitch` implements the
same two-method surface the Clerk uses (`read_context`, `post_message`) and
scripts human messages from a fixture. Everything downstream is the real code:

- the same `VouchService`;
- the same policy and evidence fixtures;
- the same assessment and challenge;
- the same approval parser and invalidation;
- the same gate;
- the same signing and verification;
- the same handoff text.

This is why the packet hashes and receipt id in the transcript are stable. The
demo is a reproducible integration test with a scripted conversation, not a
separate implementation.

The demo also freezes the clock at `2026-11-04` and generates a temporary signing
key. Both are intentional for reproducibility. A real deployment uses the
system clock and a persistent key under the Vouch home directory.

## What has not been done in this repository

Be precise about these:

1. **No live hosted Switch room is checked in.** The template and client are
   implemented against the documented protocol and the upstream reference. The
   demo does not require a Switch account. If the judges run only
   `vouch demo`, they exercise the whole decision path without the hosted
   transport.
2. **No production LLM prompt evaluation.** The five agents' instructions are in
   the template. The deterministic core does not call a model. Agent quality
   depends on the provider chosen in Console. The approval path does not.
3. **No HSM or key custody service.** The Ed25519 private key is written to the
   Vouch home directory with best-effort `0600` permissions. That is appropriate
   for a laptop demo, not for a regulated issuer. Production would use a KMS or
   HSM and a published key registry.
4. **No multi-tenant identity.** The local UI is read-only and intended for
   `127.0.0.1`. It does not authenticate users. It should not be exposed to the
   internet as-is.
5. **SQLite, not Postgres.** Fine for a team and a demo; not for high write
   concurrency across regions.
6. **No legal enforceability.** A Vouch receipt is evidence of internal
   authorization and content integrity. It is not a contract and does not
   replace counsel.
7. **No email/Slack bridge beyond Switch.** Switch is the room. If a team
   mirrors a room to Slack or Teams through Switch's bridges, Vouch inherits
   that; it does not implement a second bridge.

## Threat model

### In scope

| Threat | Control |
| --- | --- |
| A commitment is edited after approval | Approval bound to packet hash; receipt rehashes packet |
| A receipt is edited after issue | Body hash + id derivation + Ed25519 |
| History is rewritten | Append-only audit hash chain |
| An agent approves on a human's behalf | Only named humans in the approver map can satisfy the gate; agents have no approval path |
| A human approves a version they did not see | Phrase is bound to the current hash; stale approvals are ignored |
| Someone replays an old approval | Old hash does not match the current packet |
| A model invents a number | Decisions cite evidence ids and policy refs; Examiner blocks unsupported claims |
| Delivery receives an unapproved promise | Handoff is generated only from the sealed receipt |
| Wrong person claims a role | Explicit role map, case-insensitive person match, one role per pair |
| Tampered receipt is presented as valid | `/verify` and `vouch verify` fail closed |

### Out of scope

- A compromised signing host can sign anything. Key custody is the boundary.
- A compromised Switch account can post as that agent. Switch auth is the
  boundary.
- A human can lie about evidence. Vouch records the source and the owner; it
  does not audit the source of truth.
- Collusion among all required approvers is indistinguishable from approval.
  Vouch makes the consent explicit and attributable; it does not make people
  honest.
- Denial of service, rate limiting, and network hardening are not implemented in
  the local UI.

## Reproducibility

The demo is designed to be byte-stable for the packet and approval logic:

- clock frozen at `2026-11-04T09:00:00Z`;
- fixture order fixed;
- canonical JSON with sorted keys;
- SHA-256 and Ed25519;
- no network calls.

The receipt id depends on the signing key only indirectly: the id is derived
from the body hash, not the signature. Two runs with the same inputs produce the
same receipt id and body hash. The signature differs if the key differs, which
is correct.

To confirm:

```bash
vouch demo --reset --no-write
vouch demo --reset --no-write
```

The transcript's packet hashes and receipt id are identical. The signature is
identical too when the same key file is reused; `--reset` deletes the home, so
each clean run generates a fresh key and a fresh signature.

## Test strategy

280 tests, grouped by boundary:

| Area | What is asserted |
| --- | --- |
| Models | Validation, canonicalization, enum values |
| Policy | Loader, defaults, role matrix, invalid docs |
| Assessment | Every claim type, every verdict, policy refs, evidence ids |
| Challenge | Every finding code, remediation, revision, stale invalidation |
| Gate | Missing roles, stale approvals, blocking findings, sealed status |
| Receipt | Canonical JSON, id derivation, signature, tamper, pinning, expiry |
| Store | Persistence, uniqueness, audit chain, tamper detection |
| Service | End-to-end orchestration, sync, seal, verify, handoff |
| Switch client | Paths, auth header, payloads, context flattening, HTTP errors |
| Clerk | Exact phrases, wrong roles, stale packets, seal, handoff, idempotence |
| API | Every route, 404s, verify success/failure, audit |
| CLI | Each command, JSON output, error codes |
| Demo | Eleven steps, stable hashes, failed tamper, intact audit |

Run:

```bash
python -m pytest -q
ruff check src tests
mypy
```

## Honest one-line summary

**Vouch is a real deterministic approval-and-receipt system with a real Switch
client and template; the bundled demo simulates only the chat transport so it
can be reproduced offline.** That is the claim. Nothing above overstates it.
