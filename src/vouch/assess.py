"""The Actuary: deterministic assessment of every ask against policy + evidence.

No model writes a commitment here. The output is a set of typed decisions with
policy references and evidence ids, which is exactly what the Examiner and the
approval gate consume.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from vouch.models import Claim, ClaimType, Deal, Decision, Evidence, EvidenceBook, Verdict
from vouch.policy import Policy


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _latest(book: EvidenceBook, claim_id: str, kind: str) -> Evidence | None:
    items = [item for item in book.for_claim(claim_id) if item.kind == kind]
    if not items:
        return None
    return max(items, key=lambda item: item.observed_at)


def _roles(policy: Policy, claim: Claim) -> list[str]:
    return policy.required_roles(claim.type)


def _no_evidence(claim: Claim, policy: Policy) -> Decision:
    return Decision(
        claim_id=claim.id,
        verdict=Verdict.ESCALATE,
        rationale="No admissible evidence was attached, so no commitment can be underwritten.",
        approved_text="No commitment. Evidence required before this ask returns to the room.",
        conditions=["Attach a dated source owned by the accountable team."],
        policy_refs=[f"policy.{claim.type}.evidence_required"],
        required_roles=[],
    )


def _assess_feature(claim: Claim, book: EvidenceBook, policy: Policy) -> Decision:
    item = _latest(book, claim.id, "roadmap")
    if item is None:
        return _no_evidence(claim, policy)
    payload = item.payload
    beta = _date(payload.get("beta_date"))
    ga = _date(payload.get("ga_date"))
    ga_quarter = str(
        payload.get("ga_quarter") or (f"Q{(ga.month - 1) // 3 + 1} {ga.year}" if ga else "")
    )
    requested_ga = _date(claim.requested_value.get("ga_date")) or claim.due
    refs = ["policy.feature.roadmap_required"]

    if ga is not None and requested_ga is not None and ga <= requested_ga:
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.APPROVE,
            rationale=f"Roadmap shows GA on {ga.isoformat()}, on or before the requested {requested_ga.isoformat()}.",
            approved_text=f"{claim.title} - GA committed by {requested_ga.isoformat()}.",
            approved_value={"kind": "ga", "ga_date": requested_ga.isoformat()},
            conditions=["Commitment is tied to the roadmap item named in the evidence."],
            policy_refs=refs,
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )

    if beta is not None and requested_ga is not None and beta <= requested_ga:
        if not policy.feature.allow_beta:
            return Decision(
                claim_id=claim.id,
                verdict=Verdict.REFUSE,
                rationale="Only a beta is supported by the roadmap, and beta commitments are disabled by policy.",
                approved_text="No beta commitment permitted.",
                policy_refs=[*refs, "policy.feature.allow_beta=false"],
                evidence_ids=[item.id],
                required_roles=_roles(policy, claim),
            )
        ga_label = ga_quarter or (ga.isoformat() if ga else "a later release")
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.NARROW,
            rationale=(
                f"Requested GA by {requested_ga.isoformat()}, but the roadmap shows beta on "
                f"{beta.isoformat()} and GA in {ga_label}. Only the beta is supportable."
            ),
            approved_text=(
                f"{claim.title.split(':')[0].strip()}: beta available by {beta.isoformat()}; "
                f"GA targeted {ga_label}, not committed."
            ),
            approved_value={
                "kind": "beta",
                "beta_date": beta.isoformat(),
                "ga_quarter": ga_quarter,
                "ga_committed": False,
            },
            conditions=[
                "Customer-facing language must say beta, not generally available.",
                f"No GA date commitment before {ga_label}.",
            ],
            policy_refs=[*refs, "policy.feature.allow_beta=true"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )

    if not policy.feature.allow_roadmap:
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.REFUSE,
            rationale="The roadmap does not support the requested date and roadmap-only commitments are disabled.",
            approved_text="No delivery commitment.",
            policy_refs=refs,
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )
    return Decision(
        claim_id=claim.id,
        verdict=Verdict.ESCALATE,
        rationale=(
            "The roadmap does not put either beta or GA inside the requested window; "
            "product must choose a new target before sales can commit."
        ),
        approved_text=f"No commitment. {claim.title} needs a product-owned target date.",
        conditions=["Product owner must supply a supportable date."],
        policy_refs=refs,
        evidence_ids=[item.id],
        required_roles=_roles(policy, claim),
    )


def _assess_sla(claim: Claim, book: EvidenceBook, policy: Policy) -> Decision:
    item = _latest(book, claim.id, "monitoring")
    if item is None:
        return _no_evidence(claim, policy)
    requested = float(claim.requested_value.get("uptime_pct", policy.sla.standard_uptime_pct))
    observed = float(item.payload.get("uptime_pct", 0.0))
    standard = policy.sla.standard_uptime_pct
    supportable = min(requested, policy.sla.max_uptime_pct, standard)
    refs = ["policy.sla.standard", "policy.sla.max"]
    conditions = []
    if policy.sla.credit_required:
        conditions.append("Service credits follow the standard SLA schedule.")
    if policy.sla.maintenance_language_required:
        conditions.append("Planned maintenance windows are excluded from the measurement.")
    conditions.append("Uptime is measured monthly on the production API.")

    if observed < policy.sla.min_observed_uptime_pct:
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.ESCALATE,
            rationale=(
                f"Observed {observed:.2f}% over the evidence window is below the policy floor "
                f"of {policy.sla.min_observed_uptime_pct:.2f}%. Reliability must improve before an SLA is sold."
            ),
            approved_text="No uptime commitment until reliability clears the policy floor.",
            conditions=["Engineering must attach a fresh monitoring window above the floor."],
            policy_refs=[*refs, "policy.sla.min_observed"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )

    if requested <= supportable and observed >= requested:
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.APPROVE,
            rationale=(
                f"Requested {requested:.2f}% is within the standard {standard:.2f}% commitment and "
                f"observed performance is {observed:.2f}%."
            ),
            approved_text=f"{requested:.2f}% monthly uptime SLA for the production API.",
            approved_value={"uptime_pct": requested},
            conditions=conditions,
            policy_refs=refs,
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )

    return Decision(
        claim_id=claim.id,
        verdict=Verdict.NARROW,
        rationale=(
            f"Requested {requested:.2f}%; the standard commitment is {standard:.2f}% and the "
            f"observed window is {observed:.2f}%. The requested number is not supportable."
        ),
        approved_text=f"{supportable:.2f}% monthly uptime SLA for the production API.",
        approved_value={
            "uptime_pct": supportable,
            "requested_pct": requested,
            "observed_pct": observed,
        },
        conditions=conditions,
        policy_refs=[*refs, "policy.sla.observed"],
        evidence_ids=[item.id],
        required_roles=_roles(policy, claim),
    )


def _assess_discount(claim: Claim, book: EvidenceBook, policy: Policy) -> Decision:
    item = _latest(book, claim.id, "pricing")
    requested = float(claim.requested_value.get("discount_pct", 0.0))
    cap = policy.discount.max_pct
    approved = min(requested, cap)
    net = round(100.0 * (1.0 - approved / 100.0), 4)
    if item is None and requested > 0:
        return _no_evidence(claim, policy)
    refs = ["policy.discount.max"]
    conditions = [
        "Standard payment terms; no retroactive discount.",
        "Quote valid for 30 days.",
    ]
    if approved < requested:
        rationale = (
            f"Requested {requested:.2f}% exceeds the policy ceiling of {cap:.2f}%; "
            f"the approvable maximum is {cap:.2f}%."
        )
        refs.append("policy.discount.over_cap")
    else:
        rationale = (
            f"{approved:.2f}% is inside the policy ceiling of {cap:.2f}%. "
            "VP Sales and Legal own the commercial decision."
        )
    return Decision(
        claim_id=claim.id,
        verdict=Verdict.APPROVE if approved == requested else Verdict.NARROW,
        rationale=rationale,
        approved_text=(
            f"{approved:.2f}% discount on the annual list price of "
            f"{claim.requested_value.get('annual_list_price', 0):,.0f} USD."
        ),
        approved_value={
            "discount_pct": approved,
            "requested_pct": requested,
            "net_price_pct": net,
            "annual_list_price": claim.requested_value.get("annual_list_price"),
        },
        conditions=conditions,
        policy_refs=refs,
        evidence_ids=[item.id] if item else [],
        required_roles=_roles(policy, claim),
    )


def _assess_security(claim: Claim, book: EvidenceBook, policy: Policy) -> Decision:
    item = _latest(book, claim.id, "security_report")
    if item is None:
        return _no_evidence(claim, policy)
    status = str(item.payload.get("status", "unknown")).lower()
    report_date = _date(item.payload.get("report_date"))
    requested_status = str(claim.requested_value.get("status", "certified")).lower()
    refs = ["policy.security.allowed_statuses"]
    if status not in policy.security.allowed_statuses:
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.ESCALATE,
            rationale=f"Evidence status {status!r} is not an admissible security posture.",
            approved_text="No security certification commitment.",
            policy_refs=refs,
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )

    if status == "certified":
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.APPROVE,
            rationale="The security report confirms certification.",
            approved_text=claim.requested_text,
            approved_value={
                "status": status,
                "report_date": report_date.isoformat() if report_date else None,
            },
            conditions=["Reference the report date in customer-facing material."],
            policy_refs=refs,
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )

    if (
        status == "in_progress"
        and policy.security.require_report_date_for_in_progress
        and report_date is None
    ):
        return Decision(
            claim_id=claim.id,
            verdict=Verdict.ESCALATE,
            rationale="The report is in progress but no expected report date is on file.",
            approved_text="No certification claim. Report date required.",
            policy_refs=[*refs, "policy.security.report_date_required"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, claim),
        )

    label = report_date.isoformat() if report_date else "date to be confirmed"
    return Decision(
        claim_id=claim.id,
        verdict=Verdict.NARROW,
        rationale=(
            f"Requested {requested_status!r}, but the evidence shows {status!r}"
            + (f" with a report expected {label}." if report_date else ".")
            + " Certification cannot be represented as complete."
        ),
        approved_text=(
            f"{claim.title.split(':')[0].strip()}: {status.replace('_', ' ')}; "
            f"report expected {label}. Not certified."
        ),
        approved_value={
            "status": status,
            "report_date": report_date.isoformat() if report_date else None,
        },
        conditions=[
            "Do not describe Vouch as certified until the report is issued.",
            "Update the commitment when the report date becomes final.",
        ],
        policy_refs=[*refs, "policy.security.status_is_not_certification"],
        evidence_ids=[item.id],
        required_roles=_roles(policy, claim),
    )


def assess_deal(
    *,
    deal: Deal,
    evidence: EvidenceBook,
    policy: Policy,
    as_of: date | None = None,
) -> list[Decision]:
    """Assess every claim in a stable order. Pure function, no I/O."""
    del as_of  # reserved for future time-dependent policy windows
    handlers = {
        ClaimType.FEATURE.value: _assess_feature,
        ClaimType.SLA.value: _assess_sla,
        ClaimType.DISCOUNT.value: _assess_discount,
        ClaimType.SECURITY.value: _assess_security,
    }
    decisions: list[Decision] = []
    for claim in deal.claims:
        decision = handlers[claim.type](claim, evidence, policy)
        # An approval never binds to an empty commitment.
        if decision.is_commitment and not decision.approved_text.strip():
            decision = decision.model_copy(
                update={
                    "verdict": Verdict.ESCALATE.value,
                    "approved_text": "No commitment. Assessment produced empty language.",
                }
            )
        decisions.append(decision)
    return decisions
