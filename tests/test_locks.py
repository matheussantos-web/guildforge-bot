"""Testes do gerenciador de locks (TTL, contexto, release seguro)."""

from __future__ import annotations

import asyncio

import pytest

from bot.core.locks import LOCK_TTL_SECONDS, _locks, get_lock, lock_for


async def test_lock_for_releases_after_context() -> None:
    async with lock_for(1, 2):
        locked = _locks["1:2"][0].locked()
        assert locked is True
    assert _locks["1:2"][0].locked() is False


async def test_lock_for_releases_on_exception() -> None:
    with pytest.raises(RuntimeError):
        async with lock_for(3, 4):
            raise RuntimeError("boom")
    assert _locks["3:4"][0].locked() is False


async def test_lock_for_serializes_concurrent_access() -> None:
    async with lock_for(10, 20):
        assert get_lock(10, 20).locked() is True


def test_prune_removes_stale_locks_and_keeps_fresh() -> None:
    import time

    _locks.clear()
    get_lock(99, 99)
    get_lock(100, 100)
    # backdate the second entry
    _locks["100:100"] = (_locks["100:100"][0], time.monotonic() - LOCK_TTL_SECONDS - 1)
    from bot.core.locks import _prune

    _prune(time.monotonic())
    assert "99:99" in _locks
    assert "100:100" not in _locks
    _locks.clear()


async def test_get_lock_is_reentrant_returns_same_object() -> None:
    _locks.clear()
    a = get_lock(7, 8)
    b = get_lock(7, 8)
    assert a is b
    _locks.clear()


def test_release_lock_compat_removes_entry() -> None:
    _locks.clear()
    from bot.core.locks import release_lock

    get_lock(5, 6)
    assert "5:6" in _locks
    release_lock(5, 6)
    assert "5:6" not in _locks
    _locks.clear()
