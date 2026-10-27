"""Tests for maintenance mode and the staging `!d ` filter (spec 321, phase 4)."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from ansari_whatsapp.app.main import app
from ansari_whatsapp.services.ansari_client_mock import AnsariClientMock
from ansari_whatsapp.services.meta_api_service_mock import MetaApiServiceMock
from ansari_whatsapp.services.service_provider import reset_ansari_client, reset_auth_client
from ansari_whatsapp.utils.config import get_settings

BSUID = "US.13491208655302741918"
client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_singletons():
    reset_ansari_client()
    reset_auth_client()
    yield
    reset_ansari_client()
    reset_auth_client()


def _post(body_text: str):
    settings = get_settings()
    value = {
        "metadata": {
            "phone_number_id": settings.META_BUSINESS_PHONE_NUMBER_ID.get_secret_value(),
            "display_phone_number": "+1234567890",
        },
        "messages": [
            {
                "from_user_id": BSUID,
                "id": "wamid.TEST",
                "timestamp": str(int(time.time())),
                "type": "text",
                "text": {"body": body_text},
            }
        ],
    }
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    secret = settings.META_ANSARI_APP_SECRET.get_secret_value().encode("utf-8")
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return client.post("/whatsapp/v2", content=body, headers={"X-Hub-Signature-256": f"sha256={signature}"})


def _spy_on_chat_calls(monkeypatch) -> list:
    calls = []
    original = AnsariClientMock.process_message

    async def spy(self, token, thread_id, message):
        calls.append(message)
        return await original(self, token, thread_id, message)

    monkeypatch.setattr(AnsariClientMock, "process_message", spy)
    monkeypatch.setattr("ansari_whatsapp.services.ansari_client_mock.random.uniform", lambda a, b: 0)
    return calls


def _spy_on_replies(monkeypatch) -> list:
    replies = []
    original = MetaApiServiceMock.send_message

    async def spy(self, recipient_phone, message_parts):
        replies.extend(message_parts)
        return await original(self, recipient_phone, message_parts)

    monkeypatch.setattr(MetaApiServiceMock, "send_message", spy)
    return replies


def test_maintenance_mode_on_sends_the_notice_and_makes_no_chat_call(monkeypatch):
    monkeypatch.setattr(get_settings(), "WA_MAINTENANCE_MODE", True)
    chat_calls = _spy_on_chat_calls(monkeypatch)
    replies = _spy_on_replies(monkeypatch)

    response = _post("hello")

    assert response.json()["error_code"] == "MAINTENANCE_MODE"
    assert chat_calls == []
    assert any("down for maintenance" in part for part in replies)


def test_maintenance_mode_off_answers_normally(monkeypatch):
    monkeypatch.setattr(get_settings(), "WA_MAINTENANCE_MODE", False)
    chat_calls = _spy_on_chat_calls(monkeypatch)

    response = _post("hello")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert chat_calls == ["hello"]


def test_apps_api_maintenance_variable_does_not_affect_this_service(monkeypatch):
    monkeypatch.setenv("MAINTENANCE_MODE", "true")
    get_settings.cache_clear()
    try:
        assert get_settings().WA_MAINTENANCE_MODE is False
    finally:
        get_settings.cache_clear()


def test_staging_ignores_messages_starting_with_the_dev_prefix(monkeypatch):
    monkeypatch.setattr(get_settings(), "DEPLOYMENT_TYPE", "staging")
    chat_calls = _spy_on_chat_calls(monkeypatch)

    response = _post("!d what is ansari?")

    assert response.json()["error_code"] == "DEV_FILTER"
    assert chat_calls == []


def test_staging_processes_messages_without_the_dev_prefix(monkeypatch):
    monkeypatch.setattr(get_settings(), "DEPLOYMENT_TYPE", "staging")
    chat_calls = _spy_on_chat_calls(monkeypatch)

    response = _post("what is ansari?")

    assert response.status_code == 200
    assert chat_calls == ["what is ansari?"]


def test_staging_handles_an_unsupported_message_type_without_crashing(monkeypatch):
    """Meta sends an unsupported type (video note, poll...) as an `errors` list, not a dict with a body."""
    monkeypatch.setattr(get_settings(), "DEPLOYMENT_TYPE", "staging")
    chat_calls = _spy_on_chat_calls(monkeypatch)
    settings = get_settings()
    value = {
        "metadata": {
            "phone_number_id": settings.META_BUSINESS_PHONE_NUMBER_ID.get_secret_value(),
            "display_phone_number": "+1234567890",
        },
        "messages": [
            {
                "from_user_id": BSUID,
                "id": "wamid.TEST",
                "timestamp": str(int(time.time())),
                "type": "unsupported",
                "errors": [{"code": 131051, "title": "Message type is not supported"}],
            }
        ],
    }
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    secret = settings.META_ANSARI_APP_SECRET.get_secret_value().encode("utf-8")
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()

    response = client.post("/whatsapp/v2", content=body, headers={"X-Hub-Signature-256": f"sha256={signature}"})

    assert response.json()["error_code"] == "UNSUPPORTED_MESSAGE_TYPE"
    assert chat_calls == []


def test_local_processes_messages_with_the_dev_prefix(monkeypatch):
    monkeypatch.setattr(get_settings(), "DEPLOYMENT_TYPE", "local")
    chat_calls = _spy_on_chat_calls(monkeypatch)

    response = _post("!d what is ansari?")

    assert response.status_code == 200
    assert chat_calls == ["!d what is ansari?"]
