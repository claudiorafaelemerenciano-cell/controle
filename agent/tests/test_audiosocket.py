import asyncio
import uuid

import pytest

from app import audiosocket as asock

CALL_ID = "0b6c1f3e-2a4d-4e5f-8a9b-0c1d2e3f4a5b"


def test_encode_audio_header():
    pcm = b"\x01\x02" * 160
    data = asock.encode_audio(pcm)
    assert data[0] == 0x10
    assert data[1:3] == (320).to_bytes(2, "big")
    assert data[3:] == pcm


def test_encode_hangup_and_uuid():
    assert asock.encode_hangup() == b"\x00\x00\x00"
    data = asock.encode_uuid(CALL_ID)
    assert data[:3] == b"\x01\x00\x10"
    assert data[3:] == uuid.UUID(CALL_ID).bytes


def test_decode_roundtrip_and_partial():
    stream = asock.encode_uuid(CALL_ID) + asock.encode_audio(b"\xaa" * 320) + asock.encode_hangup()
    # entrega fatiada: a mensagem incompleta fica no resto
    msgs, rest = asock.decode_all(stream[:10])
    assert msgs == [] and rest == stream[:10]
    msgs, rest = asock.decode_all(stream)
    assert rest == b""
    assert [m.kind for m in msgs] == [asock.KIND_UUID, asock.KIND_AUDIO, asock.KIND_HANGUP]
    assert msgs[0].uuid == CALL_ID
    assert msgs[1].payload == b"\xaa" * 320


def test_error_kind_and_invalid_uuid():
    msgs, _ = asock.decode_all(b"\xff\x00\x01\x02")
    assert msgs[0].kind == asock.KIND_ERROR and msgs[0].payload == b"\x02"
    with pytest.raises(ValueError):
        _ = asock.Message(asock.KIND_AUDIO, b"x").uuid


def test_payload_too_big():
    with pytest.raises(ValueError):
        asock.encode_audio(b"\x00" * 70000)


async def test_server_end_to_end():
    received = {}
    done = asyncio.Event()

    async def handler(conn: asock.AudioSocketConnection):
        received["id"] = conn.call_id
        frames = []
        while True:
            msg = await conn.read_message()
            if msg is None or msg.kind == asock.KIND_HANGUP:
                break
            frames.append(msg.payload)
            await conn.send_audio(msg.payload[::-1])  # eco invertido
        received["frames"] = frames
        await conn.send_hangup()
        done.set()

    server = asock.AudioSocketServer("127.0.0.1", 0, handler)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(asock.encode_uuid(CALL_ID))
        writer.write(asock.encode_audio(b"\x01\x02" * 160))
        writer.write(asock.encode_audio(b"\x03\x04" * 160))
        await writer.drain()
        echo1 = await asock.read_message(reader)
        echo2 = await asock.read_message(reader)
        writer.write(asock.encode_hangup())
        await writer.drain()
        bye = await asock.read_message(reader)
        await asyncio.wait_for(done.wait(), 2)
        writer.close()
    finally:
        await server.stop()

    assert received["id"] == CALL_ID
    assert len(received["frames"]) == 2
    assert echo1.kind == asock.KIND_AUDIO and echo1.payload == (b"\x01\x02" * 160)[::-1]
    assert echo2.payload == (b"\x03\x04" * 160)[::-1]
    assert bye.kind == asock.KIND_HANGUP


async def test_server_rejects_connection_without_uuid():
    called = False

    async def handler(conn):
        nonlocal called
        called = True

    server = asock.AudioSocketServer("127.0.0.1", 0, handler)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(asock.encode_audio(b"\x00" * 320))
        await writer.drain()
        assert await asock.read_message(reader) is None  # servidor fechou
        writer.close()
    finally:
        await server.stop()
    assert not called
