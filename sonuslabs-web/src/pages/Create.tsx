import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Nav } from "../components/Nav";
import { Orb } from "../components/Orb";
import { CallPanel } from "../components/CallPanel";
import { api, AgentConfig } from "../api";
import { C, serif, LANGS } from "../theme";
import { useIsMobile } from "../useIsMobile";
import { CLONE_SECONDS, MIN_CLONE_SECONDS, READ_SCRIPT,
         startCloneRecording } from "../voiceCloneKit";
import { fileToB64 } from "../api";

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
  const kbFileRef = useRef<HTMLInputElement | null>(null);
  const [kbMsg, setKbMsg] = useState("");
  const [enhancing, setEnhancing] = useState(false);
  // Step-4 clone-my-voice (the agent exists by then)
  const [cloneOpen, setCloneOpen] = useState(false);
  const [cloneState, setCloneState] =
    useState<"idle" | "rec" | "uploading" | "done" | "error">("idle");
  const [cloneSecs, setCloneSecs] = useState(0);
  const [cloneMsg, setCloneMsg] = useState("");
  const cloneStop = useRef<(() => void) | null>(null);
  useEffect(() => () => { cloneStop.current?.(); }, []);
  // Step-2 clone: the agent doesn't exist yet, so we HOLD the recording and
  // apply it right after createAgent (single clone, no wasted slot).
  const [preClone, setPreClone] = useState<{ b64: string } | null>(null);
  const [rec2, setRec2] = useState<"idle" | "rec" | "done" | "error">("idle");
  const [rec2Secs, setRec2Secs] = useState(0);
  const [rec2Msg, setRec2Msg] = useState("");
  const rec2Stop = useRef<(() => void) | null>(null);
  useEffect(() => () => { rec2Stop.current?.(); }, []);

  const recordStep2 = async () => {
    if (rec2 === "rec") { rec2Stop.current?.(); return; }
    setRec2Msg("");
    try {
      rec2Stop.current = await startCloneRecording({
        onTick: setRec2Secs,
        onDone: ({ b64, seconds }) => {
          rec2Stop.current = null;
          if (seconds < MIN_CLONE_SECONDS) {
            setRec2("error"); setRec2Msg(`Too short — record at least ${MIN_CLONE_SECONDS}s.`); return;
          }
          setPreClone({ b64 });
          setRec2("done");
          setRec2Msg("✓ Your voice is ready — she'll use it once you create her.");
        },
      });
      setRec2("rec");
    } catch { setRec2("error"); setRec2Msg("Microphone permission denied."); }
  };

  const uploadKb = async (f: File) => {
    setKbMsg("Reading " + f.name + "…");
    try {
      const r = await api.parseDoc(f.name, await fileToB64(f));
      setDraft((d) => d ? { ...d, knowledge_docs: [...d.knowledge_docs, ...r.docs].slice(0, 60) } : d);
      setKbMsg(`Added ${r.docs.length} facts from ${f.name}.`);
    } catch (e: any) {
      setKbMsg(e?.message?.length > 3 ? e.message : "Couldn't read that file (.txt, .md, .csv, .pdf).");
    }
  };

  const enhance = async () => {
    if (!draft) return;
    const behaviour = desc.trim()
      || prompt("Describe how she should behave (e.g. 'debt recovery agent'):") || "";
    if (!behaviour.trim()) return;
    setEnhancing(true);
    try {
      const r = await api.enhancePrompt(behaviour, draft.name);
      upd({ system_prompt: r.system_prompt });
      setAdvOpen(true);
    } catch { setKbMsg("Enhancement failed — try again."); }
    finally { setEnhancing(false); }
  };

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
      let made: AgentConfig;
      try { made = await api.createAgent(body); }
      catch (e: any) {
        // Legacy-mode 409 fallback (accounts mode never 409s — the server
        // suffixes collisions itself).
        if (e.message === "409") { body = { ...body, agent_id: body.agent_id + "-" + Date.now().toString(36).slice(-4) }; made = await api.createAgent(body); }
        else throw e;
      }
      // Apply a step-2 voice recording now that the agent exists (held until
      // here so we never provision a clone the user might abandon).
      if (preClone) {
        try {
          const r = await api.cloneAgentVoice(made.agent_id, preClone.b64);
          made = { ...made, voice: r.voice };
        } catch { /* keep the stock voice; they can re-clone in step 4 */ }
      }
      // The SERVER owns the final agent_id (it may suffix for uniqueness) —
      // step 4's talk orb must target what was actually created.
      setDraft(made); setStep(4);
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
              <label style={lbl}>HOW SHOULD SHE BEHAVE? <span style={{ fontWeight: 500, color: "#A79E8B" }}>(optional — any role, it wins over the website)</span></label>
              <textarea value={desc} onChange={(e) => setDesc(e.target.value)}
                placeholder={"Anything: 'a firm but respectful debt recovery agent' · 'take food orders and upsell combos' · 'book salon appointments, quote prices'"}
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
                  {preClone && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, background: C.accentSoft,
                      border: `1.5px solid ${C.accent}`, borderRadius: 11, padding: "8px 12px",
                      fontSize: 13.5, fontWeight: 600, color: C.accentDeep }}>🎙 your voice</div>
                  )}
                </div>
                {/* Clone your own voice — held now, applied when she's created */}
                <div style={{ marginTop: 10 }}>
                  {rec2 !== "rec" ? (
                    <button onClick={recordStep2}
                      style={{ background: "transparent", border: `1px dashed ${C.lineSoft}`, borderRadius: 10,
                        padding: "7px 14px", fontSize: 13, fontWeight: 600, color: C.ink, cursor: "pointer" }}>
                      🎙 {preClone ? "Re-record my voice" : "Use my own voice (clone)"}</button>
                  ) : (
                    <button onClick={recordStep2}
                      style={{ background: C.red, border: "none", borderRadius: 10, padding: "8px 15px",
                        fontSize: 13, fontWeight: 700, color: "#fff", cursor: "pointer" }}>
                      ● Recording… {rec2Secs}s (tap to finish)</button>
                  )}
                  {rec2 === "rec" && (
                    <div style={{ fontSize: 12.5, color: C.muted, marginTop: 7, lineHeight: 1.5, fontStyle: "italic" }}>
                      Read aloud: {READ_SCRIPT}</div>
                  )}
                  {rec2Msg && <div style={{ fontSize: 12.5, marginTop: 6, fontWeight: 600,
                    color: rec2 === "error" ? "#C0492E" : C.green }}>{rec2Msg}</div>}
                </div></div>
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                  <label style={{ ...lbl, marginBottom: 0 }}>WHAT SHE KNOWS · {draft.knowledge_docs.length} facts</label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input ref={kbFileRef} type="file" accept=".txt,.md,.markdown,.csv,.pdf" style={{ display: "none" }}
                      onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadKb(f); e.target.value = ""; }} />
                    <button onClick={() => kbFileRef.current?.click()}
                      style={{ fontSize: 13, fontWeight: 600, color: C.accentDeep, background: C.accentSoft, border: "none",
                        borderRadius: 8, padding: "6px 11px", cursor: "pointer" }}>⇪ Upload file</button>
                    <button onClick={() => upd({ knowledge_docs: [...draft.knowledge_docs, ""] })}
                      style={{ fontSize: 13, fontWeight: 600, color: C.accentDeep, background: C.accentSoft, border: "none",
                        borderRadius: 8, padding: "6px 11px", cursor: "pointer" }}>+ Add fact</button>
                  </div>
                </div>
                {kbMsg && <div style={{ fontSize: 12.5, color: C.muted, marginBottom: 8 }}>{kbMsg}</div>}
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
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div onClick={() => setAdvOpen((o) => !o)} style={{ display: "flex", alignItems: "center", gap: 8,
                    cursor: "pointer", fontSize: 13.5, fontWeight: 600, color: C.muted }}>
                    <span style={{ transform: `rotate(${advOpen ? 90 : 0}deg)`, transition: "transform .2s" }}>▸</span>
                    Advanced · system prompt <span style={{ fontWeight: 500, color: "#A79E8B" }}>(yours to edit or replace)</span></div>
                  <button onClick={enhance} disabled={enhancing}
                    style={{ fontSize: 13, fontWeight: 600, color: C.accentDeep, background: C.accentSoft, border: "none",
                      borderRadius: 8, padding: "6px 11px", cursor: "pointer", opacity: enhancing ? 0.6 : 1 }}>
                    {enhancing ? "Enhancing…" : "✨ Enhance from my description"}</button>
                </div>
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
              <CallPanel agentId={draft.agent_id} subtitle={draft.name} orbSize={200}
                forceVoice={draft.voice} />
              <div style={{ marginTop: 14, textAlign: "left" }}>
                {!cloneOpen ? (
                  <div style={{ textAlign: "center" }}>
                    <button onClick={() => setCloneOpen(true)}
                      style={{ background: "transparent", border: `1px dashed ${C.lineSoft}`, borderRadius: 10,
                        padding: "8px 16px", fontSize: 13.5, fontWeight: 600, color: C.ink, cursor: "pointer" }}>
                      🎙 Make her speak in YOUR voice</button>
                  </div>
                ) : (
                  <div style={{ background: C.paperCard, border: `1px solid ${C.line}`, borderRadius: 14,
                    padding: "13px 15px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                      <span style={{ fontSize: 13, fontWeight: 700 }}>Clone your voice</span>
                      <span onClick={() => { cloneStop.current?.(); setCloneOpen(false); }}
                        style={{ cursor: "pointer", color: C.faint }}>✕</span>
                    </div>
                    <div style={{ fontSize: 12.5, color: C.muted, lineHeight: 1.5, marginBottom: 8 }}>
                      Read aloud in English ({CLONE_SECONDS}s) — she'll speak <b>every language</b> in your voice:
                      <div style={{ color: C.ink, marginTop: 4, fontStyle: "italic" }}>{READ_SCRIPT}</div>
                    </div>
                    <button onClick={async () => {
                      if (cloneState === "rec") { cloneStop.current?.(); return; }
                      setCloneMsg("");
                      try {
                        cloneStop.current = await startCloneRecording({
                          onTick: setCloneSecs,
                          onDone: async ({ b64, seconds }) => {
                            cloneStop.current = null;
                            if (seconds < MIN_CLONE_SECONDS) {
                              setCloneState("error"); setCloneMsg(`Too short — record at least ${MIN_CLONE_SECONDS}s.`); return;
                            }
                            setCloneState("uploading");
                            try {
                              const r = await api.cloneAgentVoice(draft.agent_id, b64);
                              setDraft((d) => d ? { ...d, voice: r.voice } : d);
                              setCloneState("done");
                              setCloneMsg("Done! Tap the orb — she speaks in YOUR voice now.");
                            } catch (e: any) {
                              setCloneState("error");
                              setCloneMsg(String(e?.message || "Cloning failed — try again."));
                            }
                          },
                        });
                        setCloneState("rec");
                      } catch { setCloneState("error"); setCloneMsg("Microphone permission denied."); }
                    }} disabled={cloneState === "uploading"}
                      style={{ width: "100%", padding: "9px 0", borderRadius: 10, border: "none", fontSize: 13,
                        fontWeight: 700, cursor: "pointer", color: "#fff",
                        background: cloneState === "rec" ? C.red : C.ink,
                        opacity: cloneState === "uploading" ? 0.6 : 1 }}>
                      {cloneState === "rec" ? `● Recording… ${cloneSecs}s (tap to finish)`
                        : cloneState === "uploading" ? "Cloning your voice…"
                        : cloneState === "done" ? "Re-record" : "● Start recording"}</button>
                    {cloneMsg && <div style={{ fontSize: 12.5, marginTop: 7, fontWeight: 600,
                      color: cloneState === "error" ? "#B24A2E" : C.green }}>{cloneMsg}</div>}
                  </div>
                )}
              </div>
            </div>
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
