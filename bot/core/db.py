"""Camada única de acesso ao connection pool do banco.

Centraliza a obtenção do :class:`asyncpg.Pool` a partir de qualquer ponto do
bot (cogs, views e módulos de integração), eliminando o acesso direto e
espalhado a atributos internos do cliente (ex.: ``interaction.client.db_pool``).

O pool é registrado no objeto ``bot`` durante o bootstrap (ver ``bot/main.py``)
e acessado aqui via :func:`get_pool`. Isso mantém a separação entre interface
(chat/interactions) e persistência (banco): os módulos de apresentação nunca
precisam conhecer o layout interno do cliente.
"""

from __future__ import annotations

from typing import Any

import asyncpg

_bot: Any | None = None


def set_bot(bot: Any) -> None:
    """Registra a instância do bot para exposição do pool (chamado no bootstrap)."""
    global _bot
    _bot = bot


def get_pool() -> asyncpg.Pool:
    """Retorna o pool registrado. Lança :class:`RuntimeError` se não disponível."""
    if _bot is None:
        raise RuntimeError("Bot não registrado; get_pool() chamado antes do bootstrap")
    pool = getattr(_bot, "db_pool", None)
    if pool is None:
        raise RuntimeError("db_pool não definido no bot")
    return pool
