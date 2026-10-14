import numpy as np
import pytest

from app.audio import FRAME_BYTES, FrameBuffer, StreamResampler, resample, ulaw_to_pcm16


def tone(freq: float, rate: int, seconds: float = 0.5, amp: float = 10000) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype("<i2").tobytes()


def dominant_freq(pcm: bytes, rate: int) -> float:
    x = np.frombuffer(pcm, dtype="<i2").astype(float)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / rate)[np.argmax(spec)]


def rms(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype="<i2").astype(float)
    return float(np.sqrt(np.mean(x**2)))


@pytest.mark.parametrize("in_rate", [16000, 24000, 22050, 44100])
def test_downsample_length_and_frequency(in_rate):
    src = tone(1000, in_rate)
    out = resample(src, in_rate, 8000)
    n_out = len(out) // 2
    assert abs(n_out - 4000) <= 2  # 0,5 s a 8 kHz
    assert abs(dominant_freq(out, 8000) - 1000) < 20
    # amplitude preservada na banda passante
    assert rms(out) == pytest.approx(rms(src), rel=0.05)


def test_anti_aliasing_attenuates_above_nyquist():
    # 6 kHz não cabe em 8 kHz (Nyquist 4 kHz): deve ser fortemente atenuado
    src = tone(6000, 16000)
    out = resample(src, 16000, 8000)
    assert rms(out) < rms(src) * 0.02


@pytest.mark.parametrize("in_rate,chunk", [(16000, 333), (24000, 1001), (22050, 77)])
def test_chunked_equals_one_shot(in_rate, chunk):
    src = tone(440, in_rate, 0.3)
    one = resample(src, in_rate, 8000)
    r = StreamResampler(in_rate, 8000)
    parts = b"".join(r.process(src[i:i + chunk]) for i in range(0, len(src), chunk))
    assert parts == one  # inclusive com blocos de tamanho ímpar em bytes


def test_passthrough_8k():
    src = tone(440, 8000, 0.1)
    assert resample(src, 8000, 8000) == src


def test_ulaw_decode_known_values():
    pcm = np.frombuffer(ulaw_to_pcm16(bytes([0xFF, 0x7F, 0x00, 0x80])), dtype="<i2")
    assert pcm[0] == 0 and pcm[1] == 0  # silêncio positivo/negativo
    assert pcm[2] == -32124 and pcm[3] == 32124  # extremos


def test_frame_buffer():
    fb = FrameBuffer()
    assert fb.push(b"\x01" * 300) == []
    frames = fb.push(b"\x02" * 400)
    assert len(frames) == 2 and all(len(f) == FRAME_BYTES for f in frames)
    last = fb.flush()
    assert len(last) == 1 and len(last[0]) == FRAME_BYTES
    assert last[0].endswith(b"\x00")
    assert fb.flush() == []
