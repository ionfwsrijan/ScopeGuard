"""The Pricer: deterministic pricing of every scope request against policy.

No model writes a price here. The output is a set of typed decisions with
policy references, evidence ids, and a signed amount, which is exactly what the
Red Team and the approval gate consume.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from scopeguard.models import (
    Decision,
    Engagement,
    Evidence,
    EvidenceBook,
    RequestType,
    ScopeRequest,
    Verdict,
)
from scopeguard.policy import Policy


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _latest(book: EvidenceBook, request_id: str, kind: str) -> Evidence | None:
    items = [item for item in book.for_request(request_id) if item.kind == kind]
    if not items:
        return None
    return max(items, key=lambda item: item.observed_at)


def _roles(policy: Policy, request: ScopeRequest) -> list[str]:
    return policy.required_roles(request.type)


def _quarter(value: date | None) -> str:
    if value is None:
        return "a later release"
    return f"Q{(value.month - 1) // 3 + 1} {value.year}"


def _no_evidence(request: ScopeRequest, policy: Policy) -> Decision:
    return Decision(
        request_id=request.id,
        verdict=Verdict.ESCALATE,
        rationale="No admissible evidence was attached, so no price or commitment can be signed.",
        priced_text="No change order line item. Evidence is required before this ask returns to the room.",
        amount=None,
        conditions=["Attach a dated estimate, capacity plan, rate card, or contract clause."],
        policy_refs=[f"policy.{request.type}.evidence_required"],
        required_roles=_roles(policy, request),
    )


def _assess_new_work(request: ScopeRequest, book: EvidenceBook, policy: Policy) -> Decision:
    item = _latest(book, request.id, "estimate")
    if item is None:
        return _no_evidence(request, policy)
    payload = item.payload
    hours = float(payload.get("hours", 0.0))
    beta_hours = float(payload.get("beta_hours", hours))
    rate = float(payload.get("rate", 0.0))
    beta = _date(payload.get("beta_date"))
    ga = _date(payload.get("ga_date"))
    ga_label = str(payload.get("ga_quarter") or _quarter(ga))
    requested_ga = _date(request.requested_value.get("ga_date")) or request.due
    refs = ["policy.new_work.estimate_required"]

    if ga is not None and requested_ga is not None and ga <= requested_ga:
        return Decision(
            request_id=request.id,
            verdict=Verdict.APPROVE,
            rationale=(
                f"The estimate supports general availability on {ga.isoformat()}, on or before "
                f"the requested {requested_ga.isoformat()}."
            ),
            priced_text=f"{request.title}: general availability by {requested_ga.isoformat()}.",
            amount=round(hours * rate, 2),
            approved_value={
                "kind": "ga",
                "ga_date": requested_ga.isoformat(),
                "hours": hours,
                "rate": rate,
            },
            conditions=["Scope is the item named in the estimate."],
            policy_refs=[*refs, "policy.new_work.ga_supported"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )

    if beta is not None and requested_ga is not None and beta <= requested_ga:
        if not policy.new_work.allow_beta:
            return Decision(
                request_id=request.id,
                verdict=Verdict.REFUSE,
                rationale="Only a beta is supported and beta-only change orders are disabled by policy.",
                priced_text="No new-work line item. Beta scope is not offered.",
                amount=None,
                policy_refs=[*refs, "policy.new_work.allow_beta=false"],
                evidence_ids=[item.id],
                required_roles=_roles(policy, request),
            )
        return Decision(
            request_id=request.id,
            verdict=Verdict.NARROW,
            rationale=(
                f"The client asked for GA by {requested_ga.isoformat()}, but the estimate shows "
                f"beta on {beta.isoformat()} and GA in {ga_label}. Only the beta is supportable."
            ),
            priced_text=(
                f"{request.title}: beta by {beta.isoformat()}; GA targeted {ga_label}, not included."
            ),
            amount=round(beta_hours * rate, 2),
            approved_value={
                "kind": "beta",
                "beta_date": beta.isoformat(),
                "ga_quarter": ga_label,
                "ga_committed": False,
                "hours": beta_hours,
                "rate": rate,
            },
            conditions=[
                "Scope is the beta build only; GA is a separate change order.",
                f"GA must not be represented as committed before {ga_label}.",
                "App-store review time is excluded from the beta date.",
            ],
            policy_refs=[*refs, "policy.new_work.allow_beta=true"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )

    if not policy.new_work.allow_ga_estimate:
        return Decision(
            request_id=request.id,
            verdict=Verdict.REFUSE,
            rationale="The estimate does not support the requested date and estimate-only work is disabled.",
            priced_text="No new-work line item.",
            amount=None,
            policy_refs=refs,
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )
    return Decision(
        request_id=request.id,
        verdict=Verdict.ESCALATE,
        rationale=(
            "The estimate puts neither beta nor GA inside the requested window; delivery must "
            "produce a new feasible date before the account lead can price it."
        ),
        priced_text="No line item. Delivery must supply a supportable target date.",
        amount=None,
        conditions=["Delivery lead must supply a supportable estimate and date."],
        policy_refs=refs,
        evidence_ids=[item.id],
        required_roles=_roles(policy, request),
    )


def _assess_timeline(request: ScopeRequest, book: EvidenceBook, policy: Policy) -> Decision:
    item = _latest(book, request.id, "capacity")
    if item is None:
        return _no_evidence(request, policy)
    payload = item.payload
    rush_hours = float(payload.get("rush_hours", 0.0))
    rate = float(payload.get("rate", 0.0))
    premium = float(payload.get("premium_pct", policy.timeline.rush_premium_pct))
    confidence = float(payload.get("confidence_pct", 0.0))
    supported = float(payload.get("supported_weeks", policy.timeline.max_weeks_saved))
    requested_weeks = float(request.requested_value.get("weeks_saved", 1))
    allowed = min(requested_weeks, supported, float(policy.timeline.max_weeks_saved))
    amount = round(rush_hours * rate * (1.0 + premium / 100.0), 2)
    refs = ["policy.timeline.capacity_required"]
    conditions = [
        "Date guarantee excludes client-side delays and late content.",
        "Capacity is reserved for the compressed window.",
        "A daily standup and a named client decision-maker are required.",
    ]

    if confidence < policy.timeline.min_confidence_pct:
        return Decision(
            request_id=request.id,
            verdict=Verdict.ESCALATE,
            rationale=(
                f"Delivery confidence is {confidence:.0f}%, below the {policy.timeline.min_confidence_pct:.0f}% "
                "floor. No acceleration can be promised on this plan."
            ),
            priced_text="No acceleration commitment until delivery confidence clears the policy floor.",
            amount=None,
            conditions=["Delivery must attach a fresh capacity plan above the confidence floor."],
            policy_refs=[*refs, "policy.timeline.min_confidence"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )

    if allowed <= 0:
        return Decision(
            request_id=request.id,
            verdict=Verdict.REFUSE,
            rationale="No compressible weeks are supported by the capacity plan.",
            priced_text="No acceleration line item.",
            amount=None,
            policy_refs=refs,
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )

    if allowed < requested_weeks:
        return Decision(
            request_id=request.id,
            verdict=Verdict.NARROW,
            rationale=(
                f"Requested {requested_weeks:.1f} week(s); the capacity plan supports "
                f"{allowed:.1f} week(s) and policy caps acceleration at "
                f"{policy.timeline.max_weeks_saved} week(s)."
            ),
            priced_text=(
                f"{request.title}: acceleration of {allowed:.1f} week(s), not "
                f"{requested_weeks:.1f}; rush premium included."
            ),
            amount=amount,
            approved_value={
                "kind": "timeline",
                "weeks_saved": allowed,
                "requested_weeks": requested_weeks,
                "confidence_pct": confidence,
                "premium_pct": premium,
            },
            conditions=conditions,
            policy_refs=[*refs, "policy.timeline.max_weeks", "policy.timeline.rush_premium"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )

    return Decision(
        request_id=request.id,
        verdict=Verdict.APPROVE,
        rationale=(
            f"{requested_weeks:.1f} week(s) of acceleration is supported at "
            f"{confidence:.0f}% confidence and priced with the standard rush premium."
        ),
        priced_text=f"{request.title}: acceleration of {requested_weeks:.1f} week(s), rush premium included.",
        amount=amount,
        approved_value={
            "kind": "timeline",
            "weeks_saved": requested_weeks,
            "requested_weeks": requested_weeks,
            "confidence_pct": confidence,
            "premium_pct": premium,
        },
        conditions=conditions,
        policy_refs=[*refs, "policy.timeline.rush_premium"],
        evidence_ids=[item.id],
        required_roles=_roles(policy, request),
    )


def _assess_rate(
    request: ScopeRequest,
    book: EvidenceBook,
    policy: Policy,
    *,
    base_amount: float,
    engagement: Engagement,
) -> Decision:
    item = _latest(book, request.id, "rate_card")
    if item is None:
        return _no_evidence(request, policy)
    requested = float(request.requested_value.get("discount_pct", 0.0))
    cap = policy.rate.max_pct
    approved = min(requested, cap)
    amount = -round(base_amount * approved / 100.0, 2)
    margin = policy.margin_pct(approved, engagement.delivery_cost_ratio_pct)
    refs = ["policy.rate.max"]
    conditions = [
        f"Quote valid for {policy.rate.quote_valid_days} days.",
        "Discount applies only to the line items in this change order.",
        "No further discount without a written finance exception.",
    ]
    if approved < requested:
        rationale = (
            f"Requested {requested:.2f}% exceeds the policy ceiling of {cap:.2f}%; "
            f"the approvable maximum is {cap:.2f}%."
        )
        refs.append("policy.rate.over_cap")
    else:
        rationale = (
            f"{approved:.2f}% is inside the headline ceiling of {cap:.2f}%. "
            "Finance and the account lead own the commercial decision."
        )
    return Decision(
        request_id=request.id,
        verdict=Verdict.APPROVE if approved == requested else Verdict.NARROW,
        rationale=rationale,
        priced_text=(
            f"{approved:.2f}% discount on this change order "
            f"({abs(amount):,.2f} {engagement.currency} off a "
            f"{base_amount:,.2f} {engagement.currency} subtotal)."
        ),
        amount=amount,
        approved_value={
            "kind": "rate",
            "discount_pct": approved,
            "requested_pct": requested,
            "base_amount": base_amount,
            "margin_pct": margin,
            "net_total": round(base_amount + amount, 2),
        },
        conditions=conditions,
        policy_refs=refs,
        evidence_ids=[item.id],
        required_roles=_roles(policy, request),
    )


def _assess_terms(request: ScopeRequest, book: EvidenceBook, policy: Policy) -> Decision:
    item = _latest(book, request.id, "contract")
    if item is None:
        return _no_evidence(request, policy)
    payload = item.payload
    review_hours = float(payload.get("review_hours", 0.0))
    rate = float(payload.get("rate", 0.0))
    amount = round(review_hours * rate, 2)
    cap = policy.terms.max_liability_multiple
    requested_unlimited = bool(request.requested_value.get("unlimited_liability"))
    raw_multiple = request.requested_value.get("liability_multiple")
    requested_multiple = float(raw_multiple) if raw_multiple is not None else None
    refs = ["policy.terms.liability_cap"]
    conditions = [
        "Legal review is required before signature.",
        "Liability is mutual and capped at the policy maximum.",
        "The security addendum is limited to the scope in this change order.",
    ]
    approved_value = {
        "kind": "terms",
        "liability_multiple": cap,
        "unlimited_liability": False,
        "custom_security_addendum": bool(payload.get("custom_security_addendum", False)),
    }

    if requested_unlimited and not policy.terms.allow_unlimited_liability:
        return Decision(
            request_id=request.id,
            verdict=Verdict.NARROW,
            rationale=(
                "The client asked for unlimited liability. Policy refuses it and caps mutual "
                f"liability at {cap:g}x fees; the custom security addendum can still be reviewed."
            ),
            priced_text=(
                f"{request.title}: mutual liability capped at {cap:g}x fees; unlimited liability "
                "is refused. Custom security addendum included."
            ),
            amount=amount,
            approved_value=approved_value,
            conditions=conditions,
            policy_refs=[*refs, "policy.terms.unlimited_refused"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )

    if requested_multiple is not None and requested_multiple > cap:
        return Decision(
            request_id=request.id,
            verdict=Verdict.NARROW,
            rationale=(
                f"Requested a {requested_multiple:g}x liability cap; policy allows at most {cap:g}x."
            ),
            priced_text=f"{request.title}: mutual liability capped at {cap:g}x fees.",
            amount=amount,
            approved_value=approved_value,
            conditions=conditions,
            policy_refs=[*refs, "policy.terms.over_cap"],
            evidence_ids=[item.id],
            required_roles=_roles(policy, request),
        )

    return Decision(
        request_id=request.id,
        verdict=Verdict.APPROVE,
        rationale=(
            "The requested terms are inside policy: mutual liability at or below the cap and a "
            "bounded security addendum."
        ),
        priced_text=f"{request.title}: custom security addendum included; liability capped at {cap:g}x fees.",
        amount=amount,
        approved_value=approved_value,
        conditions=conditions,
        policy_refs=refs,
        evidence_ids=[item.id],
        required_roles=_roles(policy, request),
    )


def assess_engagement(
    *,
    engagement: Engagement,
    evidence: EvidenceBook,
    policy: Policy,
    as_of: date | None = None,
) -> list[Decision]:
    """Price every request in a stable order. Pure function, no I/O."""
    del as_of  # reserved for future time-dependent policy windows
    decisions: list[Decision] = []
    rate_requests: list[ScopeRequest] = []
    base_amount = 0.0
    handlers = {
        RequestType.NEW_WORK.value: _assess_new_work,
        RequestType.TIMELINE.value: _assess_timeline,
        RequestType.TERMS.value: _assess_terms,
    }
    for request in engagement.requests:
        if request.type == RequestType.RATE.value:
            rate_requests.append(request)
            continue
        decision = handlers[request.type](request, evidence, policy)
        decisions.append(decision)
        if decision.is_commitment and decision.amount is not None and decision.amount > 0:
            base_amount += decision.amount

    for request in rate_requests:
        decision = _assess_rate(
            request,
            evidence,
            policy,
            base_amount=round(base_amount, 2),
            engagement=engagement,
        )
        decisions.append(decision)

    order = {request.id: index for index, request in enumerate(engagement.requests)}
    decisions.sort(key=lambda item: order[item.request_id])
    cleaned: list[Decision] = []
    for decision in decisions:
        # A signature never binds to an empty commitment.
        if decision.is_commitment and not decision.priced_text.strip():
            decision = decision.model_copy(
                update={
                    "verdict": Verdict.ESCALATE.value,
                    "priced_text": "No line item. Pricing produced empty language.",
                    "amount": None,
                }
            )
        cleaned.append(decision)
    return cleaned
