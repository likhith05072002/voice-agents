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

// Dead-microphone detection. Some Windows/Chrome driver combinations hand the
// page a stream that is DIGITALLY SILENT (every sample 0) when the processing
// constraints below are requested — the call looks perfect, frames flow at the
// right rate, and the agent simply never hears anything. Seen in production:
// server-side telemetry read peak_rms=2 for a whole call while a synthesized
// probe on the same server read 6715.
//
// A quiet room still carries ambient energy, so the bar is set at true digital
// silence rather than "quiet" — that keeps a user who simply hasn't spoken yet
// from being told their mic is broken.
const MIC_DEAD_PEAK = 0.001;      // ~32/32768 — below this nothing is arriving
const MIC_CHECK_MS = 6000;        // give the user time to actually say something

const MIC_PROCESSED: MediaTrackConstraints = {
  echoCancellation: true, noiseSuppression: true, autoGainControl: true,
};
// The fallback: identical capture with all browser DSP off. This is what
// recovers the silent-driver case, at the cost of no echo cancellation.
const MIC_RAW: MediaTrackConstraints = {
  echoCancellation: false, noiseSuppression: false, autoGainControl: false,
};

export function useWebCall() {
  const [status, setStatus] = useState<CallStatus>("idle");
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [remaining, setRemaining] = useState<number | null>(null); // seconds left, null = uncapped
  const [endedReason, setEndedReason] =
    useState<"time_limit" | "no_credits" | "credits_exhausted" | null>(null);
  // "silent" = the browser handed us a stream carrying no audio at all, even
  // after retrying without DSP. Surfaced so the caller sees a real reason
  // instead of an agent that never responds.
  const [micIssue, setMicIssue] = useState<"silent" | null>(null);
  // hot-path outputs — read these in rAF loops, never via React state
  const levelRef = useRef(0);
  const speakingRef = useRef<"agent" | "user" | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const proc = useRef<ScriptProcessorNode | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);
  const watchdog = useRef<number | null>(null);

  const stop = useCallback(() => {
    const sock = ws.current; ws.current = null;
    if (sock && sock.readyState <= 1) sock.close();
    if (watchdog.current) { clearTimeout(watchdog.current); watchdog.current = null; }
    proc.current?.disconnect(); proc.current = null;
    stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null;
    ctx.current?.close().catch(() => {}); ctx.current = null;
    if (ticker.current) { clearInterval(ticker.current); ticker.current = null; }
    levelRef.current = 0; speakingRef.current = null;
    setRemaining(null);
    setStatus("idle");
  }, []);

  const start = useCallback(async (agentId: string, voice?: string, pace?: number) => {
    if (ws.current) { stop(); return; }
    setStatus("connecting"); setCaptions([]); setEndedReason(null); setRemaining(null);
    setMicIssue(null);
    let ms: MediaStream;
    try {
      ms = await navigator.mediaDevices.getUserMedia({ audio: MIC_PROCESSED });
    } catch {
      setStatus("idle");
      throw new Error("microphone permission denied");
    }
    stream.current = ms;
    const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    ctx.current = audioCtx;

    const socket = new WebSocket(api.webCallUrl(agentId, voice, pace));
    socket.binaryType = "arraybuffer";
    ws.current = socket;

    socket.onopen = () => setStatus("live");
    socket.onclose = () => { if (ws.current) stop(); };
    socket.onerror = () => { if (ws.current) stop(); };

    let playSamples = 0, playEpoch = 0; // sample-exact schedule: no float drift
    socket.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data) as any;
        // Control frames from the server (demo time cap).
        if (m.type === "call_start" && m.max_seconds) {
          setRemaining(m.max_seconds);
          if (ticker.current) clearInterval(ticker.current);
          ticker.current = setInterval(() => {
            setRemaining((r) => (r === null ? r : Math.max(0, r - 1)));
          }, 1000);
          return;
        }
        if (m.type === "call_end") {
          setEndedReason(
            m.reason === "time_limit" || m.reason === "no_credits" ||
            m.reason === "credits_exhausted" ? m.reason : null);
          stop();
          return;
        }
        if (m.text?.trim()) setCaptions((c) => [...c, m as Caption]); // few per turn
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
    let source = audioCtx.createMediaStreamSource(ms);
    const node = audioCtx.createScriptProcessor(4096, 1, 1);
    proc.current = node;
    const ratio = audioCtx.sampleRate / 16000;
    let micPeak = 0;                  // loudest sample seen on the CURRENT mic
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
      if (peak > micPeak) micPeak = peak;      // one compare; hot path stays clean
      if (peak > 0.06) { levelRef.current = peak; speakingRef.current = "user"; }
    };
    source.connect(node);
    const mute = audioCtx.createGain(); mute.gain.value = 0; // keep node alive, no echo
    node.connect(mute); mute.connect(audioCtx.destination);

    // Dead-mic watchdog. If the stream carried literally nothing, retry ONCE
    // with the browser's DSP disabled (the known silent-driver workaround),
    // then give up and tell the caller rather than failing mutely forever.
    const swapToRawMic = async () => {
      try {
        const raw = await navigator.mediaDevices.getUserMedia({ audio: MIC_RAW });
        ms.getTracks().forEach((t) => t.stop());
        stream.current = raw;
        source.disconnect();
        source = audioCtx.createMediaStreamSource(raw);
        source.connect(node);
        micPeak = 0;                            // judge the new mic on its own
        return true;
      } catch {
        return false;
      }
    };
    const micWatchdog = window.setTimeout(async () => {
      if (!ws.current || micPeak >= MIC_DEAD_PEAK) return;   // audio is fine
      const swapped = await swapToRawMic();
      window.setTimeout(() => {
        if (!ws.current) return;
        if (!swapped || micPeak < MIC_DEAD_PEAK) setMicIssue("silent");
      }, MIC_CHECK_MS);
    }, MIC_CHECK_MS);
    watchdog.current = micWatchdog;
  }, [stop]);

  return { status, captions, levelRef, speakingRef, remaining, endedReason,
           micIssue, start, stop };
}
