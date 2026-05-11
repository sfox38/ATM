export type NodeState = "GREY" | "YELLOW" | "GREEN" | "RED";
export type Permission = "WRITE" | "READ" | "DENY" | "NO_ACCESS" | "NOT_FOUND";
export type Outcome =
  | "allowed"
  | "denied"
  | "not_found"
  | "rate_limited"
  | "not_implemented"
  | "invalid_request"
  | "pending_approval"
  | "approval_executed"
  | "approval_rejected"
  | "approval_expired"
  | "approval_cancelled";

export type CapMode = "deny" | "allow" | "confirm";
export type Persona =
  | "read_only"
  | "voice_assistant"
  | "automation_builder"
  | "power_user"
  | "custom";

export type CapTier = "read" | "everyday" | "config_write" | "system" | "irreversible";

export interface CapabilityFlagsMap {
  cap_config_read: CapMode;
  cap_template_render: CapMode;
  cap_log_read: CapMode;
  cap_broadcast: CapMode;
  cap_service_response: CapMode;
  cap_automation_write: CapMode;
  cap_script_write: CapMode;
  cap_physical_control: CapMode;
  cap_restart: CapMode;
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
  // token_hash is declared here for type completeness but the backend never includes it
  // in to_dict() responses (only to_storage_dict() uses it). This field will always be
  // undefined at runtime. Do not read it; use token_hash only in TokenCreateResponse.token.
  token_hash: string;
  created_at: string;
  created_by: string;
  expires_at: string | null;
  revoked: boolean;
  last_used_at: string | null;
  updated_at: string | null;
  pass_through: boolean;
  use_assist_exposure?: boolean;
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
  token_hash: string;
  created_at: string;
  created_by: string;
  revoked_at: string;
  revoked: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  pass_through: boolean;
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
  audit_flush_interval: number;
  audit_log_maxlen: number;
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
}

export interface EntityInfo {
  entity_id: string;
  friendly_name: string | null;
  device_id: string | null;
  area_id: string | null;
  area_name: string | null;
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
  use_assist_exposure?: boolean;
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
    }
  }
}
