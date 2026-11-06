# How I used Switch to price every "one more thing"

*Draft for the Flip the Switch blog / #HowISwitch.*

Two weeks before launch, a client sent four sentences that almost killed a
fixed-fee engagement.

They wanted a native mobile app, generally available on the launch date. They
wanted the whole launch pulled forward by two weeks, guaranteed. They wanted 20%
off the change order. And they wanted unlimited liability plus a custom security
addendum.

None of those asks was unreasonable on its own. Together they were a different
business. The mobile app was a 320-hour GA build that could not land before Q1.
The capacity plan supported one week of acceleration, not two. At a 58% delivery
cost ratio, 20% off left 27.50% gross margin. Unlimited liability was not
insurable.

Everyone found out at a different time. Sales found out at signature. Delivery
found out at standup. Finance found out at month-end. Legal found out when the
contract was already a problem.

The usual workflow is a chat thread, a spreadsheet, and hope. The real failure
is not that someone lied. It is that **the thing a person approved changed after
they approved it**, and there was no artifact that proved the difference.

I built Scope Guard for the Flip the Switch hackathon to fix that. Switch is the
reason it works.

## Why a room, not a form

The obvious version of this product is a form: fill in the change, click
approve, get a PDF. I have built that before. It fails for a social reason. The
conversation happens in chat, and the form is somewhere else, so the form becomes
a tax people route around.

Switch gave me a better primitive: a room where humans and agents are actual
participants, with instructions, and rooms can be linked. So Scope Guard is two
linked rooms, not a form.

`scope-northwind` is the change-order room. Four named humans work there: Dana
(account lead), Marco (delivery lead), Rae (finance), and Iris (legal). Four
agents work with them:

- **Scout** attaches dated, sourced evidence to every ask. It is not allowed to
  invent a date, a capacity number, or a contract term. If a claim has no
  source, it says "no evidence" and stops.
- **Pricer** runs a deterministic pricing engine and explains the typed output:
  exact approved language, conditions, policy references, evidence ids, and the
  roles that must sign.
- **Red Team** assumes the packet is wrong. It recomputes the economics,
  attacks the language, and can block the packet. It cannot sign anything.
- **Clerk** records exact human signatures, reports the gate, seals the change
  order, records the client's countersignature, and posts the handoff.

`delivery-northwind` is linked to it, labelled *countersigned change orders*. It
has the Delivery agent and the Clerk. Delivery never sees the negotiation. It
sees the signed change order and nothing else.

That split is the product. Sales cannot leak a draft into delivery, because the
only path across the link is the Clerk's handoff after both parties sign.

## The agents are the interface, not the authority

The design constraint I cared about most: **the approval path must not depend on
a model being right.**

So the core is Python. Policy is YAML, loaded into typed models. Pricing is a
pure function over an engagement, a policy, and an evidence book. The Red Team is
another pure function. The gate is a set of named checks. The change order is
canonical JSON, SHA-256, and Ed25519.

The agents call the same CLI a human would:

```
scopeguard assess    --engagement northwind-2026
scopeguard challenge --engagement northwind-2026
scopeguard sync      --engagement northwind-2026 --room-id <room>
scopeguard seal      --engagement northwind-2026
scopeguard register-client --engagement northwind-2026 --client "Jordan Reyes" --public-key jordan.pub
scopeguard client-accept --receipt receipt.json --client "Jordan Reyes" --key jordan.pem
scopeguard countersign --engagement northwind-2026 --token "<token from client-accept>"
scopeguard handoff   --engagement northwind-2026 --post <delivery-room>
```

If a model gets confused, it can mis-explain a packet. It cannot sign one, and
it cannot seal one. The worst outcome is a bad explanation that a human rejects.

That constraint made the Switch integration simpler, not harder. Each agent is a
participant with a narrow job and a shell. Switch handles presence, rooms,
identity, and message delivery. I did not have to build a chat product.

The template in
[`templates/scopeguard.template.yaml`](../templates/scopeguard.template.yaml)
creates the five account-prefixed agents, the two private rooms, the link, the
kickoffs, and the per-agent instructions in one pass. The agents are ordinary
Switch participants, authenticated with the `SWITCH_API_ENDPOINT`,
`SWITCH_API_TOKEN`, and `SWITCH_AGENT_ID` values that Switch injects.

## The moment that makes it click

The demo engagement is Northwind Health: a `$120,000` fixed-fee patient
onboarding portal with launch on 2026-12-15 and a 58% delivery cost ratio.

Scout attaches four dated sources. Pricer narrows all four:

- The mobile app becomes **beta by 2026-12-01; GA targeted Q1 2027, not
  included**. The beta is 200 hours at `$150`; the GA build is 320 hours.
- The two-week pull-in becomes **one week**, because the capacity plan supports
  one week at 88% confidence. The rush premium is 25%.
- The 20% discount is approved by the headline policy, with conditions. It is
  not yet challenged.
- Unlimited liability is **refused** and capped at 2x fees, with legal review
  priced.

Packet v1 totals `$38,400` and has hash `ed19403eb4b9d664…`.

Then Dana and Rae sign the rate line. This is the dangerous moment, and I let it
happen on purpose. Their signatures bind to that hash.

The Red Team blocks:

```
RT-001  BLOCK  RATE  rate_autonomy
        20.00% is above the 12.00% autonomy ceiling.
RT-002  BLOCK  RATE  margin_floor
        20.00% produces 27.50% gross margin, below the 30.00% floor.
```

The policy-constrained revision narrows the discount to **12%**. That creates
packet v2 with hash `2d3f89633746db1f…`. The total rises from `$38,400` to
`$42,240`, protecting `$3,840` of margin. That is the Red Team's value stated in
money.

Now look at the two signatures:

```
RATE / ACCOUNT_LEAD   Dana   stale   bound to ed19403e…
RATE / FINANCE        Rae    stale   bound to ed19403e…
```

They are kept for audit. They cannot satisfy the gate. The team has to sign the
promise it is actually making. Re-signing is not friction; it is the control
working.

The phrase is exact:

```
SIGN NORTHWIND-2026 RATE AS FINANCE
```

"I approve" is chatter. A message from someone who does not hold the role is
rejected. A signature from an old hash is stale. A bot's summary is not a
signature. The Clerk never invents one.

Eight signatures from four named humans bind to the new hash. The gate checks
that the packet is challenged or sealed, that no blocking finding remains, that
every commitment is priced, and that every required request/role pair signed
*this* hash. Then Clerk seals.

## The two-party change order

A one-party receipt is a promise. A two-party change order is an agreement. So
the artifact has two signatures.

Scope Guard signs the canonical body hash with Ed25519 and embeds its public
key. The client — Jordan Reyes at Northwind — countersigns
`canonical_json({"body_hash", "issuer", "public_key"})` with a distinct key. If
the body changes, the hash changes and the acceptance fails. If the issuer's key
changes, the acceptance fails. The client is not signing a mutable PDF; the
client is signing *this* promise.

Verification is adversarial. It recomputes everything from the serialized body:

```
PASS algorithm              PASS body_hash
PASS receipt_id             PASS signature
PASS pinned_key             PASS no_duplicate_signatures
PASS line_items_signed      PASS signature_binding
PASS every_line_item_priced PASS amounts_balance
PASS unexpired              PASS acceptance_algorithm
PASS acceptance_key_distinct PASS acceptance
PASS pinned_client_key      PASS packet_rehash
PASS packet_version         PASS line_items_match_packet
PASS recorded
```

Change one character of an approved scope line and `body_hash` and
`amounts_balance` fail. Edit the audit history and a separate hash chain fails.
The change order is self-contained: the issuer's public key travels with it, so
anyone holding the file can verify integrity without trusting my database.

The handoff into delivery is generated only from the countersigned change
order. When I show the delivery room, the point is what is *not* there: no GA
date, no two-week acceleration, no 20% discount, no unlimited liability. Delivery
cannot accidentally inherit a promise that was never signed.

## Three things I would tell another builder

1. **Model the room, not the form.** The hardest part of approval software is
   that the conversation is where the truth lives. Put the workflow where the
   conversation already is.
2. **Give agents different jobs and different powers.** Scout, Pricer, Red Team,
   and Clerk disagree by construction. A single "deal assistant" would have
   agreed with everyone and signed nothing.
3. **Bind consent to content.** A signature on a mutable document is theatre.
   Hash the thing, put the hash in the signature, and make a change invalidate
   the signature.

The whole thing runs offline with one command, because I did not want the demo to
depend on a network or a model. The deterministic core is what makes the output
trustworthy; Switch is where the humans and agents actually work. That separation
is the idea I am most proud of.

The bundled demo substitutes only the chat transport with an in-memory client.
The same Clerk, service, gate, policy, and change-order code runs. The real
Switch client is covered by tests that assert the exact paths, headers, payloads,
and error handling. I would rather say that plainly than pretend a recording is a
hosted room.

Every "one more thing" gets a price.

---

*Built with Python, FastAPI, Pydantic, SQLite, Ed25519, and Switch's agent
protocol and room templates. Code, tests, the room template, and a full
transcript are in the repository. `#HowISwitch`*
