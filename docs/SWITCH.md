# Switch integration

Scope Guard is built around Switch's actual agent protocol, not a chat webhook.
This document covers what the code speaks, how to deploy the template, and how
to run the room by hand.

## What Scope Guard uses

Switch injects three environment variables into every managed agent:

| Variable | Meaning |
| --- | --- |
| `SWITCH_API_ENDPOINT` | API base URL, for example `https://api.switchagents.ai` |
| `SWITCH_API_TOKEN` | The agent's API key, sent as `Authorization: Bearer <key>` |
| `SWITCH_AGENT_ID` | The agent's id, used in every path |

`SWITCH_API_URL` is accepted as a fallback for the endpoint because it is the
host the WebSocket runtime uses. `SwitchConfig.from_env()` fails loudly, with a
hint, if any of the three required values are missing.

### Operations

| Call | Method and path |
| --- | --- |
| List operations | `GET /agents/{agent_id}/ops` |
| Call an operation | `POST /agents/{agent_id}/ops/{operation}` |
| Post a message | `POST /agents/{agent_id}/message` |

`read_context` is called with `{room_id, limit, since?}`. The optional
`X-Switch-Room-Id` header is sent when an operation is scoped to a room.
`list_rooms` is called with no arguments and returns either a list or
`{rooms: [...]}`. `post_message` takes `{room_id, content}` and does not require
a live WebSocket, which is why a headless Clerk is possible.

`messages_from_context` flattens Switch's thread grouping (`threads[].root` plus
`replies`) into one stable timeline of `SwitchMessage` objects. This is the only
place the room shape is interpreted; everything downstream sees a flat list
ordered by timestamp.

### Check the connection

```bash
export SWITCH_API_ENDPOINT=https://api.switchagents.ai
export SWITCH_API_TOKEN=...
export SWITCH_AGENT_ID=...
scopeguard switch-check
```

Output is a small JSON object:

```json
{
  "ok": true,
  "agent_id": "agt_...",
  "endpoint": "https://api.switchagents.ai",
  "operations": 24,
  "has_read_context": true,
  "has_post_message": true
}
```

The token is never printed. `SwitchConfig.masked_token` is available for logs.

## Deploying the room template

[`templates/scopeguard.template.yaml`](../templates/scopeguard.template.yaml) is
a standard Switch group template. It creates:

- **5 agents**, account-prefixed so multiple engagements can coexist:
  `{account}-scout`, `{account}-pricer`, `{account}-redteam`,
  `{account}-clerk`, `{account}-delivery`.
- **2 private rooms:** `scope-{account}` and `delivery-{account}`.
- **1 link:** `scope-{account} → delivery-{account}`, labelled
  *countersigned change orders*.
- **Kickoffs** that start the workflow in each room.
- **Instructions** that tell each agent what it may and may not do.

### Steps

1. Open Switch Console → **Templates** → **New template**.
2. Paste the YAML, or upload the file.
3. Choose **Create from template**.
4. Fill the form:

   | Param | Value |
   | --- | --- |
   | `account` | `northwind` (short, lowercase) |
   | `scopeguard_root` | Absolute path to this checkout on the agent machine |
   | `provider` | Claude Code, Codex, or OpenCode |
   | `location` | `local` or a configured host |
   | `directory` | Leave the default, or choose an agents directory |

5. Deploy. Switch creates the five agents, the two rooms, the link, and posts
   each room's kickoff.
6. Invite the humans to `scope-northwind`: Dana (account lead), Marco (delivery
   lead), Rae (finance), and Iris (legal). The template invites `{$creator}`
   automatically; add the rest through the room.
7. Add the delivery lead to `delivery-northwind`.

### Template parameters

| Param | Type | Notes |
| --- | --- | --- |
| `account` | string | Pattern `^[a-z0-9][a-z0-9-]{1,30}$`; used in names |
| `scopeguard_root` | directory | The checkout the agents run the CLI from |
| `provider` | provider | Defaults to the first installed of Claude, Codex, OpenCode |
| `location` | location | Defaults to `local` |
| `directory` | directory | Defaults to `{$agents_dir}/{agent}` |

The template uses only documented builtins: `{$agents_dir}`, `{$creator}`.
Human users are invited with `{$creator}` rather than fabricated names.

## The room script

### `scope-northwind` (change order)

**Purpose:** turn asks into evidence-backed, priced line items and obtain exact
human signatures bound to one packet hash before the client countersigns.

**Members:** Dana, Marco, Rae, Iris, plus Scout, Pricer, Red Team, Clerk.

**Agent powers:**

| Agent | May | May not |
| --- | --- | --- |
| Scout | Read engagement/evidence, post evidence tables, say "no evidence" | Invent a date, capability, rate, or contract term |
| Pricer | Run `scopeguard assess`, explain verdicts, amounts, and policy refs | Sign, soften a refusal, invent a number |
| Red Team | Run `scopeguard challenge`, block, propose remediation | Sign, seal, or issue a change order |
| Clerk | Record exact phrases, report the gate, seal, countersign, hand off | Decide a commercial question or invent a signature |

**Canonical sequence:**

```
Dana:    Northwind wants a mobile app, a two-week pull-in, 20% off, and
         unlimited liability. They need an answer today.
Scout:   [evidence table: beta 2026-12-01 / GA Q1 2027; 1 week of capacity at
         88%; 58% cost ratio; 2x liability cap; unlimited not insurable]
Pricer:  Packet v1 ed19403e… — MOBILE narrow 30,000; RUSH narrow 12,000;
         RATE approve 20% -9,600; TERMS narrow 6,000. Total 38,400.
Dana:    SIGN NORTHWIND-2026 RATE AS ACCOUNT_LEAD
Rae:     SIGN NORTHWIND-2026 RATE AS FINANCE
Red Team: BLOCK. rate_autonomy (20% > 12%) and margin_floor (27.50% < 30%).
Pricer:  Revised to v2 2d3f8963… — RATE narrow 12%. Earlier signatures are stale.
Dana:    SIGN NORTHWIND-2026 RATE AS ACCOUNT_LEAD
Rae:     SIGN NORTHWIND-2026 RATE AS FINANCE
Dana:    SIGN NORTHWIND-2026 MOBILE AS ACCOUNT_LEAD
Marco:   SIGN NORTHWIND-2026 MOBILE AS DELIVERY_LEAD
Dana:    SIGN NORTHWIND-2026 RUSH AS ACCOUNT_LEAD
Marco:   SIGN NORTHWIND-2026 RUSH AS DELIVERY_LEAD
Dana:    SIGN NORTHWIND-2026 TERMS AS ACCOUNT_LEAD
Iris:    SIGN NORTHWIND-2026 TERMS AS LEGAL
Dana:    SCOPE SEAL
Clerk:   Sealed. Change order co_5d7602a7… Total 42,240.00 USD.
         Jordan: sign the exported change order locally and paste the token.
Jordan:  SCOPE ACCEPT sgaccept1.<client-signed-token>
Clerk:   Accepted by Jordan Reyes. Change order is now two-party signed.
         Signed change order handed to delivery-northwind.
```

Anything that is not exactly `SIGN <ENGAGEMENT> <REQUEST> AS <ROLE>` is ignored.
A message from someone who does not hold the role is rejected. A message bound
to an old hash is stale and does not count. A bot's summary is not a signature.

### `delivery-northwind` (handoff)

**Purpose:** receive only countersigned line items, restate them as work, and
refuse anything that is not in the change order.

**Members:** delivery lead, Delivery agent, Clerk.

**What the Clerk posts:**

```markdown
**Scope Guard handoff: Northwind Health (northwind-2026)**

Change order: `co_5d7602a73cba86de6db28804`
Packet: v2 / `2d3f89633746db1f...`
Policy: `scope-policy-v1@v1`
Status: Countersigned by Jordan Reyes
Total: **42,240.00 USD** (subtotal 48,000.00, discount -5,760.00)

**Signed line items**
- **MOBILE** - beta by 2026-12-01; GA targeted Q1 2027, not included. (+30,000.00 USD)
  - Condition: Scope is the beta build only; GA is a separate change order.
  ...
- **RATE** - 12.00% discount on this change order. (-5,760.00 USD)
  ...

Only the line items above leave this room. Anything not listed is not approved.
Verify: `scopeguard verify --receipt co_5d7602a73cba86de6db28804`
```

The Delivery agent's instructions say the two-party-signed change order is the
only authority. A verbal promise, a draft packet, or "sales said it was fine" is
not authority.

## Running the headless Clerk

The deterministic Clerk is an ordinary Switch participant. It is useful when a
team wants Scope Guard's last mile to work without an LLM, and it is what the
tests exercise.

```bash
export SWITCH_API_ENDPOINT=...
export SWITCH_API_TOKEN=...
export SWITCH_AGENT_ID=...          # the {account}-clerk agent
scopeguard clerk \
  --engagement northwind-2026 \
  --room-id <scope-room-id> \
  --delivery-room-id <delivery-room-id> \
  --interval 3
```

One pass for a script or a cron job:

```bash
scopeguard clerk --engagement northwind-2026 --room-id <scope-room-id> \
  --delivery-room-id <delivery-room-id> --once
```

What a pass does:

1. `read_context` the scope room.
2. Parse only unseen messages.
3. For each exact signature: validate sender role, request, role, and packet
   hash; record valid ones; report rejected ones.
4. `SCOPE SEAL` triggers a gate check; if clear, seal and post the change-order
   id.
5. `SCOPE ACCEPT <token>` records the client's countersignature only when it
   verifies against the public key registered for the engagement.
6. If a delivery room is bound, post the handoff once, after acceptance.
7. Post a compact status with the packet hash, valid pairs, stale pairs, and
   pending pairs when asked for `SCOPE STATUS`.

It never invents a signature and never decides a commercial question.

The client's private key stays on the client machine. Before acceptance, the
client creates a key with `scopeguard client-keygen`; the issuer pins the public
key with `scopeguard register-client`. The client signs the exported receipt
with `scopeguard client-accept` and posts the resulting token. The Clerk checks
the signer name, registered key, receipt id, body hash, timestamp, and Ed25519
signature before recording it or handing anything to delivery.

## Local Switch stack (optional)

The upstream repository ships a Docker-based local stack. Scope Guard does not
need it for the demo, but a fully local end-to-end run is possible:

1. Clone the Switch repository and follow its local deployment instructions.
2. Point `SWITCH_API_ENDPOINT` at the local gateway.
3. Create an agent key and use it as `SWITCH_API_TOKEN`.
4. Deploy the template from Console, or call the template endpoint directly.

See the upstream `deploy/` and `docs/` directories for the exact compose file
and bootstrap steps; they change often, so this repository does not pin them.

## Protocol notes

- Auth is `Authorization: Bearer <api_key>`. A missing key is a `401`.
- A room the agent is not in returns `403`. Scope Guard surfaces the HTTP status
  and a hint rather than retrying.
- `read_context` pages history. Scope Guard asks for `limit=100`; the Clerk keeps
  a `seen` set so repeated passes do not double-record.
- `post_message` is direct and does not need the WebSocket. This is what makes
  the headless path deterministic.
- Message timestamps are milliseconds since the epoch.
- Switch's thread grouping is flattened; deleted or edited messages are not
  reconstructed, so the Clerk treats the timeline as append-only.

## Honest boundary

The bundled demo injects a `FakeSwitch` with the same two-method surface as the
real client. That is a transport substitution, not a workflow substitution: the
same `Clerk`, `ScopeService`, gate, change order, and policy code runs. The real
client is covered by tests that assert the exact paths, headers, payloads, and
error handling. See [VALIDITY.md](VALIDITY.md).
