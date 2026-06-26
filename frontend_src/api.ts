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
  MesaIssuesResponse,
  MesaProfileDetail,
  MesaProfileDocument,
  MesaProfilesResponse,
  MesaPutResponse,
  PatchTokenBody,
  PermissionPatchBody,
  PermissionTree,
  ResolveResult,
  ScopeResult,
  TokenConnection,
  TokenCreateResponse,
  TokenRecord,
  TokenStats,
  VersionListResponse,
  VersionRecord,
  VersionRestoreResponse,
} from "./types";

const BASE = "/api/atm/admin";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let hassInstance: any = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function setHass(hass: any) {
  hassInstance = hass;
}

// The hass to authenticate with. Normally the one passed to setHass, but the
// in-context inject modal can run in a module realm where setHass was never
// called (a second injector copy, or one stood down by the singleton guard), so
// fall back to the live hass on the page's <home-assistant> element. Without this
// a request would go out with no Authorization header and HA would 401 + ban-log
// it. Harmless for the panel, which reads the same object.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function authHass(): any {
  if (hassInstance) return hassInstance;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (document.querySelector("home-assistant") as any)?.hass ?? null;
  } catch {
    return null;
  }
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
  const hass = authHass();
  // Proactively refresh if the token is expired or within 60s of expiry, avoiding a
  // guaranteed 401 that HA would log as a ban warning.
  if (!retried && hass?.auth) {
    const expires: number | undefined = hass.auth.data?.expires;
    if (expires !== undefined && Date.now() > expires - 60_000) {
      await hass.auth.refreshAccessToken();
    }
  }
  const token: string | undefined = hass?.auth?.data?.access_token;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const opts: RequestInit = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const res = await fetch(`${BASE}${path}`, opts);

  if (res.status === 401 && !retried && hass?.auth) {
    await hass.auth.refreshAccessToken();
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

  getEntityHints: () =>
    req<{ entity_hints: Record<string, string> }>("GET", "/entity-hints"),
  setEntityHint: (entityId: string, hint: string | null) =>
    req<{ entity_hints: Record<string, string> }>("PUT", `/entity-hints/${encodeURIComponent(entityId)}`, { hint }),

  getTokenStats: (tokenId: string) => req<TokenStats>("GET", `/tokens/${tokenId}/stats`),
  getTokenConnection: (tokenId: string) => req<TokenConnection>("GET", `/tokens/${tokenId}/connection`),
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
  listMesaProfiles: (params?: { domain?: string; tag?: string; area?: string; origin?: string; limit?: number; cursor?: string }) => {
    const p = new URLSearchParams();
    if (params?.domain) p.set("domain", params.domain);
    if (params?.tag) p.set("tag", params.tag);
    if (params?.area) p.set("area", params.area);
    if (params?.origin) p.set("origin", params.origin);
    if (params?.limit !== undefined) p.set("limit", String(params.limit));
    if (params?.cursor) p.set("cursor", params.cursor);
    const q = p.toString();
    return req<MesaProfilesResponse>("GET", `/mesa/profiles${q ? `?${q}` : ""}`);
  },
  getMesaProfile: (entityId: string) =>
    req<MesaProfileDetail>("GET", `/mesa/profiles/${encodeURIComponent(entityId)}`),
  putMesaProfile: (entityId: string, doc: MesaProfileDocument) =>
    req<MesaPutResponse>("PUT", `/mesa/profiles/${encodeURIComponent(entityId)}`, doc),
  deleteMesaProfile: (entityId: string) =>
    req<{ entity_id: string; deleted: boolean }>("DELETE", `/mesa/profiles/${encodeURIComponent(entityId)}`),

  listMesaDomains: () =>
    req<{ domains: { domain: string; document: MesaProfileDocument }[] }>("GET", "/mesa/domains"),
  getMesaDomain: (domain: string) =>
    req<{ domain: string; stored: MesaProfileDocument | null }>("GET", `/mesa/domains/${encodeURIComponent(domain)}`),
  putMesaDomain: (domain: string, doc: MesaProfileDocument) =>
    req<{ domain: string; stored: MesaProfileDocument }>("PUT", `/mesa/domains/${encodeURIComponent(domain)}`, doc),
  deleteMesaDomain: (domain: string) =>
    req<{ domain: string; deleted: boolean }>("DELETE", `/mesa/domains/${encodeURIComponent(domain)}`),

  listMesaIntegrations: () =>
    req<{ integrations: { integration: string; document: MesaProfileDocument }[] }>("GET", "/mesa/integrations"),
  getMesaIntegration: (integration: string) =>
    req<{ integration: string; stored: MesaProfileDocument | null }>("GET", `/mesa/integrations/${encodeURIComponent(integration)}`),
  putMesaIntegration: (integration: string, doc: MesaProfileDocument) =>
    req<{ integration: string; stored: MesaProfileDocument }>("PUT", `/mesa/integrations/${encodeURIComponent(integration)}`, doc),
  deleteMesaIntegration: (integration: string) =>
    req<{ integration: string; deleted: boolean }>("DELETE", `/mesa/integrations/${encodeURIComponent(integration)}`),
  getMesaIntegrationOptions: () =>
    req<{ integrations: { id: string; name: string }[] }>("GET", "/mesa/integration-options"),

  listMesaAreas: () =>
    req<{ areas: { area_id: string; document: MesaProfileDocument }[] }>("GET", "/mesa/areas"),
  getMesaArea: (areaId: string) =>
    req<{ area_id: string; stored: MesaProfileDocument | null }>("GET", `/mesa/areas/${encodeURIComponent(areaId)}`),
  putMesaArea: (areaId: string, doc: MesaProfileDocument) =>
    req<{ area_id: string; stored: MesaProfileDocument }>("PUT", `/mesa/areas/${encodeURIComponent(areaId)}`, doc),
  deleteMesaArea: (areaId: string) =>
    req<{ area_id: string; deleted: boolean }>("DELETE", `/mesa/areas/${encodeURIComponent(areaId)}`),

  getMesaVocabulary: () =>
    req<{ canonical_tags: string[]; canonical_roots: string[] }>("GET", "/mesa/vocabulary"),

  getMesaIssues: (refresh = false) =>
    req<MesaIssuesResponse>("GET", `/mesa/issues${refresh ? "?refresh=1" : ""}`),

  getApproval: (id: string) => req<ApprovalRecord>("GET", `/approvals/${encodeURIComponent(id)}`),
  approveApproval: (id: string, body: { note?: string } = {}) =>
    req<ApprovalRecord>("POST", `/approvals/${encodeURIComponent(id)}/approve`, body),
  rejectApproval: (id: string, body: { reason?: string } = {}) =>
    req<ApprovalRecord>("POST", `/approvals/${encodeURIComponent(id)}/reject`, body),
  cancelApproval: (id: string) => req<void>("DELETE", `/approvals/${encodeURIComponent(id)}`),

  listVersions: (params?: { resource_type?: string; resource_id?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (params?.resource_type) p.set("resource_type", params.resource_type);
    if (params?.resource_id) p.set("resource_id", params.resource_id);
    if (params?.limit !== undefined) p.set("limit", String(params.limit));
    const q = p.toString();
    return req<VersionListResponse>("GET", `/versions${q ? `?${q}` : ""}`);
  },
  getVersion: (id: string) => req<VersionRecord>("GET", `/versions/${encodeURIComponent(id)}`),
  restoreVersion: (id: string, side?: "before" | "after") =>
    req<VersionRestoreResponse>("POST", `/versions/${encodeURIComponent(id)}/restore`, side ? { side } : undefined),
};

export { ApiError };
