"""Testes do cache de configuração por guilda (TTL, validação de colunas)."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import pytest

from bot.core import guild_settings as gs


class FakeConn:
    def __init__(self, rows: list[dict] | None, row: dict | None) -> None:
        self.rows = rows or []
        self.row = row

    async def fetchrow(self, q: str, *a) -> dict | None:
        return self.row

    async def fetch(self, q: str, *a) -> list[dict]:
        return self.rows

    async def execute(self, q: str, *a) -> None:
        return None


class FakePool:
    def __init__(self, rows: list[dict] | None = None, row: dict | None = None) -> None:
        self.conn = FakeConn(rows, row)

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def test_allowed_columns_contains_known_columns() -> None:
    assert {"name", "member_role_id", "log_channel_id"} <= gs.ALLOWED_COLUMNS


async def test_upsert_guild_config_rejects_unknown_columns() -> None:
    pool = FakePool()
    with pytest.raises(ValueError):
        await gs.upsert_guild_config(pool, 1, nao_existe=42)


async def test_upsert_guild_config_rejects_empty_fields() -> None:
    pool = FakePool()
    with pytest.raises(ValueError):
        await gs.upsert_guild_config(pool, 1)


async def test_invalidate_clears_both_caches() -> None:
    gs._cache.clear()
    gs._settings_cache.clear()
    gs._cache[123] = ({"a": 1}, time.monotonic())
    gs._settings_cache[123] = ({"k": "v"}, time.monotonic())
    gs._invalidate(123)
    assert 123 not in gs._cache
    assert 123 not in gs._settings_cache


def test_fresh_logic() -> None:
    assert gs._is_fresh(time.monotonic()) is True
    assert gs._is_fresh(time.monotonic() - 10_000) is False
