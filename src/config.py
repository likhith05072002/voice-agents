from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Sarvam AI
    sarvam_api_key: str

    # Sarvam STT
    sarvam_stt_ws_url: str = "wss://api.sarvam.ai/speech-to-text/ws"
    sarvam_stt_model: str = "saaras:v3"

    # Sarvam TTS
    sarvam_tts_ws_url: str = "wss://api.sarvam.ai/text-to-speech/ws"
    sarvam_tts_model: str = "bulbul:v2"
    sarvam_tts_voice: str = "anushka"

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
        "Shop: 10AM-9PM daily. Gold: 24K=Rs.7800/g, 22K=Rs.7150/g. "
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

    # ─── Barge-in guard stack ───
    # Minimum words before a candidate counts as a real interruption (filters
    # clicks / stray STT tokens). Backchannels are filtered separately.
    bargein_min_words: int = 2
    # If VAD fired but no real transcript lands within this window, resume
    # playback (false-interruption recovery).
    bargein_false_timeout_ms: int = 1200
    # Pause-then-resume recovery for backchannels / noise. When False, the agent
    # simply keeps talking until a confirmed interruption.
    bargein_enable_recovery: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
