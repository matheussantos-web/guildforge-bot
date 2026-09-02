"""Testes das regras de negócio do LFG (parser de vagas e LFGService)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from bot.services.lfg_repository import _PARTICIPANTS_BY_SESSION, _SESSION_BY_ID
from bot.services.lfg_service import LFGService, parse_slots

SESSION = {
    "id": 1,
    "guild_id": 10,
    "creator_id": 100,
    "channel_id": 5,
    "message_id": None,
    "title": "T",
    "description": "",
    "event_time": "",
    "slots_config": [{"role": "Tank", "limit": 1, "category": "G"}],
    "status": "active",
    "created_at": None,
    "warning_sent_at": None,
    "lfg_role_id": None,
}


class Rec(dict):
    """Simula asyncpg.Record."""

    def __getattr__(self, k: str) -> Any:
        return self[k]


class FakeConn:
    def __init__(self) -> None:
        self.participants: list[dict] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, q: str, *a: Any) -> Rec | None:
        if q == _SESSION_BY_ID and a == (1,):
            return Rec(SESSION)
        return None

    async def fetch(self, q: str, *a: Any) -> list[Rec]:
        if q == _PARTICIPANTS_BY_SESSION:
            return [Rec(p) for p in self.participants]
        return []

    async def execute(self, q: str, *a: Any) -> None:
        if "DELETE FROM lfg_participants" in q:
            uid = a[-1]
            self.participants = [p for p in self.participants if p["user_id"] != uid]
        elif "INSERT INTO lfg_participants" in q or "ON CONFLICT" in q:
            self.participants.append({"session_id": a[0], "user_id": a[1], "role": a[2]})
        return None

    async def fetchval(self, q: str, *a: Any) -> Any:
        if "DELETE FROM lfg_participants" in q:
            uid = a[-1]
            for i, p in enumerate(self.participants):
                if p["user_id"] == uid:
                    role = p["role"]
                    del self.participants[i]
                    return role
            return None
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def test_parse_slots_valid_full() -> None:
    assert parse_slots("Tank:1:Front, DPS:5:DPS") == [
        {"role": "Tank", "limit": 1, "category": "Front"},
        {"role": "DPS", "limit": 5, "category": "DPS"},
    ]


def test_parse_slots_default_category() -> None:
    assert parse_slots("Tank:1") == [
        {"role": "Tank", "limit": 1, "category": "Geral"}
    ]


def test_parse_slots_invalid_inputs() -> None:
    assert parse_slots("invalido") is None
    assert parse_slots("Tank:0") is None
    assert parse_slots("Tank:abc") is None
    assert parse_slots("") is None
    assert parse_slots("  ") is None


async def test_join_role_success_then_duplicate_rejected() -> None:
    svc = LFGService(FakePool())
    ok, msg = await svc.join_role(10, 200, 1, "Tank")
    assert ok, msg
    ok2, msg2 = await svc.join_role(10, 200, 1, "Tank")
    assert not ok2
    assert "já está inscrito" in msg2


async def test_join_role_invalid_and_full_rejected() -> None:
    svc = LFGService(FakePool())
    ok, msg = await svc.join_role(10, 201, 1, "NaoExiste")
    assert not ok
    assert "Função inválida" in msg
    # preenche a única vaga de Tank
    await svc.join_role(10, 201, 1, "Tank")
    ok2, msg2 = await svc.join_role(10, 202, 1, "Tank")
    assert not ok2
    assert "vagas" in msg2


async def test_leave_not_participating() -> None:
    svc = LFGService(FakePool())
    ok, msg = await svc.leave(10, 999, 1)
    assert not ok
    assert "não está participando" in msg


async def test_leave_success_after_join() -> None:
    svc = LFGService(FakePool())
    await svc.join_role(10, 200, 1, "Tank")
    ok, msg = await svc.leave(10, 200, 1)
    assert ok, msg


async def test_close_by_creator_succeeds() -> None:
    svc = LFGService(FakePool())
    ok, msg = await svc.close(10, 100, 1, is_admin=False)
    assert ok, msg


async def test_close_by_non_admin_rejected() -> None:
    svc = LFGService(FakePool())
    ok, msg = await svc.close(10, 999, 1, is_admin=False)
    assert not ok
    assert "criador ou staff" in msg


async def test_close_by_admin_succeeds() -> None:
    svc = LFGService(FakePool())
    ok, msg = await svc.close(10, 999, 1, is_admin=True)
    assert ok, msg
