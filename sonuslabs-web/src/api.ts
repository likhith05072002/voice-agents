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

export interface LiveLine { t: number; call_id: string; role: string; text: string }

async function j<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, {
    headers: { "content-type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).error || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () => j<{ status: string; active_sessions: number; agents: number }>("/health"),
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

  voiceLab: () => j<{ voices: string[] }>("/voice-lab"),
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
