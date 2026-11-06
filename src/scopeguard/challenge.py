"""The Red Team: adversarial, independent checks on a priced packet.

The Pricer asks "is this inside the rules I was given?" The Red Team asks
"would I let my own name sit on this change order?" It can block a packet and
propose a specific remediation; a revision creates a new packet hash and
invalidates every earlier signature.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from scopeguard.models import (
    Approval,
    ApprovalStatus,
    Decision,
    Engagement,
    EvidenceBook,
    Finding,
    GateCheck,
    GateResult,
    Packet,
    RequestType,
    ScopeRequest,
    Severity,
    Verdict,
)
from scopeguard.policy import Policy
from scopeguard.receipt import expected_phrase


def _round_down(value: float, places: int = 2) -> float:
    factor = 10**places
    return int(value * factor) / factor


def _has_phrase(text: str, phrase: str) -> bool:
    return phrase.casefold() in text.casefold()


def _rate_base(packet: Packet, engagement: Engagement) -> float:
    types = {request.id: request.type for request in engagement.requests}
    return round(
        sum(
            item.amount or 0.0
            for item in packet.decisions
            if (item.amount or 0.0) > 0 and types.get(item.request_id) != RequestType.RATE.value
        ),
        2,
    )


def challenge_packet(
    *,
    packet: Packet,
    engagement: Engagement,
    evidence: EvidenceBook,
    policy: Policy,
    as_of: date,
) -> list[Finding]:
    findings: list[Finding] = []
    counter = 0
    requests = {request.id: request for request in engagement.requests}
    decisions = {item.request_id: item for item in packet.decisions}
    base = _rate_base(packet, engagement)

    def add(
        severity: Severity,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        remediation: dict[str, Any] | None = None,
    ) -> None:
        nonlocal counter
        counter += 1
        findings.append(
            Finding(
                id=f"RT-{counter:03d}",
                severity=severity,
                request_id=request_id,
                code=code,
                message=message,
                remediation=remediation or {},
            )
        )

    for request in engagement.requests:
        decision = decisions.get(request.id)
        if decision is None:
            add(
                Severity.BLOCK,
                "request_unpriced",
                f"{request.id} has no decision. Nothing may be sealed with an unpriced request.",
                request_id=request.id,
            )
            continue
        if decision.required_roles != policy.required_roles(request.type):
            add(
                Severity.BLOCK,
                "approval_matrix_mismatch",
                f"{request.id} carries roles {decision.required_roles}, but current policy requires "
                f"{policy.required_roles(request.type)}.",
                request_id=request.id,
            )

        used = [
            evidence.by_id(item)
            for item in decision.evidence_ids
            if item in {entry.id for entry in evidence.items}
        ]
        if decision.is_commitment and not used:
            add(
                Severity.BLOCK,
                "commitment_without_evidence",
                f"{request.id} is a commitment with no linked evidence.",
                request_id=request.id,
            )
        for item in used:
            age = (as_of - item.observed_at).days
            if age > policy.max_evidence_age_days:
                add(
                    Severity.BLOCK,
                    "evidence_stale",
                    f"{request.id} relies on {item.id}, observed {age} days ago; the limit is "
                    f"{policy.max_evidence_age_days} days.",
                    request_id=request.id,
                    remediation={"kind": "refresh_evidence", "evidence_id": item.id},
                )
            if item.valid_until is not None and item.valid_until < as_of:
                add(
                    Severity.BLOCK,
                    "evidence_expired",
                    f"{item.id} expired on {item.valid_until.isoformat()}.",
                    request_id=request.id,
                    remediation={"kind": "refresh_evidence", "evidence_id": item.id},
                )

        if decision.is_commitment and decision.amount is None:
            add(
                Severity.BLOCK,
                "unpriced_commitment",
                f"{request.id} is a commitment with no amount. A change order line item must have a price.",
                request_id=request.id,
            )
        if decision.is_commitment and not decision.conditions:
            add(
                Severity.WARN,
                "commitment_without_conditions",
                f"{request.id} has no conditions attached. Conditions are how a narrow commitment stays narrow.",
                request_id=request.id,
            )
        if (
            decision.amount is not None
            and decision.amount < 0
            and request.type != RequestType.RATE.value
        ):
            add(
                Severity.BLOCK,
                "unexpected_credit",
                f"{request.id} has a negative amount outside the rate line.",
                request_id=request.id,
            )

        if decision.is_commitment and request.type == RequestType.RATE.value:
            _check_rate(
                add,
                decision=decision,
                request=request,
                policy=policy,
                engagement=engagement,
                base=base,
            )

        if decision.is_commitment and request.type == RequestType.TIMELINE.value:
            _check_timeline(add, decision=decision, request=request, policy=policy)

        if decision.is_commitment and request.type == RequestType.NEW_WORK.value:
            _check_new_work(add, decision=decision, request=request)

        if decision.is_commitment and request.type == RequestType.TERMS.value:
            _check_terms(add, decision=decision, request=request, policy=policy)

    approved = {item.request_id for item in packet.decisions if item.is_commitment}
    missing = sorted(set(requests) - approved - set(decisions))
    if missing:
        add(
            Severity.BLOCK,
            "missing_decision",
            f"Requests without any decision: {', '.join(missing)}.",
        )

    if not packet.decisions:
        add(Severity.BLOCK, "empty_packet", "The packet contains no decisions.")

    severity_order = {Severity.BLOCK.value: 0, Severity.WARN.value: 1, Severity.NOTE.value: 2}
    findings.sort(key=lambda item: (severity_order[item.severity], item.id))
    return findings


def _check_rate(
    add: Any,
    *,
    decision: Decision,
    request: ScopeRequest,
    policy: Policy,
    engagement: Engagement,
    base: float,
) -> None:
    discount = float(decision.approved_value.get("discount_pct", 0.0))
    margin_max = policy.max_discount_for_margin(engagement.delivery_cost_ratio_pct)
    remediation_value = policy.rate.autonomy_pct
    if margin_max is not None:
        remediation_value = min(remediation_value, margin_max)
    remediation_value = max(0.0, _round_down(remediation_value, 2))

    if discount > policy.rate.max_pct:
        add(
            Severity.BLOCK,
            "rate_over_policy",
            f"{request.id} quotes {discount:.2f}%, above the policy maximum of {policy.rate.max_pct:.2f}%.",
            request_id=request.id,
            remediation={"kind": "narrow_rate", "value": policy.rate.max_pct},
        )
    if discount > policy.rate.autonomy_pct:
        add(
            Severity.BLOCK,
            "rate_autonomy",
            f"{request.id} quotes {discount:.2f}%, above the {policy.rate.autonomy_pct:.2f}% "
            "autonomy ceiling. A documented finance exception is required, and none is attached.",
            request_id=request.id,
            remediation={"kind": "narrow_rate", "value": remediation_value},
        )
    margin = policy.margin_pct(discount, engagement.delivery_cost_ratio_pct)
    if margin < policy.rate.margin_floor_pct:
        add(
            Severity.BLOCK,
            "margin_floor",
            f"{request.id} at {discount:.2f}% produces {margin:.2f}% gross margin, below the "
            f"{policy.rate.margin_floor_pct:.2f}% floor.",
            request_id=request.id,
            remediation={"kind": "narrow_rate", "value": remediation_value},
        )
    recorded_base = float(decision.approved_value.get("base_amount", 0.0))
    if abs(recorded_base - base) > 0.01:
        add(
            Severity.BLOCK,
            "base_amount_mismatch",
            f"{request.id} discounts a {recorded_base:,.2f} subtotal, but the positive line items "
            f"total {base:,.2f}.",
            request_id=request.id,
            remediation={"kind": "recompute_rate"},
        )
    expected_amount = -round(base * discount / 100.0, 2)
    if decision.amount is None or abs(decision.amount - expected_amount) > 0.01:
        add(
            Severity.BLOCK,
            "rate_amount_mismatch",
            f"{request.id} carries amount {decision.amount}, but {discount:.2f}% of "
            f"{base:,.2f} is {expected_amount:,.2f}.",
            request_id=request.id,
            remediation={"kind": "recompute_rate"},
        )


def _check_timeline(
    add: Any,
    *,
    decision: Decision,
    request: ScopeRequest,
    policy: Policy,
) -> None:
    weeks = float(decision.approved_value.get("weeks_saved", 0.0))
    if weeks > policy.timeline.max_weeks_saved:
        add(
            Severity.BLOCK,
            "timeline_over_policy",
            f"{request.id} commits {weeks:.1f} week(s) of acceleration; policy caps it at "
            f"{policy.timeline.max_weeks_saved}.",
            request_id=request.id,
            remediation={"kind": "narrow_timeline", "value": policy.timeline.max_weeks_saved},
        )
    confidence = float(decision.approved_value.get("confidence_pct", 0.0))
    if confidence < policy.timeline.min_confidence_pct:
        add(
            Severity.BLOCK,
            "timeline_confidence",
            f"{request.id} commits acceleration at {confidence:.0f}% confidence, below the "
            f"{policy.timeline.min_confidence_pct:.0f}% floor.",
            request_id=request.id,
            remediation={
                "kind": "add_condition",
                "condition": "No acceleration commitment below the confidence floor.",
            },
        )
    if decision.amount is None or decision.amount <= 0:
        add(
            Severity.BLOCK,
            "rush_not_priced",
            f"{request.id} commits acceleration with no positive rush line item.",
            request_id=request.id,
        )


def _check_new_work(add: Any, *, decision: Decision, request: ScopeRequest) -> None:
    kind = str(decision.approved_value.get("kind", ""))
    text = decision.priced_text
    if kind == "beta":
        says_ga = bool(re.search(r"\bga\b", text, flags=re.IGNORECASE)) or _has_phrase(
            text, "generally available"
        )
        caveated = _has_phrase(text, "not included") or _has_phrase(text, "not committed")
        if says_ga and not caveated:
            add(
                Severity.BLOCK,
                "new_work_overclaim",
                f"{request.id} is a beta-only commitment but the line item describes GA.",
                request_id=request.id,
                remediation={"kind": "reword_new_work"},
            )
        if not any("beta" in condition.casefold() for condition in decision.conditions):
            add(
                Severity.WARN,
                "beta_missing_condition",
                f"{request.id} is beta-only but no condition names the beta boundary.",
                request_id=request.id,
            )


def _check_terms(
    add: Any,
    *,
    decision: Decision,
    request: ScopeRequest,
    policy: Policy,
) -> None:
    multiple = float(decision.approved_value.get("liability_multiple", 0.0))
    if multiple > policy.terms.max_liability_multiple:
        add(
            Severity.BLOCK,
            "terms_over_policy",
            f"{request.id} caps liability at {multiple:g}x fees; policy allows at most "
            f"{policy.terms.max_liability_multiple:g}x.",
            request_id=request.id,
            remediation={"kind": "narrow_terms"},
        )
    text = decision.priced_text.casefold()
    if "unlimited" in text and "refused" not in text and "not" not in text:
        add(
            Severity.BLOCK,
            "terms_unlimited_language",
            f"{request.id} describes unlimited liability without refusing it.",
            request_id=request.id,
            remediation={"kind": "narrow_terms"},
        )
    if policy.terms.require_legal_review and not any(
        "legal review" in condition.casefold() for condition in decision.conditions
    ):
        add(
            Severity.BLOCK,
            "terms_missing_legal_review",
            f"{request.id} has no legal-review condition.",
            request_id=request.id,
            remediation={
                "kind": "add_condition",
                "condition": "Legal review is required before signature.",
            },
        )


def apply_revisions(
    *,
    packet: Packet,
    engagement: Engagement,
    policy: Policy,
    findings: list[Finding],
) -> list[Decision]:
    """Apply the Red Team's machine-readable remediations to a copy of decisions."""
    revisions = [
        item for item in findings if item.severity == Severity.BLOCK.value and item.remediation
    ]
    decisions = [item.model_copy(deep=True) for item in packet.decisions]
    by_request = {item.request_id: item for item in decisions}
    requests = {request.id: request for request in engagement.requests}
    base = _rate_base(packet, engagement)

    for finding in revisions:
        if finding.request_id is None:
            continue
        decision = by_request.get(finding.request_id)
        request = requests.get(finding.request_id)
        if decision is None or request is None:
            continue
        kind = finding.remediation.get("kind")
        if kind == "narrow_rate":
            raw_value = finding.remediation.get("value")
            if raw_value is None:
                continue
            current = decision.approved_value.get("discount_pct")
            value = float(raw_value)
            if current is not None:
                value = min(float(current), value)
            value = max(0.0, _round_down(value, 2))
            decision.verdict = Verdict.NARROW
            decision.approved_value["discount_pct"] = value
            decision.approved_value["requested_pct"] = decision.approved_value.get(
                "requested_pct", value
            )
            decision.approved_value["margin_pct"] = policy.margin_pct(
                value, engagement.delivery_cost_ratio_pct
            )
            decision.amount = -round(base * value / 100.0, 2)
            decision.approved_value["base_amount"] = base
            decision.approved_value["net_total"] = round(base + decision.amount, 2)
            decision.priced_text = (
                f"{value:.2f}% discount on this change order "
                f"({abs(decision.amount):,.2f} {engagement.currency} off a "
                f"{base:,.2f} {engagement.currency} subtotal)."
            )
            condition = f"Discount capped at {value:.2f}% without a written finance exception."
            if condition not in decision.conditions:
                decision.conditions.append(condition)
            note = (
                f"Revised after Red Team finding {finding.id}: "
                f"the approved discount is capped at {value:.2f}%."
            )
            if note not in decision.rationale:
                decision.rationale = decision.rationale.rstrip() + " " + note
        elif kind == "recompute_rate":
            discount = float(decision.approved_value.get("discount_pct", 0.0))
            decision.amount = -round(base * discount / 100.0, 2)
            decision.approved_value["base_amount"] = base
            decision.approved_value["net_total"] = round(base + decision.amount, 2)
            decision.priced_text = (
                f"{discount:.2f}% discount on this change order "
                f"({abs(decision.amount):,.2f} {engagement.currency} off a "
                f"{base:,.2f} {engagement.currency} subtotal)."
            )
            note = f"Revised after Red Team finding {finding.id}: amount recomputed."
            if note not in decision.rationale:
                decision.rationale = decision.rationale.rstrip() + " " + note
        elif kind == "narrow_timeline":
            value = float(finding.remediation.get("value", policy.timeline.max_weeks_saved))
            decision.verdict = Verdict.NARROW
            decision.approved_value["weeks_saved"] = value
            decision.priced_text = (
                f"{request.title}: acceleration of {value:.1f} week(s), rush premium included."
            )
            note = f"Revised after Red Team finding {finding.id}."
            if note not in decision.rationale:
                decision.rationale = decision.rationale.rstrip() + " " + note
        elif kind == "add_condition":
            condition = str(finding.remediation.get("condition", "")).strip()
            if condition and condition not in decision.conditions:
                decision.conditions.append(condition)
        elif kind == "reword_new_work":
            decision.verdict = Verdict.NARROW
            beta_date = decision.approved_value.get("beta_date", "the beta date")
            ga_label = decision.approved_value.get("ga_quarter", "a later release")
            decision.approved_value["kind"] = "beta"
            decision.approved_value["ga_committed"] = False
            decision.priced_text = (
                f"{request.title}: beta by {beta_date}; GA targeted {ga_label}, not included."
            )
            condition = "Customer-facing language must say beta, not generally available."
            if condition not in decision.conditions:
                decision.conditions.append(condition)
            note = f"Revised after Red Team finding {finding.id}."
            if note not in decision.rationale:
                decision.rationale = decision.rationale.rstrip() + " " + note
        elif kind == "narrow_terms":
            decision.verdict = Verdict.NARROW
            decision.approved_value["liability_multiple"] = policy.terms.max_liability_multiple
            decision.approved_value["unlimited_liability"] = False
            decision.priced_text = (
                f"{request.title}: mutual liability capped at "
                f"{policy.terms.max_liability_multiple:g}x fees; unlimited liability is refused."
            )
            condition = "Legal review is required before signature."
            if condition not in decision.conditions:
                decision.conditions.append(condition)
            note = f"Revised after Red Team finding {finding.id}."
            if note not in decision.rationale:
                decision.rationale = decision.rationale.rstrip() + " " + note

    return decisions


def gate_packet(
    *,
    packet: Packet,
    approvals: list[Approval],
    approvers: Any,
    policy: Policy,
    engagement: Engagement,
) -> GateResult:
    """The human signature gate. Returns every reason a packet cannot be sealed."""
    checks: list[GateCheck] = []
    pending: list[str] = []

    checks.append(
        GateCheck(
            code="status",
            ok=packet.status in {"challenged", "sealed"},
            detail=f"packet status is {packet.status}",
        )
    )
    checks.append(
        GateCheck(
            code="no_blocking_findings",
            ok=not packet.has_blocks(),
            detail="Red Team left no blocking findings"
            if not packet.has_blocks()
            else f"{sum(1 for item in packet.findings if item.severity == 'block')} blocking finding(s)",
        )
    )
    priced = [
        decision.request_id
        for decision in packet.decisions
        if decision.is_commitment and decision.amount is None
    ]
    checks.append(
        GateCheck(
            code="every_line_item_priced",
            ok=not priced,
            detail="every commitment has an amount"
            if not priced
            else "unpriced commitments: " + ", ".join(priced),
        )
    )
    valid = [
        item
        for item in approvals
        if item.status == ApprovalStatus.VALID.value and item.packet_hash == packet.packet_hash
    ]
    satisfied: set[tuple[str, str]] = set()
    for decision in packet.decisions:
        if not decision.is_commitment:
            continue
        for role in decision.required_roles:
            role = role.upper()
            key = f"{decision.request_id} {role}"
            match = next(
                (
                    item
                    for item in valid
                    if item.request_id == decision.request_id and item.role == role
                ),
                None,
            )
            if match is None:
                pending.append(key)
                continue
            if not approvers.can_hold(match.actor, role):
                checks.append(
                    GateCheck(
                        code=f"actor_role:{key}",
                        ok=False,
                        detail=f"{match.actor} does not hold {role} in the approver map",
                    )
                )
                pending.append(key)
                continue
            expected = expected_phrase(engagement.id, decision.request_id, role)
            if match.phrase != expected:
                checks.append(
                    GateCheck(
                        code=f"phrase:{key}",
                        ok=False,
                        detail=f"expected {expected!r}, got {match.phrase!r}",
                    )
                )
                pending.append(key)
                continue
            satisfied.add((decision.request_id, role))

    checks.append(
        GateCheck(
            code="signatures",
            ok=not pending,
            detail="all required roles signed this packet hash"
            if not pending
            else "missing signatures: " + ", ".join(pending),
        )
    )
    checks.append(
        GateCheck(
            code="signature_count",
            ok=len(satisfied) > 0,
            detail=f"{len(satisfied)} distinct request/role signatures bound to {packet.packet_hash[:12]}",
        )
    )
    stale = [item for item in approvals if item.status == ApprovalStatus.STALE.value]
    checks.append(
        GateCheck(
            code="stale_signatures_ignored",
            ok=True,
            detail=f"{len(stale)} stale signature(s) ignored; they do not bind this packet hash",
        )
    )
    return GateResult(ok=all(item.ok for item in checks), checks=checks, pending=pending)
