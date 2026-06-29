export type NodeState = "GREY" | "YELLOW" | "GREEN" | "RED";
export type Permission = "WRITE" | "READ" | "DENY" | "NO_ACCESS" | "NOT_FOUND";
export type Outcome =
  | "allowed"
  | "denied"
  | "not_found"
  | "rate_limited"
  | "not_implemented"
  | "invalid_request"
  | "pending_approval";

export type CapMode = "deny" | "allow" | "confirm";
export type Persona =
  | "new_user"
  | "read_only"
  | "voice_assistant"
  | "dashboard_designer"
  | "maintenance"
  | "automation_builder"
  | "power_user"
  | "home_admin"
  | "custom";

export type CapTier = "read" | "everyday" | "config_write" | "system" | "irreversible";

export interface CapabilityFlagsMap {
  cap_config_read: CapMode;
  cap_template_render: CapMode;
  cap_log_read: CapMode;
  cap_search: CapMode;
  cap_registry_read: CapMode;
  cap_traces: CapMode;
  cap_diagnostics: CapMode;
  cap_broadcast: CapMode;
  cap_service_response: CapMode;
  cap_automation_write: CapMode;
  cap_script_write: CapMode;
  cap_scene_write: CapMode;
  cap_helper_write: CapMode;
  cap_physical_control: CapMode;
  cap_restart: CapMode;
  cap_integration_write: CapMode;
  cap_lovelace_write: CapMode;
  cap_backup: CapMode;
  cap_filesystem: CapMode;
  cap_yaml_edit: CapMode;
}

export type CapName = keyof CapabilityFlagsMap;

export interface PermissionNode {
  state: NodeState;
  hint: string | null;
}

export interface PermissionTree {
  domains: Record<string, PermissionNode>;
  devices: Record<string, PermissionNode>;
  entities: Record<string, PermissionNode>;
}

export interface TokenRecord extends CapabilityFlagsMap {
  id: string;
  name: string;
  created_at: string;
  created_by: string;
  expires_at: string | null;
  revoked: boolean;
  last_used_at: string | null;
  updated_at: string | null;
  pass_through: boolean;
  use_assist_exposure?: boolean;
  announce_all_tools?: boolean;
  persona: Persona;
  rate_limit_requests: number;
  rate_limit_burst: number;
  permissions: PermissionTree;
}

export interface TokenCreateResponse extends TokenRecord {
  token: string;
}

export interface ArchivedTokenRecord {
  id: string;
  name: string;
  created_at: string;
  created_by: string;
  revoked_at: string;
  revoked: boolean;
  expires_at: string | null;
  last_used_at: string | null;
}

export interface GlobalSettings {
  kill_switch: boolean;
  disable_all_logging: boolean;
  log_allowed: boolean;
  log_denied: boolean;
  log_rate_limited: boolean;
  log_entity_names: boolean;
  log_client_ip: boolean;
  notify_on_rate_limit: boolean;
  notify_on_approval: boolean;
  audit_flush_interval: number;
  audit_log_maxlen: number;
  mesa_mode: MesaMode;
  mesa_inject_enabled: boolean;
}

export type MesaMode = "off" | "advisory" | "enforced";

// A MESA profile document (root form): the shape mesa-core serialises and
// accepts. Kept loose because the kernel is a small fixed subset of a larger
// optional schema that ATM does not re-specify on the frontend.
export interface MesaProfileDocument {
  semantic_profile?: Record<string, unknown>;
  privacy_classification?: Record<string, unknown>;
  // Provenance. "developer" marks a vendor-supplied profile imported from an
  // integration's mesa_profile.json sidecar; "user" is panel-authored.
  metadata_origin?: { source?: string };
}

export interface MesaProfileListItem {
  entity_id: string;
  document: MesaProfileDocument;
}

export interface MesaProfilesResponse {
  profiles: MesaProfileListItem[];
  total_matched: number;
  has_more: boolean;
  next_cursor: string | null;
}

export interface MesaProfileDetail {
  entity_id: string;
  stored: MesaProfileDocument | null;
  effective: MesaProfileDocument;
  explanation: {
    entity_id: string;
    explanation: Array<{
      field_path: string;
      effective_value: unknown;
      provided_by_level: string;
      provided_by_origin: string;
      conflict: boolean;
    }>;
    conflicts_detected: boolean;
    warnings: string[];
  };
}

export interface MesaValidationIssue {
  entity_id: string;
  declared_value: string;
  automation_id: string;
  role: string;
  severity: string;
  recommendation: string;
}

export interface MesaIssuesResponse {
  issues: MesaValidationIssue[];
  orphans: string[];
  orphan_areas: string[];
  orphan_integrations: string[];
}

export interface MesaPutResponse {
  entity_id: string;
  stored: MesaProfileDocument;
  warnings: MesaValidationIssue[];
}

export interface AuditEntry {
  request_id: string;
  timestamp: string;
  token_id: string;
  token_name: string;
  method: string;
  resource: string;
  outcome: Outcome;
  client_ip: string;
  pass_through: boolean;
  payload?: string | null;
  mesa_advisory?: boolean;
}

export interface EntityInfo {
  entity_id: string;
  friendly_name: string | null;
  device_id: string | null;
  area_id: string | null;
  area_name: string | null;
  labels: { id: string; name: string }[];
}

export interface DeviceInfo {
  device_id: string;
  name: string;
  area_id: string | null;
  area_name: string | null;
  entities: string[];
}

export interface DomainTree {
  devices: Record<string, DeviceInfo>;
  deviceless_entities: string[];
  entity_details: Record<string, EntityInfo>;
}

export type EntityTree = Record<string, DomainTree>;

export interface ResolutionStep {
  level: string;
  state: string;
}

export interface ResolveResult {
  entity_id: string;
  resolution_path: ResolutionStep[];
  effective: Permission;
  effective_hint: string | null;
}

export interface TokenConnection {
  last_used_at: string | null;
  request_count: number;
}

export interface TokenStats {
  token_id: string;
  token_name: string;
  request_count: number;
  denied_count: number;
  rate_limit_hits: number;
  last_used_at: string | null;
  status: string;
}

export interface ScopeResult {
  token_id: string;
  token_name: string;
  readable: string[];
  writable: string[];
  persona: Persona;
  capability_flags: CapabilityFlagsMap;
}

export interface CreateTokenBody {
  name: string;
  expires_at?: string;
  pass_through?: boolean;
  confirm_pass_through?: boolean;
  rate_limit_requests?: number;
  rate_limit_burst?: number;
}

export interface PatchTokenBody {
  name?: string;
  pass_through?: boolean;
  confirm_pass_through?: boolean;
  rate_limit_requests?: number;
  rate_limit_burst?: number;
  persona?: Persona;
  cap_automation_write?: CapMode;
  cap_script_write?: CapMode;
  cap_log_read?: CapMode;
  cap_config_read?: CapMode;
  cap_template_render?: CapMode;
  cap_restart?: CapMode;
  cap_physical_control?: CapMode;
  cap_service_response?: CapMode;
  cap_broadcast?: CapMode;
  cap_search?: CapMode;
  cap_registry_read?: CapMode;
  cap_traces?: CapMode;
  cap_diagnostics?: CapMode;
  cap_scene_write?: CapMode;
  cap_helper_write?: CapMode;
  cap_integration_write?: CapMode;
  cap_lovelace_write?: CapMode;
  cap_backup?: CapMode;
  cap_filesystem?: CapMode;
  cap_yaml_edit?: CapMode;
  use_assist_exposure?: boolean;
  announce_all_tools?: boolean;
}

export interface PermissionPatchBody {
  state: NodeState;
  hint?: string | null;
}

export interface AuditQueryParams {
  limit?: number;
  offset?: number;
  token_id?: string;
  outcome?: string;
  ip?: string;
}

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"
  | "cancelled";

export interface ApprovalDiff {
  kind?: "yaml_diff" | "config_diff" | "service_preview" | "system_action" | "file_write";
  summary?: string;
  target?: { type?: string; id?: string | null; label?: string | null };
  before?: string | null;
  after?: string | null;
  preview?: Record<string, unknown>;
}

export interface ApprovalRecord {
  id: string;
  token_id: string;
  token_name: string;
  tool_name: string;
  cap_name: string;
  args: Record<string, unknown>;
  diff: ApprovalDiff;
  status: ApprovalStatus;
  created_at: string;
  expires_at: string;
  resolved_at: string | null;
  approved_by_user_id: string | null;
  rejected_reason: string | null;
  result: unknown | null;
  request_id: string;
  client_ip: string | null;
}

export interface ApprovalListResponse {
  approvals: ApprovalRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApprovalListParams {
  status?: ApprovalStatus;
  token_id?: string;
  limit?: number;
  offset?: number;
}

export type VersionAction = "create" | "edit" | "delete" | "rollback";
export type VersionResourceType =
  | "automation"
  | "script"
  | "scene"
  | "helper"
  | "dashboard"
  | "yaml_config"
  | "file";

export interface VersionSummary {
  id: string;
  resource_type: VersionResourceType;
  resource_id: string;
  alias: string | null;
  action: VersionAction;
  token_id: string | null;
  token_name: string | null;
  approved_by_user_id: string | null;
  timestamp: string;
  has_before: boolean;
  has_after: boolean;
}

export interface VersionRecord {
  id: string;
  resource_type: VersionResourceType;
  resource_id: string;
  alias: string | null;
  action: VersionAction;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  token_id: string | null;
  token_name: string | null;
  request_id: string | null;
  approved_by_user_id: string | null;
  timestamp: string;
}

export interface VersionListResponse {
  resource_type: string | null;
  resource_id: string | null;
  versions: VersionSummary[];
  total: number;
}

export interface VersionRestoreResponse {
  restored: boolean;
  version_id: string;
  resource_type: string;
  resource_id: string;
}

declare global {
  namespace React.JSX {
    interface IntrinsicElements {
      "ha-card": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        header?: string;
        outlined?: boolean;
      };
      "ha-switch": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        checked?: boolean;
        disabled?: boolean;
      };
      "ha-icon": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        icon?: string;
      };
      "ha-icon-button": React.DetailedHTMLProps<React.ButtonHTMLAttributes<HTMLElement>, HTMLElement> & {
        label?: string;
        disabled?: boolean;
      };
      "ha-circular-progress": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        active?: boolean;
        size?: string;
      };
      "ha-menu-button": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
      "ha-code-editor": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}
