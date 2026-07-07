// Sign-in: one card, one button. The Google button is a full-page navigation
// (NOT fetch) — the OAuth consent redirect chain has to own the tab. After the
// callback the backend 303s back to ?next.
import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { C, serif } from "../theme";

function GoogleG() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    </svg>
  );
}

export function Login() {
  const nav = useNavigate();
  const loc = useLocation();
  const { user, loading } = useAuth();
  const next = new URLSearchParams(loc.search).get("next") || "/console";

  // Already signed in (e.g. back-button onto /login): straight through.
  useEffect(() => {
    if (!loading && user) nav(next, { replace: true });
  }, [loading, user, next, nav]);

  return (
    <div style={{ minHeight: "100vh", background: C.paper, display: "flex",
      flexDirection: "column", alignItems: "center", justifyContent: "center",
      padding: "24px 16px" }}>
      {/* logo + wordmark — identical to the site header */}
      <div onClick={() => nav("/")} style={{ display: "flex", alignItems: "center",
        gap: 11, cursor: "pointer", marginBottom: 30 }}>
        <div style={{ width: 34, height: 34, borderRadius: 10, background: C.ink,
          display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ width: 12, height: 12, borderRadius: "50%", background: C.accent,
            boxShadow: "0 0 0 3px rgba(224,138,30,.28)" }} />
        </div>
        <span style={{ fontFamily: serif, fontSize: 27, letterSpacing: ".2px" }}>SonusLabs</span>
      </div>

      <div style={{ width: "100%", maxWidth: 400, background: C.paperCard,
        border: `1px solid ${C.line}`, borderRadius: 20, padding: "34px 30px 30px",
        boxShadow: "0 24px 60px -36px rgba(33,28,21,.35)",
        animation: "sl-fadeup .4s ease both", textAlign: "center" }}>
        <div style={{ fontFamily: serif, fontSize: 27, lineHeight: 1.15 }}>
          Welcome back
        </div>
        <div style={{ fontSize: 14.5, color: C.muted, marginTop: 9, lineHeight: 1.5 }}>
          Sign in to build and manage your AI receptionists.
        </div>

        <button
          onClick={() => {
            // Full origin so the post-login redirect returns to THIS app
            // (dev server :5173 in dev, sonuslabs.online in prod) — the
            // session cookie is host-scoped so it's shared across ports.
            window.location.href = api.googleLoginUrl(`${window.location.origin}${next}`);
          }}
          style={{ marginTop: 24, width: "100%", display: "flex", alignItems: "center",
            justifyContent: "center", gap: 11, background: "#fff",
            border: `1px solid ${C.lineSoft}`, borderRadius: 12, padding: "13px 16px",
            fontSize: 15, fontWeight: 600, color: C.ink, cursor: "pointer" }}>
          <GoogleG /> Continue with Google
        </button>

        <div style={{ marginTop: 18, fontSize: 12.5, color: C.faint, lineHeight: 1.55 }}>
          New here? Signing in creates your account and your first
          workspace automatically.
        </div>
      </div>

      <div style={{ marginTop: 26, fontSize: 13, color: C.faint }}>
        Just exploring?{" "}
        <span onClick={() => nav("/")} style={{ color: C.accentDeep, fontWeight: 600,
          cursor: "pointer" }}>
          Try the live demo on the homepage →
        </span>
      </div>
      <div style={{ marginTop: 14, fontSize: 12, color: C.faint }}>
        By continuing you agree to our{" "}
        <span onClick={() => nav("/terms")} style={{ color: C.muted, cursor: "pointer",
          textDecoration: "underline" }}>Terms</span> and{" "}
        <span onClick={() => nav("/privacy")} style={{ color: C.muted, cursor: "pointer",
          textDecoration: "underline" }}>Privacy Policy</span>.
      </div>
    </div>
  );
}
