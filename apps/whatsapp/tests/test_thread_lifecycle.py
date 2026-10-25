"""Tests for the one-thread-per-user lifecycle (spec 321, phase 3)."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ansari_whatsapp.services.ansari_client_mock import AnsariClientMock
from ansari_whatsapp.services.ansari_client_real import AnsariClientReal
from ansari_whatsapp.services.auth_client_mock import AuthClientMock
from ansari_whatsapp.services.service_provider import get_ansari_client, reset_ansari_client, reset_auth_client
from ansari_whatsapp.services.whatsapp_conversation_manager import WhatsAppConversationManager
from ansari_whatsapp.utils.exceptions import ThreadDeletionError
from ansari_whatsapp.utils.time_utils import get_retention_time_seconds
from ansari_whatsapp.utils.user_locks import UserLocks

BSUID = "US.13491208655302741918"
OTHER_BSUID = "US.99999999999999999999"
EXPIRY = get_retention_time_seconds()


@pytest.fixture(autouse=True)
def fresh_singletons(monkeypatch):
    reset_ansari_client()
    reset_auth_client()
    monkeypatch.setattr("ansari_whatsapp.services.ansari_client_mock.random.uniform", lambda a, b: 0)
    yield
    reset_ansari_client()
    reset_auth_client()


def _manager(bsuid: str = BSUID) -> WhatsAppConversationManager:
    manager = WhatsAppConversationManager(
        bsuid=bsuid, incoming_msg_type="text", incoming_msg_body={"body": "hello"}, message_id="wamid.T"
    )
    manager.token = asyncio.run(AuthClientMock().get_token(bsuid))
    return manager


def _age_thread(thread_id: str, seconds: float) -> None:
    """Make the mock thread's last message `seconds` old."""
    old = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    get_ansari_client()._threads[thread_id]["last_message_time"] = old.isoformat()


def _threads_of(manager: WhatsAppConversationManager) -> list[str]:
    from ansari_whatsapp.utils.token_utils import get_subject

    user = get_subject(manager.token)
    return [t["thread_id"] for t in get_ansari_client()._threads.values() if t["user"] == user]


_RealAsyncClient = httpx.AsyncClient


def _real_client(monkeypatch, handler) -> AnsariClientReal:
    monkeypatch.setattr(
        "ansari_whatsapp.services.ansari_client_real.httpx.AsyncClient",
        lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler), **kw),
    )
    return AnsariClientReal()


def test_real_client_deletes_the_thread_with_the_users_token(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"message": "Thread deleted successfully"})

    asyncio.run(_real_client(monkeypatch, handler).delete_thread("jwt", "t1"))

    assert seen == [("DELETE", "/api/v2/threads/t1", "Bearer jwt")]


def test_real_client_treats_an_already_deleted_thread_as_success(monkeypatch):
    asyncio.run(_real_client(monkeypatch, lambda r: httpx.Response(404, json={})).delete_thread("jwt", "t1"))


def test_real_client_delete_failure_raises(monkeypatch):
    with pytest.raises(ThreadDeletionError):
        asyncio.run(_real_client(monkeypatch, lambda r: httpx.Response(500, json={})).delete_thread("jwt", "t1"))


def test_first_message_creates_the_only_thread():
    manager = _manager()

    thread_id = asyncio.run(manager._resolve_thread("hello there"))

    assert thread_id is not None
    assert _threads_of(manager) == [thread_id]


def test_message_within_expiry_reuses_the_thread_and_keeps_context():
    manager = _manager()
    first = asyncio.run(manager._resolve_thread("hello"))
    asyncio.run(manager.ansari_client.process_message(manager.token, first, "remember me"))
    _age_thread(first, EXPIRY - 60)  # just inside the expiry

    second = asyncio.run(manager._resolve_thread("hello again"))

    assert second == first
    assert _threads_of(manager) == [first]
    assert get_ansari_client()._threads[first]["messages"][0]["content"] == "remember me"


def test_message_after_expiry_deletes_the_old_thread_and_starts_a_new_one():
    manager = _manager()
    old = asyncio.run(manager._resolve_thread("hello"))
    asyncio.run(manager.ansari_client.process_message(manager.token, old, "old message"))
    _age_thread(old, EXPIRY + 60)  # just outside the expiry

    new = asyncio.run(manager._resolve_thread("hello again"))

    assert new != old
    assert _threads_of(manager) == [new]
    assert old not in get_ansari_client()._threads  # the old thread, and so its messages, are gone
    assert get_ansari_client()._threads[new]["messages"] == []


def test_expiry_is_read_from_the_z_timestamp_apps_api_really_sends(monkeypatch):
    """apps/api sends `...Z`, while the mock stores `+00:00`, so this is the format production depends on."""
    manager = _manager()
    four_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    calls = []

    async def old_thread_info(self, token):
        return {"thread_id": "old", "last_message_time": four_hours_ago}

    async def spy_delete(self, token, thread_id):
        calls.append(("delete", thread_id))

    async def spy_create(self, token, title):
        calls.append(("create", title))
        return {"thread_id": "new"}

    monkeypatch.setattr(AnsariClientMock, "get_last_thread_info", old_thread_info)
    monkeypatch.setattr(AnsariClientMock, "delete_thread", spy_delete)
    monkeypatch.setattr(AnsariClientMock, "create_thread", spy_create)

    assert asyncio.run(manager._resolve_thread("hello")) == "new"
    assert calls == [("delete", "old"), ("create", "hello")]


def test_expired_thread_flow_against_the_real_client_makes_the_expected_calls(monkeypatch):
    """List threads, delete the expired one, create a new one: in that order, all with the user's token."""
    seen = []
    four_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.method == "GET":
            return httpx.Response(200, json=[{"thread_id": "old", "updated_at": four_hours_ago}])
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "Thread deleted successfully"})
        return httpx.Response(200, json={"thread_id": "new"})

    manager = _manager()
    manager.ansari_client = _real_client(monkeypatch, handler)

    assert asyncio.run(manager._resolve_thread("hello")) == "new"
    token = f"Bearer {manager.token}"
    assert seen == [
        ("GET", "/api/v2/threads", token),
        ("DELETE", "/api/v2/threads/old", token),
        ("POST", "/api/v2/threads", token),
    ]


def test_creation_response_without_a_thread_id_is_a_failure_the_user_hears_about(monkeypatch):
    manager = _manager()
    sent = []

    async def no_id(self, token, title):
        return {}

    async def spy_send(self, msg_body, recipient_bsuid=None):
        sent.append(msg_body)

    monkeypatch.setattr(AnsariClientMock, "create_thread", no_id)
    monkeypatch.setattr(WhatsAppConversationManager, "send_whatsapp_message", spy_send)

    assert asyncio.run(manager._resolve_thread("hello")) is None
    assert sent


def test_another_users_thread_is_never_touched():
    mine = _manager()
    theirs = _manager(OTHER_BSUID)
    their_thread = asyncio.run(theirs._resolve_thread("hello"))
    my_thread = asyncio.run(mine._resolve_thread("hello"))
    _age_thread(my_thread, EXPIRY + 60)

    asyncio.run(mine._resolve_thread("hello again"))

    assert _threads_of(theirs) == [their_thread]


def test_failed_deletion_does_not_create_a_second_thread(monkeypatch):
    manager = _manager()
    old = asyncio.run(manager._resolve_thread("hello"))
    _age_thread(old, EXPIRY + 60)

    async def failing_delete(self, token, thread_id):
        raise ThreadDeletionError("apps/api is down")

    sent = []

    async def spy_send(self, msg_body, recipient_bsuid=None):
        sent.append(msg_body)

    monkeypatch.setattr(AnsariClientMock, "delete_thread", failing_delete)
    monkeypatch.setattr(WhatsAppConversationManager, "send_whatsapp_message", spy_send)

    result = asyncio.run(manager._resolve_thread("hello again"))

    assert result is None
    assert _threads_of(manager) == [old]  # still exactly one
    assert sent  # the user was told


def test_two_close_messages_after_expiry_leave_exactly_one_thread():
    first_manager = _manager()
    second_manager = _manager()
    old = asyncio.run(first_manager._resolve_thread("hello"))
    _age_thread(old, EXPIRY + 60)

    async def both():
        # Slow the mock down so the two messages genuinely overlap
        monkey_delay = 0.05
        original = AnsariClientMock.get_last_thread_info

        async def slow_get_last(self, token):
            result = await original(self, token)
            await asyncio.sleep(monkey_delay)
            return result

        AnsariClientMock.get_last_thread_info = slow_get_last
        try:
            return await asyncio.gather(
                first_manager._resolve_thread("message one"), second_manager._resolve_thread("message two")
            )
        finally:
            AnsariClientMock.get_last_thread_info = original

    a, b = asyncio.run(both())

    assert a == b  # the second message reused the thread the first one created
    assert _threads_of(first_manager) == [a]


def test_without_the_lock_two_close_messages_would_create_two_threads(monkeypatch):
    """Guards the lock test above: if the lock did nothing, that test would not notice."""

    class NoLock(UserLocks):
        def hold(self, key):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def noop():
                yield

            return noop()

    monkeypatch.setattr("ansari_whatsapp.services.whatsapp_conversation_manager.user_locks", NoLock())
    first_manager = _manager()
    second_manager = _manager()
    old = asyncio.run(first_manager._resolve_thread("hello"))
    _age_thread(old, EXPIRY + 60)

    async def both():
        original = AnsariClientMock.get_last_thread_info

        async def slow_get_last(self, token):
            result = await original(self, token)
            await asyncio.sleep(0.05)
            return result

        monkeypatch.setattr(AnsariClientMock, "get_last_thread_info", slow_get_last)
        return await asyncio.gather(
            first_manager._resolve_thread("message one"), second_manager._resolve_thread("message two")
        )

    asyncio.run(both())

    assert len(_threads_of(first_manager)) == 2


def test_user_locks_are_per_user_and_cleaned_up():
    locks = UserLocks()
    order = []

    async def work(key, name, delay):
        async with locks.hold(key):
            order.append(f"{name} start")
            await asyncio.sleep(delay)
            order.append(f"{name} end")

    async def run():
        await asyncio.gather(work("a", "a1", 0.05), work("a", "a2", 0.0), work("b", "b1", 0.0))

    asyncio.run(run())

    assert order.index("a1 end") < order.index("a2 start")  # same user: one at a time
    assert order.index("b1 start") < order.index("a1 end")  # other user: not blocked
    assert locks._locks == {}  # nothing left behind
