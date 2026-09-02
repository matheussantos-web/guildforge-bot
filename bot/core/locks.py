"""Gerenciamento centralizado de locks por recurso (guild/evento).

Evita a criação de locks sem limite de vida (memory leak) e elimina o padrão
frágil de liberar o lock manualmente. A principal API é :func:`lock_for`, que
deve ser usada como contexto assíncrono:

    async with lock_for(guild_id, event_id):
        ...

Locks ociosos são descartados automaticamente após :data:`LOCK_TTL_SECONDS`.
O padrão antigo ``get_lock``/``release_lock`` é mantido apenas por
compatibilidade com os callers existentes e não deve ser usado em código novo.
"""

from __future__ import annotations

import asyncio
import time

LOCK_TTL_SECONDS = 30.0

_locks: dict[str, tuple[asyncio.Lock, float]] = {}


def _prune(now: float) -> None:
    """Remove locks que não são usados há mais de ``LOCK_TTL_SECONDS``."""
    stale = [key for key, (_, ts) in _locks.items() if now - ts > LOCK_TTL_SECONDS]
    for key in stale:
        _locks.pop(key, None)


def _key(guild_id: int, event_id: int) -> str:
    return f"{guild_id}:{event_id}"


def get_lock(guild_id: int, event_id: int) -> asyncio.Lock:
    """Retorna (ou cria) o lock para um recurso, marcando-o como em uso."""
    now = time.monotonic()
    _prune(now)
    key = _key(guild_id, event_id)
    entry = _locks.get(key)
    if entry is None:
        lock = asyncio.Lock()
        _locks[key] = (lock, now)
        return lock
    lock, _ = entry
    _locks[key] = (lock, now)
    return lock


def release_lock(guild_id: int, event_id: int) -> None:
    """[Compatibilidade] Remove o lock do dicionário.

    Prefira :func:`lock_for`, que garante o release mesmo em caso de erro.
    """
    _locks.pop(_key(guild_id, event_id), None)


class lock_for:
    """Context manager assíncrono seguro para serializar acesso por recurso.

    Garante ``lock.release()`` mesmo quando exceções ocorrem dentro do bloco.
    É a substituição recomendada do padrão manual ``get_lock`` + ``release_lock``.
    """

    def __init__(self, guild_id: int, event_id: int) -> None:
        self._guild_id = guild_id
        self._event_id = event_id
        self._lock: asyncio.Lock | None = None

    async def __aenter__(self) -> asyncio.Lock:
        self._lock = get_lock(self._guild_id, self._event_id)
        await self._lock.acquire()
        return self._lock

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._lock is not None and self._lock.locked():
            self._lock.release()
