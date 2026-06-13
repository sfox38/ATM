"""Global profile conflict resolution: Rules A-E (Spec 5.7).

Operates on declared fields only (``SemanticProfile.declared``): absence is
inherited, never defaulted here (Rule E). Defaults for undeclared kernel fields
are applied by the InheritanceResolver from deployment defaults or the built-in
baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from custom_components.atm.mesa_core.profile import (
    CONTROL_MODE_RANK,
    HELPER_DOMAINS,
    PRIVACY_RANK,
    TRUSTED_ORIGINS,
    ControlMode,
    MetadataOrigin,
    PrivacyLevel,
    SemanticProfile,
    TriggersAutomations,
)

SCOPE_RANK = {"entity": 3, "area": 2, "domain": 1}

# Rule D fields on operational_boundaries (everything not covered by Rules A/B).
_OB_RULE_D_FIELDS = (
    "reversible",
    "reversibility_cost",
    "reversibility_note",
    "reversibility_window_seconds",
    "idempotent",
    "state_persistence",
    "expected_latency_ms",
    "side_effect_scope",
    "state_volatility",
    "enforcement_mode",
    "control_reason",
    "human_reason",
    "declared_limits",
    "temporal_constraints",
)

_PRIVACY_RULE_D_FIELDS = (
    "contains_presence_data",
    "contains_audio_capture",
    "contains_visual_capture",
    "contains_biometric_data",
    "contains_behavioural_data",
    "data_retention_local",
    "access_logging_recommended",
    "access_roles",
    "deny_response_mode",
    "privacy_note",
)


@dataclass
class Layer:
    """One inheritance level's profile: level is 'entity', 'area', or 'domain'."""

    level: str
    profile: SemanticProfile


@dataclass
class FieldExplanation:
    """One entry of the mesa_explain_profile output (Spec 9.5)."""

    field_path: str
    effective_value: Any
    provided_by_level: str
    provided_by_origin: str
    conflict: bool = False
    conflict_resolution: str | None = None
    competing_values: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "field_path": self.field_path,
            "effective_value": self.effective_value,
            "provided_by_level": self.provided_by_level,
            "provided_by_origin": self.provided_by_origin,
            "conflict": self.conflict,
        }
        if self.conflict_resolution is not None:
            out["conflict_resolution"] = self.conflict_resolution
        if self.competing_values is not None:
            out["competing_values"] = self.competing_values
        return out


@dataclass
class Resolution:
    """Outcome of merging the declared layers (before default filling)."""

    explanations: list[FieldExplanation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def conflicts_detected(self) -> bool:
        return any(e.conflict for e in self.explanations)


@dataclass
class _Candidate:
    layer: Layer
    value: Any

    @property
    def scope_rank(self) -> int:
        return SCOPE_RANK.get(self.layer.level, 0)

    @property
    def origin(self) -> MetadataOrigin:
        return self.layer.profile.metadata.source

    @property
    def origin_authority(self) -> int:
        from custom_components.atm.mesa_core.profile import ORIGIN_AUTHORITY

        return ORIGIN_AUTHORITY[self.origin]

    def describe(self) -> dict[str, Any]:
        value = self.value
        if hasattr(value, "value"):
            value = value.value
        return {"level": self.layer.level, "origin": self.origin.value, "value": value}


def _is_confirmed(profile: SemanticProfile, path: str) -> bool:
    return path in profile.metadata.confirmed_fields


def _coerced_control_mode(layer: Layer) -> tuple[ControlMode, str | None]:
    """Apply the untrusted-origin coercion of Spec 5.4 Rules 3/8.

    Profiles of inferred_ai/unknown origin, and unconfirmed hybrid fields, may
    not assert autonomous: it is read as confirm.
    """
    profile = layer.profile
    mode = profile.operational_boundaries.control_mode
    human_authored = profile.metadata.source in (
        MetadataOrigin.DEVELOPER,
        MetadataOrigin.USER,
    )
    if (
        not human_authored
        and mode == ControlMode.AUTONOMOUS
        and not _is_confirmed(profile, "operational_boundaries.control_mode")
    ):
        return ControlMode.CONFIRM, (
            f"{profile.entity_id}: unconfirmed {profile.metadata.source.value} profile asserted "
            "control_mode: autonomous; read as confirm (Spec 5.4 Rule 8)"
        )
    return mode, None


def _coerced_triggers(layer: Layer, domain: str) -> tuple[TriggersAutomations, str | None]:
    """Apply the helper-domain coercion of Spec 5.4 Rule 9."""
    profile = layer.profile
    value = profile.operational_boundaries.triggers_automations
    human_authored = profile.metadata.source in (MetadataOrigin.DEVELOPER, MetadataOrigin.USER)
    if (
        not human_authored
        and domain in HELPER_DOMAINS
        and value == TriggersAutomations.NONE
        and not _is_confirmed(profile, "operational_boundaries.triggers_automations")
    ):
        return TriggersAutomations.LIKELY, (
            f"{profile.entity_id}: unconfirmed {profile.metadata.source.value} helper profile "
            "asserted triggers_automations: none; read as likely (Spec 5.4 Rule 9)"
        )
    return value, None


class ConflictResolver:
    """Implements Rules A-E over an ordered set of inheritance layers."""

    # -- Rule A: control_mode -------------------------------------------------

    def resolve_control_mode(
        self, layers: list[Layer], resolution: Resolution
    ) -> ControlMode | None:
        candidates: list[_Candidate] = []
        for layer in layers:
            if not layer.profile.declared("operational_boundaries.control_mode"):
                continue
            mode, warning = _coerced_control_mode(layer)
            if warning:
                resolution.warnings.append(warning)
            candidates.append(_Candidate(layer, mode))
        if not candidates:
            return None

        # Rule A exception: the operator loosening override. Valid only at entity
        # scope, user origin, control_mode autonomous, with control_reason.
        override: _Candidate | None = None
        for cand in candidates:
            ob = cand.layer.profile.operational_boundaries
            if not ob.override_control_mode:
                continue
            valid = (
                cand.layer.level == "entity"
                and cand.origin == MetadataOrigin.USER
                and cand.value == ControlMode.AUTONOMOUS
                and bool(ob.control_reason)
            )
            if valid:
                override = cand
            else:
                resolution.warnings.append(
                    f"{cand.layer.profile.entity_id}: override_control_mode is malformed "
                    "(requires entity scope, user origin, control_mode: autonomous, and "
                    "control_reason); ignored (Spec 5.7 Rule A)"
                )

        hard = [c for c in candidates if CONTROL_MODE_RANK[c.value] >= 2]
        if hard:
            # prohibited / read_only can never be loosened; read_only wins the tie.
            winner = next(
                (c for c in hard if c.value == ControlMode.READ_ONLY),
                max(hard, key=lambda c: (c.scope_rank, c.origin_authority)),
            )
            effective: ControlMode = winner.value
            reason = "Rule A: prohibited/read_only is never loosened"
            if override is not None:
                resolution.warnings.append(
                    f"{override.layer.profile.entity_id}: loosening override cannot loosen "
                    f"{effective.value}; ignored (Spec 5.7 Rule A)"
                )
        elif override is not None:
            winner = override
            effective = ControlMode.AUTONOMOUS
            reason = "Rule A exception: operator loosening override applied"
        else:
            winner = max(
                candidates, key=lambda c: (CONTROL_MODE_RANK[c.value], c.scope_rank)
            )
            effective = winner.value
            reason = f"Rule A: most restrictive value wins ({effective.value})"

        distinct = {c.value for c in candidates}
        resolution.explanations.append(
            FieldExplanation(
                field_path="operational_boundaries.control_mode",
                effective_value=effective.value,
                provided_by_level=winner.layer.level,
                provided_by_origin=winner.origin.value,
                conflict=len(distinct) > 1,
                conflict_resolution=reason if len(distinct) > 1 else None,
                competing_values=(
                    [c.describe() for c in candidates] if len(distinct) > 1 else None
                ),
            )
        )
        return effective

    # -- Rule B: triggers_automations ------------------------------------------

    def resolve_triggers(
        self, layers: list[Layer], domain: str, resolution: Resolution
    ) -> TriggersAutomations | None:
        candidates: list[_Candidate] = []
        for layer in layers:
            if not layer.profile.declared("operational_boundaries.triggers_automations"):
                continue
            value, warning = _coerced_triggers(layer, domain)
            if warning:
                resolution.warnings.append(warning)
            candidates.append(_Candidate(layer, value))
        if not candidates:
            return None

        # Entity-level override of a sticky likely (Spec 6.1: requires human_reason;
        # value must be none or deployment_defined).
        override: _Candidate | None = None
        for cand in candidates:
            ob = cand.layer.profile.operational_boundaries
            if not ob.override_triggers_automations:
                continue
            valid = (
                cand.layer.level == "entity"
                and cand.value
                in (TriggersAutomations.NONE, TriggersAutomations.DEPLOYMENT_DEFINED)
                and bool(ob.human_reason)
            )
            if valid:
                override = cand
            else:
                resolution.warnings.append(
                    f"{cand.layer.profile.entity_id}: override_triggers_automations is "
                    "malformed (requires entity scope, human_reason, and a value of none "
                    "or deployment_defined); ignored (Spec 6.1)"
                )

        likely = [c for c in candidates if c.value == TriggersAutomations.LIKELY]
        if override is not None:
            winner = override
            effective: TriggersAutomations = override.value
            reason = "Rule B: entity-level override with human_reason"
        elif likely:
            winner = max(likely, key=lambda c: (c.scope_rank, c.origin_authority))
            effective = TriggersAutomations.LIKELY
            reason = "Rule B: likely is sticky upward"
        else:
            winner = max(candidates, key=lambda c: (c.scope_rank, c.origin_authority))
            effective = winner.value
            reason = "Rule B: most specific declaration wins (no likely present)"

        distinct = {c.value for c in candidates}
        resolution.explanations.append(
            FieldExplanation(
                field_path="operational_boundaries.triggers_automations",
                effective_value=effective.value,
                provided_by_level=winner.layer.level,
                provided_by_origin=winner.origin.value,
                conflict=len(distinct) > 1,
                conflict_resolution=reason if len(distinct) > 1 else None,
                competing_values=(
                    [c.describe() for c in candidates] if len(distinct) > 1 else None
                ),
            )
        )
        return effective

    # -- Rule C: privacy level ---------------------------------------------------

    def resolve_privacy_level(
        self, layers: list[Layer], resolution: Resolution
    ) -> PrivacyLevel | None:
        candidates = [
            _Candidate(layer, layer.profile.privacy_classification.level)
            for layer in layers
            if layer.profile.declared("privacy_classification.level")
        ]
        if not candidates:
            return None
        winner = max(
            candidates, key=lambda c: (PRIVACY_RANK[c.value], c.scope_rank, c.origin_authority)
        )
        effective_level: PrivacyLevel = winner.value
        distinct = {c.value for c in candidates}
        resolution.explanations.append(
            FieldExplanation(
                field_path="privacy_classification.level",
                effective_value=winner.value.value,
                provided_by_level=winner.layer.level,
                provided_by_origin=winner.origin.value,
                conflict=len(distinct) > 1,
                conflict_resolution=(
                    "Rule C: most restrictive privacy level wins" if len(distinct) > 1 else None
                ),
                competing_values=(
                    [c.describe() for c in candidates] if len(distinct) > 1 else None
                ),
            )
        )
        return effective_level

    # -- Rule D: everything else ---------------------------------------------------

    def resolve_rule_d(
        self,
        layers: list[Layer],
        path: str,
        getter_attr: str,
        container: str,
        resolution: Resolution,
    ) -> tuple[bool, Any]:
        """Resolve one Rule D field. Returns (was_declared, effective_value)."""
        candidates: list[_Candidate] = []
        for layer in layers:
            if not layer.profile.declared(path):
                continue
            obj = getattr(layer.profile, container)
            candidates.append(_Candidate(layer, getattr(obj, getter_attr)))
        if not candidates:
            return False, None

        trusted = [c for c in candidates if c.origin in TRUSTED_ORIGINS]
        pool = trusted if trusted else candidates
        winner = max(pool, key=lambda c: (c.scope_rank, c.origin_authority))

        def _comparable(v: Any) -> Any:
            return v.value if hasattr(v, "value") else v

        distinct_values = {repr(_comparable(c.value)) for c in candidates}
        conflict = len(distinct_values) > 1
        if conflict:
            if trusted and len(trusted) < len(candidates):
                reason = "Rule D: lower-tier declaration never overrides trusted tier"
            elif len({c.scope_rank for c in pool}) > 1:
                reason = "Rule D: most specific scope wins among trusted origins"
            else:
                reason = "Rule D: origin authority tiebreak at equal scope"
        else:
            reason = None
        resolution.explanations.append(
            FieldExplanation(
                field_path=path,
                effective_value=_comparable(winner.value),
                provided_by_level=winner.layer.level,
                provided_by_origin=winner.origin.value,
                conflict=conflict,
                conflict_resolution=reason,
                competing_values=[c.describe() for c in candidates] if conflict else None,
            )
        )
        return True, winner.value

    # -- full merge -------------------------------------------------------------

    def resolve(
        self, entity_id: str, layers: list[Layer]
    ) -> tuple[SemanticProfile, Resolution]:
        """Merge declared layers into a single effective profile (no default filling)."""
        resolution = Resolution()
        domain = entity_id.split(".", 1)[0]
        effective = SemanticProfile(entity_id=entity_id)

        mode = self.resolve_control_mode(layers, resolution)
        if mode is not None:
            effective.operational_boundaries.control_mode = mode
        triggers = self.resolve_triggers(layers, domain, resolution)
        if triggers is not None:
            effective.operational_boundaries.triggers_automations = triggers
        level = self.resolve_privacy_level(layers, resolution)
        if level is not None:
            effective.privacy_classification.level = level

        for attr in _OB_RULE_D_FIELDS:
            declared, value = self.resolve_rule_d(
                layers,
                f"operational_boundaries.{attr}",
                attr,
                "operational_boundaries",
                resolution,
            )
            if declared:
                setattr(effective.operational_boundaries, attr, value)

        for attr in _PRIVACY_RULE_D_FIELDS:
            declared, value = self.resolve_rule_d(
                layers,
                f"privacy_classification.{attr}",
                attr,
                "privacy_classification",
                resolution,
            )
            if declared:
                setattr(effective.privacy_classification, attr, value)

        # Effective tags are the union across levels (Spec 9.2).
        tags: list[str] = []
        for layer in sorted(layers, key=lambda layer: -SCOPE_RANK.get(layer.level, 0)):
            for tag in layer.profile.semantic_tags:
                if tag not in tags:
                    tags.append(tag)
        effective.semantic_tags = tags

        # diagnostic_profile: Rule D-style pick (most specific declaring layer).
        diag_layers = [
            Layer(layer.level, layer.profile)
            for layer in layers
            if layer.profile.diagnostic_profile is not None
        ]
        if diag_layers:
            best = max(
                diag_layers,
                key=lambda layer: (
                    SCOPE_RANK.get(layer.level, 0),
                    layer.profile.metadata.source in TRUSTED_ORIGINS,
                ),
            )
            effective.diagnostic_profile = best.profile.diagnostic_profile

        # Effective metadata: the most specific contributing layer's provenance.
        if layers:
            most_specific = max(layers, key=lambda layer: SCOPE_RANK.get(layer.level, 0))
            effective.metadata = most_specific.profile.metadata
            for layer in layers:
                resolution.warnings.extend(layer.profile.parse_warnings)

        return effective, resolution

    def merge(
        self, higher_authority_profile: SemanticProfile, lower_authority_profile: SemanticProfile
    ) -> SemanticProfile:
        """Merge two profiles, treating the first as the more specific scope."""
        layers = [
            Layer("entity", higher_authority_profile),
            Layer("domain", lower_authority_profile),
        ]
        effective, _ = self.resolve(higher_authority_profile.entity_id, layers)
        return effective
