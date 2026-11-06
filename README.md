# Scope Guard

**Every "one more thing" gets a price.**

Scope Guard is a change-order room for fixed-fee services businesses. A client
asks for "a small addition" two weeks before launch. Scope Guard turns that ask
into four typed requests, prices each one against evidence and policy, sends an
independent Red Team to break the answer, asks named humans to sign the exact
packet hash in Switch, and gets the client to countersign before anything
reaches delivery.

It is built for **Flip the Switch 2026, Track 01 - Best Business Workflow**.

<p align="center">
  <img src="demo/images/01-room.png" alt="Scope Guard ledger with a countersigned change order" width="100%">
</p>

---

## The pain

In a fixed-fee engagement, every unpriced "yes" is a margin leak:

- A native mobile app that was never in the statement of work.
- A two-week acceleration that the delivery team cannot staff.
- A 20% discount that finance never approved.
- Unlimited liability that the insurer will not cover.

The usual workflow is a Slack thread, a spreadsheet, and hope. The sales lead
says yes, delivery finds out at standup, finance finds out at month-end, and the
client remembers a promise that was never signed.

Scope Guard makes the trade explicit. The client gets a fast answer. The agency
gets a priced, defensible change order. Delivery gets only what both parties
signed.

| Ordinary AI workflow | Scope Guard |
| --- | --- |
| A model writes the commitment | A deterministic Pricer computes it; the model only explains |
| "Looks good to me" in chat | An exact phrase bound to a packet hash, from a named role holder |
| Approval survives edits | Any change to a commitment makes earlier signatures **stale** |
| One agent grades its own work | A separate Red Team attacks the packet and can block it |
| A PDF receipt | Canonical JSON, SHA-256 hash, Ed25519 signatures, offline verifier |
| Delivery hears the rumour | Delivery receives only the sealed, countersigned line items |

## The Northwind story

Northwind Health signed a $120,000 fixed-fee contract for a patient onboarding
portal. Launch is 2026-12-15. Two weeks before launch, the program director sends
four "small" additions:

| Ask | Type | What the client wants |
| --- | --- | --- |
| `MOBILE` | `new_work` | Native iOS and Android app, GA by launch |
| `RUSH` | `timeline` | Pull the entire launch forward by two weeks |
| `RATE` | `rate` | 20% off the change order |
| `TERMS` | `terms` | Unlimited liability and a custom security addendum |

The deterministic core produces:

| Ask | Verdict | Priced outcome | Amount |
| --- | --- | --- | --- |
| `MOBILE` | narrow | Beta by 2026-12-01; GA targeted Q1 2027, not included | +$30,000 |
| `RUSH` | narrow | One week of acceleration, not two; rush premium included | +$12,000 |
| `RATE` | approve | 20% off the change order | -$9,600 |
| `TERMS` | narrow | Unlimited liability refused; mutual cap at 2x fees | +$6,000 |

Packet v1 totals **$38,400**. Two humans sign the rate line before anyone checks
the margin. The Red Team then blocks the packet:

- 20% is above the **12% autonomy ceiling**.
- At a 58% delivery cost ratio, 20% off produces **27.50% gross margin**, below
  the **30% floor**.

The smallest policy-constrained revision narrows the discount to **12%**. That
creates a new packet hash, invalidates both earlier signatures, and raises the
total from **$38,400 to $42,240**. The protected margin is **$3,840**. That is
the Red Team's value, stated in money.

Eight exact signatures from four named humans bind to the new hash. The Clerk
seals a two-party change order. The client countersigns it. Verification passes.
A byte-level tamper check fails. Only the signed line items cross into the
linked delivery room. The audit chain closes with 28 events.

The full run is reproducible in one command and checked in at
[`demo/transcript.md`](demo/transcript.md).

## Quick start

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\scopeguard demo --reset
.\.venv\Scripts\scopeguard serve --demo --reset --port 8787
```

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/scopeguard demo --reset
.venv/bin/scopeguard serve --demo --reset --port 8787
```

Open `http://127.0.0.1:8787/`. The `serve --demo` option builds a populated
demo ledger before the dashboard starts, including on a clean checkout.

The demo is deterministic. The clock is frozen at `2026-11-04`, keys are
generated in a temporary home, and the same inputs always produce the same
packet hashes and totals. It writes [`demo/transcript.md`](demo/transcript.md)
and [`demo/receipt.json`](demo/receipt.json).

Verify the checked-in change order without the ledger, the demo, or any
credentials:

```bash
scopeguard --home .scopeguard/standalone verify --receipt demo/receipt.json
```

The verifier recomputes the canonical body hash, checks the Ed25519 issuer
signature, derives the receipt id, checks every line item has a signature,
balances the amounts, and verifies the client countersignature. It does not
trust this server's database.

## How Switch is used

Switch is not a chat skin over a webhook. It is the room where the work happens:

- **Two linked rooms.** `scope-northwind` is the negotiation;
  `delivery-northwind` receives only sealed commitments.
- **Five agents.** Scout gathers evidence. Pricer runs the deterministic pricing
  engine. Red Team independently challenges the packet. Clerk records exact
  human signatures, seals, and hands off. Delivery turns the signed line items
  into work.
- **Named humans.** Dana (account lead), Marco (delivery lead), Rae (finance),
  and Iris (legal) must each sign for the roles their request type requires.
- **A real protocol.** Approvals are exact phrases in the room:
  `SIGN NORTHWIND-2026 RATE AS FINANCE`. The Clerk records the sender, the role,
  the phrase, and the current packet hash. A changed packet invalidates every
  earlier signature.
- **A linked handoff.** The delivery room receives the receipt id and the signed
  line items, not a summary of the negotiation.
- **Independent client acceptance.** The client keeps its Ed25519 private key,
  registers its public key out of band, signs the exported change order locally,
  and posts a signed token. The issuer verifies the token without holding the
  client's private key.

See [`docs/SWITCH.md`](docs/SWITCH.md) for the API surface, template, and room
script. The template in
[`templates/scopeguard.template.yaml`](templates/scopeguard.template.yaml)
creates the agents, rooms, and link.

## What is in the box

| Area | What it does |
| --- | --- |
| Deterministic core | Policy, evidence, pricing, challenge, revision, hashing, signatures |
| Switch transport | HTTP client for rooms, context, messages, and operations |
| Headless Clerk | Records approvals from room messages, seals, countersigns, hands off |
| CLI | `init`, `ingest`, `assess`, `challenge`, `revise`, `sign`, `sync`, `seal`, `client-keygen`, `register-client`, `client-accept`, `countersign`, `verify`, `verify-audit`, `handoff`, `switch-check`, `export`, `demo`, `serve`, `clerk` |
| Web UI | Read-only ledger, engagement view, signed change order, verifier |
| JSON API | Engagement, packet, receipt, verification, audit, health |
| Tests | 304 tests, ruff and mypy clean, Python 3.11-3.13 |

## The trust model

A change order is an agreement only when three things are true:

1. **The pricing is reproducible.** Given the same engagement, evidence, and
   policy, the Pricer always produces the same packet.
2. **The approval is exact.** A named human holding a required role says the
   exact phrase. The signature binds to the packet hash, so a revised packet
   cannot reuse it.
3. **The artifact is self-verifying.** The receipt contains the canonical body,
   its SHA-256 hash, an Ed25519 signature, and an optional client
   countersignature. An offline verifier can check all of it.

The agents are a conversational interface. They do not approve, seal, or invent
numbers. The deterministic core is the source of truth.

See [`docs/VALIDITY.md`](docs/VALIDITY.md) for the threat model and the honest
boundaries of what the demo proves.

## Demo screenshots

| Redesigned dashboard | Engagement packet and approvals | Signed change order and verification |
| --- | --- | --- |
| ![Ledger](demo/images/01-room.png) | ![Engagement](demo/images/02-case.png) | ![Change order](demo/images/03-receipt.png) |

## Project layout

```
src/scopeguard/
  assess.py        deterministic pricing for four request types
  challenge.py     Red Team findings, revisions, and the human gate
  policy.py        typed policy and margin math
  receipt.py       canonical JSON, hashing, Ed25519, verification
  service.py       one orchestration layer for CLI, API, demo, and agents
  store.py         SQLite ledger and append-only audit chain
  switch/          Switch client, room protocol, and the deterministic Clerk
  api/             FastAPI UI and JSON API
fixtures/northwind/   engagement, evidence, approvers, room messages
policies/             scope_policy_v1.yaml
tests/                304 tests across the core, CLI, API, Clerk, and demo
```

## Quality gates

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m ruff format --check .
.venv/Scripts/python -m mypy
.venv/Scripts/scopeguard demo --reset --no-write
```

CI runs the same gates on Windows and Linux across Python 3.11, 3.12, and 3.13.

## Honest limitations

- The bundled demo uses an in-memory Switch transport (`FakeSwitch`) so it runs
  without credentials. The HTTP client and headless Clerk are real and tested
  against mocked transport; the room script is the same code path.
- Signing keys are local files. Production needs a KMS or HSM and a key
  rotation story.
- Receipts prove internal authorization and client acceptance. They are not a
  substitute for a lawyer's contract.
- The web UI is read-only and intended for localhost.

Built for Flip the Switch 2026. Track 01 - Best Business Workflow.
