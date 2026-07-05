import { useEffect, useRef } from "react";

// The signature Call Orb: breathes when idle, blooms into a live ring driven
// by `level` (0..1) when speaking. Accent = marigold, dark core.
export function Orb({ size = 210, level = 0, active = false }:
  { size?: number; level?: number; active?: boolean }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const lv = useRef(0);
  const raf = useRef(0);

  useEffect(() => { lv.current += (level - lv.current) * 0.35; }, [level]);

  useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = size * dpr; cv.height = size * dpr;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr);
    let phase = 0;

    const draw = () => {
      const cx = size / 2, cy = size / 2;
      g.clearRect(0, 0, size, size);
      phase += 0.02;
      const breathe = active ? 0 : Math.sin(phase) * 0.02 + 0.02;
      const e = Math.min(1, lv.current * 2.2);
      const base = size * 0.30;

      // outer glow rings pulse to energy
      for (let i = 3; i >= 1; i--) {
        const r = base + i * (size * 0.055) * (1 + e * 0.8) + breathe * size;
        g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2);
        g.fillStyle = `rgba(224,138,30,${(0.05 + e * 0.06) / i})`;
        g.fill();
      }
      // waveform ring when speaking
      if (active) {
        g.beginPath();
        const seg = 72;
        for (let i = 0; i <= seg; i++) {
          const ang = (i / seg) * Math.PI * 2;
          const wob = Math.sin(ang * 6 + phase * 3) * e * size * 0.05;
          const r = base + size * 0.05 + wob;
          const x = cx + Math.cos(ang) * r, y = cy + Math.sin(ang) * r;
          i === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
        }
        g.closePath();
        g.strokeStyle = `rgba(224,138,30,${0.55 + e * 0.4})`;
        g.lineWidth = 2; g.stroke();
      }
      // core
      const cr = base * (1 + breathe + e * 0.10);
      const grad = g.createRadialGradient(cx, cy - cr * 0.3, cr * 0.2, cx, cy, cr);
      grad.addColorStop(0, "#3A3327");
      grad.addColorStop(1, "#211C15");
      g.beginPath(); g.arc(cx, cy, cr, 0, Math.PI * 2); g.fillStyle = grad; g.fill();
      // inner marigold dot
      g.beginPath(); g.arc(cx, cy, cr * 0.34, 0, Math.PI * 2);
      g.fillStyle = "#E08A1E"; g.fill();
      g.beginPath(); g.arc(cx, cy, cr * 0.34 + 3 + e * 4, 0, Math.PI * 2);
      g.fillStyle = `rgba(224,138,30,${0.3 + e * 0.3})`; g.fill();

      raf.current = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf.current);
  }, [size, active]);

  return <canvas ref={ref} style={{ width: size, height: size, display: "block" }} />;
}
