"""Loop de conversa de uma chamada: STT -> LLM (streaming) -> TTS -> AudioSocket.

Fluxo:
- ao conectar: saudação (entrada) ou abertura gerada pelo LLM (saída);
- cada fala final do interlocutor dispara um turno do LLM;
- o texto do LLM é dividido em frases, sintetizadas e tocadas em sequência;
- barge-in: quando o interlocutor começa a falar, a resposta em curso é
  cancelada e o áudio pendente descartado;
- ferramentas: anotar_recado, encerrar_ligacao, transferir.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Protocol

from . import audiosocket as asock
from .config import Settings
from .db import Database
from .pipeline import AudioPlayer, SentenceSplitter
from .prompts import CallInfo, build_system_prompt, build_tools, default_greeting
from .providers.base import LLMProvider, STTProvider, STTStream, TextDelta, ToolUse, TTSProvider, TurnEnd

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
APOLOGY = "Desculpe, tive um probleminha técnico. Pode repetir, por favor?"
REFUSAL_REPLY = "Desculpe, não posso ajudar com isso. Posso anotar um recado?"
STILL_THERE = "Você ainda está aí?"
SILENCE_GOODBYE = "Como não ouvi resposta, vou encerrar a ligação. Até logo!"
MAX_DURATION_GOODBYE = "Precisamos encerrar a ligação agora. Obrigada pelo contato, até logo!"


class Connection(Protocol):
    async def read_message(self) -> asock.Message | None: ...
    async def send_audio(self, pcm: bytes) -> None: ...
    async def send_hangup(self) -> None: ...
    async def close(self) -> None: ...


# Ação de transferência: recebe (CallInfo) e retorna True se a transferência foi iniciada.
TransferFn = Callable[[CallInfo], Awaitable[bool]]


class CallSession:
    def __init__(
        self,
        call: CallInfo,
        conn: Connection,
        settings: Settings,
        db: Database,
        stt: STTProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        transfer: TransferFn | None = None,
        frame_interval: float = 0.02,
        watchdog_interval: float = 1.0,
    ):
        self.call = call
        self.conn = conn
        self.settings = settings
        self.db = db
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.transfer_fn = transfer
        self.frame_interval = frame_interval
        self.watchdog_interval = watchdog_interval

        self.transfer_available = bool(transfer and settings.owner_phone)
        self.system = build_system_prompt(settings, call, self.transfer_available)
        self.tools = build_tools(self.transfer_available)
        self.messages: list[dict[str, Any]] = []

        self.player: AudioPlayer | None = None
        self.stt_stream: STTStream | None = None
        self._response: asyncio.Task | None = None
        self._done = asyncio.Event()
        self._end_requested = False
        self._transfer_requested = False
        self._closing = False  # despedida/transferência em curso: fala do interlocutor não interrompe
        self._last_speech = 0.0
        self._silence_prompted = False
        self.end_reason = ""

    # ------------------------------------------------------------------ ciclo de vida
    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._started = loop.time()
        self._last_speech = self._started
        self.player = AudioPlayer(self.conn.send_audio, self.frame_interval)
        self.db.update_call(self.call.id, status="active")
        try:
            self.stt_stream = await self.stt.open_stream()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao abrir STT para %s", self.call.id)
            self.db.finish_call(self.call.id, "failed")
            await self.conn.send_hangup()
            return

        tasks = [
            asyncio.create_task(self.player.run(), name="player"),
            asyncio.create_task(self._reader_loop(), name="reader"),
            asyncio.create_task(self._stt_loop(), name="stt"),
            asyncio.create_task(self._watchdog(), name="watchdog"),
        ]
        for t in tasks:
            t.add_done_callback(self._on_task_done)
        self._start_opening()
        try:
            await self._done.wait()
        finally:
            await self._cancel_response()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.stt_stream.close()
            self.db.finish_call(self.call.id, "completed")
            log.info("Chamada %s encerrada (%s)", self.call.id, self.end_reason or "fim")

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Se uma tarefa essencial morrer sozinha, encerra a chamada em vez de ficar muda/surda."""
        if task.cancelled() or self._done.is_set():
            return
        exc = task.exception()
        if exc is not None:
            log.error("[%s] tarefa %s falhou: %r", self.call.id, task.get_name(), exc)
            self._finish(f"erro interno ({task.get_name()})")
        elif task.get_name() in ("reader", "stt"):
            self._finish("STT/áudio encerrado" if task.get_name() == "stt" else "interlocutor desligou")

    def _finish(self, reason: str) -> None:
        if not self.end_reason:
            self.end_reason = reason
        self._done.set()

    def _start_opening(self) -> None:
        if self.call.direction == "outbound":
            self.messages.append({
                "role": "user",
                "content": "(A ligação foi atendida. Comece a conversa conforme o objetivo.)",
            })
            self._response = asyncio.create_task(self._respond())
        else:
            greeting = default_greeting(self.settings)
            self.messages.append({
                "role": "user",
                "content": f"(Ligação recebida de {self.call.from_number or 'número não identificado'}.)",
            })
            self.messages.append({"role": "assistant", "content": greeting})
            self._response = asyncio.create_task(self._speak(greeting))

    # ------------------------------------------------------------------ entrada de áudio
    async def _reader_loop(self) -> None:
        while True:
            msg = await self.conn.read_message()
            if msg is None or msg.kind == asock.KIND_HANGUP:
                self._finish("interlocutor desligou")
                return
            if msg.kind == asock.KIND_ERROR:
                log.warning("AudioSocket erro na chamada %s: %r", self.call.id, msg.payload)
                self._finish("erro no AudioSocket")
                return
            if msg.kind == asock.KIND_AUDIO and self.stt_stream is not None:
                await self.stt_stream.send_audio(msg.payload)

    async def _stt_loop(self) -> None:
        async for ev in self.stt_stream.events():
            now = asyncio.get_running_loop().time()
            if ev.kind == "partial":
                self._last_speech = now
                if len(ev.text.strip()) >= self.settings.barge_in_min_chars:
                    await self._barge_in()
            elif ev.kind == "final":
                self._last_speech = now
                self._silence_prompted = False
                await self._on_utterance(ev.text)

    async def _on_utterance(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._closing:
            log.info("[%s] fala ignorada durante o encerramento: %s", self.call.id, text)
            return
        await self._barge_in()
        log.info("[%s] interlocutor: %s", self.call.id, text)
        self.db.add_turn(self.call.id, "caller", text)
        self._append_user_text(text)
        self._response = asyncio.create_task(self._respond())

    def _append_user_text(self, text: str) -> None:
        """Acrescenta a fala ao histórico, mesclando se a última mensagem já é do usuário."""
        if self.messages and self.messages[-1]["role"] == "user":
            last = self.messages[-1]
            content = last["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            last["content"] = [*content, {"type": "text", "text": text}]
        else:
            self.messages.append({"role": "user", "content": text})

    # ------------------------------------------------------------------ barge-in
    async def _barge_in(self) -> None:
        if self._closing:
            return
        if self._response and not self._response.done():
            log.info("[%s] barge-in: interrompendo a fala", self.call.id)
            await self._cancel_response()

    async def _cancel_response(self) -> None:
        task, self._response = self._response, None
        if self.player:
            self.player.clear()
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self.player:
            self.player.clear()

    # ------------------------------------------------------------------ saída de voz
    async def _tts_worker(self, queue: asyncio.Queue, spoken: list[str]) -> None:
        """Sintetiza frases em ordem; marca cada frase como falada ao começar a tocar."""
        while True:
            sentence = await queue.get()
            if sentence is None:
                return
            first = True
            try:
                async for chunk in self.tts.synthesize(sentence):
                    if first:
                        self.player.mark(lambda s=sentence: spoken.append(s))
                        first = False
                    self.player.enqueue_pcm(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("[%s] falha no TTS para: %r", self.call.id, sentence)
            self.player.end_segment()

    async def _speak(self, text: str) -> None:
        """Fala um texto fixo (sem LLM) e registra na transcrição."""
        spoken: list[str] = []
        queue: asyncio.Queue = asyncio.Queue()
        splitter = SentenceSplitter()
        for s in splitter.feed(text + " ") + splitter.flush():
            queue.put_nowait(s)
        queue.put_nowait(None)
        try:
            await self._tts_worker(queue, spoken)
            await self.player.wait_drained()
        finally:
            self.db.add_turn(self.call.id, "agent", " ".join(spoken))

    async def _respond(self) -> None:
        """Um turno completo do agente, incluindo rodadas de ferramentas."""
        spoken_total: list[str] = []
        spoken_round: list[str] = []
        self._end_requested = self._transfer_requested = False
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                spoken_round = []
                queue: asyncio.Queue = asyncio.Queue()
                worker = asyncio.create_task(self._tts_worker(queue, spoken_round))
                splitter = SentenceSplitter()
                tool_uses: list[ToolUse] = []
                end: TurnEnd | None = None
                try:
                    stream = self.llm.stream_turn(self.system, self.messages, self.tools)
                    async with contextlib.aclosing(stream):
                        async for ev in stream:
                            if isinstance(ev, TextDelta):
                                for s in splitter.feed(ev.text):
                                    queue.put_nowait(s)
                            elif isinstance(ev, ToolUse):
                                tool_uses.append(ev)
                            elif isinstance(ev, TurnEnd):
                                end = ev
                    for s in splitter.flush():
                        queue.put_nowait(s)
                    queue.put_nowait(None)
                    await worker
                finally:
                    if not worker.done():
                        worker.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await worker

                spoken_total.extend(spoken_round)
                if end is None:
                    raise RuntimeError("LLM terminou sem TurnEnd")
                if end.stop_reason == "refusal":
                    await self._say_and_record(REFUSAL_REPLY, spoken_total)
                    break
                if end.content:
                    self.messages.append({"role": "assistant", "content": end.content})
                spoken_round = []  # já está no histórico
                if not tool_uses:
                    break
                results = [await self._run_tool(tu) for tu in tool_uses]
                self.messages.append({"role": "user", "content": results})
                if self._end_requested or self._transfer_requested:
                    break  # não precisa de nova rodada: a ligação vai terminar
            await self.player.wait_drained()
        except asyncio.CancelledError:
            # Interrompido: registra no histórico só o que chegou a ser falado.
            if spoken_round:
                self.messages.append({"role": "assistant", "content": " ".join(spoken_round) + " ..."})
            self._end_requested = self._transfer_requested = False
            raise
        except Exception:  # noqa: BLE001
            log.exception("[%s] erro no turno do LLM", self.call.id)
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.append({"role": "assistant", "content": APOLOGY})
            await self._say_and_record(APOLOGY, spoken_total)
            await self.player.wait_drained()
        finally:
            self.db.add_turn(self.call.id, "agent", " ".join(spoken_total))

        if self._transfer_requested:
            await self._do_transfer()
        elif self._end_requested:
            await self._hangup("agente encerrou")

    async def _say_and_record(self, text: str, spoken: list[str]) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        queue.put_nowait(text)
        queue.put_nowait(None)
        await self._tts_worker(queue, spoken)

    # ------------------------------------------------------------------ ferramentas
    async def _run_tool(self, tu: ToolUse) -> dict[str, Any]:
        args = tu.input if isinstance(tu.input, dict) else {}

        def result(content: str, error: bool = False) -> dict[str, Any]:
            r = {"type": "tool_result", "tool_use_id": tu.id, "content": content}
            if error:
                r["is_error"] = True
            return r

        log.info("[%s] ferramenta %s %s", self.call.id, tu.name, args)
        if tu.name == "anotar_recado":
            recado = args.get("recado")
            if not isinstance(recado, str) or not recado.strip():
                return result("Campo 'recado' ausente ou inválido.", error=True)
            name = args.get("nome") if isinstance(args.get("nome"), str) else None
            phone = args.get("telefone") if isinstance(args.get("telefone"), str) else None
            if not phone and self.call.direction == "inbound":
                phone = self.call.from_number or None
            self.db.add_message(self.call.id, recado.strip(), (name or "").strip() or None,
                                (phone or "").strip() or None)
            return result("Recado anotado com sucesso.")
        if tu.name == "encerrar_ligacao":
            self._end_requested = True
            self._closing = True
            return result("A ligação será encerrada após sua última fala.")
        if tu.name == "transferir":
            if not self.transfer_available:
                return result("Transferência indisponível. Ofereça anotar um recado.", error=True)
            self._transfer_requested = True
            self._closing = True
            return result("Transferindo após sua última fala.")
        return result(f"Ferramenta desconhecida: {tu.name}", error=True)

    async def _do_transfer(self) -> None:
        ok = False
        try:
            ok = await self.transfer_fn(self.call)
        except Exception:  # noqa: BLE001
            log.exception("[%s] falha na transferência", self.call.id)
        if ok:
            self.db.update_call(self.call.id, next_action="transfer")
            # O Asterisk tira o canal do AudioSocket; o reader detecta o fim.
            self.end_reason = "transferida"
        else:
            self._transfer_requested = False
            self._closing = False
            text = "Não consegui transferir agora. Posso anotar um recado?"
            self.messages.append({"role": "user", "content": "(A transferência falhou.)"})
            self.messages.append({"role": "assistant", "content": text})
            self._response = asyncio.create_task(self._speak(text))

    async def _hangup(self, reason: str) -> None:
        await self.player.wait_drained()
        await asyncio.sleep(0.3)  # deixa o último áudio chegar ao interlocutor
        await self.conn.send_hangup()
        self._finish(reason)

    # ------------------------------------------------------------------ watchdog
    async def _watchdog(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self.watchdog_interval)
            now = loop.time()
            if now - self._started > self.settings.max_call_seconds:
                await self._final_words(MAX_DURATION_GOODBYE, "duração máxima")
                return
            busy = (self._response and not self._response.done()) or self.player.is_playing
            if busy or self.settings.silence_timeout_s <= 0:
                continue
            quiet_since = max(self._last_speech, self.player.idle_since)
            if now - quiet_since > self.settings.silence_timeout_s:
                if not self._silence_prompted:
                    self._silence_prompted = True
                    self._last_speech = now
                    self._response = asyncio.create_task(self._speak(STILL_THERE))
                else:
                    await self._final_words(SILENCE_GOODBYE, "silêncio")
                    return

    async def _final_words(self, text: str, reason: str) -> None:
        self._closing = True
        await self._cancel_response()
        self._response = asyncio.create_task(self._speak(text))
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._response
        await self._hangup(reason)
