"""Provedores falsos: usados nos testes e para rodar localmente sem chaves.

STT_PROVIDER=fake / LLM_PROVIDER=fake / TTS_PROVIDER=fake
"""
from __future__ import annotations

import asyncio
import math
import struct
from typing import Any, AsyncIterator, Callable, Iterable

from ..audio import SAMPLE_RATE
from .base import LLMEvent, STTEvent, TextDelta, ToolUse, TurnEnd


class FakeSTTStream:
    """Stream controlado pelo teste via `push()`; registra o áudio recebido."""

    def __init__(self) -> None:
        self.audio = bytearray()
        self._queue: asyncio.Queue[STTEvent | None] = asyncio.Queue()
        self.closed = False

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    def push(self, event: STTEvent) -> None:
        self._queue.put_nowait(event)

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)


class FakeSTT:
    def __init__(self) -> None:
        self.streams: list[FakeSTTStream] = []

    async def open_stream(self) -> FakeSTTStream:
        s = FakeSTTStream()
        self.streams.append(s)
        return s


Script = list[list[Any]]  # cada turno: lista de str (texto) ou ToolUse


class FakeLLM:
    """LLM roteirizado. Cada chamada a stream_turn consome um turno do roteiro.

    Um turno é uma lista contendo strings (fragmentos de texto) e/ou ToolUse.
    Sem roteiro, responde com um eco educado.
    """

    def __init__(self, script: Iterable[list[Any]] | None = None, delay: float = 0.0,
                 summary: str = "Resumo de teste."):
        self.script = list(script or [])
        self.delay = delay
        self.summary = summary
        self.calls: list[dict[str, Any]] = []

    async def stream_turn(self, system: str, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]]) -> AsyncIterator[LLMEvent]:
        # snapshot do histórico no momento da chamada
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        turn = self.script.pop(0) if self.script else [_echo(messages)]
        content: list[dict[str, Any]] = []
        text = ""
        tool_uses = []
        for item in turn:
            if self.delay:
                await asyncio.sleep(self.delay)
            if isinstance(item, ToolUse):
                tool_uses.append(item)
            else:
                text += item
                yield TextDelta(item)
        if text:
            content.append({"type": "text", "text": text})
        for tu in tool_uses:
            content.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input})
            yield tu
        yield TurnEnd(content=content, stop_reason="tool_use" if tool_uses else "end_turn")

    async def summarize(self, transcript: str) -> str | None:
        return self.summary


def _echo(messages: list[dict[str, Any]]) -> str:
    last = messages[-1]["content"] if messages else ""
    if isinstance(last, list):
        last = " ".join(b.get("text", "") for b in last if isinstance(b, dict))
    return f"Entendi: {last}. Posso ajudar em algo mais?"


class FakeTTS:
    """Gera um tom de 440 Hz com duração proporcional ao texto (ms_per_char)."""

    def __init__(self, ms_per_char: float = 5.0, chunk_ms: int = 100,
                 on_text: Callable[[str], None] | None = None):
        self.ms_per_char = ms_per_char
        self.chunk_ms = chunk_ms
        self.texts: list[str] = []
        self.on_text = on_text

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        if self.on_text:
            self.on_text(text)
        total = max(1, int(len(text) * self.ms_per_char * SAMPLE_RATE / 1000))
        step = max(1, SAMPLE_RATE * self.chunk_ms // 1000)
        for start in range(0, total, step):
            n = min(step, total - start)
            yield b"".join(
                struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * (start + i) / SAMPLE_RATE)))
                for i in range(n)
            )
            await asyncio.sleep(0)
