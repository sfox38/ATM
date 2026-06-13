"""Inheritance resolution: defaults -> domain -> area -> entity (Spec 5.6, 5.8).

The InheritanceResolver gathers the declared layers for an entity, merges them
through the ConflictResolver (Rules A-E), and fills undeclared kernel fields
from deployment defaults or the built-in domain safety baseline (Rule E:
defaults apply only when no profile at any level declares the field).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from custom_components.atm.mesa_core.conflict import ConflictResolver, FieldExplanation, Layer
from custom_components.atm.mesa_core.profile import (
    PrivacyLevel,
    ProfileMetadata,
    SemanticProfile,
    baseline_control_mode,
    baseline_triggers_automations,
)

if TYPE_CHECKING:
    from custom_components.atm.mesa_core.store import ProfileStore


@dataclass
class ProfileExplanation:
    """Full inheritance resolution path for an entity (Spec 9.5)."""

    entity_id: str
    effective_profile: SemanticProfile
    explanation: list[FieldExplanation] = field(default_factory=list)
    conflicts_detected: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, show_conflicts: bool = True) -> dict[str, Any]:
        entries = []
        for entry in self.explanation:
            d = entry.to_dict()
            if not show_conflicts:
                d.pop("competing_values", None)
            entries.append(d)
        return {
            "entity_id": self.entity_id,
            "effective_profile": self.effective_profile.to_dict(),
            "explanation": entries,
            "conflicts_detected": self.conflicts_detected,
            "warnings": list(self.warnings),
        }


class InheritanceResolver:
    """Resolves effective profiles for entities.

    Host callbacks supply the HA registry knowledge mesa-core does not have:
    ``get_entity_area`` maps an entity ID to its area ID (None when unassigned)
    and ``get_entity_domain`` maps an entity ID to the domain whose domain-level
    profile applies (defaults to the entity ID prefix).
    """

    def __init__(
        self,
        store: ProfileStore,
        get_entity_area: Callable[[str], str | None] | None = None,
        get_entity_domain: Callable[[str], str] | None = None,
    ) -> None:
        self.store = store
        self.get_entity_area = get_entity_area or store.get_entity_area
        self.get_entity_domain = get_entity_domain or (lambda eid: eid.split(".", 1)[0])
        self._conflicts = ConflictResolver()

    def _gather_layers(self, entity_id: str) -> list[Layer]:
        layers: list[Layer] = []
        entity_profile = self.store.get(entity_id)
        if entity_profile is not None:
            layers.append(Layer("entity", entity_profile))
        if self.get_entity_area is not None:
            area_id = self.get_entity_area(entity_id)
            if area_id is not None:
                area_profile = self.store.get_area_profile(area_id)
                if area_profile is not None:
                    layers.append(Layer("area", area_profile))
        domain = self.get_entity_domain(entity_id)
        domain_profile = self.store.get_domain_profile(domain)
        if domain_profile is not None:
            layers.append(Layer("domain", domain_profile))
        return layers

    def has_profile(self, entity_id: str) -> bool:
        """Whether any profile is declared for this entity at any inheritance level."""
        return bool(self._gather_layers(entity_id))

    def explain(self, entity_id: str) -> ProfileExplanation:
        layers = self._gather_layers(entity_id)
        effective, resolution = self._conflicts.resolve(entity_id, layers)
        domain = self.get_entity_domain(entity_id)
        defaults = self.store.get_deployment_defaults()

        declared_paths = {e.field_path for e in resolution.explanations}

        # Rule E default filling for the kernel policy fields.
        if "operational_boundaries.control_mode" not in declared_paths:
            if defaults is not None:
                mode = defaults.control_mode_for(domain)
                level = "deployment_default"
                origin = "user"
            else:
                mode = baseline_control_mode(domain)
                level = "built_in_baseline"
                origin = "unknown"
            effective.operational_boundaries.control_mode = mode
            resolution.explanations.append(
                FieldExplanation(
                    field_path="operational_boundaries.control_mode",
                    effective_value=mode.value,
                    provided_by_level=level,
                    provided_by_origin=origin,
                )
            )

        if "operational_boundaries.triggers_automations" not in declared_paths:
            if defaults is not None:
                triggers = defaults.triggers_for(domain)
                level = "deployment_default"
                origin = "user"
            else:
                triggers = baseline_triggers_automations(domain)
                level = "built_in_baseline"
                origin = "unknown"
            effective.operational_boundaries.triggers_automations = triggers
            resolution.explanations.append(
                FieldExplanation(
                    field_path="operational_boundaries.triggers_automations",
                    effective_value=triggers.value,
                    provided_by_level=level,
                    provided_by_origin=origin,
                )
            )

        if "privacy_classification.level" not in declared_paths:
            # Person entities MUST be treated as sensitive by default (Spec 17).
            privacy = PrivacyLevel.SENSITIVE if domain == "person" else PrivacyLevel.NORMAL
            effective.privacy_classification.level = privacy
            resolution.explanations.append(
                FieldExplanation(
                    field_path="privacy_classification.level",
                    effective_value=privacy.value,
                    provided_by_level="built_in_baseline",
                    provided_by_origin="unknown",
                )
            )

        if not layers:
            effective.metadata = ProfileMetadata()

        return ProfileExplanation(
            entity_id=entity_id,
            effective_profile=effective,
            explanation=resolution.explanations,
            conflicts_detected=resolution.conflicts_detected,
            warnings=resolution.warnings,
        )

    def resolve(self, entity_id: str) -> SemanticProfile:
        """Return the fully resolved effective profile for an entity."""
        return self.explain(entity_id).effective_profile
