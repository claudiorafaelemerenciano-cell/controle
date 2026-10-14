"""API HTTP (FastAPI) consumida pelo painel e pelo dialplan do Asterisk.

Contrato público (/api/*) — o painel em ../dashboard depende dele:
  GET  /api/status
  GET  /api/calls?limit=50
  GET  /api/calls/{id}
  POST /api/calls            {"to": str, "purpose": str}
  GET  /api/messages

Endpoints internos (/internal/*) — chamados pelo dialplan via CURL():
  GET /internal/inbound?from=&to=&channel=   -> UUID (texto puro)
  GET /internal/calls/{id}/answered?channel=  -> "ok"
  GET /internal/calls/{id}/hangup             -> "ok"
"""
from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .calls import PHONE_RE, CallManager, normalize_number
from .config import Settings
from .db import Database

log = logging.getLogger(__name__)

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

_VALIDATION_PT = {
    "missing": "campo obrigatório",
    "string_too_short": "texto curto demais",
    "string_too_long": "texto longo demais",
    "string_type": "deve ser um texto",
    "int_parsing": "deve ser um número inteiro",
    "greater_than_equal": "valor abaixo do mínimo",
    "less_than_equal": "valor acima do máximo",
}


def _validation_detail(errors: list[dict]) -> str:
    """Mensagem em pt-BR (string) para erros de validação, no formato {"detail": "..."}."""
    parts = []
    for err in errors:
        field = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
        msg = str(err.get("msg", ""))
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        elif err.get("type") in _VALIDATION_PT:
            msg = _VALIDATION_PT[err["type"]]
        else:
            msg = "valor inválido"
        parts.append(f"{field}: {msg}" if field else msg)
    return "Dados inválidos — " + "; ".join(parts)


def _token_eq(given: str, expected: str) -> bool:
    """Comparação em tempo constante; bytes evitam TypeError com caracteres não-ASCII."""
    return secrets.compare_digest(given.encode("utf-8", "replace"), expected.encode("utf-8", "replace"))


class RedactTokenFilter(logging.Filter):
    """Remove `token=...` das URLs no log de acesso do uvicorn (o dialplan envia o token na query)."""

    _RE = re.compile(r"([?&]token=)[^&\s\"]*")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._RE.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        return True


class StatusOut(BaseModel):
    sip_registered: bool
    active_calls: int
    owner_name: str


class CallOut(BaseModel):
    id: str
    direction: Literal["inbound", "outbound"]
    from_number: str
    to_number: str
    started_at: str
    ended_at: Optional[str]
    status: Literal["ringing", "active", "completed", "failed"]
    summary: Optional[str]


class TurnOut(BaseModel):
    role: Literal["caller", "agent"]
    text: str
    ts: str


class CallDetailOut(CallOut):
    transcript: list[TurnOut]


class CallCreate(BaseModel):
    to: str = Field(..., description="Número a ser chamado (formato aceito pelo provedor SIP)")
    purpose: str = Field(..., min_length=1, max_length=2000)

    @field_validator("to")
    @classmethod
    def _valid_number(cls, v: str) -> str:
        v = normalize_number(v)
        if not PHONE_RE.match(v):
            raise ValueError("número inválido: use apenas dígitos, opcionalmente com + no início")
        return v

    @field_validator("purpose")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("purpose não pode ser vazio")
        return v


class CallCreated(BaseModel):
    id: str
    status: Literal["ringing"]


class MessageOut(BaseModel):
    id: str
    call_id: str
    name: Optional[str]
    phone: Optional[str]
    message: str
    created_at: str


def create_app(settings: Settings, db: Database, manager: CallManager) -> FastAPI:
    app = FastAPI(title="Controle — secretária virtual", version="1.0.0")
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _validation_detail(list(exc.errors()))})

    def require_token(request: Request) -> None:
        if not settings.api_token:
            return
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not _token_eq(token.strip(), settings.api_token):
            raise HTTPException(401, "token inválido ou ausente", headers={"WWW-Authenticate": "Bearer"})

    def require_internal(request: Request, token: str = Query("", alias="token")) -> None:
        if settings.internal_token:
            if not _token_eq(token, settings.internal_token):
                raise HTTPException(403, "forbidden")
        elif (request.client.host if request.client else "") not in LOOPBACK:
            raise HTTPException(403, "forbidden")

    # ------------------------------------------------------------------ API pública
    @app.get("/api/status", response_model=StatusOut, dependencies=[Depends(require_token)])
    async def status() -> StatusOut:
        registered = False
        if manager.ami is not None and manager.ami.configured:
            registered = await manager.ami.is_registered()
        return StatusOut(sip_registered=registered, active_calls=manager.active_calls,
                         owner_name="" if settings.owner_name == "o responsável" else settings.owner_name)

    @app.get("/api/calls", response_model=list[CallOut], dependencies=[Depends(require_token)])
    async def list_calls(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        return db.list_calls(limit)

    @app.get("/api/calls/{call_id}", response_model=CallDetailOut, dependencies=[Depends(require_token)])
    async def get_call(call_id: str) -> dict:
        call = db.get_call(call_id)
        if not call:
            raise HTTPException(404, "chamada não encontrada")
        return call

    @app.post("/api/calls", response_model=CallCreated, dependencies=[Depends(require_token)])
    async def create_call(body: CallCreate) -> CallCreated:
        try:
            call_id = await manager.start_outbound(body.to, body.purpose)
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao originar chamada")
            raise HTTPException(
                502, "não foi possível originar a chamada: o Asterisk (AMI) está indisponível ou recusou o pedido"
            ) from exc
        return CallCreated(id=call_id, status="ringing")

    @app.get("/api/messages", response_model=list[MessageOut], dependencies=[Depends(require_token)])
    async def list_messages(limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
        return db.list_messages(limit)

    # ------------------------------------------------------------------ internos (dialplan)
    @app.get("/internal/inbound", response_class=PlainTextResponse, dependencies=[Depends(require_internal)])
    async def internal_inbound(from_: str = Query("", alias="from"), to: str = "", channel: str = "") -> str:
        return manager.register_inbound(from_, to, channel or None)

    @app.get("/internal/calls/{call_id}/answered", response_class=PlainTextResponse,
             dependencies=[Depends(require_internal)])
    async def internal_answered(call_id: str, channel: str = "") -> str:
        return "ok" if manager.mark_answered(call_id, channel or None) else "unknown"

    @app.get("/internal/calls/{call_id}/hangup", response_class=PlainTextResponse,
             dependencies=[Depends(require_internal)])
    async def internal_hangup(call_id: str) -> str:
        return "ok" if manager.mark_hangup(call_id) else "unknown"

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    # ------------------------------------------------------------------ painel estático
    dash = Path(settings.dashboard_dir)
    if dash.is_dir():
        app.mount("/", StaticFiles(directory=str(dash), html=True), name="dashboard")
    else:
        log.info("Diretório do painel não encontrado (%s); servindo só a API", dash)

    return app
