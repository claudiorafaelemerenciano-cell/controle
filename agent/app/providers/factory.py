"""Instancia provedores conforme STT_PROVIDER / LLM_PROVIDER / TTS_PROVIDER."""
from __future__ import annotations

from ..config import Settings


def make_stt(s: Settings):
    if s.stt_provider == "deepgram":
        from .stt_deepgram import DeepgramSTT

        return DeepgramSTT(s.deepgram_api_key, s.deepgram_model, s.deepgram_language,
                           s.deepgram_endpointing_ms, s.deepgram_utterance_end_ms)
    if s.stt_provider == "fake":
        from .fake import FakeSTT

        return FakeSTT()
    raise ValueError(f"STT_PROVIDER desconhecido: {s.stt_provider}")


def make_llm(s: Settings):
    if s.llm_provider == "anthropic":
        from .llm_anthropic import AnthropicLLM

        return AnthropicLLM(s.anthropic_api_key, s.anthropic_model, s.anthropic_effort,
                            s.anthropic_fallbacks, s.anthropic_max_tokens, s.anthropic_summary_model)
    if s.llm_provider == "fake":
        from .fake import FakeLLM

        return FakeLLM()
    raise ValueError(f"LLM_PROVIDER desconhecido: {s.llm_provider}")


def make_tts(s: Settings):
    from . import tts

    if s.tts_provider == "azure":
        return tts.AzureTTS(s.azure_speech_key, s.azure_speech_region, s.azure_tts_voice, s.azure_tts_rate)
    if s.tts_provider == "elevenlabs":
        return tts.ElevenLabsTTS(s.elevenlabs_api_key, s.elevenlabs_voice_id, s.elevenlabs_model,
                                 s.elevenlabs_output_format)
    if s.tts_provider == "openai":
        return tts.OpenAITTS(s.openai_api_key, s.openai_tts_model, s.openai_tts_voice,
                             s.openai_tts_instructions)
    if s.tts_provider == "fake":
        from .fake import FakeTTS

        return FakeTTS()
    raise ValueError(f"TTS_PROVIDER desconhecido: {s.tts_provider}")
