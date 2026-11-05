# Switch integration

Vouch is built around Switch's actual agent protocol, not a chat webhook. This
document covers what the code speaks, how to deploy the template, and how to run
the room by hand.

## What Vouch uses

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
vouch switch-check
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

[`templates/vouch.template.yaml`](../templates/vouch.template.yaml) is a
standard Switch group template. It creates:

- **5 agents**, account-prefixed so multiple deals can coexist:
  `{account}-scout`, `{account}-actuary`, `{account}-examiner`,
  `{account}-clerk`, `{account}-delivery`.
- **2 private rooms:** `deal-{account}` and `delivery-{account}`.
- **1 link:** `deal-{account} → delivery-{account}`, labelled
  *approved commitments*.
- **Kickoffs** that start the workflow in each room.
- **Instructions** that tell each agent what it may and may not do.

### Steps

1. Open Switch Console → **Templates** → **New template**.
2. Paste the YAML, or upload the file.
3. Choose **Create from template**.
4. Fill the form:

   | Param | Value |
   | --- | --- |
   | `account` | `acme` (short, lowercase) |
   | `vouch_root` | Absolute path to this checkout on the agent machine |
   | `provider` | Claude Code, Codex, or OpenCode |
   | `location` | `local` or a configured host |
   | `directory` | Leave the default, or choose an agents directory |

5. Deploy. Switch creates the agents, the two rooms, the link, and posts each
   room's kickoff.
6. Invite the humans to `deal-acme`: Priya (AE), Maya (VP Sales), Sam (Eng Lead),
   Alex (Legal). The template invites `{$creator}` automatically; add the rest
   through the room.
7. Add the delivery lead to `delivery-acme`.

### Template parameters

| Param | Type | Notes |
| --- | --- | --- |
| `account` | string | Pattern `^[a-z0-9][a-z0-9-]{1,30}$`; used in names |
| `vouch_root` | directory | The checkout the agents run the CLI from |
| `provider` | provider | Defaults to the first installed of Claude, Codex, OpenCode |
| `location` | location | Defaults to `local` |
| `directory` | directory | Defaults to `{$agents_dir}/{agent}` |

The template uses only documented builtins: `{$agents_dir}`, `{$creator}`.
Human users are invited with `{$creator}` rather than fabricated names.

## The room script

### `deal-acme` (underwriting)

**Purpose:** turn asks into evidence-backed commitments and obtain exact human
approvals bound to one packet hash.

**Members:** Priya, Maya, Sam, Alex, plus Scout, Actuary, Examiner, Clerk.

**Agent powers:**

| Agent | May | May not |
| --- | --- | --- |
| Scout | Read deal/evidence, post evidence tables, say "no evidence" | Invent a date, certification, or number |
| Actuary | Run `vouch assess`, explain verdicts and policy refs | Approve, soften a refusal, invent a number |
| Examiner | Run `vouch challenge`, block, propose remediation | Approve, seal, or issue a receipt |
| Clerk | Record exact phrases, report gate, seal, hand off | Decide a commercial question or invent approval |

**Canonical sequence:**

```
Priya:   Acme will not sign without SSO, 99.95% SLA, SOC 2 Type II, and 20% off.
Scout:   [evidence table: SSO beta 2026-12-15 / GA Q1 2027; 99.92% observed;
          SOC 2 in progress, report 2027-01-15; list $240k, cost 55%]
Actuary: Packet v1 c232e989… — SSO narrow, SLA narrow, DISC approve 20%, SOC2 narrow.
Priya:   APPROVE ACME-2026 DISC AS AE
Maya:    APPROVE ACME-2026 DISC AS VP_SALES
Examiner: BLOCK. discount_autonomy (20% > 15%) and margin_floor (31.25% < 33%).
Actuary: Revised to v2 0f8fe1ab… — DISC narrow 15%. Earlier approvals are stale.
Priya:   APPROVE ACME-2026 DISC AS AE
Maya:    APPROVE ACME-2026 DISC AS VP_SALES
Sam:     APPROVE ACME-2026 SSO AS ENG_LEAD
Sam:     APPROVE ACME-2026 SLA AS ENG_LEAD
Alex:    APPROVE ACME-2026 SOC2 AS LEGAL
Priya:   APPROVE ACME-2026 SSO AS AE
Priya:   APPROVE ACME-2026 SLA AS AE
Priya:   APPROVE ACME-2026 SOC2 AS AE
Maya:    VOUCH SEAL
Clerk:   Receipt vch_… issued. Verify: vouch verify --receipt vch_…
```

Anything that is not exactly `APPROVE <DEAL> <CLAIM> AS <ROLE>` is ignored. A
message from someone who does not hold the role is rejected. A message bound to
an old hash is stale and does not count.

### `delivery-acme` (handoff)

**Purpose:** receive only approved commitments, restate them as work, and refuse
anything that is not in the receipt.

**Members:** delivery lead, Delivery agent, Clerk.

**What the Clerk posts:**

```
**Vouch handoff: Acme Corp (acme-2026)**

Receipt: `vch_072ab073fdc4bcab3a226d9a`
Packet: v2 / `0f8fe1ab6a766d8a...`
Policy: `deal-policy-v1@v1`

**Approved commitments**
- **SSO** — beta by 2026-12-15; GA targeted Q1 2027, not committed.
  - Condition: Customer-facing language must say beta, not generally available.
  ...
Only the commitments above leave this room. Anything not listed is not approved.
Verify: `vouch verify --receipt vch_...`
```

The Delivery agent's instructions say the signed receipt is the only authority.
A verbal promise, a draft packet, or "sales said it was fine" is not authority.

## Running the headless Clerk

The deterministic Clerk is an ordinary Switch participant. It is useful when a
team wants Vouch's last mile to work without an LLM, and it is what the tests
exercise.

```bash
export SWITCH_API_ENDPOINT=...
export SWITCH_API_TOKEN=...
export SWITCH_AGENT_ID=...          # the {account}-clerk agent
vouch clerk \
  --deal acme-2026 \
  --room-id <deal-room-id> \
  --delivery-room-id <delivery-room-id> \
  --interval 3
```

One pass for a script or a cron job:

```bash
vouch clerk --deal acme-2026 --room-id <deal-room-id> \
  --delivery-room-id <delivery-room-id> --once
```

What a pass does:

1. `read_context` the deal room.
2. Parse only unseen messages.
3. For each exact approval: validate sender role, claim, role, packet hash;
   record valid ones; report rejected ones.
4. `VOUCH SEAL` triggers a gate check; if clear, seal and post the receipt id.
5. If a delivery room is bound, post the handoff once.
6. Post a compact status with the packet hash, valid pairs, stale pairs, and
   pending pairs.

It never invents an approval and never decides a commercial question.

## Local Switch stack (optional)

The upstream repository ships a Docker-based local stack. Vouch does not need
it for the demo, but a fully local end-to-end run is possible:

1. Clone the Switch repository and follow its local deployment instructions.
2. Point `SWITCH_API_ENDPOINT` at the local gateway.
3. Create an agent key and use it as `SWITCH_API_TOKEN`.
4. Deploy the template from Console, or call the template endpoint directly.

See the upstream `deploy/` and `docs/` directories for the exact compose file
and bootstrap steps; they change often, so this repository does not pin them.

## Protocol notes

- Auth is `Authorization: Bearer <api_key>`. A missing key is a `401`.
- A room the agent is not in returns `403`. Vouch surfaces the HTTP status and a
  hint rather than retrying.
- `read_context` pages history. Vouch asks for `limit=100`; the Clerk keeps a
  `seen` set so repeated passes do not double-record.
- `post_message` is direct and does not need the WebSocket. This is what makes
  the headless path deterministic.
- Message timestamps are milliseconds since the epoch.
- Switch's thread grouping is flattened; deleted or edited messages are not
  reconstructed, so the Clerk treats the timeline as append-only.

## Honest boundary

The bundled demo injects a `FakeSwitch` with the same two-method surface as the
real client. That is a transport substitution, not a workflow substitution: the
same `Clerk`, `VouchService`, gate, receipt, and policy code runs. The real
client is covered by tests that assert the exact paths, headers, payloads, and
error handling. See [VALIDITY.md](VALIDITY.md).
