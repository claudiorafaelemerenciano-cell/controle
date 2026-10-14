"""Peças do pipeline de voz: divisor de frases e player de áudio com ritmo real."""
from __future__ import annotations

import asyncio
import re
from collections import deque
from typing import Awaitable, Callable

from .audio import FRAME_MS, FrameBuffer

# Abreviações comuns que não terminam frase.
_ABBREV = {"sr", "sra", "srta", "dr", "dra", "prof", "profa", "av", "r", "n", "nº", "tel", "etc", "ltda", "s.a"}
_END_RE = re.compile(r"[.!?…]+[\"')\]]*\s")


class SentenceSplitter:
    """Recebe fragmentos de texto em streaming e devolve frases completas.

    Enviar cada frase ao TTS assim que ela fecha reduz a latência percebida.
    Frases muito longas são quebradas na vírgula.
    """

    def __init__(self, max_chars: int = 180, min_chars: int = 2):
        self.max_chars = max_chars
        self.min_chars = min_chars
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            sentence, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if len(sentence) >= self.min_chars:
                out.append(sentence)
        return out

    def _find_cut(self) -> int | None:
        for m in _END_RE.finditer(self._buf):
            before = self._buf[: m.start()].split()
            last_word = before[-1].lower().rstrip(".") if before else ""
            if self._buf[m.start()] == "." and last_word in _ABBREV:
                continue
            return m.end()
        if len(self._buf) > self.max_chars:
            idx = self._buf.rfind(", ", 0, self.max_chars)
            if idx > 20:
                return idx + 2
        return None

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if len(rest) >= self.min_chars else []


class AudioPlayer:
    """Fila de frames de 20 ms enviados no ritmo real; suporta interrupção (barge-in).

    Itens da fila são frames (bytes) ou marcadores (callables) executados quando
    a reprodução os alcança — usados para saber o que de fato foi falado.
    """

    def __init__(self, send: Callable[[bytes], Awaitable[None]],
                 frame_interval: float = FRAME_MS / 1000, lead: float = 0.04):
        self._send = send
        self.frame_interval = frame_interval
        self.lead = lead  # quanto podemos adiantar em relação ao tempo real
        self._queue: deque[bytes | Callable[[], None]] = deque()
        self._wakeup = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._framer = FrameBuffer()
        self.frames_sent = 0
        self.idle_since = asyncio.get_running_loop().time()

    @property
    def is_playing(self) -> bool:
        return not self._idle.is_set()

    def _push(self, item: bytes | Callable[[], None]) -> None:
        self._queue.append(item)
        self._idle.clear()
        self._wakeup.set()

    def enqueue_pcm(self, pcm: bytes) -> None:
        for frame in self._framer.push(pcm):
            self._push(frame)

    def end_segment(self) -> None:
        """Completa o último frame parcial com silêncio."""
        for frame in self._framer.flush():
            self._push(frame)

    def mark(self, callback: Callable[[], None]) -> None:
        self._push(callback)

    def clear(self) -> None:
        """Descarta tudo que ainda não foi tocado."""
        self._queue.clear()
        self._framer.clear()
        self._set_idle()

    def _set_idle(self) -> None:
        if not self._idle.is_set():
            self._idle.set()
            self.idle_since = asyncio.get_running_loop().time()

    async def wait_drained(self) -> None:
        await self._idle.wait()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_t: float | None = None
        while True:
            if not self._queue:
                self._set_idle()
                next_t = None
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            item = self._queue.popleft()
            if callable(item):
                item()
                continue
            now = loop.time()
            if next_t is None or now - next_t > 0.1:
                next_t = now - self.lead  # (re)início: pequeno adiantamento
            delay = next_t - now
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0)
            await self._send(item)
            self.frames_sent += 1
            next_t += self.frame_interval
