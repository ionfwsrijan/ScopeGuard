"""Policy: the part of Scope Guard that never asks a language model for an opinion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopeguard.errors import ConfigError
from scopeguard.models import RequestType


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RateRule(PolicyModel):
    """Discount policy for a change order."""

    max_pct: float = Field(ge=0, le=100)
    autonomy_pct: float = Field(ge=0, le=100)
    margin_floor_pct: float = Field(ge=0, le=100)
    quote_valid_days: int = Field(default=14, ge=1, le=365)

    @model_validator(mode="after")
    def _ordered(self) -> RateRule:
        if self.autonomy_pct > self.max_pct:
            raise ValueError("autonomy_pct cannot exceed max_pct")
        return self


class TimelineRule(PolicyModel):
    max_weeks_saved: int = Field(ge=0, le=52)
    rush_premium_pct: float = Field(ge=0, le=200)
    min_confidence_pct: float = Field(ge=0, le=100)
    require_capacity_evidence: bool = True


class NewWorkRule(PolicyModel):
    allow_beta: bool = True
    allow_ga_estimate: bool = True


class TermsRule(PolicyModel):
    max_liability_multiple: float = Field(gt=0)
    allow_unlimited_liability: bool = False
    require_legal_review: bool = True


class Policy(PolicyModel):
    id: str
    version: int = Field(ge=1)
    currency: str = "USD"
    max_evidence_age_days: int = Field(default=90, gt=0)
    rate: RateRule
    timeline: TimelineRule
    new_work: NewWorkRule = Field(default_factory=NewWorkRule)
    terms: TermsRule
    approval_matrix: dict[str, list[str]]

    @model_validator(mode="after")
    def _matrix(self) -> Policy:
        for request_type in RequestType:
            roles = self.approval_matrix.get(request_type.value, [])
            if not roles:
                raise ValueError(f"approval_matrix has no roles for {request_type.value}")
        return self

    def required_roles(self, request_type: RequestType | str) -> list[str]:
        key = request_type.value if isinstance(request_type, RequestType) else str(request_type)
        return [role.upper() for role in self.approval_matrix[key]]

    def margin_pct(self, discount_pct: float, cost_ratio_pct: float) -> float:
        """Gross margin after a discount on the change order.

        Revenue is the discounted price. Delivery cost stays at the rate-card
        cost, so a discount compresses margin faster than a naive "list minus
        cost" calculation suggests.
        """
        revenue = 100.0 * (1.0 - discount_pct / 100.0)
        cost = cost_ratio_pct
        if revenue <= 0:
            return -100.0
        return round((revenue - cost) / revenue * 100.0, 2)

    def max_discount_for_margin(self, cost_ratio_pct: float) -> float | None:
        """Largest discount that still clears the gross-margin floor."""
        floor = self.rate.margin_floor_pct
        if floor >= 100 or cost_ratio_pct >= 100:
            return None
        max_revenue_pct = cost_ratio_pct / (1.0 - floor / 100.0)
        if max_revenue_pct >= 100:
            # Full price already misses the floor, so no discount is available.
            return 0.0
        return max(0.0, round(100.0 - max_revenue_pct, 2))

    @classmethod
    def load(cls, path: Path) -> Policy:
        if not path.exists():
            raise ConfigError(
                f"policy not found: {path}", hint="Pass --policy or run from the repo root."
            )
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid policy YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError("policy must be a YAML mapping")
        try:
            return cls.model_validate(raw)
        except Exception as exc:
            raise ConfigError(f"invalid policy {path.name}: {exc}") from exc


DEFAULT_POLICY = Path("policies") / "scope_policy_v1.yaml"
