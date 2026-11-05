# Submission

Flip the Switch · Nov 4–7, 2026

**Project:** Vouch
**Tagline:** Every promise, vouched for.
**One line:** A commitment-underwriting room where sales, engineering, legal, and
five Switch agents turn a nervous yes into a signed, tamper-evident receipt.

## Elevator pitch

Sales promises are approved in chat and enforced nowhere. Vouch turns each ask
into an atomic claim, attaches dated evidence, applies a deterministic policy,
has an independent Examiner attack the packet, and requires exact approval
phrases from named humans in a Switch room. Any change to the promise makes
earlier approvals stale. Only sealed commitments cross the link into delivery.

## The judge path (5 minutes)

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"    # Windows: .venv\Scripts\python
.venv/bin/vouch demo --reset
.venv/bin/vouch --root . --home .vouch/demo serve --port 8787
```

Then open `http://127.0.0.1:8787/`.

What to look for, in order:

1. The transcript: two approvals are invalidated by a revision.
2. The case page: packet v2, 8 valid approvals, 2 stale, 0 blocking findings.
3. The receipt: Ed25519 signature and 12 verification checks.
4. The tamper step in the demo: one character changed, verification fails.
5. The handoff: only four approved commitments enter delivery.

To verify the checked-in receipt without running anything:

```bash
vouch --home .vouch/standalone verify --receipt demo/receipt.json
```

## Criteria mapping

### Use of Switch — 30%

| Judge question | Evidence |
| --- | --- |
| Is Switch central or decorative? | Two linked rooms are the workflow: underwriting and delivery. The product is the room. |
| Does it use the real protocol? | `src/vouch/switch/client.py`: `POST /agents/{id}/ops/{operation}`, `POST /agents/{id}/message`, Bearer auth, env injection. |
| Are agents doing real work? | Five agents with distinct instructions and powers in `templates/vouch.template.yaml`: Scout, Actuary, Examiner, Clerk, Delivery. |
| Is there human-agent collaboration? | Named humans approve exact phrases; agents gather, assess, challenge, record, and hand off. Agents cannot approve. |
| Does it use rooms/links well? | A group template with two private rooms and a labelled link, *approved commitments*. |
| Does it work if the model is wrong? | The deterministic core owns policy, gate, and receipt. The approval path needs no LLM. |
| Is the integration honest? | `docs/VALIDITY.md` separates the real client from the simulated demo transport. |

### Technical Execution — 25%

| Judge question | Evidence |
| --- | --- |
| Is it built, not mocked? | 280 tests; CLI, API, UI, Switch client, Clerk, demo. |
| Is the core correct? | Typed models; pure assessment; canonical JSON; SHA-256 hash chain; Ed25519. |
| Does it handle failure? | Tamper tests, stale approvals, wrong roles, duplicate approvals, expired evidence, audit tamper, HTTP errors. |
| Is the code quality high? | `ruff` clean, `mypy` clean on 19 files, CI on Windows and Linux, Python 3.11–3.13. |
| Is it reproducible? | Frozen clock, deterministic packets, one-command demo, transcript checked in. |
| Is the security model stated? | `docs/VALIDITY.md` threat model, in scope and out of scope. |

### Creativity and Impact — 25%

| Judge question | Evidence |
| --- | --- |
| Is the idea novel? | Approval is bound to a content hash; a changed promise invalidates consent. This is consent-drift control for sales promises. |
| Is there a real pain? | Discounts, SLAs, SSO dates, and security certifications are routinely promised before they are true. |
| Is the impact legible? | 20% discount at 55% cost ⇒ 31.25% margin, below a 33% floor. The Examiner shows the arithmetic. |
| Is it more than a chatbot? | A signed receipt, an offline verifier, an audit chain, and a delivery handoff. |
| Does it generalize? | The policy is data. The claim types are extensible. The pattern applies to any irreversible promise. |

### Presentation — 20%

| Judge question | Evidence |
| --- | --- |
| Is the story clear? | README opens with the problem, the flow, and the receipt. |
| Is the demo tight? | 3-minute script in `docs/DEMO.md`, with exact beats and narration. |
| Are the visuals good? | Case dashboard, receipt view, consistent palette, screenshots checked in. |
| Is it honest? | A dedicated validity document and a demo that labels the simulated transport. |

## The money shot

Two approvals on packet v1:

```
DISC / AE:        Priya   valid   c232e989…
DISC / VP_SALES:  Maya    valid   c232e989…
```

Examiner blocks. Revision creates packet v2 (`0f8fe1ab…`). The same two rows
become:

```
DISC / AE:        Priya   stale   bound to c232e989…
DISC / VP_SALES:  Maya    stale   bound to c232e989…
```

This is the whole thesis in one screen: **the promise changed, so the consent no
longer applies.** Most approval workflows cannot show this because the approval
was never bound to the content.

## Submission copy

**Short description (≤ 200 chars):**

> Vouch turns B2B sales promises into evidence-backed commitments, has an
> independent Examiner block bad ones, and requires exact human approvals bound
> to a packet hash before issuing a signed receipt.

**Long description:**

> Sales teams promise SSO dates, SLAs, discounts, and security certifications in
> chat, then discover after signing that engineering, finance, or legal never
> agreed. Vouch is a commitment-underwriting room built on Switch. Five agents —
> Scout, Actuary, Examiner, Clerk, and Delivery — turn each ask into a claim,
> attach dated evidence, apply a deterministic policy, and challenge the packet
> before any human signs it. Named humans approve with an exact phrase bound to
> the packet hash, so any change to the promise invalidates earlier consent. The
> result is an Ed25519-signed, tamper-evident Vouch Receipt that anyone can
> verify offline, and a delivery room that receives only sealed commitments.
> The deterministic core runs without an LLM in the approval path; the agents are
> the interface, not the authority.

**Built with:** Python, FastAPI, Pydantic v2, SQLite, Ed25519 (cryptography),
Jinja2, Rich, pytest, ruff, mypy, Switch agent protocol and room templates.

## Track selection

**Track 01 — Best Business Workflow ($2,000).**

The track asks for a practical workflow that helps people and AI agents get work
done, open to technical and nontechnical participants, with any combination of
people, agents, and rooms. Vouch is exactly that:

- **A real business workflow:** deal-desk underwriting for non-standard sales
  terms, not a demo of an agent talking to itself.
- **People and agents together:** five agents do evidence, assessment, challenge,
  and procedure; four named humans make the commercial call.
- **Technical and nontechnical roles:** engineers can inspect the deterministic
  policy and receipt; sales, legal, and finance can work entirely in the room.
- **Rooms are the product:** two linked rooms, an in-room approval grammar, and a
  handoff that crosses the link only when the gate is clear.

**Track 02** (personal or creative workflow) does not fit: this is revenue
operations, not everyday life or creative work.

**Track 03** (Best Core Contribution) is an optional second play. The Switch group
template and integration notes could be offered upstream as an
example/integration contribution, but the main submission is Track 01.

The four judging criteria are mapped separately below; they are not tracks.

## Assets checklist

- [x] `README.md` with problem, flow, screenshots, quick start, honesty section
- [x] `demo/transcript.md` — full 11-step run
- [x] `demo/receipt.json` — a real signed receipt, verifiable standalone
- [x] `demo/images/01-room.png`
- [x] `demo/images/02-case.png`
- [x] `demo/images/03-receipt.png`
- [x] `templates/vouch.template.yaml` — five agents, two linked rooms
- [x] `fixtures/acme/` — deal, evidence, approvers, Switch messages
- [x] `policies/deal_policy_v1.yaml`
- [x] `docs/ARCHITECTURE.md`
- [x] `docs/SWITCH.md`
- [x] `docs/POLICY.md`
- [x] `docs/DEMO.md`
- [x] `docs/VALIDITY.md`
- [x] `docs/BLOG.md`
- [x] `LICENSE` (MIT)
- [x] `.github/workflows/ci.yml`
- [x] 280 tests, ruff clean, mypy clean

## Pre-submit checks

1. `vouch demo --reset` from a clean checkout succeeds.
2. `python -m pytest -q` is green.
3. `ruff check src tests` and `mypy` are clean.
4. All three screenshots render and are committed.
5. The video says "deterministic replay" once, plainly.
6. No secrets, tokens, `.venv`, `.vouch`, or `.reference-switch` are committed.
7. The repo link is public or shared with the judges.
8. The submission copy names Switch and the five agents.

## Suggested repo settings

- Description: *Commitment underwriting for B2B sales promises. Five Switch
  agents, exact human consent, signed receipts.*
- Topics: `switch`, `agents`, `hackathon`, `sales`, `governance`, `ed25519`,
  `fastapi`.
- Pin the README screenshots.
- Keep the demo transcript in the repo so judges can read the run without
  executing anything.

## Known limitations to state if asked

- The bundled demo simulates the chat transport; the client and Clerk are real
  and tested.
- The signing key is local; production needs a KMS/HSM.
- The UI is read-only and local-only; it is not an authenticated multi-tenant
  service.
- Vouch is evidence of internal authorization, not a legal contract.
