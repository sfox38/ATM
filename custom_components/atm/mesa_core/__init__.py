"""mesa-core: reference implementation of the MESA specification."""

from custom_components.atm.mesa_core.conflict import ConflictResolver
from custom_components.atm.mesa_core.enforcer import ConfirmationManager, EnforcementResult, MesaEnforcer
from custom_components.atm.mesa_core.exceptions import (
    InvalidCursorError,
    MesaEnforcementError,
    MesaError,
    MesaValidationError,
)
from custom_components.atm.mesa_core.inheritance import InheritanceResolver, ProfileExplanation
from custom_components.atm.mesa_core.integration_import import import_from_integration
from custom_components.atm.mesa_core.migration import migrate_profile
from custom_components.atm.mesa_core.privacy import AccessDecision, CallerContext, PrivacyEnforcer
from custom_components.atm.mesa_core.profile import (
    DOMAIN_SAFETY_BASELINE,
    HELPER_DOMAINS,
    ControlMode,
    MetadataOrigin,
    OperationalBoundaries,
    PrivacyClassification,
    PrivacyLevel,
    ProfileMetadata,
    SemanticProfile,
    TriggersAutomations,
)
from custom_components.atm.mesa_core.store import DeploymentDefaults, ProfileQueryResult, ProfileStore
from custom_components.atm.mesa_core.temporal import TemporalEvaluator, TemporalResult
from custom_components.atm.mesa_core.trigger_validator import (
    TriggerValidator,
    ValidationIssue,
    entities_by_role,
)
from custom_components.atm.mesa_core.validation import ValidationReport, validate_document, validate_or_raise

__version__ = "1.0.0"

__all__ = [
    "DOMAIN_SAFETY_BASELINE",
    "HELPER_DOMAINS",
    "AccessDecision",
    "CallerContext",
    "ConfirmationManager",
    "ConflictResolver",
    "ControlMode",
    "DeploymentDefaults",
    "EnforcementResult",
    "InheritanceResolver",
    "InvalidCursorError",
    "MesaEnforcementError",
    "MesaEnforcer",
    "MesaError",
    "MesaValidationError",
    "MetadataOrigin",
    "OperationalBoundaries",
    "PrivacyClassification",
    "PrivacyEnforcer",
    "PrivacyLevel",
    "ProfileExplanation",
    "ProfileMetadata",
    "ProfileQueryResult",
    "ProfileStore",
    "SemanticProfile",
    "TemporalEvaluator",
    "TemporalResult",
    "TriggerValidator",
    "TriggersAutomations",
    "ValidationIssue",
    "ValidationReport",
    "entities_by_role",
    "import_from_integration",
    "migrate_profile",
    "validate_document",
    "validate_or_raise",
]
