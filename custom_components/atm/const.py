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
MAX_SSE_CONNECTIONS_PER_TOKEN = 5
MAX_BATCH_ITEMS = 50

TOKEN_PREFIX = "atm_"
TOKEN_HEX_LENGTH = 64
TOKEN_LENGTH = len(TOKEN_PREFIX) + TOKEN_HEX_LENGTH

TOKEN_NAME_REGEX = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")

DEFAULT_RATE_LIMIT_REQUESTS = 60
DEFAULT_RATE_LIMIT_BURST = 10

SSE_HEARTBEAT_INTERVAL = datetime.timedelta(seconds=15)
FLUSH_INTERVAL = datetime.timedelta(minutes=5)
EXPIRY_CHECK_INTERVAL = datetime.timedelta(minutes=1)
SENSOR_PUSH_INTERVAL = datetime.timedelta(hours=1)

AUDIT_LOG_MAXLEN = 10000
AUDIT_STORAGE_KEY = "atm_audit"
AUDIT_STORAGE_VERSION = 1
# audit_flush_interval is stored and exposed in minutes (not seconds).
# Valid values: 0 (disable periodic flush), 5, 10, 15, 30, 60.

SENSITIVE_ATTRIBUTES = frozenset({
    "entity_picture",
    "stream_url",
    "access_token",
    "still_image_url",
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
    "cap_broadcast",
    "cap_service_response",
    "cap_automation_write",
    "cap_script_write",
    "cap_physical_control",
    "cap_restart",
)

# Tiers drive UI grouping and which capabilities offer Confirm.
# Read and Everyday tiers are deny/allow only; the others support Confirm.
CAPABILITY_TIERS: dict[str, str] = {
    "cap_config_read": "read",
    "cap_template_render": "read",
    "cap_log_read": "read",
    "cap_broadcast": "everyday",
    "cap_service_response": "everyday",
    "cap_automation_write": "config_write",
    "cap_script_write": "config_write",
    "cap_physical_control": "system",
    "cap_restart": "system",
}

CAPABILITY_TIER_ORDER = ("read", "everyday", "config_write", "system", "irreversible")

# Capabilities for which Confirm is a meaningful third state.
# UI hides the Confirm option for caps not in this set.
CONFIRM_AVAILABLE_CAPS = frozenset({
    "cap_automation_write",
    "cap_script_write",
    "cap_physical_control",
    "cap_restart",
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
PERSONA_CUSTOM = "custom"
PERSONA_NAMES = frozenset({
    PERSONA_READ_ONLY,
    PERSONA_VOICE_ASSISTANT,
    PERSONA_AUTOMATION_BUILDER,
    PERSONA_POWER_USER,
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

