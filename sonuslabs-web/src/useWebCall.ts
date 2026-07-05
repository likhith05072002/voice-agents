import { useCallback, useRef, useState } from "react";
import { api } from "./api";

// Every audio decision here is a hard-won backend lesson — do not "simplify":
//  - mic downsample by BOX-AVERAGE (naive picking aliases noise into STT)
//  - playback scheduled by SAMPLE COUNT (float durations drift + click)
//  - 250ms jitter cushion; re-anchor only on a real stall
// Uplink: PCM16 mono 16k. Downlink: PCM16 mono 16k + JSON {role,text} frames.

export type CallStatus = "idle" | "connecting" | "live";
export interface Caption { role: "user" | "assistant"; text: string }

interface Live {
  status: CallStatus;
  captions: Caption[];
  level: number;          // 0..1 mic/agent energy for the orb
  speaking: "agent" | "user" | null;
}

export function useWebCall() {
  const [live, setLive] = useState<Live>({
    status: "idle", captions: [], level: 0, speaking: null,
  });
  const ws = useRef<WebSocket | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const proc = useRef<ScriptProcessorNode | null>(null);
  const playEpoch = useRef(0);
  const playSamples = useRef(0);
  const levelRef = useRef(0);

  const stop = useCallback(() => {
    ws.current?.close(); ws.current = null;
    proc.current?.disconnect(); proc.current = null;
    stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null;
    ctx.current?.close().catch(() => {}); ctx.current = null;
    setLive((l) => ({ ...l, status: "idle", level: 0, speaking: null }));
  }, []);

  const start = useCallback(async (agentId: string) => {
    if (ws.current) { stop(); return; }
    setLive({ status: "connecting", captions: [], level: 0, speaking: null });
    let ms: MediaStream;
    try {
      ms = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      setLive((l) => ({ ...l, status: "idle" }));
      throw new Error("microphone permission denied");
    }
    stream.current = ms;
    const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    ctx.current = audioCtx;
    playEpoch.current = 0; playSamples.current = 0;

    const socket = new WebSocket(api.webCallUrl(agentId));
    socket.binaryType = "arraybuffer";
    ws.current = socket;

    socket.onopen = () => setLive((l) => ({ ...l, status: "live" }));
    socket.onclose = () => { if (ws.current) stop(); };
    socket.onerror = () => { if (ws.current) stop(); };

    socket.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data) as Caption;
        if (m.text?.trim()) {
          setLive((l) => ({ ...l, captions: [...l.captions, m], speaking: "agent" }));
        }
        return;
      }
      // agent audio: PCM16 mono 16k
      const i16 = new Int16Array(e.data as ArrayBuffer);
      const buf = audioCtx.createBuffer(1, i16.length, 16000);
      const ch = buf.getChannelData(0);
      let peak = 0;
      for (let i = 0; i < i16.length; i++) {
        const s = i16[i] / 32768;
        ch[i] = s;
        const a = Math.abs(s); if (a > peak) peak = a;
      }
      const src = audioCtx.createBufferSource();
      src.buffer = buf; src.connect(audioCtx.destination);
      let t = playEpoch.current + playSamples.current / 16000;
      if (t < audioCtx.currentTime + 0.02) {
        playEpoch.current = audioCtx.currentTime + 0.25; // re-anchor after a real stall
        playSamples.current = 0;
        t = playEpoch.current;
      }
      src.start(t);
      playSamples.current += i16.length;
      levelRef.current = peak;
      setLive((l) => (l.level === peak ? l : { ...l, level: peak, speaking: "agent" }));
    };

    // uplink: mic -> 16k PCM16
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
        const a = Math.floor(i * ratio), b = Math.floor((i + 1) * ratio);
        let sum = 0; for (let k = a; k < b; k++) sum += inp[k];
        const v = sum / Math.max(1, b - a);
        out[i] = Math.max(-32768, Math.min(32767, v * 32768));
        const av = Math.abs(v); if (av > peak) peak = av;
      }
      socket.send(out.buffer);
      if (peak > 0.06) {
        levelRef.current = peak;
        setLive((l) => ({ ...l, level: peak, speaking: "user" }));
      }
    };
    source.connect(node);
    const mute = audioCtx.createGain(); mute.gain.value = 0;
    node.connect(mute); mute.connect(audioCtx.destination);
  }, [stop]);

  return { live, start, stop };
}
