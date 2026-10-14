import asyncio
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from app import audiosocket as asock  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    knowledge = tmp_path / "knowledge.md"
    knowledge.write_text("Horário de atendimento: segunda a sexta, das 9h às 18h.", encoding="utf-8")
    return Settings(
        owner_name="Dra. Ana Souza",
        owner_phone="5511999990000",
        business_description="Consultório de odontologia em São Paulo.",
        knowledge_file=str(knowledge),
        llm_provider="fake",
        stt_provider="fake",
        tts_provider="fake",
        db_path=":memory:",
        dashboard_dir=str(tmp_path / "no-dashboard"),
        silence_timeout_s=0,
        internal_token="segredo-interno",
        sip_caller_id="551130000000",
    )


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


class FakeConnection:
    """Simula a conexão AudioSocket: o teste injeta mensagens e inspeciona o que foi enviado."""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent_audio: list[bytes] = []
        self.hangup_sent = False
        self.closed = False

    async def read_message(self):
        return await self.incoming.get()

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(pcm)

    async def send_hangup(self) -> None:
        self.hangup_sent = True
        # o Asterisk encerra o socket depois do hangup
        self.incoming.put_nowait(None)

    async def close(self) -> None:
        self.closed = True

    def caller_hangs_up(self) -> None:
        self.incoming.put_nowait(asock.Message(asock.KIND_HANGUP))


@pytest.fixture
def fake_conn_cls():
    return FakeConnection


async def wait_until(predicate, timeout: float = 3.0, interval: float = 0.005):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condição não atingida a tempo")
        await asyncio.sleep(interval)
