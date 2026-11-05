"""Policy: the part of Vouch that never asks a language model for an opinion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vouch.errors import ConfigError
from vouch.models import ClaimType


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscountRule(PolicyModel):
    max_pct: float = Field(ge=0, le=100)
    autonomy_pct: float = Field(ge=0, le=100)
    margin_floor_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> DiscountRule:
        if self.autonomy_pct > self.max_pct:
            raise ValueError("autonomy_pct cannot exceed max_pct")
        return self


class SlaRule(PolicyModel):
    max_uptime_pct: float = Field(ge=0, le=100)
    standard_uptime_pct: float = Field(ge=0, le=100)
    min_observed_uptime_pct: float = Field(ge=0, le=100)
    credit_required: bool = True
    maintenance_language_required: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> SlaRule:
        if self.standard_uptime_pct > self.max_uptime_pct:
            raise ValueError("standard_uptime_pct cannot exceed max_uptime_pct")
        return self


class FeatureRule(PolicyModel):
    allow_beta: bool = True
    allow_roadmap: bool = True


class SecurityRule(PolicyModel):
    allowed_statuses: list[str] = Field(default_factory=lambda: ["certified", "in_progress"])
    require_report_date_for_in_progress: bool = True


class Policy(PolicyModel):
    id: str
    version: int = Field(ge=1)
    currency: str = "USD"
    max_evidence_age_days: int = Field(default=90, gt=0)
    discount: DiscountRule
    sla: SlaRule
    feature: FeatureRule = Field(default_factory=FeatureRule)
    security: SecurityRule = Field(default_factory=SecurityRule)
    approval_matrix: dict[str, list[str]]

    @model_validator(mode="after")
    def _matrix(self) -> Policy:
        for claim_type in ClaimType:
            roles = self.approval_matrix.get(claim_type.value, [])
            if not roles:
                raise ValueError(f"approval_matrix has no roles for {claim_type.value}")
        return self

    def required_roles(self, claim_type: ClaimType | str) -> list[str]:
        key = claim_type.value if isinstance(claim_type, ClaimType) else str(claim_type)
        return [role.upper() for role in self.approval_matrix[key]]

    def margin_pct(self, discount_pct: float, cost_ratio_pct: float) -> float:
        """Gross margin after discount.

        Revenue is list price less discount. Cost stays at list price times
        the cost ratio, so discounting compresses margin faster than a naive
        "list minus cost" calculation suggests.
        """
        revenue = 100.0 * (1.0 - discount_pct / 100.0)
        cost = cost_ratio_pct
        if revenue <= 0:
            return -100.0
        return round((revenue - cost) / revenue * 100.0, 2)

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


DEFAULT_POLICY = Path("policies") / "deal_policy_v1.yaml"
