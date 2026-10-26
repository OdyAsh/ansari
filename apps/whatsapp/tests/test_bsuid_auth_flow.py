"""Tests for the BSUID-to-JWT identity flow (spec 321, phase 2): no phone number is ever used."""

import asyncio
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from ansari_whatsapp.app.main import app
from ansari_whatsapp.services.ansari_client_real import AnsariClientReal
from ansari_whatsapp.services.auth_client_mock import AuthClientMock
from ansari_whatsapp.services.auth_client_real import API_KEY_HEADER, SESSION_PATH, AuthClientReal
from ansari_whatsapp.services.meta_api_service_mock import MetaApiServiceMock
from ansari_whatsapp.services.service_provider import reset_ansari_client, reset_auth_client
from ansari_whatsapp.utils.config import get_settings
from ansari_whatsapp.utils.exceptions import AuthTokenError, MessageProcessingError

BSUID = "US.13491208655302741918"
PHONE = "201234567899"

_RealAsyncClient = httpx.AsyncClient
client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_singletons(monkeypatch):
    reset_ansari_client()
    reset_auth_client()
    # Skip the mock backend's simulated latency
    monkeypatch.setattr("ansari_whatsapp.services.ansari_client_mock.random.uniform", lambda a, b: 0)
    yield
    reset_ansari_client()
    reset_auth_client()


def _use_transport(monkeypatch, module: str, handler):
    """Make the real client in `module` talk to `handler` instead of the network."""
    monkeypatch.setattr(
        f"{module}.httpx.AsyncClient", lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler), **kw)
    )


def _post_signed_webhook(message: dict):
    settings = get_settings()
    value = {
        "metadata": {
            "phone_number_id": settings.META_BUSINESS_PHONE_NUMBER_ID.get_secret_value(),
            "display_phone_number": "+1234567890",
        },
        "messages": [message],
    }
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    secret = settings.META_ANSARI_APP_SECRET.get_secret_value().encode("utf-8")
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return client.post("/whatsapp/v2", content=body, headers={"X-Hub-Signature-256": f"sha256={signature}"})


def _sse(*events: dict, ping: bool = True) -> bytes:
    """An apps/api `/chat` stream: optional keep-alive comment lines, then one `data:` line per event."""
    lines = [": ping\n\n"] if ping else []
    lines += [f"data: {json.dumps(e)}\n\n" for e in events]
    return "".join(lines).encode("utf-8")


ANSWER_STREAM = _sse({"type": "text", "content": "As-salamu "}, {"type": "text", "content": "alaykum"}, {"type": "done"})


def _text_message(**ids) -> dict:
    return {"id": "wamid.TEST", "timestamp": str(int(time.time())), "type": "text", "text": {"body": "hello"}, **ids}


# ---------- auth client ----------


def test_auth_client_exchanges_bsuid_for_token(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get(API_KEY_HEADER)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"token": "jwt-123"})

    _use_transport(monkeypatch, "ansari_whatsapp.services.auth_client_real", handler)

    token = asyncio.run(AuthClientReal().get_token(BSUID))

    assert token == "jwt-123"
    assert seen["url"].endswith(SESSION_PATH)
    assert seen["key"] == "test"
    assert seen["body"] == {"bsuid": BSUID}


@pytest.mark.parametrize("status", [400, 401, 500])
def test_auth_client_http_errors_raise(monkeypatch, status):
    _use_transport(monkeypatch, "ansari_whatsapp.services.auth_client_real", lambda r: httpx.Response(status, json={}))

    with pytest.raises(AuthTokenError):
        asyncio.run(AuthClientReal().get_token(BSUID))


def test_auth_client_timeout_raises(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    _use_transport(monkeypatch, "ansari_whatsapp.services.auth_client_real", handler)

    with pytest.raises(AuthTokenError):
        asyncio.run(AuthClientReal().get_token(BSUID))


def test_auth_client_response_without_token_raises(monkeypatch):
    _use_transport(monkeypatch, "ansari_whatsapp.services.auth_client_real", lambda r: httpx.Response(200, json={}))

    with pytest.raises(AuthTokenError):
        asyncio.run(AuthClientReal().get_token(BSUID))


def test_mock_auth_same_bsuid_same_user_new_bsuid_new_user():
    mock = AuthClientMock()

    first = asyncio.run(mock.get_token(BSUID))
    again = asyncio.run(mock.get_token(BSUID))
    other = asyncio.run(mock.get_token("US.99999999999999999999"))

    assert first == again
    assert first != other


def test_mock_auth_returns_the_configured_real_token_when_one_is_set(monkeypatch):
    monkeypatch.setenv("MOCK_AUTH_TOKEN", "a.real.jwt")
    get_settings.cache_clear()

    try:
        assert asyncio.run(AuthClientMock().get_token(BSUID)) == "a.real.jwt"
    finally:
        monkeypatch.delenv("MOCK_AUTH_TOKEN")
        get_settings.cache_clear()


# ---------- apps/api client ----------


def test_ansari_client_calls_api_with_jwt_and_reads_the_event_stream(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/api/v2/threads" and request.method == "GET":
            return httpx.Response(200, json=[{"thread_id": "t1", "updated_at": "2026-10-25T10:00:00.000Z"}])
        if request.url.path == "/api/v2/threads":
            return httpx.Response(200, json={"thread_id": "t2"})
        return httpx.Response(
            200,
            content=_sse(
                {"type": "tool_call", "name": "search_quran"},
                {"type": "tool_result", "tool": "search_quran", "resultCount": 3},
                {"type": "text", "content": "As-salamu "},
                {"type": "text", "content": "alaykum"},
                {"type": "done"},
            ),
        )

    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", handler)
    ansari = AnsariClientReal()

    last = asyncio.run(ansari.get_last_thread_info("jwt"))
    created = asyncio.run(ansari.create_thread("jwt", "title"))
    answer = asyncio.run(ansari.process_message("jwt", "t1", "hi"))

    assert last == {"thread_id": "t1", "last_message_time": "2026-10-25T10:00:00.000Z"}
    assert created == {"thread_id": "t2"}
    assert answer == "As-salamu alaykum"
    assert calls[-1][:2] == ("POST", "/api/v2/threads/t1/chat")
    assert all(auth == "Bearer jwt" for _, _, auth in calls)


def test_ansari_client_no_threads_yet(monkeypatch):
    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", lambda r: httpx.Response(200, json=[]))

    assert asyncio.run(AnsariClientReal().get_last_thread_info("jwt")) == {"thread_id": None, "last_message_time": None}


def test_ansari_client_sends_the_message_in_the_chat_body(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=ANSWER_STREAM)

    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", handler)

    asyncio.run(AnsariClientReal().process_message("jwt", "t1", "hi there"))

    assert seen["body"] == {"message": "hi there"}


def test_ansari_client_error_event_is_a_failure_not_an_answer(monkeypatch):
    # `done` follows the error, so only the error event itself can make this fail (not the missing-done check)
    stream = _sse({"type": "text", "content": "partial"}, {"type": "error", "message": "model overloaded"}, {"type": "done"})
    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", lambda r: httpx.Response(200, content=stream))

    with pytest.raises(MessageProcessingError):
        asyncio.run(AnsariClientReal().process_message("jwt", "t1", "hi"))


def test_ansari_client_stream_cut_off_before_done_is_a_failure(monkeypatch):
    stream = _sse({"type": "text", "content": "half an ans"})
    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", lambda r: httpx.Response(200, content=stream))

    with pytest.raises(MessageProcessingError):
        asyncio.run(AnsariClientReal().process_message("jwt", "t1", "hi"))


def test_ansari_client_done_without_text_is_an_empty_answer(monkeypatch):
    stream = _sse({"type": "done"})
    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", lambda r: httpx.Response(200, content=stream))

    assert asyncio.run(AnsariClientReal().process_message("jwt", "t1", "hi")) == ""


def test_ansari_client_malformed_event_is_a_failure(monkeypatch):
    _use_transport(
        monkeypatch,
        "ansari_whatsapp.services.ansari_client_real",
        lambda r: httpx.Response(200, content=b"data: this is not json\n\n"),
    )

    with pytest.raises(MessageProcessingError):
        asyncio.run(AnsariClientReal().process_message("jwt", "t1", "hi"))


def test_ansari_client_error_status_raises(monkeypatch):
    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", lambda r: httpx.Response(500, text="boom"))

    with pytest.raises(MessageProcessingError):
        asyncio.run(AnsariClientReal().process_message("jwt", "t1", "hi"))


# ---------- webhook flow ----------


def test_phone_visible_user_is_identified_and_replied_to_by_bsuid_only(monkeypatch):
    tokens_requested = []
    recipients = []

    original_get_token = AuthClientMock.get_token

    async def spy_get_token(self, bsuid):
        tokens_requested.append(bsuid)
        return await original_get_token(self, bsuid)

    original_send = MetaApiServiceMock.send_message

    async def spy_send(self, recipient_phone, message_parts):
        recipients.append(recipient_phone)
        return await original_send(self, recipient_phone, message_parts)

    monkeypatch.setattr(AuthClientMock, "get_token", spy_get_token)
    monkeypatch.setattr(MetaApiServiceMock, "send_message", spy_send)

    response = _post_signed_webhook(_text_message(**{"from": PHONE, "from_user_id": BSUID}))

    assert response.status_code == 200
    assert tokens_requested == [BSUID]
    assert recipients and set(recipients) == {BSUID}
    assert PHONE not in tokens_requested + recipients


def test_no_phone_number_reaches_apps_api(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url} {dict(request.headers)} {request.content.decode()}")
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/threads":
            return httpx.Response(200, json={"thread_id": "t1"})
        return httpx.Response(200, content=ANSWER_STREAM)

    monkeypatch.setattr(get_settings(), "MOCK_ANSARI_CLIENT", False)
    _use_transport(monkeypatch, "ansari_whatsapp.services.ansari_client_real", handler)

    response = _post_signed_webhook(_text_message(**{"from": PHONE, "from_user_id": BSUID}))

    assert response.status_code == 200
    assert len(requests) == 3  # list threads, create thread, send message
    assert not any(PHONE in r or BSUID in r for r in requests)


def test_phone_hidden_user_is_answered():
    response = _post_signed_webhook(_text_message(from_user_id=BSUID))

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_token_exchange_failure_notifies_user_and_is_reported(monkeypatch):
    recipients = []
    sent = []
    original_send = MetaApiServiceMock.send_message

    async def failing_get_token(self, bsuid):
        raise AuthTokenError("apps/auth is down")

    async def spy_send(self, recipient_phone, message_parts):
        recipients.append(recipient_phone)
        sent.extend(message_parts)
        return await original_send(self, recipient_phone, message_parts)

    monkeypatch.setattr(AuthClientMock, "get_token", failing_get_token)
    monkeypatch.setattr(MetaApiServiceMock, "send_message", spy_send)

    response = _post_signed_webhook(_text_message(from_user_id=BSUID))

    assert response.json()["error_code"] == "USER_AUTHENTICATION_FAILED"
    assert recipients == [BSUID]
    assert any("sign you in" in part for part in sent)


def test_message_without_bsuid_is_an_unexpected_format():
    response = _post_signed_webhook(_text_message(**{"from": PHONE}))

    assert response.json()["error_code"] == "UNEXPECTED_PAYLOAD_FORMAT"


def test_logs_contain_no_phone_number_bsuid_or_message_text():
    from loguru import logger

    captured = []
    sink_id = logger.add(lambda m: captured.append(str(m)), level="DEBUG")
    try:
        message = _text_message(**{"from": PHONE, "from_user_id": BSUID})
        message["text"] = {"body": "a-very-distinctive-question"}
        response = _post_signed_webhook(message)
    finally:
        logger.remove(sink_id)

    log_text = "\n".join(captured)
    assert response.status_code == 200
    assert log_text
    assert PHONE not in log_text
    assert BSUID not in log_text
    assert "a-very-distinctive-question" not in log_text
