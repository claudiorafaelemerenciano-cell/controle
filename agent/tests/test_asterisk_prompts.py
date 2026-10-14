import asyncio

from app.asterisk import AMIClient, format_action, parse_message
from app.prompts import CallInfo, build_system_prompt, build_tools, default_greeting


def test_format_and_parse():
    data = format_action({"Action": "Originate", "Variable": ["A=1", "B=2"]})
    assert data == b"Action: Originate\r\nVariable: A=1\r\nVariable: B=2\r\n\r\n"
    assert parse_message("Response: Success\r\nMessage: ok: yes") == {"Response": "Success", "Message": "ok: yes"}


async def test_ami_client_against_fake_server():
    """Servidor AMI falso: login, originate (com OriginateResponse) e lista de registros."""
    received = []
    orig_result = asyncio.get_running_loop().create_future()

    async def on_client(reader, writer):
        writer.write(b"Asterisk Call Manager/9.0.0\r\n")
        buf = b""
        while True:
            data = await reader.read(4096)
            if not data:
                break
            buf += data
            while b"\r\n\r\n" in buf:
                block, buf = buf.split(b"\r\n\r\n", 1)
                msg = parse_message(block.decode())
                received.append(msg)
                aid = msg["ActionID"]
                action = msg["Action"]
                if action == "Login":
                    writer.write(f"Response: Success\r\nActionID: {aid}\r\n\r\n".encode())
                elif action == "Originate":
                    writer.write(f"Response: Success\r\nActionID: {aid}\r\nMessage: queued\r\n\r\n".encode())
                    writer.write(f"Event: OriginateResponse\r\nActionID: {aid}\r\nResponse: Failure\r\nReason: 3\r\n\r\n".encode())
                elif action == "PJSIPShowRegistrationsOutbound":
                    writer.write(f"Response: Success\r\nActionID: {aid}\r\nEventList: start\r\n\r\n".encode())
                    writer.write(f"Event: OutboundRegistrationDetail\r\nActionID: {aid}\r\nStatus: Registered\r\n\r\n".encode())
                    writer.write(f"Event: OutboundRegistrationDetailComplete\r\nActionID: {aid}\r\nEventList: Complete\r\n\r\n".encode())
                await writer.drain()

    server = await asyncio.start_server(on_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    ami = AMIClient("127.0.0.1", port, "user", "pass")
    try:
        await ami.originate("abc", "11999998888", caller_id="1130000000",
                            on_result=lambda ok, reason: orig_result.set_result((ok, reason)))
        assert await asyncio.wait_for(orig_result, 2) == (False, "3")
        assert await ami.is_registered() is True
    finally:
        await ami.close()
        server.close()

    orig = next(m for m in received if m["Action"] == "Originate")
    assert orig["Channel"] == "PJSIP/11999998888@provider"
    assert orig["Context"] == "agent-outbound"
    assert orig["CallerID"] == "1130000000"


async def test_ami_unreachable_means_not_registered():
    ami = AMIClient("127.0.0.1", 1, "u", "p", timeout=0.5)
    assert await ami.is_registered() is False


def test_system_prompt_ptbr(settings):
    call = CallInfo(id="x", direction="inbound", from_number="11988887777")
    prompt = build_system_prompt(settings, call, transfer_available=True)
    assert "Dra. Ana Souza" in prompt
    assert "Consultório de odontologia" in prompt
    assert "segunda a sexta" in prompt  # knowledge.md
    assert "SEMPRE em português do Brasil" in prompt
    assert "markdown" in prompt and "emojis" in prompt
    assert "como seriam falados" in prompt
    assert "11988887777" in prompt
    assert "{{" not in prompt
    assert "Transferência para o responsável: disponível" in prompt


def test_outbound_prompt_has_purpose(settings):
    call = CallInfo(id="x", direction="outbound", to_number="1133334444", purpose="Lembrar da consulta")
    prompt = build_system_prompt(settings, call)
    assert "Lembrar da consulta" in prompt and "1133334444" in prompt


def test_tools_and_greeting(settings):
    names = [t["name"] for t in build_tools(False)]
    assert names == ["anotar_recado", "encerrar_ligacao"]
    assert "transferir" in [t["name"] for t in build_tools(True)]
    for t in build_tools(True):
        assert t["input_schema"]["additionalProperties"] is False
    assert "Dra. Ana Souza" in default_greeting(settings)
    settings.greeting = "Alô, consultório!"
    assert default_greeting(settings) == "Alô, consultório!"
