// Shared voice-clone recording kit — used by the landing demo widget
// (CallPanel) and the console agent editor. 15s of mic -> PCM16 mono 16k
// WAV -> base64. English-only: Inworld's cross-lingual cloning means one
// English sample speaks every language the agent does (verified live).
//
// Hot-path rule (same as the call client): audio chunks accumulate in a
// plain array — NOTHING in onaudioprocess touches React state.

export const CLONE_SECONDS = 15;                    // Inworld's max sample length
export const MAX_CLONE_SAMPLES = CLONE_SECONDS * 16000;
export const MIN_CLONE_SECONDS = 4;

// "Warm Welcome" (user-picked): reads as the owner greeting a customer —
// meaningful to say, still phonetically COMPLETE. Audited: all 26 letters
// (quickest/relax/zero/just), all 5 diphthongs (my/waiting/enjoy/zero/how),
// both th's (think/this), both affricates (such/just), zh (pleasure),
// sh (fresh), ng (giving/waiting), hard g (giving) — everyday words only.
// Ends on a question: rising pitch widens the prosody the cloner captures.
export const READ_SCRIPT =
  "“Hi, this is my voice. Welcome — it's such a pleasure to have you here. " +
  "I enjoy giving the quickest and best answers, with zero waiting. " +
  "Just relax, and think of this as a fresh, warm start. " +
  "How does my voice sound?”";

export function encodeWavB64(chunks: Int16Array[]): string {
  let n = 0;
  for (const c of chunks) n += c.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(buf);
  const w = (o: number, s: string) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); dv.setUint32(4, 36 + n * 2, true); w(8, "WAVE");
  w(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true); dv.setUint32(24, 16000, true);
  dv.setUint32(28, 32000, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  w(36, "data"); dv.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) dv.setInt16(o, c[i], true);
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000)
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export interface CloneRecordingResult { b64: string; seconds: number }

/** Start a clone recording. Resolves to a stop() that ends it early; the
 *  recording also auto-stops at CLONE_SECONDS. onDone fires exactly once
 *  with the hard-trimmed WAV. Throws if the mic permission is denied. */
export async function startCloneRecording(opts: {
  onTick: (secondsLeft: number) => void;
  onDone: (r: CloneRecordingResult) => void;
}): Promise<() => void> {
  const ms = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
  const chunks: Int16Array[] = [];              // plain array, never state
  const source = audioCtx.createMediaStreamSource(ms);
  const node = audioCtx.createScriptProcessor(4096, 1, 1);
  const ratio = audioCtx.sampleRate / 16000;
  node.onaudioprocess = (ev) => {
    const inp = ev.inputBuffer.getChannelData(0);
    const out = new Int16Array(Math.floor(inp.length / ratio));
    for (let i = 0; i < out.length; i++) {
      // Box-average over the decimation window: naive sample-picking aliases
      // high frequencies into audible noise (same lesson as the call uplink).
      const a = Math.floor(i * ratio), b = Math.floor((i + 1) * ratio);
      let sum = 0; for (let k = a; k < b; k++) sum += inp[k];
      const v = sum / Math.max(1, b - a);
      out[i] = Math.max(-32768, Math.min(32767, v * 32768));
    }
    chunks.push(out);
  };
  source.connect(node);
  const mute = audioCtx.createGain(); mute.gain.value = 0;   // keep node alive, no echo
  node.connect(mute); mute.connect(audioCtx.destination);

  let left = CLONE_SECONDS;
  opts.onTick(left);
  const tick = setInterval(() => { left = Math.max(0, left - 1); opts.onTick(left); }, 1000);

  let finished = false;
  const finish = () => {
    if (finished) return; finished = true;
    clearInterval(tick); clearTimeout(cap);
    node.disconnect(); source.disconnect();
    ms.getTracks().forEach((t) => t.stop());
    audioCtx.close().catch(() => {});
    // Hard-trim to Inworld's 15s sample cap (timer jitter can overshoot).
    let total = 0;
    const trimmed: Int16Array[] = [];
    for (const c of chunks) {
      if (total >= MAX_CLONE_SAMPLES) break;
      trimmed.push(c.length + total > MAX_CLONE_SAMPLES
        ? c.subarray(0, MAX_CLONE_SAMPLES - total) : c);
      total += c.length;
    }
    opts.onDone({ b64: encodeWavB64(trimmed), seconds: total / 16000 });
  };
  const cap = setTimeout(finish, CLONE_SECONDS * 1000);
  return finish;
}
