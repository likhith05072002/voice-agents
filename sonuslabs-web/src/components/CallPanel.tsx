import { memo, useEffect, useRef, useState } from "react";
import { Orb } from "./Orb";
import { useWebCall } from "../useWebCall";
import { api } from "../api";
import { C, mono } from "../theme";
import { CLONE_SECONDS, MIN_CLONE_SECONDS, READ_SCRIPT,
         startCloneRecording } from "../voiceCloneKit";

// Reusable "talk to this agent" panel: orb + connect + live dual captions.
// Wired to the REAL /web-call WebSocket — no fakes.
// memo()ed so parent-page state flips (taglines etc.) never re-render the
// call subtree while audio is running; the orb animates via levelRef, not props.
export const CallPanel = memo(function CallPanel({ agentId, subtitle, orbSize = 210, voicePicker, forceVoice, forcePace }:
  { agentId: string; subtitle?: string; orbSize?: number; voicePicker?: boolean;
    forceVoice?: string; forcePace?: number }) {
  const { status, captions, levelRef, remaining, endedReason, micIssue, start, stop } = useWebCall();
  const capRef = useRef<HTMLDivElement | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [voice, setVoice] = useState<string>("");      // "" = agent default
  const previewRef = useRef<HTMLAudioElement | null>(null);
  // ── Clone-your-voice (one English sample covers every spoken language) ──
  const [cloneOpen, setCloneOpen] = useState(false);
  const [cloneState, setCloneState] =
    useState<"idle" | "rec" | "uploading" | "done" | "error">("idle");
  const [cloneMsg, setCloneMsg] = useState("");
  const [cloneSecs, setCloneSecs] = useState(0);
  const [clonedId, setClonedId] = useState("");
  const recStop = useRef<(() => void) | null>(null);
  useEffect(() => () => { recStop.current?.(); }, []); // kill recorder on unmount
  useEffect(() => () => stop(), [stop]); // hang up on unmount
  useEffect(() => {
    if (voicePicker) api.voiceLab().then((r) => setVoices(r.voices)).catch(() => {});
  }, [voicePicker]);
  useEffect(() => {
    if (capRef.current) capRef.current.scrollTop = capRef.current.scrollHeight;
  }, [captions]);
  const previewVoice = (v: string) => {
    if (!v || v.startsWith("inworld:")) return;  // cloned voices have no sample
    if (!previewRef.current) previewRef.current = new Audio();
    previewRef.current.src = api.voiceSampleUrl(v);
    previewRef.current.play().catch(() => {});
  };

  const recordClone = async () => {
    if (cloneState === "rec") { recStop.current?.(); return; }
    setCloneMsg("");
    let stop: () => void;
    try {
      stop = await startCloneRecording({
        onTick: setCloneSecs,
        onDone: async ({ b64, seconds }) => {
          recStop.current = null;
          if (seconds < MIN_CLONE_SECONDS) {
            setCloneState("error");
            setCloneMsg(`Too short — record at least ${MIN_CLONE_SECONDS} seconds.`);
            return;
          }
          setCloneState("uploading");
          try {
            const r = await api.cloneVoice("en", b64);
            setClonedId(r.voice_id);
            setVoice(`inworld:${r.voice_id}`);
            setCloneState("done");
            setCloneMsg("Cloned! Tap the orb — the agent speaks in YOUR voice, in every language.");
          } catch (e: any) {
            setCloneState("error");
            setCloneMsg(e?.message === "429" ? "Please wait a moment and try again."
              : "Cloning failed — try a longer, clearer recording.");
          }
        },
      });
    } catch { setCloneState("error"); setCloneMsg("Microphone permission denied."); return; }
    setCloneState("rec");
    recStop.current = stop;
  };

  const readScript = READ_SCRIPT;

  const low = remaining !== null && remaining <= 20;
  const clock = remaining === null ? null
    : `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
  const statusColor =
    status === "live" ? (low ? C.red : C.green) : status === "connecting" ? C.accent : C.faint;
  const statusLabel =
    status === "live" ? (clock ? `${clock} left` : "live")
      : status === "connecting" ? "connecting…" : "idle";
  const center =
    status === "idle" ? "Tap to talk" : status === "connecting" ? "…" : "Listening";

  return (
    <div style={{ background: C.paperCard, border: `1px solid ${C.line}`, borderRadius: 26,
      padding: "26px 24px 22px", boxShadow: "0 30px 60px -34px rgba(33,28,21,.28)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 30, height: 30, borderRadius: 9, background: C.ink,
            display: "flex", alignItems: "center", justifyContent: "center", color: "#fff",
            fontSize: 12, fontWeight: 700 }}>{(agentId[0] || "S").toUpperCase()}</div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, lineHeight: 1.1 }}>{agentId}</div>
            <div style={{ fontSize: 11.5, color: C.faint }}>{subtitle || "web call"}</div>
          </div>
        </div>
        <div style={{ fontFamily: mono, fontSize: 11.5, color: statusColor,
          display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: statusColor }} />
          {statusLabel}
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "center", padding: "14px 0 10px" }}>
        <div style={{ position: "relative", width: orbSize, height: orbSize, cursor: "pointer" }}
          onClick={() => (status === "idle"
            ? start(agentId, forceVoice || voice || undefined, forcePace)
            : stop())}>
          <Orb size={orbSize} levelRef={levelRef} active={status === "live"} />
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center",
            justifyContent: "center", pointerEvents: "none" }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: "#fff",
              textShadow: "0 1px 6px rgba(120,66,0,.5)" }}>{center}</div>
          </div>
        </div>
      </div>

      {voicePicker && voices.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
          margin: "2px 0 6px" }}>
          <span style={{ fontSize: 12, color: C.faint }}>Voice</span>
          <select value={voice} onChange={(e) => setVoice(e.target.value)}
            disabled={status !== "idle"}
            style={{ background: C.paper, border: `1px solid ${C.lineSoft}`, borderRadius: 9,
              padding: "7px 10px", fontSize: 13, color: C.ink, outline: "none",
              textTransform: "capitalize", cursor: status === "idle" ? "pointer" : "default",
              opacity: status === "idle" ? 1 : 0.6 }}>
            <option value="">Neha (default)</option>
            {voices.filter((v) => v !== "neha").map((v) => (
              <option key={v} value={v} style={{ textTransform: "capitalize" }}>{v}</option>
            ))}
            {clonedId && (
              <option value={`inworld:${clonedId}`}>🎙 Your voice (cloned)</option>
            )}
          </select>
          <span onClick={() => previewVoice(voice || "neha")} title="Preview this voice"
            style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28,
              height: 28, borderRadius: "50%", background: C.ink,
              cursor: voice.startsWith("inworld:") ? "default" : "pointer",
              opacity: voice.startsWith("inworld:") ? 0.35 : 1 }}>
            <div style={{ width: 0, height: 0, borderLeft: "7px solid #fff",
              borderTop: "5px solid transparent", borderBottom: "5px solid transparent", marginLeft: 2 }} />
          </span>
        </div>
      )}

      {voicePicker && (
        <div style={{ textAlign: "center", margin: "0 0 12px" }}>
          {!cloneOpen ? (
            <button onClick={() => setCloneOpen(true)} disabled={status !== "idle"}
              style={{ background: "transparent", border: `1px dashed ${C.lineSoft}`,
                borderRadius: 10, padding: "6px 14px", fontSize: 12.5, fontWeight: 600,
                color: C.ink, cursor: "pointer", opacity: status === "idle" ? 1 : 0.5 }}>
              🎙 Clone your voice
            </button>
          ) : (
            <div style={{ background: C.paper, border: `1px solid ${C.lineSoft}`,
              borderRadius: 14, padding: "12px 14px", textAlign: "left" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 12.5, fontWeight: 700 }}>Clone your voice</span>
                <span onClick={() => { recStop.current?.(); setCloneOpen(false); }}
                  style={{ cursor: "pointer", fontSize: 14, color: C.faint, padding: "0 2px" }}>✕</span>
              </div>
              <div style={{ fontSize: 12, color: C.faint, lineHeight: 1.5, margin: "8px 0" }}>
                Read this aloud in English ({CLONE_SECONDS}s, your normal voice) — the
                clone will speak <b>every language</b> the agent does:
                <div style={{ color: C.ink, marginTop: 4, fontStyle: "italic" }}>{readScript}</div>
                <div style={{ marginTop: 5 }}>
                  Relax and keep going — a small slip is fine, stopping and restarting is not.
                </div>
              </div>
              <button onClick={recordClone} disabled={cloneState === "uploading"}
                style={{ width: "100%", padding: "9px 0", borderRadius: 10, border: "none",
                  fontSize: 13, fontWeight: 700, cursor: "pointer", color: "#fff",
                  background: cloneState === "rec" ? C.red : C.ink,
                  opacity: cloneState === "uploading" ? 0.6 : 1 }}>
                {cloneState === "rec" ? `● Recording… ${cloneSecs}s (tap to finish)`
                  : cloneState === "uploading" ? "Cloning your voice…"
                  : cloneState === "done" ? "Re-record" : "● Start recording"}
              </button>
              {cloneMsg && (
                <div style={{ fontSize: 12, marginTop: 8, lineHeight: 1.45,
                  color: cloneState === "error" ? "#B24A2E" : C.green, fontWeight: 600 }}>
                  {cloneMsg}
                </div>
              )}
              <div style={{ fontSize: 10.5, color: C.faint, marginTop: 8, lineHeight: 1.4 }}>
                Clone only your own voice. Demo clones auto-delete after 30 minutes.
              </div>
            </div>
          )}
        </div>
      )}

      {micIssue === "silent" && status === "live" && (
        <div style={{ textAlign: "center", background: "#FBE8E2", border: "1px solid #F1C9BD",
          color: "#B24A2E", borderRadius: 10, padding: "9px 12px", fontSize: 12.5,
          fontWeight: 600, margin: "0 2px 10px", lineHeight: 1.5 }}>
          🎙 We can't hear your microphone — it's connected but sending no sound.<br />
          <span style={{ fontWeight: 500 }}>
            Close any app using it (Zoom, Teams, Meet), check the mic isn't muted,
            then reload and try again.
          </span>
        </div>
      )}

      {endedReason !== null && status === "idle" && (
        <div style={{ textAlign: "center", background: "#FBE8E2", border: "1px solid #F1C9BD",
          color: "#B24A2E", borderRadius: 10, padding: "8px 12px", fontSize: 12.5, fontWeight: 600,
          margin: "0 2px 10px" }}>
          {endedReason === "time_limit"
            ? "⏱ Demo time's up — 3-minute limit. Tap the orb to talk again."
            : endedReason === "no_credits"
              ? "💳 Out of credits — add credits in the Billing tab to keep talking."
              : "💳 Credits ran out mid-call — top up in the Billing tab to continue."}
        </div>
      )}
      <div ref={capRef} style={{ minHeight: 132, maxHeight: 210, overflowY: "auto",
        display: "flex", flexDirection: "column", gap: 8, padding: "6px 2px 2px" }}>
        {captions.length === 0 ? (
          <div style={{ textAlign: "center", color: "#A79E8B", fontSize: 13, padding: "30px 10px", lineHeight: 1.5 }}>
            {endedReason === "time_limit" ? (
              <>Demo time's up — 3-minute limit.<br />Tap the orb to start a new call.</>
            ) : (
              <>Tap the orb and say hello.<br />She answers in English — reply in हिंदी and she'll follow.</>
            )}
          </div>
        ) : captions.map((c, i) => (
          <div key={i} style={{
            maxWidth: "82%", padding: "9px 13px", borderRadius: 14, fontSize: 13.5, lineHeight: 1.4,
            ...(c.role === "user"
              ? { alignSelf: "flex-end", background: C.accent, color: C.ink, borderBottomRightRadius: 4 }
              : { alignSelf: "flex-start", background: "#F1ECE0", color: C.ink, borderBottomLeftRadius: 4 }),
          }}>{c.text}</div>
        ))}
      </div>
    </div>
  );
});
