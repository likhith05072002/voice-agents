import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Nav } from "../components/Nav";
import { CallPanel } from "../components/CallPanel";
import { api } from "../api";
import { C, serif, LANGS } from "../theme";

const TAGLINES = [
  { text: "आपकी आवाज़, हमारा एआई।", font: "'Noto Serif Devanagari',serif" },
  { text: "ನಿಮ್ಮ ಧ್ವನಿ, ನಮ್ಮ ಎಐ.", font: "'Noto Sans Kannada',sans-serif" },
  { text: "Your voice, our AI.", font: serif },
  { text: "உங்கள் குரல், எங்கள் ஏஐ.", font: "'Noto Sans Tamil',sans-serif" },
  { text: "మీ స్వరం, మా ఏఐ.", font: "'Noto Sans Telugu',sans-serif" },
];

export function Landing() {
  const nav = useNavigate();
  const [teaser, setTeaser] = useState("");
  const [tag, setTag] = useState(0);
  const [tagVis, setTagVis] = useState(true);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const t = setInterval(() => {
      setTagVis(false);
      setTimeout(() => { setTag((i) => (i + 1) % TAGLINES.length); setTagVis(true); }, 320);
    }, 2600);
    return () => clearInterval(t);
  }, []);

  // The hero always demos the SonusLabs assistant itself — a general,
  // ask-anything AI with live web search (a fixed, known product agent).
  const demo = "sonuslabs";
  const [playingLang, setPlayingLang] = useState<string | null>(null);
  const playLanguage = (lang: string) => {
    if (!audioRef.current) audioRef.current = new Audio();
    setPlayingLang(lang);
    audioRef.current.src = api.languageSampleUrl(lang);
    audioRef.current.onended = () => setPlayingLang(null);
    audioRef.current.play().catch(() => setPlayingLang(null));
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: C.paper,
      backgroundImage: "radial-gradient(#E7DFCF 1.1px,transparent 1.1px)", backgroundSize: "24px 24px" }}>
      <Nav />

      {/* HERO */}
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "56px 28px 30px",
        display: "grid", gridTemplateColumns: "1.05fr .95fr", gap: 52, alignItems: "center" }}>
        <div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, background: C.accentSoft,
            border: `1px solid ${C.accentSoftBorder}`, borderRadius: 100, padding: "6px 13px",
            fontSize: 12.5, fontWeight: 600, color: C.accentDeep, marginBottom: 22 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.green,
              animation: "sl-livedot 1.6s infinite" }} />
            Built in India · speaks 11 Indian languages
          </div>
          <h1 style={{ fontFamily: serif, fontWeight: 400, fontSize: 60, lineHeight: 1.02,
            letterSpacing: "-.5px", marginBottom: 6 }}>The receptionist who never sleeps.</h1>
          <div style={{ height: 40, marginBottom: 20, display: "flex", alignItems: "center", overflow: "hidden" }}>
            <span style={{ fontFamily: TAGLINES[tag].font, fontSize: 26, color: C.accent, fontWeight: 600,
              lineHeight: 1.35, whiteSpace: "nowrap", opacity: tagVis ? 1 : 0,
              transform: tagVis ? "translateY(0)" : "translateY(8px)", transition: "opacity .32s, transform .32s" }}>
              {TAGLINES[tag].text}</span>
          </div>
          <p style={{ fontSize: 17.5, lineHeight: 1.55, color: C.inkSoft, maxWidth: 460, marginBottom: 28 }}>
            A human-sounding AI that answers your phone for any business — shop, clinic, cafe, office —
            in English, Hindi, Kannada, Telugu, Tamil and more, switching language the moment your caller does.
            Books appointments, quotes live prices, handles interruptions like a person.
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <button onClick={() => nav("/create")} style={{ fontSize: 15.5, fontWeight: 600, color: "#fff",
              background: C.accent, border: "none", borderRadius: 13, padding: "15px 24px", cursor: "pointer",
              boxShadow: "0 8px 22px -8px rgba(224,138,30,.6)" }}>Create your receptionist →</button>
            <a href="#pricing" style={{ fontSize: 15.5, fontWeight: 600, color: C.ink, background: "#fff",
              border: `1px solid ${C.lineSoft}`, borderRadius: 13, padding: "15px 22px", cursor: "pointer",
              textDecoration: "none" }}>See pricing</a>
          </div>
          <div style={{ marginTop: 20, fontSize: 13.5, color: C.faint }}>
            No app to install · from ₹3/min all-in · answers on a real phone number or your website
          </div>
        </div>

        {/* LIVE demo call */}
        <CallPanel agentId={demo} subtitle="live demo · talk to it" voicePicker />
      </div>

      {/* TRUST — business categories, not named companies (brand-neutral) */}
      <div style={{ maxWidth: 1200, margin: "26px auto 0", padding: "0 28px" }}>
        <div style={{ fontSize: 12, letterSpacing: ".14em", textTransform: "uppercase", color: "#A79E8B",
          textAlign: "center", marginBottom: 16 }}>Built for every kind of business</div>
        <div style={{ display: "flex", gap: 34, justifyContent: "center", flexWrap: "wrap",
          fontFamily: serif, fontSize: 21, color: "#B9B0A0" }}>
          {["Jewellers", "Clinics", "Cafés & restaurants", "Real estate", "Salons", "Retail"]
            .map((c) => <span key={c}>{c}</span>)}
        </div>
      </div>

      {/* ONBOARDING TEASER */}
      <div style={{ maxWidth: 1200, margin: "78px auto 0", padding: "0 28px" }}>
        <div style={{ background: C.ink, borderRadius: 28, padding: "48px 44px", color: "#F3EEE3",
          position: "relative", overflow: "hidden" }}>
          <div style={{ position: "absolute", right: -40, top: -40, width: 260, height: 260, borderRadius: "50%",
            background: "radial-gradient(circle,rgba(224,138,30,.45),transparent 70%)" }} />
          <div style={{ position: "relative", maxWidth: 640 }}>
            <div style={{ fontSize: 13, letterSpacing: ".14em", textTransform: "uppercase", color: C.accent,
              fontWeight: 600, marginBottom: 14 }}>The two-minute setup</div>
            <h2 style={{ fontFamily: serif, fontWeight: 400, fontSize: 40, lineHeight: 1.05, marginBottom: 14 }}>
              Paste your website. Meet your receptionist.</h2>
            <p style={{ fontSize: 16.5, lineHeight: 1.55, color: "#C9C2B4", marginBottom: 24 }}>
              SonusLabs reads your site, learns your business, and writes your agent's persona and knowledge
              automatically. You're talking to your own receptionist before your chai gets cold.</p>
            <div style={{ display: "flex", gap: 10, background: "#2C261D", border: "1px solid #3A332765",
              borderRadius: 14, padding: 8, maxWidth: 520 }}>
              <input value={teaser} onChange={(e) => setTeaser(e.target.value)} placeholder="https://yourbusiness.in"
                style={{ flex: 1, background: "transparent", border: "none", outline: "none", color: "#fff",
                  fontSize: 15, padding: "8px 12px" }} />
              <button onClick={() => nav("/create", { state: { url: teaser } })}
                style={{ fontSize: 14.5, fontWeight: 600, color: C.ink, background: C.accent, border: "none",
                  borderRadius: 10, padding: "11px 20px", cursor: "pointer", whiteSpace: "nowrap" }}>
                Research my business →</button>
            </div>
          </div>
        </div>
      </div>

      {/* LANGUAGES */}
      <div style={{ maxWidth: 1200, margin: "78px auto 0", padding: "0 28px" }}>
        <div style={{ textAlign: "center", marginBottom: 30 }}>
          <h2 style={{ fontFamily: serif, fontWeight: 400, fontSize: 38, marginBottom: 8 }}>
            One receptionist. Eleven languages.</h2>
          <p style={{ fontSize: 16, color: C.muted }}>
            And she switches mid-sentence when your caller does. Tap any — she introduces herself in it.</p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "center" }}>
          {LANGS.map((l) => {
            const on = playingLang === l.v;
            return (
              <div key={l.v} onClick={() => playLanguage(l.v)} style={{
                background: on ? C.accentSoft : C.paperCard,
                border: `1px solid ${on ? C.accentSoftBorder : C.line}`, borderRadius: 14,
                padding: "13px 18px", minWidth: 150, cursor: "pointer",
                boxShadow: on ? "0 10px 26px -14px rgba(224,138,30,.5)" : "0 10px 24px -18px rgba(33,28,21,.4)",
                textAlign: "center", transition: "background .2s, box-shadow .2s" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                  fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase",
                  color: on ? C.accentDeep : C.faint, marginBottom: 6 }}>
                  {on && <span style={{ display: "flex", gap: 1.5, height: 9 }}>
                    {[0, .15, .3].map((d) => <span key={d} style={{ width: 2, background: C.accent,
                      borderRadius: 2, animation: `sl-eq .6s infinite ${d}s` }} />)}</span>}
                  {l.en}
                </div>
                <div style={{ fontFamily: l.font, fontSize: 20, color: C.ink }}>{l.native}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* HOW IT WORKS */}
      <div style={{ maxWidth: 1200, margin: "82px auto 0", padding: "0 28px" }}>
        <h2 style={{ fontFamily: serif, fontWeight: 400, fontSize: 38, textAlign: "center", marginBottom: 34 }}>
          How it works</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 20 }}>
          {[
            { n: "1", title: "Paste your website", body: "We research your business and draft your agent's persona, greeting and knowledge — automatically." },
            { n: "2", title: "Make her yours", body: "Edit anything, pick a voice and language, then create her with one click." },
            { n: "3", title: "Put her on the phone", body: "Talk to her on your website instantly, or get a real phone number that answers 24/7." },
          ].map((s) => (
            <div key={s.n} style={{ background: C.paperCard, border: `1px solid ${C.line}`, borderRadius: 20,
              padding: "28px 24px" }}>
              <div style={{ width: 42, height: 42, borderRadius: 12, background: C.accentSoft, color: C.accentDeep,
                display: "flex", alignItems: "center", justifyContent: "center", fontFamily: serif, fontSize: 22,
                marginBottom: 16 }}>{s.n}</div>
              <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 7 }}>{s.title}</div>
              <div style={{ fontSize: 14.5, lineHeight: 1.5, color: C.muted }}>{s.body}</div>
            </div>
          ))}
        </div>
      </div>

      {/* LIVE DATA */}
      <div style={{ maxWidth: 1200, margin: "70px auto 0", padding: "0 28px" }}>
        <div style={{ background: "linear-gradient(100deg,#FBEBD2,#FDF6E7)", border: `1px solid ${C.accentSoftBorder}`,
          borderRadius: 22, padding: "34px 36px", display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 26 }}>
          {[
            { big: "Live rates", t: "Quotes today's real prices", s: "Gold, silver, anything — live market data, not a stale number." },
            { big: "Today, IST", t: "Knows the date & time", s: "\"Come tomorrow at 4\" means the right day." },
            { big: "Live web", t: "Searches for current facts", s: "Answers things it wasn't told in advance." },
          ].map((d, i) => (
            <div key={i}>
              <div style={{ fontFamily: serif, fontSize: 30, color: C.accentDeep, marginBottom: 6 }}>{d.big}</div>
              <div style={{ fontSize: 14.5, fontWeight: 700, marginBottom: 3 }}>{d.t}</div>
              <div style={{ fontSize: 13, color: "#8B7A55" }}>{d.s}</div>
            </div>
          ))}
        </div>
      </div>

      {/* INTERRUPTION */}
      <div style={{ maxWidth: 1200, margin: "74px auto 0", padding: "0 28px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 44, alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 13, letterSpacing: ".14em", textTransform: "uppercase", color: C.accent,
              fontWeight: 600, marginBottom: 12 }}>Barge-in</div>
            <h2 style={{ fontFamily: serif, fontWeight: 400, fontSize: 38, lineHeight: 1.05, marginBottom: 14 }}>
              Talk over it. It stops. Like a person.</h2>
            <p style={{ fontSize: 16.5, lineHeight: 1.55, color: C.inkSoft }}>
              Interrupt mid-sentence and she goes quiet instantly — never talks over your caller, never loses
              her place. The awkward robot delay is gone.</p>
          </div>
          <div style={{ background: C.ink, borderRadius: 20, padding: 26, color: "#EDE7DB" }}>
            <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
              <div style={{ background: "#3A3327", borderRadius: "12px 12px 12px 4px", padding: "9px 13px",
                fontSize: 13.5, maxWidth: "80%" }}>…so for that we usually recommend the premium option, which inclu—</div>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginBottom: 6 }}>
              <div style={{ background: C.accent, color: C.ink, borderRadius: "12px 12px 4px 12px",
                padding: "9px 13px", fontSize: 13.5, fontWeight: 600 }}>Wait, what's the cheapest?</div>
            </div>
            <div style={{ textAlign: "center", fontFamily: "'JetBrains Mono',monospace", fontSize: 11,
              color: C.faint, margin: "10px 0 8px" }}>— stopped instantly —</div>
            <div style={{ display: "flex", gap: 8 }}>
              <div style={{ background: "#3A3327", borderRadius: "12px 12px 12px 4px", padding: "9px 13px",
                fontSize: 13.5, maxWidth: "80%" }}>Of course — the basic plan starts at ₹499. Want the details?</div>
            </div>
          </div>
        </div>
      </div>

      {/* PRICING */}
      <div id="pricing" style={{ maxWidth: 1200, margin: "84px auto 0", padding: "0 28px", scrollMarginTop: 80 }}>
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <h2 style={{ fontFamily: serif, fontWeight: 400, fontSize: 40, marginBottom: 8 }}>
            Honest, per-minute pricing.</h2>
          <p style={{ fontSize: 16, color: C.muted }}>
            Indian prices for Indian businesses. <span style={{ color: C.accentDeep, fontWeight: 600 }}>
            You only pay for minutes you actually use.</span></p>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 20 }}>
          {PLANS.map((p) => (
            <div key={p.name} style={{ background: p.featured ? C.ink : C.paperCard,
              border: `1.5px solid ${p.featured ? C.ink : C.line}`, borderRadius: 22, padding: "30px 26px",
              color: p.featured ? "#F3EEE3" : C.ink, position: "relative" }}>
              {p.featured && <div style={{ position: "absolute", top: 16, right: 16, background: C.accent,
                color: C.ink, fontSize: 11, fontWeight: 700, padding: "4px 10px", borderRadius: 100 }}>Most popular</div>}
              <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase",
                color: p.featured ? "#B9B0A0" : C.faint, marginBottom: 14 }}>{p.name}</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 6 }}>
                <span style={{ fontFamily: serif, fontSize: 42 }}>{p.price}</span>
                <span style={{ fontSize: 14, color: p.featured ? "#B9B0A0" : C.faint }}>{p.unit}</span>
              </div>
              <div style={{ fontSize: 14, color: p.featured ? "#C9C2B4" : C.muted, marginBottom: 20, minHeight: 20 }}>{p.tag}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 24 }}>
                {p.features.map((f, i) => (
                  <div key={i} style={{ display: "flex", gap: 9, alignItems: "flex-start", fontSize: 14 }}>
                    <span style={{ color: C.accent, fontWeight: 700 }}>✓</span>
                    <span>{f}</span></div>
                ))}
              </div>
              <button onClick={() => nav("/create")} style={{ width: "100%", fontSize: 14.5, fontWeight: 600,
                color: p.featured ? C.ink : "#fff", background: p.featured ? C.accent : C.ink, border: "none",
                borderRadius: 12, padding: 13, cursor: "pointer" }}>{p.cta}</button>
            </div>
          ))}
        </div>
        <p style={{ textAlign: "center", fontSize: 13, color: C.faint, marginTop: 18 }}>
          All-in price includes speech, AI, and telephony. Web-widget calls are ₹2.50/min. Launch pricing — indicative.
        </p>
      </div>

      {/* CTA */}
      <div style={{ maxWidth: 1200, margin: "84px auto 0", padding: "0 28px 20px" }}>
        <div style={{ textAlign: "center", padding: "40px 20px" }}>
          <h2 style={{ fontFamily: serif, fontWeight: 400, fontSize: 44, lineHeight: 1.05, marginBottom: 8 }}>
            Meet your receptionist.</h2>
          <div style={{ fontFamily: "'Noto Serif Devanagari',serif", fontSize: 20, color: C.accent, marginBottom: 22 }}>
            ग्यारह भाषाएँ · एक आवाज़</div>
          <button onClick={() => nav("/create")} style={{ fontSize: 16, fontWeight: 600, color: "#fff",
            background: C.accent, border: "none", borderRadius: 14, padding: "16px 30px", cursor: "pointer",
            boxShadow: "0 12px 26px -10px rgba(224,138,30,.6)" }}>Create yours in two minutes →</button>
        </div>
      </div>
      <div style={{ borderTop: `1px solid ${C.line}`, marginTop: 30 }}>
        <div style={{ maxWidth: 1200, margin: "0 auto", padding: "26px 28px", display: "flex",
          justifyContent: "space-between", flexWrap: "wrap", gap: 16, color: C.faint, fontSize: 13 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span style={{ fontFamily: serif, fontSize: 18, color: C.ink }}>SonusLabs</span>
            <span>· The receptionist who never sleeps.</span></div>
          <div style={{ display: "flex", gap: 20 }}><span>Made in India 🇮🇳</span><span>hello@sonuslabs.ai</span></div>
        </div>
      </div>
    </div>
  );
}

const PLANS = [
  { name: "Starter", price: "₹3", unit: "/min", tag: "Pay as you go. No monthly fee.", featured: false,
    cta: "Start free", features: ["All 11 Indian languages", "Web + phone calls", "Live prices, date & web search",
      "Interruption handling", "Call transcripts & recordings"] },
  { name: "Business", price: "₹2,999", unit: "/mo", tag: "Includes 1,200 minutes, then ₹2.50/min.", featured: true,
    cta: "Choose Business", features: ["Everything in Starter", "Your own phone number", "Priority voices",
      "Analytics dashboard", "Appointment booking flows"] },
  { name: "Enterprise", price: "Custom", unit: "", tag: "For high volume & multi-location.", featured: false,
    cta: "Talk to us", features: ["Volume rates below ₹2.50/min", "Dedicated hosting & SLA", "On-prem option",
      "CRM / booking integrations", "Priority support"] },
];
