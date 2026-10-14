"""Garante que cada provedor monta requisições em português do Brasil (sem rede)."""
import json
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import httpx
import pytest

from app.config import Settings
from app.providers import factory
from app.providers.base import STTEvent
from app.providers.llm_anthropic import FALLBACK_BETA, AnthropicLLM
from app.providers.stt_deepgram import TranscriptAssembler, build_deepgram_url
from app.providers.tts import AzureTTS, ElevenLabsTTS, OpenAITTS


def test_azure_ssml_ptbr():
    tts = AzureTTS("k", "brazilsouth", "pt-BR-FranciscaNeural", rate="-5%")
    req = tts.build_request("Olá, <tudo> bem & você?")
    assert req.url == "https://brazilsouth.tts.speech.microsoft.com/cognitiveservices/v1"
    assert req.headers["X-Microsoft-OutputFormat"] == "raw-8khz-16bit-mono-pcm"
    assert req.headers["Content-Type"] == "application/ssml+xml"
    assert req.sample_rate == 8000 and req.encoding == "pcm"
    root = ElementTree.fromstring(req.content.decode())  # SSML válido mesmo com < e &
    assert root.attrib["{http://www.w3.org/XML/1998/namespace}lang"] == "pt-BR"
    voice = root.find("{http://www.w3.org/2001/10/synthesis}voice")
    assert voice.attrib["name"] == "pt-BR-FranciscaNeural"
    assert "Olá, <tudo> bem & você?" in "".join(voice.itertext())


@pytest.mark.parametrize("voice", ["pt-BR-AntonioNeural", "pt-BR-ThalitaNeural"])
def test_azure_alternative_voices(voice):
    req = AzureTTS("k", voice=voice).build_request("Oi")
    assert f"name='{voice}'" in req.content.decode()


def test_elevenlabs_multilingual_v2():
    tts = ElevenLabsTTS("k", "voz-br", "eleven_multilingual_v2", "pcm_16000")
    req = tts.build_request("Bom dia")
    assert req.url.endswith("/v1/text-to-speech/voz-br/stream")
    assert req.json["model_id"] == "eleven_multilingual_v2"
    assert "language_code" not in req.json  # v2 não aceita language_code
    assert req.params == {"output_format": "pcm_16000"}
    assert (req.encoding, req.sample_rate) == ("pcm", 16000)


@pytest.mark.parametrize("model", ["eleven_turbo_v2_5", "eleven_flash_v2_5"])
def test_elevenlabs_v2_5_forces_portuguese(model):
    req = ElevenLabsTTS("k", "voz-br", model, "ulaw_8000").build_request("Bom dia")
    assert req.json["language_code"] == "pt"
    assert (req.encoding, req.sample_rate) == ("ulaw", 8000)


def test_elevenlabs_rejects_unknown_format():
    with pytest.raises(ValueError):
        ElevenLabsTTS("k", "v", output_format="mp3_44100_128").build_request("x")


def test_openai_instructions_ptbr():
    s = Settings()
    tts = OpenAITTS("k", s.openai_tts_model, s.openai_tts_voice, s.openai_tts_instructions)
    req = tts.build_request("Boa tarde")
    assert req.json["model"] == "gpt-4o-mini-tts"
    assert "português do Brasil" in req.json["instructions"]
    assert "sotaque brasileiro" in req.json["instructions"]
    assert req.json["response_format"] == "pcm" and req.sample_rate == 24000


async def test_tts_streaming_converts_to_8k():
    """Simula a API (MockTransport) e confere a reamostragem 24 kHz -> 8 kHz."""
    pcm24 = b"\x10\x00" * 2400  # 100 ms a 24 kHz

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["instructions"]
        return httpx.Response(200, content=pcm24)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = OpenAITTS("k", instructions="Fale em português do Brasil.", client=client)
    out = b"".join([c async for c in tts.synthesize("Oi")])
    assert abs(len(out) // 2 - 800) <= 2  # 100 ms a 8 kHz


async def test_tts_http_error_raises():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    tts = AzureTTS("k", client=client)
    with pytest.raises(RuntimeError, match="401"):
        _ = [c async for c in tts.synthesize("Oi")]


def test_deepgram_url_ptbr():
    url = build_deepgram_url("nova-3", "pt-BR", 300, 1000)
    q = parse_qs(urlparse(url).query)
    assert q["language"] == ["pt-BR"]
    assert q["model"] == ["nova-3"]
    assert q["encoding"] == ["linear16"] and q["sample_rate"] == ["8000"]
    assert q["endpointing"] == ["300"] and q["vad_events"] == ["true"]
    assert q["interim_results"] == ["true"] and q["utterance_end_ms"] == ["1000"]


def test_deepgram_assembler():
    a = TranscriptAssembler()

    def res(text, is_final=False, speech_final=False):
        return {"type": "Results", "is_final": is_final, "speech_final": speech_final,
                "channel": {"alternatives": [{"transcript": text}]}}

    assert a.handle({"type": "SpeechStarted"}) == [STTEvent("speech_started")]
    assert a.handle(res("bom")) == [STTEvent("partial", "bom")]
    assert a.handle(res("bom dia", is_final=True)) == []
    assert a.handle(res("tudo")) == [STTEvent("partial", "bom dia tudo")]
    assert a.handle(res("tudo bem", is_final=True, speech_final=True)) == [STTEvent("final", "bom dia tudo bem")]
    assert a.handle(res("", is_final=True, speech_final=True)) == []
    a.handle(res("alô", is_final=True))
    assert a.handle({"type": "UtteranceEnd"}) == [STTEvent("final", "alô")]
    assert a.handle({"type": "Metadata"}) == []


def test_anthropic_params_sonnet_5_5():
    llm = AnthropicLLM(api_key="x", model="claude-sonnet-5-5", effort="low")
    p = llm.request_params("claude-sonnet-5-5")
    assert p["thinking"] == {"type": "between_tools"}
    assert p["output_config"] == {"effort": "low"}
    assert p["betas"] == [FALLBACK_BETA] and p["fallbacks"] == "default"


def test_anthropic_params_other_models():
    llm = AnthropicLLM(api_key="x", model="claude-opus-5-5", effort="low")
    p = llm.request_params("claude-opus-5-5")
    assert "thinking" not in p  # between_tools só existe no Sonnet 5.5
    assert p["fallbacks"] == "default"
    p = AnthropicLLM(api_key="x", effort="", fallbacks="auto").request_params("claude-haiku-4-5")
    assert p == {"model": "claude-haiku-4-5"}
    assert "fallbacks" not in AnthropicLLM(api_key="x", fallbacks="off").request_params("claude-sonnet-5-5")


def test_factory_builds_each_tts():
    s = Settings(azure_speech_key="a", elevenlabs_api_key="e", openai_api_key="o")
    for name, cls in [("azure", AzureTTS), ("elevenlabs", ElevenLabsTTS), ("openai", OpenAITTS)]:
        s.tts_provider = name
        assert isinstance(factory.make_tts(s), cls)
    s.tts_provider = "nope"
    with pytest.raises(ValueError):
        factory.make_tts(s)
