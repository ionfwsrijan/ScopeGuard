# Policy

The policy is a small, reviewable YAML document. It is loaded into typed models
and evaluated by pure functions. Finance and legal can argue about the numbers
in a pull request instead of arguing about a model's output after the change
order is signed.

Default: [`policies/scope_policy_v1.yaml`](../policies/scope_policy_v1.yaml).

```yaml
id: scope-policy-v1
version: 1
currency: USD
max_evidence_age_days: 90

rate:
  max_pct: 20
  autonomy_pct: 12
  margin_floor_pct: 30
  quote_valid_days: 14

timeline:
  max_weeks_saved: 1
  rush_premium_pct: 25
  min_confidence_pct: 80
  require_capacity_evidence: true

new_work:
  allow_beta: true
  allow_ga_estimate: true

terms:
  max_liability_multiple: 2
  allow_unlimited_liability: false
  require_legal_review: true

approval_matrix:
  new_work: [ACCOUNT_LEAD, DELIVERY_LEAD]
  timeline: [ACCOUNT_LEAD, DELIVERY_LEAD]
  rate: [ACCOUNT_LEAD, FINANCE]
  terms: [ACCOUNT_LEAD, LEGAL]
```

## Thresholds

| Rule | Value | Meaning |
| --- | --- | --- |
| Discount ceiling | 20% | Above this, the Pricer narrows to 20% before the Red Team sees it |
| Autonomy ceiling | 12% | Above this, the Red Team requires a documented finance exception that this workflow does not have |
| Margin floor | 30% | The approved discount must not push gross margin below this |
| Quote validity | 14 days | The rate line expires with the change order |
| Acceleration cap | 1 week | The most delivery can compress without a new staffing plan |
| Rush premium | 25% | Applied to the incremental rush hours |
| Confidence floor | 80% | Below this, no acceleration can be promised |
| Liability cap | 2x fees | Unlimited liability is refused |
| Evidence age | 90 days | Older evidence is stale |

## Margin arithmetic

With a fixed fee `P` and a delivery cost ratio `c`, revenue after discount `d`
is `P·(1−d)` and cost is `P·c`, so gross margin is:

```
margin(d) = 1 − c / (1 − d)
```

For Northwind, `c = 0.58`:

| Discount | Margin | Verdict |
| --- | --- | --- |
| 0% | 42.00% | inside floor |
| 10% | 35.56% | inside floor |
| 12% | 34.09% | inside floor, exactly at autonomy |
| 17.14% | 30.00% | exactly at the margin floor |
| 20% | 27.50% | **below floor** |

Two independent controls apply to 20%:

1. **Autonomy** — 20% > 12%, so a finance exception reference is required. None
   is attached, so the Red Team blocks.
2. **Margin** — 20% produces 27.50% < 30%, so the Red Team blocks again.

The Red Team proposes the smaller of the two corrections: the autonomy ceiling
(12%) is below the margin-derived maximum (17.14%), so the revision narrows to
**12.00%**. That raises the change order from **38,400** to **42,240** and
protects **3,840** of margin.

`Policy.max_discount_for_margin(c)` solves the same equation for the largest
discount that still meets the floor. A cost ratio at or above 100% returns
`None`, because no price can reach the floor.

## Request-by-request behaviour

### `new_work`

Reads the latest `estimate` evidence for the request.

| Evidence | Verdict | Priced language |
| --- | --- | --- |
| GA date ≤ requested | `approve` | GA committed by the requested date, full estimate hours |
| Beta date ≤ requested, GA later | `narrow` | Beta by date; GA targeted quarter, **not included**; beta hours only |
| Beta supported but `allow_beta: false` | `refuse` | No beta commitment permitted |
| No beta or GA inside the window | `escalate` | Delivery must supply a supportable target |
| No estimate | `escalate` | Evidence required |

For Northwind, the estimate is 320 hours for GA in Q1 2027 and 200 hours for a
beta on 2026-12-01. The client asked for GA on 2026-12-15, so the line item is
**200 × $150 = $30,000** for the beta, with GA explicitly excluded.

The narrowing is explicit: the approved text contains the word **beta** and the
word **not included**, and the conditions forbid customer-facing GA language.
This is the difference between "we can probably do it" and a promise.

### `timeline`

Reads the latest `capacity` evidence.

```
allowed = min(requested_weeks, supported_weeks, policy.max_weeks_saved)
price   = rush_hours × rate × (1 + premium_pct / 100)
```

| Evidence | Verdict |
| --- | --- |
| Confidence < floor | `escalate` — no acceleration on this plan |
| No compressible weeks | `refuse` |
| `allowed < requested` | `narrow` to `allowed` |
| Otherwise | `approve` |

For Northwind, the client asks for two weeks. The plan supports one week at 88%
confidence, and policy caps it at one. The line item is one week of acceleration
for **64 × $150 × 1.25 = $12,000**. Every acceleration carries a condition that
excludes client-side delays, reserves capacity, and requires a named client
decision-maker.

### `rate`

Reads the latest `rate_card` evidence.

```
approved = min(requested, policy.rate.max_pct)
amount   = −(sum of positive line items) × approved / 100
```

The Pricer does **not** apply the autonomy or margin floors — those belong to the
Red Team, whose job is to be independent. This separation is deliberate: if the
Pricer already applied every control, the challenge would be decorative.

The approved value records `discount_pct`, `requested_pct`, `base_amount`,
`margin_pct`, and `net_total`, so the change order is financially legible.

For Northwind, the positive line items total **$48,000**. A 20% request produces
**−$9,600** and a 27.50% margin. The Pricer approves it; the Red Team blocks it;
the revision reprices at 12% for **−$5,760** and a 34.09% margin.

### `terms`

Reads the latest `contract` evidence.

| Evidence | Verdict |
| --- | --- |
| Unlimited liability requested, policy refuses it | `narrow` to the policy multiple, explicitly refusing unlimited |
| Requested multiple above the cap | `narrow` to the cap |
| Otherwise | `approve` |
| No contract | `escalate` |

The legal review is priced from `review_hours × rate`. For Northwind,
**40 × $150 = $6,000**. The approved language never upgrades evidence: unlimited
liability stays refused, and the security addendum is bounded to the scope in
the change order.

## Approval matrix

| Request type | Roles |
| --- | --- |
| `new_work` | `ACCOUNT_LEAD`, `DELIVERY_LEAD` |
| `timeline` | `ACCOUNT_LEAD`, `DELIVERY_LEAD` |
| `rate` | `ACCOUNT_LEAD`, `FINANCE` |
| `terms` | `ACCOUNT_LEAD`, `LEGAL` |

Role holders come from
[`fixtures/northwind/approvers.yaml`](../fixtures/northwind/approvers.yaml):

```yaml
engagement_id: northwind-2026
roles:
  ACCOUNT_LEAD: [Dana]
  DELIVERY_LEAD: [Marco]
  FINANCE: [Rae]
  LEGAL: [Iris]
```

A role can have several holders ("any one of these people may sign"), but one
person cannot satisfy two different roles for the same request. The gate counts
distinct `(request, role)` pairs, and the change order records exactly which
person said which phrase.

The four requests require eight signatures in total: two for each of `new_work`,
`timeline`, `rate`, and `terms`. Northwind's `ACCOUNT_LEAD` signs four times,
once per request. That is intentional: the account lead owns the client
relationship on every line, while finance, legal, and delivery own their own
domain.

## Evidence admissibility

An evidence item must have:

- a stable id (`EV-*`) and a request id,
- a kind from the allowed set: `estimate`, `capacity`, `rate_card`, `contract`,
  `sow`, or `note`,
- a named source owned by an accountable team,
- an `observed_at` date,
- a summary,
- and a payload with the fields the relevant assessor reads.

Evidence older than `max_evidence_age_days`, or past `valid_until`, is stale.
The Red Team reports it and no commitment may rely on it.

Chat is not evidence. A model's summary is not evidence. Only a dated, sourced
record is.

## Changing the policy

1. Copy the YAML to a new file, for example `policies/scope_policy_v2.yaml`.
2. Bump `version`.
3. Pass `--policy policies/scope_policy_v2.yaml` to any command, or set the path
   in the service.
4. Re-assess. The packet hash changes because the decisions change, which
   invalidates prior signatures by construction.

Policy changes are not hot-reloaded into a sealed packet. A change order records
the `policy_id` it was issued under, so an old change order remains verifiable
against the policy it claims.

## What the policy is not

It is not a pricing engine, a legal contract, or a complete risk model. It is a
small set of guardrails that makes the common failure modes impossible to ship
silently. Extend it deliberately, with tests.
