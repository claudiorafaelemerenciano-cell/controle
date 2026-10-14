"""Configuração lida de variáveis de ambiente (veja ../.env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


@dataclass
class Settings:
    # Persona / negócio
    owner_name: str = "o responsável"
    owner_phone: str = ""
    business_description: str = ""
    assistant_name: str = "Sofia"
    greeting: str = ""
    knowledge_file: str = "/config/knowledge.md"
    system_prompt_file: str = str(AGENT_DIR / "prompts" / "system_pt.md")
    timezone: str = "America/Sao_Paulo"

    # Provedores
    llm_provider: str = "anthropic"
    stt_provider: str = "deepgram"
    tts_provider: str = "azure"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_summary_model: str = ""
    anthropic_effort: str = "low"
    anthropic_fallbacks: str = "auto"
    anthropic_max_tokens: int = 1024

    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    deepgram_language: str = "pt-BR"
    deepgram_endpointing_ms: int = 300
    deepgram_utterance_end_ms: int = 1000

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_output_format: str = "pcm_16000"

    openai_api_key: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"
    openai_tts_instructions: str = (
        "Fale em português do Brasil, com sotaque brasileiro natural, "
        "tom cordial e profissional."
    )

    azure_speech_key: str = ""
    azure_speech_region: str = "brazilsouth"
    azure_tts_voice: str = "pt-BR-FranciscaNeural"
    azure_tts_rate: str = ""

    # Rede / servidores
    audiosocket_host: str = "127.0.0.1"
    audiosocket_port: int = 9092
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    db_path: str = "/data/controle.db"
    dashboard_dir: str = str(AGENT_DIR.parent / "dashboard")
    api_token: str = ""
    internal_token: str = ""
    cors_origins: list[str] = field(default_factory=list)

    # Asterisk AMI
    ami_host: str = "127.0.0.1"
    ami_port: int = 5038
    ami_username: str = ""
    ami_secret: str = ""
    sip_caller_id: str = ""
    sip_trunk_endpoint: str = "provider"
    originate_timeout_s: int = 45

    # Comportamento da conversa
    barge_in_min_chars: int = 2
    silence_timeout_s: float = 15.0
    max_call_seconds: float = 900.0
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.owner_name = _env("OWNER_NAME", s.owner_name)
        s.owner_phone = _env("OWNER_PHONE")
        s.business_description = _env("BUSINESS_DESCRIPTION")
        s.assistant_name = _env("ASSISTANT_NAME", s.assistant_name)
        s.greeting = _env("GREETING")
        s.knowledge_file = _env("KNOWLEDGE_FILE", s.knowledge_file)
        s.system_prompt_file = _env("SYSTEM_PROMPT_FILE", s.system_prompt_file)
        s.timezone = _env("TZ", s.timezone)

        s.llm_provider = _env("LLM_PROVIDER", s.llm_provider).lower()
        s.stt_provider = _env("STT_PROVIDER", s.stt_provider).lower()
        s.tts_provider = _env("TTS_PROVIDER", s.tts_provider).lower()

        s.anthropic_api_key = _env("ANTHROPIC_API_KEY")
        s.anthropic_model = _env("ANTHROPIC_MODEL", s.anthropic_model)
        s.anthropic_summary_model = _env("ANTHROPIC_SUMMARY_MODEL")
        s.anthropic_effort = _env("ANTHROPIC_EFFORT", s.anthropic_effort)
        s.anthropic_fallbacks = _env("ANTHROPIC_FALLBACKS", s.anthropic_fallbacks).lower()
        s.anthropic_max_tokens = _env_int("ANTHROPIC_MAX_TOKENS", s.anthropic_max_tokens)

        s.deepgram_api_key = _env("DEEPGRAM_API_KEY")
        s.deepgram_model = _env("DEEPGRAM_MODEL", s.deepgram_model)
        s.deepgram_language = _env("DEEPGRAM_LANGUAGE", s.deepgram_language)
        s.deepgram_endpointing_ms = _env_int("DEEPGRAM_ENDPOINTING_MS", s.deepgram_endpointing_ms)
        s.deepgram_utterance_end_ms = _env_int(
            "DEEPGRAM_UTTERANCE_END_MS", s.deepgram_utterance_end_ms
        )

        s.elevenlabs_api_key = _env("ELEVENLABS_API_KEY")
        s.elevenlabs_voice_id = _env("ELEVENLABS_VOICE_ID")
        s.elevenlabs_model = _env("ELEVENLABS_MODEL", s.elevenlabs_model)
        s.elevenlabs_output_format = _env("ELEVENLABS_OUTPUT_FORMAT", s.elevenlabs_output_format)

        s.openai_api_key = _env("OPENAI_API_KEY")
        s.openai_tts_model = _env("OPENAI_TTS_MODEL", s.openai_tts_model)
        s.openai_tts_voice = _env("OPENAI_TTS_VOICE", s.openai_tts_voice)
        s.openai_tts_instructions = _env("OPENAI_TTS_INSTRUCTIONS", s.openai_tts_instructions)

        s.azure_speech_key = _env("AZURE_SPEECH_KEY")
        s.azure_speech_region = _env("AZURE_SPEECH_REGION", s.azure_speech_region)
        s.azure_tts_voice = _env("AZURE_TTS_VOICE", s.azure_tts_voice)
        s.azure_tts_rate = _env("AZURE_TTS_RATE")

        s.audiosocket_host = _env("AUDIOSOCKET_HOST", s.audiosocket_host)
        s.audiosocket_port = _env_int("AUDIOSOCKET_PORT", s.audiosocket_port)
        s.http_host = _env("HTTP_HOST", s.http_host)
        s.http_port = _env_int("HTTP_PORT", s.http_port)
        s.db_path = _env("DB_PATH", s.db_path)
        s.dashboard_dir = _env("DASHBOARD_DIR", s.dashboard_dir)
        s.api_token = _env("API_TOKEN")
        s.internal_token = _env("INTERNAL_TOKEN")
        s.cors_origins = [o.strip() for o in _env("CORS_ORIGINS").split(",") if o.strip()]

        s.ami_host = _env("AMI_HOST", s.ami_host)
        s.ami_port = _env_int("AMI_PORT", s.ami_port)
        s.ami_username = _env("AMI_USERNAME")
        s.ami_secret = _env("AMI_SECRET")
        s.sip_caller_id = _env("SIP_CALLER_ID") or _env("SIP_DID")
        s.originate_timeout_s = _env_int("ORIGINATE_TIMEOUT_S", s.originate_timeout_s)

        s.barge_in_min_chars = _env_int("BARGE_IN_MIN_CHARS", s.barge_in_min_chars)
        s.silence_timeout_s = _env_float("SILENCE_TIMEOUT_S", s.silence_timeout_s)
        s.max_call_seconds = _env_float("MAX_CALL_SECONDS", s.max_call_seconds)
        s.log_level = _env("LOG_LEVEL", s.log_level).upper()
        return s
