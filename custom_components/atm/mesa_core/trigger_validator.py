"""TriggerValidator: live validation of triggers_automations declarations (Spec 5.5).

Cross-references profiles declaring ``triggers_automations: none`` against the
actual HA automation configurations supplied by the host. An entity declared
``none`` that appears in an automation trigger or condition block is a stale
and unsafe declaration: agents will skip cascade caution for it.

mesa-core never calls HA: the host provides automation configs through the
``get_automation_configs`` callback, from any source (REST API, YAML parse, or
test fixture).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from custom_components.atm.mesa_core.profile import TriggersAutomations
from custom_components.atm.mesa_core.store import ProfileStore

# HA configs use singular and plural section keys depending on age and editor.
_SECTION_KEYS = {
    "trigger": ("trigger", "triggers"),
    "condition": ("condition", "conditions"),
    "action": ("action", "actions"),
}


@dataclass
class ValidationIssue:
    entity_id: str
    declared_value: str
    automation_id: str
    role: str  # "trigger", "condition", or "action"
    severity: str  # "error" or "warning"
    recommendation: str


def _collect_entity_ids(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "entity_id":
                if isinstance(value, str):
                    found.add(value)
                elif isinstance(value, list):
                    found.update(v for v in value if isinstance(v, str))
            else:
                _collect_entity_ids(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_entity_ids(item, found)


def _entities_by_role(config: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for role, keys in _SECTION_KEYS.items():
        found: set[str] = set()
        for key in keys:
            if key in config:
                _collect_entity_ids(config[key], found)
        result[role] = found
    return result


class TriggerValidator:
    def __init__(self, store: ProfileStore) -> None:
        self.store = store

    def _declared_none_entities(self) -> list[str]:
        entities = []
        for key in self.store.entity_keys():
            profile = self.store.get(key)
            if (
                profile is not None
                and profile.declared("operational_boundaries.triggers_automations")
                and profile.operational_boundaries.triggers_automations
                == TriggersAutomations.NONE
            ):
                entities.append(key)
        return entities

    def _issues_for(
        self, entity_id: str, configs: list[dict[str, Any]]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for config in configs:
            automation_id = str(config.get("id", "<unknown>"))
            by_role = _entities_by_role(config)
            # Only trigger and condition references invalidate a none declaration
            # (Spec 5.5): an entity written by an action does not trigger automations.
            for role, severity in (("trigger", "error"), ("condition", "warning")):
                if entity_id in by_role[role]:
                    issues.append(
                        ValidationIssue(
                            entity_id=entity_id,
                            declared_value="none",
                            automation_id=automation_id,
                            role=role,
                            severity=severity,
                            recommendation=(
                                f"{entity_id} is declared triggers_automations: none but "
                                f"appears in the {role} block of {automation_id}. "
                                "Change the declaration to 'likely', or to "
                                "'deployment_defined' with affected_automations listing "
                                "this automation."
                            ),
                        )
                    )
        return issues

    def validate(
        self, get_automation_configs: Callable[[], list[dict[str, Any]]]
    ) -> list[ValidationIssue]:
        """Cross-reference all ``none`` declarations against the automation registry."""
        configs = get_automation_configs()
        issues: list[ValidationIssue] = []
        for entity_id in self._declared_none_entities():
            issues.extend(self._issues_for(entity_id, configs))
        return issues

    def validate_entity(
        self,
        entity_id: str,
        get_automation_configs: Callable[[], list[dict[str, Any]]],
    ) -> list[ValidationIssue]:
        """Validate a single entity against the automation registry."""
        profile = self.store.get(entity_id)
        if (
            profile is None
            or not profile.declared("operational_boundaries.triggers_automations")
            or profile.operational_boundaries.triggers_automations != TriggersAutomations.NONE
        ):
            return []
        return self._issues_for(entity_id, get_automation_configs())
