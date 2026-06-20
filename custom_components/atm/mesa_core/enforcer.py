"""MesaEnforcer: service call evaluation (Module 4.4; Spec 4, 5.8, 6.4-6.6).

Evaluation order: resolve effective profile -> apply temporal constraints (so a
temporally tightened control_mode is honoured) -> privacy -> control_mode
(including the enforced-mode confirmation round-trip) -> declared limits.

The server-level ``mode`` interacts with per-profile ``enforcement_mode``: a
call is enforced when either is "enforced". ``read_only`` blocks regardless of
mode because it describes entity nature, not policy.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from custom_components.atm.mesa_core.inheritance import InheritanceResolver
from custom_components.atm.mesa_core.privacy import CallerContext, PrivacyEnforcer
from custom_components.atm.mesa_core.profile import (
    DOMAIN_SAFETY_BASELINE,
    ControlMode,
    MetadataOrigin,
    SemanticProfile,
)
from custom_components.atm.mesa_core.store import ProfileStore
from custom_components.atm.mesa_core.temporal import TemporalEvaluator

__all__ = [
    "DOMAIN_SAFETY_BASELINE",
    "ConfirmationManager",
    "EnforcementResult",
    "MesaEnforcer",
]

CHALLENGE_TTL_SECONDS = 120  # Spec 6.6: challenges SHOULD expire within 120 seconds.
INFERRED_CONFIDENCE_FLOOR = 0.7  # Spec 5.4 Rule 3.


@dataclass
class EnforcementResult:
    allowed: bool
    reason: str
    rule_applied: str | None
    entity_id: str
    effective_profile: SemanticProfile
    warnings: list[str] = field(default_factory=list)
    confirmation_challenge: dict[str, Any] | None = None


def _canonical_params(params: dict[str, Any] | None) -> str:
    return json.dumps(params or {}, sort_keys=True, default=str)


class ConfirmationManager:
    """Issues and redeems single-use confirmation challenges (Spec 6.6).

    A token is valid only for the exact entity, service, and parameters of the
    original challenge; expired or reused tokens are rejected.
    """

    def __init__(self, ttl_seconds: int = CHALLENGE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._challenges: dict[str, dict[str, Any]] = {}

    def issue(
        self,
        entity_id: str,
        service: str,
        params: dict[str, Any] | None,
        now: datetime,
    ) -> dict[str, Any]:
        challenge_id = uuid.uuid4().hex
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        self._challenges[challenge_id] = {
            "entity_id": entity_id,
            "service": service,
            "params": _canonical_params(params),
            "expires_at": expires_at,
            "used": False,
        }
        return {
            "challenge_id": challenge_id,
            "entity_id": entity_id,
            "service": service,
            "parameters": dict(params or {}),
            "expires_at": expires_at.isoformat(),
        }

    def redeem(
        self,
        token: dict[str, Any],
        entity_id: str,
        service: str,
        params: dict[str, Any] | None,
        now: datetime,
    ) -> tuple[bool, str]:
        challenge_id = token.get("challenge_id")
        if not isinstance(challenge_id, str):
            return False, "confirmation token missing challenge_id"
        record = self._challenges.get(challenge_id)
        if record is None:
            return False, "unknown or expired confirmation challenge"
        if record["used"]:
            return False, "confirmation token already used (single-use)"
        if now > record["expires_at"]:
            del self._challenges[challenge_id]
            return False, "confirmation challenge expired"
        if (
            record["entity_id"] != entity_id
            or record["service"] != service
            or record["params"] != _canonical_params(params)
        ):
            return False, (
                "confirmation token does not match this request: a token is valid "
                "only for the exact entity, service, and parameters challenged"
            )
        record["used"] = True
        return True, "confirmed"


def _compare(operator: str, state: str, value: Any) -> bool | None:
    """Evaluate a canonical predicate operator. None = unevaluable."""
    try:
        if operator in ("gt", "gte", "lt", "lte"):
            s, v = float(state), float(value)
            return {
                "gt": s > v,
                "gte": s >= v,
                "lt": s < v,
                "lte": s <= v,
            }[operator]
        if operator in ("eq", "neq"):
            if isinstance(value, bool):
                # HA states are strings; booleans map onto on/off conventions.
                matched = state.lower() in (("on", "true") if value else ("off", "false"))
            elif isinstance(value, int | float):
                matched = float(state) == float(value)
            else:
                matched = state == str(value)
            return matched if operator == "eq" else not matched
        if operator == "in":
            return any(state == str(item) for item in value)
        if operator == "contains":
            return str(value) in state
    except (TypeError, ValueError):
        return None
    return None


class MesaEnforcer:
    def __init__(
        self,
        store: ProfileStore,
        resolver: InheritanceResolver | None = None,
        *,
        mode: str = "enforced",
        interactive: bool = True,
        privacy_enforcer: PrivacyEnforcer | None = None,
        get_state: Callable[[str], str | None] | None = None,
        get_calendar_events: Callable[[str], list[Any]] | None = None,
        challenge_ttl_seconds: int = CHALLENGE_TTL_SECONDS,
    ) -> None:
        self.store = store
        self.resolver = resolver or InheritanceResolver(store=store)
        self.mode = mode
        self.interactive = interactive
        self.privacy = privacy_enforcer or PrivacyEnforcer()
        self.get_state = get_state
        self.temporal = TemporalEvaluator(
            get_state=get_state, get_calendar_events=get_calendar_events
        )
        self.confirmations = ConfirmationManager(ttl_seconds=challenge_ttl_seconds)

    # -- helpers -----------------------------------------------------------------

    def _is_enforced(self, profile_mode: str) -> bool:
        return self.mode == "enforced" or profile_mode == "enforced"

    def _is_minor(self, entity_id: str) -> bool:
        stored = self.store.get(entity_id)
        if stored is None or not stored.raw:
            return False
        traits = stored.raw.get("semantic_profile", {}).get("person_traits", {})
        return traits.get("is_minor") is True

    def _evaluate_predicate(
        self, predicate: dict[str, Any], warnings: list[str], limit_id: str
    ) -> bool:
        """True when the predicate (and therefore the limit) is active.

        Unevaluable predicates are treated as active, mirroring the temporal
        fail-closed rule: an evaluation failure must not disable a limit.
        """
        if predicate.get("type") == "ha_condition":
            warnings.append(
                f"declared limit {limit_id!r}: ha_condition predicates require host "
                "evaluation; treated as active (fail-closed)"
            )
            return True
        entity = predicate.get("entity")
        if self.get_state is None or not entity:
            warnings.append(
                f"declared limit {limit_id!r}: predicate cannot be evaluated without "
                "a get_state callback; treated as active (fail-closed)"
            )
            return True
        state = self.get_state(str(entity))
        if state is None:
            warnings.append(
                f"declared limit {limit_id!r}: entity {entity!r} unavailable; "
                "treated as active (fail-closed)"
            )
            return True
        outcome = _compare(str(predicate.get("operator")), state, predicate.get("value"))
        if outcome is None:
            warnings.append(
                f"declared limit {limit_id!r}: predicate could not be evaluated; "
                "treated as active (fail-closed)"
            )
            return True
        return outcome

    def _check_limit(
        self,
        limit: dict[str, Any],
        service: str,
        service_params: dict[str, Any],
    ) -> str | None:
        """Returns a violation description, or None when the call is within limits."""
        spec = limit.get("limit") or {}
        if spec.get("service") != service:
            return None
        parameter = spec.get("parameter")
        if parameter not in service_params:
            return None
        value = service_params[parameter]
        human_reason = limit.get("human_reason") or "declared limit"
        if "max_value" in spec:
            try:
                if float(value) > float(spec["max_value"]):
                    return (
                        f"{parameter}={value} exceeds max_value {spec['max_value']}: "
                        f"{human_reason}"
                    )
            except (TypeError, ValueError):
                return f"{parameter}={value!r} is not comparable to max_value: {human_reason}"
        if "min_value" in spec:
            try:
                if float(value) < float(spec["min_value"]):
                    return (
                        f"{parameter}={value} is below min_value {spec['min_value']}: "
                        f"{human_reason}"
                    )
            except (TypeError, ValueError):
                return f"{parameter}={value!r} is not comparable to min_value: {human_reason}"
        if "permitted_values" in spec:
            permitted = spec["permitted_values"]
            if not any(str(value) == str(item) for item in permitted):
                return f"{parameter}={value!r} is not a permitted value: {human_reason}"
        return None

    # -- evaluation -----------------------------------------------------------------

    def evaluate(
        self,
        entity_id: str,
        service: str,
        service_params: dict[str, Any] | None = None,
        caller_context: CallerContext | None = None,
        current_time: datetime | None = None,
        confirmation_token: dict[str, Any] | None = None,
    ) -> EnforcementResult:
        now = current_time or datetime.now()
        service_params = service_params or {}
        explanation = self.resolver.explain(entity_id)
        profile = explanation.effective_profile
        warnings = list(explanation.warnings)

        def blocked(reason: str, rule: str) -> EnforcementResult:
            return EnforcementResult(
                allowed=False,
                reason=reason,
                rule_applied=rule,
                entity_id=entity_id,
                effective_profile=profile,
                warnings=warnings,
            )

        # 1. Temporal constraints first, so a temporally tightened control_mode
        #    is what gets evaluated below.
        temporal = self.temporal.apply(profile.operational_boundaries, now)
        boundaries = temporal.boundaries
        warnings.extend(temporal.warnings)

        # 2. Privacy.
        is_person = profile.domain == "person"
        decision = self.privacy.evaluate(
            profile.privacy_classification,
            caller_context,
            entity_id=entity_id,
            is_person=is_person,
            is_minor=self._is_minor(entity_id),
        )
        if not decision.allowed:
            return blocked(decision.reason, "privacy:deny_for")
        if (
            decision.effective_level.value == "restricted"
            and boundaries.control_mode == ControlMode.AUTONOMOUS
        ):
            # Restricted entities may not be acted on autonomously (Spec 7.1).
            boundaries.control_mode = ControlMode.CONFIRM
            warnings.append(
                "privacy level restricted: autonomous action not permitted; "
                "confirmation required (Spec 7.1)"
            )

        # 3. Low-confidence inferred profiles are surfaced (Spec 5.4 Rule 3).
        if (
            profile.metadata.source == MetadataOrigin.INFERRED_AI
            and profile.effective_confidence() < INFERRED_CONFIDENCE_FLOOR
        ):
            warnings.append(
                f"effective profile is inferred_ai with confidence "
                f"{profile.effective_confidence():.2f} < {INFERRED_CONFIDENCE_FLOOR} "
                "(Spec 5.4 Rule 3)"
            )

        # 4. control_mode.
        mode = boundaries.control_mode
        reason_suffix = boundaries.control_reason or entity_id
        enforced = self._is_enforced(boundaries.enforcement_mode)
        if mode == ControlMode.READ_ONLY:
            # Entity nature, not policy: blocks regardless of enforcement mode.
            return blocked(
                f"Entity is read-only by nature: {reason_suffix}", "control_mode:read_only"
            )
        if mode == ControlMode.PROHIBITED:
            if enforced:
                return blocked(
                    f"Entity is prohibited by policy: {reason_suffix}",
                    "control_mode:prohibited",
                )
            warnings.append(
                f"advisory: entity is prohibited by MESA policy ({reason_suffix}); "
                "the call is not blocked because enforcement is advisory"
            )
        if mode == ControlMode.CONFIRM:
            if not self.interactive:
                # No interaction channel: confirm is blocked for all domains
                # (Spec 4). Operators pre-authorise via deployment_defaults or
                # the Rule A loosening override.
                return blocked(
                    f"Entity requires confirmation but no interaction channel exists: "
                    f"{reason_suffix}",
                    "control_mode:confirm_no_channel",
                )
            if enforced:
                if confirmation_token is not None:
                    ok, message = self.confirmations.redeem(
                        confirmation_token, entity_id, service, service_params, now
                    )
                    if not ok:
                        return blocked(message, "control_mode:confirm")
                    warnings.append(
                        "confirmation accepted"
                        + (
                            f" (approved_by={confirmation_token.get('approved_by')})"
                            if confirmation_token.get("approved_by")
                            else ""
                        )
                    )
                else:
                    challenge = self.confirmations.issue(entity_id, service, service_params, now)
                    result = blocked(
                        f"Confirmation required: {reason_suffix}. Present this action to "
                        "the user and re-submit with the confirmation_token.",
                        "control_mode:confirm",
                    )
                    result.confirmation_challenge = challenge
                    return result
            else:
                warnings.append(
                    f"confirmation required before acting (advisory): {reason_suffix}"
                )

        # 5. Declared limits (profile limits plus active temporal value constraints).
        all_limits = list(boundaries.declared_limits) + temporal.active_limits
        for limit in all_limits:
            limit_id = str(limit.get("id", "<unnamed>"))
            if "predicate" in limit and not self._evaluate_predicate(
                limit["predicate"], warnings, limit_id
            ):
                continue
            violation = self._check_limit(limit, service, service_params)
            if violation is not None:
                if enforced:
                    return blocked(violation, f"declared_limit:{limit_id}")
                warnings.append(f"advisory: {violation}")

        return EnforcementResult(
            allowed=True,
            reason="permitted",
            rule_applied=None,
            entity_id=entity_id,
            effective_profile=profile,
            warnings=warnings,
        )

    async def aevaluate(
        self,
        entity_id: str,
        service: str,
        service_params: dict[str, Any] | None = None,
        caller_context: CallerContext | None = None,
        current_time: datetime | None = None,
        confirmation_token: dict[str, Any] | None = None,
    ) -> EnforcementResult:
        return await asyncio.to_thread(
            self.evaluate,
            entity_id,
            service,
            service_params,
            caller_context,
            current_time,
            confirmation_token,
        )
