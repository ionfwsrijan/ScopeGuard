"""The Examiner: adversarial, independent checks on an assessed packet.

The Actuary asks "is this inside the rules it was given?" The Examiner asks
"would I sign this with my own name?" It can block a packet and propose a
specific remediation; a revision creates a new packet hash and invalidates
every earlier approval.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from vouch.models import (
    Approval,
    ApprovalStatus,
    Deal,
    Decision,
    EvidenceBook,
    Finding,
    GateCheck,
    GateResult,
    Packet,
    Severity,
    Verdict,
)
from vouch.policy import Policy
from vouch.receipt import expected_phrase


def _round_down(value: float, places: int = 1) -> float:
    factor = 10**places
    return int(value * factor) / factor


def _max_discount_for_margin(policy: Policy, cost_ratio_pct: float) -> float | None:
    floor = policy.discount.margin_floor_pct
    if floor >= 100:
        return None
    max_revenue_pct = cost_ratio_pct / (1.0 - floor / 100.0)
    if max_revenue_pct >= 100:
        return None
    return max(0.0, _round_down(100.0 - max_revenue_pct, 2))


def challenge_packet(
    *,
    packet: Packet,
    deal: Deal,
    evidence: EvidenceBook,
    policy: Policy,
    as_of: date,
) -> list[Finding]:
    findings: list[Finding] = []
    counter = 0

    def add(
        severity: Severity,
        code: str,
        message: str,
        *,
        claim_id: str | None = None,
        remediation: dict[str, Any] | None = None,
    ) -> None:
        nonlocal counter
        counter += 1
        findings.append(
            Finding(
                id=f"EX-{counter:03d}",
                severity=severity,
                claim_id=claim_id,
                code=code,
                message=message,
                remediation=remediation or {},
            )
        )

    decisions = {item.claim_id: item for item in packet.decisions}
    for claim in deal.claims:
        decision = decisions.get(claim.id)
        if decision is None:
            add(
                Severity.BLOCK,
                "claim_unassessed",
                f"{claim.id} has no decision. Nothing may be sealed with an unassessed ask.",
                claim_id=claim.id,
            )
            continue
        if decision.required_roles != policy.required_roles(claim.type):
            add(
                Severity.BLOCK,
                "approval_matrix_mismatch",
                f"{claim.id} carries roles {decision.required_roles}, but current policy requires "
                f"{policy.required_roles(claim.type)}.",
                claim_id=claim.id,
            )

        used = [
            evidence.by_id(item)
            for item in decision.evidence_ids
            if item in {e.id for e in evidence.items}
        ]
        if decision.is_commitment and not used:
            add(
                Severity.BLOCK,
                "commitment_without_evidence",
                f"{claim.id} is a commitment with no linked evidence.",
                claim_id=claim.id,
            )
        for item in used:
            age = (as_of - item.observed_at).days
            if age > policy.max_evidence_age_days:
                add(
                    Severity.BLOCK,
                    "evidence_stale",
                    f"{claim.id} relies on {item.id}, observed {age} days ago; the limit is "
                    f"{policy.max_evidence_age_days} days.",
                    claim_id=claim.id,
                    remediation={"kind": "refresh_evidence", "evidence_id": item.id},
                )
            if item.valid_until is not None and item.valid_until < as_of:
                add(
                    Severity.BLOCK,
                    "evidence_expired",
                    f"{item.id} expired on {item.valid_until.isoformat()}.",
                    claim_id=claim.id,
                    remediation={"kind": "refresh_evidence", "evidence_id": item.id},
                )

        if decision.verdict == Verdict.APPROVE.value and str(claim.type) == "discount":
            discount = float(decision.approved_value.get("discount_pct", 0.0))
            if discount > policy.discount.autonomy_pct:
                add(
                    Severity.BLOCK,
                    "discount_autonomy",
                    f"{claim.id} approves {discount:.2f}%, above the {policy.discount.autonomy_pct:.2f}% "
                    "autonomy ceiling. A documented CFO exception is required, and none is attached.",
                    claim_id=claim.id,
                    remediation={
                        "kind": "narrow_discount",
                        "value": policy.discount.autonomy_pct,
                    },
                )
            margin = policy.margin_pct(discount, deal.cost_ratio_pct)
            if margin < policy.discount.margin_floor_pct:
                ceiling = _max_discount_for_margin(policy, deal.cost_ratio_pct)
                add(
                    Severity.BLOCK,
                    "margin_floor",
                    f"{claim.id} at {discount:.2f}% produces {margin:.2f}% gross margin, below the "
                    f"{policy.discount.margin_floor_pct:.2f}% floor.",
                    claim_id=claim.id,
                    remediation={"kind": "narrow_discount", "value": ceiling},
                )
            if decision.approved_value.get("annual_list_price") not in (
                None,
                deal.annual_list_price,
            ):
                add(
                    Severity.WARN,
                    "price_basis_mismatch",
                    f"{claim.id} uses a different annual list price than the deal record.",
                    claim_id=claim.id,
                )

        if decision.verdict == Verdict.APPROVE.value and str(claim.type) == "sla":
            approved = float(decision.approved_value.get("uptime_pct", 0.0))
            if approved > policy.sla.max_uptime_pct:
                add(
                    Severity.BLOCK,
                    "sla_over_policy",
                    f"{claim.id} commits {approved:.2f}%, above the policy maximum of "
                    f"{policy.sla.max_uptime_pct:.2f}%.",
                    claim_id=claim.id,
                    remediation={"kind": "narrow_sla", "value": policy.sla.max_uptime_pct},
                )

        if str(claim.type) == "security" and decision.is_commitment:
            text = decision.approved_text.casefold()
            if "certified" in text and "not certified" not in text:
                add(
                    Severity.BLOCK,
                    "security_overclaim",
                    f"{claim.id} says 'certified' while the evidence does not support certification.",
                    claim_id=claim.id,
                    remediation={"kind": "reword_security"},
                )
            if "in progress" in text and not any(
                "report" in item.casefold() for item in decision.conditions
            ):
                add(
                    Severity.BLOCK,
                    "security_missing_report_caveat",
                    f"{claim.id} describes an in-progress report without a report-date caveat.",
                    claim_id=claim.id,
                    remediation={
                        "kind": "add_condition",
                        "condition": "No certification claim before the report is issued.",
                    },
                )

        if decision.is_commitment and not decision.conditions:
            add(
                Severity.WARN,
                "commitment_without_conditions",
                f"{claim.id} has no conditions attached. Conditions are how a narrow commitment stays narrow.",
                claim_id=claim.id,
            )

    approved_claims = {item.claim_id for item in packet.decisions if item.is_commitment}
    missing = sorted(
        {claim.id for claim in deal.claims}
        - approved_claims
        - {item.claim_id for item in packet.decisions}
    )
    if missing:
        add(
            Severity.BLOCK,
            "missing_decision",
            f"Claims without any decision: {', '.join(missing)}.",
        )

    if not packet.decisions:
        add(Severity.BLOCK, "empty_packet", "The packet contains no decisions.")

    severity_order = {Severity.BLOCK.value: 0, Severity.WARN.value: 1, Severity.NOTE.value: 2}
    findings.sort(key=lambda item: (severity_order[item.severity], item.id))
    return findings


def apply_revisions(
    *,
    packet: Packet,
    deal: Deal,
    policy: Policy,
    findings: list[Finding],
) -> list[Decision]:
    """Apply the Examiner's machine-readable remediations to a copy of decisions."""
    revisions = [
        item for item in findings if item.severity == Severity.BLOCK.value and item.remediation
    ]
    decisions = [item.model_copy(deep=True) for item in packet.decisions]
    by_claim = {item.claim_id: item for item in decisions}

    for finding in revisions:
        if finding.claim_id is None:
            continue
        decision = by_claim.get(finding.claim_id)
        if decision is None:
            continue
        kind = finding.remediation.get("kind")
        if kind == "narrow_discount":
            raw_value = finding.remediation.get("value")
            if raw_value is None:
                continue
            current = decision.approved_value.get("discount_pct")
            value = float(raw_value)
            if current is not None:
                value = min(float(current), value)
            decision.verdict = Verdict.NARROW
            decision.approved_value["discount_pct"] = value
            decision.approved_value["requested_pct"] = decision.approved_value.get(
                "requested_pct", value
            )
            price = float(deal.annual_list_price)
            decision.approved_text = (
                f"{value:.2f}% discount on the annual list price of {price:,.0f} {deal.currency}."
            )
            decision.approved_value["net_price_pct"] = round(100.0 * (1.0 - value / 100.0), 4)
            condition = f"Discount capped at {value:.2f}% without a documented CFO exception."
            if condition not in decision.conditions:
                decision.conditions.append(condition)
            note = (
                f"Revised after Examiner finding {finding.id}: "
                f"the approved discount is capped at {value:.2f}%."
            )
            if note not in decision.rationale:
                decision.rationale = decision.rationale.rstrip() + " " + note
        elif kind == "narrow_sla":
            value = float(finding.remediation.get("value", policy.sla.max_uptime_pct))
            decision.verdict = Verdict.NARROW
            decision.approved_value["uptime_pct"] = value
            decision.approved_text = f"{value:.2f}% monthly uptime SLA for the production API."
            note = f"Revised after Examiner finding {finding.id}."
            decision.rationale = decision.rationale.rstrip() + " " + note
        elif kind == "add_condition":
            condition = str(finding.remediation.get("condition", "")).strip()
            if condition and condition not in decision.conditions:
                decision.conditions.append(condition)
        elif kind == "reword_security":
            decision.verdict = Verdict.NARROW
            decision.approved_text = decision.approved_text.replace("certified", "in progress")
            note = f"Revised after Examiner finding {finding.id}."
            decision.rationale = decision.rationale.rstrip() + " " + note

    return decisions


def gate_packet(
    *,
    packet: Packet,
    approvals: list[Approval],
    approvers: Any,
    policy: Policy,
    deal: Deal,
) -> GateResult:
    """The human gate. Returns every reason a packet cannot be sealed."""
    checks: list[GateCheck] = []
    pending: list[str] = []

    checks.append(
        GateCheck(
            code="status",
            ok=packet.status == "challenged",
            detail=f"packet status is {packet.status}",
        )
    )
    checks.append(
        GateCheck(
            code="no_blocking_findings",
            ok=not packet.has_blocks(),
            detail="Examiner left no blocking findings"
            if not packet.has_blocks()
            else f"{sum(1 for item in packet.findings if item.severity == 'block')} blocking finding(s)",
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
            key = f"{decision.claim_id} {role}"
            match = next(
                (
                    item
                    for item in valid
                    if item.claim_id == decision.claim_id and item.role == role
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
            expected = expected_phrase(deal.id, decision.claim_id, role)
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
            satisfied.add((decision.claim_id, role))

    checks.append(
        GateCheck(
            code="approvals",
            ok=not pending,
            detail="all required roles approved this packet hash"
            if not pending
            else "missing approvals: " + ", ".join(pending),
        )
    )
    checks.append(
        GateCheck(
            code="approval_count",
            ok=len(satisfied) > 0,
            detail=f"{len(satisfied)} distinct claim/role approvals bound to {packet.packet_hash[:12]}",
        )
    )
    stale = [item for item in approvals if item.status == ApprovalStatus.STALE.value]
    checks.append(
        GateCheck(
            code="stale_approvals_ignored",
            ok=True,
            detail=f"{len(stale)} stale approval(s) ignored; they do not bind this packet hash",
        )
    )
    return GateResult(ok=all(item.ok for item in checks), checks=checks, pending=pending)
