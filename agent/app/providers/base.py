"""Interfaces mínimas dos provedores (STT, LLM, TTS).

Qualquer implementação que siga estes contratos pode ser plugada via
STT_PROVIDER / LLM_PROVIDER / TTS_PROVIDER (veja factory.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


# --- STT -------------------------------------------------------------------------

@dataclass(frozen=True)
class STTEvent:
    """kind: 'speech_started' | 'partial' | 'final'."""

    kind: str
    text: str = ""


@runtime_checkable
class STTStream(Protocol):
    async def send_audio(self, pcm: bytes) -> None:
        """Recebe PCM s16le mono 8 kHz (frames do AudioSocket)."""

    def events(self) -> AsyncIterator[STTEvent]:
        """Itera eventos até o stream ser fechado."""

    async def close(self) -> None: ...


class STTProvider(Protocol):
    async def open_stream(self) -> STTStream: ...


# --- LLM -------------------------------------------------------------------------

@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class TurnEnd:
    """Fim de um turno do modelo.

    `content` é o conteúdo do assistente exatamente como deve voltar no
    histórico (opaco para a conversa — pode conter blocos de thinking etc.).
    """

    content: list[Any] = field(default_factory=list)
    stop_reason: str | None = None


LLMEvent = TextDelta | ToolUse | TurnEnd


class LLMProvider(Protocol):
    def stream_turn(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMEvent]:
        """Gera TextDelta*, ToolUse* e termina com exatamente um TurnEnd."""

    async def summarize(self, transcript: str) -> str | None: ...


# --- TTS -------------------------------------------------------------------------

class TTSProvider(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Gera PCM s16le mono 8 kHz em blocos de qualquer tamanho."""
