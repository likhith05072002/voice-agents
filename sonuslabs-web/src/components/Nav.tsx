import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { C, serif } from "../theme";
import { useIsMobile } from "../useIsMobile";
import { useAuth } from "../auth";

export function Nav() {
  const nav = useNavigate();
  const mob = useIsMobile();
  const { user, enabled, loading } = useAuth();
  const [menu, setMenu] = useState(false);
  const go = (p: string) => { setMenu(false); nav(p); };

  const Avatar = () => user ? (
    <div onClick={() => go("/console")} title={user.email}
      style={{ width: 30, height: 30, borderRadius: "50%", overflow: "hidden",
        background: C.accentSoft, border: `1px solid ${C.accentSoftBorder}`,
        display: "flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer", flexShrink: 0 }}>
      {user.picture
        ? <img src={user.picture} alt="" referrerPolicy="no-referrer"
            style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        : <span style={{ fontSize: 13, fontWeight: 700, color: C.accentDeep }}>
            {(user.name || user.email)[0]?.toUpperCase()}</span>}
    </div>
  ) : null;

  return (
    <div style={{ position: "sticky", top: 0, zIndex: 50, backdropFilter: "blur(10px)",
      background: "rgba(250,247,240,.9)", borderBottom: `1px solid ${C.line}` }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: mob ? "0 16px" : "0 28px",
        height: mob ? 58 : 66, display: "flex", alignItems: "center", gap: mob ? 12 : 28 }}>
        <div onClick={() => go("/")} style={{ display: "flex", alignItems: "center", gap: 10,
          cursor: "pointer", marginRight: "auto" }}>
          <div style={{ width: 30, height: 30, borderRadius: 9, background: C.ink, display: "flex",
            alignItems: "center", justifyContent: "center" }}>
            <div style={{ width: 11, height: 11, borderRadius: "50%", background: C.accent,
              boxShadow: "0 0 0 3px rgba(224,138,30,.28)" }} />
          </div>
          <span style={{ fontFamily: serif, fontSize: mob ? 20 : 23, letterSpacing: ".2px" }}>SonusLabs</span>
        </div>

        {/* ── desktop ── */}
        {!mob && <>
          <span onClick={() => go("/")} style={link}>Product</span>
          <a href="#pricing" style={{ ...link, textDecoration: "none" }}>Pricing</a>
          <span onClick={() => go("/docs")} style={link}>Docs</span>
          <span onClick={() => go("/console")} style={link}>Console</span>
          {enabled && !loading && !user &&
            <span onClick={() => go("/login")} style={link}>Sign in</span>}
          <Avatar />
          <button onClick={() => go("/create")} style={cta(false)}>Create your receptionist</button>
        </>}

        {/* ── mobile: avatar + hamburger ── */}
        {mob && <>
          <Avatar />
          <button aria-label="Menu" onClick={() => setMenu((m) => !m)}
            style={{ background: "transparent", border: "none", cursor: "pointer", padding: 8,
              display: "flex", flexDirection: "column", gap: 5 }}>
            {[0, 1, 2].map((i) => (
              <span key={i} style={{ width: 22, height: 2.5, borderRadius: 2, background: C.ink,
                transition: "transform .2s, opacity .2s",
                transform: menu && i === 0 ? "translateY(7.5px) rotate(45deg)"
                  : menu && i === 2 ? "translateY(-7.5px) rotate(-45deg)" : "none",
                opacity: menu && i === 1 ? 0 : 1 }} />
            ))}
          </button>
        </>}
      </div>

      {/* ── mobile dropdown ── */}
      {mob && menu && (
        <div style={{ borderTop: `1px solid ${C.line}`, background: C.paper,
          padding: "8px 12px 16px", display: "flex", flexDirection: "column", gap: 2,
          boxShadow: "0 20px 30px -18px rgba(33,28,21,.3)" }}>
          <span onClick={() => go("/")} style={mlink}>Product</span>
          <a href="#pricing" onClick={() => setMenu(false)} style={{ ...mlink, textDecoration: "none" }}>Pricing</a>
          <span onClick={() => go("/docs")} style={mlink}>Docs</span>
          <span onClick={() => go("/console")} style={mlink}>Console</span>
          {enabled && !loading && !user &&
            <span onClick={() => go("/login")} style={mlink}>Sign in</span>}
          <button onClick={() => go("/create")} style={{ ...cta(true), marginTop: 8 }}>
            Create your receptionist</button>
        </div>
      )}
    </div>
  );
}

const link: React.CSSProperties = { fontSize: 14.5, fontWeight: 500, color: C.muted, cursor: "pointer" };
const mlink: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: C.ink, cursor: "pointer",
  padding: "12px 8px", borderRadius: 10 };
const cta = (full: boolean): React.CSSProperties => ({
  fontSize: full ? 15 : 14, fontWeight: 600, color: "#fff", background: C.ink, border: "none",
  borderRadius: 11, padding: full ? "13px" : "10px 17px", cursor: "pointer", whiteSpace: "nowrap",
  width: full ? "100%" : "auto", textAlign: "center" });
