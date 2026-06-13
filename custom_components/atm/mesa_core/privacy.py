"""Privacy classification enforcement and caller context (Spec 7, 9.4, 17).

Access to sensitive/restricted entities and all person entities is audit-logged
through the ``mesa_core.audit`` logger using structured ``extra`` fields. A
standardised audit event schema is planned for v1.1; any structured format
satisfies v1.0 (Spec 7.1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from custom_components.atm.mesa_core.profile import PRIVACY_RANK, PrivacyClassification, PrivacyLevel

audit_logger = logging.getLogger("mesa_core.audit")


@dataclass
class CallerContext:
    """Caller identity for the current session, surfaced by the host server.

    Not authored by the agent (Spec 9.4). When ``is_authenticated`` is False
    the caller is treated as having no roles.
    """

    caller_id: str
    roles: list[str] = field(default_factory=list)
    is_authenticated: bool = False
    session_id: str = ""
    display_name: str | None = None
    session_started_at: str | None = None

    def effective_roles(self) -> list[str]:
        return list(self.roles) if self.is_authenticated else []

    def to_dict(self) -> dict[str, object]:
        return {
            "caller_id": self.caller_id,
            "display_name": self.display_name,
            "roles": self.effective_roles(),
            "is_authenticated": self.is_authenticated,
            "session_id": self.session_id,
            "session_started_at": self.session_started_at,
        }


@dataclass
class AccessDecision:
    allowed: bool
    effective_level: PrivacyLevel
    deny_response_mode: str
    reason: str


class PrivacyEnforcer:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or audit_logger

    def evaluate(
        self,
        privacy: PrivacyClassification,
        caller: CallerContext | None,
        *,
        entity_id: str = "",
        is_person: bool = False,
        is_minor: bool = False,
    ) -> AccessDecision:
        roles = set(caller.effective_roles()) if caller else set()
        access_roles = privacy.access_roles or {}
        level = privacy.level

        # Person entities are at least sensitive (Spec 17).
        if is_person and PRIVACY_RANK[level] < PRIVACY_RANK[PrivacyLevel.SENSITIVE]:
            level = PrivacyLevel.SENSITIVE

        if roles & set(access_roles.get("deny_for") or []):
            decision = AccessDecision(
                allowed=False,
                effective_level=PrivacyLevel.RESTRICTED,
                deny_response_mode=privacy.deny_response_mode,
                reason="caller role is denied access to this entity (deny_for)",
            )
            self._audit(entity_id, caller, decision, is_person)
            return decision

        if roles & set(access_roles.get("restricted_for") or []):
            level = PrivacyLevel.RESTRICTED
        elif roles & set(access_roles.get("unrestricted_for") or []) and (
            PRIVACY_RANK[level] > PRIVACY_RANK[PrivacyLevel.NORMAL]
        ):
            level = PrivacyLevel.NORMAL

        # is_minor: true forces restricted regardless of declared level or roles
        # (Spec 17, cannot be overridden).
        if is_minor:
            level = PrivacyLevel.RESTRICTED

        decision = AccessDecision(
            allowed=True,
            effective_level=level,
            deny_response_mode=privacy.deny_response_mode,
            reason=f"access permitted at privacy level {level.value}",
        )
        self._audit(entity_id, caller, decision, is_person)
        return decision

    def _audit(
        self,
        entity_id: str,
        caller: CallerContext | None,
        decision: AccessDecision,
        is_person: bool,
    ) -> None:
        # Spec 7.1/17: log access for sensitive and restricted entities and for
        # ALL person entities regardless of access_logging_recommended.
        if not is_person and PRIVACY_RANK[decision.effective_level] < PRIVACY_RANK[
            PrivacyLevel.SENSITIVE
        ]:
            return
        self._logger.info(
            "mesa privacy access: entity=%s caller=%s allowed=%s level=%s",
            entity_id or "<unknown>",
            caller.caller_id if caller else "<no-context>",
            decision.allowed,
            decision.effective_level.value,
            extra={
                "mesa_entity_id": entity_id,
                "mesa_caller_id": caller.caller_id if caller else None,
                "mesa_roles": caller.effective_roles() if caller else [],
                "mesa_decision": "allowed" if decision.allowed else "denied",
                "mesa_effective_level": decision.effective_level.value,
                "mesa_is_person": is_person,
            },
        )
