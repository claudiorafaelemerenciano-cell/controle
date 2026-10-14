"""Monta o prompt de sistema e define as ferramentas expostas ao LLM."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings

log = logging.getLogger(__name__)

WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro"]


@dataclass
class CallInfo:
    id: str
    direction: str  # inbound | outbound
    from_number: str = ""
    to_number: str = ""
    purpose: str | None = None
    channel: str | None = None


def _read(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _now_pt(tz: str) -> str:
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:  # noqa: BLE001
        now = datetime.now()
    return f"{WEEKDAYS[now.weekday()]}, {now.day} de {MONTHS[now.month - 1]} de {now.year}, {now:%H:%M}"


def call_context(settings: Settings, call: CallInfo, transfer_available: bool) -> str:
    lines = [f"Agora é {_now_pt(settings.timezone)} (horário de Brasília)."]
    if call.direction == "outbound":
        lines.append(
            f"Esta é uma ligação que VOCÊ fez em nome de {settings.owner_name} para o número {call.to_number or 'desconhecido'}."
        )
        lines.append(f"Objetivo da ligação: {call.purpose or 'não informado'}.")
        lines.append(
            "Ao ser atendida, cumprimente, apresente-se como assistente virtual de "
            f"{settings.owner_name} e explique o motivo de forma breve. Se cair numa caixa postal, "
            "deixe uma mensagem curta e encerre."
        )
    else:
        lines.append(f"Ligação recebida do número {call.from_number or 'não identificado'}.")
        lines.append("Você já atendeu e cumprimentou a pessoa; continue a conversa a partir da resposta dela.")
    lines.append(
        "Transferência para o responsável: disponível." if transfer_available
        else "Transferência para o responsável: indisponível (ofereça anotar recado)."
    )
    return "\n".join(lines)


def build_system_prompt(settings: Settings, call: CallInfo, transfer_available: bool = False) -> str:
    template = _read(settings.system_prompt_file)
    if not template:
        raise RuntimeError(f"Prompt de sistema não encontrado: {settings.system_prompt_file}")
    knowledge = re.sub(r"<!--.*?-->", "", _read(settings.knowledge_file), flags=re.S).strip()
    knowledge = knowledge or "(nenhuma informação adicional cadastrada)"
    values = {
        "ASSISTANT_NAME": settings.assistant_name,
        "OWNER_NAME": settings.owner_name,
        "BUSINESS_DESCRIPTION": settings.business_description or "(sem descrição)",
        "KNOWLEDGE": knowledge,
        "CALL_CONTEXT": call_context(settings, call, transfer_available),
    }
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)
    return out


def default_greeting(settings: Settings) -> str:
    if settings.greeting:
        return settings.greeting
    return (
        f"Olá! Você ligou para {settings.owner_name}. Eu sou a {settings.assistant_name}, "
        "assistente virtual. Como posso ajudar?"
    )


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def build_tools(transfer_available: bool) -> list[dict]:
    tools = [
        _tool(
            "anotar_recado",
            "Salva um recado para o responsável. Use depois de confirmar nome, telefone de retorno e assunto.",
            {
                "nome": {"type": "string", "description": "Nome de quem deixou o recado (vazio se não informado)."},
                "telefone": {"type": "string", "description": "Telefone para retorno, só dígitos (vazio se não informado)."},
                "recado": {"type": "string", "description": "O recado, de forma clara e completa."},
            },
            ["nome", "telefone", "recado"],
        ),
        _tool(
            "encerrar_ligacao",
            "Encerra a ligação. Use somente depois de se despedir.",
            {"motivo": {"type": "string", "description": "Motivo curto do encerramento."}},
            ["motivo"],
        ),
    ]
    if transfer_available:
        tools.append(
            _tool(
                "transferir",
                "Transfere a ligação para o telefone do responsável. Avise a pessoa antes de usar.",
                {"motivo": {"type": "string", "description": "Motivo da transferência."}},
                ["motivo"],
            )
        )
    return tools
