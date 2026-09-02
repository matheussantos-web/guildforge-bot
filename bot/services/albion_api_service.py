"""Integração com a API pública do Albion Online.

Um único :class:`aiohttp.ClientSession` é reutilizado entre as chamadas para
aproveitar connection pooling / keep-alive (evita a criação de uma session por
requisição, que é custosa e vaza sockets em cenários de alto volume).

A session é criada sob demanda e fechada em :func:`close_albion_api`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

SEARCH_URL = "https://gameinfo.albiononline.com/api/gameinfo/search"
GUILD_MEMBERS_URL = "https://gameinfo.albiononline.com/api/gameinfo/guilds/{guild_id}/members"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8)
GUILD_MEMBERS_TIMEOUT = aiohttp.ClientTimeout(total=20)
GUILD_MEMBERS_RETRIES = 3
HEADERS = {"User-Agent": "GuildForge/0.1 (Discord bot; contato via owner do servidor)"}

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    """Retorna a session compartilhada, criando-a (lazy) se necessário."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=REQUEST_TIMEOUT,
            headers=HEADERS,
            raise_for_status=False,
        )
    return _session


async def close_albion_api() -> None:
    """Fecha a session compartilhada. Deve ser chamada no shutdown do bot."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


class AlbionAPIError(Exception):
    """Erro de rede/API do Albion. ``kind`` classifica o motivo.

    ``kind`` pode ser ``"network"``, ``"http"`` ou ``"not_found"``.
    """

    def __init__(self, message: str, kind: str = "network") -> None:
        super().__init__(message)
        self.kind = kind

    def __str__(self) -> str:  # pragma: no cover - simples
        return self.args[0]


async def _get_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
) -> Any:
    session = get_session()
    try:
        async with session.get(url, params=params, timeout=timeout) as resp:
            if resp.status != 200:
                raise AlbionAPIError(
                    f"A API do Albion respondeu HTTP {resp.status}",
                    kind="http",
                )
            try:
                return await resp.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError) as exc:
                raise AlbionAPIError(
                    "A API do Albion devolveu uma resposta inválida"
                ) from exc
    except asyncio.TimeoutError as exc:
        raise AlbionAPIError(
            "A API do Albion Online demorou demais para responder"
        ) from exc
    except aiohttp.ClientError as exc:
        raise AlbionAPIError(
            "Erro de rede ao consultar a API do Albion Online"
        ) from exc


async def fetch_character(character_name: str) -> dict[str, Any]:
    query = character_name.strip()
    if not query:
        raise AlbionAPIError(
            "Nome de personagem vazio.",
            kind="not_found",
        )

    data = await _get_json(SEARCH_URL, params={"q": query})
    players = data.get("players") or []

    match = next(
        (p for p in players if p.get("Name", "").lower() == query.lower()),
        None,
    )
    if match is None:
        log.info("Personagem '%s' não encontrado na busca do Albion", query)
        raise AlbionAPIError(
            f"Personagem '{query}' não encontrado no Albion Online.",
            kind="not_found",
        )

    return {
        "id": match.get("Id"),
        "name": match.get("Name") or query,
        "guild_id": match.get("GuildId") or None,
        "alliance_id": match.get("AllianceId") or None,
    }


async def search_guild_by_name(guild_name: str) -> dict[str, Any] | None:
    query = guild_name.strip()
    if not query:
        return None

    data = await _get_json(SEARCH_URL, params={"q": query})
    guilds = data.get("guilds") or []

    match = next(
        (g for g in guilds if g.get("Name", "").lower() == query.lower()),
        None,
    )
    if match is None:
        log.info("Guilda '%s' não encontrada na busca do Albion", query)
        return None

    return {
        "id": match.get("Id"),
        "name": match.get("Name") or query,
    }


async def fetch_guild_members(albion_guild_id: str) -> list[dict[str, Any]]:
    last_error: AlbionAPIError | None = None
    for attempt in range(GUILD_MEMBERS_RETRIES):
        try:
            data = await _get_json(
                GUILD_MEMBERS_URL.format(guild_id=albion_guild_id),
                timeout=GUILD_MEMBERS_TIMEOUT,
            )
            if not isinstance(data, list):
                return []
            return [
                {"id": member.get("Id"), "name": member.get("Name")}
                for member in data
                if member.get("Id") and member.get("Name")
            ]
        except AlbionAPIError as exc:
            last_error = exc
            log.warning(
                "Tentativa %d de buscar membros da guilda Albion %s falhou: %s",
                attempt + 1,
                albion_guild_id,
                exc,
            )
            if attempt < GUILD_MEMBERS_RETRIES - 1:
                await asyncio.sleep(2 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return []
