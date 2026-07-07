import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CallPanel } from "../components/CallPanel";
import { api, AgentConfig, AgentLite, ApiKeyInfo, CallRecord, LiveLine, WalletInfo } from "../api";
import { C, serif, mono, serif as SF, langLabel, LANGS } from "../theme";
import { useIsMobile } from "../useIsMobile";
import { useAuth } from "../auth";

type Tab = "agents" | "phone" | "calls" | "live" | "voice" | "analytics" | "billing" | "api";
const TABS: [Tab, string][] = [["agents", "Agents"], ["phone", "Phone"], ["live", "Live"],
  ["calls", "Call logs"], ["voice", "Voice Lab"], ["analytics", "Analytics"],
  ["billing", "Billing"], ["api", "API"]];

const rupees = (paise: number) => `₹${(paise / 100).toFixed(2)}`;
const mins = (seconds: number) => Math.floor(seconds / 60);

/* ─── workspace switcher (accounts mode only) ─── */
function WorkspaceSwitcher() {
  const { user, workspaces, wsId, setWsId, refresh } = useAuth();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  if (!user) return null;   // legacy server: no workspaces concept

  const createWs = async () => {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    try {
      const ws = await api.createWorkspace(n);
      await refresh();
      setWsId(ws.id);
      setAdding(false);
      setName("");
    } catch { /* keep the input open */ }
    setBusy(false);
  };

  return (
    <div style={{ padding: "0 4px 12px" }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".09em",
        textTransform: "uppercase", color: C.darkMuted, padding: "0 8px 6px" }}>
        Workspace
      </div>
      <select value={wsId}
        onChange={(e) => {
          if (e.target.value === "__new__") { setAdding(true); return; }
          setWsId(e.target.value);
        }}
        style={{ width: "100%", background: C.darkCard, color: C.darkText,
          border: `1px solid ${C.darkLine}`, borderRadius: 10, padding: "9px 10px",
          fontSize: 13.5, fontWeight: 600, outline: "none", cursor: "pointer" }}>
        {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        <option value="__new__">+ New workspace…</option>
      </select>
      {adding && (
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createWs(); if (e.key === "Escape") setAdding(false); }}
            placeholder="Workspace name"
            style={{ flex: 1, minWidth: 0, background: C.darkCard, color: C.darkText,
              border: `1px solid ${C.darkLine}`, borderRadius: 9, padding: "8px 10px",
              fontSize: 13, outline: "none" }} />
          <button onClick={createWs} disabled={busy}
            style={{ border: "none", borderRadius: 9, padding: "8px 11px", fontSize: 13,
              fontWeight: 700, background: C.accent, color: C.ink, cursor: "pointer",
              opacity: busy ? 0.6 : 1 }}>
            {busy ? "…" : "Add"}
          </button>
        </div>
      )}
    </div>
  );
}

function BalanceBadge({ onOpen }: { onOpen: () => void }) {
  const { user } = useAuth();
  const [w, setW] = useState<{ balance_paise: number; seconds_left: number } | null>(null);
  useEffect(() => {
    if (!user) return;
    let alive = true;
    const load = () => api.wallet().then((r) => { if (alive) setW(r); }).catch(() => {});
    load();
    const iv = setInterval(load, 30000);   // refresh after calls burn credits
    return () => { alive = false; clearInterval(iv); };
  }, [user]);
  if (!user || !w) return null;
  const low = w.seconds_left < 300;        // < 5 min: nudge
  return (
    <div onClick={onOpen} title="Open billing"
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        margin: "0 4px 10px", padding: "8px 11px", borderRadius: 10, cursor: "pointer",
        background: C.darkCard, border: `1px solid ${low ? "#5A3326" : C.darkLine}` }}>
      <span style={{ fontSize: 12.5, fontWeight: 700, color: low ? C.red : C.darkText }}>
        {rupees(w.balance_paise)}
      </span>
      <span style={{ fontSize: 11.5, color: C.darkMuted }}>~{mins(w.seconds_left)} min</span>
    </div>
  );
}

function UserRow() {
  const nav = useNavigate();
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 10px 2px" }}>
      <div style={{ width: 26, height: 26, borderRadius: "50%", overflow: "hidden",
        background: C.darkCard, border: `1px solid ${C.darkLine}`, display: "flex",
        alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        {user.picture
          ? <img src={user.picture} alt="" referrerPolicy="no-referrer"
              style={{ width: "100%", height: "100%", objectFit: "cover" }} />
          : <span style={{ fontSize: 12, fontWeight: 700, color: C.accent }}>
              {(user.name || user.email)[0]?.toUpperCase()}
            </span>}
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, color: C.darkText, whiteSpace: "nowrap",
          overflow: "hidden", textOverflow: "ellipsis" }}>{user.name || user.email}</div>
      </div>
      <span onClick={async () => { await logout(); nav("/"); }}
        title="Sign out"
        style={{ fontSize: 11.5, fontWeight: 700, color: C.darkMuted, cursor: "pointer",
          whiteSpace: "nowrap" }}>
        Sign out
      </span>
    </div>
  );
}

export function Console() {
  const nav = useNavigate();
  const mob = useIsMobile();
  const { wsId } = useAuth();
  const [tab, setTab] = useState<Tab>("agents");
  const [liveCount, setLiveCount] = useState(0);

  const tabRow = TABS.map(([k, label]) => {
    const on = tab === k;
    return (
      <div key={k} onClick={() => setTab(k)} style={{ display: "flex", alignItems: "center", gap: 11,
        padding: mob ? "9px 13px" : "10px 12px", borderRadius: 10, cursor: "pointer",
        fontSize: 14.5, fontWeight: 600, whiteSpace: "nowrap",
        background: on ? C.darkCard : "transparent", color: on ? C.darkText : C.darkMuted }}>
        <span style={{ width: 7, height: 7, borderRadius: 2, background: on ? C.accent : "#463F32" }} />
        {label}
        {k === "live" && liveCount > 0 && (
          <span style={{ marginLeft: mob ? 4 : "auto", display: "flex", alignItems: "center", gap: 5,
            fontSize: 10.5, fontWeight: 700, color: C.red }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.red, animation: "sl-livedot 1.4s infinite" }} />
            {liveCount}</span>
        )}
      </div>
    );
  });

  // key={wsId}: switching workspace remounts every tab so all data refetches
  // under the new X-Workspace-Id — no per-tab plumbing.
  const body = (
    <div key={wsId || "legacy"}>
      {tab === "agents" && <AgentsTab />}
      {tab === "phone" && <PhoneTab />}
      {tab === "calls" && <CallsTab />}
      {tab === "live" && <LiveTab onCount={setLiveCount} />}
      {tab === "voice" && <VoiceTab />}
      {tab === "analytics" && <AnalyticsTab />}
      {tab === "billing" && <BillingTab />}
      {tab === "api" && <DevelopersTab />}
    </div>
  );

  if (mob) {
    // Mobile: sticky top bar (logo + New) + horizontal scrollable tabs, content below.
    return (
      <div style={{ minHeight: "100vh", background: C.dark, color: C.darkText }}>
        <div style={{ position: "sticky", top: 0, zIndex: 20, background: C.dark,
          borderBottom: `1px solid ${C.darkLine}` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px" }}>
            <div onClick={() => nav("/")} style={{ display: "flex", alignItems: "center", gap: 9, cursor: "pointer" }}>
              <div style={{ width: 24, height: 24, borderRadius: 7, background: C.accent }} />
              <span style={{ fontFamily: serif, fontSize: 18 }}>SonusLabs</span>
            </div>
            <div onClick={() => nav("/create")} style={{ fontSize: 13, fontWeight: 600, background: C.accent,
              color: C.ink, borderRadius: 9, padding: "8px 13px", cursor: "pointer" }}>+ New</div>
          </div>
          <div style={{ padding: "0 16px 8px" }}><WorkspaceSwitcher /></div>
          <div style={{ display: "flex", gap: 4, overflowX: "auto", padding: "0 12px 10px",
            WebkitOverflowScrolling: "touch" }}>{tabRow}</div>
        </div>
        <div style={{ padding: "18px 16px" }}>{body}</div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: C.dark, color: C.darkText,
      display: "grid", gridTemplateColumns: "216px 1fr" }}>
      {/* side nav */}
      <div style={{ borderRight: `1px solid ${C.darkLine}`, padding: "22px 14px", display: "flex",
        flexDirection: "column", gap: 4 }}>
        <div onClick={() => nav("/")} style={{ display: "flex", alignItems: "center", gap: 9, cursor: "pointer",
          padding: "0 10px 16px" }}>
          <div style={{ width: 26, height: 26, borderRadius: 8, background: C.accent }} />
          <span style={{ fontFamily: serif, fontSize: 19, color: C.darkText }}>SonusLabs</span>
        </div>
        <WorkspaceSwitcher />
        <BalanceBadge onOpen={() => setTab("billing")} />
        {tabRow}
        <div style={{ marginTop: "auto", paddingTop: 12, borderTop: `1px solid ${C.darkLine}` }}>
          <div onClick={() => nav("/create")} style={{ display: "flex", alignItems: "center", gap: 9,
            padding: "11px 12px", borderRadius: 10, cursor: "pointer", fontSize: 14, fontWeight: 600,
            background: C.accent, color: C.ink }}>+ New agent</div>
          <UserRow />
        </div>
      </div>

      <div style={{ padding: "26px 30px", overflowX: "hidden" }}>{body}</div>
    </div>
  );
}

/* ─── AGENTS ─── */
function AgentsTab() {
  const nav = useNavigate();
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const reload = () => api.agentsLite().then((r) => { setAgents(r.agents); setLoaded(true); })
    .catch(() => setLoaded(true));
  useEffect(() => { reload(); }, []);

  if (sel) return <AgentDetail id={sel} onClose={() => { setSel(null); reload(); }} />;
  if (loaded && agents.length === 0) return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      minHeight: "60vh", textAlign: "center" }}>
      <div style={{ width: 56, height: 56, borderRadius: 16, background: C.darkCard, border: `1px solid ${C.darkLine}`,
        display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 18 }}>
        <div style={{ width: 16, height: 16, borderRadius: "50%", background: C.accent }} /></div>
      <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 32, marginBottom: 8 }}>No agents yet</h1>
      <p style={{ fontSize: 15, color: C.darkMuted, maxWidth: 380, lineHeight: 1.5, marginBottom: 22 }}>
        Create your first AI receptionist — paste your website and we'll research your business and draft it for you.</p>
      <button onClick={() => nav("/create")} style={{ fontSize: 15, fontWeight: 600, color: C.ink,
        background: C.accent, border: "none", borderRadius: 12, padding: "13px 24px", cursor: "pointer" }}>
        Create your first agent →</button>
    </div>
  );
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 30 }}>Agents</h1>
        <span style={{ fontSize: 13, color: C.darkMuted }}>{agents.length} {agents.length === 1 ? "agent" : "agents"}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(230px,1fr))", gap: 16 }}>
        {agents.map((a) => (
          <div key={a.agent_id} onClick={() => setSel(a.agent_id)} style={card}>
            <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 14 }}>
              <div style={{ width: 40, height: 40, borderRadius: 11, background: C.accent, color: C.ink,
                display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 16 }}>
                {a.name[0]?.toUpperCase()}</div>
              <div><div style={{ fontWeight: 700, fontSize: 15 }}>{a.name}</div>
                <div style={{ fontSize: 12, color: C.darkMuted }}>{a.agent_id}</div></div>
            </div>
            <div style={{ fontSize: 12, color: "#9A907C" }}>Tap to edit & talk →</div>
          </div>
        ))}
      </div>
    </>
  );
}

function AgentDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const mob = useIsMobile();
  const { telephony } = useAuth();
  const [a, setA] = useState<AgentConfig | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const [phone, setPhone] = useState("");
  const [callMsg, setCallMsg] = useState("");

  useEffect(() => { api.agent(id).then(setA).catch(() => {}); api.voiceLab().then((r) => setVoices(r.voices)); }, [id]);
  if (!a) return <div style={{ color: C.darkMuted }}>Loading…</div>;
  const upd = (p: Partial<AgentConfig>) => setA({ ...a, ...p });

  const save = async () => { await api.updateAgent(id, a); setSaved(true); setTimeout(() => setSaved(false), 1500); };
  const del = async () => { if (confirm(`Delete ${a.name}?`)) { await api.deleteAgent(id); onClose(); } };
  const callMe = async () => {
    if (!/^\+\d{8,15}$/.test(phone)) { setCallMsg("Enter as +91XXXXXXXXXX"); return; }
    setCallMsg("calling…");
    const r = await api.callMe(phone, id);
    setCallMsg(r.error ? "❌ " + r.error : "📞 ringing — pick up!");
  };

  return (
    <>
      {/* Top action bar: breadcrumb + Save/Delete always visible, no scrolling */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 22 }}>
        <span onClick={onClose} style={{ cursor: "pointer", color: "#9A907C", fontSize: 14, fontWeight: 600 }}>← Agents</span>
        <span style={{ color: "#463F32" }}>/</span><span style={{ fontWeight: 700 }}>{a.name}</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10 }}>
          <button onClick={del} style={{ fontSize: 14, fontWeight: 600, color: C.red, background: "transparent",
            border: "1px solid #4A2E29", borderRadius: 10, padding: "9px 16px", cursor: "pointer" }}>Delete</button>
          <button onClick={save} style={{ fontSize: 14, fontWeight: 600, color: C.ink, background: C.accent,
            border: "none", borderRadius: 10, padding: "9px 18px", cursor: "pointer" }}>{saved ? "Saved ✓" : "Save changes"}</button>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: mob ? "1fr" : "1fr 340px", gap: 22, alignItems: "start" }}>
        <div style={{ ...cardPad, display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: mob ? "1fr" : "1fr 1fr", gap: 14 }}>
            <div><div style={dlbl}>NAME</div>
              <input value={a.name} onChange={(e) => upd({ name: e.target.value })} style={dfield} /></div>
            <div><div style={dlbl}>LANGUAGE</div>
              <select value={a.language} onChange={(e) => upd({ language: e.target.value })} style={dfield}>
                {LANGS.map((o) => <option key={o.v} value={o.v}>{o.en}</option>)}</select></div>
          </div>
          <div><div style={dlbl}>GREETING</div>
            <textarea value={a.greeting_text} onChange={(e) => upd({ greeting_text: e.target.value })}
              style={{ ...dfield, minHeight: 56, resize: "vertical", lineHeight: 1.5 }} /></div>
          <div style={{ display: "grid", gridTemplateColumns: mob ? "1fr" : "1fr 1fr", gap: 14 }}>
            <div><div style={dlbl}>VOICE</div>
              <select value={a.voice} onChange={(e) => upd({ voice: e.target.value })} style={dfield}>
                {voices.map((v) => <option key={v} value={v}>{v}</option>)}</select></div>
            <div><div style={dlbl}>EAGERNESS</div>
              <select value={a.eagerness || "balanced"} onChange={(e) => upd({ eagerness: e.target.value })} style={dfield}>
                <option value="cautious">Cautious</option>
                <option value="balanced">Balanced (default)</option>
                <option value="eager">Snappy (may interrupt more)</option></select></div>
          </div>
          <div><div style={dlbl}>PACE · {(a.voice_pace ?? 1).toFixed(1)}×</div>
            <input type="range" min={0.5} max={2} step={0.1} value={a.voice_pace ?? 1}
              onChange={(e) => upd({ voice_pace: parseFloat(e.target.value) })}
              style={{ width: "100%", accentColor: C.accent }} /></div>
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
              <div style={{ ...dlbl, marginBottom: 0 }}>KNOWLEDGE · {(a.knowledge_docs || []).length} facts</div>
              <button onClick={() => upd({ knowledge_docs: [...(a.knowledge_docs || []), ""] })}
                style={{ fontSize: 12.5, fontWeight: 600, color: C.ink, background: C.accent, border: "none",
                  borderRadius: 8, padding: "6px 11px", cursor: "pointer" }}>+ Add fact</button>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(a.knowledge_docs || []).map((d, i) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                  <textarea value={d} onChange={(e) => {
                    const docs = [...a.knowledge_docs]; docs[i] = e.target.value; upd({ knowledge_docs: docs });
                  }} style={{ ...dfield, flex: 1, minHeight: 40, resize: "vertical", fontSize: 13, lineHeight: 1.4 }} />
                  <span onClick={() => upd({ knowledge_docs: a.knowledge_docs.filter((_, j) => j !== i) })}
                    title="Remove fact" style={{ cursor: "pointer", color: "#7A7160", fontSize: 18, lineHeight: 1,
                    padding: "8px 6px" }}>×</span>
                </div>
              ))}
              {(a.knowledge_docs || []).length === 0 && (
                <div style={{ fontSize: 13, color: C.darkMuted }}>No facts yet — add what the agent should know.</div>
              )}
            </div></div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ background: C.paper, borderRadius: 16, padding: 4 }}>
            <CallPanel agentId={id} subtitle="uses your current settings" orbSize={170}
              forceVoice={a.voice} forcePace={a.voice_pace} /></div>
          <div style={{ ...cardPad }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.darkMuted, marginBottom: 10 }}>TEST-CALL MY PHONE</div>
            {telephony ? (<>
              <div style={{ display: "flex", gap: 8 }}>
                <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98…"
                  style={{ ...dfield, flex: 1 }} />
                <button onClick={callMe} style={{ fontWeight: 600, color: C.ink, background: C.accent, border: "none",
                  borderRadius: 9, padding: "10px 14px", cursor: "pointer", whiteSpace: "nowrap" }}>Call me</button>
              </div>
              {callMsg && <div style={{ fontSize: 12.5, color: "#B7AD98", marginTop: 8 }}>{callMsg}</div>}
            </>) : (
              <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13, color: C.darkMuted, lineHeight: 1.5 }}>
                <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: ".06em", textTransform: "uppercase",
                  color: C.accent, border: `1px solid ${C.darkLine}`, borderRadius: 6, padding: "2px 7px" }}>Coming soon</span>
                <span>Phone calls launch soon. For now, talk to your agent with the orb, or use the live API.</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/* ─── CALLS ─── */
function CallsTab() {
  const mob = useIsMobile();
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    api.calls(50).then((r) => setCalls(r.calls)).catch(() => {});
    api.agentsLite().then((r) =>
      setNames(Object.fromEntries(r.agents.map((a) => [a.agent_id, a.name])))).catch(() => {});
  }, []);
  useEffect(() => {
    const t = setTimeout(() => {
      (q.trim() ? api.callsSearch(q.trim()) : api.calls(50)).then((r) => setCalls(r.calls)).catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [q]);
  const fmt = (s?: number) => s ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "—";
  const parseTurns = (c: CallRecord) => { try { return JSON.parse(c.turns || "[]"); } catch { return []; } };
  // Show the OTHER party's number: outbound => to, inbound => from. Browser
  // test calls have no numbers.
  const peer = (c: CallRecord) => c.to_number || c.from_number || "";
  const cols = "128px 150px 1fr 60px 78px 82px";

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
        <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 30 }}>Call logs</h1>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search transcripts…"
          style={{ width: mob ? 150 : 260, background: C.darkCard, border: `1px solid ${C.darkLine}`, borderRadius: 10,
            padding: "10px 13px", color: C.darkText, fontSize: 14, outline: "none" }} />
      </div>
      <div style={{ ...cardPad, padding: 0, overflowX: mob ? "auto" : "hidden", overflowY: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: cols, gap: 12,
          minWidth: mob ? 620 : undefined,
          padding: "12px 18px", fontSize: 11.5, fontWeight: 700, color: C.darkMuted, borderBottom: `1px solid ${C.darkLine}` }}>
          <span>WHEN</span><span>NUMBER</span><span>AGENT</span><span>DUR</span><span>PERCEIVED</span><span>OUTCOME</span>
        </div>
        {calls.length === 0 && <div style={{ padding: 40, textAlign: "center", color: C.darkMuted, fontSize: 14 }}>No calls yet. Make one from Agents → Call me, or wait for an inbound call.</div>}
        {calls.map((c) => {
          const lat = c.avg_perceived_ms || 0;
          const num = peer(c);
          return (
            <div key={c.call_id}>
              <div onClick={() => setOpen(open === c.call_id ? null : c.call_id)} style={{ display: "grid",
                gridTemplateColumns: cols, gap: 12, minWidth: mob ? 620 : undefined,
                padding: "14px 18px", fontSize: 13.5,
                borderBottom: "1px solid #241F18", cursor: "pointer", alignItems: "center" }}>
                <span style={{ color: "#B7AD98", fontSize: 12.5 }}>
                  {c.started_at ? new Date(c.started_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</span>
                <span style={{ fontFamily: mono, fontWeight: 700, fontSize: 13,
                  color: num ? C.darkText : C.darkMuted }}>{num || "web"}</span>
                <span style={{ fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{names[c.agent_id] || c.agent_id}</span>
                <span style={{ color: "#B7AD98" }}>{fmt(c.duration_s)}</span>
                <span style={{ color: lat > 1500 ? C.red : lat > 900 ? C.accent : C.green, fontFamily: mono, fontSize: 12.5 }}>
                  {lat ? `${lat}ms` : "—"}</span>
                <span><span style={{ background: "#2A251C", color: "#B7AD98", fontSize: 11, fontWeight: 700,
                  padding: "3px 9px", borderRadius: 100 }}>{c.outcome || "—"}</span></span>
              </div>
              {open === c.call_id && (
                <div style={{ padding: "14px 18px 18px", background: "#1A1610", borderBottom: "1px solid #241F18" }}>
                  <div style={{ fontSize: 12, color: C.darkMuted, marginBottom: 10, fontFamily: mono }}>
                    {num ? `☎ ${num}` : "browser call"} · {c.turn_count ?? 0} turns · {fmt(c.duration_s)}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {parseTurns(c).length === 0 && <div style={{ color: C.darkMuted, fontSize: 13 }}>No transcript recorded.</div>}
                    {parseTurns(c).map((t: any, i: number) => {
                      const role = t.role || t[0]; const text = t.text || t[1];
                      return (
                        <div key={i} style={{ maxWidth: "74%", padding: "8px 12px", borderRadius: 12, fontSize: 13, lineHeight: 1.4,
                          ...(role === "user"
                            ? { alignSelf: "flex-end", background: C.accent, color: C.ink }
                            : { alignSelf: "flex-start", background: "#2A251C", color: C.darkText }) }}>{text}</div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

/* ─── LIVE ─── */
interface LiveCall { call_id: string; number: string; agent_id: string; lines: LiveLine[]; last: number }

function LiveTab({ onCount }: { onCount: (n: number) => void }) {
  const mob = useIsMobile();
  const [calls, setCalls] = useState<Record<string, LiveCall>>({});
  const [active, setActive] = useState(0);
  const [sel, setSel] = useState<string | null>(null);
  const since = useRef(Date.now() / 1000 - 120);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await api.liveTranscript(since.current);
        if (!alive) return;
        if (r.lines.length) {
          since.current = Math.max(since.current, ...r.lines.map((l) => l.t));
          setCalls((prev) => {
            const next = { ...prev };
            for (const l of r.lines) {
              const c = next[l.call_id] || { call_id: l.call_id, number: l.number || "",
                agent_id: l.agent_id || "", lines: [], last: l.t };
              next[l.call_id] = { ...c, number: l.number || c.number,
                agent_id: l.agent_id || c.agent_id, lines: [...c.lines, l].slice(-200), last: l.t };
            }
            return next;
          });
        }
        setActive(r.active); onCount(r.active);
      } catch { /* ignore */ }
    };
    const t = setInterval(poll, 700); poll();
    return () => { alive = false; clearInterval(t); };
  }, [onCount]);

  // auto-pick the most recent call, and auto-scroll its transcript
  const list = Object.values(calls).sort((a, b) => b.last - a.last);
  useEffect(() => { if (!sel && list.length) setSel(list[0].call_id); }, [list, sel]);
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; });
  const current = sel ? calls[sel] : null;
  const isLive = (c: LiveCall) => Date.now() / 1000 - c.last < 20;
  const fmtNum = (n: string) => n || "web / browser";

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 30 }}>Live</h1>
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, color: C.red,
          background: "#2A1D18", border: "1px solid #4A2E29", borderRadius: 100, padding: "4px 11px" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: C.red, animation: "sl-livedot 1.4s infinite" }} />
          {active} active</span>
      </div>
      {list.length === 0 ? (
        <div style={{ ...cardPad, color: C.darkMuted, fontSize: 14, minHeight: 200 }}>
          No live calls right now. Start one from a phone call (Agents → Call me) or the talk orb.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: mob ? "1fr" : "260px 1fr", gap: 14 }}>
          {/* call list */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {list.map((c) => {
              const on = c.call_id === sel; const live = isLive(c);
              return (
                <div key={c.call_id} onClick={() => setSel(c.call_id)}
                  style={{ ...cardPad, padding: "12px 14px", cursor: "pointer",
                    border: `1px solid ${on ? C.accent : C.darkLine}` }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: "50%",
                      background: live ? C.red : C.darkMuted,
                      animation: live ? "sl-livedot 1.4s infinite" : "none" }} />
                    <span style={{ fontFamily: mono, fontSize: 14, fontWeight: 700 }}>{fmtNum(c.number)}</span>
                  </div>
                  <div style={{ fontSize: 11.5, color: C.darkMuted, marginTop: 5 }}>
                    {c.agent_id} · {c.lines.length} lines · {new Date(c.last * 1000).toLocaleTimeString()}
                  </div>
                </div>
              );
            })}
          </div>
          {/* transcript */}
          <div ref={bodyRef} style={{ ...cardPad, maxHeight: 480, overflowY: "auto" }}>
            {current && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12,
                position: "sticky", top: -16, background: C.darkCard, padding: "2px 0 8px" }}>
                <span style={{ fontFamily: mono, fontSize: 15, fontWeight: 700 }}>{fmtNum(current.number)}</span>
                {isLive(current) && <span style={{ fontSize: 10.5, fontWeight: 800, color: C.red,
                  textTransform: "uppercase" }}>● live</span>}
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {current?.lines.map((l, i) => (
                <div key={i} style={{ maxWidth: "80%", padding: "8px 12px", borderRadius: 12, fontSize: 13,
                  lineHeight: 1.45, ...(l.role === "user"
                    ? { alignSelf: "flex-end", background: C.accent, color: C.ink }
                    : { alignSelf: "flex-start", background: "#2A251C", color: C.darkText }) }}>
                  {l.text}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ─── VOICE LAB ─── */
function VoiceTab() {
  const [voices, setVoices] = useState<string[]>([]);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [playing, setPlaying] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => { api.voiceLab().then((r) => setVoices(r.voices)); api.agentsLite().then((r) => setAgents(r.agents)); }, []);
  const play = (v: string) => {
    if (!audioRef.current) audioRef.current = new Audio();
    setPlaying(v); audioRef.current.src = api.voiceSampleUrl(v);
    audioRef.current.onended = () => setPlaying(null);
    audioRef.current.play().catch(() => setPlaying(null));
  };
  const assign = async (voice: string, agentId: string) => {
    if (!agentId) return;
    await api.updateAgent(agentId, { voice });
    const name = agents.find((a) => a.agent_id === agentId)?.name || agentId;
    setToast(`${voice[0].toUpperCase()}${voice.slice(1)} is now ${name}'s voice.`);
    setTimeout(() => setToast(""), 2500);
  };
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 30 }}>Voice Lab</h1>
        {toast && <span style={{ fontSize: 13, fontWeight: 600, color: C.ink, background: C.accent,
          borderRadius: 100, padding: "5px 12px", animation: "sl-fadeup .3s" }}>✓ {toast}</span>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(230px,1fr))", gap: 16 }}>
        {voices.map((v) => (
          <div key={v} style={{ ...cardPad }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 15, textTransform: "capitalize" }}>{v}</div>
              <div onClick={() => play(v)} style={{ width: 38, height: 38, borderRadius: "50%", background: C.accent,
                display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                {playing === v
                  ? <div style={{ display: "flex", gap: 2, height: 14 }}>
                      {[0, .2, .4].map((d) => <span key={d} style={{ width: 2.5, background: C.ink, borderRadius: 2,
                        animation: `sl-eq .6s infinite ${d}s` }} />)}</div>
                  : <div style={{ width: 0, height: 0, borderLeft: `9px solid ${C.ink}`, borderTop: "6px solid transparent",
                      borderBottom: "6px solid transparent", marginLeft: 2 }} />}
              </div>
            </div>
            <select defaultValue="" onChange={(e) => assign(v, e.target.value)} style={{ width: "100%", background: C.dark,
              border: `1px solid ${C.darkLine}`, borderRadius: 9, padding: "9px 11px", color: "#B7AD98", fontSize: 13, outline: "none" }}>
              <option value="">Set as default for…</option>
              {agents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.name}</option>)}
            </select>
          </div>
        ))}
      </div>
    </>
  );
}

/* ─── ANALYTICS ─── */
function AnalyticsTab() {
  const mob = useIsMobile();
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    api.calls(500).then((r) => setCalls(r.calls)).catch(() => {});
    api.agentsLite().then((r) =>
      setNames(Object.fromEntries(r.agents.map((a) => [a.agent_id, a.name])))).catch(() => {});
  }, []);
  const total = calls.length;
  const avgLat = total ? Math.round(calls.reduce((s, c) => s + (c.avg_perceived_ms || 0), 0) / total) : 0;
  const totalSecs = calls.reduce((s, c) => s + (c.duration_s || 0), 0);
  const mins = Math.round(totalSecs / 60);
  const avgLen = total ? Math.round(totalSecs / total) : 0;
  const cards = [
    { label: "Total calls", value: String(total), sub: "all time" },
    { label: "Avg response", value: avgLat ? `${avgLat}ms` : "—", sub: "perceived latency" },
    { label: "Minutes handled", value: String(mins), sub: `≈ ₹${mins * 3} at ₹3/min` },
    { label: "Avg call length", value: avgLen ? `${Math.floor(avgLen / 60)}:${String(avgLen % 60).padStart(2, "0")}` : "—", sub: "minutes:seconds" },
  ];
  // Real breakdown: calls per agent (outcomes aren't classified server-side yet).
  const byAgent = Object.entries(
    calls.reduce<Record<string, number>>((m, c) => {
      m[c.agent_id] = (m[c.agent_id] || 0) + 1; return m;
    }, {})
  ).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const maxAgent = byAgent[0]?.[1] || 1;
  return (
    <>
      <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 30, marginBottom: 18 }}>Analytics</h1>
      <div style={{ display: "grid", gridTemplateColumns: mob ? "1fr 1fr" : "repeat(4,1fr)", gap: mob ? 12 : 16, marginBottom: 22 }}>
        {cards.map((s) => (
          <div key={s.label} style={{ ...cardPad }}>
            <div style={{ fontSize: 12, color: C.darkMuted, marginBottom: 8 }}>{s.label}</div>
            <div style={{ fontFamily: serif, fontSize: 34, color: C.accent }}>{s.value}</div>
            <div style={{ fontSize: 12, color: "#6B6250", marginTop: 4 }}>{s.sub}</div>
          </div>
        ))}
      </div>
      <div style={{ ...cardPad, maxWidth: 520 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: C.darkMuted, marginBottom: 18 }}>CALLS BY AGENT</div>
        {byAgent.length === 0 && <div style={{ color: C.darkMuted, fontSize: 14 }}>No calls yet.</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {byAgent.map(([id, n]) => (
            <div key={id}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}>
                <span style={{ color: "#D8CFBE" }}>{names[id] || id}</span>
                <span style={{ color: C.darkMuted }}>{n} {n === 1 ? "call" : "calls"}</span></div>
              <div style={{ height: 8, background: C.dark, borderRadius: 6, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${Math.round(n / maxAgent * 100)}%`, background: C.accent, borderRadius: 6 }} /></div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

const card: React.CSSProperties = { background: C.darkCard, border: `1px solid ${C.darkLine}`, borderRadius: 16,
  padding: 20, cursor: "pointer" };
const cardPad: React.CSSProperties = { background: C.darkCard, border: `1px solid ${C.darkLine}`, borderRadius: 16, padding: 18 };
const dlbl: React.CSSProperties = { fontSize: 11.5, fontWeight: 700, color: C.darkMuted, marginBottom: 6 };
const dfield: React.CSSProperties = { width: "100%", background: C.dark, border: `1px solid ${C.darkLine}`,
  borderRadius: 9, padding: "10px 12px", color: C.darkText, fontSize: 14.5, outline: "none" };

/* ─── PHONE (numbers + BYON forwarding wizard) ─── */

// Carrier forwarding codes (researched 2026-07 — see BUSINESS-PHONE-NUMBERS.md).
// <n> is replaced with the customer's SonusLabs number.
const CARRIERS: { id: string; label: string; region: "IN" | "US"; codes: [string, string][];
  cancel: string }[] = [
  { id: "jio", label: "Jio", region: "IN", cancel: "*402 (all) · *404 / *406 / *410 per type",
    codes: [["Forward ALL calls", "*401*<n>"], ["When you don't answer", "*403*<n>"],
      ["When you're busy", "*405*<n>"], ["When unreachable", "*409*<n>"]] },
  { id: "airtel", label: "Airtel", region: "IN", cancel: "##21# / ##61# / ##67# / ##62#",
    codes: [["Forward ALL calls", "**21*<n>#"], ["When you don't answer", "**61*<n>*11*20#"],
      ["When you're busy", "**67*<n>#"], ["When unreachable", "**62*<n>#"]] },
  { id: "vi", label: "Vi", region: "IN", cancel: "##21# / ##61# / ##67# / ##62#",
    codes: [["Forward ALL calls", "**21*<n>#"], ["When you don't answer", "**61*<n>#"],
      ["When you're busy", "**67*<n>#"], ["When unreachable", "**62*<n>#"]] },
  { id: "bsnl", label: "BSNL", region: "IN", cancel: "##21# / ##61# / ##67# / ##62#",
    codes: [["Forward ALL calls", "**21**<n>#"], ["When you don't answer", "**61**<n>#"],
      ["When you're busy", "**67**<n>#"], ["When unreachable", "**62**<n>#"]] },
  { id: "us-verizon", label: "Verizon / US landline", region: "US", cancel: "*73",
    codes: [["Forward ALL calls", "*72<n>"]] },
  { id: "us-att", label: "AT&T / GSM", region: "US", cancel: "#21#",
    codes: [["Forward ALL calls", "*21*<n>#"]] },
  { id: "us-tmobile", label: "T-Mobile", region: "US", cancel: "##21#",
    codes: [["Forward ALL calls", "**21*<n>#"]] },
];

function PhoneTab() {
  const { user, telephony } = useAuth();
  const [data, setData] = useState<{ numbers: import("../api").OwnedNumber[];
    available: import("../api").NumberStock[] } | null>(null);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [agentSel, setAgentSel] = useState("");
  const [countrySel, setCountrySel] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [carrier, setCarrier] = useState(CARRIERS[0].id);
  const [fwdTarget, setFwdTarget] = useState("");

  const reload = () => {
    api.numbers().then((d) => {
      setData(d);
      if (d.numbers.length && !fwdTarget) setFwdTarget(d.numbers[0].number);
      if (d.available.length && !countrySel) setCountrySel(d.available[0].country);
    }).catch(() => {});
    api.agentsLite().then((r) => {
      setAgents(r.agents);
      if (r.agents.length) setAgentSel((s) => s || r.agents[0].agent_id);
    }).catch(() => {});
  };
  useEffect(() => { reload(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!user) return <div style={{ color: C.darkMuted, fontSize: 14 }}>
    Phone numbers need an account — this server runs in legacy mode.</div>;

  // No telephony wired (the PAYG launch) → the whole tab is a coming-soon teaser.
  if (!telephony) {
    return (
      <div style={{ maxWidth: 620 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          <div style={{ fontFamily: SF, fontSize: 24 }}>Phone</div>
          <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: ".06em", textTransform: "uppercase",
            color: C.accent, border: `1px solid ${C.darkLine}`, borderRadius: 6, padding: "3px 8px" }}>Coming soon</span>
        </div>
        <div style={{ ...cardPad, lineHeight: 1.7, color: "#CFC7B6", fontSize: 14 }}>
          <p style={{ marginBottom: 12 }}>Give your agent a real phone number, or forward your existing business
            number to it — callers dial the number they know, your AI answers. <b>Launching soon.</b></p>
          <p style={{ color: C.darkMuted, fontSize: 13.5, marginBottom: 0 }}>
            Available today: talk to your agents in the browser (the orb), and build them into your
            own product over the <a href="/docs/phone-numbers" target="_blank" rel="noreferrer"
              style={{ color: C.accent, fontWeight: 700, textDecoration: "none" }}>API</a>.
            Want early access to phone numbers? Email <span style={{ color: "#CFC7B6" }}>hello@sonuslabs.online</span>.
          </p>
        </div>
      </div>
    );
  }

  if (!data) return <div style={{ color: C.darkMuted, fontSize: 14 }}>Loading…</div>;

  const stock = data.available.find((a) => a.country === countrySel);
  const claim = async () => {
    if (!agentSel || !countrySel || busy) return;
    setBusy(true); setMsg("");
    try {
      const r = await api.claimNumber(agentSel, countrySel);
      setMsg(`✓ ${r.number} is live — calls to it now reach your agent.`);
      setFwdTarget(r.number);
      reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : "Claim failed."); }
    setBusy(false);
  };

  const car = CARRIERS.find((c) => c.id === carrier)!;
  const agentName = (id: string) => agents.find((a) => a.agent_id === id)?.name || id;

  return (
    <div style={{ maxWidth: 780 }}>
      <div style={{ fontFamily: SF, fontSize: 24, marginBottom: 6 }}>Phone</div>
      <div style={{ fontSize: 13.5, color: C.darkMuted, marginBottom: 16, lineHeight: 1.6 }}>
        Give your agent a real phone number — or keep your existing business number and
        forward it. Callers dial the number they already know; your AI answers.
      </div>

      {/* your numbers */}
      <div style={{ ...cardPad, marginBottom: 14 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>Your numbers</div>
        {data.numbers.length === 0 ? (
          <div style={{ color: C.darkMuted, fontSize: 13.5 }}>
            No numbers yet — claim one below.</div>
        ) : data.numbers.map((n, i) => (
          <div key={n.number} style={{ display: "flex", alignItems: "center", gap: 12,
            padding: "10px 2px", borderTop: i ? `1px solid ${C.darkLine}` : "none" }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontFamily: mono, fontSize: 15, fontWeight: 700 }}>{n.number}</div>
              <div style={{ fontSize: 11.5, color: C.darkMuted }}>
                rings “{agentName(n.agent_id)}” · {rupees(n.monthly_paise)}/month · {n.country}
              </div>
            </div>
            <span onClick={() => setFwdTarget(n.number)}
              style={{ fontSize: 12.5, fontWeight: 700, color: C.accent, cursor: "pointer" }}>
              Set up forwarding</span>
            <span onClick={async () => {
                if (confirm(`Release ${n.number}? Callers will no longer reach your agent on it.`)) {
                  await api.releaseNumber(n.number); reload();
                } }}
              style={{ fontSize: 12.5, fontWeight: 700, color: C.red, cursor: "pointer" }}>
              Release</span>
          </div>
        ))}
      </div>

      {/* get a number */}
      <div style={{ ...cardPad, marginBottom: 14 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Get a number</div>
        <div style={{ fontSize: 12.5, color: C.darkMuted, marginBottom: 12 }}>
          A dedicated number for your agent. First month's rent is charged from your
          credits when you claim it.
        </div>
        {data.available.length === 0 ? (
          <div style={{ fontSize: 13.5, color: C.darkMuted, background: C.dark,
            border: `1px dashed ${C.darkLine}`, borderRadius: 10, padding: "12px 14px" }}>
            No numbers in stock right now — we're adding more. Check back soon, or use
            “forward your existing number” once stock is available.
          </div>
        ) : (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select value={agentSel} onChange={(e) => setAgentSel(e.target.value)}
              style={{ ...dfield, width: 200 }}>
              {agents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.name}</option>)}
            </select>
            <select value={countrySel} onChange={(e) => setCountrySel(e.target.value)}
              style={{ ...dfield, width: 130 }}>
              {data.available.map((a) => (
                <option key={a.country} value={a.country}>{a.country} ({a.n} left)</option>))}
            </select>
            <button onClick={claim} disabled={busy || !agents.length}
              style={{ border: "none", borderRadius: 10, padding: "11px 18px", fontSize: 14,
                fontWeight: 700, background: C.accent, color: C.ink, cursor: "pointer",
                opacity: busy ? 0.6 : 1 }}>
              {busy ? "…" : stock ? `Claim · ${rupees(stock.monthly_paise)}/mo` : "Claim"}
            </button>
          </div>
        )}
        {msg && <div style={{ fontSize: 13, fontWeight: 600, marginTop: 10,
          color: msg.startsWith("✓") ? "#7BC89B" : C.red }}>{msg}</div>}
      </div>

      {/* BYON wizard */}
      <div style={cardPad}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
          Keep your existing number</div>
        <div style={{ fontSize: 12.5, color: C.darkMuted, marginBottom: 14, lineHeight: 1.6 }}>
          Your business number stays exactly where it is — you just tell your carrier to
          forward calls to your SonusLabs number. Takes one dial code, works in minutes,
          switch it off anytime. No porting, no downtime.
        </div>
        {data.numbers.length === 0 ? (
          <div style={{ fontSize: 13.5, color: C.darkMuted }}>
            First claim a number above — that's where your calls get forwarded.
          </div>
        ) : (<>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
            <select value={fwdTarget} onChange={(e) => setFwdTarget(e.target.value)}
              style={{ ...dfield, width: 210 }}>
              {data.numbers.map((n) => <option key={n.number} value={n.number}>{n.number}</option>)}
            </select>
            <select value={carrier} onChange={(e) => setCarrier(e.target.value)}
              style={{ ...dfield, width: 210 }}>
              <optgroup label="India">
                {CARRIERS.filter((c) => c.region === "IN").map((c) => (
                  <option key={c.id} value={c.id}>{c.label}</option>))}
              </optgroup>
              <optgroup label="United States">
                {CARRIERS.filter((c) => c.region === "US").map((c) => (
                  <option key={c.id} value={c.id}>{c.label}</option>))}
              </optgroup>
            </select>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {car.codes.map(([label, code]) => (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 12,
                background: C.dark, border: `1px solid ${C.darkLine}`, borderRadius: 10,
                padding: "10px 14px" }}>
                <div style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>{label}
                  {label.includes("don't answer") && (
                    <span style={{ color: C.darkMuted, fontWeight: 400 }}> — most popular:
                      you answer when you can, AI catches the rest</span>)}
                </div>
                <code style={{ fontFamily: mono, fontSize: 14, fontWeight: 700,
                  color: C.accent }}>
                  {code.replace("<n>", fwdTarget.replace("+91", "").replace("+1", ""))}
                </code>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 12, color: C.darkMuted, marginTop: 12, lineHeight: 1.6 }}>
            Dial the code from your business phone, then call your business number from
            another phone — your AI should answer. Turn forwarding off anytime:{" "}
            <span style={{ fontFamily: mono }}>{car.cancel}</span>.
            {car.region === "IN" && (
              <> Note: forward Indian numbers to an <b>Indian</b> SonusLabs number —
              international forwarding is blocked or billed as ISD by most Indian carriers.</>)}
          </div>
        </>)}
      </div>
    </div>
  );
}

/* ─── BILLING (prepaid credits) ─── */
declare global { interface Window { Razorpay?: any } }

function loadRazorpay(): Promise<boolean> {
  return new Promise((res) => {
    if (window.Razorpay) { res(true); return; }
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => res(true);
    s.onerror = () => res(false);
    document.body.appendChild(s);
  });
}

const KIND_LABEL: Record<string, [string, string]> = {
  trial_grant: ["Free trial", "#7BC89B"],
  topup: ["Credits added", "#7BC89B"],
  call_usage: ["Call", C.darkMuted],
  number_rent: ["Number rent", C.accent],
  adjustment: ["Adjustment", C.darkMuted],
};

function BillingTab() {
  const { user } = useAuth();
  const [w, setW] = useState<WalletInfo | null>(null);
  const [amount, setAmount] = useState(500);   // rupees
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const reload = () => api.wallet().then(setW).catch(() => {});
  useEffect(() => { reload(); }, []);

  if (!user) return <div style={{ color: C.darkMuted, fontSize: 14 }}>
    Billing needs an account — this server runs in legacy mode.</div>;
  if (!w) return <div style={{ color: C.darkMuted, fontSize: 14 }}>Loading…</div>;

  const addCredits = async () => {
    const paise = Math.round(amount * 100);
    if (paise < 5000) { setMsg("Minimum top-up is ₹50."); return; }
    setBusy(true); setMsg("");
    try {
      if (w.payments_enabled) {
        const order = await api.topupOrder(paise);
        const ok = await loadRazorpay();
        if (!ok) { setMsg("Could not load the payment window."); setBusy(false); return; }
        new window.Razorpay({
          key: order.key_id, order_id: order.order_id, amount: order.amount_paise,
          currency: order.currency, name: "SonusLabs", description: "Call credits",
          prefill: { email: user.email, name: user.name },
          theme: { color: "#E08A1E" },
          handler: async (resp: Record<string, string>) => {
            try { await api.topupVerify(resp); setMsg("Credits added ✓"); reload(); }
            catch { setMsg("Payment verification failed — contact support."); }
          },
        }).open();
      } else if (w.dev_topup) {
        await api.topupDev(paise);
        setMsg("Dev credits added ✓");
        reload();
      } else {
        setMsg("Online payments are coming soon — contact us to add credits.");
      }
    } catch (e) { setMsg(e instanceof Error ? e.message : "Top-up failed."); }
    setBusy(false);
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ fontFamily: SF, fontSize: 24, marginBottom: 16 }}>Billing</div>

      {/* balance card */}
      <div style={{ ...cardPad, display: "flex", alignItems: "center", gap: 22,
        flexWrap: "wrap", marginBottom: 14 }}>
        <div>
          <div style={dlbl}>BALANCE</div>
          <div style={{ fontFamily: SF, fontSize: 34, lineHeight: 1 }}>{rupees(w.balance_paise)}</div>
        </div>
        <div>
          <div style={dlbl}>TALK TIME LEFT</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>~{mins(w.seconds_left)} min</div>
        </div>
        <div style={{ marginLeft: "auto", fontSize: 12.5, color: C.darkMuted, lineHeight: 1.6 }}>
          Rate: {rupees(w.rate_paise_per_min)}/min, billed per second.<br />
          New accounts get {w.trial_minutes} min free.
        </div>
      </div>

      {/* add credits */}
      <div style={{ ...cardPad, marginBottom: 14 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Add credits</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {[100, 500, 1000, 2000].map((r) => (
            <button key={r} onClick={() => setAmount(r)}
              style={{ border: `1px solid ${amount === r ? C.accent : C.darkLine}`,
                background: amount === r ? "rgba(224,138,30,.12)" : C.dark,
                color: amount === r ? C.accent : C.darkText, borderRadius: 10,
                padding: "9px 15px", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
              ₹{r}
            </button>
          ))}
          <input type="number" min={50} value={amount}
            onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))}
            style={{ ...dfield, width: 110 }} />
          <button onClick={addCredits} disabled={busy}
            style={{ border: "none", borderRadius: 10, padding: "11px 18px", fontSize: 14,
              fontWeight: 700, background: C.accent, color: C.ink, cursor: "pointer",
              opacity: busy ? 0.6 : 1 }}>
            {busy ? "…" : w.payments_enabled ? `Pay ₹${amount}` : w.dev_topup ? `Add ₹${amount} (dev)` : "Add credits"}
          </button>
        </div>
        <div style={{ fontSize: 12, color: C.darkMuted, marginTop: 9 }}>
          ₹{amount || 0} ≈ {Math.floor((amount * 100 || 0) / w.rate_paise_per_min)} minutes of calls.
          {!w.payments_enabled && !w.dev_topup && " Online payments launching soon."}
        </div>
        {msg && <div style={{ fontSize: 13, fontWeight: 600, marginTop: 10,
          color: msg.includes("✓") ? "#7BC89B" : C.red }}>{msg}</div>}
      </div>

      {/* ledger */}
      <div style={cardPad}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>History</div>
        {w.ledger.length === 0 ? (
          <div style={{ color: C.darkMuted, fontSize: 13.5 }}>No activity yet.</div>
        ) : w.ledger.map((l, i) => {
          const [label, color] = KIND_LABEL[l.kind] || [l.kind, C.darkMuted];
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 12,
              padding: "9px 2px", borderTop: i ? `1px solid ${C.darkLine}` : "none" }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <span style={{ fontSize: 13.5, fontWeight: 600, color }}>{label}</span>
                {l.seconds != null && (
                  <span style={{ fontSize: 12, color: C.darkMuted }}> · {Math.ceil(l.seconds)}s</span>
                )}
                <div style={{ fontSize: 11.5, color: C.darkMuted }}>
                  {new Date(l.t * 1000).toLocaleString()}</div>
              </div>
              <div style={{ fontSize: 13.5, fontWeight: 700, fontFamily: mono,
                color: l.delta_paise >= 0 ? "#7BC89B" : C.darkText }}>
                {l.delta_paise >= 0 ? "+" : ""}{rupees(l.delta_paise)}
              </div>
              <div style={{ fontSize: 11.5, color: C.darkMuted, fontFamily: mono, width: 84,
                textAlign: "right" }}>{rupees(l.balance_after)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── DEVELOPERS (API keys) ─── */
function DevelopersTab() {
  const { user } = useAuth();
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const reload = () => api.apiKeys().then((r) => setKeys(r.keys)).catch(() => {});
  useEffect(() => { reload(); }, []);

  if (!user) return <div style={{ color: C.darkMuted, fontSize: 14 }}>
    API keys need an account — this server runs in legacy mode.</div>;

  const origin = window.location.origin.includes("localhost")
    ? "http://localhost:8001" : window.location.origin;

  const createKey = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const made = await api.createApiKey(name.trim() || "API key");
      setFresh({ name: made.name, key: made.key });
      setName(""); setCopied(false);
      reload();
    } catch { /* limit reached etc. */ }
    setBusy(false);
  };

  const curlAgents = [
    `curl ${origin}/agents \\`,
    `  -H "Authorization: Bearer sk_sonus_..." \\`,
    `  -H "X-Workspace-Id: <your-workspace-id>"`,
  ].join("\n");
  const curlCreate = [
    `curl -X POST ${origin}/agents \\`,
    `  -H "Authorization: Bearer sk_sonus_..." \\`,
    `  -H "X-Workspace-Id: <your-workspace-id>" \\`,
    `  -H "content-type: application/json" \\`,
    `  -d '{"name":"Reception","language":"en-IN","voice":"neha",`,
    `       "greeting_text":"Hello! How can I help?"}'`,
  ].join("\n");
  const wsCall = [
    `# PCM16 mono 16kHz binary frames in, agent audio + JSON captions out`,
    `${origin.replace("http", "ws")}/web-call?agent_id=<agent_id>&api_key=sk_sonus_...`,
  ].join("\n");
  const curlCalls = [
    `curl "${origin}/calls?limit=20" \\`,
    `  -H "Authorization: Bearer sk_sonus_..." \\`,
    `  -H "X-Workspace-Id: <your-workspace-id>"`,
  ].join("\n");

  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ fontFamily: SF, fontSize: 24, marginBottom: 6 }}>API</div>
      <div style={{ fontSize: 13.5, color: C.darkMuted, marginBottom: 16, lineHeight: 1.6 }}>
        Build SonusLabs into your own product — create agents, read calls and stream
        voice programmatically. Usage is billed from your credits at the same per-minute rate.{" "}
        <a href="/docs" target="_blank" rel="noreferrer"
          style={{ color: C.accent, fontWeight: 700, textDecoration: "none" }}>
          Read the full documentation →</a>
      </div>

      {/* fresh key reveal — the ONLY time the raw key exists */}
      {fresh && (
        <div style={{ ...cardPad, border: `1px solid ${C.accent}`, marginBottom: 14 }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>
            "{fresh.name}" created — copy it now
          </div>
          <div style={{ fontSize: 12.5, color: C.red, fontWeight: 600, marginBottom: 10 }}>
            This key is shown only once. We store a hash, not the key.
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code style={{ flex: 1, fontFamily: mono, fontSize: 12.5, background: C.dark,
              border: `1px solid ${C.darkLine}`, borderRadius: 9, padding: "10px 12px",
              overflowX: "auto", whiteSpace: "nowrap" }}>{fresh.key}</code>
            <button onClick={() => { navigator.clipboard.writeText(fresh.key); setCopied(true); }}
              style={{ border: "none", borderRadius: 9, padding: "10px 14px", fontWeight: 700,
                fontSize: 13, background: copied ? "#2E4638" : C.accent,
                color: copied ? "#7BC89B" : C.ink, cursor: "pointer", whiteSpace: "nowrap" }}>
              {copied ? "Copied ✓" : "Copy"}
            </button>
            <button onClick={() => setFresh(null)}
              style={{ border: `1px solid ${C.darkLine}`, background: "transparent",
                color: C.darkMuted, borderRadius: 9, padding: "10px 12px", fontSize: 13,
                cursor: "pointer" }}>Done</button>
          </div>
        </div>
      )}

      {/* create + list */}
      <div style={{ ...cardPad, marginBottom: 14 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>API keys</div>
        <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
          <input value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createKey(); }}
            placeholder="Key name (e.g. production server)"
            style={{ ...dfield, flex: 1 }} />
          <button onClick={createKey} disabled={busy}
            style={{ border: "none", borderRadius: 10, padding: "10px 16px", fontSize: 14,
              fontWeight: 700, background: C.accent, color: C.ink, cursor: "pointer",
              whiteSpace: "nowrap", opacity: busy ? 0.6 : 1 }}>+ Create key</button>
        </div>
        {keys.length === 0 ? (
          <div style={{ color: C.darkMuted, fontSize: 13.5, paddingTop: 8 }}>No keys yet.</div>
        ) : keys.map((k, i) => (
          <div key={k.id} style={{ display: "flex", alignItems: "center", gap: 12,
            padding: "10px 2px", borderTop: `1px solid ${C.darkLine}`,
            marginTop: i === 0 ? 8 : 0 }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>{k.name}</div>
              <div style={{ fontSize: 11.5, color: C.darkMuted, fontFamily: mono }}>
                {k.key_prefix} · created {new Date(k.created_at * 1000).toLocaleDateString()}
                {k.last_used_at ? ` · last used ${new Date(k.last_used_at * 1000).toLocaleDateString()}` : " · never used"}
              </div>
            </div>
            <span onClick={async () => { if (confirm(`Revoke "${k.name}"? Anything using it stops working.`)) { await api.revokeApiKey(k.id); reload(); } }}
              style={{ fontSize: 12.5, fontWeight: 700, color: C.red, cursor: "pointer" }}>
              Revoke</span>
          </div>
        ))}
      </div>

      {/* quickstart */}
      <div style={cardPad}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>Quickstart</div>
        {([["List your agents", curlAgents], ["Create an agent", curlCreate],
           ["Stream a live voice call (WebSocket)", wsCall],
           ["Read call history", curlCalls]] as [string, string][]).map(([title, code]) => (
          <div key={title} style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6, color: C.darkText }}>{title}</div>
            <pre style={{ fontFamily: mono, fontSize: 11.5, lineHeight: 1.55, background: C.dark,
              border: `1px solid ${C.darkLine}`, borderRadius: 9, padding: "10px 12px",
              overflowX: "auto", color: "#B9AF99", margin: 0 }}>{code}</pre>
          </div>
        ))}
        <div style={{ fontSize: 12, color: C.darkMuted }}>
          Find your workspace id via GET {origin}/auth/me (workspaces[].id) using your key.
        </div>
      </div>
    </div>
  );
}
