from __future__ import annotations

import discord

GUILDFORGE_COLOR = discord.Color(0x5865F2)

GUILDFORGE_LOGO_URL = (
    "https://cdn-icons-png.flaticon.com/512/3135/3135783.png"
)

ROLE_EMOJI_MAP: dict[str, str] = {
    "tank": "🛡️",
    "healer": "💚",
    "dps": "⚔️",
    "support": "🛡️",
    "off-healer": "💚",
    "offhealer": "💚",
}

ROLE_EMOJI_FALLBACK = "🔹"


def get_role_emoji(role_name: str) -> str:
    return ROLE_EMOJI_MAP.get(role_name.lower().strip(), ROLE_EMOJI_FALLBACK)


def build_progress_bar(filled: int, total: int, length: int = 5) -> str:
    if total <= 0:
        return ""
    ratio = min(filled / total, 1.0)
    filled_blocks = round(ratio * length)
    return "■" * filled_blocks + "□" * (length - filled_blocks)
