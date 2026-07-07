# Phone Numbers: Providing & Connecting (Business Plan)

*Research date: 2026-07-07. This is the core GTM mechanic — an AI receptionist
nobody can call is a demo, not a product.*

---

## 1. The big insight from competitor research

**"Bring your own number" is NOT tunneling, proxying, or porting. It's plain
carrier call forwarding — a 60-year-old feature every phone line already has.**

How every AI-receptionist product (ai-receptionist.com, Smith.ai, Rosie,
Goodcall, My AI Front Desk, Upfirst…) actually works:

1. **At signup, the platform gives the customer a dedicated AI number** (a DID
   the platform bought from its carrier — Telnyx/Twilio in the US).
2. The customer dials a forwarding code **on their own phone**
   (US: `*72<AI number>`, GSM: `*21*<AI number>#`) — their carrier now
   forwards calls to the AI number.
3. A caller dials the business's **familiar number** → the carrier forwards →
   the platform's webhook fires → the AI answers *as that business*.
4. The **original caller's ID passes through** (forwarding preserves CLI), so
   the platform logs who called — not the forwarding line.
5. Turning it off is one dial code (`*73` / `#21#`). **The customer never loses
   control of their number.** No porting, no downtime, no carrier change,
   ~5-minute setup.

That's the entire trick. The "provided number" and "bring your own number"
stories are **the same feature**: every account gets a platform DID; BYON just
means the customer forwards their existing number to it.

### Forwarding modes = product tiers

| Mode | Carrier feature | Product story |
|---|---|---|
| Always | CFU (unconditional) | "AI answers every call" |
| When busy | CFB | "AI takes the overflow" |
| No answer (N sec) | CFNA | "AI catches what you miss" — **best seller**: humans answer when they can, AI when they can't |
| Unreachable | CFNRc | "Never lose a call when your phone dies" |

### The heavier options (later phases)

- **Number porting**: move the customer's number onto our carrier (LOA
  paperwork, 1–4 weeks, their old line goes away). Only worth it for customers
  going all-in. Phase 3.
- **SIP trunking / BYOC**: enterprises point their existing trunk at us.
  Phase 3+, deal-driven.

---

## 2. Carrier forwarding codes (the BYON wizard content)

### India

| Carrier | Forward ALL | No answer | Busy | Unreachable | Cancel all |
|---|---|---|---|---|---|
| **Jio** | `*401*<num>` | `*403*<num>` | `*405*<num>` | `*409*<num>` | `*402` / `*404` / `*406` / `*410` per type |
| **Airtel** | `**21*<num>#` | `**61*<num>*11*<sec>#` | `**67*<num>#` | `**62*<num>#` | `##21#` / `##61#` / `##67#` / `##62#` |
| **Vi** | `**21*<num>#` | `**61*<num>#` | `**67*<num>#` | `**62*<num>#` | `##21#` / `##61#` / `##67#` / `##62#` |
| **BSNL** | `**21**<num>#` | `**61**<num>#` | `**67**<num>#` | `**62**<num>#` | `##21#` etc. |

### US

| Carrier | Forward ALL | Cancel |
|---|---|---|
| Verizon / most landlines | `*72<num>` | `*73` |
| AT&T wireless / GSM | `*21*<num>#` | `#21#` |
| T-Mobile | `**21*<num>#` | `##21#` |

Note: forwarding is free on most plans, but **forwarded legs can consume the
customer's minutes** on some Indian plans — mention in onboarding FAQ.

---

## 3. Number supply: the two very different markets

### US — trivial, fully self-serve (Telnyx)

- Telnyx has a **number search + purchase API** (`/available_phone_numbers`,
  number orders) and numbers are assigned to our existing Call Control app —
  inbound webhooks then hit our existing `/webhook/telnyx` and route by DID
  (we already have this: `AgentConfig.phone_numbers` → `by_phone` resolution).
- Cost: **~$1–2/mo per local DID** + ~$0.005/min inbound. We can programmatically
  buy a number the second a customer asks.
- This makes the **US GTM (HVAC/plumbing pilot) self-serve end to end.**

### India — regulated, needs a licensed partner

- **Telnyx/Twilio cannot sell Indian DIDs.** TRAI/DoT rules: Indian numbers
  come only via licensed Indian operators or authorized cloud-telephony
  providers, with **business KYC** and OSP-style audit-trail obligations
  (call records kept ≥1 year — we already persist all calls).
- Indian supply = partner with a cloud telephony provider: **Exotel, Ozonetel,
  MyOperator, Tata Smartflo, EnableX** (we already have EnableX creds in our
  config from earlier work). Numbers cost roughly **₹200–1,000/mo** wholesale
  depending on type (mobile/fixed/toll-free) + per-minute charges.
- Practical rollout: operator (us) procures a **pool** of Indian numbers under
  our KYC, assigns them to customers from the pool, collects usage. Later:
  per-customer KYC flow for dedicated numbers at scale.
- **Important:** the customer-side BYON forwarding story works in India TODAY
  regardless — Jio/Airtel/Vi/BSNL all support the codes above. The only thing
  we need is an Indian DID to forward TO.

---

## 4. What we build (product)

**One mental model: every workspace can attach numbers to agents.**

- **"Get a number"** — claim a number from the platform pool (US: Telnyx-backed,
  India: partner-backed). Monthly rent charged from the same prepaid credits
  wallet (`number_rent` ledger entries). Number is attached to ONE agent;
  inbound calls to it route to that agent (already-working DID routing).
- **"Use your existing number"** — the BYON wizard: after a number is attached,
  show the customer their carrier's exact dial codes (with the assigned DID
  filled in), for each forwarding mode. Verify with a test call. Off-codes
  shown too — trust matters.
- **Admin pool management** — operator buys numbers (Telnyx portal/API today,
  partner portal for India), adds them to the pool with their monthly price;
  monitors assignment and utilization in /admin.

### Pricing (v1)

| Item | Price | Cost | Margin |
|---|---|---|---|
| US number | ₹199/mo | ~₹90–170/mo ($1–2) | thin but fine — it anchors usage |
| India number | ₹499/mo | ₹200–600/mo (partner) | similar |
| Usage | ₹3/min existing rate | — | unchanged; forwarding adds no cost to us (caller pays their carrier for the forwarded leg? No — the FORWARDER pays for the leg on most plans) |

The number is the hook; the per-minute usage is the business.

### Rollout phases

1. **Now (built this phase):** pool model — admin stocks numbers, users claim
   self-serve, BYON wizard with India+US codes, rent from credits.
2. **Next:** Telnyx auto-buy on claim for US (API purchase when pool empty),
   monthly auto-renewal billing (cron: charge rent monthly, suspend number on
   empty wallet after grace).
3. **Later:** India partner integration (Exotel/EnableX number APIs + KYC
   upload flow), porting, SIP trunking for enterprise.

### Risks / honest notes

- **India partner dependency:** number cost, provisioning latency, and KYC
  friction are all set by the partner. Mitigation: pool model hides latency;
  start with 10–20 pooled numbers.
- **Forwarded-leg minutes:** some Indian retail plans bill the customer for the
  forwarded leg. FAQ + recommend business plans with free forwarding.
- **Caller-ID edge cases:** a minority of carriers present the forwarding
  line's ID instead of the original caller's. Detect + note in call records.
- **Compliance:** keep call records ≥1 year (already done), business KYC for
  Indian dedicated numbers when we get there.

---

## 5. Sources

- [ai-receptionist.com — call forwarding feature](https://ai-receptionist.com/features/call-forwarding/) (provided-number + BYON flow, $14/mo, carrier code table)
- [Upfirst — call forwarding setup](https://upfirst.ai/solutions/call-forwarding)
- [SkipCalls — forwarding setup guide](https://skipcalls.com/solutions/call-forwarding-setup)
- [My AI Front Desk — phone integration tutorial](https://www.myaifrontdesk.com/tutorials/how-to-integrate-your-business-phone-with-my-ai-front-desk-receptionist)
- [TelecomTalk — India forwarding codes (Jio/Airtel/Vi/BSNL)](https://telecomtalk.info/how-to-start-and-stop-call-forwarding/493353/)
- [Smartprix — India carrier code guide](https://www.smartprix.com/bytes/codes-to-activate-deactivate-call-forwarding-on-jio-airtel-vi-bsnl/)
- [Telnyx — buy a phone number API](https://developers.telnyx.com/docs/numbers/phone-numbers/buy-phone-number)
- [Telnyx — Call Control app configuration](https://support.telnyx.com/en/articles/4374050-configuring-call-control-texml-applications-voice-api)
- [CloudConnect — TRAI rules for cloud telephony](https://cloudconnect.in/blogs/why-is-it-so-important-for-cloud-telephony-providers-to-follow-trai-ru)
- [Avoxi — India virtual numbers (KYC requirements)](https://www.avoxi.com/india-virtual-phone-numbers/)
- [Bonvoice — business phone numbers in India](https://bonvoice.com/insights/business-phone-number-in-india/)
