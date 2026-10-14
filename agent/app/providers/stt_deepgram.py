"""STT em streaming via WebSocket da Deepgram (pt-BR, endpointing + VAD)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode

import websockets

from ..audio import SAMPLE_RATE
from .base import STTEvent

log = logging.getLogger(__name__)

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


def build_deepgram_url(
    model: str = "nova-3",
    language: str = "pt-BR",
    endpointing_ms: int = 300,
    utterance_end_ms: int = 1000,
    sample_rate: int = SAMPLE_RATE,
) -> str:
    params = {
        "model": model,
        "language": language,
        "encoding": "linear16",
        "sample_rate": sample_rate,
        "channels": 1,
        "interim_results": "true",  # necessário para utterance_end_ms e barge-in rápido
        "endpointing": endpointing_ms,  # silêncio (ms) que finaliza uma fala
        "utterance_end_ms": utterance_end_ms,  # fallback robusto a ruído
        "vad_events": "true",  # eventos SpeechStarted
        "smart_format": "true",
        "punctuate": "true",
    }
    return f"{DEEPGRAM_URL}?{urlencode(params)}"


class TranscriptAssembler:
    """Converte mensagens da Deepgram em STTEvents (lógica pura, testável)."""

    def __init__(self) -> None:
        self._finals: list[str] = []

    def handle(self, msg: dict) -> list[STTEvent]:
        mtype = msg.get("type")
        if mtype == "SpeechStarted":
            return [STTEvent("speech_started")]
        if mtype == "UtteranceEnd":
            return self._flush()
        if mtype != "Results":
            return []
        alts = (msg.get("channel") or {}).get("alternatives") or [{}]
        text = (alts[0].get("transcript") or "").strip()
        events: list[STTEvent] = []
        if msg.get("is_final"):
            if text:
                self._finals.append(text)
            if msg.get("speech_final"):
                events.extend(self._flush())
        elif text:
            events.append(STTEvent("partial", " ".join([*self._finals, text])))
        return events

    def _flush(self) -> list[STTEvent]:
        if not self._finals:
            return []
        text = " ".join(self._finals)
        self._finals = []
        return [STTEvent("final", text)]


class DeepgramStream:
    def __init__(self, api_key: str, url: str):
        self._api_key = api_key
        self._url = url
        self._ws = None
        self._queue: asyncio.Queue[STTEvent | None] = asyncio.Queue()
        self._assembler = TranscriptAssembler()
        self._tasks: list[asyncio.Task] = []
        self._closed = False
        self._last_send = 0.0

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self._url,
            additional_headers={"Authorization": f"Token {self._api_key}"},
            max_size=2**22,
            ping_interval=20,
        )
        self._tasks = [
            asyncio.create_task(self._recv_loop()),
            asyncio.create_task(self._keepalive_loop()),
        ]

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for ev in self._assembler.handle(msg):
                    await self._queue.put(ev)
        except websockets.ConnectionClosed as exc:
            if not self._closed:
                log.warning("Deepgram fechou a conexão: %s", exc)
        finally:
            await self._queue.put(None)

    async def _keepalive_loop(self) -> None:
        # Se nenhum áudio for enviado por ~10 s a Deepgram encerra a conexão.
        loop = asyncio.get_running_loop()
        while not self._closed:
            await asyncio.sleep(4)
            if loop.time() - self._last_send > 4 and self._ws is not None:
                with contextlib.suppress(Exception):
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed or self._ws is None:
            return
        try:
            await self._ws.send(pcm)
            self._last_send = asyncio.get_running_loop().time()
        except websockets.ConnectionClosed:
            pass

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            with contextlib.suppress(Exception):
                await self._ws.close()
        for t in self._tasks:
            t.cancel()
        await self._queue.put(None)


class DeepgramSTT:
    def __init__(self, api_key: str, model: str = "nova-3", language: str = "pt-BR",
                 endpointing_ms: int = 300, utterance_end_ms: int = 1000):
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY não configurada")
        self.api_key = api_key
        self.url = build_deepgram_url(model, language, endpointing_ms, utterance_end_ms)

    async def open_stream(self) -> DeepgramStream:
        stream = DeepgramStream(self.api_key, self.url)
        await stream.connect()
        return stream
