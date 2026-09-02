"""Testes das regras de permissão, branding e parser de migrations."""

from __future__ import annotations


def test_has_member_role_no_config() -> None:
    from bot.core.permissions import has_member_role

    member = _FakeMember(roles={})
    assert has_member_role(None, member) is False
    assert has_member_role({}, member) is False


def test_has_member_role_missing_role() -> None:
    from bot.core.permissions import has_member_role

    member = _FakeMember(roles={1, 2})
    assert has_member_role({"member_role_id": 50}, member) is False


def test_has_member_role_with_role() -> None:
    from bot.core.permissions import has_member_role

    member = _FakeMember(roles={1, 2, 50})
    assert has_member_role({"member_role_id": 50}, member) is True


class _FakeRole:
    def __init__(self, rid: int) -> None:
        self.id = rid


class _FakeMember:
    def __init__(self, roles: set[int]) -> None:
        self._roles = {_FakeRole(rid) for rid in roles}

    def get_role(self, rid: int):
        for role in self._roles:
            if role.id == rid:
                return role
        return None


def test_get_role_emoji_map_and_fallback() -> None:
    from bot.core.branding import get_role_emoji

    assert get_role_emoji("tank") == "🛡️"
    assert get_role_emoji("TANK") == "🛡️"
    assert get_role_emoji("healer") == "💚"
    assert get_role_emoji("desconhecido") == "🔹"


def test_build_progress_bar() -> None:
    from bot.core.branding import build_progress_bar

    assert build_progress_bar(0, 0) == ""
    assert build_progress_bar(5, 5) == "■■■■■"
    assert build_progress_bar(0, 5) == "□□□□□"
    assert len(build_progress_bar(2, 5)) == 5


def test_split_statements_handles_semicolons_and_quotes() -> None:
    from bot.core.migrate import split_statements

    sql = (
        "CREATE TABLE t (id int);"
        "INSERT INTO t VALUES ('a;b');"
        "UPDATE t SET x = 1;"
    )
    stmts = split_statements(sql)
    assert stmts == [
        "CREATE TABLE t (id int)",
        "INSERT INTO t VALUES ('a;b')",
        "UPDATE t SET x = 1",
    ]


def test_split_statements_dollar_quote() -> None:
    from bot.core.migrate import split_statements

    sql = "CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql;"
    stmts = split_statements(sql)
    assert len(stmts) == 1
    assert "RETURN 1; END;" in stmts[0]
