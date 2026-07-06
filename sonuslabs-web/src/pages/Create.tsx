import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Nav } from "../components/Nav";
import { Orb } from "../components/Orb";
import { CallPanel } from "../components/CallPanel";
import { api, AgentConfig } from "../api";
import { C, serif, LANGS } from "../theme";
import { useIsMobile } from "../useIsMobile";

const RESEARCH_LINES = [
  "Reading your website…", "Learning what you do…", "Asking around about you…",
  "Writing her persona…", "Building her knowledge…",
];

export function Create() {
  const nav = useNavigate();
  const mob = useIsMobile();
  const loc = useLocation() as { state?: { url?: string } };
  const [step, setStep] = useState(1);
  const [url, setUrl] = useState(loc.state?.url || "");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [nowLine, setNowLine] = useState(0);
  const [draft, setDraft] = useState<AgentConfig | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [advOpen, setAdvOpen] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => { api.voiceLab().then((r) => setVoices(r.voices)).catch(() => {}); }, []);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setNowLine((i) => Math.min(i + 1, RESEARCH_LINES.length - 1)), 3500);
    return () => clearInterval(t);
  }, [busy]);

  const runResearch = async () => {
    if (!url.trim()) { setErr("Enter your website URL"); return; }
    setErr(""); setBusy(true); setNowLine(0);
    try {
      const d = await api.research(url.trim(), desc.trim());
      setDraft(d); setStep(2);
    } catch (e: any) {
      setErr(e.message === "422" || /research/.test(e.message)
        ? "Couldn't read that site. Check the URL and try again."
        : "Research failed — please try again.");
    } finally { setBusy(false); }
  };

  const upd = (patch: Partial<AgentConfig>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const playVoice = (v: string) => {
    if (!audioRef.current) audioRef.current = new Audio();
    audioRef.current.src = api.voiceSampleUrl(v); audioRef.current.play().catch(() => {});
  };

  const createAgent = async () => {
    if (!draft) return;
    setStep(3);
    try {
      let body = { ...draft };
      try { await api.createAgent(body); }
      catch (e: any) {
        if (e.message === "409") { body = { ...body, agent_id: body.agent_id + "-" + Date.now().toString(36).slice(-4) }; await api.createAgent(body); }
        else throw e;
      }
      setDraft(body); setStep(4);
    } catch { setErr("Could not create the agent."); setStep(2); }
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: C.paper,
      backgroundImage: "radial-gradient(#E7DFCF 1.1px,transparent 1.1px)", backgroundSize: "24px 24px" }}>
      <Nav />
      <div style={{ maxWidth: 820, margin: "0 auto", padding: mob ? "26px 16px 50px" : "40px 28px 70px" }}>
        {/* progress dots */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, marginBottom: 34 }}>
          {[1, 2, 3, 4].map((n, i) => (
            <div key={n} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ width: 26, height: 26, borderRadius: "50%",
                background: step >= n ? C.accent : "#fff", color: step >= n ? "#fff" : C.faint,
                border: `1.5px solid ${step >= n ? C.accent : C.lineSoft}`, display: "flex",
                alignItems: "center", justifyContent: "center", fontSize: 12.5, fontWeight: 700 }}>{n}</div>
              {i < 3 && <div style={{ width: 34, height: 1.5, background: C.lineSoft }} />}
            </div>
          ))}
        </div>

        {/* STEP 1 */}
        {step === 1 && (busy ? (
          <div style={{ textAlign: "center", padding: "20px 0" }}>
            <div style={{ display: "flex", justifyContent: "center", marginBottom: 22 }}>
              <Orb size={170} active level={0.5} /></div>
            <h2 style={{ fontFamily: serif, fontWeight: 400, fontSize: 32, marginBottom: 20 }}>
              Getting to know your business…</h2>
            <div style={{ maxWidth: 440, margin: "0 auto", display: "flex", flexDirection: "column", gap: 10 }}>
              {RESEARCH_LINES.slice(0, nowLine).map((r) => (
                <div key={r} style={{ display: "flex", alignItems: "center", gap: 11, background: C.paperCard,
                  border: `1px solid ${C.line}`, borderRadius: 12, padding: "11px 15px", fontSize: 14,
                  animation: "sl-fadeup .4s", textAlign: "left" }}>
                  <span style={{ color: C.green, fontWeight: 700 }}>✓</span>
                  <span style={{ color: "#4A3F2A" }}>{r}</span></div>
              ))}
              <div style={{ display: "flex", alignItems: "center", gap: 11, padding: "11px 15px", fontSize: 14, color: C.faint }}>
                <span style={{ width: 13, height: 13, border: `2px solid ${C.lineSoft}`, borderTopColor: C.accent,
                  borderRadius: "50%", animation: "sl-spin .7s linear infinite" }} />{RESEARCH_LINES[nowLine]}</div>
            </div>
          </div>
        ) : (
          <div style={{ animation: "sl-fadeup .4s" }}>
            <div style={{ textAlign: "center", marginBottom: 26 }}>
              <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 40, marginBottom: 8 }}>
                Create your receptionist</h1>
              <p style={{ fontSize: 16, color: C.muted }}>Paste your website — we'll do the rest.</p>
            </div>
            <div style={{ background: C.paperCard, border: `1px solid ${C.line}`, borderRadius: 20, padding: 26 }}>
              <label style={lbl}>YOUR WEBSITE</label>
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://yourbusiness.in"
                style={{ ...field, marginBottom: 20 }} />
              <label style={lbl}>HOW SHOULD SHE BEHAVE? <span style={{ fontWeight: 500, color: "#A79E8B" }}>(optional)</span></label>
              <textarea value={desc} onChange={(e) => setDesc(e.target.value)}
                placeholder="Warm and polite. Always offer to book an appointment. Quote today's price when asked."
                style={{ ...field, minHeight: 96, resize: "vertical", lineHeight: 1.5 }} />
              {err && <div style={{ color: "#C0492E", fontSize: 13.5, marginTop: 12 }}>{err}</div>}
              <button onClick={runResearch} style={{ ...primaryBtn, width: "100%", marginTop: 20 }}>
                Research my business →</button>
              <div style={{ textAlign: "center", fontSize: 13, color: "#A79E8B", marginTop: 12 }}>
                Takes 15–60 seconds while she reads your site.</div>
            </div>
          </div>
        ))}

        {/* STEP 2 */}
        {step === 2 && draft && (
          <div style={{ animation: "sl-fadeup .4s" }}>
            <div style={{ textAlign: "center", marginBottom: 24 }}>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 7, background: "#DFF3EC",
                border: "1px solid #B9E3D4", color: "#1E7A63", borderRadius: 100, padding: "5px 13px",
                fontSize: 12.5, fontWeight: 600, marginBottom: 12 }}>✓ Draft ready{draft.industry ? ` · ${draft.industry}` : ""}</div>
              <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 36, marginBottom: 6 }}>
                Meet the draft. Make her yours.</h1>
              <p style={{ fontSize: 15.5, color: C.muted }}>Everything below is editable. Change what you like, then create her.</p>
            </div>
            <div style={{ background: C.paperCard, border: `1px solid ${C.line}`, borderRadius: 20, padding: 26,
              display: "flex", flexDirection: "column", gap: 20 }}>
              <div style={{ display: "grid", gridTemplateColumns: mob ? "1fr" : "1fr 1fr", gap: 16 }}>
                <div><label style={lbl}>NAME</label>
                  <input value={draft.name} onChange={(e) => upd({ name: e.target.value })} style={field} /></div>
                <div><label style={lbl}>LANGUAGE</label>
                  <select value={draft.language} onChange={(e) => upd({ language: e.target.value })} style={field}>
                    {LANGS.map((o) => <option key={o.v} value={o.v}>{o.en}</option>)}</select></div>
              </div>
              <div><label style={lbl}>GREETING</label>
                <textarea value={draft.greeting_text} onChange={(e) => upd({ greeting_text: e.target.value })}
                  style={{ ...field, minHeight: 64, resize: "vertical", lineHeight: 1.5 }} /></div>
              <div><label style={lbl}>VOICE</label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {voices.map((v) => {
                    const on = draft.voice === v;
                    return (
                      <div key={v} onClick={() => upd({ voice: v })} style={{ display: "flex", alignItems: "center",
                        gap: 8, background: on ? C.accentSoft : C.paper, border: `1.5px solid ${on ? C.accent : C.lineSoft}`,
                        borderRadius: 11, padding: "8px 12px", cursor: "pointer", fontSize: 13.5, fontWeight: 600,
                        color: on ? C.accentDeep : C.muted }}>
                        <span onClick={(e) => { e.stopPropagation(); playVoice(v); }} style={{ width: 22, height: 22,
                          borderRadius: "50%", background: C.ink, display: "flex", alignItems: "center",
                          justifyContent: "center" }}>
                          <div style={{ width: 0, height: 0, borderLeft: "6px solid #fff", borderTop: "4px solid transparent",
                            borderBottom: "4px solid transparent", marginLeft: 1 }} /></span>
                        {v}</div>
                    );
                  })}
                </div></div>
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                  <label style={{ ...lbl, marginBottom: 0 }}>WHAT SHE KNOWS · {draft.knowledge_docs.length} facts</label>
                  <button onClick={() => upd({ knowledge_docs: [...draft.knowledge_docs, ""] })}
                    style={{ fontSize: 13, fontWeight: 600, color: C.accentDeep, background: C.accentSoft, border: "none",
                      borderRadius: 8, padding: "6px 11px", cursor: "pointer" }}>+ Add fact</button>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                  {draft.knowledge_docs.map((d, i) => (
                    <div key={i} style={{ display: "flex", gap: 9, alignItems: "flex-start", background: C.paper,
                      border: `1px solid ${C.lineSoft}`, borderRadius: 11, padding: "10px 12px" }}>
                      <textarea value={d} onChange={(e) => {
                        const docs = [...draft.knowledge_docs]; docs[i] = e.target.value; upd({ knowledge_docs: docs });
                      }} style={{ flex: 1, minHeight: 44, background: "transparent", border: "none", outline: "none",
                        fontSize: 14, resize: "vertical", lineHeight: 1.45, color: "#3A3327" }} />
                      <span onClick={() => upd({ knowledge_docs: draft.knowledge_docs.filter((_, j) => j !== i) })}
                        style={{ cursor: "pointer", color: "#B9B0A0", fontSize: 18, lineHeight: 1, padding: "2px 4px" }}>×</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div onClick={() => setAdvOpen((o) => !o)} style={{ display: "flex", alignItems: "center", gap: 8,
                  cursor: "pointer", fontSize: 13.5, fontWeight: 600, color: C.muted }}>
                  <span style={{ transform: `rotate(${advOpen ? 90 : 0}deg)`, transition: "transform .2s" }}>▸</span>
                  Advanced · system prompt</div>
                {advOpen && <textarea value={draft.system_prompt} onChange={(e) => upd({ system_prompt: e.target.value })}
                  style={{ width: "100%", marginTop: 10, minHeight: 140, background: C.ink, color: "#D8CFBE",
                    border: `1px solid ${C.lineSoft}`, borderRadius: 10, padding: 13,
                    fontFamily: "'JetBrains Mono',monospace", fontSize: 12.5, outline: "none", resize: "vertical", lineHeight: 1.6 }} />}
              </div>
            </div>
            {err && <div style={{ color: "#C0492E", fontSize: 13.5, marginTop: 12 }}>{err}</div>}
            <div style={{ display: "flex", gap: 12, marginTop: 20 }}>
              <button onClick={() => setStep(1)} style={{ fontSize: 15, fontWeight: 600, color: C.muted,
                background: "#fff", border: `1px solid ${C.lineSoft}`, borderRadius: 12, padding: "14px 22px", cursor: "pointer" }}>← Back</button>
              <button onClick={createAgent} style={{ ...primaryBtn, flex: 1 }}>Create {draft.name} →</button>
            </div>
          </div>
        )}

        {/* STEP 3 */}
        {step === 3 && (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <div style={{ width: 30, height: 30, margin: "0 auto 18px", border: `3px solid ${C.lineSoft}`,
              borderTopColor: C.accent, borderRadius: "50%", animation: "sl-spin .7s linear infinite" }} />
            <div style={{ fontSize: 16, color: C.muted }}>Creating {draft?.name}…</div>
          </div>
        )}

        {/* STEP 4 */}
        {step === 4 && draft && (
          <div style={{ textAlign: "center", animation: "sl-fadeup .4s" }}>
            <div style={{ display: "inline-flex", alignItems: "center", gap: 7, background: "#DFF3EC",
              border: "1px solid #B9E3D4", color: "#1E7A63", borderRadius: 100, padding: "5px 13px",
              fontSize: 12.5, fontWeight: 600, marginBottom: 14 }}>🎉 {draft.name} is live</div>
            <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 38, marginBottom: 6 }}>Say hello to {draft.name}.</h1>
            <p style={{ fontSize: 15.5, color: C.muted, marginBottom: 18 }}>Tap the orb and talk — she answers as your business would.</p>
            <div style={{ maxWidth: 420, margin: "0 auto" }}>
              <CallPanel agentId={draft.agent_id} subtitle={draft.name} orbSize={200} /></div>
            <div style={{ display: "flex", gap: 12, justifyContent: "center", marginTop: 24 }}>
              <button onClick={() => nav("/console")} style={primaryBtn}>Open in the console →</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const lbl: React.CSSProperties = { display: "block", fontSize: 12.5, fontWeight: 700, color: C.muted, marginBottom: 6 };
const field: React.CSSProperties = { width: "100%", background: C.paper, border: `1px solid ${C.lineSoft}`,
  borderRadius: 10, padding: "12px 14px", fontSize: 15, outline: "none" };
const primaryBtn: React.CSSProperties = { fontSize: 15.5, fontWeight: 600, color: "#fff", background: C.accent,
  border: "none", borderRadius: 12, padding: 14, cursor: "pointer" };
