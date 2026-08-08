import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)

SEARCH_URL = "https://gameinfo.albiononline.com/api/gameinfo/search"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8)
HEADERS = {"User-Agent": "GuildForge/0.1 (Discord bot; contato via owner do servidor)"}


class AlbionAPIError(Exception):
    def __init__(self, message: str, kind: str = "network") -> None:
        super().__init__(message)
        self.kind = kind


async def _get_json(url: str, *, params: dict[str, str]) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, headers=HEADERS) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise AlbionAPIError(f"API do Albion respondeu HTTP {resp.status}")
                return await resp.json(content_type=None)
    except asyncio.TimeoutError as exc:
        raise AlbionAPIError(
            "A API do Albion Online demorou demais para responder"
        ) from exc
    except aiohttp.ClientError as exc:
        raise AlbionAPIError(
            "Erro de rede ao consultar a API do Albion Online"
        ) from exc


async def fetch_character(character_name: str) -> dict:
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
