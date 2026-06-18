"""Constants for the Advanced Token Management (ATM) integration."""

import datetime
import re

ATM_VERSION = "2.0.0"
MIN_HA_VERSION = "2024.5.0"
GITHUB_URL = "https://github.com/sfox38/atm"
DOMAIN = "atm"
STORAGE_KEY = "atm"
STORAGE_VERSION = 2

PROXY_TIMEOUT_SECONDS = 30
MAX_REQUEST_BODY_BYTES = 1_048_576
MAX_ACTIVE_TOKENS_WARNING = 50
MAX_BATCH_ITEMS = 50

TOKEN_PREFIX = "atm_"
TOKEN_HEX_LENGTH = 64
TOKEN_LENGTH = len(TOKEN_PREFIX) + TOKEN_HEX_LENGTH

TOKEN_NAME_REGEX = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")

DEFAULT_RATE_LIMIT_REQUESTS = 60
DEFAULT_RATE_LIMIT_BURST = 10

FLUSH_INTERVAL = datetime.timedelta(minutes=5)
EXPIRY_CHECK_INTERVAL = datetime.timedelta(minutes=1)
SENSOR_PUSH_INTERVAL = datetime.timedelta(hours=1)

AUDIT_LOG_MAXLEN = 10000
AUDIT_STORAGE_KEY = "atm_audit"
AUDIT_STORAGE_VERSION = 1
# audit_flush_interval is stored and exposed in minutes (not seconds).
# Valid values: 0 (disable periodic flush), 5, 10, 15, 30, 60.

# Configuration version history (SPEC Section 16). Immutable before/after
# snapshots of agent-driven create/edit/delete of automations, scripts, scenes,
# and helpers, with admin-only rollback. Stored separately from tokens so the
# schema versions evolve independently.
VERSION_STORAGE_KEY = "atm_versions"
VERSION_STORAGE_VERSION = 1
# Per-resource FIFO retention: the newest N versions per (resource_type,
# resource_id) are kept; older ones are evicted on write.
MAX_VERSIONS_PER_RESOURCE = 20
# Resource types eligible for version history. Dashboards are intentionally
# excluded: ATM exposes only their registry metadata, not the view/card layout.
VERSIONED_RESOURCE_TYPES = frozenset({"automation", "script", "scene", "helper"})

# MESA (semantic safety layer) integration. Profiles persist in a separate
# Store from tokens so the two storage versions evolve independently.
MESA_STORAGE_KEY = "atm_mesa"
MESA_STORAGE_VERSION = 1
# Global enforcement mode for the vendored MesaEnforcer. "off" disables MESA
# entirely; "advisory" warns but never blocks (except read_only, which is
# entity-nature, not policy); "enforced" blocks and routes confirm through the
# admin approval gate. Default advisory: the zero-profile domain baseline is
# aggressive (lock/alarm prohibited), so enforced is opt-in.
MESA_MODE_OFF = "off"
MESA_MODE_ADVISORY = "advisory"
MESA_MODE_ENFORCED = "enforced"
MESA_MODES = frozenset({MESA_MODE_OFF, MESA_MODE_ADVISORY, MESA_MODE_ENFORCED})
# Sentinel cap_name on a PendingApproval created by a MESA control_mode:confirm
# block. It is not a real capability; the approval re-validation path special-
# cases it so effective_cap() (which would auto-deny an unknown cap) is skipped.
MESA_CONFIRM_CAP = "mesa_control_mode"
# Executor key for re-running a MESA-gated service call after admin approval.
# Registered in mcp_view._EXECUTOR_REGISTRY but never dispatchable from the
# tool router, so a token cannot invoke the approved-bypass path directly.
MESA_APPROVED_EXECUTOR = "call_service_mesa_approved"

SENSITIVE_ATTRIBUTES = frozenset({
    "entity_picture",
    "stream_url",
    "access_token",
    "still_image_url",
})

# Substrings (matched case-insensitively against a key name) that mark a value as
# sensitive regardless of which integration produced it. Such values are dropped
# from state attributes and replaced with "<redacted>" in service-response and
# event data. Defense in depth: third-party integrations can surface secrets
# (tokens, passwords, API keys) under arbitrary attribute/response keys that the
# fixed SENSITIVE_ATTRIBUTES list does not name. Over-redaction is the safe
# failure mode here, so the substrings are matched liberally.
SENSITIVE_KEY_SUBSTRINGS = frozenset({
    "password", "secret", "api_key", "apikey", "access_token",
    "auth_token", "authorization", "credential", "private_key",
    "token", "session",
})

BLOCKED_DOMAINS = frozenset({"atm"})

HIGH_RISK_DOMAINS = frozenset({
    "homeassistant",
    "recorder",
    "system_log",
    "hassio",
    "backup",
    "notify",
    "persistent_notification",
    "mqtt",
})

DUAL_GATE_SERVICES = frozenset({
    "homeassistant/restart",
    "homeassistant/stop",
})

# Services that require cap_physical_control even when pass_through is True.
# These represent irreversible or safety-relevant physical actions.
PHYSICAL_GATE_SERVICES = frozenset({
    "lock/lock",
    "lock/unlock",
    "lock/open",
    "alarm_control_panel/alarm_disarm",
    "alarm_control_panel/alarm_arm_away",
    "alarm_control_panel/alarm_arm_home",
    "alarm_control_panel/alarm_arm_night",
    "alarm_control_panel/alarm_arm_vacation",
    "alarm_control_panel/alarm_trigger",
    "cover/open_cover",
    "cover/close_cover",
    "cover/stop_cover",
    "cover/set_cover_position",
    "cover/set_cover_tilt_position",
})

# Capability modes. A token's per-capability state is one of these three values.
CAP_DENY = "deny"
CAP_ALLOW = "allow"
CAP_CONFIRM = "confirm"
CAP_MODES = frozenset({CAP_DENY, CAP_ALLOW, CAP_CONFIRM})

# Canonical list of all capability names. Every cap_* field on TokenRecord
# must appear here. Adding a new cap is a three-step change: add to this list,
# add to CAPABILITY_TIERS, and update the persona table in personas.py.
CAPABILITY_NAMES = (
    "cap_config_read",
    "cap_template_render",
    "cap_log_read",
    "cap_search",
    "cap_registry_read",
    "cap_traces",
    "cap_diagnostics",
    "cap_broadcast",
    "cap_service_response",
    "cap_automation_write",
    "cap_script_write",
    "cap_scene_write",
    "cap_helper_write",
    "cap_physical_control",
    "cap_restart",
    "cap_integration_write",
    "cap_lovelace_write",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
)

# Tiers drive UI grouping and which capabilities offer Confirm.
# Read and Everyday tiers are deny/allow only; the others support Confirm.
CAPABILITY_TIERS: dict[str, str] = {
    "cap_config_read": "read",
    "cap_template_render": "read",
    "cap_log_read": "read",
    "cap_search": "read",
    "cap_registry_read": "read",
    "cap_traces": "read",
    "cap_diagnostics": "read",
    "cap_broadcast": "everyday",
    "cap_service_response": "everyday",
    "cap_automation_write": "config_write",
    "cap_script_write": "config_write",
    "cap_scene_write": "config_write",
    "cap_helper_write": "config_write",
    "cap_physical_control": "system",
    "cap_restart": "system",
    "cap_integration_write": "system",
    "cap_lovelace_write": "system",
    "cap_backup": "irreversible",
    "cap_filesystem": "irreversible",
    "cap_yaml_edit": "irreversible",
}

CAPABILITY_TIER_ORDER = ("read", "everyday", "config_write", "system", "irreversible")

# Capabilities for which Confirm is a meaningful third state.
# UI hides the Confirm option for caps not in this set.
CONFIRM_AVAILABLE_CAPS = frozenset({
    "cap_automation_write",
    "cap_script_write",
    "cap_scene_write",
    "cap_helper_write",
    "cap_physical_control",
    "cap_restart",
    "cap_integration_write",
    "cap_lovelace_write",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
})

# Capabilities ALWAYS evaluated regardless of pass_through state.
# All other capabilities are bypassed (treated as "allow") when pass_through is True,
# except that "confirm" is honored even for non-exempt caps under pass_through
# (admin's intent to gate is preserved). See helpers.effective_cap.
PASS_THROUGH_EXEMPT_CAPS = frozenset({
    "cap_restart",
    "cap_physical_control",
    "cap_automation_write",
    "cap_script_write",
    "cap_log_read",
    "cap_scene_write",
    "cap_helper_write",
    "cap_integration_write",
    "cap_lovelace_write",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
})

# Pending-approval queue limits.
MAX_PENDING_APPROVALS_PER_TOKEN = 100
APPROVAL_DEFAULT_TTL_SECONDS = 3600
APPROVAL_TTL_MIN_SECONDS = 300
APPROVAL_TTL_MAX_SECONDS = 86400
APPROVAL_SWEEP_INTERVAL = datetime.timedelta(minutes=5)

# Diff size limits for approval records.
MAX_DIFF_INLINE_BYTES = 100_000
MAX_PREVIEW_ENTITY_IDS = 500

# Persona identifiers. Definitions live in personas.py.
PERSONA_READ_ONLY = "read_only"
PERSONA_VOICE_ASSISTANT = "voice_assistant"
PERSONA_AUTOMATION_BUILDER = "automation_builder"
PERSONA_POWER_USER = "power_user"
PERSONA_HOME_ADMIN = "home_admin"
PERSONA_CUSTOM = "custom"
# Gentle starter persona: reads plus service calls, physical control gated to
# confirm. Seeded by the onboarding wizard and also offered in the normal picker.
PERSONA_NEW_USER = "new_user"
# Dashboard/UI work: reads + registry + dashboard write; filesystem confirm for
# theme and custom-card assets. No device control.
PERSONA_DASHBOARD_DESIGNER = "dashboard_designer"
# Routine upkeep: full reads + diagnostics + backups; restart gated to confirm.
# No config authoring or device control.
PERSONA_MAINTENANCE = "maintenance"
PERSONA_NAMES = frozenset({
    PERSONA_READ_ONLY,
    PERSONA_VOICE_ASSISTANT,
    PERSONA_AUTOMATION_BUILDER,
    PERSONA_POWER_USER,
    PERSONA_HOME_ADMIN,
    PERSONA_NEW_USER,
    PERSONA_DASHBOARD_DESIGNER,
    PERSONA_MAINTENANCE,
    PERSONA_CUSTOM,
})

# Domains whose services require cap_physical_control.
# Derived from the domain portion of PHYSICAL_GATE_SERVICES.
PHYSICAL_GATE_DOMAINS = frozenset({"lock", "alarm_control_panel", "cover"})

# assist_satellite feature bit for ANNOUNCE support.
ANNOUNCE_BIT = 2

# Maximum time range for history and statistics queries.
MAX_HISTORY_RANGE_DAYS = 7

# Maximum number of log entries returned by the logs endpoint/tool.
MAX_LOG_ENTRIES = 100

# Hard cap (and default) for the bounded watch_entity tool.
# It blocks the tool call up to this many seconds waiting for a state change.
MAX_SUBSCRIPTION_SECONDS = 30

# Directories (relative to the HA config dir) the cap_filesystem tools may touch.
# Paths are realpath-resolved and must stay within one of these, blocking traversal.
FILESYSTEM_ALLOWED_DIRS = ("www", "themes", "custom_templates")
# Maximum file size (bytes) the filesystem read/write tools will handle inline.
MAX_FILE_BYTES = 1_048_576

