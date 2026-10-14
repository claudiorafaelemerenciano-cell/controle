"""Ponto de entrada: servidor AudioSocket (:9092) + API HTTP (:8080) no mesmo loop asyncio."""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from .api import RedactTokenFilter, create_app
from .asterisk import AMIClient
from .audiosocket import AudioSocketServer
from .calls import CallManager
from .config import Settings
from .db import Database
from .providers.factory import make_llm, make_stt, make_tts


async def amain() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("controle")
    logging.getLogger("uvicorn.access").addFilter(RedactTokenFilter())
    if not settings.api_token and settings.http_host not in ("127.0.0.1", "::1", "localhost"):
        log.error("API_TOKEN vazio com HTTP_HOST=%s: qualquer pessoa na rede pode ver as ligações e "
                  "DISCAR em seu nome. Defina API_TOKEN no .env.", settings.http_host)

    db = Database(settings.db_path)
    stt, llm, tts = make_stt(settings), make_llm(settings), make_tts(settings)
    ami = AMIClient(settings.ami_host, settings.ami_port, settings.ami_username, settings.ami_secret)
    if not ami.configured:
        log.warning("AMI_USERNAME/AMI_SECRET ausentes: sem ligações de saída, transferência ou status SIP")
    manager = CallManager(settings, db, stt, llm, tts, ami if ami.configured else None)

    audio_server = AudioSocketServer(settings.audiosocket_host, settings.audiosocket_port,
                                     manager.handle_connection)
    await audio_server.start()

    app = create_app(settings, db, manager)
    config = uvicorn.Config(app, host=settings.http_host, port=settings.http_port,
                            log_level=settings.log_level.lower(), proxy_headers=False)
    server = uvicorn.Server(config)
    log.info("Secretária de %s pronta (LLM=%s, STT=%s, TTS=%s)", settings.owner_name,
             settings.llm_provider, settings.stt_provider, settings.tts_provider)
    try:
        await server.serve()
    finally:
        await audio_server.stop()
        await manager.wait_background()
        await ami.close()
        db.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
