# Per-user locks for ansari-whatsapp
"""In-process locks that make one user's steps run one at a time."""

import asyncio
from contextlib import asynccontextmanager


class UserLocks:
    """One asyncio lock per user key, dropped again once nobody holds or waits for it.

    These locks live in this process's memory, so they only serialize work inside ONE running instance of
    the service. That is deliberate (spec 321): the service runs as a single instance, because WhatsApp
    traffic is not expected to need more. If it ever runs as several instances, this must move to a shared
    place, for example a database lock.
    """

    def __init__(self) -> None:
        self._locks: dict[str, list] = {}  # key -> [lock, number of holders and waiters]

    @asynccontextmanager
    async def hold(self, key: str):
        """Wait for this user's lock, run the `async with` body alone for that user, then release."""
        entry = self._locks.get(key)
        if entry is None:
            entry = self._locks[key] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self._locks[key]


user_locks = UserLocks()
