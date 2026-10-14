"""Gerencia o ciclo de vida das chamadas (entrada e saída) e as sessões ativas."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .asterisk import AMIClient
from .config import Settings
from .conversation import CallSession
from .db import Database
from .prompts import CallInfo

log = logging.getLogger(__name__)

PHONE_RE = re.compile(r"^\+?[0-9]{3,20}$")


def normalize_number(raw: str) -> str:
    """Remove espaços, traços, parênteses e pontos."""
    return re.sub(r"[\s\-().]", "", raw or "")


class CallManager:
    def __init__(self, settings: Settings, db: Database, stt, llm, tts, ami: AMIClient | None = None,
                 frame_interval: float = 0.02):
        self.settings = settings
        self.db = db
        self.stt, self.llm, self.tts = stt, llm, tts
        self.ami = ami
        self.frame_interval = frame_interval
        self.sessions: dict[str, CallSession] = {}
        self._bg: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ registro
    def register_inbound(self, from_number: str, to_number: str, channel: str | None = None) -> str:
        """Chamado pelo dialplan (via CURL) antes do AudioSocket; devolve o UUID da chamada."""
        call_id = self.db.create_call("inbound", from_number, to_number, status="ringing", channel=channel)
        log.info("Chamada de entrada %s: %s -> %s", call_id, from_number, to_number)
        return call_id

    def mark_answered(self, call_id: str, channel: str | None) -> bool:
        if not self.db.get_call_row(call_id):
            return False
        if channel:
            self.db.update_call(call_id, channel=channel)
        return True

    def mark_hangup(self, call_id: str) -> bool:
        """Chamado pelo dialplan no 'h': encerra registros que nunca chegaram ao AudioSocket."""
        row = self.db.get_call_row(call_id)
        if not row:
            return False
        if call_id not in self.sessions and row["status"] in ("ringing", "active"):
            self.db.finish_call(call_id, "completed" if row["status"] == "active" else "failed")
        return True

    async def start_outbound(self, to: str, purpose: str) -> str:
        to = normalize_number(to)
        call_id = self.db.create_call(
            "outbound", self.settings.sip_caller_id, to, status="ringing", purpose=purpose
        )
        if self.ami is None:
            self.db.finish_call(call_id, "failed")
            raise RuntimeError("AMI indisponível")

        def on_result(success: bool, reason: str) -> None:
            if not success:
                log.warning("Originate %s falhou (reason=%s)", call_id, reason)
                self.db.finish_call(call_id, "failed")

        try:
            await self.ami.originate(
                call_id, to, endpoint=self.settings.sip_trunk_endpoint, caller_id=self.settings.sip_caller_id,
                timeout_s=self.settings.originate_timeout_s, on_result=on_result,
            )
        except Exception:
            self.db.finish_call(call_id, "failed")
            raise
        return call_id

    # ------------------------------------------------------------------ AudioSocket
    async def handle_connection(self, conn: Any) -> None:
        call_id = conn.call_id
        row = self.db.get_call_row(call_id)
        if row is None:
            # UUID gerado pelo próprio dialplan (CURL falhou): registra como entrada desconhecida.
            self.db.create_call("inbound", "", "", status="ringing", call_id=call_id)
            row = self.db.get_call_row(call_id)
        info = CallInfo(
            id=call_id, direction=row["direction"], from_number=row["from_number"],
            to_number=row["to_number"], purpose=row["purpose"], channel=row["channel"],
        )
        session = CallSession(
            info, conn, self.settings, self.db, self.stt, self.llm, self.tts,
            transfer=self._transfer if self.ami else None, frame_interval=self.frame_interval,
        )
        self.sessions[call_id] = session
        try:
            await session.run()
        finally:
            self.sessions.pop(call_id, None)
            self._spawn(self._summarize(call_id))

    async def _transfer(self, call: CallInfo) -> bool:
        row = self.db.get_call_row(call.id)
        channel = (row or {}).get("channel") or call.channel
        if not (self.ami and channel and self.settings.owner_phone):
            return False
        return await self.ami.redirect(channel, "agent-transfer", "s")

    # ------------------------------------------------------------------ resumo
    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _summarize(self, call_id: str) -> None:
        transcript = self.db.get_transcript(call_id)
        if not any(t["role"] == "caller" for t in transcript):
            return
        lines = [("Interlocutor" if t["role"] == "caller" else "Assistente") + f": {t['text']}" for t in transcript]
        recados = self.db.list_messages(call_id=call_id)
        if recados:
            lines.append("")
            lines.append("Recados anotados: " + "; ".join(r["message"] for r in recados))
        try:
            summary = await self.llm.summarize("\n".join(lines))
        except Exception:  # noqa: BLE001
            log.exception("Falha ao gerar resumo de %s", call_id)
            return
        if summary:
            self.db.update_call(call_id, summary=summary)

    async def wait_background(self) -> None:
        if self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)

    @property
    def active_calls(self) -> int:
        return len(self.sessions)
