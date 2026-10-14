"""Cliente AMI (Asterisk Manager Interface) mínimo e assíncrono.

Usado para: originar ligações de saída, consultar o registro SIP e
transferir (Redirect) uma chamada em andamento.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


def format_action(fields: dict[str, str | list[str]]) -> bytes:
    lines = []
    for key, val in fields.items():
        for v in val if isinstance(val, list) else [val]:
            lines.append(f"{key}: {v}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def parse_message(block: str) -> dict[str, str]:
    msg: dict[str, str] = {}
    for line in block.split("\r\n"):
        key, sep, val = line.partition(":")
        if sep:
            msg[key.strip()] = val.strip()
    return msg


@dataclass
class _Pending:
    future: asyncio.Future
    response: dict[str, str] | None = None
    events: list[dict[str, str]] = field(default_factory=list)
    wait_list: bool = False


class AMIError(RuntimeError):
    pass


class AMIClient:
    def __init__(self, host: str, port: int, username: str, secret: str, timeout: float = 5.0):
        self.host, self.port, self.username, self.secret = host, port, username, secret
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._pending: dict[str, _Pending] = {}
        self._originate_cbs: dict[str, Callable[[bool, str], None]] = {}
        self._ids = itertools.count(1)
        self._connect_lock = asyncio.Lock()
        self._reg_cache: tuple[float, bool] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.username and self.secret)

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _ensure(self) -> None:
        if not self.configured:
            raise AMIError("AMI não configurado (AMI_USERNAME/AMI_SECRET)")
        async with self._connect_lock:
            if self.connected:
                return
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
            await asyncio.wait_for(self._reader.readline(), self.timeout)  # banner
            self._read_task = asyncio.create_task(self._read_loop())
            resp = await self._send({"Action": "Login", "Username": self.username, "Secret": self.secret})
            if resp.get("Response") != "Success":
                await self.close()
                raise AMIError(f"login AMI falhou: {resp.get('Message')}")
            log.info("Conectado ao AMI em %s:%s", self.host, self.port)

    async def _read_loop(self) -> None:
        buf = ""
        try:
            while True:
                data = await self._reader.read(65536)
                if not data:
                    break
                buf += data.decode(errors="replace")
                while "\r\n\r\n" in buf:
                    block, buf = buf.split("\r\n\r\n", 1)
                    self._dispatch(parse_message(block))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            for p in self._pending.values():
                if not p.future.done():
                    p.future.set_exception(AMIError("conexão AMI perdida"))
            self._pending.clear()
            if self._writer:
                self._writer.close()
            self._writer = None

    def _dispatch(self, msg: dict[str, str]) -> None:
        action_id = msg.get("ActionID")
        if not action_id:
            return
        if msg.get("Event") == "OriginateResponse":
            cb = self._originate_cbs.pop(action_id, None)
            if cb:
                cb(msg.get("Response") == "Success", msg.get("Reason", ""))
            return
        pending = self._pending.get(action_id)
        if not pending:
            return
        if "Response" in msg and pending.response is None:
            pending.response = msg
            if msg.get("EventList", "").lower() == "start":
                pending.wait_list = True
                return
            self._resolve(action_id)
        elif "Event" in msg:
            if msg.get("EventList", "").lower() == "complete":
                self._resolve(action_id)
            else:
                pending.events.append(msg)

    def _resolve(self, action_id: str) -> None:
        pending = self._pending.pop(action_id, None)
        if pending and not pending.future.done():
            resp = dict(pending.response or {})
            resp["_events"] = pending.events  # type: ignore[assignment]
            pending.future.set_result(resp)

    async def _send(self, fields: dict[str, str | list[str]], action_id: str | None = None) -> dict:
        action_id = action_id or f"controle-{next(self._ids)}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[action_id] = _Pending(fut)
        self._writer.write(format_action({**fields, "ActionID": action_id}))
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, self.timeout)
        finally:
            self._pending.pop(action_id, None)

    async def action(self, fields: dict[str, str | list[str]], action_id: str | None = None) -> dict:
        await self._ensure()
        return await self._send(fields, action_id)

    async def close(self) -> None:
        if self._read_task:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task
        if self._writer:
            self._writer.close()
        self._writer = None

    # ------------------------------------------------------------------ operações
    async def is_registered(self, cache_seconds: float = 10.0) -> bool:
        """True se ao menos um registro SIP de saída estiver 'Registered'."""
        loop = asyncio.get_running_loop()
        if self._reg_cache and loop.time() - self._reg_cache[0] < cache_seconds:
            return self._reg_cache[1]
        try:
            resp = await self.action({"Action": "PJSIPShowRegistrationsOutbound"})
            ok = any(e.get("Status") == "Registered" for e in resp.get("_events", []))
        except Exception as exc:  # noqa: BLE001
            log.debug("Status de registro indisponível: %s", exc)
            ok = False
        self._reg_cache = (loop.time(), ok)
        return ok

    async def originate(
        self,
        call_id: str,
        number: str,
        endpoint: str = "provider",
        context: str = "agent-outbound",
        caller_id: str = "",
        timeout_s: int = 45,
        on_result: Callable[[bool, str], None] | None = None,
    ) -> None:
        """Origina a chamada; quando atendida ela entra em `context` com CALL_UUID definido."""
        fields: dict[str, str | list[str]] = {
            "Action": "Originate",
            "Channel": f"PJSIP/{number}@{endpoint}",
            "Context": context,
            "Exten": "s",
            "Priority": "1",
            "Timeout": str(timeout_s * 1000),
            "Async": "true",
            "Variable": [f"CALL_UUID={call_id}", f"CALL_TO={number}"],
        }
        if caller_id:
            fields["CallerID"] = caller_id
        action_id = f"orig-{call_id}"
        if on_result:
            self._originate_cbs[action_id] = on_result
        resp = await self.action(fields, action_id=action_id)
        if resp.get("Response") != "Success":
            self._originate_cbs.pop(action_id, None)
            raise AMIError(resp.get("Message", "originate falhou"))

    async def redirect(self, channel: str, context: str = "agent-transfer", exten: str = "s") -> bool:
        resp = await self.action(
            {"Action": "Redirect", "Channel": channel, "Context": context, "Exten": exten, "Priority": "1"}
        )
        return resp.get("Response") == "Success"
