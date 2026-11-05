"""Domain errors with stable codes for the CLI and API."""

from __future__ import annotations


class VouchError(Exception):
    """Base class. ``code`` is part of the API contract."""

    code = "vouch_error"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(VouchError):
    code = "config_error"


class NotFoundError(VouchError):
    code = "not_found"


class StateError(VouchError):
    code = "state_error"


class ApprovalError(VouchError):
    code = "approval_error"


class GateError(VouchError):
    code = "gate_error"


class SwitchError(VouchError):
    code = "switch_error"
