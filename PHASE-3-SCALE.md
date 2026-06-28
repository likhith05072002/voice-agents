# Phase 3: Cloud Scale — 200-500 Clients with High Availability

## Goal
Migrate from the Raspberry Pi 5 to cloud infrastructure to serve 200-500 dental clinics and hospitals. The system must handle 5,000-15,000 calls/day with 99.9% uptime, automatic failover, full observability, client-facing dashboard, and billing integration.

**Timeline**: 5-6 weeks (after Phase 2)
**Deliverable**: Production cloud deployment serving 200-500 clients with SLA guarantees

**Trigger to start Phase 3**: When RPi 5 hits >70% capacity consistently (15+ concurrent calls during peak hours, or >100 tenants)

---

## System Design: Cloud Architecture

### High-Level Architecture

```
                         ┌──────────────────┐
                         │   Cloudflare /    │
                         │   Nginx (LB)      │
                         │                   │
                         │  ┌─ API requests  │
                         │  └─ WebSocket     │
                         │     (sticky by    │
                         │      call SID)    │
                         └────────┬──────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    │             │             │
              ┌─────▼─────┐┌─────▼─────┐┌─────▼─────┐
              │  Worker 1  ││  Worker 2  ││  Worker N  │
              │            ││            ││            │
              │ FastAPI    ││ FastAPI    ││ FastAPI    │
              │ + Pipeline ││ + Pipeline ││ + Pipeline │
              │            ││            ││            │
              │ 30 calls   ││ 30 calls   ││ 30 calls   │
              └──────┬─────┘└──────┬─────┘└──────┬─────┘
                     │             │             │
                     └──────┬──────┘──────┬──────┘
                            │             │
                   ┌────────▼──────┐ ┌────▼────────────┐
                   │  Redis Cluster │ │  PostgreSQL     │
                   │  (sessions,    │ │  Primary +      │
                   │   metrics,     │ │  Read Replica   │
                   │   pub/sub)     │ │                 │
                   └───────────────┘ └─────────────────┘
                            │
                   ┌────────▼──────┐
                   │  Object Store  │
                   │  (S3/R2)       │
                   │  Call recordings│
                   │  Filler audio  │
                   └───────────────┘
```

### Why This Architecture

```
RPi 5 limits:
  - Single point of failure (power outage = all calls drop)
  - 15-20 concurrent calls max
  - No horizontal scaling
  - Home network unreliable for SLA

Cloud solves:
  - Multiple workers → any one can die without impact
  - Scale from 2 to 20 workers on demand
  - Cloud network = predictable latency
  - 99.9% uptime achievable
```

---

## Infrastructure Design

### Compute: Docker on VMs (Mumbai Region)

```
Why Docker on VMs, not Kubernetes:
  - K8s is overkill for <20 worker nodes
  - Docker Compose + Swarm is simpler and cheaper
  - Can migrate to K8s later if needed (>500 clients)
  - Each VM runs 1 worker container (simple isolation)

VM sizing per worker:
  - 4 vCPU, 8GB RAM (e.g., AWS c6g.xlarge or GCP e2-standard-4)
  - ARM64 preferred (same architecture as RPi 5 — no code changes)
  - Ubuntu 24.04 LTS

Worker capacity:
  - 30 concurrent calls per worker (more CPU than RPi 5)
  - 2 workers = 60 concurrent calls → ~200 tenants
  - 5 workers = 150 concurrent calls → ~500 tenants
  - Auto-scale based on active call count
```

### Worker Scaling Strategy

```
Scale triggers:
  - Scale UP:   Active calls > 70% of (workers × 30) for 5 minutes
  - Scale DOWN: Active calls < 30% of (workers × 30) for 15 minutes
  - Min workers: 2 (always — for redundancy)
  - Max workers: 10 (cost ceiling)

Implementation:
  - Each worker reports active_call_count to Redis every 10s
  - Scale controller (separate lightweight container) monitors
  - Scale via cloud API (AWS ASG / GCP MIG / manual script)

Cost:
  2 workers (min): ~₹15,000/month
  5 workers (avg): ~₹35,000/month
  10 workers (max): ~₹70,000/month
```

### Load Balancer: WebSocket Affinity

```
Critical requirement: A call's WebSocket MUST stay on the same worker
for the entire duration (the worker holds STT/TTS connections).

Solution: Sticky sessions based on call_control_id

Nginx config:
  upstream voice_workers {
      ip_hash;  # Simple sticky (or use cookie-based)
      server worker1:8000;
      server worker2:8000;
      server worker3:8000;
  }

  map $http_upgrade $connection_upgrade {
      default upgrade;
      '' close;
  }

  server {
      location /ws/ {
          proxy_pass http://voice_workers;
          proxy_http_version 1.1;
          proxy_set_header Upgrade $http_upgrade;
          proxy_set_header Connection $connection_upgrade;
          proxy_read_timeout 3600s;  # Keep WebSocket alive for long calls
      }

      location /api/ {
          proxy_pass http://voice_workers;
          # API requests don't need affinity
      }
  }
```

### Redis: Sentinel (3-node) → Cluster at 500+ tenants

```
Redis Sentinel (Phase 3 start):
  - 1 primary + 2 replicas + 3 sentinels
  - Auto-failover if primary dies
  - ~₹5,000/month (managed Redis or 3 small VMs)
  
Redis Cluster (Phase 3 mature, if needed):
  - 6 nodes (3 primary + 3 replicas)
  - Hash slot sharding by tenant_id
  - ~₹15,000/month

Data in Redis:
  - Session state (same as Phase 2)
  - Pub/Sub for inter-worker communication
  - Rate limiting counters
  - Real-time metrics aggregation
```

### PostgreSQL: Primary + Read Replica

```
Primary (Mumbai):
  - Handles all writes (call logs, appointments, tenant CRUD)
  - 2 vCPU, 8GB RAM, 100GB SSD
  - ~₹8,000/month (managed RDS/Cloud SQL)

Read Replica:
  - Handles read-heavy queries (call history, analytics, dashboard)
  - 2 vCPU, 4GB RAM
  - ~₹4,000/month

Backup:
  - Automated daily snapshots (managed service)
  - Point-in-time recovery (7 days)
  - Monthly export to S3 for long-term retention
```

---

## Stateless Worker Design

### What changes from Phase 2 (RPi 5)

```
Phase 2 (RPi 5):               Phase 3 (Cloud):
─────────────────               ─────────────────
Single process                  Multiple workers
Sessions in local Redis         Sessions in shared Redis Cluster
PostgreSQL on Pi                Managed PostgreSQL (remote)
Filler audio on disk            Filler audio on S3/R2 (cached in memory)
Direct Telnyx WebSocket         Telnyx → LB → Worker WebSocket
No failover                     Worker dies → active calls reconnect
Logs to file                    Logs to centralized system
```

### Worker Startup Sequence

```python
# On worker boot:
# 1. Connect to Redis Sentinel (get primary)
# 2. Connect to PostgreSQL
# 3. Load all tenant configs into memory cache
# 4. Download filler audio from S3 into memory
# 5. Register self in Redis: voice:workers:{worker_id} = {host, port, active_calls: 0}
# 6. Start accepting WebSocket connections
# 7. Start background tasks:
#    - Heartbeat to Redis every 10s
#    - Tenant config refresh every 60s
#    - Metrics reporting every 30s
```

### Worker Health Check

```python
# GET /api/health
#
# Checks:
# 1. Redis connectivity (ping)
# 2. PostgreSQL connectivity (SELECT 1)
# 3. Sarvam API reachability (lightweight check)
# 4. Active calls < max capacity
# 5. Memory usage < 80%
# 6. CPU usage < 90%
#
# Returns:
# {"status": "healthy", "active_calls": 12, "capacity": 30}
# OR
# {"status": "degraded", "reason": "redis_unreachable"}
# OR
# {"status": "unhealthy", "reason": "oom_risk"}
#
# LB removes unhealthy workers from rotation
```

---

## Resilience: Circuit Breakers & Fallbacks

### Circuit Breaker Pattern

```
For each external service (Sarvam STT, Sarvam TTS, Sarvam LLM):

                    ┌──────────┐
                    │  CLOSED   │  (normal operation)
                    │           │
                    │ Track     │
                    │ failures  │
                    └─────┬─────┘
                          │
                   3 failures in 10s
                          │
                    ┌─────▼─────┐
                    │   OPEN    │  (all requests go to fallback)
                    │           │
                    │ Wait 30s  │
                    └─────┬─────┘
                          │
                    ┌─────▼─────┐
                    │ HALF-OPEN │  (try 1 request to primary)
                    │           │
                    │ Success?  │──Yes──► CLOSED
                    │ Fail?     │──No───► OPEN
                    └───────────┘
```

### Fallback Providers

```
Service       Primary            Fallback              Notes
───────────────────────────────────────────────────────────────
STT           Sarvam Saaras V3   Deepgram Nova-3       Deepgram supports Hindi, Telugu
                                                       Limited Kannada/Tamil
TTS           Sarvam Bulbul V2   Google Cloud TTS      Google has Telugu, Kannada,
                                                       Hindi, Tamil voices
LLM           Sarvam-30B         Groq Llama 3.3 70B    English-only fallback
                                                       (better than no response)
Telephony     Telnyx             (none — single point) Mitigate via multi-region
```

### Implementation: `src/resilience/circuit_breaker.py`

```python
# class CircuitBreaker:
#     def __init__(
#         self,
#         failure_threshold: int = 3,
#         recovery_timeout: int = 30,
#         expected_exceptions: tuple = (TimeoutError, ConnectionError),
#     ):
#         self.state = "closed"
#         self.failure_count = 0
#         self.last_failure_time = None
#
#     async def call(self, func, *args, **kwargs):
#         if self.state == "open":
#             if time_since_last_failure > self.recovery_timeout:
#                 self.state = "half-open"
#             else:
#                 raise CircuitOpenError()
#
#         try:
#             result = await func(*args, **kwargs)
#             if self.state == "half-open":
#                 self.state = "closed"
#                 self.failure_count = 0
#             return result
#         except self.expected_exceptions:
#             self.failure_count += 1
#             if self.failure_count >= self.failure_threshold:
#                 self.state = "open"
#                 self.last_failure_time = time.time()
#             raise
```

### Resilient Service Wrapper: `src/resilience/fallback.py`

```python
# class ResilientSTT:
#     def __init__(self):
#         self.primary = SarvamSTTClient()
#         self.fallback = DeepgramSTTClient()
#         self.breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
#
#     async def transcribe(self, audio: bytes, language: str):
#         try:
#             return await self.breaker.call(self.primary.transcribe, audio, language)
#         except CircuitOpenError:
#             log.warning("stt.circuit_open", provider="sarvam", fallback="deepgram")
#             return await self.fallback.transcribe(audio, language)
#
# Similarly for ResilientTTS and ResilientLLM
```

---

## Observability: Prometheus + Grafana

### Metrics: `src/monitoring/metrics.py`

```python
# Prometheus metrics (using prometheus_client library):
#
# Histograms (latency):
#   voice_e2e_latency_ms              labels: [tenant_id, language]
#   voice_perceived_latency_ms        labels: [tenant_id, language]
#   voice_stt_latency_ms              labels: [provider]
#   voice_llm_ttft_ms                 labels: [model]
#   voice_tts_ttfa_ms                 labels: [provider]
#   voice_barge_in_latency_ms         labels: [tenant_id]
#
# Counters:
#   voice_calls_total                 labels: [tenant_id, direction, outcome]
#   voice_turns_total                 labels: [tenant_id]
#   voice_errors_total                labels: [service, error_type]
#   voice_tool_calls_total            labels: [tenant_id, tool_name, success]
#   voice_circuit_breaker_trips       labels: [service]
#
# Gauges:
#   voice_active_calls                labels: [worker_id]
#   voice_active_calls_by_tenant      labels: [tenant_id]
#   voice_worker_cpu_percent          labels: [worker_id]
#   voice_worker_memory_percent       labels: [worker_id]
#
# Cost tracking:
#   voice_cost_inr_total              labels: [tenant_id, service]
```

### Grafana Dashboards

```
Dashboard 1: Operations Overview
├── Active calls (real-time gauge)
├── Calls per hour (time series)
├── E2E latency P50/P95/P99 (time series)
├── Error rate (time series)
├── Circuit breaker status (table)
├── Worker CPU/Memory (per worker)
└── Cost accumulated today (INR)

Dashboard 2: Per-Tenant View
├── Tenant selector (dropdown)
├── Calls today / this week / this month
├── Average latency for this tenant
├── Tool usage (calendar bookings, SMS sent)
├── Error rate for this tenant
├── Cost breakdown for this tenant
└── Recent calls with outcomes

Dashboard 3: Latency Deep Dive
├── Pipeline stage breakdown (stacked bar)
│   ├── STT latency
│   ├── Turn detection
│   ├── LLM TTFT
│   ├── Sentence accumulation
│   ├── TTS TTFA
│   └── Transport
├── Latency by language (comparison)
├── Latency by time of day (heatmap)
└── Slowest calls (table with drill-down)
```

### Centralized Logging

```
Stack: structlog → JSON → stdout → Docker log driver → Loki (or CloudWatch)

Log format:
{
    "timestamp": "2026-08-15T10:23:45.123Z",
    "level": "info",
    "event": "pipeline.turn_complete",
    "worker_id": "w-001",
    "call_id": "c-abc123",
    "tenant_id": "t-def456",
    "stt_latency_ms": 210,
    "llm_ttft_ms": 780,
    "tts_ttfa_ms": 290,
    "e2e_measured_ms": 1280,
    "e2e_perceived_ms": 250,
    "language": "te-IN",
    "transcript": "రేపు అపాయింట్‌మెంట్ కావాలి"
}
```

### Alerting Rules

```yaml
# Prometheus alerting rules (alertmanager → Telegram/Slack/PagerDuty)

groups:
  - name: voice-agent
    rules:
      - alert: HighLatency
        expr: histogram_quantile(0.95, voice_e2e_latency_ms) > 2000
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "P95 latency exceeds 2s for 5 minutes"

      - alert: HighErrorRate
        expr: rate(voice_errors_total[5m]) > 0.05
        for: 3m
        labels: { severity: critical }
        annotations:
          summary: "Error rate exceeds 5%"

      - alert: CircuitBreakerOpen
        expr: voice_circuit_breaker_trips > 0
        for: 1m
        labels: { severity: critical }
        annotations:
          summary: "Circuit breaker tripped for {{ $labels.service }}"

      - alert: HighConcurrency
        expr: sum(voice_active_calls) / (count(voice_active_calls) * 30) > 0.8
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "Cluster at 80% call capacity — consider scaling up"

      - alert: WorkerDown
        expr: up{job="voice-worker"} == 0
        for: 30s
        labels: { severity: critical }
        annotations:
          summary: "Worker {{ $labels.instance }} is down"

      - alert: TenantCostSpike
        expr: increase(voice_cost_inr_total[1h]) > 500
        for: 0m
        labels: { severity: warning }
        annotations:
          summary: "Tenant {{ $labels.tenant_id }} spent ₹500+ in the last hour"
```

---

## Client Onboarding Portal

### Self-Service Onboarding Flow

```
1. Clinic signs up at portal.yourvoiceai.com
   ├── Business name, contact info
   ├── Choose language (Telugu/Kannada/Hindi/Tamil/English)
   ├── Choose plan ($200/month basic, $300/month pro)
   └── Enter payment details (Razorpay)

2. System provisions:
   ├── Create tenant record in PostgreSQL
   ├── Purchase Telnyx DID (Indian phone number)
   ├── Generate API key
   ├── Create default system prompt (based on business type)
   └── Assign TTS voice

3. Clinic customizes:
   ├── Edit system prompt (business hours, services, pricing)
   ├── Connect Google Calendar (OAuth flow)
   ├── Connect WhatsApp Business
   ├── Test call (call the number, talk to the bot)
   └── Go live

4. Ongoing management:
   ├── View call history and transcripts
   ├── View appointment bookings
   ├── Update business info
   ├── View usage and billing
   └── Download call analytics
```

### Portal Tech Stack

```
Frontend: Next.js or simple HTML + HTMX (fast, minimal JS)
  ├── Landing page
  ├── Dashboard (call history, analytics)
  ├── Settings (prompt editor, voice config)
  └── Billing page

Backend: Same FastAPI server (add routes)
  ├── /portal/signup
  ├── /portal/dashboard
  ├── /portal/settings
  ├── /portal/billing
  └── /portal/test-call

Authentication:
  ├── Phone number + OTP (Telnyx SMS)
  ├── Session cookies
  └── Simple enough for clinic staff
```

---

## Billing Integration

### Razorpay Subscription

```python
# Billing model:
#
# Plans:
#   Basic:  $200/month (₹17,000)
#     - Up to 1,500 calls/month
#     - 1 language
#     - 2 concurrent calls
#     - Email support
#
#   Pro:    $300/month (₹25,000)
#     - Up to 5,000 calls/month
#     - All 5 languages
#     - 5 concurrent calls
#     - Google Calendar + WhatsApp
#     - Priority support
#
#   Enterprise: Custom
#     - Unlimited calls
#     - Custom integrations
#     - Dedicated support
#
# Overage:
#   Basic: ₹10/call beyond limit
#   Pro:   ₹8/call beyond limit
#
# Implementation:
#   - Razorpay Subscriptions API
#   - Webhook for payment success/failure
#   - Auto-suspend tenant on payment failure (3-day grace)
#   - Usage tracking in Redis (calls_this_month counter per tenant)
```

---

## Cost Optimization at Scale

### TTS Caching (Biggest Cost Saver)

```
Observation: 30-40% of TTS calls are for repeated phrases:
  - "నమస్కారం, డాక్టర్ క్లినిక్‌కి కాల్ చేసినందుకు ధన్యవాదాలు" (greeting)
  - "ఒక నిమిషం, చూస్తాను" (checking)
  - "మీ అపాయింట్‌మెంట్ కన్ఫర్మ్ అయింది" (confirmed)
  - "ధన్యవాదాలు, మీకు శుభ దినం" (goodbye)

Solution: Cache TTS audio for common phrases per tenant+voice

Cache key: hash(text + voice_id + language + pace)
Cache store: Redis (for hot phrases, <1MB) + S3 (for all cached audio)

Savings estimate:
  1,000 calls/day × 6 turns × 30% cache hit = 1,800 TTS calls saved
  1,800 × ~500 chars = 900K chars saved/day
  900K / 10K × ₹15 = ₹1,350/day saved = ₹40,500/month saved

Implementation:
  Before calling Sarvam TTS:
    1. Hash the text + voice config
    2. Check Redis cache → hit? Return cached audio
    3. Miss? Call Sarvam TTS, cache result with 7-day TTL
```

### Prompt Caching (LLM Cost Saver)

```
Sarvam-30B pricing:
  Uncached input: ₹2.50/M tokens
  Cached input:   ₹1.50/M tokens (40% savings)

System prompt (~300 tokens) is identical across all turns in a call.
With prompt caching enabled, turns 2+ use cached input pricing.

For a 6-turn call:
  Without caching: 6 × 300 = 1,800 tokens at ₹2.50/M = ₹0.0045
  With caching:    300 + (5 × 300 cached) = 300 at ₹2.50 + 1,500 at ₹1.50 = ₹0.003
  Savings: 33% on LLM input costs
  
Small per-call, but at scale (15K calls/day) it adds up.
```

### Filler Audio CDN

```
Filler audio files are static, small (5-50KB each), and accessed frequently.

Current (Phase 2): Loaded into memory on each worker at startup
Cloud: Serve from Cloudflare R2 (free egress) with memory caching on workers

Cost: Effectively ₹0 (R2 free tier covers this easily)
Benefit: Workers start faster, consistent audio across all workers
```

---

## Security Hardening

### Data Security

```
1. Encryption at rest:
   - PostgreSQL: encrypted storage volumes
   - Redis: encrypted at rest (managed service)
   - S3/R2: encrypted by default
   - Google OAuth tokens: AES-256 encrypted in DB

2. Encryption in transit:
   - All API endpoints: TLS 1.3
   - WebSocket connections: WSS (TLS)
   - Internal services: TLS between workers and Redis/PostgreSQL

3. Data retention:
   - Call recordings: NOT stored (no audio retention — privacy)
   - Transcripts: 90-day retention, then delete
   - Appointment data: 1-year retention
   - Tenant data: retained while active + 30 days after cancellation

4. Access control:
   - Tenant API keys: scoped to their own data only
   - Admin API: separate auth, IP whitelisted
   - Database: separate credentials per service
```

### HIPAA/Healthcare Considerations

```
Since serving hospitals/clinics, healthcare data may be involved:

1. No PHI in logs: Strip patient names, phone numbers from log events
2. Transcript access: Only the tenant (clinic) can view their call transcripts
3. No recording: Audio is processed in real-time, never stored
4. Consent: Greeting includes "This call may be monitored for quality purposes"
5. Data residency: All data stored in India (Mumbai region)
```

---

## Migration Plan: RPi 5 → Cloud

### Zero-Downtime Migration

```
Week 1: Preparation
  ├── Set up cloud infrastructure (VMs, Redis, PostgreSQL, LB)
  ├── Deploy application to cloud workers (same Docker image)
  ├── Migrate PostgreSQL data: pg_dump on Pi → pg_restore on cloud
  ├── Test with synthetic calls on cloud

Week 2: Gradual Migration
  ├── Day 1: Route 10% of new calls to cloud (via Telnyx webhook URL)
  ├── Day 2: Monitor latency, errors, costs
  ├── Day 3: Route 50% to cloud
  ├── Day 4: Route 90% to cloud
  ├── Day 5: Route 100% to cloud
  ├── Keep RPi 5 running as hot standby for 1 week

Week 3: Cleanup
  ├── Decommission RPi 5 from production
  ├── Keep RPi 5 as local dev/test environment
  └── Monitor cloud for 1 week, confirm stability
```

### Telnyx Webhook Migration

```
Migration is as simple as changing the webhook URL in Telnyx dashboard.

RPi 5:  webhook_url = "https://rpi5.yourdomain.com/telnyx/webhook"
Cloud:  webhook_url = "https://cloud.yourdomain.com/telnyx/webhook"

Can be done per phone number (per tenant), enabling gradual migration.
```

---

## Implementation Steps

### Step 1: Cloud Infrastructure Setup (Day 1-5)
- Provision 2 VMs (Mumbai region, ARM64)
- Set up managed PostgreSQL (primary + replica)
- Set up managed Redis (Sentinel)
- Set up Nginx load balancer with WebSocket affinity
- Deploy Docker containers
- Configure DNS and TLS certificates

### Step 2: Stateless Worker Refactor (Day 6-10)
- Move all session state to shared Redis
- Move filler audio to S3/R2 with local cache
- Move tenant config to database (no local files)
- Add worker registration in Redis
- Add health check endpoint
- Test with 2 workers handling the same tenant

### Step 3: Circuit Breakers & Fallbacks (Day 11-15)
- Implement CircuitBreaker class
- Implement ResilientSTT (Sarvam → Deepgram fallback)
- Implement ResilientTTS (Sarvam → Google fallback)
- Implement ResilientLLM (Sarvam-30B → Groq English fallback)
- Test failover scenarios (kill Sarvam connection → verify fallback)

### Step 4: Observability (Day 16-20)
- Add Prometheus metrics to all pipeline stages
- Deploy Prometheus + Grafana (single VM or managed service)
- Create dashboards (Operations, Per-Tenant, Latency)
- Set up alerting rules (Telegram + email)
- Configure centralized logging (Loki or CloudWatch)

### Step 5: Auto-Scaling (Day 21-23)
- Implement scale controller (monitors Redis, triggers cloud API)
- Test scale-up scenario (simulate 50+ concurrent calls)
- Test scale-down scenario (low traffic period)
- Configure min/max worker limits

### Step 6: Client Onboarding Portal (Day 24-30)
- Build signup flow (business registration)
- Build dashboard (call history, analytics)
- Build settings page (prompt editor, voice config, calendar OAuth)
- Build billing page (plan selection, payment history)
- Integrate Razorpay subscriptions

### Step 7: Billing & Usage Tracking (Day 31-33)
- Implement per-tenant usage counters in Redis
- Implement overage detection and alerting
- Implement auto-suspend on payment failure
- Test billing lifecycle (signup → usage → invoice → payment)

### Step 8: TTS Caching (Day 34-35)
- Implement TTS audio cache (Redis + S3)
- Identify common phrases per tenant
- Measure cache hit rate and cost savings
- Tune TTL and cache size

### Step 9: Migration from RPi 5 (Day 36-38)
- Execute migration plan (gradual traffic shift)
- Monitor all metrics during migration
- Verify no degradation in latency or quality

### Step 10: Production Verification (Day 39-42)
- Load test: simulate 200 concurrent calls
- Chaos test: kill a worker mid-call → verify failover
- Latency test: verify P95 < 2000ms measured, P95 < 800ms perceived
- Cost test: verify per-call cost matches projections
- SLA test: measure uptime over 1 week

---

## Cost Summary at Scale

### Monthly Infrastructure Cost

```
Component                     | 200 tenants | 500 tenants
───────────────────────────────────────────────────────────
Compute (2-5 workers)         | ₹15,000     | ₹35,000
PostgreSQL (managed)          | ₹12,000     | ₹20,000
Redis (managed sentinel)      | ₹5,000      | ₹10,000
Load balancer                 | ₹2,000      | ₹2,000
Object storage (S3/R2)        | ₹500        | ₹1,000
Monitoring (Grafana Cloud)    | ₹3,000      | ₹5,000
DNS + TLS                     | ₹500        | ₹500
───────────────────────────────────────────────────────────
Total infrastructure          | ₹38,000     | ₹73,500
```

### Monthly Revenue vs Cost

```
                              | 200 tenants         | 500 tenants
───────────────────────────────────────────────────────────────────
Revenue ($200/mo × tenants)   | ₹34,00,000          | ₹85,00,000
API costs (₹5/call × calls)  | ₹7,50,000 (5K/day)  | ₹22,50,000 (15K/day)
Infrastructure                | ₹38,000             | ₹73,500
───────────────────────────────────────────────────────────────────
Gross Profit                  | ₹26,12,000          | ₹61,76,500
Margin                        | 76.8%               | 72.7%
```

---

## Phase 3 Deliverables Checklist

- [ ] Cloud infrastructure provisioned (Mumbai region)
- [ ] Stateless workers with shared Redis/PostgreSQL
- [ ] Nginx load balancer with WebSocket affinity
- [ ] Circuit breakers for all external services
- [ ] Fallback providers (Deepgram STT, Google TTS, Groq LLM)
- [ ] Prometheus + Grafana monitoring
- [ ] Alerting (latency, errors, circuit breakers, capacity)
- [ ] Centralized logging
- [ ] Auto-scaling (2-10 workers)
- [ ] Client onboarding portal
- [ ] Razorpay billing integration
- [ ] TTS caching for cost optimization
- [ ] Zero-downtime migration from RPi 5
- [ ] Load tested at 200 concurrent calls
- [ ] Chaos tested (worker failure + recovery)
- [ ] P95 latency < 2000ms measured, < 800ms perceived
- [ ] 99.9% uptime verified over 1 week
- [ ] Security hardened (encryption, access control, data retention)
- [ ] 200+ tenants onboarded and active
