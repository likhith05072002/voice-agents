import { memo, useEffect, useRef, useState } from "react";
import { Orb } from "./Orb";
import { useWebCall } from "../useWebCall";
import { api } from "../api";
import { C, mono } from "../theme";

// Reusable "talk to this agent" panel: orb + connect + live dual captions.
// Wired to the REAL /web-call WebSocket — no fakes.
// memo()ed so parent-page state flips (taglines etc.) never re-render the
// call subtree while audio is running; the orb animates via levelRef, not props.
export const CallPanel = memo(function CallPanel({ agentId, subtitle, orbSize = 210, voicePicker, forceVoice, forcePace }:
  { agentId: string; subtitle?: string; orbSize?: number; voicePicker?: boolean;
    forceVoice?: string; forcePace?: number }) {
  const { status, captions, levelRef, remaining, endedReason, start, stop } = useWebCall();
  const capRef = useRef<HTMLDivElement | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [voice, setVoice] = useState<string>("");      // "" = agent default
  const previewRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => stop(), [stop]); // hang up on unmount
  useEffect(() => {
    if (voicePicker) api.voiceLab().then((r) => setVoices(r.voices)).catch(() => {});
  }, [voicePicker]);
  useEffect(() => {
    if (capRef.current) capRef.current.scrollTop = capRef.current.scrollHeight;
  }, [captions]);
  const previewVoice = (v: string) => {
    if (!v) return;
    if (!previewRef.current) previewRef.current = new Audio();
    previewRef.current.src = api.voiceSampleUrl(v);
    previewRef.current.play().catch(() => {});
  };

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
          margin: "2px 0 12px" }}>
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
          </select>
          <span onClick={() => previewVoice(voice || "neha")} title="Preview this voice"
            style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28,
              height: 28, borderRadius: "50%", background: C.ink, cursor: "pointer" }}>
            <div style={{ width: 0, height: 0, borderLeft: "7px solid #fff",
              borderTop: "5px solid transparent", borderBottom: "5px solid transparent", marginLeft: 2 }} />
          </span>
        </div>
      )}

      {endedReason === "time_limit" && status === "idle" && (
        <div style={{ textAlign: "center", background: "#FBE8E2", border: "1px solid #F1C9BD",
          color: "#B24A2E", borderRadius: 10, padding: "8px 12px", fontSize: 12.5, fontWeight: 600,
          margin: "0 2px 10px" }}>
          ⏱ Demo time's up — 3-minute limit. Tap the orb to talk again.
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
