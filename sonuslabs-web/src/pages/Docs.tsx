// Developer portal — public docs for the SonusLabs API (no login needed).
// Everything documented here is the REAL API surface: keep in sync with
// src/main.py + src/accounts/*. Light paper theme to match the landing.
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { C, serif, mono } from "../theme";
import { useIsMobile } from "../useIsMobile";

const ORIGIN = typeof window !== "undefined" ? window.location.origin : "";
const BASE = ORIGIN.includes("localhost") ? "http://localhost:8001" : (ORIGIN || "https://sonuslabs.online");
const WS = BASE.replace(/^http/, "ws");

/* ─── building blocks ─── */

function Code({ code, lang }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div style={{ position: "relative", margin: "10px 0 18px" }}>
      {lang && <div style={{ position: "absolute", top: 9, left: 14, fontSize: 10.5,
        fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase",
        color: "#8A806C" }}>{lang}</div>}
      <button onClick={() => { navigator.clipboard.writeText(code); setCopied(true);
        setTimeout(() => setCopied(false), 1400); }}
        style={{ position: "absolute", top: 7, right: 8, border: "none", borderRadius: 7,
          padding: "4px 10px", fontSize: 11.5, fontWeight: 700, cursor: "pointer",
          background: copied ? "#2E4638" : "#2A251C", color: copied ? "#7BC89B" : "#B9AF99" }}>
        {copied ? "Copied ✓" : "Copy"}
      </button>
      <pre style={{ fontFamily: mono, fontSize: 12.5, lineHeight: 1.6, background: C.dark,
        color: "#D8CFBB", borderRadius: 12, padding: lang ? "30px 16px 14px" : "14px 16px",
        overflowX: "auto", margin: 0, border: `1px solid ${C.darkLine}` }}>{code}</pre>
    </div>
  );
}

function Endpoint({ method, path }: { method: string; path: string }) {
  const colors: Record<string, string> = {
    GET: "#1E8E78", POST: "#B26A10", PATCH: "#7A5EA8", DELETE: "#C2482E", WS: "#2E6EB2",
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "22px 0 8px" }}>
      <span style={{ fontSize: 11.5, fontWeight: 800, letterSpacing: ".04em", color: "#fff",
        background: colors[method] || C.ink, borderRadius: 7, padding: "4px 9px" }}>{method}</span>
      <code style={{ fontFamily: mono, fontSize: 14.5, fontWeight: 600 }}>{path}</code>
    </div>
  );
}

function Tbl({ rows, head }: { head: string[]; rows: string[][] }) {
  return (
    <div style={{ overflowX: "auto", margin: "10px 0 18px" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
        <thead><tr>{head.map((h) => (
          <th key={h} style={{ textAlign: "left", padding: "8px 12px", background: "#F3EEE1",
            borderBottom: `2px solid ${C.lineSoft}`, fontSize: 11.5, fontWeight: 800,
            letterSpacing: ".05em", textTransform: "uppercase", color: C.muted,
            whiteSpace: "nowrap" }}>{h}</th>))}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i}>{r.map((c, j) => (
            <td key={j} style={{ padding: "8px 12px", borderBottom: `1px solid ${C.line}`,
              verticalAlign: "top", lineHeight: 1.5,
              fontFamily: j === 0 ? mono : "inherit",
              fontWeight: j === 0 ? 600 : 400,
              whiteSpace: j === 0 ? "nowrap" : "normal" }}>{c}</td>))}</tr>))}</tbody>
      </table>
    </div>
  );
}

const H2 = ({ children }: { children: React.ReactNode }) => (
  <h2 style={{ fontFamily: serif, fontSize: 30, fontWeight: 500, margin: "0 0 12px" }}>{children}</h2>);
const H3 = ({ children }: { children: React.ReactNode }) => (
  <h3 style={{ fontSize: 17, fontWeight: 700, margin: "26px 0 8px" }}>{children}</h3>);
const P = ({ children }: { children: React.ReactNode }) => (
  <p style={{ fontSize: 14.5, lineHeight: 1.7, color: C.inkSoft, margin: "0 0 12px" }}>{children}</p>);
const Note = ({ children }: { children: React.ReactNode }) => (
  <div style={{ background: C.accentSoft, border: `1px solid ${C.accentSoftBorder}`,
    borderRadius: 12, padding: "12px 16px", fontSize: 13.5, lineHeight: 1.6,
    color: "#7A4E10", margin: "10px 0 18px" }}>{children}</div>);
const IC = ({ children }: { children: React.ReactNode }) => (
  <code style={{ fontFamily: mono, fontSize: 12.5, background: "#F3EEE1",
    border: `1px solid ${C.lineSoft}`, borderRadius: 6, padding: "1px 6px" }}>{children}</code>);

/* ─── sections ─── */

function Overview() {
  return (<>
    <H2>SonusLabs API</H2>
    <P>Build AI voice receptionists into your own product. The API gives you everything the
      console does: create and configure agents, stream live voice conversations over a
      WebSocket, read call history and transcripts, and get webhooks when calls complete.</P>
    <H3>Base URL</H3>
    <Code code={BASE} />
    <H3>What you can build</H3>
    <Tbl head={["Capability", "How"]} rows={[
      ["Agents", "Create, configure and delete AI receptionists (persona, voice, language, knowledge)"],
      ["Website widget", "One-line embed — a 'Talk to us' voice button on any site, no key in the page"],
      ["Phone numbers", "Claim real numbers for your agents, or keep your existing number and forward it"],
      ["Live voice", "Bidirectional audio over WebSocket — put a talking agent in any app"],
      ["Calls", "Full call history with transcripts, latency metrics and outcomes"],
      ["Analytics", "Aggregate volume, duration and outcome stats"],
      ["Webhooks", "HMAC-signed call.completed events pushed to your server"],
    ]} />
    <H3>Languages & voices</H3>
    <P>Agents speak 11 Indian languages — <IC>en-IN</IC>, <IC>hi-IN</IC>, <IC>kn-IN</IC>,{" "}
      <IC>te-IN</IC>, <IC>ta-IN</IC>, <IC>ml-IN</IC>, <IC>mr-IN</IC>, <IC>bn-IN</IC>,{" "}
      <IC>gu-IN</IC>, <IC>pa-IN</IC>, <IC>od-IN</IC> — and switch mid-call when the caller
      switches. Available voices come from <IC>GET /voice-lab</IC>:</P>
    <Code lang="json" code={`{"voices": ["ishita", "priya", "ritu", "neha", "kavya", "shreya", "simran", "tanya"]}`} />
    <Note>Usage is billed from your prepaid credits at the platform per-minute rate, per second
      of call time. New accounts include 30 free minutes. See <b>Billing</b>.</Note>
  </>);
}

function Authentication() {
  return (<>
    <H2>Authentication</H2>
    <P>Create an API key in the console under <b>API → Create key</b>. Keys look like{" "}
      <IC>sk_sonus_…</IC> and are shown exactly once — we store only a hash.</P>
    <H3>Sending the key</H3>
    <P>Use either header on every request:</P>
    <Code lang="bash" code={[
      `curl ${BASE}/agents \\`,
      `  -H "Authorization: Bearer sk_sonus_..." \\`,
      `  -H "X-Workspace-Id: <workspace-id>"`,
      ``,
      `# or equivalently`,
      `curl ${BASE}/agents \\`,
      `  -H "x-api-key: sk_sonus_..." \\`,
      `  -H "X-Workspace-Id: <workspace-id>"`,
    ].join("\n")} />
    <H3>The workspace header</H3>
    <P>Agents and calls live inside a workspace. Console endpoints require{" "}
      <IC>X-Workspace-Id</IC>; find your workspace ids (and confirm your key works) with:</P>
    <Code lang="bash" code={[
      `curl ${BASE}/auth/me -H "Authorization: Bearer sk_sonus_..."`,
      ``,
      `# {"enabled": true,`,
      `#  "user": {"id": "...", "email": "...", "name": "...", "picture": ""},`,
      `#  "workspaces": [{"id": "e62ad375-...", "name": "My workspace", "role": "owner"}]}`,
    ].join("\n")} />
    <H3>Security rules</H3>
    <Tbl head={["Rule", "Why"]} rows={[
      ["Keep keys server-side", "Anyone with the key can spend your credits. Never ship it in browser or mobile code."],
      ["Revoke leaked keys immediately", "Console → API → Revoke. Revocation is instant (requests return 401)."],
      ["One key per system", "Name keys after what uses them so a revoke has a known blast radius."],
    ]} />
    <P>Requests without a valid key (or session) return <IC>401</IC>. Requests for
      resources outside your workspace return <IC>404</IC> — ids are never confirmed
      to exist for other tenants.</P>
  </>);
}

function Quickstart() {
  return (<>
    <H2>Quickstart</H2>
    <P>From zero to a live voice conversation in four steps.</P>
    <H3>1 — Get your key and workspace</H3>
    <Code lang="bash" code={[
      `# Console -> API -> Create key, then:`,
      `curl ${BASE}/auth/me -H "Authorization: Bearer $SONUS_KEY"`,
      `# grab workspaces[0].id`,
    ].join("\n")} />
    <H3>2 — Create an agent</H3>
    <Code lang="bash" code={[
      `curl -X POST ${BASE}/agents \\`,
      `  -H "Authorization: Bearer $SONUS_KEY" \\`,
      `  -H "X-Workspace-Id: $WORKSPACE" \\`,
      `  -H "content-type: application/json" \\`,
      `  -d '{`,
      `    "name": "Reception",`,
      `    "language": "en-IN",`,
      `    "voice": "neha",`,
      `    "system_prompt": "You are the receptionist for Blue Dental Clinic...",`,
      `    "greeting_text": "Hello! Blue Dental, how can I help?",`,
      `    "knowledge_docs": ["Open Mon-Sat 9am-8pm.", "Cleaning costs Rs. 1500."]`,
      `  }'`,
      ``,
      `# 201 -> the response includes the FINAL "agent_id" (the server may`,
      `# suffix it for uniqueness, e.g. "reception-f4f3") — always read it back.`,
    ].join("\n")} />
    <H3>3 — Talk to it</H3>
    <P>Open the voice WebSocket and stream microphone audio; agent audio streams back.
      Full protocol in <b>Voice streaming</b>.</P>
    <Code lang="python" code={[
      `import asyncio, json, websockets`,
      ``,
      `KEY = "sk_sonus_..."`,
      `AGENT = "reception-f4f3"   # from step 2`,
      ``,
      `async def main():`,
      `    url = f"${WS}/web-call?agent_id={AGENT}&api_key={KEY}"`,
      `    async with websockets.connect(url) as ws:`,
      `        # send: PCM16 mono 16kHz binary frames (your mic / telephony leg)`,
      `        # recv: binary = agent audio (PCM16 mono 16kHz), text = JSON events`,
      `        async for msg in ws:`,
      `            if isinstance(msg, bytes):`,
      `                play(msg)                  # your audio output`,
      `            else:`,
      `                print(json.loads(msg))     # captions + call_start/call_end`,
      ``,
      `asyncio.run(main())`,
    ].join("\n")} />
    <H3>4 — Read the call afterwards</H3>
    <Code lang="bash" code={[
      `curl "${BASE}/calls?limit=5" \\`,
      `  -H "Authorization: Bearer $SONUS_KEY" \\`,
      `  -H "X-Workspace-Id: $WORKSPACE"`,
    ].join("\n")} />
    <H3>5 — Put it on a real phone line</H3>
    <P>Claim a phone number for the agent (or forward your existing business number to
      it) and callers can dial it directly — see <b>Phone numbers</b>.</P>
    <Code lang="bash" code={[
      `curl -X POST ${BASE}/numbers/claim \\`,
      `  -H "Authorization: Bearer $SONUS_KEY" \\`,
      `  -H "X-Workspace-Id: $WORKSPACE" \\`,
      `  -H "content-type: application/json" \\`,
      `  -d '{"agent_id": "reception-f4f3", "country": "US"}'`,
    ].join("\n")} />
  </>);
}

function ApiReference() {
  const R = (m: string, p: string, d: string, a: string): string[] => [m, p, d, a];
  return (<>
    <H2>API reference</H2>
    <P>Every endpoint in one place. <b>Auth</b> column: 🔑 = API key required,
      🔑+WS = API key + <IC>X-Workspace-Id</IC> header, — = public.</P>

    <H3>Agents</H3>
    <Tbl head={["Method", "Path", "Description", "Auth"]} rows={[
      R("GET", "/agents", "List all agents in the workspace (full configs)", "🔑+WS"),
      R("GET", "/agents-lite", "Lightweight id + name list", "🔑+WS"),
      R("GET", "/agents/{agent_id}", "Fetch one agent", "🔑+WS"),
      R("POST", "/agents", "Create an agent (server generates the final agent_id)", "🔑+WS"),
      R("PATCH", "/agents/{agent_id}", "Partial update (id + workspace immutable)", "🔑+WS"),
      R("DELETE", "/agents/{agent_id}", "Delete an agent", "🔑+WS"),
      R("POST", "/onboard/research", "Website URL → drafted agent config (AI research)", "🔑"),
    ]} />

    <H3>Voice</H3>
    <Tbl head={["Method", "Path", "Description", "Auth"]} rows={[
      R("GET", "/embed.js", "The website widget loader (embed on your site, no key)", "—"),
      R("WS", "/web-call?agent_id=", "Live voice via the widget (Origin-gated, no key)", "—"),
      R("WS", "/web-call?agent_id=&api_key=", "Live voice raw (server-side/native; key in URL)", "🔑"),
      R("GET", "/voice-lab", "List available voices", "—"),
      R("GET", "/voice-sample/{voice}", "Short audio preview of a voice (WAV)", "—"),
      R("GET", "/language-sample/{lang}", "Audio preview of a language (WAV)", "—"),
    ]} />

    <H3>Phone numbers</H3>
    <Tbl head={["Method", "Path", "Description", "Auth"]} rows={[
      R("GET", "/numbers", "Your numbers + what's claimable from stock", "🔑+WS"),
      R("POST", "/numbers/claim", "Attach a stock number to an agent (charges first month's rent)", "🔑+WS"),
      R("POST", "/numbers/release", "Release a number back to stock", "🔑+WS"),
    ]} />

    <H3>Calls & analytics</H3>
    <Tbl head={["Method", "Path", "Description", "Auth"]} rows={[
      R("GET", "/calls?limit=", "Recent call records, newest first (max 500)", "🔑+WS"),
      R("GET", "/calls/search?q=", "Keyword search over transcripts (max 200)", "🔑+WS"),
      R("GET", "/analytics?agent_id=&since=", "Aggregate stats (volume, duration, outcomes)", "🔑+WS"),
      R("GET", "/live-transcript?since=", "Live caption feed across active calls", "🔑+WS"),
    ]} />

    <H3>Account & billing</H3>
    <Tbl head={["Method", "Path", "Description", "Auth"]} rows={[
      R("GET", "/auth/me", "Who am I + my workspaces (works with an API key)", "🔑"),
      R("GET", "/workspaces", "List workspaces", "🔑"),
      R("POST", "/workspaces", "Create a workspace {name}", "🔑"),
      R("PATCH", "/workspaces/{id}", "Rename a workspace (owner only)", "🔑"),
      R("GET", "/billing/wallet", "Balance, rate, seconds left + full ledger", "🔑"),
      R("POST", "/billing/topup/order", "Create a payment order {amount_paise}", "🔑"),
      R("POST", "/billing/topup/verify", "Verify payment + credit the wallet", "🔑"),
      R("GET", "/api-keys", "List active API keys", "🔑"),
      R("POST", "/api-keys", "Create a key {name} — raw key returned ONCE", "🔑"),
      R("DELETE", "/api-keys/{id}", "Revoke a key (instant)", "🔑"),
    ]} />

    <H3>Platform</H3>
    <Tbl head={["Method", "Path", "Description", "Auth"]} rows={[
      R("GET", "/health", "Liveness + active session count", "—"),
    ]} />
    <Note>Detailed request/response shapes live in the feature sections:{" "}
      <b>Agents</b>, <b>Voice streaming</b>, <b>Calls & analytics</b>, <b>Webhooks</b>,{" "}
      <b>Billing & limits</b>.</Note>
  </>);
}

function Agents() {
  return (<>
    <H2>Agents</H2>
    <P>An agent is one AI receptionist: persona, voice, language, knowledge and behavior.
      All endpoints require your API key + <IC>X-Workspace-Id</IC>.</P>

    <Endpoint method="GET" path="/agents" />
    <P>List every agent in the workspace (full configs).</P>

    <Endpoint method="GET" path="/agents/{agent_id}" />
    <P>Fetch one agent. <IC>404</IC> if it isn't in your workspace.</P>

    <Endpoint method="POST" path="/agents" />
    <P>Create an agent. The server generates a globally-unique <IC>agent_id</IC> from your
      name/slug (collisions get a suffix) — <b>read the id from the 201 response</b>.</P>

    <Endpoint method="PATCH" path="/agents/{agent_id}" />
    <P>Partial update — send only the fields you're changing.{" "}
      <IC>agent_id</IC> and the owning workspace are immutable.</P>

    <Endpoint method="DELETE" path="/agents/{agent_id}" />
    <P>Delete the agent. Returns <IC>{`{"deleted": "<agent_id>"}`}</IC>.</P>

    <H3>Fields</H3>
    <Tbl head={["Field", "Type", "Notes"]} rows={[
      ["name", "string", "Display name (free-form; two workspaces can both have a “Reception”)"],
      ["language", "string", "Primary language, e.g. en-IN, hi-IN (11 supported)"],
      ["voice", "string", "Voice id from GET /voice-lab (e.g. neha)"],
      ["voice_pace", "number", "0.5–2.0 speaking speed (1.0 default, ~0.95 calmer)"],
      ["system_prompt", "string", "The persona. Who the agent is, what it knows, how it behaves"],
      ["greeting_text", "string", "First thing spoken when a call connects"],
      ["idle_reprompt_text", "string", "Spoken when the caller goes silent"],
      ["eagerness", "string", "Turn-taking style: cautious | balanced | eager"],
      ["knowledge_docs", "string[]", "Facts the agent can use (auto-translated across languages)"],
      ["enable_language_switch", "bool", "Follow the caller when they switch language (default true)"],
      ["enable_human_expression", "bool", "Natural hums/backchannels/pauses (default true)"],
      ["phone_numbers", "string[]", "DIDs that ring this agent (phone integration)"],
      ["transfer_numbers", "object", "Named human-handoff targets, e.g. {\"owner\": \"+91...\"}"],
      ["webhook_url", "string", "POST call.completed here (see Webhooks)"],
      ["webhook_secret", "string", "HMAC secret for webhook signatures"],
      ["max_turns_per_min", "int", "Per-call turn rate cap (0 = platform default)"],
    ]} />
    <H3>Example response</H3>
    <Code lang="json" code={[
      `{`,
      `  "agent_id": "reception-f4f3",`,
      `  "workspace_id": "e62ad375-bd93-4837-9c11-58a5b71efcb9",`,
      `  "name": "Reception",`,
      `  "language": "en-IN",`,
      `  "voice": "neha",`,
      `  "voice_pace": 1.0,`,
      `  "system_prompt": "You are the receptionist for...",`,
      `  "greeting_text": "Hello! Blue Dental, how can I help?",`,
      `  "knowledge_docs": ["Open Mon-Sat 9am-8pm."],`,
      `  "eagerness": "balanced",`,
      `  "...": "other flags and fields"`,
      `}`,
    ].join("\n")} />
  </>);
}

function PhoneNumbers() {
  return (<>
    <H2>Phone numbers</H2>
    <P>An agent becomes a real receptionist when people can call it. There are two ways —
      and they work together:</P>
    <Tbl head={["Option", "How it works", "Setup time"]} rows={[
      ["Get a SonusLabs number", "Claim a dedicated number; calls to it ring your agent directly", "instant"],
      ["Keep your existing number", "Your number stays with your carrier — you forward it to your SonusLabs number with one dial code", "~5 minutes"],
    ]} />
    <Note><b>No porting, no downtime, no carrier change.</b> Call forwarding is a standard
      feature of every phone line. Callers keep dialing the number they already know; your
      AI answers. The original caller's number passes through, so your call history shows
      who actually called. Turn forwarding off anytime with one code.</Note>

    <H3>Claiming a number</H3>
    <Endpoint method="GET" path="/numbers" />
    <P>Your workspace's numbers plus current stock by country:</P>
    <Code lang="json" code={[
      `{`,
      `  "numbers": [{"number": "+15550001111", "country": "US",`,
      `               "monthly_paise": 19900, "agent_id": "helpdesk",`,
      `               "assigned_at": 1783380000.0}],`,
      `  "available": [{"country": "US", "n": 12, "monthly_paise": 19900},`,
      `                {"country": "IN", "n": 4,  "monthly_paise": 49900}]`,
      `}`,
    ].join("\n")} />
    <Endpoint method="POST" path="/numbers/claim" />
    <P>Attaches a stock number to one of your agents. The first month's rent is charged
      from your credits immediately, and inbound routing is live from that second.</P>
    <Code lang="bash" code={[
      `curl -X POST ${BASE}/numbers/claim \\`,
      `  -H "Authorization: Bearer $SONUS_KEY" \\`,
      `  -H "X-Workspace-Id: $WORKSPACE" \\`,
      `  -H "content-type: application/json" \\`,
      `  -d '{"agent_id": "helpdesk", "country": "US"}'`,
      ``,
      `# 200 -> {"number": "+15550001111", "agent_id": "helpdesk", "monthly_paise": 19900}`,
      `# 400 -> insufficient credits for the first month's rent`,
      `# 409 -> no numbers in stock for that country right now`,
    ].join("\n")} />
    <Endpoint method="POST" path="/numbers/release" />
    <P>Releases the number back to stock and detaches it from the agent. Callers can no
      longer reach the agent on it. No partial-month refunds.</P>
    <Code lang="bash" code={[
      `curl -X POST ${BASE}/numbers/release \\`,
      `  -H "Authorization: Bearer $SONUS_KEY" \\`,
      `  -H "X-Workspace-Id: $WORKSPACE" \\`,
      `  -H "content-type: application/json" \\`,
      `  -d '{"number": "+15550001111"}'`,
    ].join("\n")} />

    <H3>Forwarding your existing number</H3>
    <P>Dial the code for your carrier <b>from your business phone</b>, replacing{" "}
      <IC>{"<n>"}</IC> with your SonusLabs number. The "when you don't answer" mode is the
      most popular: you pick up when you can, the AI catches everything you miss.</P>
    <P><b>India</b></P>
    <Tbl head={["Carrier", "Forward ALL", "No answer", "Busy", "Unreachable", "Cancel"]} rows={[
      ["Jio", "*401*<n>", "*403*<n>", "*405*<n>", "*409*<n>", "*402 / *404 / *406 / *410"],
      ["Airtel", "**21*<n>#", "**61*<n>*11*20#", "**67*<n>#", "**62*<n>#", "##21# etc."],
      ["Vi", "**21*<n>#", "**61*<n>#", "**67*<n>#", "**62*<n>#", "##21# etc."],
      ["BSNL", "**21**<n>#", "**61**<n>#", "**67**<n>#", "**62**<n>#", "##21# etc."],
    ]} />
    <P><b>United States</b></P>
    <Tbl head={["Carrier", "Forward ALL", "Cancel"]} rows={[
      ["Verizon / most landlines", "*72<n>", "*73"],
      ["AT&T / GSM", "*21*<n>#", "#21#"],
      ["T-Mobile", "**21*<n>#", "##21#"],
    ]} />
    <Note><b>India note:</b> forward Indian numbers to an <b>Indian</b> SonusLabs number.
      Most Indian carriers block international forwarding or bill it as ISD. Also check
      your plan — some Indian retail plans bill the forwarded leg; business plans usually
      include forwarding free.</Note>

    <H3>Verify it works</H3>
    <P>1. Dial the forwarding code from your business phone (listen for the confirmation
      tone or message).<br />
      2. Call your business number from a different phone.<br />
      3. Your AI should answer with its greeting — and the call appears in{" "}
      <IC>GET /calls</IC> with the caller's real number.</P>

    <H3>Billing</H3>
    <P>Number rent is charged from the same prepaid credits as call usage — you'll see{" "}
      <IC>number_rent</IC> entries in your wallet ledger. The first month is charged when
      you claim; rent keeps the number reserved for you whether or not calls come in.
      Per-minute call usage is billed separately at the platform rate.</P>
  </>);
}

function Widget() {
  return (<>
    <H2>Website widget</H2>
    <P>Put a "Talk to us" voice button on any website with <b>one line</b> — a visitor clicks
      it and talks live to your agent. This is the right way to add voice to a web page:
      <b> no API key goes in the browser.</b></P>

    <H3>1. Enable the widget on your agent</H3>
    <P>In the console, open the agent → <b>Website widget</b> → toggle <b>Enable on my
      website</b> and list the exact site origins allowed to embed it (one per line), e.g.
      <IC>https://yourbusiness.com</IC>. Save.</P>

    <H3>2. Paste the snippet</H3>
    <Code lang="html" code={[
      `<script src="${BASE}/embed.js"`,
      `        data-agent="your-agent-id"`,
      `        data-label="Talk to us"`,
      `        data-color="#0D9488"></script>`,
    ].join("\n")} />
    <P>That's it. A floating button appears; clicking it opens the mic and streams a live
      conversation. Usage bills your SonusLabs wallet like any call.</P>
    <Tbl head={["Attribute", "Meaning"]} rows={[
      ["data-agent", "The agent id to talk to (not secret — safe to expose)"],
      ["data-label", "Button text (default \"Talk to us\")"],
      ["data-color", "Button + accent colour (hex)"],
    ]} />

    <H3>Why it's safe without a key</H3>
    <P>Access is gated by the embedding page's <IC>Origin</IC>, which the browser sets on the
      WebSocket handshake and <b>cannot be forged from page JavaScript</b>. The server only
      accepts the call if that Origin is on your agent's allowlist. So even though the
      <IC>data-agent</IC> id is visible in your page source, nobody can lift it onto another
      site and spend your credits.</P>
    <Note>Use the widget for <b>browsers</b>. Use the raw WebSocket with a key (below) only
      from a <b>trusted server</b> or a native app — never ship an <IC>sk_sonus_</IC> key in
      web page code.</Note>
  </>);
}

function Streaming() {
  return (<>
    <H2>Voice streaming (raw)</H2>
    <Endpoint method="WS" path="/web-call?agent_id=<id>&api_key=sk_sonus_..." />
    <Note><b>Server-side / native apps only.</b> This raw socket takes an{" "}
      <IC>sk_sonus_</IC> key in the URL — fine from your backend or a mobile app, but it must
      NEVER appear in website JavaScript. For a web page, use the <b>Website widget</b> above
      (no key).</Note>
    <P>One WebSocket = one live phone-quality conversation with an agent. You stream the
      caller's audio up; the agent's voice and live captions stream back. This is the same
      transport the widget and the console's talk orb use.</P>
    <H3>Sending audio (you → agent)</H3>
    <Tbl head={["Property", "Value"]} rows={[
      ["Encoding", "PCM16, little-endian, mono"],
      ["Sample rate", "16,000 Hz"],
      ["Framing", "Binary WS messages, any chunk size (20–100 ms recommended)"],
    ]} />
    <H3>Receiving (agent → you)</H3>
    <Tbl head={["Message", "Meaning"]} rows={[
      ["binary", "Agent voice: PCM16 mono 16kHz, batched ~80 ms per message"],
      [`{"role","text"}`, "Live caption — role is \"user\" (transcript) or \"assistant\" (reply)"],
      [`{"type":"call_start","max_seconds":N}`, "Sent once if the call has a time budget (your credit balance)"],
      [`{"type":"call_end","reason":R}`, "Call ended by the server — see reasons below"],
    ]} />
    <H3>End reasons & close codes</H3>
    <Tbl head={["Signal", "Meaning"]} rows={[
      ["reason time_limit", "Public-demo 3-minute cap (doesn't apply to API calls)"],
      ["reason no_credits", "Wallet empty at call start — top up to talk"],
      ["reason credits_exhausted", "Wallet ran out mid-call"],
      ["close 1008", "Unauthorized: bad/revoked key, or the agent isn't in your workspace"],
      ["close 1013", "Server at capacity — retry with backoff"],
    ]} />
    <H3>Behavior notes</H3>
    <P>• <b>Barge-in is built-in:</b> if the caller talks over the agent, the agent pauses
      within ~100 ms and listens. Just keep streaming mic audio continuously.<br />
      • <b>Language switching:</b> the caller can switch between the 11 languages mid-call;
      the agent follows in the same voice.<br />
      • <b>Billing:</b> the clock runs from connection to close, billed per second from
      your credits.</P>
    <H3>Minimal client</H3>
    <Code lang="python" code={[
      `import asyncio, json, websockets`,
      ``,
      `async def call(agent_id: str, key: str, mic_frames):`,
      `    url = f"${WS}/web-call?agent_id={agent_id}&api_key={key}"`,
      `    async with websockets.connect(url) as ws:`,
      ``,
      `        async def uplink():                      # your mic -> agent`,
      `            async for frame in mic_frames:       # PCM16 mono 16k bytes`,
      `                await ws.send(frame)`,
      ``,
      `        async def downlink():                    # agent -> your speaker`,
      `            async for msg in ws:`,
      `                if isinstance(msg, bytes):`,
      `                    play(msg)`,
      `                else:`,
      `                    ev = json.loads(msg)`,
      `                    if ev.get("type") == "call_end":`,
      `                        print("ended:", ev["reason"]); return`,
      `                    print(ev.get("role"), ":", ev.get("text"))`,
      ``,
      `        await asyncio.gather(uplink(), downlink())`,
    ].join("\n")} />
  </>);
}

function Calls() {
  return (<>
    <H2>Calls & analytics</H2>

    <Endpoint method="GET" path="/calls?limit=50" />
    <P>Recent calls in the workspace, newest first (max 500).</P>
    <Code lang="json" code={[
      `{"calls": [{`,
      `  "call_id": "v3:kTIfY0...",`,
      `  "agent_id": "reception-f4f3",`,
      `  "from_number": "+9198...", "to_number": "+1557...",`,
      `  "started_at": 1783374018.5, "ended_at": 1783374139.1,`,
      `  "duration_s": 120.6,`,
      `  "turn_count": 14,`,
      `  "avg_perceived_ms": 840,`,
      `  "outcome": "completed",`,
      `  "turns": "[{\\"role\\":\\"user\\",\\"text\\":\\"...\\",\\"t\\":3.1}, ...]",`,
      `  "metrics": "[...]", "metadata": "{...}"`,
      `}]}`,
    ].join("\n")} />
    <Note><IC>turns</IC>, <IC>metrics</IC> and <IC>metadata</IC> are JSON <i>strings</i> —
      parse them client-side. <IC>turns</IC> is the transcript;{" "}
      <IC>avg_perceived_ms</IC> is the caller-perceived response latency.</Note>

    <Endpoint method="GET" path="/calls/search?q=refund" />
    <P>Keyword search over transcripts. Same response shape (max 200).</P>

    <Endpoint method="GET" path="/analytics" />
    <P>Aggregate stats. Optional <IC>?agent_id=</IC> and <IC>?since=</IC> (unix seconds).</P>
    <Code lang="json" code={[
      `{`,
      `  "agent_id": null,`,
      `  "total_calls": 412,`,
      `  "avg_duration_s": 96.4,`,
      `  "avg_perceived_ms": 810.2,`,
      `  "by_outcome": {"completed": 371, "transferred": 28, "abandoned": 13}`,
      `}`,
    ].join("\n")} />
  </>);
}

function Webhooks() {
  return (<>
    <H2>Webhooks</H2>
    <P>Set <IC>webhook_url</IC> (and a <IC>webhook_secret</IC>) on an agent and SonusLabs
      POSTs the full call record to your server when each call completes — transcripts into
      your CRM, follow-ups, alerting, whatever you need.</P>
    <H3>Delivery</H3>
    <Tbl head={["Property", "Value"]} rows={[
      ["Event", "call.completed — fired once when the call ends"],
      ["Method", "POST, content-type: application/json, 10s timeout"],
      ["Signature", "x-signature-sha256 header = HMAC-SHA256 hex of the RAW body, keyed by webhook_secret"],
      ["Retries", "Best-effort single delivery today — design your handler to be fast (respond 200 immediately, process async)"],
    ]} />
    <H3>Payload</H3>
    <Code lang="json" code={[
      `{`,
      `  "event": "call.completed",`,
      `  "call_id": "v3:kTIfY0...",`,
      `  "agent_id": "reception-f4f3",`,
      `  "from_number": "+9198...", "to_number": "+1557...",`,
      `  "started_at": 1783374018.5, "ended_at": 1783374139.1,`,
      `  "duration_s": 120.6, "turn_count": 14,`,
      `  "outcome": "completed",`,
      `  "turns": "[{\\"role\\":\\"user\\",\\"text\\":\\"...\\"}, ...]",`,
      `  "metadata": "{\\"summary\\": \\"Caller booked a cleaning...\\"}"`,
      `}`,
    ].join("\n")} />
    <H3>Verifying the signature</H3>
    <Code lang="python" code={[
      `import hashlib, hmac`,
      ``,
      `def verify(raw_body: bytes, header_sig: str, secret: str) -> bool:`,
      `    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()`,
      `    return hmac.compare_digest(expected, header_sig)`,
      ``,
      `# FastAPI example`,
      `# @app.post("/hooks/sonuslabs")`,
      `# async def hook(request: Request):`,
      `#     raw = await request.body()`,
      `#     if not verify(raw, request.headers["x-signature-sha256"], SECRET):`,
      `#         return Response(status_code=401)`,
      `#     ...  # respond 200 fast, process in background`,
    ].join("\n")} />
  </>);
}

function Billing() {
  return (<>
    <H2>Billing & limits</H2>
    <P>SonusLabs is prepaid: you add credits, calls consume them. Every account starts with{" "}
      <b>30 free minutes</b>.</P>
    <H3>How usage is billed</H3>
    <Tbl head={["Rule", "Detail"]} rows={[
      ["Rate", "₹3 per minute of call time"],
      ["Granularity", "Per second (₹3/min = 5 paise/sec), rounded up to the whole second"],
      ["When", "Charged when the call ends, for actual connected time"],
      ["What counts", "Every agent call: API WebSocket calls, console test calls, phone calls"],
      ["Numbers", "Phone numbers rent monthly from the same credits (charged at claim)"],
      ["What's free", "The public homepage demo; creating/editing agents; reading calls & analytics"],
    ]} />
    <H3>Ledger entry types</H3>
    <P>Every credit movement is a ledger row (see it in <IC>GET /billing/wallet</IC> or the
      console's Billing tab):</P>
    <Tbl head={["kind", "Direction", "Meaning"]} rows={[
      ["trial_grant", "+", "Your 30 free minutes at signup (once)"],
      ["topup", "+", "Credits you purchased"],
      ["call_usage", "−", "A finished call, with its seconds recorded"],
      ["number_rent", "−", "Monthly rent for a claimed phone number"],
      ["adjustment", "±", "Manual correction by support"],
    ]} />
    <H3>Enforcement</H3>
    <P>At call start, the maximum duration is derived from your balance —{" "}
      <IC>call_start.max_seconds</IC> tells you the budget. An empty wallet refuses new calls
      (<IC>call_end.reason = no_credits</IC>; inbound phone calls simply don't answer), and a
      wallet that empties mid-call ends it (<IC>credits_exhausted</IC>).</P>
    <Endpoint method="GET" path="/billing/wallet" />
    <P>Your balance, rate and full ledger — poll it to alert your own systems before credits
      run dry.</P>
    <Code lang="json" code={[
      `{`,
      `  "balance_paise": 19970,`,
      `  "seconds_left": 3994,`,
      `  "rate_paise_per_min": 300,`,
      `  "trial_minutes": 30,`,
      `  "ledger": [{"kind": "call_usage", "delta_paise": -85, "seconds": 16.1,`,
      `              "balance_after": 19970, "ref": "web:reception:17833...", "t": 1783374040.1}]`,
      `}`,
    ].join("\n")} />
    <H3>Platform limits</H3>
    <Tbl head={["Limit", "Value"]} rows={[
      ["API keys per account", "10 active"],
      ["Top-up range", "₹50 – ₹1,00,000 per transaction"],
      ["Concurrent calls", "Server capacity guarded — WS close 1013 means retry with backoff"],
    ]} />
  </>);
}

function Errors() {
  return (<>
    <H2>Errors</H2>
    <P>Errors are JSON: <IC>{`{"error": "human-readable message"}`}</IC> with a standard
      status code.</P>
    <Tbl head={["Status", "Meaning", "What to do"]} rows={[
      ["400", "Bad request — missing/invalid field", "Check the message; fix the payload"],
      ["401", "No/invalid/revoked credentials", "Check the Authorization header and key status"],
      ["404", "Not found — including resources outside your workspace", "Verify the id AND the X-Workspace-Id header"],
      ["409", "Conflict (e.g. payment already processed)", "Treat as done; don't retry blindly"],
      ["422", "Semantically invalid (e.g. research URL unreachable)", "Fix the input"],
      ["502/503", "Upstream/provider unavailable or feature not configured", "Retry with backoff"],
    ]} />
    <H3>WebSocket close codes</H3>
    <Tbl head={["Code", "Meaning"]} rows={[
      ["1000", "Normal end (also used for server-ended calls — read the call_end reason)"],
      ["1008", "Unauthorized for this agent"],
      ["1013", "At capacity — retry later"],
    ]} />
  </>);
}

/* ─── shell ─── */

const SECTIONS: { id: string; label: string; el: () => JSX.Element }[] = [
  { id: "overview", label: "Overview", el: Overview },
  { id: "authentication", label: "Authentication", el: Authentication },
  { id: "quickstart", label: "Quickstart", el: Quickstart },
  { id: "api-reference", label: "API reference", el: ApiReference },
  { id: "agents", label: "Agents", el: Agents },
  { id: "widget", label: "Website widget", el: Widget },
  { id: "phone-numbers", label: "Phone numbers", el: PhoneNumbers },
  { id: "streaming", label: "Voice streaming", el: Streaming },
  { id: "calls", label: "Calls & analytics", el: Calls },
  { id: "webhooks", label: "Webhooks", el: Webhooks },
  { id: "billing", label: "Billing & limits", el: Billing },
  { id: "errors", label: "Errors", el: Errors },
];

export function Docs() {
  const nav = useNavigate();
  const mob = useIsMobile();
  const { section } = useParams();
  const active = SECTIONS.find((s) => s.id === section) || SECTIONS[0];
  useEffect(() => { window.scrollTo(0, 0); }, [active.id]);

  const sideLink = (s: typeof SECTIONS[number]) => {
    const on = s.id === active.id;
    return (
      <div key={s.id} onClick={() => nav(`/docs/${s.id}`)}
        style={{ padding: mob ? "8px 13px" : "8px 12px", borderRadius: 9, cursor: "pointer",
          fontSize: 13.5, fontWeight: on ? 700 : 500, whiteSpace: "nowrap",
          background: on ? C.accentSoft : "transparent",
          color: on ? C.accentDeep : C.muted,
          border: `1px solid ${on ? C.accentSoftBorder : "transparent"}` }}>
        {s.label}
      </div>
    );
  };

  return (
    <div style={{ minHeight: "100vh", background: C.paper }}>
      {/* header */}
      <div style={{ position: "sticky", top: 0, zIndex: 50, backdropFilter: "blur(10px)",
        background: "rgba(250,247,240,.88)", borderBottom: `1px solid ${C.line}` }}>
        <div style={{ maxWidth: 1160, margin: "0 auto", padding: mob ? "0 16px" : "0 28px",
          height: mob ? 56 : 62, display: "flex", alignItems: "center", gap: 12 }}>
          <div onClick={() => nav("/")} style={{ display: "flex", alignItems: "center",
            gap: 10, cursor: "pointer" }}>
            <div style={{ width: 28, height: 28, borderRadius: 8, background: C.ink,
              display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ width: 10, height: 10, borderRadius: "50%", background: C.accent,
                boxShadow: "0 0 0 3px rgba(224,138,30,.28)" }} />
            </div>
            <span style={{ fontFamily: serif, fontSize: mob ? 19 : 21 }}>SonusLabs</span>
          </div>
          <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: ".08em",
            textTransform: "uppercase", color: C.accentDeep, background: C.accentSoft,
            border: `1px solid ${C.accentSoftBorder}`, borderRadius: 7,
            padding: "3px 9px" }}>Docs</span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 18, alignItems: "center" }}>
            {!mob && <span onClick={() => nav("/")} style={{ fontSize: 14, fontWeight: 500,
              color: C.muted, cursor: "pointer" }}>Home</span>}
            <button onClick={() => nav("/console")} style={{ fontSize: 13.5, fontWeight: 600,
              color: "#fff", background: C.ink, border: "none", borderRadius: 10,
              padding: "8px 14px", cursor: "pointer" }}>Console</button>
          </div>
        </div>
        {mob && (
          <div style={{ display: "flex", gap: 4, overflowX: "auto", padding: "0 12px 10px",
            WebkitOverflowScrolling: "touch" }}>
            {SECTIONS.map(sideLink)}
          </div>
        )}
      </div>

      <div style={{ maxWidth: 1160, margin: "0 auto", padding: mob ? "22px 16px 60px" : "34px 28px 90px",
        display: mob ? "block" : "grid", gridTemplateColumns: "205px 1fr", gap: 40 }}>
        {!mob && (
          <div>
            <div style={{ position: "sticky", top: 86, display: "flex",
              flexDirection: "column", gap: 3 }}>
              <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: ".1em",
                textTransform: "uppercase", color: C.faint, padding: "0 12px 8px" }}>
                Documentation</div>
              {SECTIONS.map(sideLink)}
            </div>
          </div>
        )}
        <div style={{ minWidth: 0, animation: "sl-fadeup .3s ease both" }}>
          <active.el />
          {/* prev/next */}
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12,
            marginTop: 44, paddingTop: 20, borderTop: `1px solid ${C.line}` }}>
            {(() => {
              const i = SECTIONS.indexOf(active);
              const prev = SECTIONS[i - 1], next = SECTIONS[i + 1];
              return (<>
                <span>{prev && (
                  <span onClick={() => nav(`/docs/${prev.id}`)} style={{ fontSize: 13.5,
                    fontWeight: 600, color: C.muted, cursor: "pointer" }}>← {prev.label}</span>)}
                </span>
                <span>{next && (
                  <span onClick={() => nav(`/docs/${next.id}`)} style={{ fontSize: 13.5,
                    fontWeight: 700, color: C.accentDeep, cursor: "pointer" }}>{next.label} →</span>)}
                </span>
              </>);
            })()}
          </div>
        </div>
      </div>
    </div>
  );
}
