import asyncio

from app import audiosocket as asock
from app.conversation import CallSession
from app.prompts import CallInfo
from app.providers.base import STTEvent, ToolUse
from app.providers.fake import FakeLLM, FakeSTT, FakeTTS

from .conftest import FakeConnection, wait_until


def make_session(settings, db, llm, tts=None, direction="inbound", transfer=None, **kw):
    call_id = db.create_call(direction, "5511988887777", "551130000000", purpose=kw.pop("purpose", None),
                             channel="PJSIP/provider-0001")
    call = CallInfo(id=call_id, direction=direction, from_number="5511988887777",
                    to_number="551130000000", purpose=db.get_call_row(call_id)["purpose"],
                    channel="PJSIP/provider-0001")
    conn = FakeConnection(call_id)
    stt = FakeSTT()
    session = CallSession(call, conn, settings, db, stt, llm, tts or FakeTTS(ms_per_char=1),
                          transfer=transfer, frame_interval=0.0005, **kw)
    return session, conn, stt


async def test_inbound_greeting_message_and_hangup(settings, db):
    llm = FakeLLM([
        ["Claro! Vou anotar o recado. ", "Pode deixar.",
         ToolUse("t1", "anotar_recado", {"nome": "João", "telefone": "11988887777",
                                         "recado": "Quer remarcar a consulta de quinta."})],
        ["Recado anotado. Até logo!", ToolUse("t2", "encerrar_ligacao", {"motivo": "fim"})],
    ])
    tts = FakeTTS(ms_per_char=1)
    session, conn, stt = make_session(settings, db, llm, tts)
    run = asyncio.create_task(session.run())

    await wait_until(lambda: stt.streams)
    # áudio do interlocutor chega ao STT
    conn.incoming.put_nowait(asock.Message(asock.KIND_AUDIO, b"\x01\x00" * 160))
    await wait_until(lambda: len(stt.streams[0].audio) == 320)
    # saudação falada antes de qualquer turno do LLM
    await wait_until(lambda: "Dra. Ana Souza" in " ".join(tts.texts))
    n_greeting = len(tts.texts)
    await wait_until(lambda: not session.player.is_playing and session._response.done())

    stt.streams[0].push(STTEvent("final", "Oi, aqui é o João, quero deixar um recado."))
    await asyncio.wait_for(run, 5)

    # LLM chamado 2x (tool round + resposta final)
    assert len(llm.calls) == 2
    first_msgs = llm.calls[0]["messages"]
    assert first_msgs[0]["role"] == "user"
    assert first_msgs[1]["role"] == "assistant"  # saudação
    assert first_msgs[-1] == {"role": "user", "content": "Oi, aqui é o João, quero deixar um recado."}
    second_msgs = llm.calls[1]["messages"]
    assert second_msgs[-1]["content"][0]["type"] == "tool_result"
    assert second_msgs[-1]["content"][0]["tool_use_id"] == "t1"
    assert "português do Brasil" in llm.calls[0]["system"]

    recados = db.list_messages()
    assert len(recados) == 1
    assert recados[0]["name"] == "João" and recados[0]["phone"] == "11988887777"
    assert "remarcar" in recados[0]["message"]

    assert conn.hangup_sent
    assert conn.sent_audio and all(len(f) == 320 for f in conn.sent_audio)
    call = db.get_call(session.call.id)
    assert call["status"] == "completed" and call["ended_at"]
    roles = [t["role"] for t in call["transcript"]]
    assert roles == ["agent", "caller", "agent"]
    assert "Até logo" in call["transcript"][-1]["text"]
    assert tts.texts[n_greeting:] == ["Claro!", "Vou anotar o recado.", "Pode deixar.", "Recado anotado.", "Até logo!"]


async def test_barge_in_stops_playback(settings, db):
    long_text = "Esta é uma resposta bem longa que vai demorar para terminar. " * 3
    llm = FakeLLM([[long_text], ["Ok, pode falar."]])
    # áudio longo: 40 ms por caractere
    tts = FakeTTS(ms_per_char=40)
    session, conn, stt = make_session(settings, db, llm, tts, direction="outbound", purpose="Confirmar consulta")
    session.frame_interval = 0.002
    run = asyncio.create_task(session.run())

    await wait_until(lambda: stt.streams)
    # saída: o LLM gera a abertura
    await wait_until(lambda: len(conn.sent_audio) > 20)
    assert session.player.is_playing
    stt.streams[0].push(STTEvent("partial", "espera"))
    await wait_until(lambda: session._response is None or session._response.done())
    sent_at_interrupt = len(conn.sent_audio)
    await asyncio.sleep(0.05)
    assert len(conn.sent_audio) <= sent_at_interrupt + 1  # parou de tocar
    assert not session.player.is_playing

    stt.streams[0].push(STTEvent("final", "Espera, quem está falando?"))
    await wait_until(lambda: len(llm.calls) == 2)
    msgs = llm.calls[1]["messages"]
    # o histórico registra só o que foi falado, marcado como interrompido
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["content"].endswith("...")
    assert len(msgs[-2]["content"]) < len(long_text)
    assert msgs[-1] == {"role": "user", "content": "Espera, quem está falando?"}
    assert "Confirmar consulta" in llm.calls[0]["system"]

    conn.caller_hangs_up()
    await asyncio.wait_for(run, 5)
    assert not conn.hangup_sent  # quem desligou foi o interlocutor
    assert db.get_call(session.call.id)["status"] == "completed"


async def test_interruption_before_speech_merges_user_turns(settings, db):
    llm = FakeLLM([["Resposta um."], ["Resposta dois."]], delay=0.2)
    session, conn, stt = make_session(settings, db, llm)
    run = asyncio.create_task(session.run())
    await wait_until(lambda: stt.streams)
    await wait_until(lambda: session._response.done())
    stt.streams[0].push(STTEvent("final", "Primeira frase"))
    await wait_until(lambda: len(llm.calls) == 1)
    stt.streams[0].push(STTEvent("final", "e mais uma coisa"))  # antes do LLM responder
    await wait_until(lambda: len(llm.calls) == 2)
    last = llm.calls[1]["messages"][-1]
    assert last["role"] == "user"
    assert [b["text"] for b in last["content"]] == ["Primeira frase", "e mais uma coisa"]
    conn.caller_hangs_up()
    await asyncio.wait_for(run, 5)


async def test_transfer_tool(settings, db):
    transferred = []

    async def transfer(call):
        transferred.append(call.id)
        return True

    llm = FakeLLM([["Vou transferir para a doutora.", ToolUse("t1", "transferir", {"motivo": "urgente"})]])
    session, conn, stt = make_session(settings, db, llm, transfer=transfer)
    assert any(t["name"] == "transferir" for t in session.tools)
    run = asyncio.create_task(session.run())
    await wait_until(lambda: stt.streams)
    stt.streams[0].push(STTEvent("final", "É urgente, preciso falar com ela."))
    await wait_until(lambda: transferred)
    assert db.get_call_row(session.call.id)["next_action"] == "transfer"
    conn.caller_hangs_up()  # Asterisk retira o canal do AudioSocket
    await asyncio.wait_for(run, 5)
    assert session.end_reason == "transferida"


async def test_transfer_unavailable_without_owner_phone(settings, db):
    settings.owner_phone = ""
    llm = FakeLLM()
    session, conn, stt = make_session(settings, db, llm, transfer=lambda c: None)
    assert not any(t["name"] == "transferir" for t in session.tools)
    assert "indisponível" in session.system


async def test_llm_error_apologizes(settings, db):
    class BrokenLLM(FakeLLM):
        async def stream_turn(self, system, messages, tools):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    tts = FakeTTS(ms_per_char=1)
    session, conn, stt = make_session(settings, db, BrokenLLM(), tts)
    run = asyncio.create_task(session.run())
    await wait_until(lambda: stt.streams)
    stt.streams[0].push(STTEvent("final", "Alô?"))
    await wait_until(lambda: any("técnico" in t for t in tts.texts))
    assert session.messages[-1]["role"] == "assistant"
    conn.caller_hangs_up()
    await asyncio.wait_for(run, 5)


async def test_silence_timeout_prompts_then_hangs_up(settings, db):
    settings.silence_timeout_s = 0.15
    tts = FakeTTS(ms_per_char=0.5)
    session, conn, stt = make_session(settings, db, FakeLLM(), tts, watchdog_interval=0.02)
    run = asyncio.create_task(session.run())
    await asyncio.wait_for(run, 5)
    assert any("ainda está aí" in t for t in tts.texts)
    assert any("encerrar" in t for t in tts.texts)
    assert conn.hangup_sent
    assert session.end_reason == "silêncio"


async def test_manager_runs_call_and_stores_summary(settings, db):
    from app.calls import CallManager

    llm = FakeLLM([["Até logo!", ToolUse("t1", "encerrar_ligacao", {"motivo": "fim"})]],
                  summary="João ligou para remarcar.")
    stt = FakeSTT()
    manager = CallManager(settings, db, stt, llm, FakeTTS(ms_per_char=1), None, frame_interval=0.0005)
    call_id = manager.register_inbound("11988887777", "1130000000", "PJSIP/provider-1")
    conn = FakeConnection(call_id)
    task = asyncio.create_task(manager.handle_connection(conn))
    await wait_until(lambda: stt.streams and manager.active_calls == 1)
    assert db.get_call_row(call_id)["status"] == "active"
    stt.streams[0].push(STTEvent("final", "Era só isso, obrigado."))
    await asyncio.wait_for(task, 5)
    await manager.wait_background()
    assert manager.active_calls == 0
    call = db.get_call(call_id)
    assert call["status"] == "completed"
    assert call["summary"] == "João ligou para remarcar."


async def test_manager_unknown_uuid_creates_inbound(settings, db):
    from app.calls import CallManager

    stt = FakeSTT()
    manager = CallManager(settings, db, stt, FakeLLM(), FakeTTS(ms_per_char=1), None, frame_interval=0.0005)
    conn = FakeConnection("11111111-2222-3333-4444-555555555555")
    task = asyncio.create_task(manager.handle_connection(conn))
    await wait_until(lambda: stt.streams)
    conn.caller_hangs_up()
    await asyncio.wait_for(task, 5)
    row = db.get_call_row(conn.call_id)
    assert row["direction"] == "inbound" and row["status"] == "completed"
