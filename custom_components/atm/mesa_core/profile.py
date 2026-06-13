"""MESA profile data model (Spec Sections 4-7; Module Proposal Section 4.1).

Profiles are treated as immutable after parsing. ``raw`` retains the original
document (root form), including fields this version does not model, so unknown
fields survive round-trips (Spec Section 23 forward compatibility).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from custom_components.atm.mesa_core import validation
from custom_components.atm.mesa_core.exceptions import MesaValidationError


class ControlMode(StrEnum):
    AUTONOMOUS = "autonomous"
    CONFIRM = "confirm"
    READ_ONLY = "read_only"
    PROHIBITED = "prohibited"


class TriggersAutomations(StrEnum):
    LIKELY = "likely"
    NONE = "none"
    UNKNOWN = "unknown"
    DEPLOYMENT_DEFINED = "deployment_defined"


class PrivacyLevel(StrEnum):
    PUBLIC = "public"
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class MetadataOrigin(StrEnum):
    DEVELOPER = "developer"
    USER = "user"
    HYBRID = "hybrid"
    INFERRED_AI = "inferred_ai"
    UNKNOWN = "unknown"


# Rule A restrictiveness ranking. read_only ties with prohibited but wins the tie
# because it describes entity nature rather than operator policy (Spec Section 4).
CONTROL_MODE_RANK: dict[ControlMode, int] = {
    ControlMode.AUTONOMOUS: 0,
    ControlMode.CONFIRM: 1,
    ControlMode.PROHIBITED: 2,
    ControlMode.READ_ONLY: 2,
}

PRIVACY_RANK: dict[PrivacyLevel, int] = {
    PrivacyLevel.PUBLIC: 0,
    PrivacyLevel.NORMAL: 1,
    PrivacyLevel.SENSITIVE: 2,
    PrivacyLevel.RESTRICTED: 3,
}

# Rule D authority within equal scope (Spec 5.7).
ORIGIN_AUTHORITY: dict[MetadataOrigin, int] = {
    MetadataOrigin.DEVELOPER: 4,
    MetadataOrigin.USER: 3,
    MetadataOrigin.HYBRID: 2,
    MetadataOrigin.INFERRED_AI: 1,
    MetadataOrigin.UNKNOWN: 0,
}

TRUSTED_ORIGINS: frozenset[MetadataOrigin] = frozenset(
    {MetadataOrigin.DEVELOPER, MetadataOrigin.USER, MetadataOrigin.HYBRID}
)

HELPER_DOMAINS: frozenset[str] = frozenset(
    {
        "input_boolean",
        "input_select",
        "input_number",
        "input_text",
        "input_datetime",
        "counter",
        "timer",
    }
)

# Built-in domain safety baseline (Spec 5.8). Applies only when an entity has no
# profile at any inheritance level and no deployment_defaults are configured.
DOMAIN_SAFETY_BASELINE: dict[str, ControlMode] = {
    "light": ControlMode.AUTONOMOUS,
    "media_player": ControlMode.CONFIRM,
    "input_select": ControlMode.CONFIRM,
    "switch": ControlMode.CONFIRM,
    "cover": ControlMode.CONFIRM,
    "climate": ControlMode.CONFIRM,
    "lock": ControlMode.PROHIBITED,
    "alarm_control_panel": ControlMode.PROHIBITED,
    "input_boolean": ControlMode.CONFIRM,
    "script": ControlMode.CONFIRM,
    "scene": ControlMode.CONFIRM,
}


def baseline_control_mode(domain: str) -> ControlMode:
    return DOMAIN_SAFETY_BASELINE.get(domain, ControlMode.CONFIRM)


def baseline_triggers_automations(domain: str) -> TriggersAutomations:
    if domain in HELPER_DOMAINS:
        return TriggersAutomations.LIKELY
    return TriggersAutomations.UNKNOWN


@dataclass
class PrivacyClassification:
    level: PrivacyLevel = PrivacyLevel.NORMAL
    contains_presence_data: bool = False
    contains_audio_capture: bool = False
    contains_visual_capture: bool = False
    contains_biometric_data: bool = False
    contains_behavioural_data: bool = False
    data_retention_local: bool | None = None
    access_logging_recommended: bool | None = None
    access_roles: dict[str, list[str]] | None = None
    deny_response_mode: str = "omit"
    privacy_note: str | None = None


@dataclass
class OperationalBoundaries:
    control_mode: ControlMode = ControlMode.CONFIRM
    triggers_automations: TriggersAutomations = TriggersAutomations.UNKNOWN
    # Absence is not a permissive default (Spec 5.7 Rule E): None means "not declared".
    reversible: bool | None = None
    reversibility_cost: str | None = None
    reversibility_note: str | None = None
    reversibility_window_seconds: float | None = None
    idempotent: bool | None = None
    state_persistence: str | None = None
    expected_latency_ms: float | None = None
    side_effect_scope: str | None = None
    state_volatility: str | None = None
    enforcement_mode: str = "advisory"
    control_reason: str | None = None
    declared_limits: list[dict[str, Any]] = field(default_factory=list)
    temporal_constraints: list[dict[str, Any]] = field(default_factory=list)
    override_triggers_automations: bool = False
    override_control_mode: bool = False
    human_reason: str | None = None


@dataclass
class ProfileMetadata:
    schema_version: str = "1.0"
    profile_version: str | None = None
    source: MetadataOrigin = MetadataOrigin.UNKNOWN
    confidence: float | None = None
    generated_at: str | None = None
    staleness_window_days: int = 60
    confirmed_fields: list[str] = field(default_factory=list)
    last_updated: str | None = None
    profile_valid_for: dict[str, Any] | None = None


def _get_path(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


@dataclass
class SemanticProfile:
    entity_id: str
    semantic_tags: list[str] = field(default_factory=list)
    metadata: ProfileMetadata = field(default_factory=ProfileMetadata)
    operational_boundaries: OperationalBoundaries = field(default_factory=OperationalBoundaries)
    privacy_classification: PrivacyClassification = field(default_factory=PrivacyClassification)
    inheritance_scope: str = "entity"
    diagnostic_profile: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    parse_warnings: list[str] = field(default_factory=list)

    # -- identity helpers ---------------------------------------------------

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def origin(self) -> MetadataOrigin:
        return self.metadata.source

    def is_inferred(self) -> bool:
        return self.metadata.source == MetadataOrigin.INFERRED_AI

    def is_trusted(self) -> bool:
        return self.metadata.source in TRUSTED_ORIGINS

    def declared(self, path: str) -> bool:
        """Whether a dotted field path was explicitly declared in the source document.

        ``privacy_classification.*`` paths are checked at the canonical sibling
        location and the nested fallback location (Spec 7).
        """
        if not self.raw:
            return False
        if path.startswith("privacy_classification"):
            return (
                _get_path(self.raw, path) is not None
                or _get_path(self.raw, f"semantic_profile.{path}") is not None
            )
        return _get_path(self.raw, f"semantic_profile.{path}") is not None

    def effective_confidence(self) -> float:
        if self.metadata.confidence is not None:
            return self.metadata.confidence
        return 1.0 if self.is_trusted() else 0.0

    def staleness_status(self, now: datetime | None = None) -> str:
        """``current`` / ``stale`` / ``unknown`` (Spec 5.4). Trusted profiles do not decay."""
        if not self.is_inferred():
            return "current"
        if not self.metadata.generated_at:
            return "unknown"
        try:
            generated = datetime.fromisoformat(self.metadata.generated_at)
        except ValueError:
            return "unknown"
        now = now or datetime.now(tz=generated.tzinfo)
        if now.tzinfo is None and generated.tzinfo is not None:
            generated = generated.replace(tzinfo=None)
        window = timedelta(days=self.metadata.staleness_window_days)
        return "stale" if now - generated > window else "current"

    # -- parsing ------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        entity_id: str,
        data: dict[str, Any],
        *,
        default_origin: MetadataOrigin = MetadataOrigin.UNKNOWN,
    ) -> SemanticProfile:
        """Parse a profile document (root form, or bare semantic_profile contents).

        ``default_origin`` applies when ``metadata_origin`` is absent: UNKNOWN
        everywhere except integration sidecar imports, which pass DEVELOPER
        (Spec 5.3 location-based provenance defaults).
        """
        validation.validate_or_raise(data, entity_id)
        warnings: list[str] = []

        if "semantic_profile" in data:
            root = copy.deepcopy(data)
        else:
            root = {"semantic_profile": copy.deepcopy(data)}
        sp = root.get("semantic_profile") or {}

        # Canonicalise privacy_classification to the sibling location (Spec 7).
        pc_raw = root.get("privacy_classification")
        if pc_raw is None and isinstance(sp.get("privacy_classification"), dict):
            pc_raw = sp["privacy_classification"]
            root["privacy_classification"] = pc_raw

        mo = sp.get("metadata_origin") or {}
        source = MetadataOrigin(mo["source"]) if "source" in mo else default_origin
        metadata = ProfileMetadata(
            schema_version=sp.get("schema_version", "1.0"),
            profile_version=sp.get("profile_version"),
            source=source,
            confidence=mo.get("confidence"),
            generated_at=mo.get("generated_at"),
            staleness_window_days=int(mo.get("staleness_window_days", 60)),
            confirmed_fields=list(mo.get("confirmed_fields") or []),
            last_updated=sp.get("last_updated"),
            profile_valid_for=sp.get("profile_valid_for"),
        )

        ob_raw = sp.get("operational_boundaries") or {}
        boundaries = OperationalBoundaries(
            control_mode=ControlMode(ob_raw.get("control_mode", "confirm")),
            triggers_automations=TriggersAutomations(
                ob_raw.get("triggers_automations", "unknown")
            ),
            reversible=ob_raw.get("reversible"),
            reversibility_cost=ob_raw.get("reversibility_cost"),
            reversibility_note=ob_raw.get("reversibility_note"),
            reversibility_window_seconds=ob_raw.get("reversibility_window_seconds"),
            idempotent=ob_raw.get("idempotent"),
            state_persistence=ob_raw.get("state_persistence"),
            expected_latency_ms=ob_raw.get("expected_latency_ms"),
            side_effect_scope=ob_raw.get("side_effect_scope"),
            state_volatility=ob_raw.get("state_volatility"),
            enforcement_mode=ob_raw.get("enforcement_mode", "advisory"),
            control_reason=ob_raw.get("control_reason"),
            declared_limits=list(ob_raw.get("declared_limits") or []),
            temporal_constraints=list(ob_raw.get("temporal_constraints") or []),
            override_triggers_automations=bool(ob_raw.get("override_triggers_automations", False)),
            override_control_mode=bool(ob_raw.get("override_control_mode", False)),
            human_reason=ob_raw.get("human_reason"),
        )

        # NOTE: untrusted-origin safety coercions (Spec 5.4 Rules 3, 8, 9) are NOT
        # applied here. Parsing is faithful to the document; trust policy is applied
        # at resolution time (mesa_core.conflict), which every consumption path uses.

        if isinstance(pc_raw, dict):
            privacy = PrivacyClassification(
                level=PrivacyLevel(pc_raw.get("level", "normal")),
                contains_presence_data=bool(pc_raw.get("contains_presence_data", False)),
                contains_audio_capture=bool(pc_raw.get("contains_audio_capture", False)),
                contains_visual_capture=bool(pc_raw.get("contains_visual_capture", False)),
                contains_biometric_data=bool(pc_raw.get("contains_biometric_data", False)),
                contains_behavioural_data=bool(pc_raw.get("contains_behavioural_data", False)),
                data_retention_local=pc_raw.get("data_retention_local"),
                access_logging_recommended=pc_raw.get("access_logging_recommended"),
                access_roles=pc_raw.get("access_roles"),
                deny_response_mode=pc_raw.get("deny_response_mode", "omit"),
                privacy_note=pc_raw.get("privacy_note"),
            )
        else:
            privacy = PrivacyClassification()

        return cls(
            entity_id=entity_id,
            semantic_tags=list(sp.get("semantic_tags") or []),
            metadata=metadata,
            operational_boundaries=boundaries,
            privacy_classification=privacy,
            inheritance_scope=sp.get("inheritance_scope", "entity"),
            diagnostic_profile=root.get("diagnostic_profile"),
            raw=root,
            parse_warnings=warnings,
        )

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the root document form.

        Profiles parsed via ``from_dict`` return a copy of the original document
        (unknown fields preserved). Programmatically constructed profiles are
        serialised from their typed fields.
        """
        if self.raw:
            return copy.deepcopy(self.raw)

        ob = self.operational_boundaries
        ob_dict: dict[str, Any] = {
            "control_mode": ob.control_mode.value,
            "triggers_automations": ob.triggers_automations.value,
        }
        for key in (
            "reversible",
            "reversibility_cost",
            "reversibility_note",
            "reversibility_window_seconds",
            "idempotent",
            "state_persistence",
            "expected_latency_ms",
            "side_effect_scope",
            "state_volatility",
            "control_reason",
            "human_reason",
        ):
            value = getattr(ob, key)
            if value is not None:
                ob_dict[key] = value
        if ob.enforcement_mode != "advisory":
            ob_dict["enforcement_mode"] = ob.enforcement_mode
        if ob.declared_limits:
            ob_dict["declared_limits"] = copy.deepcopy(ob.declared_limits)
        if ob.temporal_constraints:
            ob_dict["temporal_constraints"] = copy.deepcopy(ob.temporal_constraints)
        if ob.override_triggers_automations:
            ob_dict["override_triggers_automations"] = True
        if ob.override_control_mode:
            ob_dict["override_control_mode"] = True

        mo_dict: dict[str, Any] = {"source": self.metadata.source.value}
        if self.metadata.confidence is not None:
            mo_dict["confidence"] = self.metadata.confidence
        if self.metadata.generated_at is not None:
            mo_dict["generated_at"] = self.metadata.generated_at
        if self.metadata.staleness_window_days != 60:
            mo_dict["staleness_window_days"] = self.metadata.staleness_window_days
        if self.metadata.confirmed_fields:
            mo_dict["confirmed_fields"] = list(self.metadata.confirmed_fields)

        sp_dict: dict[str, Any] = {
            "schema_version": self.metadata.schema_version,
            "metadata_origin": mo_dict,
            "operational_boundaries": ob_dict,
        }
        if self.metadata.profile_version is not None:
            sp_dict["profile_version"] = self.metadata.profile_version
        if self.semantic_tags:
            sp_dict["semantic_tags"] = list(self.semantic_tags)
        if self.metadata.last_updated is not None:
            sp_dict["last_updated"] = self.metadata.last_updated
        if self.metadata.profile_valid_for is not None:
            sp_dict["profile_valid_for"] = copy.deepcopy(self.metadata.profile_valid_for)
        if self.inheritance_scope != "entity":
            sp_dict["inheritance_scope"] = self.inheritance_scope

        pc = self.privacy_classification
        pc_dict: dict[str, Any] = {"level": pc.level.value}
        for key in (
            "contains_presence_data",
            "contains_audio_capture",
            "contains_visual_capture",
            "contains_biometric_data",
            "contains_behavioural_data",
        ):
            if getattr(pc, key):
                pc_dict[key] = True
        for key in ("data_retention_local", "access_logging_recommended", "privacy_note"):
            value = getattr(pc, key)
            if value is not None:
                pc_dict[key] = value
        if pc.access_roles is not None:
            pc_dict["access_roles"] = copy.deepcopy(pc.access_roles)
        if pc.deny_response_mode != "omit":
            pc_dict["deny_response_mode"] = pc.deny_response_mode

        root: dict[str, Any] = {
            "semantic_profile": sp_dict,
            "privacy_classification": pc_dict,
        }
        if self.diagnostic_profile is not None:
            root["diagnostic_profile"] = copy.deepcopy(self.diagnostic_profile)
        return root


def parse_control_mode(value: str) -> ControlMode:
    try:
        return ControlMode(value)
    except ValueError as err:
        raise MesaValidationError(f"invalid control_mode: {value!r}") from err
