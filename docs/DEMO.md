# Demo and video plan

The demo has two modes that share one implementation:

1. **Deterministic replay** — `vouch demo`, works offline, no credentials.
2. **Live Switch room** — the same flow in `deal-acme` with named humans.

Record the live room if it is available. Use the replay for the receipt and
tamper segment, where deterministic hashes make the story legible. Say which one
is on screen. Never imply the replay is a live hosted room.

## One-time setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\vouch demo --reset
```

For the live segment:

1. Deploy [`templates/vouch.template.yaml`](../templates/vouch.template.yaml)
   with `account=acme`, `vouch_root=<this checkout>`.
2. Invite Priya, Maya, Sam, and Alex to `deal-acme`; invite the delivery lead to
   `delivery-acme`.
3. Run `vouch switch-check` inside the Clerk agent's environment.
4. Keep the approval phrase visible in the room instructions.

## Screen layout

| Region | Content |
| --- | --- |
| Left | Switch room `deal-acme` (or the replay terminal) |
| Right top | The case UI at `http://127.0.0.1:8787/deals/acme-2026` |
| Right bottom | Terminal for `vouch verify` and `vouch handoff` |

Do not show four terminals. Two panes plus one terminal is enough.

## Video: 3 minutes

### 0:00–0:18 — Hook

**On screen:** A single sentence, then the receipt.

**Narration:**

> A sales team promises SSO by December, a 99.95% SLA, and 20% off. Three
> departments find out after the contract is signed. Vouch turns each promise
> into something a named human approved, and gives you a receipt that proves it.
> Every promise, vouched for.

### 0:18–0:40 — The problem is consent drift

**On screen:** A chat message: "Can we just say yes?" Then a diff showing a
commitment changing from 20% to 15% after approval.

**Narration:**

> The problem is not that people lie. It is that the thing they approved changes
> after they approve it. A discount moves, an SLA gets rounded up, "in progress"
> becomes "certified". Vouch binds every approval to the exact packet hash. If
> the promise changes, the approval is stale. That is a feature.

### 0:40–1:10 — The room does the work

**On screen:** Switch `deal-acme`. Four humans, four agents. Show Scout's
evidence table and Actuary's packet hash.

**Narration:**

> This is a Switch room. Scout gathers dated evidence. Actuary runs the
> deterministic policy engine and produces typed commitments with exact
> approved language. Notice what it did to the customer's asks: SSO is narrowed
> to beta, because the roadmap says beta in December and GA in Q1. The SLA is
> narrowed to 99.9, because observed uptime is 99.92. SOC 2 becomes "in
> progress, report expected January 15th, not certified." No model wrote those
> numbers. They come from evidence and a policy file finance can review.

**Show:** `vouch status --json` briefly, or the case UI's packet table.

### 1:10–1:40 — The premature approval and the block

**On screen:** Priya and Maya type their approvals on packet v1. Then Examiner
posts `BLOCK`. Then the revision.

**Narration:**

> Priya and Maya approve the 20% discount. This is the dangerous moment, and it
> is deliberately allowed. The Examiner is a separate agent whose job is to
> assume the packet is wrong. It recomputes the economics: 20% is above the 15%
> autonomy ceiling, and at a 55% cost ratio it leaves 31.25% margin, below the
> 33% floor. It blocks.
>
> The revision narrows the discount to 15%. New packet, new hash. Look at the
> approvals: both earlier approvals are now stale. They are kept for audit, but
> they cannot satisfy the gate. Re-approval is the control working.

**Show:** the two stale rows in the case UI. Hold for two seconds.

### 1:40–2:15 — Eight exact phrases, one hash

**On screen:** The approval messages arriving. Then `vouch seal`.

**Narration:**

> Now every required role approves the exact packet. The phrase is not "looks
> good". It is `APPROVE ACME-2026 DISC AS VP_SALES`, from a person who holds
> that role, bound to this hash. Wrong person, wrong claim, wrong version, or a
> casual "I approve" is ignored.
>
> The gate passes. Clerk seals the receipt with Ed25519. The receipt contains
> only approved commitments and their conditions.

### 2:15–2:40 — Tamper, then verify

**On screen:** `vouch verify --receipt latest` passes. Edit one character in the
JSON. Run it again. It fails. Then `vouch handoff`.

**Narration:**

> Verification is adversarial. Change one character in the receipt and the body
> hash, the id, and the signature all fail. The audit chain is separate and also
> hashes every event, so history cannot be quietly rewritten.
>
> Only the sealed commitments cross the link into delivery. Notice what is not
> there: no 20% discount, no 99.95% SLA, no GA date, no claim that SOC 2 is
> certified. Delivery cannot inherit a promise that was never approved.

### 2:40–3:00 — Close

**On screen:** The receipt with the verify command, then the repo.

**Narration:**

> Vouch is a deterministic core with a conversational shell. Five Switch agents,
> two linked rooms, exact consent, and a receipt anyone can verify offline.
> Every promise, vouched for.

## Exact live-room script

Post these in `deal-acme` in order. The Clerk records only the exact phrases.

```
Priya   Acme will not sign without SSO, a 99.95% SLA, SOC 2 Type II, and 20% off. Can we just say yes?
Scout   (posts the evidence table; no approval)
Actuary (posts packet v1 and the hash)
Priya   APPROVE ACME-2026 DISC AS AE
Maya    APPROVE ACME-2026 DISC AS VP_SALES
Examiner BLOCK: discount_autonomy and margin_floor. v1 cannot be sealed.
Actuary Revised to v2. Earlier approvals are stale.
Priya   APPROVE ACME-2026 DISC AS AE
Maya    APPROVE ACME-2026 DISC AS VP_SALES
Sam     APPROVE ACME-2026 SSO AS ENG_LEAD
Sam     APPROVE ACME-2026 SLA AS ENG_LEAD
Alex    APPROVE ACME-2026 SOC2 AS LEGAL
Priya   APPROVE ACME-2026 SSO AS AE
Priya   APPROVE ACME-2026 SLA AS AE
Priya   APPROVE ACME-2026 SOC2 AS AE
Maya    VOUCH SEAL
```

The same sequence is in
[`fixtures/acme/switch_messages.yaml`](../fixtures/acme/switch_messages.yaml) and
is replayed by `vouch demo`.

## Fallback: record the deterministic replay

If the live room cannot be shown, record the terminal. It is the same workflow,
and the hashes are stable:

```bash
vouch demo --reset
```

Terminal beats to pause on:

| Beat | What to point at |
| --- | --- |
| Packet v1 hash | `c232e989bc464648…` |
| Premature approvals | two valid approvals on v1 |
| Examiner block | `discount_autonomy`, `margin_floor` |
| Revision | new hash `0f8fe1ab6a766d8a…` |
| Stale approvals | two rows marked stale |
| Gate | 8 valid, 2 stale, 0 blocking findings |
| Receipt | `vch_…`, body hash, signature |
| Tamper | `signature` and `body_hash` fail |
| Handoff | only four approved commitments |
| Audit | 26 events, chain intact |

## Screenshots

| File | Route |
| --- | --- |
| `demo/images/01-room.png` | `/` — portfolio view, audit chain intact |
| `demo/images/02-case.png` | `/deals/acme-2026` — packet, approvals, findings, handoff |
| `demo/images/03-receipt.png` | `/receipts/<id>` — signed receipt and verification |

Regenerate them with Chrome headless after `vouch demo` and `vouch serve`:

```powershell
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$root = (Get-Location).Path
& $chrome --headless=new --disable-gpu --no-first-run `
  --user-data-dir="$env:TEMP\chrome-vouch" --window-size=1440,1800 `
  --screenshot="$root\demo\images\01-room.png" "http://127.0.0.1:8787/"
```

## Editing notes

- Do not speed-ramp the Examiner block or the stale-approval moment. Those are
  the product.
- Show a real hash being identical in two places (room message and receipt). It
  is the clearest proof that consent is bound to content.
- Say "deterministic replay" once, plainly. Judges reward honesty more than a
  seamless fiction.
- Keep the closing claim small and true: this is a procedural gate, not
  artificial general counsel.
