"""LLM via SDK oficial `anthropic` (streaming, tool use)."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import anthropic

from .base import LLMEvent, TextDelta, ToolUse, TurnEnd

log = logging.getLogger(__name__)

# Fallback server-side em caso de recusa ("refusal"): forma "default" (beta).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Modelos em que `fallbacks: "default"` é aceito (Claude API).
_FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1")

SUMMARY_SYSTEM = (
    "Você resume ligações telefônicas atendidas por uma secretária virtual. "
    "Escreva em português do Brasil, em 1 a 3 frases curtas, sem markdown: quem ligou "
    "(nome e telefone, se informados), o motivo e qualquer pendência ou recado para o responsável."
)


class AnthropicLLM:
    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-sonnet-5-5",
        effort: str = "low",
        fallbacks: str = "auto",
        max_tokens: int = 1024,
        summary_model: str = "",
        client: anthropic.AsyncAnthropic | None = None,
    ):
        # Sem api_key explícita o SDK resolve credenciais do ambiente.
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key or None)
        self.model = model
        self.summary_model = summary_model or model
        self.effort = effort
        self.fallbacks = fallbacks
        self.max_tokens = max_tokens

    def request_params(self, model: str) -> dict[str, Any]:
        """Parâmetros comuns, ajustados ao modelo escolhido."""
        params: dict[str, Any] = {"model": model}
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if model.startswith("claude-sonnet-5-5"):
            # Menor latência no Sonnet 5.5: sem raciocínio estendido
            # (aceito só com effort <= high; nenhum outro campo em `thinking`).
            params["thinking"] = {"type": "between_tools"}
        use_fallbacks = self.fallbacks == "default" or (
            self.fallbacks == "auto" and model.startswith(_FALLBACK_MODELS)
        )
        if use_fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def stream_turn(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMEvent]:
        params = self.request_params(self.model)
        async with self.client.beta.messages.stream(
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            cache_control={"type": "ephemeral"},
            **params,
        ) as stream:
            async for event in stream:
                if event.type == "text" and event.text:
                    yield TextDelta(event.text)
            final = await stream.get_final_message()

        if final.stop_reason == "refusal":
            log.warning("Modelo recusou o turno: %s", getattr(final, "stop_details", None))
        if final.stop_reason != "max_tokens":
            for block in final.content:
                if block.type == "tool_use":
                    yield ToolUse(block.id, block.name, block.input if isinstance(block.input, dict) else {})
        # O conteúdo volta ao histórico sem alterações (inclui blocos de thinking).
        yield TurnEnd(content=list(final.content), stop_reason=final.stop_reason)

    async def summarize(self, transcript: str) -> str | None:
        params = self.request_params(self.summary_model)
        resp = await self.client.beta.messages.create(
            max_tokens=600,
            system=SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": f"Transcrição da ligação:\n\n{transcript}"}],
            **params,
        )
        if resp.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None
