# Demo and video plan

Scope Guard has two modes that share one implementation:

1. **Deterministic replay** — `scopeguard demo`, works offline, no credentials.
2. **Live Switch rooms** — the same flow in `scope-northwind` and
   `delivery-northwind` with named humans.

Record the live rooms if they are available. Use the replay for the receipt,
countersignature, and tamper segment, where stable hashes make the story
legible. Say which one is on screen. Never imply the replay is a live hosted
room.

## One-time setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\scopeguard.exe demo --reset
```

POSIX:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/scopeguard demo --reset
```

The demo freezes the clock at `2026-11-04`, creates keys in
`.scopeguard/demo/`, and writes `demo/transcript.md` plus `demo/receipt.json`.

For the live segment:

1. Deploy
   [`templates/scopeguard.template.yaml`](../templates/scopeguard.template.yaml)
   with `account=northwind`, `scopeguard_root=<this checkout>`.
2. Invite Dana, Marco, Rae, and Iris to `scope-northwind`; invite the delivery
   lead and the client to `delivery-northwind` when the handoff is due.
3. Run `scopeguard switch-check` inside the Clerk agent's environment.
4. Keep the signature phrase visible in the room instructions:
   `SIGN <ENGAGEMENT> <REQUEST> AS <ROLE>`.

## Screen layout

| Region | Content |
| --- | --- |
| Left | Switch room `scope-northwind` (or the replay terminal) |
| Right top | The ledger UI at `http://127.0.0.1:8787/` |
| Right bottom | The engagement page at `http://127.0.0.1:8787/engagements/northwind-2026` |

Do not show four terminals. Two panes plus one terminal is enough. Open the
signed change order at `/change-orders/co_…` when the receipt is the subject.

## Video: 3 minutes

### 0:00–0:15 — Hook

**On screen:** The signed change order, then the four client asks.

**Narration:**

> A client asks for a mobile app, a two-week acceleration, twenty percent off,
> and unlimited liability. Four small words. One fixed fee. Scope Guard turns
> each one into a price, an independent challenge, and a two-party change order
> before delivery hears about it.

### 0:15–0:40 — The pain is unpriced consent

**On screen:** The four asks, then the original `$38,400` total.

**Narration:**

> In a fixed-fee business, every unpriced yes is a margin leak. The usual
> workflow is a chat thread and hope: sales says yes, delivery finds out at
> standup, finance finds out at month-end, and nobody can prove what was
> actually approved. The problem is not dishonesty. It is that the thing a
> person approved changes after they approve it.

### 0:40–1:15 — The room does the work

**On screen:** Switch `scope-northwind`. Five agents, four named humans.

**Narration:**

> This is a Switch room, not a form. Scout attaches a dated source to every
> ask. Pricer runs a deterministic policy engine and produces typed
> commitments. Notice what it did: the mobile app narrows to a beta, because
> the estimate says GA is Q1 2027. The two-week acceleration narrows to one
> week, because the capacity plan supports one. Unlimited liability is refused
> and capped at two times fees. No model wrote those numbers. They come from
> evidence and a policy file finance can review.

**Show:** Packet v1 `ed19403e…`, four verdicts, and the `$48,000` subtotal.

### 1:15–1:45 — The premature signature and the block

**On screen:** Dana and Rae sign the 20% rate on packet v1. The Red Team blocks.

**Narration:**

> Dana and Rae sign the v1 rate. This is the dangerous moment, and it is
> deliberately allowed. The Red Team is a separate agent whose only job is to
> assume the packet is wrong. It recomputes the economics: 20% is above the 12%
> autonomy ceiling, and at a 58% cost ratio it leaves 27.50% gross margin,
> below the 30% floor. It blocks. The packet cannot be sealed.

### 1:45–2:15 — Revision makes consent stale

**On screen:** Packet v2 `2d3f8963…`. The two earlier signatures turn stale.
Then eight exact signatures arrive.

**Narration:**

> The revision narrows the discount to 12%. New packet, new hash, and the total
> rises from `$38,400` to `$42,240`. That is `$3,840` of margin protected, the
> Red Team's value stated in money. Look at the two earlier signatures: still in
> the ledger for audit, but stale, because they are bound to the old hash. They
> cannot satisfy the gate. Re-signing is the control working, not friction.

### 2:15–2:40 — Two-party seal, then verify

**On screen:** The gate clears, Clerk seals, Jordan Reyes countersigns, then a
tampered copy fails.

**Narration:**

> Eight signatures from four named humans bind to one hash. The gate clears and
> Clerk issues an Ed25519-signed change order. The client countersigns the
> issuer's body hash. A one-party receipt is a promise; a two-party change
> order is an agreement. Now verification: change one character and the body
> hash, the id, and the signature all fail. The audit chain is separate and also
> hashes every event, so history cannot be quietly rewritten.

### 2:40–3:00 — Handoff and close

**On screen:** `delivery-northwind` receives only the signed line items.

**Narration:**

> Only the countersigned line items cross the link into delivery. Notice what is
> not there: no GA date, no two-week acceleration, no 20% discount, no unlimited
> liability. Delivery cannot inherit a promise that was never signed. Scope
> Guard is a deterministic core with a conversational shell: five Switch agents,
> two linked rooms, exact consent, and a change order anyone can verify offline.

## Exact live-room script

Post these in `scope-northwind` in order. The Clerk records only exact phrases
from named role holders.

```
Dana      Northwind wants a mobile app, a two-week pull-in, 20% off, and
          unlimited liability. They need an answer today.
Scout     Four requests, four dated sources. Mobile is beta, not GA. One week
          of acceleration is supportable. Unlimited liability is not insurable.
Pricer    Packet v1 ed19403e… priced at 48,000 USD with a 20% discount. The
          Red Team still has to sign off on the margin.
Dana      SIGN NORTHWIND-2026 RATE AS ACCOUNT_LEAD
Rae       SIGN NORTHWIND-2026 RATE AS FINANCE
Red Team  BLOCK: rate_autonomy and margin_floor. 20% leaves 27.50% margin.
          v1 cannot be sealed.
Pricer    Revised to v2 2d3f8963… — RATE narrow 12%. Earlier signatures are
          stale.
Dana      SIGN NORTHWIND-2026 RATE AS ACCOUNT_LEAD
Rae       SIGN NORTHWIND-2026 RATE AS FINANCE
Dana      SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD
Marco     SIGN NORTHWIND-2026 MOBILE AS DELIVERY_LEAD
Dana      SIGN NORTHWIND-2026 RUSH AS ACCOUNT_LEAD
Marco     SIGN NORTHWIND-2026 RUSH AS DELIVERY_LEAD
Dana      SIGN NORTHWIND-2026 TERMS AS ACCOUNT_LEAD
Iris      SIGN NORTHWIND-2026 TERMS AS LEGAL
Dana      SCOPE SEAL
Clerk     Sealed. Change order co_5d7602a7… Total 42,240.00 USD.
          Client, reply `SCOPE ACCEPT <your name>` to countersign.
Jordan    SCOPE ACCEPT Jordan Reyes
Clerk     Accepted by Jordan Reyes. Change order is now two-party signed.
          Signed change order handed to delivery-northwind.
```

Anything that is not exactly `SIGN <ENGAGEMENT> <REQUEST> AS <ROLE>` is
ignored. A message from someone who does not hold the role is rejected. A
message bound to an old hash is stale and does not count. A bot's summary is not
a signature.

The same sequence is in
[`fixtures/northwind/switch_messages.yaml`](../fixtures/northwind/switch_messages.yaml)
and is replayed by `scopeguard demo`. The client acceptance is recorded by the
demo and the headless Clerk through the same `countersign` path.

## Fallback: record the deterministic replay

If the live rooms cannot be shown, record the terminal. It is the same workflow,
and the hashes are stable:

```bash
scopeguard demo --reset
```

Terminal beats to pause on:

| Beat | What to point at |
| --- | --- |
| Packet v1 hash | `ed19403eb4b9d664…` |
| Premature signatures | two valid signatures on v1 |
| Red Team block | `rate_autonomy`, `margin_floor` |
| Revision | new hash `2d3f89633746db1f…` |
| Stale signatures | two rows marked stale |
| Gate | 8 valid, 2 stale, 0 blocking findings |
| Total | `$38,400` → `$42,240`; `$3,840` margin protected |
| Change order | `co_5d7602a73cba86de6db28804` |
| Countersignature | Jordan Reyes over the issuer's body hash |
| Verify | 19 checks with pinned keys, all PASS |
| Tamper | `body_hash` and `amounts_balance` fail |
| Handoff | only four signed line items |
| Audit | 27 events, chain intact |

## Screenshots

| File | Route |
| --- | --- |
| `demo/images/01-room.png` | `/` — ledger with the countersigned change order |
| `demo/images/02-case.png` | `/engagements/northwind-2026` — packet, signatures, findings, gate |
| `demo/images/03-receipt.png` | `/change-orders/co_…` — signed change order and verification |
| `demo/receipt.json` | A real signed change order that verifies standalone |

Regenerate them with Chrome headless after `scopeguard demo` and
`scopeguard serve`:

```powershell
.\.venv\Scripts\scopeguard.exe demo --reset
Start-Process -WindowStyle Hidden .\.venv\Scripts\scopeguard.exe `
  -ArgumentList '--root','.','--home','.scopeguard/demo','serve','--port','8787'
Start-Sleep -Seconds 2

$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$root = (Get-Location).Path
$receipt = (Get-Content demo\receipt.json | ConvertFrom-Json).body.receipt_id
& $chrome --headless=new --disable-gpu --no-first-run `
  --user-data-dir="$env:TEMP\chrome-scopeguard" --window-size=1440,1800 `
  --screenshot="$root\demo\images\01-room.png" "http://127.0.0.1:8787/"
& $chrome --headless=new --disable-gpu --no-first-run `
  --user-data-dir="$env:TEMP\chrome-scopeguard" --window-size=1440,1800 `
  --screenshot="$root\demo\images\02-case.png" `
  "http://127.0.0.1:8787/engagements/northwind-2026"
& $chrome --headless=new --disable-gpu --no-first-run `
  --user-data-dir="$env:TEMP\chrome-scopeguard" --window-size=1440,1800 `
  --screenshot="$root\demo\images\03-receipt.png" `
  "http://127.0.0.1:8787/change-orders/$receipt"
```

Stop the server when done. The screenshots are read-only views of the same
ledger; they do not contain a signature path.

## Editing notes

- Do not speed-ramp the Red Team block or the stale-signature moment. Those are
  the product.
- Show a real hash being identical in two places: the room message and the
  change order. It is the clearest proof that consent is bound to content.
- Say "deterministic replay" once, plainly. Judges reward honesty more than a
  seamless fiction.
- Keep the closing claim small and true: this is a procedural gate with
  cryptographic evidence, not artificial general counsel.
