"""Provedores de TTS — todos em português do Brasil, saída PCM s16le 8 kHz mono.

Cada provedor separa a montagem da requisição (`build_request`, função pura,
testável sem rede) do envio HTTP em streaming (`synthesize`).

- azure:       vozes neurais nativas pt-BR (Francisca, Antonio, Thalita...),
               saída `raw-8khz-16bit-mono-pcm` (sem reamostragem). Recomendado.
- elevenlabs:  modelos multilíngues; escolha uma voz brasileira na Voice Library.
- openai:      gpt-4o-mini-tts com `instructions` pedindo sotaque brasileiro
               (as vozes não são nativas pt-BR).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from xml.sax.saxutils import escape

import httpx

from ..audio import SAMPLE_RATE, StreamResampler, ulaw_to_pcm16

log = logging.getLogger(__name__)


@dataclass
class TTSRequest:
    url: str
    headers: dict[str, str]
    params: dict[str, Any] = field(default_factory=dict)
    json: dict[str, Any] | None = None
    content: bytes | None = None
    # formato da resposta: ("pcm", taxa) ou ("ulaw", 8000)
    encoding: str = "pcm"
    sample_rate: int = SAMPLE_RATE


class HTTPStreamingTTS:
    """Base: faz POST em streaming e converte a resposta para slin 8 kHz."""

    timeout = httpx.Timeout(30.0, connect=5.0)

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    def build_request(self, text: str) -> TTSRequest:  # pragma: no cover - abstrato
        raise NotImplementedError

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        req = self.build_request(text)
        resampler = StreamResampler(req.sample_rate, SAMPLE_RATE)
        async with self._http().stream(
            "POST", req.url, headers=req.headers, params=req.params or None,
            json=req.json, content=req.content,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread())[:500]
                raise RuntimeError(f"{type(self).__name__}: HTTP {resp.status_code}: {body!r}")
            async for chunk in resp.aiter_bytes():
                if req.encoding == "ulaw":
                    chunk = ulaw_to_pcm16(chunk)
                out = resampler.process(chunk)
                if out:
                    yield out


class AzureTTS(HTTPStreamingTTS):
    """Azure AI Speech (REST). Vozes neurais nativas pt-BR."""

    OUTPUT_FORMAT = "raw-8khz-16bit-mono-pcm"

    def __init__(self, key: str, region: str = "brazilsouth", voice: str = "pt-BR-FranciscaNeural",
                 rate: str = "", client: httpx.AsyncClient | None = None):
        super().__init__(client)
        if not key:
            raise ValueError("AZURE_SPEECH_KEY não configurada")
        self.key, self.region, self.voice, self.rate = key, region, voice, rate

    def build_ssml(self, text: str) -> str:
        body = escape(text)
        if self.rate:
            body = f'<prosody rate="{escape(self.rate)}">{body}</prosody>'
        return (
            "<speak version='1.0' xml:lang='pt-BR' xmlns='http://www.w3.org/2001/10/synthesis'>"
            f"<voice xml:lang='pt-BR' name='{escape(self.voice)}'>{body}</voice></speak>"
        )

    def build_request(self, text: str) -> TTSRequest:
        return TTSRequest(
            url=f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": self.key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": self.OUTPUT_FORMAT,
                "User-Agent": "controle-ai-secretary",
            },
            content=self.build_ssml(text).encode("utf-8"),
            encoding="pcm",
            sample_rate=8000,
        )


# Voz pré-pronta multilíngue usada só se ELEVENLABS_VOICE_ID não for definida.
# NÃO é uma voz nativa brasileira: escolha uma na Voice Library (sotaque brasileiro).
ELEVENLABS_FALLBACK_VOICE = "21m00Tcm4TlvDq8ikWAM"


class ElevenLabsTTS(HTTPStreamingTTS):
    def __init__(self, api_key: str, voice_id: str = "", model: str = "eleven_multilingual_v2",
                 output_format: str = "pcm_16000", client: httpx.AsyncClient | None = None):
        super().__init__(client)
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY não configurada")
        if not voice_id:
            log.warning(
                "ELEVENLABS_VOICE_ID não definida: usando voz genérica multilíngue. "
                "Para sotaque brasileiro natural escolha uma voz pt-BR na Voice Library."
            )
        self.api_key = api_key
        self.voice_id = voice_id or ELEVENLABS_FALLBACK_VOICE
        self.model = model
        self.output_format = output_format

    def _format(self) -> tuple[str, int]:
        kind, _, rate = self.output_format.partition("_")
        if kind not in ("pcm", "ulaw") or not rate.isdigit():
            raise ValueError(f"ELEVENLABS_OUTPUT_FORMAT não suportado: {self.output_format}")
        return kind, int(rate)

    def build_request(self, text: str) -> TTSRequest:
        encoding, rate = self._format()
        body: dict[str, Any] = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        # `language_code` só é aceito pelos modelos v2.5 (turbo/flash); nos demais a API rejeita.
        if "v2_5" in self.model:
            body["language_code"] = "pt"
        return TTSRequest(
            url=f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream",
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            params={"output_format": self.output_format},
            json=body,
            encoding=encoding,
            sample_rate=rate,
        )


class OpenAITTS(HTTPStreamingTTS):
    PCM_RATE = 24000  # response_format=pcm -> 24 kHz s16le mono

    def __init__(self, api_key: str, model: str = "gpt-4o-mini-tts", voice: str = "coral",
                 instructions: str = "", client: httpx.AsyncClient | None = None):
        super().__init__(client)
        if not api_key:
            raise ValueError("OPENAI_API_KEY não configurada")
        self.api_key, self.model, self.voice, self.instructions = api_key, model, voice, instructions

    def build_request(self, text: str) -> TTSRequest:
        body: dict[str, Any] = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": "pcm",
        }
        if self.instructions:
            body["instructions"] = self.instructions  # suportado pelo gpt-4o-mini-tts
        return TTSRequest(
            url="https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
            encoding="pcm",
            sample_rate=self.PCM_RATE,
        )
