"""Temporal constraint evaluation (Spec 6.5).

Invariants this module guarantees:
- Effects are tightening-only: a loosening control_mode effect is ignored with
  a warning, never applied.
- Unevaluable conditions are treated as ACTIVE regardless of any ``negate``
  flag (fail-closed): no evaluation failure can grant a permission.

v1 condition types: time_range, day_of_week, calendar_entity (via host
callback). solar_angle, duration, and relative_to_event are unevaluable in v1
and therefore fail closed.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from custom_components.atm.mesa_core.profile import CONTROL_MODE_RANK, ControlMode, OperationalBoundaries

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass
class TemporalResult:
    """Outcome of applying temporal constraints to an entity's boundaries."""

    boundaries: OperationalBoundaries
    active_limits: list[dict[str, Any]] = field(default_factory=list)
    active_constraint_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TemporalEvaluator:
    def __init__(
        self,
        get_state: Callable[[str], str | None] | None = None,
        get_calendar_events: Callable[[str], list[Any]] | None = None,
    ) -> None:
        self.get_state = get_state
        self.get_calendar_events = get_calendar_events

    # -- condition evaluation ------------------------------------------------

    def _eval_time_range(self, cond: dict[str, Any], now: datetime) -> bool | None:
        try:
            start = time.fromisoformat(cond["start_time"])
            end = time.fromisoformat(cond["end_time"])
        except (KeyError, ValueError):
            return None
        current = now.time()
        if start <= end:
            return start <= current < end
        # Midnight-crossing range, e.g. 23:00-06:00.
        return current >= start or current < end

    def _eval_day_of_week(self, cond: dict[str, Any], now: datetime) -> bool | None:
        days = cond.get("days")
        if not isinstance(days, list) or not days:
            return None
        return _WEEKDAYS[now.weekday()] in [str(d).lower() for d in days]

    def _eval_calendar(self, cond: dict[str, Any]) -> bool | None:
        calendar_id = cond.get("calendar_entity")
        if not calendar_id or self.get_calendar_events is None:
            return None
        try:
            return bool(self.get_calendar_events(str(calendar_id)))
        except Exception:
            return None

    def evaluate_condition(self, cond: dict[str, Any], now: datetime) -> bool | None:
        """Returns True/False, or None when the condition cannot be evaluated."""
        cond_type = cond.get("type")
        if cond_type == "time_range":
            result = self._eval_time_range(cond, now)
        elif cond_type == "day_of_week":
            result = self._eval_day_of_week(cond, now)
        elif cond_type == "calendar_entity":
            result = self._eval_calendar(cond)
        else:
            result = None  # solar_angle / duration / relative_to_event are v2
        if result is None:
            return None
        if cond.get("negate") is True:
            return not result
        return result

    # -- application ----------------------------------------------------------

    def apply(
        self, boundaries: OperationalBoundaries, current_time: datetime
    ) -> TemporalResult:
        result = TemporalResult(boundaries=copy.deepcopy(boundaries))
        for tc in boundaries.temporal_constraints:
            tc_id = str(tc.get("id", "<unnamed>"))
            cond = tc.get("condition") or {}
            effect = tc.get("effect") or {}
            evaluated = self.evaluate_condition(cond, current_time)
            if evaluated is None:
                # Spec 6.5: an unevaluable constraint is treated as active, not
                # ignored, regardless of negate. Fail-closed.
                result.warnings.append(
                    f"temporal constraint {tc_id!r}: condition could not be evaluated; "
                    "treated as active (fail-closed, Spec 6.5)"
                )
            elif not evaluated:
                continue
            result.active_constraint_ids.append(tc_id)

            if "control_mode" in effect:
                try:
                    effect_mode = ControlMode(effect["control_mode"])
                except ValueError:
                    result.warnings.append(
                        f"temporal constraint {tc_id!r}: invalid effect control_mode; ignored"
                    )
                    effect_mode = None
                if effect_mode is not None:
                    current_mode = result.boundaries.control_mode
                    if CONTROL_MODE_RANK[effect_mode] > CONTROL_MODE_RANK[current_mode]:
                        result.boundaries.control_mode = effect_mode
                    elif CONTROL_MODE_RANK[effect_mode] < CONTROL_MODE_RANK[current_mode]:
                        result.warnings.append(
                            f"temporal constraint {tc_id!r}: effect control_mode "
                            f"{effect_mode.value!r} would loosen the effective base "
                            f"{current_mode.value!r}; ignored (tightening-only, Spec 6.5)"
                        )

            if "service" in effect and "parameter" in effect:
                limit: dict[str, Any] = {
                    "id": tc_id,
                    "limit": {
                        "service": effect["service"],
                        "parameter": effect["parameter"],
                    },
                }
                for key in ("max_value", "min_value", "permitted_values"):
                    if key in effect:
                        limit["limit"][key] = effect[key]
                if "human_reason" in tc:
                    limit["human_reason"] = tc["human_reason"]
                result.active_limits.append(limit)
        return result
