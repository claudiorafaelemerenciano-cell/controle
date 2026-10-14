import asyncio

from app.audio import FRAME_BYTES
from app.pipeline import AudioPlayer, SentenceSplitter


def test_splitter_streams_sentences():
    sp = SentenceSplitter()
    out = []
    for piece in ["Olá! Tudo", " bem? Eu sou a Sofia", ". Como posso", " ajudar"]:
        out += sp.feed(piece)
    assert out == ["Olá!", "Tudo bem?", "Eu sou a Sofia."]
    assert sp.flush() == ["Como posso ajudar"]
    assert sp.flush() == []


def test_splitter_keeps_abbreviations_and_decimals():
    sp = SentenceSplitter()
    out = sp.feed("A Dra. Ana atende às 9.30 amanhã. Certo? ")
    assert out == ["A Dra. Ana atende às 9.30 amanhã.", "Certo?"]


def test_splitter_breaks_long_text_on_comma():
    sp = SentenceSplitter(max_chars=60)
    text = "Nós atendemos convênios, particulares e também empresas parceiras, com horário estendido"
    out = sp.feed(text)
    assert out and out[0].endswith(",")
    assert all(len(s) <= 60 for s in out)


async def test_player_paces_and_marks():
    sent = []
    times = []
    loop = asyncio.get_running_loop()

    async def send(frame):
        sent.append(frame)
        times.append(loop.time())

    player = AudioPlayer(send, frame_interval=0.01, lead=0)
    task = asyncio.create_task(player.run())
    marks = []
    player.mark(lambda: marks.append("a"))
    player.enqueue_pcm(b"\x01" * (FRAME_BYTES * 5 + 10))
    player.end_segment()
    await asyncio.sleep(0)
    assert player.is_playing
    await asyncio.wait_for(player.wait_drained(), 2)
    task.cancel()
    assert marks == ["a"]
    assert len(sent) == 6 and all(len(f) == FRAME_BYTES for f in sent)
    # 6 frames a 10 ms => ~50 ms entre o primeiro e o último
    assert times[-1] - times[0] >= 0.04


async def test_player_clear_drops_pending_audio():
    sent = []

    async def send(frame):
        sent.append(frame)

    player = AudioPlayer(send, frame_interval=0.005, lead=0)
    task = asyncio.create_task(player.run())
    player.enqueue_pcm(b"\x00" * FRAME_BYTES * 200)  # 1 s de áudio "acelerado"
    await asyncio.sleep(0.03)
    player.clear()
    count = len(sent)
    await asyncio.sleep(0.05)
    task.cancel()
    assert 0 < count < 200
    assert len(sent) <= count + 1  # no máximo o frame que já estava saindo
    assert not player.is_playing
