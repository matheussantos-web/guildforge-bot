import asyncio

_locks: dict[str, asyncio.Lock] = {}


def get_lock(guild_id: int, event_id: int) -> asyncio.Lock:
    key = f"{guild_id}:{event_id}"
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock
