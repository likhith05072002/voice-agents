# Phase 2: Multi-Tenant Production — 50-100 Clients on RPi 5

## Goal
Transform the single-tenant MVP into a production multi-tenant SaaS serving 50-100 dental clinics and hospitals. Each clinic gets its own AI receptionist with custom voice, language, prompts, and business tool integrations. All running on a single Raspberry Pi 5.

**Timeline**: 4-5 weeks (after Phase 1)
**Deliverable**: Production system serving 50-100 clinics on RPi 5, handling ~500 calls/day total

**Prerequisites**: Phase 1 complete — single call works end-to-end with acceptable latency

---

## System Design: Multi-Tenant Architecture

### Tenant Isolation Model

```
                    ┌─────────────────────────────────┐
                    │          LOAD BALANCER           │
                    │    (by Telnyx phone number)      │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │        FastAPI Server            │
                    │       (single process)           │
                    │                                  │
                    │  ┌──────────────────────────┐   │
                    │  │    TENANT REGISTRY        │   │
                    │  │                          │   │
                    │  │  phone_number → tenant   │   │
                    │  │  +91-9876543210 → T001   │   │
                    │  │  +91-9876543211 → T002   │   │
                    │  │  ...                     │   │
                    │  └──────────────────────────┘   │
                    │                                  │
                    │  On incoming call:                │
                    │  1. Look up phone number          │
                    │  2. Load tenant config            │
                    │  3. Create session with           │
                    │     tenant's voice, language,     │
                    │     system prompt, tools          │
                    │  4. Run pipeline as Phase 1       │
                    │     but with tenant context       │
                    └──────────────────────────────────┘
```

### Data Model: `src/tenant/models.py`

```python
# Tenant (Clinic) — stored in PostgreSQL
#
# class Tenant:
#     id: UUID
#     name: str                    # "Dr. Reddy's Dental Clinic"
#     phone_number: str            # Telnyx DID assigned to this tenant
#     
#     # Voice Agent Config
#     language: str                # "te-IN", "kn-IN", "hi-IN", "ta-IN", "en-IN"
#     voice_id: str                # Sarvam TTS voice name
#     system_prompt: str           # Agent persona and clinic info
#     greeting_message: str        # First thing agent says when call starts
#     
#     # Business Config
#     business_hours: dict         # {"mon": "09:00-18:00", "tue": "09:00-18:00", ...}
#     timezone: str                # "Asia/Kolkata"
#     google_calendar_id: str | None
#     google_credentials: dict | None  # OAuth tokens (encrypted)
#     whatsapp_number: str | None  # For sending confirmations
#     
#     # Tools Config
#     tools_enabled: list[str]     # ["calendar", "sms", "whatsapp"]
#     
#     # Metadata
#     plan: str                    # "basic", "pro" (for future billing tiers)
#     is_active: bool
#     created_at: datetime
#     updated_at: datetime
#
#
# Call — logged per call
#
# class Call:
#     id: UUID
#     tenant_id: UUID              # FK → Tenant
#     caller_number: str
#     direction: str               # "inbound" | "outbound"
#     started_at: datetime
#     ended_at: datetime | None
#     duration_seconds: int | None
#     language_detected: str
#     
#     # Outcome
#     outcome: str                 # "appointment_booked", "info_provided", 
#                                  # "transferred", "voicemail", "missed"
#     outcome_details: dict | None # {"appointment_date": "2026-07-01", "time": "15:00"}
#     
#     # Conversation
#     transcript: list[dict]       # [{"role": "user", "text": "..."}, ...]
#     turns: int
#     
#     # Cost tracking
#     stt_seconds: float
#     tts_characters: int
#     llm_input_tokens: int
#     llm_output_tokens: int
#     estimated_cost_inr: float
#     
#     # Quality
#     latency_avg_ms: float        # Average e2e latency across turns
#     latency_p95_ms: float
#
#
# Appointment — created by business tools
#
# class Appointment:
#     id: UUID
#     tenant_id: UUID
#     call_id: UUID | None
#     patient_name: str
#     patient_phone: str
#     appointment_date: date
#     appointment_time: time
#     duration_minutes: int        # Default 30
#     service: str                 # "dental_checkup", "root_canal", etc.
#     status: str                  # "confirmed", "cancelled", "completed"
#     google_event_id: str | None
#     created_at: datetime
```

---

## Database Schema (PostgreSQL)

```sql
-- Run on RPi 5 with PostgreSQL 16

CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    
    -- Voice config
    language VARCHAR(10) NOT NULL DEFAULT 'te-IN',
    voice_id VARCHAR(50) NOT NULL DEFAULT 'meera',
    system_prompt TEXT NOT NULL,
    greeting_message TEXT NOT NULL,
    
    -- Business config
    business_hours JSONB NOT NULL DEFAULT '{}',
    timezone VARCHAR(50) NOT NULL DEFAULT 'Asia/Kolkata',
    google_calendar_id VARCHAR(255),
    google_credentials_encrypted BYTEA,
    whatsapp_number VARCHAR(20),
    
    -- Tools
    tools_enabled TEXT[] NOT NULL DEFAULT '{}',
    
    -- Meta
    plan VARCHAR(20) NOT NULL DEFAULT 'basic',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    caller_number VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL DEFAULT 'inbound',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    language_detected VARCHAR(10),
    
    -- Outcome
    outcome VARCHAR(50),
    outcome_details JSONB,
    
    -- Conversation
    transcript JSONB NOT NULL DEFAULT '[]',
    turns INTEGER NOT NULL DEFAULT 0,
    
    -- Cost
    stt_seconds REAL NOT NULL DEFAULT 0,
    tts_characters INTEGER NOT NULL DEFAULT 0,
    llm_input_tokens INTEGER NOT NULL DEFAULT 0,
    llm_output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_inr REAL NOT NULL DEFAULT 0,
    
    -- Quality
    latency_avg_ms REAL,
    latency_p95_ms REAL
);

CREATE INDEX idx_calls_tenant ON calls(tenant_id);
CREATE INDEX idx_calls_started ON calls(started_at);
CREATE INDEX idx_calls_tenant_date ON calls(tenant_id, started_at);

CREATE TABLE appointments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    call_id UUID REFERENCES calls(id),
    patient_name VARCHAR(255) NOT NULL,
    patient_phone VARCHAR(20) NOT NULL,
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 30,
    service VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'confirmed',
    google_event_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_appointments_tenant_date ON appointments(tenant_id, appointment_date);
```

---

## Tenant Management API

### Endpoints: `src/api/rest.py`

```
# Tenant CRUD
POST   /api/tenants                  # Create new clinic
GET    /api/tenants                  # List all clinics
GET    /api/tenants/{id}             # Get clinic details
PUT    /api/tenants/{id}             # Update clinic config
DELETE /api/tenants/{id}             # Deactivate clinic

# Tenant Config
PUT    /api/tenants/{id}/prompt      # Update system prompt
PUT    /api/tenants/{id}/voice       # Change TTS voice
PUT    /api/tenants/{id}/tools       # Enable/disable business tools
POST   /api/tenants/{id}/calendar    # Connect Google Calendar (OAuth flow)

# Call History
GET    /api/tenants/{id}/calls       # List calls (paginated, filterable)
GET    /api/calls/{call_id}          # Get call details + transcript

# Appointments
GET    /api/tenants/{id}/appointments # List appointments
POST   /api/tenants/{id}/appointments # Manual appointment creation

# System
GET    /api/health                   # Health check
GET    /api/metrics                  # Basic metrics (active calls, etc.)
```

### API Authentication
```
Phase 2 (simple):
  - API key per tenant (passed as X-API-Key header)
  - Admin master key for tenant CRUD
  - No public-facing auth yet (admin-only API)

Phase 3 (production):
  - JWT tokens
  - OAuth2 for Google Calendar
  - Rate limiting per tenant
```

---

## Business Tools Integration

### Architecture: LLM Function Calling

```
LLM generates response with tool calls:

User: "రేపు 3 గంటలకు అపాయింట్‌మెంట్ కావాలి"

LLM response (streaming):
  "ఒక నిమిషం, చూస్తాను..."  ←── text goes to TTS immediately
  [tool_call: check_calendar(date="2026-07-02", time="15:00")]

Pipeline:
  1. Sentence "ఒక నిమిషం, చూస్తాను..." → TTS → caller hears this
  2. Tool call executes in parallel
  3. Tool result feeds back to LLM
  4. LLM generates: "3 గంటలకు స్లాట్ అందుబాటులో ఉంది. బుక్ చేయమంటారా?"
  5. → TTS → caller hears confirmation

Timeline:
  [User speaks]  [Filler]  [TTS: "oka nimisham..."]  [Calendar API call]  [TTS: "slot available..."]
                                                      ├── 200-500ms ──┤
                                                      (masked by TTS playback)
```

### Tool Definitions for LLM

```python
# src/tools/executor.py
#
# TOOLS = [
#     {
#         "type": "function",
#         "function": {
#             "name": "check_calendar_availability",
#             "description": "Check if a time slot is available for appointment",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "date": {"type": "string", "description": "Date in YYYY-MM-DD"},
#                     "time": {"type": "string", "description": "Time in HH:MM (24hr)"},
#                     "duration_minutes": {"type": "integer", "default": 30}
#                 },
#                 "required": ["date", "time"]
#             }
#         }
#     },
#     {
#         "type": "function",
#         "function": {
#             "name": "book_appointment",
#             "description": "Book an appointment for a patient",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "patient_name": {"type": "string"},
#                     "patient_phone": {"type": "string"},
#                     "date": {"type": "string"},
#                     "time": {"type": "string"},
#                     "service": {"type": "string", "description": "dental_checkup, filling, root_canal, etc."}
#                 },
#                 "required": ["patient_name", "date", "time"]
#             }
#         }
#     },
#     {
#         "type": "function",
#         "function": {
#             "name": "send_confirmation",
#             "description": "Send appointment confirmation via WhatsApp or SMS",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "phone": {"type": "string"},
#                     "message": {"type": "string"},
#                     "channel": {"type": "string", "enum": ["whatsapp", "sms"]}
#                 },
#                 "required": ["phone", "message", "channel"]
#             }
#         }
#     },
#     {
#         "type": "function",
#         "function": {
#             "name": "get_clinic_info",
#             "description": "Get clinic information (hours, services, location, pricing)",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "query": {"type": "string", "description": "What info is needed: hours, services, location, pricing"}
#                 },
#                 "required": ["query"]
#             }
#         }
#     }
# ]
```

### Google Calendar Integration: `src/tools/calendar.py`

```python
# class GoogleCalendarTool:
#     """
#     Google Calendar integration for appointment management.
#     Uses Google Calendar API v3.
#     
#     Setup per tenant:
#     1. Admin connects Google account via OAuth2
#     2. We store refresh token (encrypted) in tenant record
#     3. Access token refreshed automatically
#     """
#
#     async def check_availability(
#         self, tenant: Tenant, date: str, time: str, duration: int = 30
#     ) -> dict:
#         """Check if time slot is free"""
#         # Query Google Calendar freebusy API
#         # Returns {"available": True/False, "next_available": "15:30"}
#
#     async def create_event(
#         self, tenant: Tenant, appointment: dict
#     ) -> str:
#         """Create calendar event, return event_id"""
#         # Create event on tenant's calendar
#         # Title: "Dental Appointment - {patient_name}"
#         # Description: service type, patient phone
#
#     async def cancel_event(
#         self, tenant: Tenant, event_id: str
#     ) -> bool:
#         """Cancel/delete calendar event"""
```

### WhatsApp Integration: `src/tools/whatsapp.py`

```python
# class WhatsAppTool:
#     """
#     Send appointment confirmations via WhatsApp Business API.
#     Uses WhatsApp Cloud API (Meta Business Platform).
#     
#     Message templates (pre-approved):
#     - appointment_confirmation: "Your appointment at {clinic} is confirmed for {date} at {time}"
#     - appointment_reminder: "Reminder: Your appointment at {clinic} is tomorrow at {time}"
#     - appointment_cancelled: "Your appointment at {clinic} has been cancelled"
#     """
#
#     async def send_confirmation(
#         self, phone: str, clinic_name: str, date: str, time: str
#     ) -> bool:
#         """Send appointment confirmation via WhatsApp template"""
#         # POST to WhatsApp Cloud API
#         # Uses pre-approved template
#         # Returns True on success
```

### SMS Fallback: `src/tools/sms.py`

```python
# class SMSTool:
#     """
#     SMS via Telnyx API for patients without WhatsApp.
#     """
#
#     async def send_sms(self, to: str, message: str, from_number: str) -> bool:
#         """Send SMS via Telnyx"""
#         # POST https://api.telnyx.com/v2/messages
```

---

## Session Management with Redis

### Why Redis (even on RPi 5)

```
Without Redis:
  - Sessions stored in Python dict (memory)
  - Server restart = all active calls drop with no recovery
  - No visibility into active sessions
  - Can't scale to multiple workers later

With Redis:
  - Sessions survive server restart
  - Active call monitoring
  - Session metadata queryable
  - Foundation for Phase 3 multi-worker scaling
  - Redis on RPi 5: ~50MB RAM, negligible CPU
```

### Redis Data Model: `src/session/manager.py`

```python
# Session state in Redis:
#
# voice:session:{call_id} (HASH, TTL: 3600s)
#   tenant_id: UUID
#   caller_number: str
#   language: str
#   state: "listening" | "processing" | "speaking"
#   started_at: ISO timestamp
#   last_activity: ISO timestamp
#   turns: int
#
# voice:history:{call_id} (LIST, max 20 items)
#   JSON-encoded conversation turns
#   [{"role": "user", "text": "...", "ts": 1234567890}, ...]
#
# voice:active (SET)
#   Set of active call_ids (for monitoring)
#
# voice:tenant:{tenant_id}:active_calls (SET)
#   Active calls per tenant (for concurrent call limiting)
#
# voice:metrics:daily:{YYYY-MM-DD} (HASH)
#   total_calls: int
#   total_duration: int (seconds)
#   total_cost: float (INR)
#   calls_by_tenant: JSON
```

### Concurrent Call Limiting

```python
# Each RPi 5 can handle ~15-20 concurrent calls (after Phase 1 optimizations)
# Each tenant (clinic) probably gets 1-3 concurrent calls max
#
# Limits:
#   Global max concurrent: 15 (configurable, based on Pi capacity)
#   Per-tenant max concurrent: 3 (basic plan), 5 (pro plan)
#
# On new call:
#   1. Check global concurrent < MAX_GLOBAL
#   2. Check tenant concurrent < tenant.max_concurrent
#   3. If either exceeded:
#      Play pre-recorded: "All lines are currently busy. Please try again shortly."
#      Hang up gracefully
```

---

## All 5 Languages

### Language Detection and Routing

```
On incoming call:
  1. Look up tenant → get tenant.language (configured default)
  2. Start STT with tenant's language
  3. If STT detects different language → switch dynamically
  
Language switching mid-call:
  - Patient starts in Telugu, switches to English mid-sentence
  - Sarvam STT codemix mode handles this
  - LLM system prompt includes: "If the patient switches to English, respond in English"
  - TTS voice stays the same (Sarvam voices handle multilingual)
```

### Filler Audio for All Languages

```
Generate fillers for all 5 languages at setup.
Each language has ~10-15 fillers across categories.

assets/fillers/
├── te-IN/   (Telugu)
│   ├── acknowledge/  (అవును, సరే, ఔను)
│   ├── thinking/     (హ్మ్, చూస్తాను, ఒక నిమిషం)
│   └── greeting/     (నమస్కారం)
├── kn-IN/   (Kannada)
│   ├── acknowledge/  (ಹೌದು, ಸರಿ, ಆಯ್ತು)
│   ├── thinking/     (ಹ್ಮ್, ನೋಡ್ತೀನಿ, ಒಂದು ನಿಮಿಷ)
│   └── greeting/     (ನಮಸ್ಕಾರ)
├── hi-IN/   (Hindi)
│   ├── acknowledge/  (हाँ, जी, अच्छा)
│   ├── thinking/     (हम्म, देखती हूँ, एक मिनट)
│   └── greeting/     (नमस्ते)
├── ta-IN/   (Tamil)
│   ├── acknowledge/  (ஆமா, சரி, ஓகே)
│   ├── thinking/     (ம்ம், பார்க்கிறேன், ஒரு நிமிடம்)
│   └── greeting/     (வணக்கம்)
└── en-IN/   (Indian English)
    ├── acknowledge/  (sure, right, okay)
    ├── thinking/     (hmm, let me check, one moment)
    └── greeting/     (hello, good morning)
```

---

## RPi 5 Capacity Planning

### Resource Budget (16GB RAM)

```
Component           | RAM per unit  | Count        | Total RAM
─────────────────────────────────────────────────────────────
Python process      | 100MB         | 1            | 100MB
FastAPI + uvicorn   | 50MB          | 1            | 50MB
Redis               | 50MB          | 1            | 50MB
PostgreSQL          | 200MB         | 1            | 200MB
Silero VAD model    | 30MB          | 1 (shared)   | 30MB
Active call session | 2MB           | 20 max       | 40MB
Filler audio cache  | 5MB           | 1            | 5MB
OS + system         | 500MB         | 1            | 500MB
─────────────────────────────────────────────────────────────
Total                                              | ~975MB
Available                                          | ~15GB free
```

### CPU Budget (4 cores @ 2.4GHz)

```
Component                | CPU per call  | 20 calls | Notes
──────────────────────────────────────────────────────────────
Audio resampling         | 0.05 core     | 1.0 core | After optimization
VAD processing           | 0.02 core     | 0.4 core | Silero is lightweight
WebSocket management     | 0.01 core     | 0.2 core | Mostly I/O wait
Base64 encode/decode     | 0.01 core     | 0.2 core | Small chunks
μ-law conversion         | 0.01 core     | 0.2 core | audioop (C impl)
──────────────────────────────────────────────────────────────
Total                                    | 2.0 cores | 50% utilization
```

### Network Budget

```
Per call bandwidth:
  Inbound (from Telnyx):  8kHz × 16bit = 128 kbps = 16 KB/s
  Outbound (to Telnyx):   8kHz × 16bit = 128 kbps = 16 KB/s
  To Sarvam STT:          16kHz × 16bit = 256 kbps = 32 KB/s
  From Sarvam TTS:        8kHz × 16bit = 128 kbps = 16 KB/s
  LLM API:                ~2 KB/s (text only)
  ──────────────────────────────────────────────────
  Per call total:         ~82 KB/s = ~0.66 Mbps

20 concurrent calls:      ~1.6 MB/s = ~13 Mbps

Typical home broadband:   50-100 Mbps → plenty of headroom
RPi 5 Gigabit Ethernet:   1000 Mbps → no bottleneck
```

### Capacity Summary

```
Raspberry Pi 5 (16GB) can handle:
  ├── 20 concurrent calls (conservative estimate)
  ├── 500 calls/day (at 3 min avg, spread across 10 hours)
  ├── 50-100 tenants (most clinics get 5-20 calls/day)
  ├── 50% CPU headroom for spikes
  └── 15GB RAM headroom

Bottleneck: concurrent calls during peak hours (10-12 AM typically)
  50 clinics × 0.3 concurrent avg = 15 concurrent → within budget
  100 clinics × 0.3 concurrent avg = 30 concurrent → at limit, consider cloud
```

---

## Monitoring on RPi 5

### Basic Monitoring (No Prometheus yet — too heavy for Phase 2)

```python
# src/api/rest.py → GET /api/metrics
#
# Response:
# {
#     "active_calls": 5,
#     "active_calls_by_tenant": {"T001": 2, "T002": 1, "T003": 2},
#     "total_calls_today": 47,
#     "avg_latency_ms": 850,
#     "cpu_percent": 45.2,
#     "memory_percent": 12.3,
#     "disk_percent": 23.1,
#     "uptime_hours": 72.5,
#     "errors_last_hour": 0
# }
#
# Use psutil for system metrics on RPi 5
# Log structured events with structlog
# Daily cost summary in Redis
```

### Alerting (Simple)

```python
# Lightweight alerting for RPi 5:
#
# 1. CPU > 80% for 5 minutes → log.critical + optional Telegram bot alert
# 2. Active calls > 18 → log.warning (approaching limit)
# 3. STT/TTS error rate > 5% → log.critical
# 4. No calls for 30 minutes during business hours → log.warning
#
# Implementation: Background asyncio task checking metrics every 60s
# Alert channel: Telegram bot (simple HTTP POST, no extra infra)
```

---

## Implementation Steps

### Step 1: Database Setup (Day 1-2)
- Install PostgreSQL on RPi 5
- Create database schema (tables above)
- Set up Alembic for migrations
- Implement SQLAlchemy async models
- Implement repositories (CRUD operations)
- Seed with 2-3 test tenants

### Step 2: Redis Setup (Day 3)
- Install Redis on RPi 5
- Implement session manager with Redis
- Implement concurrent call limiting
- Test session persistence across server restarts

### Step 3: Multi-Tenant Routing (Day 4-6)
- Tenant registry (phone number → tenant lookup)
- Modify orchestrator to accept tenant config
- Dynamic system prompt per tenant
- Dynamic voice/language per tenant
- Test with 3 different tenant configs

### Step 4: Tenant Management API (Day 7-9)
- REST endpoints for tenant CRUD
- API key authentication (simple)
- Input validation (pydantic models)
- Test all endpoints

### Step 5: All 5 Languages (Day 10-12)
- Generate fillers for all languages
- Test STT/TTS/LLM in each language
- Language detection and routing
- Code-mix handling verification

### Step 6: Google Calendar Integration (Day 13-16)
- OAuth2 flow for Google Calendar
- Check availability tool
- Book appointment tool
- Cancel appointment tool
- Test full booking flow via voice

### Step 7: WhatsApp + SMS (Day 17-19)
- WhatsApp Business API setup
- SMS via Telnyx
- Send confirmation after booking
- Send reminder (background cron task)

### Step 8: Call Logging & Cost Tracking (Day 20-22)
- Log every call to PostgreSQL
- Track STT seconds, TTS chars, LLM tokens per call
- Calculate estimated cost per call
- Daily summary metrics in Redis

### Step 9: Capacity Testing (Day 23-25)
- Load test with simulated concurrent calls
- Measure CPU/RAM/network under load
- Identify actual concurrent call capacity
- Tune worker settings if needed

### Step 10: Production Hardening (Day 26-30)
- Systemd service file for auto-start on RPi boot
- Log rotation (logrotate config)
- Database backups (daily pg_dump to external storage)
- Redis persistence (RDB snapshots)
- Basic Telegram alerting
- Graceful shutdown (finish active calls before restart)

---

## Phase 2 Deliverables Checklist

- [ ] PostgreSQL with tenant/call/appointment tables
- [ ] Redis session management
- [ ] Multi-tenant routing (phone number → tenant)
- [ ] Tenant CRUD API with auth
- [ ] All 5 languages working (Telugu, Kannada, Hindi, Tamil, English)
- [ ] Google Calendar integration (check/book/cancel)
- [ ] WhatsApp appointment confirmation
- [ ] SMS fallback
- [ ] LLM function calling for business tools
- [ ] Call logging with cost tracking
- [ ] Concurrent call limiting (global + per-tenant)
- [ ] Capacity tested with 20 concurrent calls
- [ ] Systemd auto-start on RPi boot
- [ ] Database backups
- [ ] Basic alerting (Telegram)
- [ ] 50 tenant configs loaded and working

## Phase 2 Does NOT Include
- Horizontal scaling (single RPi 5 only)
- Circuit breakers / fallback providers
- Client-facing dashboard or onboarding portal
- Prometheus/Grafana monitoring
- Billing/payment integration
- SLA guarantees
- Multi-region deployment
- Auto-scaling
