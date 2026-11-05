# How I used Switch to stop sales promises from becoming surprises

*Draft for the Flip the Switch blog / #HowISwitch.*

A few months ago I watched a deal almost die over one sentence.

The customer had been told, in a chat, that SSO would be generally available by
the end of the year. Engineering had said beta in December and GA in Q1. Finance
had approved a discount that someone later rounded up by five points. Legal had
been told "SOC 2 certified" while the report was still in progress. Nobody was
lying. Every person had approved something, and then the something changed.

That is the whole problem. Not dishonesty — **consent drift**. The thing a human
approved is not the thing that shipped, and there is no artifact that proves the
difference.

I built Vouch for the Flip the Switch hackathon to fix that, and Switch is the
reason it works.

## Why a room, not a workflow

The obvious version of this product is a form: fill in the promise, click
approve, get a PDF. I have built that before. It fails for a social reason. The
conversation happens in chat, and the form is somewhere else, so the form becomes
a tax people route around.

Switch gave me a better primitive: a room where humans and agents are actual
participants, with instructions, and rooms can be linked. So Vouch is two rooms,
not a form.

`deal-acme` is the underwriting room. Four humans — an account executive, a VP
of sales, an engineering lead, and legal — work with four agents:

- **Scout** gathers dated, sourced evidence. It is not allowed to invent a date,
  a certification, or a number. If a claim has no source, it says "no evidence"
  and stops.
- **Actuary** runs a deterministic policy engine and explains the typed output:
  exact approved language, conditions, policy references, evidence ids, and the
  roles that must approve.
- **Examiner** assumes the packet is wrong. It recomputes the economics and
  blocks the packet when the promise is not supportable. It cannot approve
  anything.
- **Clerk** records exact human approvals, reports the gate, seals the receipt,
  and posts the handoff.

`delivery-acme` is linked to it, labelled *approved commitments*. Only two
participants: a delivery lead and the Delivery agent. Delivery never sees the
negotiation. It sees the sealed receipt and nothing else.

That split is the product. Sales cannot leak a draft into delivery, because the
only path across the link is the Clerk's sealed handoff.

## The agents are the interface, not the authority

Here is the design constraint I cared about most: **the approval path must not
depend on a model being right.**

So the core is Python. Policy is YAML, loaded into typed models. Assessment is a
pure function over a deal, a policy, and an evidence book. The Examiner is
another pure function. The gate is a set of named checks. The receipt is
canonical JSON, SHA-256, and Ed25519.

The agents call the same CLI a human would:

```
vouch assess
vouch challenge
vouch sync --room-id <room>
vouch seal
vouch handoff
```

If a model gets confused, it can mis-explain a packet. It cannot approve one, and
it cannot sign one. The worst outcome is a bad explanation that a human rejects.

That constraint made the Switch integration simpler, not harder. Each agent is
just a participant with a narrow job and a shell. Switch handles presence,
rooms, identity, and delivery. I did not have to build a chat product.

## The moment that makes it click

The demo deal is Acme: a $240k renewal at a 55% cost ratio. They ask for SSO by
December 31, a 99.95% SLA, 20% off, and SOC 2 Type II.

Scout finds four pieces of evidence. Actuary narrows all four:

- SSO becomes beta by December 15, GA targeted Q1 2027, **not committed**.
- The SLA becomes 99.90%, because the 90-day observed uptime is 99.92%.
- SOC 2 becomes "in progress, report expected January 15, **not certified**".
- The discount is approved by policy at 20%, with conditions.

Then Priya and Maya approve the 20% discount. This is the dangerous moment, and
I let it happen on purpose. Their approvals are bound to packet hash
`c232e989…`.

The Examiner blocks. 20% is above the 15% autonomy ceiling, and at a 55% cost
ratio it produces 31.25% gross margin, below the 33% floor. The revision narrows
the discount to 15%. That creates packet v2 with a new hash, `0f8fe1ab…`.

Now look at the approvals:

```
DISC / AE        Priya   stale   bound to c232e989…
DISC / VP_SALES  Maya    stale   bound to c232e989…
```

They are kept for audit. They cannot satisfy the gate. The team has to approve
the promise they are actually making. Re-approval is not friction; it is the
control working.

The phrase is exact:

```
APPROVE ACME-2026 DISC AS VP_SALES
```

"I approve" is chatter. A message from someone who does not hold the role is
rejected. An approval from an old hash is stale. The Clerk never invents one.

## The receipt

When the gate is clear, Clerk seals an Ed25519 receipt. It contains the packet
hash, the policy id, only approved commitments and their conditions, every
approval with the exact phrase and actor, the previous receipt hash for the
account, and an expiry.

Verification is adversarial. It recomputes everything from the serialized body:

```
PASS body_hash        PASS signature
PASS receipt_id       PASS no_duplicate_approvals
PASS commitments_approved
PASS approval_binding
PASS packet_rehash
PASS commitments_match_packet
...
```

Change one character and three checks fail. Edit the audit history and a
separate hash chain fails. The receipt is self-contained: the public key travels
with it, so anyone holding the file can verify integrity without trusting my
database. `--pinned-key` adds a check against the local key when you do trust it.

The handoff into delivery is generated only from the sealed receipt. When I show
the delivery room, the point is what is *not* there: no 20%, no 99.95%, no GA
date, no claim that SOC 2 is certified.

## What I would tell another builder

Three things I got from using Switch this way:

1. **Model the room, not the form.** The hardest part of approval software is
   that the conversation is where the truth lives. Put the workflow where the
   conversation already is.
2. **Give agents different jobs and different powers.** Scout, Actuary,
   Examiner, and Clerk disagree by construction. A single "deal assistant" would
   have agreed with everyone.
3. **Bind consent to content.** A signature on a mutable document is theatre.
   Hash the thing, put the hash in the approval, and make a change invalidate the
   approval.

The whole thing runs offline with one command, because I did not want the demo to
depend on a network or a model. Switch is the place the humans and agents work;
the deterministic core is what makes the output trustworthy. That separation is
the idea I am most proud of.

Every promise, vouched for.

---

*Built with Python, FastAPI, Pydantic, SQLite, Ed25519, and Switch's agent
protocol and room templates. Code, tests, the room template, and a full
transcript are in the repository. `#HowISwitch`*
