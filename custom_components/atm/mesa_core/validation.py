"""Hand-rolled profile validation, kept in agreement with schemas/mesa_profile.schema.json.

The JSON Schema file is the canonical machine-readable artifact for third parties;
this module is the zero-dependency implementation mesa-core uses internally. The
test suite asserts both reject the same documents (tests/test_validation_schema_agreement.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from custom_components.atm.mesa_core import vocabulary
from custom_components.atm.mesa_core.exceptions import MesaValidationError

VALID_CONTROL_MODES = {"autonomous", "confirm", "read_only", "prohibited"}
VALID_TRIGGERS = {"likely", "none", "unknown", "deployment_defined"}
VALID_PRIVACY_LEVELS = {"public", "normal", "sensitive", "restricted"}
VALID_ORIGINS = {"developer", "user", "hybrid", "inferred_ai", "unknown"}
VALID_ENFORCEMENT_MODES = {"advisory", "enforced"}
VALID_REVERSIBILITY_COSTS = {"none", "trivial", "moderate", "high"}
VALID_SIDE_EFFECT_SCOPES = {
    "entity_only",
    "device_localized",
    "room_localized",
    "zone_wide",
    "deployment_wide",
}
VALID_STATE_VOLATILITY = {"static", "low", "medium", "high", "realtime"}
VALID_STATE_PERSISTENCE = {"permanent", "temporary", "session", "transient"}
VALID_DENY_RESPONSE_MODES = {"omit", "redact", "error"}
VALID_INHERITANCE_SCOPES = {"entity", "domain", "integration", "area"}
PREDICATE_OPERATORS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "contains"}
VALID_TEMPORAL_TYPES = {
    "time_range",
    "day_of_week",
    "calendar_entity",
    "solar_angle",
    "duration",
    "relative_to_event",
}


@dataclass
class ValidationReport:
    """Result of validating a profile document."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _semantic_profile_of(data: dict[str, Any]) -> dict[str, Any]:
    sp = data.get("semantic_profile", data)
    return sp if isinstance(sp, dict) else {}


def _privacy_of(data: dict[str, Any]) -> Any:
    # Canonical location is a sibling of semantic_profile; nested is accepted (Spec 7).
    if "privacy_classification" in data:
        return data["privacy_classification"]
    return _semantic_profile_of(data).get("privacy_classification")


def _check_enum(value: Any, valid: set[str], where: str, report: ValidationReport) -> None:
    if value is not None and value not in valid:
        report.errors.append(f"{where}: invalid value {value!r} (valid: {sorted(valid)})")


def _check_predicate(pred: Any, where: str, report: ValidationReport) -> None:
    if not isinstance(pred, dict):
        report.errors.append(f"{where}: predicate must be an object")
        return
    if pred.get("type") == "ha_condition":
        if not isinstance(pred.get("condition"), dict):
            report.errors.append(f"{where}: ha_condition predicate requires a 'condition' object")
        return
    op = pred.get("operator")
    if op not in PREDICATE_OPERATORS:
        report.errors.append(
            f"{where}: unrecognised predicate operator {op!r} "
            f"(canonical tokens: {sorted(PREDICATE_OPERATORS)}; Spec 6.3)"
        )
    if "entity" not in pred:
        report.errors.append(f"{where}: predicate requires 'entity'")
    if "value" not in pred:
        report.errors.append(f"{where}: predicate requires 'value'")


def _check_metadata_origin(sp: dict[str, Any], report: ValidationReport) -> None:
    mo = sp.get("metadata_origin")
    if mo is None:
        return
    if not isinstance(mo, dict):
        report.errors.append("metadata_origin must be an object")
        return
    source = mo.get("source")
    _check_enum(source, VALID_ORIGINS, "metadata_origin.source", report)
    confidence = mo.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0
    ):
        report.errors.append("metadata_origin.confidence must be a number between 0.0 and 1.0")
    if source == "inferred_ai":
        # Inferred Rule 1 (Spec 5.4): missing either field makes the profile malformed.
        if confidence is None:
            report.errors.append("inferred_ai profile is malformed: missing 'confidence' (Rule 1)")
        if mo.get("generated_at") is None:
            report.errors.append(
                "inferred_ai profile is malformed: missing 'generated_at' (Rule 1)"
            )
    if source in ("developer", "user") and "generated_at" in mo:
        report.warnings.append(
            f"trust laundering suspected: source {source!r} but profile carries "
            "'generated_at', an AI-inference marker. AI-generated content must be "
            "marked 'hybrid' or 'inferred_ai' (Getting Started Guide)."
        )


def _check_boundaries(sp: dict[str, Any], report: ValidationReport) -> None:
    ob = sp.get("operational_boundaries")
    if ob is None:
        return
    if not isinstance(ob, dict):
        report.errors.append("operational_boundaries must be an object")
        return
    _check_enum(ob.get("control_mode"), VALID_CONTROL_MODES, "control_mode", report)
    _check_enum(ob.get("triggers_automations"), VALID_TRIGGERS, "triggers_automations", report)
    _check_enum(ob.get("enforcement_mode"), VALID_ENFORCEMENT_MODES, "enforcement_mode", report)
    _check_enum(
        ob.get("reversibility_cost"), VALID_REVERSIBILITY_COSTS, "reversibility_cost", report
    )
    _check_enum(ob.get("side_effect_scope"), VALID_SIDE_EFFECT_SCOPES, "side_effect_scope", report)
    _check_enum(ob.get("state_volatility"), VALID_STATE_VOLATILITY, "state_volatility", report)
    _check_enum(ob.get("state_persistence"), VALID_STATE_PERSISTENCE, "state_persistence", report)

    # Override flags: malformed overrides are ignored at resolution (Spec 5.7 Rule A, 6.1);
    # surfaced here as warnings so authors find out why.
    if ob.get("override_control_mode") is True and not ob.get("control_reason"):
        report.warnings.append(
            "override_control_mode: true without control_reason is malformed "
            "and will be ignored (Spec 5.7 Rule A)"
        )
    if ob.get("override_triggers_automations") is True and not ob.get("human_reason"):
        report.warnings.append(
            "override_triggers_automations: true without human_reason is malformed "
            "and will be ignored (Spec 6.1)"
        )

    for i, limit in enumerate(ob.get("declared_limits") or []):
        where = f"declared_limits[{i}]"
        if not isinstance(limit, dict):
            report.errors.append(f"{where}: must be an object")
            continue
        if not limit.get("id"):
            report.errors.append(f"{where}: 'id' is required (Spec 6.4)")
        _check_predicate(limit.get("predicate"), where, report)
        lim = limit.get("limit")
        if not isinstance(lim, dict) or "service" not in lim or "parameter" not in lim:
            report.errors.append(f"{where}: 'limit' requires 'service' and 'parameter'")

    for i, tc in enumerate(ob.get("temporal_constraints") or []):
        where = f"temporal_constraints[{i}]"
        if not isinstance(tc, dict):
            report.errors.append(f"{where}: must be an object")
            continue
        if not tc.get("id"):
            report.errors.append(f"{where}: 'id' is required (Spec 6.5)")
        cond = tc.get("condition")
        if not isinstance(cond, dict):
            report.errors.append(f"{where}: 'condition' object is required")
        else:
            _check_enum(cond.get("type"), VALID_TEMPORAL_TYPES, f"{where}.condition.type", report)
            if "negate" in cond and not isinstance(cond["negate"], bool):
                report.errors.append(f"{where}.condition.negate must be a boolean")
        effect = tc.get("effect")
        if not isinstance(effect, dict) or not effect:
            report.errors.append(f"{where}: 'effect' must be a non-empty object (Spec 6.5)")
        elif "control_mode" in effect:
            _check_enum(
                effect["control_mode"], VALID_CONTROL_MODES, f"{where}.effect.control_mode", report
            )


def validate_document(data: dict[str, Any], entity_id: str = "") -> ValidationReport:
    """Validate a profile document (root form or bare semantic_profile contents)."""
    report = ValidationReport()
    if not isinstance(data, dict):
        report.errors.append("profile document must be an object")
        return report

    sp = _semantic_profile_of(data)
    _check_metadata_origin(sp, report)
    _check_boundaries(sp, report)

    tags = sp.get("semantic_tags")
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            report.errors.append("semantic_tags must be an array of strings")
        else:
            report.errors.extend(vocabulary.check_tags(tags))

    _check_enum(
        sp.get("inheritance_scope"), VALID_INHERITANCE_SCOPES, "inheritance_scope", report
    )

    pc = _privacy_of(data)
    if pc is not None:
        if not isinstance(pc, dict):
            report.errors.append("privacy_classification must be an object")
        else:
            if "level" not in pc:
                report.errors.append("privacy_classification requires 'level' (Spec 7.1)")
            _check_enum(
                pc.get("level"), VALID_PRIVACY_LEVELS, "privacy_classification.level", report
            )
            _check_enum(
                pc.get("deny_response_mode"),
                VALID_DENY_RESPONSE_MODES,
                "deny_response_mode",
                report,
            )

    return report


def validate_or_raise(data: dict[str, Any], entity_id: str = "") -> ValidationReport:
    """Validate and raise MesaValidationError when the document is malformed."""
    report = validate_document(data, entity_id)
    if not report.ok:
        raise MesaValidationError(
            f"profile for {entity_id or '<unkeyed>'} is malformed: {'; '.join(report.errors)}",
            errors=report.errors,
        )
    return report
