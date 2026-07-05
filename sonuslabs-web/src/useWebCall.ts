import { useCallback, useRef, useState } from "react";
import { api } from "./api";

// This is a 1:1 port of the battle-tested client in src/testing/dashboard.py
// (webCall). Every audio decision is a hard-won backend lesson — do not "simplify":
//  - mic downsample by BOX-AVERAGE (naive picking aliases noise into STT)
//  - playback scheduled by SAMPLE COUNT (float durations drift + click)
//  - 250ms cushion; re-anchor only on a real stall
//  - NOTHING on the audio hot paths touches React state. ScriptProcessorNode
//    runs on the main thread; a setState per chunk delays capture/scheduling
//    and turns wifi jitter into audible gaps. Level/speaking go through
//    mutable refs that the Orb reads inside its rAF loop.
// Uplink: PCM16 mono 16k. Downlink: PCM16 mono 16k + JSON {role,text} frames.

export type CallStatus = "idle" | "connecting" | "live";
export interface Caption { role: "user" | "assistant"; text: string }

export function useWebCall() {
  const [status, setStatus] = useState<CallStatus>("idle");
  const [captions, setCaptions] = useState<Caption[]>([]);
  // hot-path outputs — read these in rAF loops, never via React state
  const levelRef = useRef(0);
  const speakingRef = useRef<"agent" | "user" | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const proc = useRef<ScriptProcessorNode | null>(null);

  const stop = useCallback(() => {
    const sock = ws.current; ws.current = null;
    if (sock && sock.readyState <= 1) sock.close();
    proc.current?.disconnect(); proc.current = null;
    stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null;
    ctx.current?.close().catch(() => {}); ctx.current = null;
    levelRef.current = 0; speakingRef.current = null;
    setStatus("idle");
  }, []);

  const start = useCallback(async (agentId: string) => {
    if (ws.current) { stop(); return; }
    setStatus("connecting"); setCaptions([]);
    let ms: MediaStream;
    try {
      ms = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      setStatus("idle");
      throw new Error("microphone permission denied");
    }
    stream.current = ms;
    const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    ctx.current = audioCtx;

    const socket = new WebSocket(api.webCallUrl(agentId));
    socket.binaryType = "arraybuffer";
    ws.current = socket;

    socket.onopen = () => setStatus("live");
    socket.onclose = () => { if (ws.current) stop(); };
    socket.onerror = () => { if (ws.current) stop(); };

    let playSamples = 0, playEpoch = 0; // sample-exact schedule: no float drift
    socket.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data) as Caption;
        if (m.text?.trim()) setCaptions((c) => [...c, m]); // few per turn — fine
        return;
      }
      // agent audio: PCM16 mono 16k — decode + schedule, NO state updates
      const i16 = new Int16Array(e.data as ArrayBuffer);
      const buf = audioCtx.createBuffer(1, i16.length, 16000);
      const ch = buf.getChannelData(0);
      let peak = 0;
      for (let i = 0; i < i16.length; i++) {
        const s = i16[i] / 32768;
        ch[i] = s;
        const a = s < 0 ? -s : s; if (a > peak) peak = a;
      }
      const src = audioCtx.createBufferSource();
      src.buffer = buf; src.connect(audioCtx.destination);
      // Accumulating float durations drifts by sub-sample amounts and clicks at
      // chunk joins; deriving each start time from the TOTAL SAMPLE COUNT keeps
      // every chunk sample-adjacent forever. The 250ms cushion is the jitter
      // buffer: hiccups smaller than it are inaudible, and a re-anchor (an
      // audible gap) only happens on a genuine stall.
      let t = playEpoch + playSamples / 16000;
      if (t < audioCtx.currentTime + 0.02) {
        playEpoch = audioCtx.currentTime + 0.25;
        playSamples = 0;
        t = playEpoch;
      }
      src.start(t);
      playSamples += i16.length;
      levelRef.current = peak;
      speakingRef.current = "agent";
    };

    // uplink: mic -> 16k PCM16 — NO state updates in here
    const source = audioCtx.createMediaStreamSource(ms);
    const node = audioCtx.createScriptProcessor(4096, 1, 1);
    proc.current = node;
    const ratio = audioCtx.sampleRate / 16000;
    node.onaudioprocess = (ev) => {
      if (socket.readyState !== 1) return;
      const inp = ev.inputBuffer.getChannelData(0);
      const out = new Int16Array(Math.floor(inp.length / ratio));
      let peak = 0;
      for (let i = 0; i < out.length; i++) {
        // Box-average over the decimation window: naive sample-picking aliases
        // high frequencies into audible noise in the STT feed.
        const a = Math.floor(i * ratio), b = Math.floor((i + 1) * ratio);
        let sum = 0; for (let k = a; k < b; k++) sum += inp[k];
        const v = sum / Math.max(1, b - a);
        out[i] = Math.max(-32768, Math.min(32767, v * 32768));
        const av = v < 0 ? -v : v; if (av > peak) peak = av;
      }
      socket.send(out.buffer);
      if (peak > 0.06) { levelRef.current = peak; speakingRef.current = "user"; }
    };
    source.connect(node);
    const mute = audioCtx.createGain(); mute.gain.value = 0; // keep node alive, no echo
    node.connect(mute); mute.connect(audioCtx.destination);
  }, [stop]);

  return { status, captions, levelRef, speakingRef, start, stop };
}
