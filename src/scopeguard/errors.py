"""Domain errors with stable codes for the CLI and API."""

from __future__ import annotations


class ScopeError(Exception):
    """Base class. ``code`` is part of the API contract."""

    code = "scopeguard_error"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(ScopeError):
    code = "config_error"


class NotFoundError(ScopeError):
    code = "not_found"


class StateError(ScopeError):
    code = "state_error"


class ApprovalError(ScopeError):
    code = "approval_error"


class GateError(ScopeError):
    code = "gate_error"


class SwitchError(ScopeError):
    code = "switch_error"
