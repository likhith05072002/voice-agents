import { useNavigate } from "react-router-dom";
import { C, serif } from "../theme";
import { useIsMobile } from "../useIsMobile";
import { useAuth } from "../auth";

export function Nav() {
  const nav = useNavigate();
  const mob = useIsMobile();
  const { user, enabled, loading } = useAuth();
  return (
    <div style={{ position: "sticky", top: 0, zIndex: 50, backdropFilter: "blur(10px)",
      background: "rgba(250,247,240,.82)", borderBottom: `1px solid ${C.line}` }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: mob ? "0 16px" : "0 28px",
        height: mob ? 58 : 66, display: "flex", alignItems: "center", gap: mob ? 14 : 28 }}>
        <div onClick={() => nav("/")} style={{ display: "flex", alignItems: "center", gap: 10,
          cursor: "pointer", marginRight: "auto" }}>
          <div style={{ width: 30, height: 30, borderRadius: 9, background: C.ink, display: "flex",
            alignItems: "center", justifyContent: "center" }}>
            <div style={{ width: 11, height: 11, borderRadius: "50%", background: C.accent,
              boxShadow: "0 0 0 3px rgba(224,138,30,.28)" }} />
          </div>
          <span style={{ fontFamily: serif, fontSize: mob ? 20 : 23, letterSpacing: ".2px" }}>SonusLabs</span>
        </div>
        {!mob && <>
          <span onClick={() => nav("/")} style={link}>Product</span>
          <a href="#pricing" style={{ ...link, textDecoration: "none" }}>Pricing</a>
          <span onClick={() => nav("/docs")} style={link}>Docs</span>
          <span onClick={() => nav("/console")} style={link}>Console</span>
          {enabled && !loading && !user &&
            <span onClick={() => nav("/login")} style={link}>Sign in</span>}
        </>}
        {user && (
          <div onClick={() => nav("/console")} title={user.email}
            style={{ width: 30, height: 30, borderRadius: "50%", overflow: "hidden",
              background: C.accentSoft, border: `1px solid ${C.accentSoftBorder}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              cursor: "pointer", flexShrink: 0 }}>
            {user.picture
              ? <img src={user.picture} alt="" referrerPolicy="no-referrer"
                  style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              : <span style={{ fontSize: 13, fontWeight: 700, color: C.accentDeep }}>
                  {(user.name || user.email)[0]?.toUpperCase()}
                </span>}
          </div>
        )}
        <button onClick={() => nav(mob ? "/console" : "/create")} style={{ fontSize: mob ? 13 : 14,
          fontWeight: 600, color: "#fff", background: C.ink, border: "none", borderRadius: 11,
          padding: mob ? "8px 13px" : "10px 17px", cursor: "pointer", whiteSpace: "nowrap" }}>
          {mob ? "Console" : "Create your receptionist"}</button>
      </div>
    </div>
  );
}
const link: React.CSSProperties = { fontSize: 14.5, fontWeight: 500, color: C.muted, cursor: "pointer" };
