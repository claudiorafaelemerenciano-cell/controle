"""Exercita o adaptador Anthropic com um cliente falso (sem rede)."""
from types import SimpleNamespace as NS

from app.providers.base import TextDelta, ToolUse, TurnEnd
from app.providers.llm_anthropic import AnthropicLLM


class FakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        return self._final


class FakeMessages:
    def __init__(self, stream=None, created=None):
        self._stream, self._created = stream, created
        self.kwargs = None

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return self._stream

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self._created


def fake_client(messages):
    return NS(beta=NS(messages=messages))


async def test_stream_turn_maps_events():
    tool = NS(type="tool_use", id="tu1", name="anotar_recado", input={"nome": "A", "telefone": "", "recado": "r"})
    thinking = NS(type="thinking", thinking="nota", signature="sig")
    final = NS(stop_reason="tool_use", content=[thinking, NS(type="text", text="Olá."), tool])
    events = [NS(type="text", text="Ol"), NS(type="text", text="á."), NS(type="content_block_stop")]
    msgs = FakeMessages(stream=FakeStream(events, final))
    llm = AnthropicLLM(model="claude-sonnet-5-5", client=fake_client(msgs))

    out = [e async for e in llm.stream_turn("sys", [{"role": "user", "content": "oi"}], [{"name": "t"}])]
    assert out[:2] == [TextDelta("Ol"), TextDelta("á.")]
    assert out[2] == ToolUse("tu1", "anotar_recado", {"nome": "A", "telefone": "", "recado": "r"})
    assert isinstance(out[3], TurnEnd) and out[3].stop_reason == "tool_use"
    assert out[3].content[0] is thinking  # devolvido sem alterações
    kw = msgs.kwargs
    assert kw["model"] == "claude-sonnet-5-5" and kw["system"] == "sys"
    assert kw["thinking"] == {"type": "between_tools"}
    assert kw["fallbacks"] == "default"
    assert kw["max_tokens"] == 1024


async def test_truncated_tool_input_not_executed():
    tool = NS(type="tool_use", id="tu1", name="anotar_recado", input={"nome": "A"})
    final = NS(stop_reason="max_tokens", content=[tool])
    llm = AnthropicLLM(client=fake_client(FakeMessages(stream=FakeStream([], final))))
    out = [e async for e in llm.stream_turn("s", [], [])]
    assert not any(isinstance(e, ToolUse) for e in out)


async def test_summarize():
    created = NS(stop_reason="end_turn", content=[NS(type="text", text=" Maria pediu retorno. ")])
    msgs = FakeMessages(created=created)
    llm = AnthropicLLM(client=fake_client(msgs))
    assert await llm.summarize("Interlocutor: oi") == "Maria pediu retorno."
    assert "português do Brasil" in msgs.kwargs["system"]
    msgs._created = NS(stop_reason="refusal", content=[])
    assert await llm.summarize("x") is None
