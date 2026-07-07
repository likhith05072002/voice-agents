// Auth context: bootstrapped once from GET /auth/me (the HttpOnly session
// cookie rides on credentials:"include" — no token ever touches JS). Also owns
// the ACTIVE WORKSPACE selection, persisted per-browser and pushed into the
// api layer so every console request carries X-Workspace-Id.
import React, {
  createContext, useCallback, useContext, useEffect, useState,
} from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, AuthUser, Workspace, setWorkspaceHeader } from "./api";
import { C } from "./theme";

const WS_KEY = "sl_ws";

interface AuthState {
  loading: boolean;
  enabled: boolean;            // accounts mode off (legacy server) -> no gating
  user: AuthUser | null;
  isAdmin: boolean;            // platform operator (ADMIN_EMAILS)
  telephony: boolean;          // real phone calling wired (else "coming soon")
  workspaces: Workspace[];
  wsId: string;
  setWsId: (id: string) => void;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

function pickWs(workspaces: Workspace[]): string {
  const saved = localStorage.getItem(WS_KEY) || "";
  if (saved && workspaces.some((w) => w.id === saved)) return saved;
  return workspaces[0]?.id || "";
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [telephony, setTelephony] = useState(false);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [wsId, setWsIdState] = useState("");

  const apply = useCallback((me: { enabled: boolean; user: AuthUser | null; workspaces: Workspace[]; is_admin?: boolean; telephony?: boolean }) => {
    setEnabled(me.enabled);
    setUser(me.user);
    setIsAdmin(!!me.is_admin);
    setTelephony(!!me.telephony);
    setWorkspaces(me.workspaces);
    const ws = me.user ? pickWs(me.workspaces) : "";
    setWorkspaceHeader(ws);          // BEFORE children render and fetch
    setWsIdState(ws);
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await api.authMe());
    } catch {
      // Backend unreachable: fail open to the login screen, not a crash.
      apply({ enabled: true, user: null, workspaces: [] });
    }
  }, [apply]);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const setWsId = useCallback((id: string) => {
    localStorage.setItem(WS_KEY, id);
    setWorkspaceHeader(id);
    setWsIdState(id);
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* cookie cleared server-side anyway */ }
    setWorkspaceHeader("");
    localStorage.removeItem(WS_KEY);
    setUser(null);
    setIsAdmin(false);
    setWorkspaces([]);
    setWsIdState("");
  }, []);

  return (
    <Ctx.Provider value={{ loading, enabled, user, isAdmin, telephony, workspaces, wsId, setWsId, refresh, logout }}>
      {children}
    </Ctx.Provider>
  );
}

/** Gate for /create and /console: spinner while the session bootstraps, then
 * bounce logged-out users to /login preserving the intended destination. */
export function RequireAuth({ children }: { children: React.ReactElement }) {
  const { loading, enabled, user } = useAuth();
  const loc = useLocation();
  if (loading) {
    return (
      <div style={{ minHeight: "100vh", background: C.paper, display: "flex",
        alignItems: "center", justifyContent: "center" }}>
        <div style={{ width: 26, height: 26, border: `3px solid ${C.line}`,
          borderTopColor: C.accent, borderRadius: "50%",
          animation: "sl-spin .7s linear infinite" }} />
      </div>
    );
  }
  if (enabled && !user) {
    const next = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return children;
}
