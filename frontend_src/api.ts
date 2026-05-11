import type {
  ApprovalListParams,
  ApprovalListResponse,
  ApprovalRecord,
  AuditEntry,
  AuditQueryParams,
  ArchivedTokenRecord,
  CreateTokenBody,
  EntityTree,
  GlobalSettings,
  PatchTokenBody,
  PermissionPatchBody,
  PermissionTree,
  ResolveResult,
  ScopeResult,
  TokenCreateResponse,
  TokenRecord,
  TokenStats,
} from "./types";

const BASE = "/api/atm/admin";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let hassInstance: any = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function setHass(hass: any) {
  hassInstance = hass;
}

class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function _doReq<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  // Proactively refresh if the token is expired or within 60s of expiry, avoiding a
  // guaranteed 401 that HA would log as a ban warning.
  if (!retried && hassInstance?.auth) {
    const expires: number | undefined = hassInstance.auth.data?.expires;
    if (expires !== undefined && Date.now() > expires - 60_000) {
      await hassInstance.auth.refreshAccessToken();
    }
  }
  const token: string | undefined = hassInstance?.auth?.data?.access_token;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const opts: RequestInit = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const res = await fetch(`${BASE}${path}`, opts);

  if (res.status === 401 && !retried && hassInstance?.auth) {
    await hassInstance.auth.refreshAccessToken();
    return _doReq<T>(method, path, body, true);
  }

  if (res.status === 204) return undefined as T;
  const json = await res.json().catch(() => ({ error: "parse_error", message: res.statusText }));
  if (!res.ok) throw new ApiError(res.status, json.error ?? "unknown", json.message ?? res.statusText);
  return json as T;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  return _doReq<T>(method, path, body);
}

function buildQuery(params?: AuditQueryParams): string {
  if (!params) return "";
  const p = new URLSearchParams();
  if (params.limit !== undefined) p.set("limit", String(params.limit));
  if (params.offset !== undefined) p.set("offset", String(params.offset));
  if (params.token_id) p.set("token_id", params.token_id);
  if (params.outcome) p.set("outcome", params.outcome);
  if (params.ip) p.set("ip", params.ip);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  listTokens: () => req<TokenRecord[]>("GET", "/tokens"),
  getToken: (id: string) => req<TokenRecord>("GET", `/tokens/${id}`),
  createToken: (body: CreateTokenBody) =>
    req<TokenCreateResponse>("POST", "/tokens", body),
  patchToken: (id: string, body: PatchTokenBody) =>
    req<TokenRecord>("PATCH", `/tokens/${id}`, body),
  revokeToken: (id: string) => req<void>("DELETE", `/tokens/${id}`),
  rotateToken: (id: string) => req<TokenCreateResponse>("POST", `/tokens/${id}/rotate`),

  listArchivedTokens: () => req<ArchivedTokenRecord[]>("GET", "/tokens/archived"),
  deleteArchivedToken: (id: string) => req<void>("DELETE", `/tokens/archived/${id}`),

  getPermissions: (id: string) => req<PermissionTree>("GET", `/tokens/${id}/permissions`),
  setPermissions: (id: string, tree: PermissionTree) =>
    req<PermissionTree>("PUT", `/tokens/${id}/permissions`, tree),
  patchDomainPermission: (tokenId: string, domain: string, body: PermissionPatchBody) =>
    req<PermissionTree>("PATCH", `/tokens/${tokenId}/permissions/domains/${encodeURIComponent(domain)}`, body),
  patchDevicePermission: (tokenId: string, deviceId: string, body: PermissionPatchBody) =>
    req<PermissionTree>("PATCH", `/tokens/${tokenId}/permissions/devices/${encodeURIComponent(deviceId)}`, body),
  patchEntityPermission: (tokenId: string, entityId: string, body: PermissionPatchBody) =>
    req<PermissionTree>("PATCH", `/tokens/${tokenId}/permissions/entities/${encodeURIComponent(entityId)}`, body),

  resolve: (tokenId: string, entityId: string) =>
    req<ResolveResult>("GET", `/tokens/${tokenId}/resolve/${encodeURIComponent(entityId)}`),
  getScope: (tokenId: string) => req<ScopeResult>("GET", `/tokens/${tokenId}/scope`),

  getEntityTree: (forceReload = false) =>
    req<EntityTree>("GET", `/entities${forceReload ? "?force_reload=1" : ""}`),

  getTokenStats: (tokenId: string) => req<TokenStats>("GET", `/tokens/${tokenId}/stats`),
  getTokenAudit: (tokenId: string, params?: AuditQueryParams) =>
    req<AuditEntry[]>("GET", `/tokens/${tokenId}/audit${buildQuery(params)}`),
  getAudit: (params?: AuditQueryParams) =>
    req<AuditEntry[]>("GET", `/audit${buildQuery(params)}`),

  getInfo: () => req<{ version: string; min_ha_version: string; github_url: string }>("GET", "/info"),

  getSettings: () => req<GlobalSettings>("GET", "/settings"),
  patchSettings: (body: Partial<GlobalSettings>) =>
    req<GlobalSettings>("PATCH", "/settings", body),

  wipe: () => req<void>("DELETE", "/wipe", { confirm: "WIPE" }),

  listApprovals: (params?: ApprovalListParams) => {
    const p = new URLSearchParams();
    if (params?.status) p.set("status", params.status);
    if (params?.token_id) p.set("token_id", params.token_id);
    if (params?.limit !== undefined) p.set("limit", String(params.limit));
    if (params?.offset !== undefined) p.set("offset", String(params.offset));
    const q = p.toString();
    return req<ApprovalListResponse>("GET", `/approvals${q ? `?${q}` : ""}`);
  },
  getApproval: (id: string) => req<ApprovalRecord>("GET", `/approvals/${encodeURIComponent(id)}`),
  approveApproval: (id: string, body: { note?: string } = {}) =>
    req<ApprovalRecord>("POST", `/approvals/${encodeURIComponent(id)}/approve`, body),
  rejectApproval: (id: string, body: { reason?: string } = {}) =>
    req<ApprovalRecord>("POST", `/approvals/${encodeURIComponent(id)}/reject`, body),
  cancelApproval: (id: string) => req<void>("DELETE", `/approvals/${encodeURIComponent(id)}`),
};

export { ApiError };
