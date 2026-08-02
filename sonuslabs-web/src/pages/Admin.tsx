// Platform operations monitor — /admin. Only accounts whose email is in
// ADMIN_EMAILS see anything (backend 404s everyone else; this page also
// gates client-side). Cross-tenant + money views, auto-refreshing.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api, AdminCall, AdminLedgerRow, AdminOverview, AdminUser, LiveLine,
} from "../api";
import { useAuth } from "../auth";
import { C, serif, mono } from "../theme";
import { useIsMobile } from "../useIsMobile";

type Tab = "overview" | "users" | "calls" | "money" | "live";
const TABS: [Tab, string][] = [["overview", "Overview"], ["users", "Users"],
  ["calls", "Calls"], ["money", "Money"], ["live", "Live"]];

const rupees = (paise: number) => `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const when = (t?: number | null) => t ? new Date(t * 1000).toLocaleString() : "—";
const ago = (t?: number | null) => {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export function Admin() {
  const nav = useNavigate();
  const mob = useIsMobile();
  const { loading, user, isAdmin } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");

  if (loading) return <div style={{ minHeight: "100vh", background: C.dark }} />;
  if (!user || !isAdmin) {
    return (
      <div style={{ minHeight: "100vh", background: C.dark, color: C.darkText,
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", gap: 14 }}>
        <div style={{ fontFamily: serif, fontSize: 26 }}>Nothing here.</div>
        <div onClick={() => nav("/")} style={{ fontSize: 13.5, color: C.darkMuted,
          cursor: "pointer" }}>← back home</div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: C.dark, color: C.darkText }}>
      <div style={{ position: "sticky", top: 0, zIndex: 20, background: C.dark,
        borderBottom: `1px solid ${C.darkLine}` }}>
        <div style={{ maxWidth: 1240, margin: "0 auto", padding: mob ? "12px 16px" : "14px 26px",
          display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <div onClick={() => nav("/")} style={{ display: "flex", alignItems: "center",
            gap: 9, cursor: "pointer" }}>
            <div style={{ width: 24, height: 24, borderRadius: 7, background: C.red }} />
            <span style={{ fontFamily: serif, fontSize: 18 }}>SonusLabs</span>
            <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: ".1em",
              textTransform: "uppercase", color: C.red, border: `1px solid #5A3326`,
              borderRadius: 6, padding: "2px 7px" }}>Admin</span>
          </div>
          <div style={{ display: "flex", gap: 4, overflowX: "auto", marginLeft: mob ? 0 : 14 }}>
            {TABS.map(([k, label]) => (
              <div key={k} onClick={() => setTab(k)}
                style={{ padding: "8px 13px", borderRadius: 9, cursor: "pointer",
                  fontSize: 13.5, fontWeight: 600, whiteSpace: "nowrap",
                  background: tab === k ? C.darkCard : "transparent",
                  color: tab === k ? C.darkText : C.darkMuted }}>{label}</div>
            ))}
          </div>
          <div style={{ marginLeft: "auto", fontSize: 12, color: C.darkMuted }}>
            {user.email}
            <span onClick={() => nav("/console")} style={{ marginLeft: 14, color: C.accent,
              fontWeight: 700, cursor: "pointer" }}>Console →</span>
          </div>
        </div>
      </div>

      <div style={{ maxWidth: 1240, margin: "0 auto", padding: mob ? "18px 16px 50px" : "24px 26px 70px" }}>
        {tab === "overview" && <OverviewTab />}
        {tab === "users" && <UsersTab />}
        {tab === "calls" && <CallsTab />}
        {tab === "money" && <MoneyTab />}
        {tab === "live" && <LiveTab />}
      </div>
    </div>
  );
}

/* ─── shared bits ─── */
const card: React.CSSProperties = { background: C.darkCard, border: `1px solid ${C.darkLine}`,
  borderRadius: 14, padding: "16px 18px" };
const thStyle: React.CSSProperties = { textAlign: "left", padding: "8px 10px", fontSize: 10.5,
  fontWeight: 800, letterSpacing: ".07em", textTransform: "uppercase", color: C.darkMuted,
  borderBottom: `1px solid ${C.darkLine}`, whiteSpace: "nowrap" };
const tdStyle: React.CSSProperties = { padding: "9px 10px", fontSize: 13,
  borderBottom: `1px solid ${C.darkLine}`, verticalAlign: "top" };

function Table({ head, rows, onRowClick }: {
  head: string[]; rows: React.ReactNode[][]; onRowClick?: (i: number) => void;
}) {
  return (
    <div style={{ ...card, padding: 6, overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead><tr>{head.map((h) => <th key={h} style={thStyle}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i} onClick={onRowClick && (() => onRowClick(i))}
              style={onRowClick ? { cursor: "pointer" } : undefined}>
            {r.map((c, j) => <td key={j} style={tdStyle}>{c}</td>)}</tr>))}</tbody>
      </table>
      {rows.length === 0 && <div style={{ padding: 16, color: C.darkMuted, fontSize: 13 }}>Nothing yet.</div>}
    </div>
  );
}

/* ─── overview ─── */
function OverviewTab() {
  const [o, setO] = useState<AdminOverview | null>(null);
  const [active, setActive] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => {
      api.adminOverview().then((r) => { if (alive) setO(r); }).catch(() => {});
      api.adminLive(Date.now() / 1000).then((r) => { if (alive) setActive(r.active); }).catch(() => {});
    };
    load();
    const iv = setInterval(load, 10000);
    return () => { alive = false; clearInterval(iv); };
  }, []);
  if (!o) return <div style={{ color: C.darkMuted }}>Loading…</div>;

  const Stat = ({ label, value, sub, accent }: { label: string; value: string;
    sub?: string; accent?: string }) => (
    <div style={card}>
      <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: ".08em",
        textTransform: "uppercase", color: C.darkMuted, marginBottom: 7 }}>{label}</div>
      <div style={{ fontFamily: serif, fontSize: 29, lineHeight: 1, color: accent || C.darkText }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: C.darkMuted, marginTop: 6 }}>{sub}</div>}
    </div>
  );

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 12 }}>
      <Stat label="Live calls now" value={String(active)}
        accent={active > 0 ? "#7BC89B" : undefined} sub="active sessions" />
      <Stat label="Users" value={String(o.users)} sub={`+${o.new_users_24h} in 24h`} />
      <Stat label="Workspaces" value={String(o.workspaces)} sub={`${o.agents} customer agents`} />
      <Stat label="Calls (24h)" value={String(o.calls_24h)}
        sub={`${o.minutes_24h.toLocaleString()} min · ${o.calls_total} all-time`} />
      <Stat label="Minutes (all-time)" value={o.minutes_total.toLocaleString()}
        sub={`${o.calls_total} calls`} />
      <Stat label="Revenue (paid)" value={rupees(o.revenue_paid_paise)} accent="#7BC89B"
        sub="verified payments" />
      <Stat label="Usage burned" value={rupees(o.usage_burned_paise)}
        sub="credits consumed by calls" />
      <Stat label="Credits outstanding" value={rupees(o.credits_outstanding_paise)}
        sub="our liability — unspent balances" />
      <Stat label="Credits issued" value={rupees(o.credits_issued_paise)}
        sub="top-ups + trials, all-time" />
      <Stat label="Active API keys" value={String(o.api_keys)} />
    </div>
  );
}

/* ─── users ─── */
function UsersTab() {
  const [rows, setRows] = useState<AdminUser[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { api.adminUsers().then((r) => setRows(r.users)).catch(() => {}); }, []);
  const shown = rows.filter((u) => !q ||
    u.email.toLowerCase().includes(q.toLowerCase()) ||
    (u.name || "").toLowerCase().includes(q.toLowerCase()));
  return (<>
    <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search email or name…"
      style={{ width: 280, maxWidth: "100%", background: C.darkCard, color: C.darkText,
        border: `1px solid ${C.darkLine}`, borderRadius: 10, padding: "9px 12px",
        fontSize: 13.5, outline: "none", marginBottom: 12 }} />
    <Table head={["User", "Joined", "Last seen", "Balance", "Spent", "WS", "Agents", "Keys"]}
      rows={shown.map((u) => [
        <div key="u"><div style={{ fontWeight: 600 }}>{u.name || "—"}</div>
          <div style={{ fontSize: 11.5, color: C.darkMuted }}>{u.email}</div></div>,
        <span style={{ color: C.darkMuted }}>{ago(u.created_at)}</span>,
        <span style={{ color: C.darkMuted }}>{ago(u.last_login_at)}</span>,
        <span style={{ fontFamily: mono, color: u.balance_paise > 0 ? "#7BC89B" : C.darkMuted }}>
          {rupees(u.balance_paise)}</span>,
        <span style={{ fontFamily: mono }}>{rupees(u.spent_paise)}</span>,
        String(u.workspaces), String(u.agents), String(u.api_keys),
      ])} />
  </>);
}

/* ─── calls ─── */
// Who was on the other end. We don't store a call DIRECTION, so show BOTH legs
// rather than guess: on an inbound call `from` is the caller, on an outbound
// one `to` is whoever we dialled, and picking one would print our own Telnyx
// number half the time. A browser/widget call has no phone number on either
// leg — the caller's IP is the only handle there is.
function farEnd(c: AdminCall): string {
  const from = (c.from_number || "").trim();
  const to = (c.to_number || "").trim();
  if (to === "web") return from ? `web · ${from}` : "web";
  if (from && to) return `${from} → ${to}`;
  return from || to || "—";
}

function CallsTab() {
  const [rows, setRows] = useState<AdminCall[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.adminCalls().then((r) => { if (alive) setRows(r.calls); }).catch(() => {});
    load();
    const iv = setInterval(load, 15000);
    return () => { alive = false; clearInterval(iv); };
  }, []);
  return (
    <>
      <div style={{ fontSize: 12.5, color: C.darkMuted, marginBottom: 10 }}>
        Click any row to read the full transcript.
      </div>
      <Table head={["When", "Number / source", "Customer", "Agent", "Duration", "Turns", "Outcome"]}
        onRowClick={(i) => setOpen(rows[i].call_id)}
        rows={rows.map((c) => [
          <span style={{ whiteSpace: "nowrap", color: C.darkMuted }}>{when(c.started_at)}</span>,
          <span style={{ fontFamily: mono, fontSize: 12, color: C.accent }}>{farEnd(c)}</span>,
          <span style={{ fontSize: 12 }}>{c.owner_email || <span style={{ color: C.darkMuted }}>platform</span>}</span>,
          <span style={{ fontFamily: mono, fontSize: 12 }}>{c.agent_id}</span>,
          c.duration_s != null ? `${Math.round(c.duration_s)}s` : "—",
          c.turn_count != null ? String(c.turn_count) : "—",
          <span style={{ color: c.outcome === "completed" ? "#7BC89B" : C.darkMuted }}>
            {c.outcome || "—"}</span>,
        ])} />
      {open && <TranscriptModal callId={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function TranscriptModal({ callId, onClose }: { callId: string; onClose: () => void }) {
  const [d, setD] = useState<(AdminCall & { turns: { role: string; text: string }[] }) | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.adminCallDetail(callId).then(setD).catch(() => setErr("Could not load this call."));
  }, [callId]);
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.6)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: C.dark,
        border: `1px solid ${C.darkLine}`, borderRadius: 16, width: "min(760px, 96vw)",
        maxHeight: "86vh", display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "14px 18px", borderBottom: `1px solid ${C.darkLine}` }}>
          <div>
            <div style={{ fontWeight: 700 }}>{d ? farEnd(d) : "Call transcript"}</div>
            <div style={{ fontSize: 12, color: C.darkMuted, fontFamily: mono }}>
              {d ? [d.agent_id, d.workspace_name || "platform", when(d.started_at),
                    d.duration_s != null ? `${Math.round(d.duration_s)}s` : null,
                    d.avg_perceived_ms != null ? `${Math.round(d.avg_perceived_ms)}ms` : null,
                   ].filter(Boolean).join(" · ")
                 : callId}
            </div>
          </div>
          <span onClick={onClose} style={{ cursor: "pointer", color: C.darkMuted, fontSize: 20 }}>✕</span>
        </div>
        <div style={{ overflowY: "auto", padding: "16px 18px", display: "flex",
          flexDirection: "column", gap: 9 }}>
          {err && <div style={{ color: C.red }}>{err}</div>}
          {!d && !err && <div style={{ color: C.darkMuted }}>Loading…</div>}
          {d && d.turns.length === 0 && (
            <div style={{ color: C.darkMuted }}>No transcript recorded for this call.</div>
          )}
          {d?.turns.map((t, i) => (
            <div key={i} style={{ maxWidth: "84%", padding: "9px 13px", borderRadius: 13,
              fontSize: 13.5, lineHeight: 1.45, whiteSpace: "pre-wrap",
              ...(t.role === "user"
                ? { alignSelf: "flex-end", background: C.accent, color: C.ink, fontWeight: 500 }
                : { alignSelf: "flex-start", background: "#1E1A14", border: `1px solid ${C.darkLine}` }) }}>
              {t.text}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─── money ─── */
function MoneyTab() {
  const [rows, setRows] = useState<AdminLedgerRow[]>([]);
  useEffect(() => { api.adminLedger().then((r) => setRows(r.ledger)).catch(() => {}); }, []);
  const KIND: Record<string, [string, string]> = {
    trial_grant: ["Trial", "#7BC89B"], topup: ["Top-up", "#7BC89B"],
    call_usage: ["Usage", C.darkMuted], number_rent: ["Number rent", C.accent],
    adjustment: ["Adjust", C.accent],
  };
  return (
    <Table head={["When", "User", "Type", "Amount", "Balance after", "Ref"]}
      rows={rows.map((l) => {
        const [label, color] = KIND[l.kind] || [l.kind, C.darkMuted];
        return [
          <span style={{ whiteSpace: "nowrap", color: C.darkMuted }}>{when(l.t)}</span>,
          <span style={{ fontSize: 12 }}>{l.email}</span>,
          <span style={{ color, fontWeight: 700, fontSize: 12.5 }}>{label}
            {l.seconds != null && <span style={{ color: C.darkMuted, fontWeight: 400 }}> · {Math.ceil(l.seconds)}s</span>}</span>,
          <span style={{ fontFamily: mono, color: l.delta_paise >= 0 ? "#7BC89B" : C.darkText }}>
            {l.delta_paise >= 0 ? "+" : ""}{rupees(l.delta_paise)}</span>,
          <span style={{ fontFamily: mono, color: C.darkMuted }}>{rupees(l.balance_after)}</span>,
          <span style={{ fontFamily: mono, fontSize: 11, color: C.darkMuted }}>{l.ref}</span>,
        ];
      })} />
  );
}

/* ─── live ─── */
function LiveTab() {
  const [active, setActive] = useState(0);
  const [lines, setLines] = useState<(LiveLine & { agent_id?: string })[]>([]);
  const since = useRef(Date.now() / 1000 - 300);   // start with last 5 min
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await api.adminLive(since.current);
        if (!alive) return;
        setActive(r.active);
        if (r.lines.length) {
          since.current = r.lines[r.lines.length - 1].t;
          setLines((prev) => [...prev, ...r.lines].slice(-200));
        }
      } catch { /* keep polling */ }
    };
    poll();
    const iv = setInterval(poll, 1000);
    return () => { alive = false; clearInterval(iv); };
  }, []);
  return (<>
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
      <span style={{ width: 9, height: 9, borderRadius: "50%",
        background: active > 0 ? "#7BC89B" : C.darkMuted,
        animation: active > 0 ? "sl-livedot 1.4s infinite" : "none" }} />
      <span style={{ fontSize: 14.5, fontWeight: 700 }}>
        {active} live {active === 1 ? "call" : "calls"}</span>
      <span style={{ fontSize: 12, color: C.darkMuted }}>captions across all tenants, ~1s delay</span>
    </div>
    <div style={{ ...card, maxHeight: 520, overflowY: "auto", display: "flex",
      flexDirection: "column", gap: 8 }}>
      {lines.length === 0 && <div style={{ color: C.darkMuted, fontSize: 13 }}>
        Quiet right now — captions appear here the moment any call is live.</div>}
      {lines.map((l, i) => (
        <div key={i} style={{ fontSize: 13, lineHeight: 1.5 }}>
          <span style={{ fontFamily: mono, fontSize: 10.5, color: C.darkMuted }}>
            {new Date(l.t * 1000).toLocaleTimeString()} · {l.agent_id || l.call_id?.slice(0, 8)}
          </span>{" "}
          <span style={{ fontWeight: 700, color: l.role === "assistant" ? C.accent : "#7BC89B" }}>
            {l.role === "assistant" ? "agent" : "caller"}:</span>{" "}
          {l.text}
        </div>
      ))}
    </div>
  </>);
}
