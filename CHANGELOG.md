# Changelog

All notable changes to Scope Guard are recorded here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/), and this project
follows semantic versioning.

## [1.0.0] — 2026-11-05

First complete release, built for Flip the Switch 2026.

### Added

- Deterministic change-order core: typed engagement, scope request, evidence,
  decision, packet, signature, change-order, and acceptance models.
- Four request types — `new_work`, `timeline`, `rate`, and `terms` — with
  evidence-based pricing in `assess.py`.
- Policy engine with discount ceilings, autonomy and gross-margin floors,
  acceleration caps, confidence floors, liability limits, legal-review rules,
  evidence freshness, and an approval matrix.
- Independent Red Team with 23 finding codes, policy-constrained remediation,
  packet revision, and automatic invalidation of signatures bound to a
  superseded packet.
- Exact signature grammar: `SIGN <ENGAGEMENT> <REQUEST> AS <ROLE>`, validated
  against the sender's role, the current packet hash, and duplicate pairs.
- Canonical JSON, SHA-256 body hashing, derived change-order ids, Ed25519 issuer
  signatures, and optional client countersignatures.
- Offline verification with up to 19 checks, including body integrity, id
  derivation, line-item signatures, amount balancing, expiry, packet rehashing,
  ledger presence, and distinct issuer/client keys.
- Append-only audit hash chain.
- SQLite ledger with engagements, evidence, packets, signatures, change orders,
  acceptance, audit events, and room bindings.
- Orchestration service shared by the CLI, API, demo, and headless Clerk.
- Switch HTTP client for `list_rooms`, `read_context`, `post_message`, and
  operation discovery, with Bearer auth and Switch environment injection.
- Headless deterministic Clerk: record exact signatures, report the gate, seal,
  record the client's acceptance, and hand off into a linked delivery room.
- Switch group template: five account-prefixed agents, two linked private rooms,
  kickoffs, and per-agent instructions.
- CLI with `init`, `ingest`, `assess`, `challenge`, `revise`, `status`, `sign`
  (alias `approve`), `sync`, `seal`, `countersign`, `verify`, `verify-audit`,
  `handoff`, `switch-check`, `export`, `demo`, `serve`, and `clerk`.
- Read-only FastAPI UI and JSON API, including change-order verification and
  audit status.
- Deterministic twelve-step demo with a checked-in transcript and a real
  two-party change order.
- Northwind fixture: a `$120,000` fixed-fee engagement, four asks, four pieces
  of dated evidence, four named approvers, and a Switch message transcript.
- 304 tests; `ruff check`, `ruff format --check`, and `mypy` clean; CI on Windows
  and Linux across Python 3.11–3.13.
- Documentation: architecture, Switch integration, policy, demo/video plan,
  validity, submission mapping, and a `#HowISwitch` draft.

### Changed

- Repositioned the product from a generic B2B deal desk to a fixed-fee
  change-order room, with scope creep as the primary pain.
- Pricing is deterministic and policy-driven; LLM agents explain and challenge
  the output but never sign, seal, or invent a number.
- The artifact is now a two-party change order: issuer signature over the body
  hash plus a client countersignature over the issuer's body hash, name, and
  public key.

### Known limitations

- The bundled demo simulates the chat transport; the Switch client and Clerk are
  real and tested against mocked transport.
- Signing keys are local files; production needs a KMS or HSM, key rotation, and
  a published key registry.
- The UI is read-only and intended for localhost.
- Change orders are evidence of internal authorization and client acceptance,
  not legal contracts.
