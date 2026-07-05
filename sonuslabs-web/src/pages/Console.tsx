import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CallPanel } from "../components/CallPanel";
import { api, AgentConfig, AgentLite, CallRecord, LiveLine } from "../api";
import { C, serif, mono, serif as SF, langLabel, LANGS } from "../theme";

type Tab = "agents" | "calls" | "live" | "voice" | "analytics";

export function Console() {
  const nav = useNavigate();
  const [tab, setTab] = useState<Tab>("agents");
  const [liveCount, setLiveCount] = useState(0);

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
        {([["agents", "Agents"], ["calls", "Calls"], ["live", "Live"], ["voice", "Voice Lab"],
          ["analytics", "Analytics"]] as [Tab, string][]).map(([k, label]) => {
          const on = tab === k;
          return (
            <div key={k} onClick={() => setTab(k)} style={{ display: "flex", alignItems: "center", gap: 11,
              padding: "10px 12px", borderRadius: 10, cursor: "pointer", fontSize: 14.5, fontWeight: 600,
              background: on ? C.darkCard : "transparent", color: on ? C.darkText : C.darkMuted }}>
              <span style={{ width: 7, height: 7, borderRadius: 2, background: on ? C.accent : "#463F32" }} />
              {label}
              {k === "live" && liveCount > 0 && (
                <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 5, fontSize: 10.5,
                  fontWeight: 700, color: C.red }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.red, animation: "sl-livedot 1.4s infinite" }} />
                  {liveCount}</span>
              )}
            </div>
          );
        })}
        <div style={{ marginTop: "auto", paddingTop: 12, borderTop: `1px solid ${C.darkLine}` }}>
          <div onClick={() => nav("/create")} style={{ display: "flex", alignItems: "center", gap: 9,
            padding: "11px 12px", borderRadius: 10, cursor: "pointer", fontSize: 14, fontWeight: 600,
            background: C.accent, color: C.ink }}>+ New agent</div>
        </div>
      </div>

      <div style={{ padding: "26px 30px", overflowX: "hidden" }}>
        {tab === "agents" && <AgentsTab />}
        {tab === "calls" && <CallsTab />}
        {tab === "live" && <LiveTab onCount={setLiveCount} />}
        {tab === "voice" && <VoiceTab />}
        {tab === "analytics" && <AnalyticsTab />}
      </div>
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
      <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 22, alignItems: "start" }}>
        <div style={{ ...cardPad, display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <div><div style={dlbl}>NAME</div>
              <input value={a.name} onChange={(e) => upd({ name: e.target.value })} style={dfield} /></div>
            <div><div style={dlbl}>LANGUAGE</div>
              <select value={a.language} onChange={(e) => upd({ language: e.target.value })} style={dfield}>
                {LANGS.map((o) => <option key={o.v} value={o.v}>{o.en}</option>)}</select></div>
          </div>
          <div><div style={dlbl}>GREETING</div>
            <textarea value={a.greeting_text} onChange={(e) => upd({ greeting_text: e.target.value })}
              style={{ ...dfield, minHeight: 56, resize: "vertical", lineHeight: 1.5 }} /></div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
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
            <div style={{ display: "flex", gap: 8 }}>
              <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98…"
                style={{ ...dfield, flex: 1 }} />
              <button onClick={callMe} style={{ fontWeight: 600, color: C.ink, background: C.accent, border: "none",
                borderRadius: 9, padding: "10px 14px", cursor: "pointer", whiteSpace: "nowrap" }}>Call me</button>
            </div>
            {callMsg && <div style={{ fontSize: 12.5, color: "#B7AD98", marginTop: 8 }}>{callMsg}</div>}
          </div>
        </div>
      </div>
    </>
  );
}

/* ─── CALLS ─── */
function CallsTab() {
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

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
        <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 30 }}>Calls</h1>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search transcripts…"
          style={{ width: 260, background: C.darkCard, border: `1px solid ${C.darkLine}`, borderRadius: 10,
            padding: "10px 13px", color: C.darkText, fontSize: 14, outline: "none" }} />
      </div>
      <div style={{ ...cardPad, padding: 0, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "150px 1fr 70px 60px 100px 100px", gap: 12,
          padding: "12px 18px", fontSize: 11.5, fontWeight: 700, color: C.darkMuted, borderBottom: `1px solid ${C.darkLine}` }}>
          <span>TIME</span><span>AGENT</span><span>DUR</span><span>TURNS</span><span>PERCEIVED</span><span>OUTCOME</span>
        </div>
        {calls.length === 0 && <div style={{ padding: 40, textAlign: "center", color: C.darkMuted, fontSize: 14 }}>No calls yet.</div>}
        {calls.map((c) => {
          const lat = c.avg_perceived_ms || 0;
          return (
            <div key={c.call_id}>
              <div onClick={() => setOpen(open === c.call_id ? null : c.call_id)} style={{ display: "grid",
                gridTemplateColumns: "150px 1fr 70px 60px 100px 100px", gap: 12, padding: "14px 18px", fontSize: 13.5,
                borderBottom: "1px solid #241F18", cursor: "pointer", alignItems: "center" }}>
                <span style={{ color: "#B7AD98" }}>{c.started_at ? new Date(c.started_at * 1000).toLocaleTimeString() : ""}</span>
                <span style={{ fontWeight: 600 }}>{names[c.agent_id] || c.agent_id}</span>
                <span style={{ color: "#B7AD98" }}>{fmt(c.duration_s)}</span>
                <span style={{ color: "#B7AD98" }}>{c.turn_count ?? ""}</span>
                <span style={{ color: lat > 1500 ? C.red : lat > 900 ? C.accent : C.green, fontFamily: mono, fontSize: 12.5 }}>
                  {lat ? `${lat}ms` : ""}</span>
                <span><span style={{ background: "#2A251C", color: "#B7AD98", fontSize: 11, fontWeight: 700,
                  padding: "3px 9px", borderRadius: 100 }}>{c.outcome || "—"}</span></span>
              </div>
              {open === c.call_id && (
                <div style={{ padding: "14px 18px 18px", background: "#1A1610", borderBottom: "1px solid #241F18",
                  display: "flex", flexDirection: "column", gap: 8 }}>
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
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

/* ─── LIVE ─── */
function LiveTab({ onCount }: { onCount: (n: number) => void }) {
  const [feed, setFeed] = useState<LiveLine[]>([]);
  const [active, setActive] = useState(0);
  const since = useRef(Date.now() / 1000 - 60);
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await api.liveTranscript(since.current);
        if (!alive) return;
        if (r.lines.length) {
          since.current = Math.max(since.current, ...r.lines.map((l) => l.t));
          setFeed((f) => [...f, ...r.lines].slice(-60));
        }
        setActive(r.active); onCount(r.active);
      } catch { /* ignore */ }
    };
    const t = setInterval(poll, 700); poll();
    return () => { alive = false; clearInterval(t); };
  }, [onCount]);

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 30 }}>Live</h1>
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, color: C.red,
          background: "#2A1D18", border: "1px solid #4A2E29", borderRadius: 100, padding: "4px 11px" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: C.red, animation: "sl-livedot 1.4s infinite" }} />
          {active} active</span>
      </div>
      <div style={{ ...cardPad, display: "flex", flexDirection: "column", gap: 10, minHeight: 300 }}>
        {feed.length === 0 && <div style={{ color: C.darkMuted, fontSize: 14 }}>No live calls right now. Start one from Agents → talk, or a phone call.</div>}
        {feed.map((l, i) => (
          <div key={i} style={{ display: "flex", gap: 12, alignItems: "baseline", animation: "sl-fadeup .3s" }}>
            <span style={{ fontFamily: mono, fontSize: 11, color: "#6B6250", whiteSpace: "nowrap" }}>
              {new Date(l.t * 1000).toLocaleTimeString()}</span>
            <span style={{ fontSize: 11, fontWeight: 700, color: C.darkMuted, background: C.dark, borderRadius: 6,
              padding: "2px 7px", whiteSpace: "nowrap" }}>{l.call_id.slice(-6)}</span>
            <span style={{ fontSize: 11, fontWeight: 700, color: l.role === "user" ? C.accent : C.green,
              whiteSpace: "nowrap", textTransform: "uppercase" }}>{l.role}</span>
            <span style={{ fontSize: 13.5, color: "#D8CFBE", lineHeight: 1.4 }}>{l.text}</span>
          </div>
        ))}
      </div>
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
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 16, marginBottom: 22 }}>
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
