"""Protocolo AudioSocket do Asterisk (app_audiosocket).

Cada mensagem: 1 byte de tipo + 2 bytes de tamanho (big-endian) + payload.

  0x00  hangup  (sem payload)
  0x01  UUID    (16 bytes, primeira mensagem enviada pelo Asterisk)
  0x10  áudio   (slin 8 kHz, 16-bit, mono; o Asterisk manda frames de 320 bytes)
  0xff  erro    (payload opcional com código de erro)
"""
from __future__ import annotations

import asyncio
import logging
import struct
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

KIND_HANGUP = 0x00
KIND_UUID = 0x01
KIND_AUDIO = 0x10
KIND_ERROR = 0xFF

HEADER = struct.Struct(">BH")
MAX_PAYLOAD = 0xFFFF


@dataclass(frozen=True)
class Message:
    kind: int
    payload: bytes = b""

    @property
    def uuid(self) -> str:
        if self.kind != KIND_UUID or len(self.payload) != 16:
            raise ValueError("mensagem não é um UUID válido")
        return str(uuid.UUID(bytes=self.payload))


def encode(kind: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload grande demais para AudioSocket")
    return HEADER.pack(kind, len(payload)) + payload


def encode_audio(pcm: bytes) -> bytes:
    return encode(KIND_AUDIO, pcm)


def encode_hangup() -> bytes:
    return encode(KIND_HANGUP)


def encode_uuid(call_id: str) -> bytes:
    return encode(KIND_UUID, uuid.UUID(call_id).bytes)


def decode_all(data: bytes) -> tuple[list[Message], bytes]:
    """Decodifica quantas mensagens completas houver; devolve (mensagens, resto)."""
    msgs: list[Message] = []
    offset = 0
    while len(data) - offset >= HEADER.size:
        kind, length = HEADER.unpack_from(data, offset)
        end = offset + HEADER.size + length
        if end > len(data):
            break
        msgs.append(Message(kind, bytes(data[offset + HEADER.size:end])))
        offset = end
    return msgs, bytes(data[offset:])


async def read_message(reader: asyncio.StreamReader) -> Message | None:
    """Lê uma mensagem; None em EOF."""
    try:
        header = await reader.readexactly(HEADER.size)
        kind, length = HEADER.unpack(header)
        payload = await reader.readexactly(length) if length else b""
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    return Message(kind, payload)


class AudioSocketConnection:
    """Uma chamada conectada via AudioSocket."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, call_id: str):
        self.reader = reader
        self.writer = writer
        self.call_id = call_id
        self.closed = False
        self._lock = asyncio.Lock()

    async def read_message(self) -> Message | None:
        if self.closed:
            return None
        return await read_message(self.reader)

    async def _write(self, data: bytes) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                self.writer.write(data)
                await self.writer.drain()
            except (ConnectionError, RuntimeError):
                self.closed = True

    async def send_audio(self, pcm: bytes) -> None:
        await self._write(encode_audio(pcm))

    async def send_hangup(self) -> None:
        await self._write(encode_hangup())

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, RuntimeError):
            pass


CallHandler = Callable[[AudioSocketConnection], Awaitable[None]]


class AudioSocketServer:
    """Servidor TCP: aguarda a mensagem de UUID e entrega a conexão ao handler."""

    def __init__(self, host: str, port: int, handler: CallHandler, uuid_timeout: float = 5.0):
        self.host = host
        self.port = port
        self.handler = handler
        self.uuid_timeout = uuid_timeout
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_client, self.host, self.port)
        sock = self._server.sockets[0].getsockname()
        self.port = sock[1]
        log.info("AudioSocket ouvindo em %s:%s", sock[0], sock[1])

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            first = await asyncio.wait_for(read_message(reader), self.uuid_timeout)
        except asyncio.TimeoutError:
            first = None
        if first is None or first.kind != KIND_UUID:
            log.warning("Conexão AudioSocket de %s sem UUID; fechando", peer)
            writer.close()
            return
        try:
            call_id = first.uuid
        except ValueError:
            log.warning("UUID inválido de %s", peer)
            writer.close()
            return
        conn = AudioSocketConnection(reader, writer, call_id)
        log.info("Chamada %s conectada via AudioSocket (%s)", call_id, peer)
        try:
            await self.handler(conn)
        except Exception:  # noqa: BLE001 - nunca derrubar o servidor por uma chamada
            log.exception("Erro na chamada %s", call_id)
        finally:
            await conn.close()
