import { memo, useEffect, useRef } from "react";
import { Orb } from "./Orb";
import { useWebCall } from "../useWebCall";
import { C, mono } from "../theme";

// Reusable "talk to this agent" panel: orb + connect + live dual captions.
// Wired to the REAL /web-call WebSocket — no fakes.
// memo()ed so parent-page state flips (taglines etc.) never re-render the
// call subtree while audio is running; the orb animates via levelRef, not props.
export const CallPanel = memo(function CallPanel({ agentId, subtitle, orbSize = 210 }:
  { agentId: string; subtitle?: string; orbSize?: number }) {
  const { status, captions, levelRef, start, stop } = useWebCall();
  const capRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => () => stop(), [stop]); // hang up on unmount
  useEffect(() => {
    if (capRef.current) capRef.current.scrollTop = capRef.current.scrollHeight;
  }, [captions]);

  const statusColor =
    status === "live" ? C.green : status === "connecting" ? C.accent : C.faint;
  const statusLabel =
    status === "live" ? "live" : status === "connecting" ? "connecting…" : "idle";
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
          onClick={() => (status === "idle" ? start(agentId) : stop())}>
          <Orb size={orbSize} levelRef={levelRef} active={status === "live"} />
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center",
            justifyContent: "center", pointerEvents: "none" }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: "#fff",
              textShadow: "0 1px 6px rgba(120,66,0,.5)" }}>{center}</div>
          </div>
        </div>
      </div>

      <div ref={capRef} style={{ minHeight: 132, maxHeight: 210, overflowY: "auto",
        display: "flex", flexDirection: "column", gap: 8, padding: "6px 2px 2px" }}>
        {captions.length === 0 ? (
          <div style={{ textAlign: "center", color: "#A79E8B", fontSize: 13, padding: "30px 10px", lineHeight: 1.5 }}>
            Tap the orb and say hello.<br />She answers in English — reply in हिंदी and she'll follow.
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
