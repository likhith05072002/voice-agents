// Privacy Policy + Terms of Service. Public, brand-matched, and written for a
// voice platform that RECORDS calls and processes them through AI subprocessors.
// These satisfy Google OAuth's publishing requirement (public privacy + terms
// URLs). NOT legal advice — have counsel review before scaling.
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { C, serif } from "../theme";
import { useIsMobile } from "../useIsMobile";

const UPDATED = "7 July 2026";
const COMPANY = "SonusLabs";
const CONTACT = "hello@sonuslabs.online";

function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  const nav = useNavigate();
  const mob = useIsMobile();
  useEffect(() => { window.scrollTo(0, 0); }, []);
  return (
    <div style={{ minHeight: "100vh", background: C.paper, color: C.ink }}>
      <div style={{ position: "sticky", top: 0, zIndex: 50, backdropFilter: "blur(10px)",
        background: "rgba(250,247,240,.88)", borderBottom: `1px solid ${C.line}` }}>
        <div style={{ maxWidth: 820, margin: "0 auto", padding: mob ? "0 16px" : "0 28px",
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
          <div style={{ marginLeft: "auto", display: "flex", gap: 16 }}>
            <span onClick={() => nav("/privacy")} style={navLink}>Privacy</span>
            <span onClick={() => nav("/terms")} style={navLink}>Terms</span>
            <span onClick={() => nav("/")} style={navLink}>Home</span>
          </div>
        </div>
      </div>
      <div style={{ maxWidth: 820, margin: "0 auto", padding: mob ? "26px 18px 70px" : "44px 28px 100px" }}>
        <h1 style={{ fontFamily: serif, fontSize: mob ? 32 : 40, fontWeight: 500, margin: 0 }}>{title}</h1>
        <div style={{ fontSize: 13, color: C.faint, margin: "8px 0 6px" }}>
          Last updated: {UPDATED}</div>
        <div style={{ fontSize: 13, color: C.faint, marginBottom: 28, lineHeight: 1.6,
          background: C.accentSoft, border: `1px solid ${C.accentSoftBorder}`,
          borderRadius: 10, padding: "10px 14px" }}>
          This is a plain-language policy for {COMPANY}. It is provided for transparency and
          is not legal advice. Have your own counsel review before relying on it at scale.
        </div>
        {children}
        <div style={{ marginTop: 40, paddingTop: 20, borderTop: `1px solid ${C.line}`,
          fontSize: 13.5, color: C.muted }}>
          Questions? Email <a href={`mailto:${CONTACT}`} style={{ color: C.accentDeep,
            fontWeight: 600 }}>{CONTACT}</a>.
        </div>
      </div>
    </div>
  );
}
const navLink: React.CSSProperties = { fontSize: 14, fontWeight: 500, color: C.muted, cursor: "pointer" };

const H = ({ children }: { children: React.ReactNode }) => (
  <h2 style={{ fontSize: 19, fontWeight: 700, margin: "30px 0 10px" }}>{children}</h2>);
const P = ({ children }: { children: React.ReactNode }) => (
  <p style={{ fontSize: 15, lineHeight: 1.75, color: C.inkSoft, margin: "0 0 12px" }}>{children}</p>);
const LI = ({ children }: { children: React.ReactNode }) => (
  <li style={{ fontSize: 15, lineHeight: 1.7, color: C.inkSoft, margin: "0 0 7px" }}>{children}</li>);
const UL = ({ children }: { children: React.ReactNode }) => (
  <ul style={{ margin: "0 0 12px", paddingLeft: 22 }}>{children}</ul>);

export function Privacy() {
  return (
    <Shell title="Privacy Policy">
      <P>{COMPANY} (“we”, “us”) provides an AI voice-receptionist platform (“the Service”).
        This policy explains what we collect, why, and your choices. By using the Service you
        agree to this policy.</P>

      <H>1. Who this covers</H>
      <P>Two groups: <b>customers</b> (businesses who sign up and configure AI agents) and
        <b> callers</b> (people who phone a customer's AI agent). Callers interact with us
        only through our customers; those customers are responsible for informing their
        callers about AI handling and recording.</P>

      <H>2. What we collect</H>
      <UL>
        <LI><b>Account data</b> — when you sign in with Google we receive your name, email
          address and profile picture. We do not receive your Google password.</LI>
        <LI><b>Call data</b> — audio is processed in real time to run the conversation. We
          store <b>transcripts</b>, call metadata (numbers, timestamps, duration, outcome)
          and quality metrics. Audio is not retained as recordings beyond what is needed to
          process the call unless a feature you enable requires it.</LI>
        <LI><b>Configuration</b> — the agents, prompts, knowledge and phone numbers you set up.</LI>
        <LI><b>Billing data</b> — your prepaid credit balance and an itemised ledger. Card
          and bank details are handled by our payment processor (Razorpay); we never see or
          store full payment credentials.</LI>
        <LI><b>Technical data</b> — a session cookie to keep you signed in, and standard
          server logs (IP, timestamps) for security and debugging.</LI>
      </UL>

      <H>3. How we use it</H>
      <UL>
        <LI>To operate the Service — run calls, store transcripts, show your dashboard.</LI>
        <LI>To bill usage accurately from your prepaid credits.</LI>
        <LI>To secure the platform, prevent abuse, and debug issues.</LI>
        <LI>To contact you about your account and important service changes.</LI>
      </UL>
      <P>We do <b>not</b> sell your data, and we do not use your call content to train
        general-purpose AI models.</P>

      <H>4. Call recording &amp; consent</H>
      <P>The Service transcribes and processes phone conversations. <b>As the customer, you
        are responsible</b> for obtaining any consent your callers are legally owed and for
        disclosing that an AI assistant is handling and processing the call, as required in
        your jurisdiction. We provide greeting and disclosure controls to help you do this.</P>

      <H>5. Service providers (subprocessors)</H>
      <P>We share the minimum data needed with providers who help us run the Service:</P>
      <UL>
        <LI><b>Sarvam AI</b> — speech-to-text, language model and text-to-speech.</LI>
        <LI><b>OpenRouter</b> — supplementary language-model and web-search features.</LI>
        <LI><b>Telnyx / telephony partners</b> — carrying phone calls.</LI>
        <LI><b>Razorpay</b> — payment processing.</LI>
        <LI><b>Google</b> — sign-in (OAuth).</LI>
        <LI><b>Cloudflare</b> — network delivery and protection.</LI>
      </UL>
      <P>Each processes data under its own terms and only to provide its function.</P>

      <H>6. Data retention</H>
      <P>We keep account and configuration data while your account is active. Call records
        and transcripts are retained to give you history and analytics, and because
        telephony regulations (including India's OSP framework) can require call records to
        be kept for a minimum period (typically at least one year). You can request deletion
        of your account data subject to those legal retention obligations.</P>

      <H>7. Security</H>
      <P>Sessions are stored server-side and only a hash of the session token is kept; API
        keys are stored hashed and shown only once; traffic is encrypted in transit (TLS).
        No system is perfectly secure, but we apply reasonable technical and organisational
        safeguards.</P>

      <H>8. Your rights</H>
      <P>Depending on your location (including under India's Digital Personal Data
        Protection Act, 2023, and comparable laws) you may request access to, correction of,
        or deletion of your personal data, and you may withdraw consent. Contact us at{" "}
        <a href={`mailto:${CONTACT}`} style={{ color: C.accentDeep, fontWeight: 600 }}>{CONTACT}</a>{" "}
        and we will respond within the timeframe the applicable law requires.</P>

      <H>9. Cookies</H>
      <P>We use a single essential, HttpOnly session cookie to keep you signed in. We do not
        use advertising or third-party tracking cookies.</P>

      <H>10. International transfers</H>
      <P>Our providers may process data outside your country. Where required, we rely on
        appropriate safeguards for such transfers.</P>

      <H>11. Children</H>
      <P>The Service is for businesses and is not directed to anyone under 18.</P>

      <H>12. Changes</H>
      <P>We may update this policy; we will post the new date above and, for material
        changes, notify account holders.</P>
    </Shell>
  );
}

export function Terms() {
  return (
    <Shell title="Terms of Service">
      <P>These Terms govern your use of the {COMPANY} AI voice-receptionist platform (“the
        Service”). By creating an account or using the Service you agree to them.</P>

      <H>1. The Service</H>
      <P>We provide software to build and run AI voice agents, optionally attach phone
        numbers, and review call history. Features evolve; we may add, change or remove
        functionality.</P>

      <H>2. Accounts</H>
      <P>You must provide accurate information, keep your credentials and API keys secure,
        and are responsible for all activity under your account. You must be authorised to
        act for the business you register.</P>

      <H>3. Acceptable use</H>
      <P>You agree <b>not</b> to use the Service to:</P>
      <UL>
        <LI>make unlawful calls, spam, or robocalls that violate telemarketing / DND rules
          (including India's TRAI regulations and, where applicable, the US TCPA);</LI>
        <LI>impersonate others, commit fraud, or harass;</LI>
        <LI>handle calls without any consent or disclosure your jurisdiction requires;</LI>
        <LI>reverse-engineer, overload, or abuse the platform.</LI>
      </UL>
      <P><b>You are solely responsible</b> for the lawfulness of your calls and for obtaining
        any caller consent required by law.</P>

      <H>4. Credits &amp; billing</H>
      <UL>
        <LI>The Service is <b>prepaid</b>: you add credits and usage is deducted at the
          published rate, billed per second of call time.</LI>
        <LI>New accounts receive a limited free trial of call minutes.</LI>
        <LI><b>Phone numbers</b> rent monthly from your credits, charged when claimed.
          Number rent is non-refundable, including for partial months.</LI>
        <LI>Purchased credits are non-refundable except where required by law. Prices may
          change with notice.</LI>
        <LI>If your balance reaches zero, calls will be refused or ended, and rented numbers
          may be suspended after a grace period.</LI>
      </UL>

      <H>5. Phone numbers &amp; forwarding</H>
      <P>Numbers are provided from our (or our partners') inventory and remain our property;
        you hold a right to use them while rented. If you forward your own carrier number to
        the Service, your relationship with your carrier and any forwarding charges are your
        responsibility.</P>

      <H>6. AI output disclaimer</H>
      <P>AI agents can make mistakes and may produce inaccurate or unexpected responses. The
        Service is provided “as is” without warranty that outputs are accurate, complete or
        fit for a particular purpose. Do not rely on it for medical, legal, financial or
        other professional advice, and review it before acting on business-critical matters.</P>

      <H>7. Your content</H>
      <P>You retain ownership of the prompts, knowledge and configurations you provide. You
        grant us the licence needed to process them to run the Service. You represent that
        you have the rights to the content you upload.</P>

      <H>8. Intellectual property</H>
      <P>The Service, software and branding are owned by us. These Terms grant you a limited,
        non-exclusive, non-transferable right to use the Service, nothing more.</P>

      <H>9. Suspension &amp; termination</H>
      <P>You may stop using the Service any time. We may suspend or terminate accounts that
        violate these Terms, pose a security or legal risk, or have a zero balance for an
        extended period. On termination, access ends; retention of records follows our
        Privacy Policy and applicable law.</P>

      <H>10. Limitation of liability</H>
      <P>To the maximum extent permitted by law, we are not liable for indirect, incidental
        or consequential damages, or lost profits or data. Our total liability for any claim
        is limited to the amount you paid us for the Service in the three months before the
        claim.</P>

      <H>11. Indemnity</H>
      <P>You agree to indemnify us against claims arising from your use of the Service,
        your calls, your content, or your breach of these Terms or of any law — including
        claims by your callers relating to consent or recording.</P>

      <H>12. Governing law</H>
      <P>These Terms are governed by the laws of India, and the courts at our principal place
        of business have jurisdiction, unless a mandatory law in your location requires
        otherwise.</P>

      <H>13. Changes</H>
      <P>We may update these Terms; continued use after the posted date means you accept the
        changes.</P>
    </Shell>
  );
}
