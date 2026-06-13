"""mesa-core exceptions."""

from __future__ import annotations


class MesaError(Exception):
    """Base class for all mesa-core errors."""


class MesaValidationError(MesaError):
    """A profile or query failed validation.

    ``errors`` holds the individual validation failure messages.
    """

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = errors or [message]


class InvalidCursorError(MesaError):
    """A pagination cursor is invalid or has been invalidated by profile changes.

    Callers handle this by restarting pagination from the beginning (Spec 9.2).
    """


class MesaEnforcementError(MesaError):
    """A service call was blocked by MESA enforcement."""

    def __init__(self, reason: str, rule_applied: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule_applied = rule_applied
