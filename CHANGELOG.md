# Changelog

All notable changes to Vouch are recorded here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/), and this project follows
semantic versioning.

## [1.0.0] — 2026-11-05

First complete release, built for Flip the Switch 2026.

### Added

- Deterministic commitment-underwriting core: typed deal, claim, evidence,
  decision, packet, approval, and receipt models.
- Policy engine with discount ceilings, autonomy and margin floors, SLA limits,
  security-posture rules, and an approval matrix.
- Independent Examiner with eleven finding codes, policy-constrained revisions,
  and automatic invalidation of approvals bound to a superseded packet.
- Exact approval grammar: `APPROVE <DEAL> <CLAIM> AS <ROLE>`, validated against
  the sender's role and the current packet hash.
- Canonical JSON, SHA-256 body hashing, receipt-id derivation, and Ed25519
  signing with twelve verification checks.
- Append-only audit hash chain.
- SQLite ledger with deals, evidence, packets, approvals, receipts, audit, and
  room bindings.
- Orchestration service shared by the CLI, API, demo, and Switch agents.
- Switch HTTP client for `list_rooms`, `read_context`, `post_message`, and
  operation discovery, with Bearer auth and Switch env injection.
- Headless deterministic Clerk: record approvals, report the gate, seal, and
  hand off into a linked delivery room.
- Switch group template: five account-prefixed agents, two linked private
  rooms, kickoffs, and per-agent instructions.
- CLI with init, ingest, assess, challenge, revise, status, approve, sync, seal,
  verify, verify-audit, handoff, switch-check, export, demo, serve, and clerk.
- Read-only FastAPI UI and JSON API, including receipt verification.
- Deterministic eleven-step demo with a checked-in transcript.
- 280 tests, ruff and mypy clean, CI on Windows and Linux, Python 3.11–3.13.
- Documentation: architecture, Switch integration, policy, demo/video plan,
  validity, submission mapping, and a #HowISwitch draft.

### Known limitations

- The bundled demo simulates the chat transport; the Switch client and Clerk are
  real and tested.
- The signing key is local; production needs a KMS or HSM.
- The UI is read-only and intended for localhost.
- Receipts are evidence of internal authorization, not legal contracts.
