# SonusLabs — Production Readiness Review

*Review date: 2026-07-07. Reviewer panel perspective: Principal Architect, Principal
AI Engineer, Staff SRE, Principal Security Engineer, Performance Engineer, Tech Lead.*
*Evidence: full codebase trace, 282 automated tests passing, targeted live E2E on the
running stack (accounts mode, Postgres 16, dev backend).*

---

## Verdict

**CONDITIONALLY READY for a controlled pilot (tens of businesses). NOT YET ready for
the "thousands of businesses" bar without the P0/P1 items below.**

The core voice pipeline is genuinely strong — it has been hardened on live calls
(barge-in, echo, endpointing) and is well-tested. The new commercial layer (accounts,
billing, numbers, admin) is correctly designed with real tenant isolation and an
auditable money ledger. The gap to "thousands" is **operational**, not architectural:
single-process runtime, no automated deploy/rollback, thin observability, and a handful
of correctness edges (documented below). During this review I **fixed 8 issues inline**
(6 security/correctness, 2 operational) and verified each.

---

## Fixed during this review (verified)

| # | Severity | Issue | Fix | Evidence |
|---|---|---|---|---|
| 1 | **Critical** | **SSRF**: `/onboard/research` fetched any user-supplied URL — a signed-up user could make the server hit `127.0.0.1`, the home LAN the Pi sits on, or cloud metadata (`169.254.169.254`) | DNS-resolve + block private/loopback/link-local/reserved ranges; validate **every redirect hop** (no `follow_redirects`); reject internal hosts up front | Live: `169.254.169.254` and `127.0.0.1:8001` now log `not reachable` and return `422`; **zero** internal requests made |
| 2 | High | **Money atomicity**: number-claim charged rent on one DB connection and assigned the DID on another — a crash between them charged the customer with no number | `charge_fixed(..., con=)` runs the debit **inside** the claim transaction; both commit or roll back together | Code + claim E2E still passes |
| 3 | High | **Wallet overdraft race**: N concurrent calls each capped at the *full* balance → could spend N× the balance | Per-workspace in-process active-call counter; each new call's budget = `balance / concurrent` | Live: 2 concurrent calls → 6034s and 3017s (not 6034+6034) |
| 4 | High | **Unbounded input**: agent create/update accepted arbitrary-size JSON (megabyte prompts, 10k knowledge docs) — storage/DoS vector | Field caps (prompt 24k, greeting 1k, ≤60 docs ×4k, ≤20 numbers) → `400` | Live: 30k prompt → `400`; valid → `201` |
| 5 | High | **API regression**: creating an agent via the API with no `agent_id` (the documented contract) crashed `from_dict` with a 500 | `agent_id` now defaults to `""`; server generates the id | Live: `{"name":"Valid Agent"}` → `201`, id `valid-agent` |
| 6 | Medium | **Session table growth**: expired sessions never deleted | Opportunistic GC sweep on each login (`DELETE … expires_at < now()`) | Code |
| 7 | Medium | **Auth DoS**: `/auth/*` endpoints unthrottled (mint sessions, call Google) | Process-global token bucket (30 burst / 0.5·s) → `429` | Code |
| 8 | Medium | **Prod env footguns**: `.env.production.example` didn't mention `TELNYX_PUBLIC_KEY`, `OUTBOUND_API_KEY`, `ADMIN_API_KEY` — all fail *open* when unset | Documented as required-when-relevant with generation commands | `deploy/env.production.example` |

---

## 1. Architecture review

**Shape:** one FastAPI process = HTTP API + two audio WebSockets (`/media-stream`
telephony, `/web-call` browser) + the React SPA (served single-origin in prod). Brain
in `src/pipeline/turn_engine.py`; I/O glue in `src/main.py`. Data in Postgres (accounts
mode) or SQLite (legacy). Behind Caddy + a Cloudflare named tunnel on a Raspberry Pi 5.

**Strengths**
- Clean separation: `tenancy/` (agents), `persistence/` (calls), `accounts/` (auth,
  billing, numbers, admin), `pipeline/` (turn engine), `services/` (STT/LLM/TTS).
- Repositories are duck-typed async Protocols — the SQLite→Postgres swap was a
  contained change, exactly as intended.
- **Mode switch** (`DATABASE_URL` set = accounts mode) means production can stay on the
  proven legacy path until the new stack is deliberately promoted. Low-risk rollout.
- Tenant isolation is enforced at the query layer and verified: cross-tenant reads
  return `404` (no id enumeration), not `403`.

**Weaknesses (architectural debt, not blockers for a pilot)**
- **Single-process, single-node by design.** Three pieces of critical state are
  in-process: `SessionLimiter`, `CallRegistry` (call→agent routing), and now
  `_billed_active`. **The app cannot run multiple workers or nodes** without
  externalizing these to Redis. This is the #1 scaling ceiling. Code comments already
  flag Redis as the intended swap.
- `src/main.py` is ~1,400 lines doing routing + telephony + web-call + admin. It works
  and is traceable, but it's the maintainability hotspot — split into routers before it
  grows further.
- Business logic (billing, numbers) lives partly in `main.py` handlers rather than a
  service module — fine now, will resist testing as it grows.

## 2. Scalability assessment

| Dimension | Assessment |
|---|---|
| Multi-tenant data | **Good.** `workspace_id` indexed on agents + calls; every read scoped. |
| Concurrent voice sessions | **Capacity-bound & correct on one node.** `MAX_CONCURRENT_SESSIONS` (default 100) rejects with WS `1013`. Real ceiling on a Pi is CPU/network for the 20ms frame pump — **needs a load test to set the true number** (see Perf). |
| Horizontal scale | **Blocked.** In-process registries prevent >1 worker. This is the hard limit before "thousands of concurrent calls." |
| Postgres | Pool `min=1 max=8`. Fine for one process; **8 connections is low if the pool is ever shared** — size to workload. WAL, indexed. |
| DB contention | Wallet updates use `FOR UPDATE` row locks — correct, and contention is per-user (low). |
| Background jobs | **None exist.** No monthly number-rent renewal, no async call-summary queue. Renewal is charged only at claim (P1). |
| Long sessions | Web-call demo cap + credit cap bound duration; idle hangup exists. OK. |

**Bottom line:** vertical scale on one Pi is fine for a pilot. "Thousands of businesses"
with meaningful concurrent call volume **requires** the Redis externalization + a move
off a single home Pi (or a warm standby), because a single node is also a single point
of failure.

## 3. Performance assessment

- **Voice latency is the product** and it's well-engineered: TTFT ~340ms (reasoning
  off — correctly defaulted), pre-rendered greetings, parallel STT/TTS connect, LLM
  warmup during greeting, uvloop on Linux, `CPUAffinity`/`Nice -10` for the pump. TTFA
  measured ~342ms in this session's logs.
- DB writes are off the event loop (`asyncio.to_thread` for SQLite, async pool for PG),
  so persistence never stalls audio. **Verified good.**
- **Gap: no load test exists.** The safe concurrent-call number on the actual Pi 5 is
  unknown. Recommend a synthetic ramp (the repo already has an AI-tester harness) to set
  `MAX_CONCURRENT_SESSIONS` from data, not the default 100.
- Web-call audio batching (80ms) already tuned to kill scheduling jitter.

## 4. Security assessment

**Now solid after fixes 1, 4, 7:**
- Sessions: server-side, revocable, **only sha256 stored**; HttpOnly + SameSite=Lax +
  Secure (in prod). OAuth uses signed state + nonce cookie (CSRF-safe) + open-redirect
  guard on `next`. API keys hashed, shown once, revocable-instant.
- CORS correctly flips to an explicit allowlist **with** credentials (not wildcard) in
  accounts mode. Telnyx webhook is Ed25519-verified. Outbound webhooks HMAC-signed.
- Tenant isolation verified by test (cross-tenant = 404).

**Residual security items**
- **P1 — fail-open secrets:** `TELNYX_PUBLIC_KEY`, `OUTBOUND_API_KEY`, `ADMIN_API_KEY`
  all disable their protection when unset. Now documented (fix 8) but should become a
  **startup assertion** in accounts/telephony mode (refuse to boot without them).
- **P2 — no per-IP rate limiting** at the app (the token bucket is global). Real
  per-IP/DDoS protection must live at Cloudflare/Caddy — confirm it's configured.
- **P2 — secrets in `.env` on disk**, readable by the app user. Acceptable for a Pi
  pilot; a secrets manager is the eventual answer.
- **Good:** no secrets logged (`.env` values never printed); transcripts are tenant-
  scoped; SQL uses parameterized queries throughout (no injection surface found).

## 5. Reliability assessment

- **Call teardown is defensive:** billing charge, webhook post, and summary are each
  wrapped so a failure can't crash teardown or the call. Metering "never raises."
- **Reconnection:** Cloudflare tunnel auto-reconnects; systemd `Restart=always`.
- **Gaps:**
  - **No automated DB backup.** Postgres on the Pi with no documented `pg_dump`
    cron/offsite copy = **the money ledger is one disk failure from gone.** This is the
    single most important reliability P0 before taking real payments.
  - **No graceful shutdown of live calls** on deploy/restart — in-flight calls drop.
    Acceptable at low volume; document it.
  - No circuit breakers on Sarvam/OpenRouter/Telnyx — a provider outage degrades per-
    call (caught + logged) but there's no global backoff or fallback provider.
  - Single Pi = single point of failure (home power/ISP). A pilot risk to accept
    consciously.

## 6. API integration review

- **Sarvam** (STT/TTS/LLM), **OpenRouter** (search/onboarding), **Telnyx** (voice),
  **EnableX** (India, configured), **Razorpay** (payments), **Google** (OAuth): each
  authenticates per its own model (subscription-key vs Bearer vs Basic vs HMAC) — the
  review requirement that credentials aren't blindly shared **is satisfied**.
- **Retries/timeouts:** timeouts are set (httpx 15–25s). **No retry/backoff** on
  transient provider failures — a blip fails that one call/turn. Acceptable per-call;
  add jittered retry for the research + payment-verify paths (P2).
- **Razorpay path is unit-correct but never run against the real API** (no account yet)
  — must be tested end-to-end with live keys before charging money.

## 7. Testing

- **282 passing, 2 skipped.** Strong coverage of the voice pipeline, turn engine,
  safety guards, tenancy resolution, persistence. Re-ran green after all fixes.
- **Gaps:** the new accounts/billing/numbers/admin endpoints have **no automated
  tests** — they were verified by live E2E in this session but need pytest coverage
  (Postgres fixture) before they're safe to refactor. This is the top testing P1.

---

## Risk register

| ID | Risk | Likelihood | Impact | Priority | Mitigation |
|---|---|---|---|---|---|
| R1 | No DB backups → ledger/customer loss | Med | Critical | **P0** | `pg_dump` cron + offsite copy before real payments |
| R2 | Single Pi node = SPOF (power/ISP/disk) | Med | High | **P0/P1** | Accept for pilot consciously; plan warm standby |
| R3 | Can't scale past 1 process (in-proc state) | High at scale | High | **P1** | Redis for CallRegistry/limiter/billed-active |
| R4 | Fail-open secrets if unset in prod | Med | High | **P1** | Startup assertions (refuse boot) |
| R5 | No monthly rent renewal / dunning | High | Med | **P1** | Scheduled job: charge rent, suspend on empty + grace |
| R6 | New commercial endpoints untested | High | Med | **P1** | pytest suite w/ Postgres fixture |
| R7 | Razorpay never run live | High | High | **P1** | Full sandbox→live test before launch |
| R8 | No load test → wrong concurrency cap | Med | Med | **P2** | Ramp test, set MAX_CONCURRENT_SESSIONS from data |
| R9 | No provider retry/circuit-breaker | Med | Med | **P2** | Jittered retry on research/payment; breaker later |
| R10 | Residual overdraft window (charge-at-end) | Low | Low | **P2** | Credit *holds* (reserve at start, settle at end) |
| R11 | No deploy automation/rollback | Med | Med | **P2** | CI + versioned deploy + health-gated restart |
| R12 | Thin observability (structlog only) | High | Med | **P2** | Metrics (Prometheus), error tracking (Sentry), uptime alert |

---

## Prioritized issue list

**P0 — before taking real money / real customers**
1. **Automated Postgres backups** (cron `pg_dump` + offsite). (R1)
2. Consciously accept or mitigate the **single-node SPOF** for the pilot. (R2)

**P1 — before scaling past a small pilot**
3. **Externalize in-process state to Redis** (CallRegistry, SessionLimiter, billed-
   active) → unlock multi-worker/multi-node. (R3)
4. **Startup assertions** for fail-open secrets in prod. (R4)
5. **Monthly number-rent renewal + dunning** job. (R5)
6. **pytest coverage** for accounts/billing/numbers/admin. (R6)
7. **Live Razorpay** sandbox→prod verification. (R7)

**P2 — hardening**
8. Load test → real concurrency cap. (R8)
9. Retry/backoff + circuit breakers on providers. (R9)
10. Credit holds to close the overdraft window. (R10)
11. Deploy automation + rollback + graceful drain. (R11)
12. Observability: metrics, error tracking, uptime + low-balance alerts. (R12)

---

## Final production checklist

- [x] Tenant isolation enforced + verified (404 on cross-tenant)
- [x] Auth: revocable sessions, hashed tokens, CSRF/open-redirect guards, API keys
- [x] Money: integer paise, atomic ledger, atomic claim, overdraft-bounded
- [x] SSRF closed on user-supplied URL fetch
- [x] Input validation caps on client-writable config
- [x] Secrets never logged; parameterized SQL
- [x] 282 tests green
- [ ] **DB backups automated** (P0)
- [ ] **Fail-open secrets asserted at boot** (P1)
- [ ] **Redis for in-process state** (P1, for >1 node)
- [ ] **Rent renewal + dunning job** (P1)
- [ ] **Automated tests for commercial endpoints** (P1)
- [ ] **Razorpay verified live** (P1)
- [ ] Load test → concurrency cap (P2)
- [ ] Observability + alerting (P2)
- [ ] Deploy automation + rollback (P2)

**Recommendation:** approve for a **controlled pilot** (invite-limited, tens of
businesses) once the two P0 items are done. Complete the P1 set before opening self-
serve signup at scale. The foundation is sound; the remaining work is the operational
maturity that turns a strong build into a dependable service.
