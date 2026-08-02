from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Sarvam AI
    sarvam_api_key: str
    # OpenRouter (search-grounded models) — powers the web_search tool.
    openrouter_api_key: str = ""

    # Sarvam STT
    sarvam_stt_ws_url: str = "wss://api.sarvam.ai/speech-to-text/ws"
    sarvam_stt_model: str = "saaras:v3"

    # Sarvam TTS
    sarvam_tts_ws_url: str = "wss://api.sarvam.ai/text-to-speech/ws"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_voice: str = "anushka"

    # Inworld TTS — powers "Clone your voice" (English/Hindi) on the demo
    # widget. Empty key = cloning endpoints return 503, Sarvam path untouched.
    inworld_api_key: str = ""
    inworld_tts_model: str = "inworld-tts-1.5-mini"

    # Private demo page served at /demo when this points at a local HTML file
    # (e.g. private/demo.html — the private/ dir is untracked). Empty (the
    # default) = 404: client-specific demos must never ship on the public
    # site — they expose an unauthenticated outbound-dial box and burn TTS
    # credits.
    private_demo_file: str = ""

    # ElevenLabs TTS — optional provider on the private demo page (dropdown +
    # voice-id bar). Default voice is Sarah (premade: works on the free API
    # tier — LIBRARY voices 402 until the ElevenLabs plan is paid).
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
    elevenlabs_tts_model: str = "eleven_flash_v2_5"

    # Sarvam LLM
    sarvam_llm_base_url: str = "https://api.sarvam.ai/v1"
    sarvam_llm_model: str = "sarvam-30b"
    # CRITICAL: None disables reasoning mode. With reasoning, TTFT is 8s+. Without, TTFT is ~340ms.
    sarvam_llm_reasoning_effort: str | None = None

    # EnableX (India telephony)
    enablex_app_id: str = ""
    enablex_app_key: str = ""

    # Telnyx (International telephony)
    telnyx_api_key: str = ""
    # Outbound: Call Control App connection id + default caller-ID.
    telnyx_connection_id: str = ""
    telnyx_from_number: str = ""
    # /outbound is billable: protect it. When set, requests must send x-api-key.
    outbound_api_key: str = ""
    max_outbound_per_min: int = 0   # 0 = unlimited

    # AI voice tester over a REAL call: the tester dials from its own number to
    # the main agent's number (both on the same Telnyx Call Control app).
    tester_from_number: str = ""    # e.g. +18722698117
    main_agent_number: str = ""     # e.g. +15572046319
    # Let inbound calls RING this long before the agent answers (0 = instant
    # robot pickup). A couple seconds feels more natural to human callers and
    # makes the ring phase visible/audible in tests.
    answer_delay_ms: int = 0
    # Telnyx webhook signing public key (base64 Ed25519). When set, inbound
    # webhooks are verified and rejected if the signature is invalid.
    telnyx_public_key: str = ""

    # OpenRouter (LLM fallback)
    openrouter_api_key: str = ""

    # OpenAI (LLM fallback)
    openai_api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "debug"
    default_language: str = "te-IN"
    public_url: str = ""

    # ─── Agent persona (override per deployment via env) ───
    system_prompt: str = (
        "You are Lakshmi, AI assistant at Nama Srinivasa Jewellery, Banjara Hills, Hyderabad. "
        "CRITICAL: Reply in the SAME language the customer uses. "
        "Telugu->Telugu. English->English. Hindi->Hindi. Kannada->Kannada. "
        "Keep answers SHORT: 1-2 sentences max. "
        "Shop: 10AM-9PM daily. For ANY gold or silver price (any karat, any "
        "quantity in grams) ALWAYS call the get_metal_price tool and quote its "
        "numbers; NEVER quote from memory or output a placeholder like "
        "'[today's price]'. "
        "Services: gold, silver, diamond jewellery, old gold exchange, hallmark."
    )
    greeting_text: str = (
        "నమస్కారం! నమ శ్రీనివాస జ్యూవెల్లరీ కి స్వాగతం. మీకు ఏమి సహాయం చేయగలను?"
    )

    # ─── Turn engine tunables ───
    # Play a pre-recorded filler ("hmm") while the LLM thinks. Off by default:
    # the bundled filler assets need format verification on a live call first.
    enable_fillers: bool = False
    # Milliseconds of audio STT buffers before sending. Lower = snappier
    # VAD/barge-in, at the cost of more websocket messages.
    stt_buffer_ms: int = 100
    # Sarvam STT VAD sensitivity. True fires speech-onset sooner (snappier
    # barge-in) but triggers more often on noise/breath. Turn off if the agent
    # stutters/self-interrupts in a noisy environment.
    stt_high_vad_sensitivity: bool = True
    # Inbound DSP: noise gate + AGC clean/level the caller audio before STT.
    # Disable if STT accuracy drops on a live call.
    enable_input_dsp: bool = True
    # NLMS echo canceller — removes the agent's own voice leaking back (self
    # interruptions). OFF by default: the echo delay needs live-call tuning.
    enable_echo_cancellation: bool = False
    # Spectral noise suppression (+8dB SNR on steady noise, 0.06ms/frame).
    # OFF by default until validated against live Sarvam STT accuracy.
    enable_noise_suppression: bool = False

    # Eagerness preset: "cautious" | "balanced" | "eager" drive the barge-in /
    # endpointing knobs as a single dial. Anything else ("custom") uses the
    # individual BARGEIN_* / endpointing values below.
    eagerness: str = "balanced"

    # ─── Barge-in guard stack ───
    # Minimum words before a candidate counts as a real interruption (filters
    # clicks / stray STT tokens). Backchannels are filtered separately.
    bargein_min_words: int = 2
    # If VAD fired but no real transcript lands within this window, resume
    # playback (false-interruption recovery).
    bargein_false_timeout_ms: int = 1200
    # Faster recovery: once the caller's speech blip ENDS with no transcript,
    # wait only this long before resuming (vs the full false_timeout). This is
    # the main anti-stutter lever for coughs/breaths/background noise.
    bargein_speech_end_grace_ms: int = 300
    # Pause-then-resume recovery for backchannels / noise. When False, the agent
    # simply keeps talking until a confirmed interruption.
    bargein_enable_recovery: bool = True
    # Instant pause on VAD (pre-transcript). Superb on echo-free paths (web);
    # on PSTN legs with strong uncancelled line echo the false pauses truncate
    # answers — keep False for telephony until proper AEC is deployed.
    bargein_instant_pause: bool = False

    # Tool/function calling. When on, the engine runs a tool-decision pass each
    # turn (the demo registry: gold price + shop hours). Off by default.
    # Tools on by default: the demo registry now carries the LIVE gold-price
    # feed, and a price-from-memory answer is worse than a tool round-trip.
    enable_tools: bool = True

    # RAG: inject relevant shop-knowledge snippets into the system context per
    # turn (demo knowledge base). Off by default — adds prompt tokens.
    enable_rag: bool = False

    # Safety guards: flag prompt-injection in the transcript and block any spoken
    # sentence that leaks the system prompt (replaced with a refusal). On by
    # default — the leak guard is high-precision.
    enable_safety: bool = True

    # Persistence: durable call records (transcript + latency + outcome).
    enable_persistence: bool = True
    calls_db_path: str = "calls.db"
    # Post-call LLM summary (summary + outcome + sentiment). Opt-in (LLM cost).
    enable_call_summary: bool = False

    # Multi-tenant: path to a JSON array of AgentConfig objects (one per
    # business, with their phone_numbers). Empty -> single default agent built
    # from the persona settings below.
    agents_file: str = ""
    # Runtime agent CRUD: SQLite store + admin API key (required to mutate).
    agents_db_path: str = "agents.db"
    admin_api_key: str = ""

    # ─── Accounts mode (Phase 4: auth + workspaces) ───
    # Postgres DSN. When set, the app runs in ACCOUNTS mode: users/workspaces/
    # sessions live in Postgres, agents+calls persist there too (tenant-scoped),
    # and the console/create APIs require a signed-in user. Empty -> legacy
    # single-trust SQLite mode (exactly the old behavior; prod-safe default).
    database_url: str = ""
    # Google OAuth (authorization-code flow). Redirect URI is
    # {PUBLIC_URL|request origin}/auth/google/callback — whitelist it in the
    # Google Cloud console.
    google_client_id: str = ""
    google_client_secret: str = ""
    # Signs the OAuth state (CSRF). Required in accounts mode. Generate:
    # python -c "import secrets; print(secrets.token_urlsafe(48))"
    session_secret: str = ""
    # Base URL for the Google OAuth callback. Leave empty in prod (falls back to
    # PUBLIC_URL = the real domain). Set to http://localhost:8001 in local dev
    # WHEN PUBLIC_URL is pointed at a telephony tunnel, so sign-in keeps using
    # the localhost callback that's registered in Google.
    oauth_redirect_base: str = ""
    # LOCAL DEV ONLY: /auth/dev-login signs in without Google. NEVER in prod.
    dev_login_enabled: bool = False
    # Platform operators: CSV of emails whose accounts see /admin (full
    # cross-tenant monitoring). Everyone else gets 404s on /admin/*.
    admin_emails: str = ""
    # CSV of exact origins allowed to send credentialed requests. When set,
    # CORS switches from "*" to this allowlist WITH credentials (browsers
    # forbid wildcard+credentials). e.g. "http://localhost:5173"
    cors_allow_origins: str = ""
    # Agents callable via the public web demo WITHOUT auth (the landing orb).
    public_demo_agents: str = "sonuslabs"

    # ─── Billing (prepaid credits; INTEGER PAISE everywhere) ───
    # Platform rate charged per minute of call time (350 = ₹3.5/min, the PAYG
    # price). Billing is per second at rate/60, rounded up.
    rate_paise_per_min: int = 350
    # Free trial granted once at first sign-in, in minutes of talk time.
    trial_minutes: int = 15
    # Razorpay (top-ups). Empty -> "payments not configured"; dev top-up is
    # available when DEV_LOGIN_ENABLED for local testing.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # ─── Limits (abuse / overload protection) ───
    max_concurrent_sessions: int = 100   # reject new media streams past this
    max_turns_per_min: int = 0           # per-call turn cap (0 = disabled)

    # ─── Session / silence handling ───
    enable_idle: bool = True
    idle_reprompt_ms: int = 20000    # re-prompt a silent caller after this
    idle_hangup_ms: int = 45000      # end the call after this much silence
    idle_reprompt_text: str = "Are you still there?"
    # Hard cap on a single browser web-call (the sonuslabs.ai demo). 0 disables.
    # Enforced server-side so it cannot be bypassed by editing the client.
    web_call_max_seconds: int = 180

    # ─── Landing-page "call me" demo (public, unauthenticated) ───
    # Every click DIALS A REAL PHONE on our carrier account, so this is the
    # one endpoint an abuser can turn directly into our money (toll fraud,
    # harassment dialing). OFF unless explicitly enabled, and every limit
    # below is a spend ceiling, not a nicety.
    demo_call_enabled: bool = False
    # E.164 country prefixes we will dial. Anything else is refused — this is
    # the single most important control: premium-rate and satellite ranges are
    # where dial-fraud pays out.
    demo_call_allowed_prefixes: str = "+91,+1"
    demo_call_max_seconds: int = 180          # hard hangup (the 3-minute cap)
    demo_call_warn_seconds: int = 15          # agent announces the ending here
    demo_call_ip_cooldown_s: int = 600        # one call per IP per 10 min
    demo_call_number_cooldown_s: int = 600    # ...and per destination number
    demo_call_per_ip_daily: int = 3
    demo_call_per_number_daily: int = 3
    demo_call_global_hourly: int = 20         # platform-wide spend ceiling
    # Switch the TTS voice language to match the language the caller is speaking.
    enable_language_switch: bool = True

    # ─── Semantic endpointing (opt-in) ───
    # Hold a final transcript that looks unfinished (trailing conjunction /
    # dangling comma) and merge it with the caller's next final, so the agent
    # doesn't answer half a sentence. OFF by default — needs live A/B validation
    # before it changes turn-taking in production.
    enable_smart_endpointing: bool = False
    # How long to wait for the continuation before firing the buffered fragment.
    endpointing_continuation_ms: int = 600

    @field_validator("sarvam_llm_reasoning_effort", mode="before")
    @classmethod
    def _normalize_reasoning_effort(cls, v):
        """An empty / "none" / "off" env value must become None so the LLM sends
        reasoning_effort: null (disabled). Otherwise an empty string would be
        sent verbatim and is not a valid effort level."""
        if isinstance(v, str) and v.strip().lower() in ("", "none", "null", "off", "false"):
            return None
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
