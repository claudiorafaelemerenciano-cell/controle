import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.calls import CallManager
from app.providers.fake import FakeLLM, FakeSTT, FakeTTS

ISO_Z = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
CALL_KEYS = {"id", "direction", "from_number", "to_number", "started_at", "ended_at", "status", "summary"}


class FakeAMI:
    configured = True

    def __init__(self, registered=True, fail=False):
        self.registered = registered
        self.fail = fail
        self.originated = []
        self.on_result = None

    async def is_registered(self):
        return self.registered

    async def originate(self, call_id, number, endpoint="provider", context="agent-outbound",
                        caller_id="", timeout_s=45, on_result=None):
        if self.fail:
            raise RuntimeError("AMI fora do ar")
        self.originated.append({"id": call_id, "number": number, "caller_id": caller_id})
        self.on_result = on_result

    async def redirect(self, channel, context="agent-transfer", exten="s"):
        return True


@pytest.fixture
def ami():
    return FakeAMI()


@pytest.fixture
def manager(settings, db, ami):
    return CallManager(settings, db, FakeSTT(), FakeLLM(), FakeTTS(), ami)


@pytest.fixture
def client(settings, db, manager):
    with TestClient(create_app(settings, db, manager)) as c:
        yield c


def test_status(client, settings):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json() == {"sip_registered": True, "active_calls": 0, "owner_name": "Dra. Ana Souza"}


def test_status_without_ami(settings, db):
    m = CallManager(settings, db, FakeSTT(), FakeLLM(), FakeTTS(), None)
    with TestClient(create_app(settings, db, m)) as c:
        assert c.get("/api/status").json()["sip_registered"] is False


def test_calls_list_and_detail_shape(client, db):
    cid = db.create_call("inbound", "5511988887777", "551130000000")
    db.update_call(cid, status="active")
    db.add_turn(cid, "agent", "Olá, como posso ajudar?")
    db.add_turn(cid, "caller", "Quero marcar uma consulta.")
    db.finish_call(cid)
    db.update_call(cid, summary="Paciente quer marcar consulta.")
    db.create_call("outbound", "551130000000", "5511977776666")

    r = client.get("/api/calls?limit=50")
    assert r.status_code == 200
    calls = r.json()
    assert len(calls) == 2
    for c in calls:
        assert set(c) == CALL_KEYS
        assert c["direction"] in ("inbound", "outbound")
        assert c["status"] in ("ringing", "active", "completed", "failed")
    by_id = {c["id"]: c for c in calls}
    done = by_id[cid]
    assert done["status"] == "completed" and done["ended_at"] and done["summary"]
    import re
    assert re.match(ISO_Z, done["started_at"]) and re.match(ISO_Z, done["ended_at"])

    r = client.get(f"/api/calls/{cid}")
    assert r.status_code == 200
    detail = r.json()
    assert set(detail) == CALL_KEYS | {"transcript"}
    assert [t["role"] for t in detail["transcript"]] == ["agent", "caller"]
    assert set(detail["transcript"][0]) == {"role", "text", "ts"}

    assert client.get("/api/calls?limit=1").json().__len__() == 1
    assert client.get("/api/calls?limit=0").status_code == 422


def test_call_not_found(client):
    assert client.get("/api/calls/nao-existe").status_code == 404


def test_create_outbound_call(client, ami, db):
    r = client.post("/api/calls", json={"to": "+55 (11) 97777-6666", "purpose": "Confirmar consulta de amanhã"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "status"} and body["status"] == "ringing"
    assert ami.originated == [{"id": body["id"], "number": "+5511977776666", "caller_id": "551130000000"}]
    row = db.get_call_row(body["id"])
    assert row["direction"] == "outbound" and row["purpose"] == "Confirmar consulta de amanhã"
    assert row["to_number"] == "+5511977776666"
    # originate assíncrono falhou (não atendeu) -> failed
    ami.on_result(False, "3")
    assert client.get(f"/api/calls/{body['id']}").json()["status"] == "failed"


@pytest.mark.parametrize("payload", [
    {"to": "abc", "purpose": "x"},
    {"to": "12", "purpose": "x"},
    {"to": "11999998888", "purpose": "   "},
    {"to": "11999998888"},
])
def test_create_call_validation(client, payload):
    assert client.post("/api/calls", json=payload).status_code == 422


def test_create_call_ami_failure(settings, db):
    m = CallManager(settings, db, FakeSTT(), FakeLLM(), FakeTTS(), FakeAMI(fail=True))
    with TestClient(create_app(settings, db, m)) as c:
        r = c.post("/api/calls", json={"to": "11999998888", "purpose": "teste"})
        assert r.status_code == 502
        assert c.get("/api/calls").json()[0]["status"] == "failed"


def test_messages(client, db):
    cid = db.create_call("inbound", "5511988887777", "551130000000")
    db.add_message(cid, "Ligar de volta sobre orçamento", "Maria", "11988887777")
    db.add_message(cid, "Sem nome")
    r = client.get("/api/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) == 2
    for m in msgs:
        assert set(m) == {"id", "call_id", "name", "phone", "message", "created_at"}
        assert m["call_id"] == cid
    assert {m["name"] for m in msgs} == {"Maria", None}


def test_bearer_auth(settings, db, manager):
    settings.api_token = "s3cret"
    with TestClient(create_app(settings, db, manager)) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/calls", headers={"Authorization": "Bearer errado"}).status_code == 401
        assert c.get("/api/calls", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/messages", headers={"Authorization": "bearer s3cret"}).status_code == 200
        assert c.get("/healthz").status_code == 200


def test_internal_endpoints(client, db):
    assert client.get("/internal/inbound?from=11988887777&to=1130000000").status_code == 403
    r = client.get("/internal/inbound", params={"token": "segredo-interno", "from": "11988887777",
                                                "to": "1130000000", "channel": "PJSIP/provider-00000001"})
    assert r.status_code == 200
    call_id = r.text
    assert len(call_id) == 36
    row = db.get_call_row(call_id)
    assert row["from_number"] == "11988887777" and row["status"] == "ringing"
    assert row["channel"] == "PJSIP/provider-00000001"
    # desligou antes do AudioSocket -> failed
    assert client.get(f"/internal/calls/{call_id}/hangup?token=segredo-interno").text == "ok"
    assert db.get_call_row(call_id)["status"] == "failed"
    assert client.get("/internal/calls/x/answered?token=segredo-interno&channel=a").text == "unknown"


def test_serves_dashboard(settings, db, manager, tmp_path):
    dash = tmp_path / "dashboard"
    dash.mkdir()
    (dash / "index.html").write_text("<h1>painel</h1>")
    settings.dashboard_dir = str(dash)
    with TestClient(create_app(settings, db, manager)) as c:
        assert "painel" in c.get("/").text
        assert c.get("/api/status").status_code == 200


def test_validation_error_is_ptbr_string_detail(client):
    r = client.post("/api/calls", json={"to": "abc", "purpose": "oi"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str) and "número inválido" in detail and "Value error" not in detail
    r = client.post("/api/calls", json={})
    assert r.status_code == 422 and "campo obrigatório" in r.json()["detail"]


def test_originate_failure_does_not_leak_exception_text(settings, db):
    m = CallManager(settings, db, FakeSTT(), FakeLLM(), FakeTTS(), FakeAMI(fail=True))
    with TestClient(create_app(settings, db, m)) as c:
        r = c.post("/api/calls", json={"to": "5511988887777", "purpose": "oi"})
    assert r.status_code == 502
    assert isinstance(r.json()["detail"], str) and "AMI" in r.json()["detail"]
    assert "fora do ar" not in r.json()["detail"]


def test_non_ascii_tokens_do_not_500(settings, db, manager):
    settings.api_token = "segredo"
    with TestClient(create_app(settings, db, manager)) as c:
        r = c.get("/api/status", headers={"Authorization": "Bearer sécret".encode("latin-1")})
        assert r.status_code == 401
        assert c.get("/internal/inbound?token=s%C3%A9cret").status_code == 403
        assert c.get("/api/status", headers={"Authorization": "Bearer segredo"}).status_code == 200


def test_access_log_redacts_internal_token():
    import logging
    from app.api import RedactTokenFilter

    rec = logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:1", "GET", "/internal/inbound?token=abc123&from=1", "1.1", 200), None)
    RedactTokenFilter().filter(rec)
    assert "abc123" not in rec.getMessage() and "token=***&from=1" in rec.getMessage()
