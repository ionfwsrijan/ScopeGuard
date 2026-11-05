# Policy

The policy is a small, reviewable YAML document. It is loaded into typed models
and evaluated by pure functions. Finance and legal can argue about the numbers
in a pull request instead of arguing about a model's output after the deal is
signed.

Default: [`policies/deal_policy_v1.yaml`](../policies/deal_policy_v1.yaml).

```yaml
id: deal-policy-v1
version: 1
currency: USD
max_evidence_age_days: 90

discount:
  max_pct: 20
  autonomy_pct: 15
  margin_floor_pct: 33

sla:
  max_uptime_pct: 99.95
  standard_uptime_pct: 99.9
  min_observed_uptime_pct: 99.9
  credit_required: true
  maintenance_language_required: true

feature:
  allow_beta: true
  allow_roadmap: true

security:
  allowed_statuses: [certified, in_progress]
  require_report_date_for_in_progress: true

approval_matrix:
  feature: [AE, ENG_LEAD]
  sla: [AE, ENG_LEAD]
  discount: [AE, VP_SALES]
  security: [AE, LEGAL]
```

## Thresholds

| Rule | Value | Meaning |
| --- | --- | --- |
| Discount ceiling | 20% | Above this, the Actuary narrows to 20% before challenge |
| Autonomy ceiling | 15% | Above this, the Examiner requires a documented CFO exception |
| Margin floor | 33% | Approved discount must not push gross margin below this |
| SLA maximum | 99.95% | Above this cannot be approved by this workflow |
| SLA standard | 99.9% | The standard contractual commitment |
| Minimum observed | 99.9% | Below this, reliability must improve before an SLA is sold |
| Evidence age | 90 days | Older evidence is stale |

## Margin arithmetic

With annual list price `P` and cost ratio `c`, revenue after discount `d` is
`P·(1−d)`, cost is `P·c`, so gross margin is:

```
margin(d) = 1 − c / (1 − d)
```

For Acme, `c = 0.55`:

| Discount | Margin | Verdict |
| --- | --- | --- |
| 0% | 45.00% | inside floor |
| 10% | 38.89% | inside floor |
| 15% | 35.29% | inside floor, above autonomy |
| 17.91% | 33.00% | exactly at floor |
| 20% | 31.25% | **below floor** |

Two independent controls apply to 20%:

1. **Autonomy** — 20% > 15%, so a CFO exception reference is required. None is
   attached, so the Examiner blocks.
2. **Margin** — 20% produces 31.25% < 33%, so the Examiner blocks again.

The Examiner proposes the smaller of the two corrections: the autonomy ceiling
(15%) is below the margin-derived maximum (17.91%), so the revision narrows to
15.00%.

## Claim-by-claim behaviour

### Feature

Reads the latest `roadmap` evidence for the claim.

| Evidence | Verdict | Approved language |
| --- | --- | --- |
| GA date ≤ requested | `approve` | GA committed by the requested date |
| Beta date ≤ requested, GA later | `narrow` | Beta by date; GA targeted quarter, **not committed** |
| Beta supported but `allow_beta: false` | `refuse` | No beta commitment permitted |
| No beta or GA inside the window | `escalate` | Product must supply a target |
| No roadmap evidence | `escalate` | Evidence required |

The narrowing is explicit: the approved text contains the word **beta** and the
phrase **not committed**, and the conditions forbid customer-facing GA language.
This is the difference between "we can probably do it" and a promise.

### SLA

Reads the latest `monitoring` evidence.

```
supportable = min(requested, policy.max_uptime_pct, policy.standard_uptime_pct)
```

| Evidence | Verdict |
| --- | --- |
| Observed < minimum observed | `escalate` — no SLA until reliability improves |
| Requested ≤ supportable and observed ≥ requested | `approve` |
| Otherwise | `narrow` to `supportable` |

Every SLA commitment carries service-credit and maintenance-window conditions
when policy requires them, plus a monthly measurement statement.

For Acme: requested 99.95%, standard 99.90%, observed 99.92%. Supportable is
99.90%, so the ask narrows to **99.90%**, not 99.95%.

### Discount

Reads the latest `pricing` evidence. Hard-narrows to the policy ceiling:

```
approved = min(requested, max_pct)
```

The Actuary does **not** apply the autonomy or margin floors — those belong to
the Examiner, whose job is to be independent. This separation is deliberate: if
the assessment already applied every control, the challenge would be decorative.

The approved value records `discount_pct`, `requested_pct`, `net_price_pct`, and
`annual_list_price`, so the receipt is financially legible.

### Security

Reads the latest `security_report` evidence.

| Evidence | Verdict |
| --- | --- |
| Status not in `allowed_statuses` | `escalate` |
| Status `in_progress`, report date present | `narrow` to exact status + date, "not certified" |
| Status `in_progress`, no report date and policy requires it | `narrow`/`escalate` with a missing-date condition |
| Status `certified` and requested "certified" | `approve` |
| Evidence says in progress but request says certified | `narrow` to "in progress; not certified" |

The approved language never upgrades evidence. "In progress" stays "in
progress", and the report date is stated.

## Approval matrix

| Claim type | Roles |
| --- | --- |
| `feature` | `AE`, `ENG_LEAD` |
| `sla` | `AE`, `ENG_LEAD` |
| `discount` | `AE`, `VP_SALES` |
| `security` | `AE`, `LEGAL` |

Role holders come from [`fixtures/acme/approvers.yaml`](../fixtures/acme/approvers.yaml):

```yaml
roles:
  AE: [Priya]
  VP_SALES: [Maya]
  ENG_LEAD: [Sam]
  LEGAL: [Alex]
```

A role can have several holders ("any one of these people may approve"), but one
person cannot satisfy two different roles. The gate counts distinct
`(claim, role)` pairs, and the receipt records exactly which person said which
phrase.

## Evidence admissibility

An evidence item must have:

- a stable id (`EV-*`) and a claim id,
- a kind from the allowed set (`roadmap`, `monitoring`, `security_report`,
  `pricing`, `contract`, `note`),
- a named source owned by an accountable team,
- an `observed_at` date,
- a summary,
- and a payload with the fields the relevant assessor reads.

Evidence older than `max_evidence_age_days`, or past `valid_until`, is stale.
The Examiner reports it and no commitment may rely on it.

Chat is not evidence. A model's summary is not evidence. Only a dated, sourced
record is.

## Changing the policy

1. Copy the YAML to a new file, for example `policies/deal_policy_v2.yaml`.
2. Bump `version`.
3. Pass `--policy policies/deal_policy_v2.yaml` to any command, or set the path
   in the service.
4. Re-assess. The packet hash changes because the decisions change, which
   invalidates prior approvals by construction.

Policy changes are not hot-reloaded into a sealed packet. A receipt records the
`policy_id` it was issued under, so an old receipt remains verifiable against
the policy it claims.

## What the policy is not

It is not a pricing engine, a legal contract, or a complete risk model. It is a
small set of guardrails that makes the common failure modes impossible to ship
silently. Extend it deliberately, with tests.
