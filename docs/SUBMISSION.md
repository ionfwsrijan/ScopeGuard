# Submission

Flip the Switch · Nov 4–7, 2026

**Project:** Scope Guard
**Tagline:** Every "one more thing" gets a price.
**One line:** A change-order room for fixed-fee services, where evidence,
deterministic pricing, an independent Red Team, exact human signatures, and a
client countersignature turn scope creep into a two-party signed artifact.

## Elevator pitch

In a fixed-fee engagement, every unpriced "yes" is a margin leak. Scope Guard
turns each client ask into one of four typed requests, attaches dated evidence,
prices it with a deterministic policy, sends an independent Red Team to break
the answer, and requires exact signature phrases from named humans in a Switch
room. Any change to the promise makes earlier signatures stale. Only the
countersigned change order crosses the link into delivery.

## The judge path (5 minutes)

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"    # Windows: .venv\Scripts\python
.venv/bin/scopeguard serve --demo --reset --port 8787
```

Then open `http://127.0.0.1:8787/`.

What to look for, in order:

1. The transcript: two signatures on packet v1 are invalidated by a revision.
2. The engagement page: packet v2, 8 valid signatures, 2 stale, 0 blocking
   findings.
3. The change order: Ed25519 issuer signature, client-held countersignature, and 22
   passing verification checks with both keys pinned.
4. The tamper step in the demo: one character changed, verification fails.
5. The handoff: only four signed line items enter delivery; the refused and
   narrowed asks do not.

Verify the checked-in change order without the ledger, the demo, or any
credentials:

```bash
scopeguard --home .scopeguard/standalone verify \
  --receipt demo/receipt.json --require-acceptance
```

## Criteria mapping

### Use of Switch — 30%

| Judge question | Evidence |
| --- | --- |
| Is Switch central or decorative? | Two linked rooms are the workflow: `scope-northwind` negotiates; `delivery-northwind` receives only signed work. The product is the room. |
| Does it use the real protocol? | `src/scopeguard/switch/client.py`: `POST /agents/{id}/ops/{operation}`, `POST /agents/{id}/message`, Bearer auth, env injection. |
| Are agents doing real work? | Five agents with distinct instructions and powers in `templates/scopeguard.template.yaml`: Scout, Pricer, Red Team, Clerk, Delivery. |
| Is there human-agent collaboration? | Named humans sign exact phrases; agents gather, price, challenge, record, countersign, and hand off. Agents cannot sign. |
| Does it use rooms and links well? | A group template with two private rooms and a labelled link, *countersigned change orders*. |
| Does it work if the model is wrong? | The deterministic core owns pricing, the gate, and the change order. The signature path needs no LLM. |
| Is the integration honest? | `docs/VALIDITY.md` separates the real client from the simulated demo transport. |

### Technical Execution — 25%

| Judge question | Evidence |
| --- | --- |
| Is it built, not mocked? | 304 tests; CLI, API, UI, Switch client, headless Clerk, and demo. |
| Is the core correct? | Typed models; pure pricing; canonical JSON; SHA-256 hash chain; Ed25519 issuer and client signatures. |
| Does it handle failure? | Tamper tests, stale signatures, wrong roles, duplicates, expired evidence, audit tamper, HTTP errors, missing keys. |
| Is the code quality high? | `ruff check .` and `ruff format --check .` clean; `mypy` clean on 19 source files; CI on Windows and Linux, Python 3.11–3.13. |
| Is it reproducible? | Frozen clock, deterministic packets, one-command demo, transcript checked in. |
| Is the security model stated? | `docs/VALIDITY.md` threat model, in scope and out of scope, including key custody. |

### Creativity and Impact — 25%

| Judge question | Evidence |
| --- | --- |
| Is the idea novel? | Consent is bound to a content hash; a changed promise invalidates the signature. This is consent-drift control for fixed-fee scope creep. |
| Is there a real pain? | Every services agency loses margin to "small" additions, unstaffable accelerations, unapproved discounts, and unlimited liability. |
| Is the impact legible? | 20% off at a 58% cost ratio yields 27.50% margin, below a 30% floor; the revision protects `$3,840` in cash. |
| Is it more than a chatbot? | A two-party signed change order, an offline verifier, an audit chain, and a delivery handoff. |
| Does it generalize? | The policy is data. Request types are extensible. Any fixed-fee or milestone-based promise has the same failure mode. |

### Presentation — 20%

| Judge question | Evidence |
| --- | --- |
| Is the story clear? | README opens with the pain, the Northwind story, the flow, and the change order. |
| Is the demo tight? | A 3-minute script in `docs/DEMO.md` with exact beats, narration, and fallback hashes. |
| Are the visuals good? | Ledger, engagement, and change-order views with a consistent palette and checked-in screenshots. |
| Is it honest? | A dedicated validity document and a demo that labels the simulated transport. The client signs acceptance with a key kept on the client machine. |

## The money shot

Two signatures on packet v1 (`ed19403eb4b9d664…`):

```
RATE / ACCOUNT_LEAD:  Dana   valid   ed19403e…
RATE / FINANCE:       Rae    valid   ed19403e…
```

The Red Team blocks: `rate_autonomy` (20% > 12%) and `margin_floor`
(27.50% < 30%). The revision narrows the discount to 12% and creates packet v2
(`2d3f89633746db1f…`). The same two rows become:

```
RATE / ACCOUNT_LEAD:  Dana   stale   bound to ed19403e…
RATE / FINANCE:       Rae    stale   bound to ed19403e…
```

The total moves from `$38,400` to `$42,240`, protecting `$3,840` of margin.
That is the whole thesis in one screen: **the promise changed, so the consent
no longer applies.** Most approval workflows cannot show this because the
signature was never bound to the content.

## Submission copy

**Short description (≤ 200 chars):**

> Scope Guard turns client "one more things" into priced change orders:
> deterministic pricing, an independent Red Team, hash-bound human signatures,
> and a client countersignature before delivery.

**Long description:**

> Fixed-fee agencies lose margin to "small" client additions that were never
> priced, staffed, or approved. Scope Guard is a change-order room built on
> Switch. Five agents — Scout, Pricer, Red Team, Clerk, and Delivery — turn each
> ask into a typed request, attach dated evidence, apply a deterministic policy,
> and attack the result before any human signs it. Named humans sign an exact
> phrase bound to the packet hash, so any change to the promise invalidates
> earlier consent. The client countersigns the resulting Ed25519-signed,
> tamper-evident change order, and only signed line items cross the link into
> delivery. The deterministic core runs without an LLM in the approval path;
> the agents are the interface, not the authority.

**Built with:** Python, FastAPI, Pydantic v2, SQLite, Ed25519
(`cryptography`), Jinja2, Rich, pytest, ruff, mypy, and the Switch agent
protocol and room templates.

## Track selection

**Track 01 — Best Business Workflow ($2,000).**

Scope Guard is a practical workflow that helps people and agents get work done,
open to technical and nontechnical participants, with any combination of
people, agents, and rooms:

- **A real business workflow:** change-order underwriting for fixed-fee
  services, not an agent talking to itself.
- **People and agents together:** five agents do evidence, pricing, challenge,
  and procedure; four named humans make the commercial call; the client
  countersigns.
- **Technical and nontechnical roles:** engineers can inspect the policy and
  the receipt; sales, finance, delivery, and legal work entirely in the room.
- **Rooms are the product:** two linked rooms, an in-room signature grammar, and
  a handoff that crosses the link only after both parties sign.

**Track 02** (personal or creative workflow) does not fit: this is revenue
operations for a services business.

**Track 03** (Best Core Contribution) is an optional second play. The Switch
group template and integration notes could be offered upstream as an example,
but the main submission is Track 01.

## Assets checklist

- [x] `README.md` — pain, Northwind story, quick start, trust model, honesty
- [x] `demo/transcript.md` — full 12-step run with countersignature and tamper
- [x] `demo/receipt.json` — a real two-party change order, verifiable standalone
- [x] `demo/images/01-room.png` — redesigned dashboard
- [x] `demo/images/02-case.png` — engagement, packet, signatures, findings
- [x] `demo/images/03-receipt.png` — signed change order and verification
- [x] `templates/scopeguard.template.yaml` — five agents, two linked rooms
- [x] `fixtures/northwind/` — engagement, evidence, approvers, Switch messages
- [x] `policies/scope_policy_v1.yaml`
- [x] `docs/ARCHITECTURE.md`
- [x] `docs/SWITCH.md`
- [x] `docs/POLICY.md`
- [x] `docs/DEMO.md`
- [x] `docs/VALIDITY.md`
- [x] `docs/BLOG.md`
- [x] `LICENSE` (MIT)
- [x] `.github/workflows/ci.yml`
- [x] 304 tests, ruff clean, mypy clean

## Pre-submit checks

1. `scopeguard demo --reset` from a clean checkout succeeds.
2. `python -m pytest -q` is green.
3. `ruff check .`, `ruff format --check .`, and `mypy` are clean.
4. All three screenshots render and are committed.
5. The video says "deterministic replay" once, plainly.
6. No secrets, tokens, `.venv`, `.scopeguard`, or `.reference-switch` are
   committed.
7. The repo link is public or shared with the judges.
8. The submission copy names Switch, the five agents, and the two rooms.
9. The checked-in change order verifies with `--require-acceptance`.
10. The track is set to **Track 01 — Best Business Workflow**.

## Suggested repo settings

- Description: *Change-order room for fixed-fee services. Five Switch agents,
  exact human signatures, two-party signed receipts.*
- Topics: `switch`, `agents`, `hackathon`, `change-orders`, `scope-creep`,
  `governance`, `ed25519`, `fastapi`.
- Pin the README screenshots.
- Keep the demo transcript in the repo so judges can read the run without
  executing anything.

## Known limitations to state if asked

- The bundled demo simulates the chat transport; the Switch client and Clerk are
  real and tested.
- The signing keys are local; production needs a KMS/HSM, key rotation, and a
  published key registry.
- The UI is read-only and local-only; it is not an authenticated multi-tenant
  service.
- A Scope Guard change order is evidence of internal authorization, client
  acceptance, and content integrity. It is not a legal contract.
- Evidence provenance is recorded, not audited; Scope Guard trusts the named
  source owner.
