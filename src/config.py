from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Sarvam AI
    sarvam_api_key: str

    # Sarvam STT
    sarvam_stt_ws_url: str = "wss://api.sarvam.ai/speech-to-text/ws"
    sarvam_stt_model: str = "saaras:v3"

    # Sarvam TTS
    sarvam_tts_ws_url: str = "wss://api.sarvam.ai/text-to-speech/stream"
    sarvam_tts_model: str = "bulbul:v3"
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
