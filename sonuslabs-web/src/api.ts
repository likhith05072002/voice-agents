// Single source of truth for every backend call. No mock data anywhere.
// If VITE_API_BASE is set (dev: http://localhost:8001), use it. Otherwise fall
// back to the page's own origin — so a production build served BY the backend
// (same origin, one tunnel) just works, phone included.
const RAW = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
export const API_BASE =
  RAW || (typeof window !== "undefined" ? window.location.origin : "http://localhost:8001");

const WS_BASE = API_BASE.replace(/^http/, "ws");

export interface AgentLite { agent_id: string; name: string }

export interface AgentConfig {
  agent_id: string;
  name: string;
  language: string;
  voice: string;
  voice_pace?: number;
  system_prompt: string;
  greeting_text: string;
  knowledge_docs: string[];
  enable_rag?: boolean;
  enable_tools?: boolean;
  eagerness?: string;
  industry?: string;
  embed_enabled?: boolean;
  embed_origins?: string[];
  [k: string]: unknown;
}

export interface CallRecord {
  call_id: string;
  agent_id: string;
  from_number?: string;
  to_number?: string;
  started_at?: number;
  ended_at?: number;
  duration_s?: number;
  turn_count?: number;
  avg_perceived_ms?: number;
  outcome?: string;
  turns?: string; // JSON string of [{role,text}]
}

export interface LiveLine {
  t: number; call_id: string; role: string; text: string;
  agent_id?: string; number?: string;
}

export interface AuthUser { id: string; email: string; name: string; picture: string }

export interface LedgerEntry {
  delta_paise: number; balance_after: number; kind: string; ref: string;
  seconds: number | null; t: number;
}
export interface WalletInfo {
  balance_paise: number; seconds_left: number; rate_paise_per_min: number;
  trial_minutes: number; payments_enabled: boolean; dev_topup: boolean;
  ledger: LedgerEntry[];
}
export interface ApiKeyInfo {
  id: string; name: string; key_prefix: string; created_at: number;
  last_used_at?: number | null;
}
export interface Workspace {
  id: string; name: string; owner_user_id: string; created_at: number; role: string;
}
export interface AuthMe {
  enabled: boolean; user: AuthUser | null; workspaces: Workspace[];
  is_admin?: boolean; telephony?: boolean;
}

export interface AdminOverview {
  users: number; new_users_24h: number; workspaces: number; agents: number;
  api_keys: number; calls_total: number; calls_24h: number;
  minutes_total: number; minutes_24h: number;
  revenue_paid_paise: number; credits_issued_paise: number;
  usage_burned_paise: number; credits_outstanding_paise: number;
}
export interface AdminUser {
  id: string; email: string; name: string; created_at: number; last_login_at: number;
  balance_paise: number; workspaces: number; agents: number; spent_paise: number;
  api_keys: number;
}
export interface AdminCall {
  call_id: string; agent_id: string; from_number?: string; to_number?: string;
  started_at?: number; duration_s?: number; turn_count?: number;
  avg_perceived_ms?: number; outcome?: string;
  workspace_name?: string; owner_email?: string;
}
export interface OwnedNumber {
  number: string; country: string; monthly_paise: number; agent_id: string;
  assigned_at: number;
}
export interface NumberStock { country: string; n: number; monthly_paise: number }
export interface AdminNumber {
  number: string; country: string; monthly_paise: number; status: string;
  agent_id?: string | null; notes: string; assigned_at?: number | null;
  workspace_name?: string | null; owner_email?: string | null;
}

export interface AdminLedgerRow {
  delta_paise: number; balance_after: number; kind: string; ref: string;
  seconds: number | null; t: number; email: string;
}

// Read an uploaded file to base64 (KB uploads via /onboard/parse-doc).
export function fileToB64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(f);
  });
}

// Active workspace: set by the AuthProvider, sent on every console request so
// the backend scopes agents/calls to the tenant. Module-level (not React state)
// so the fetch layer stays dependency-free.
let _workspaceId = "";
export const setWorkspaceHeader = (id: string) => { _workspaceId = id; };

async function j<T>(path: string, opts?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (_workspaceId) headers["X-Workspace-Id"] = _workspaceId;
  const r = await fetch(API_BASE + path, {
    credentials: "include",   // the HttpOnly session cookie
    headers,
    ...opts,
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).error || msg; } catch { /* ignore */ }
    const err = new Error(msg) as Error & { status?: number };
    err.status = r.status;
    throw err;
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () => j<{ status: string; active_sessions: number; agents: number }>("/health"),

  // ─── auth + workspaces ───
  authMe: () => j<AuthMe>("/auth/me"),
  logout: () => j<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  // Full-page navigations (the OAuth redirect chain can't be fetch()ed):
  googleLoginUrl: (next: string) =>
    `${API_BASE}/auth/google/login?next=${encodeURIComponent(next)}`,
  devLoginUrl: (email: string, next: string) =>
    `${API_BASE}/auth/dev-login?email=${encodeURIComponent(email)}&next=${encodeURIComponent(next)}`,
  workspaces: () => j<{ workspaces: Workspace[] }>("/workspaces"),
  createWorkspace: (name: string) =>
    j<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name }) }),
  renameWorkspace: (id: string, name: string) =>
    j<Workspace>(`/workspaces/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),

  // ─── billing (credits) + API keys ───
  wallet: () => j<WalletInfo>("/billing/wallet"),
  topupDev: (amount_paise: number) =>
    j<{ balance_paise: number }>("/billing/topup/dev", {
      method: "POST", body: JSON.stringify({ amount_paise }) }),
  topupOrder: (amount_paise: number) =>
    j<{ order_id: string; amount_paise: number; key_id: string; currency: string }>(
      "/billing/topup/order", { method: "POST", body: JSON.stringify({ amount_paise }) }),
  topupVerify: (payload: Record<string, string>) =>
    j<{ balance_paise: number }>("/billing/topup/verify", {
      method: "POST", body: JSON.stringify(payload) }),
  // ─── phone numbers ───
  numbers: () => j<{ numbers: OwnedNumber[]; available: NumberStock[] }>("/numbers"),
  claimNumber: (agent_id: string, country: string) =>
    j<{ number: string; agent_id: string; monthly_paise: number }>(
      "/numbers/claim", { method: "POST", body: JSON.stringify({ agent_id, country }) }),
  releaseNumber: (number: string) =>
    j<{ released: string }>("/numbers/release", {
      method: "POST", body: JSON.stringify({ number }) }),

  // ─── admin (platform operators only; 404 for everyone else) ───
  adminNumbers: () => j<{ numbers: AdminNumber[] }>("/admin/numbers"),
  adminAddNumber: (body: { number: string; country: string; monthly_paise: number; notes?: string }) =>
    j<{ number: string }>("/admin/numbers", { method: "POST", body: JSON.stringify(body) }),
  adminOverview: () => j<AdminOverview>("/admin/overview"),
  adminUsers: () => j<{ users: AdminUser[] }>("/admin/users"),
  adminCalls: () => j<{ calls: AdminCall[] }>("/admin/calls"),
  adminLedger: () => j<{ ledger: AdminLedgerRow[] }>("/admin/ledger"),
  adminLive: (since: number) =>
    j<{ active: number; lines: (LiveLine & { agent_id?: string })[] }>(
      `/admin/live?since=${since}`),

  apiKeys: () => j<{ keys: ApiKeyInfo[] }>("/api-keys"),
  createApiKey: (name: string) =>
    j<ApiKeyInfo & { key: string }>("/api-keys", {
      method: "POST", body: JSON.stringify({ name }) }),
  revokeApiKey: (id: string) =>
    j<{ revoked: string }>(`/api-keys/${id}`, { method: "DELETE" }),
  agentsLite: () => j<{ agents: AgentLite[] }>("/agents-lite"),
  agents: () => j<{ agents: AgentConfig[] }>("/agents"),
  agent: (id: string) => j<AgentConfig>(`/agents/${id}`),
  createAgent: (body: Partial<AgentConfig>) =>
    j<AgentConfig>("/agents", { method: "POST", body: JSON.stringify(body) }),
  updateAgent: (id: string, patch: Partial<AgentConfig>) =>
    j<AgentConfig>(`/agents/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteAgent: (id: string) =>
    j<{ deleted: string }>(`/agents/${id}`, { method: "DELETE" }),

  research: (website_url: string, description: string) =>
    j<AgentConfig>("/onboard/research", {
      method: "POST",
      body: JSON.stringify({ website_url, description }),
    }),
  enhancePrompt: (description: string, business_name?: string) =>
    j<{ system_prompt: string }>("/onboard/enhance", {
      method: "POST", body: JSON.stringify({ description, business_name }) }),
  parseDoc: (filename: string, content_b64: string) =>
    j<{ docs: string[]; chars: number }>("/onboard/parse-doc", {
      method: "POST", body: JSON.stringify({ filename, content_b64 }) }),

  demoCallMe: (phone: string) =>
    j<{ status: string; max_seconds: number }>("/demo/call-me", {
      method: "POST", body: JSON.stringify({ phone }) }),

  voiceLab: () => j<{ voices: string[] }>("/voice-lab"),
  cloneVoice: (language: "en" | "hi", audio_b64: string) =>
    j<{ voice_id: string; expires_in_s: number }>("/voice-clone", {
      method: "POST", body: JSON.stringify({ language, audio_b64 }) }),
  cloneAgentVoice: (agentId: string, audio_b64: string) =>
    j<{ voice_id: string; voice: string }>(`/agents/${agentId}/voice-clone`, {
      method: "POST", body: JSON.stringify({ audio_b64 }) }),
  deleteAgentVoice: (agentId: string) =>
    j<{ voice: string; deleted: string }>(`/agents/${agentId}/voice-clone`, {
      method: "DELETE" }),
  voiceSampleUrl: (voice: string) => `${API_BASE}/voice-sample/${voice}`,
  languageSampleUrl: (lang: string) => `${API_BASE}/language-sample/${lang}`,

  calls: (limit = 50) => j<{ calls: CallRecord[] }>(`/calls?limit=${limit}`),
  callsSearch: (q: string) =>
    j<{ calls: CallRecord[] }>(`/calls/search?q=${encodeURIComponent(q)}`),
  analytics: () => j<Record<string, unknown>>("/analytics"),
  liveTranscript: (since: number) =>
    j<{ lines: LiveLine[]; active: number }>(`/live-transcript?since=${since}`),

  callMe: (to: string, agent_id: string) =>
    j<{ status?: string; error?: string }>("/test/call-me", {
      method: "POST",
      body: JSON.stringify({ to, agent_id }),
    }),

  webCallUrl: (agentId: string, voice?: string, pace?: number) =>
    `${WS_BASE}/web-call?agent_id=${encodeURIComponent(agentId)}` +
    (voice ? `&voice=${encodeURIComponent(voice)}` : "") +
    (pace ? `&pace=${encodeURIComponent(String(pace))}` : ""),
};
