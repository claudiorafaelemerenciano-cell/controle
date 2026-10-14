"""Utilitários de áudio: PCM 16-bit, reamostragem em streaming, µ-law, frames de 20 ms.

O AudioSocket do Asterisk trafega PCM linear assinado 16-bit, little-endian,
mono, 8 kHz ("slin"). 20 ms = 160 amostras = 320 bytes.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 8000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 160
FRAME_BYTES = FRAME_SAMPLES * 2  # 320


def _lowpass_taps(cutoff: float, num_taps: int = 63) -> np.ndarray:
    """FIR passa-baixa (sinc janelado, Blackman). `cutoff` normalizado em ciclos/amostra (0..0.5)."""
    n = np.arange(num_taps) - (num_taps - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * n)
    h *= np.blackman(num_taps)
    return h / h.sum()


class StreamResampler:
    """Reamostrador incremental para PCM s16le mono.

    Filtra (anti-aliasing) e interpola linearmente, mantendo estado entre
    blocos — portanto o resultado em blocos é idêntico ao processamento de uma
    vez só. Para razões inteiras (16k->8k, 24k->8k) a interpolação cai sempre
    em amostras exatas, equivalendo a uma decimação clássica.
    """

    def __init__(self, in_rate: int, out_rate: int, num_taps: int = 63):
        if in_rate <= 0 or out_rate <= 0:
            raise ValueError("taxas de amostragem devem ser positivas")
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.passthrough = in_rate == out_rate
        self._step = in_rate / out_rate
        # Corte em 90% da Nyquist da menor taxa.
        cutoff = 0.45 * min(in_rate, out_rate) / in_rate
        self._taps = _lowpass_taps(cutoff, num_taps)
        self._hist = np.zeros(num_taps - 1, dtype=np.float64)
        self._prev_y = 0.0
        self._base = 0  # índice global da primeira amostra filtrada do bloco atual
        self._next_pos = 0.0  # posição global (em amostras de entrada) da próxima saída
        self._odd = b""  # byte solto entre blocos

    def process(self, data: bytes) -> bytes:
        data = self._odd + data
        if len(data) % 2:
            self._odd, data = data[-1:], data[:-1]
        else:
            self._odd = b""
        if not data:
            return b""
        if self.passthrough:
            return data
        x = np.frombuffer(data, dtype="<i2").astype(np.float64)
        ext = np.concatenate([self._hist, x])
        y = np.convolve(ext, self._taps, mode="valid")  # len(y) == len(x)
        yy = np.concatenate([[self._prev_y], y])
        last = self._base - 1 + len(y)  # maior posição global interpolável
        out = b""
        if self._next_pos <= last:
            count = int(np.floor((last - self._next_pos) / self._step)) + 1
            pos = self._next_pos + self._step * np.arange(count)
            local = pos - (self._base - 1)
            i0 = np.floor(local).astype(np.int64)
            frac = local - i0
            i1 = np.minimum(i0 + 1, len(yy) - 1)
            vals = yy[i0] * (1 - frac) + yy[i1] * frac
            out = np.clip(np.round(vals), -32768, 32767).astype("<i2").tobytes()
            self._next_pos += self._step * count
        self._base += len(y)
        self._prev_y = float(y[-1])
        self._hist = ext[-(len(self._taps) - 1):]
        return out


def resample(data: bytes, in_rate: int, out_rate: int) -> bytes:
    """Reamostra um buffer completo de PCM s16le mono."""
    return StreamResampler(in_rate, out_rate).process(data)


# --- G.711 µ-law ---------------------------------------------------------------

def _build_ulaw_table() -> np.ndarray:
    table = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        table[i] = -sample if sign else sample
    return table


_ULAW_TABLE = _build_ulaw_table()


def ulaw_to_pcm16(data: bytes) -> bytes:
    """Decodifica G.711 µ-law para PCM s16le."""
    idx = np.frombuffer(data, dtype=np.uint8)
    return _ULAW_TABLE[idx].astype("<i2").tobytes()


# --- frames ----------------------------------------------------------------------

class FrameBuffer:
    """Acumula PCM de tamanho arbitrário e entrega frames de exatamente FRAME_BYTES."""

    def __init__(self, frame_bytes: int = FRAME_BYTES):
        self.frame_bytes = frame_bytes
        self._buf = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        frames = []
        while len(self._buf) >= self.frame_bytes:
            frames.append(bytes(self._buf[: self.frame_bytes]))
            del self._buf[: self.frame_bytes]
        return frames

    def flush(self) -> list[bytes]:
        """Completa o último frame com silêncio."""
        if not self._buf:
            return []
        frame = bytes(self._buf) + b"\x00" * (self.frame_bytes - len(self._buf))
        self._buf.clear()
        return [frame]

    def clear(self) -> None:
        self._buf.clear()


def silence(ms: int, rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * (rate * ms // 1000)
