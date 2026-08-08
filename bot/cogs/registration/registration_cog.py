from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.guild_settings import get_guild_config, get_setting
from bot.services.albion_api_service import AlbionAPIError, fetch_character

MEMBER_UPSERT = """
    INSERT INTO members (guild_id, discord_user_id, albion_character_name)
    VALUES ($1, $2, $3)
    ON CONFLICT (guild_id, discord_user_id)
    DO UPDATE SET albion_character_name = EXCLUDED.albion_character_name,
                  registered_at = now()
"""


class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @app_commands.command(
        name="registrar",
        description="Vincula seu personagem do Albion Online à sua conta Discord",
    )
    @app_commands.describe(
        character_name="Nome exato do seu personagem no Albion Online",
    )
    async def registrar(
        self,
        interaction: discord.Interaction,
        character_name: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use este comando em um servidor.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        member = interaction.user

        config = await get_guild_config(self.pool, guild.id)
        member_role_id = config.get("member_role_id") if config else None
        if not member_role_id:
            await interaction.response.send_message(
                "Este servidor ainda não configurou o cargo de membro. "
                "Peça a um administrador para usar `/setup` informando `member_role` "
                "antes de registrar.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            player = await fetch_character(character_name)
        except AlbionAPIError as exc:
            if exc.kind == "not_found":
                message = (
                    f"Não encontrei um personagem chamado **{character_name}** no Albion "
                    "Online. Confira a grafia (incluindo acentos e espaços) e tente novamente."
                )
            else:
                message = f"Não foi possível consultar o Albion Online no momento: {exc}"
            await interaction.followup.send(message, ephemeral=True)
            return

        expected_guild = await get_setting(self.pool, guild.id, "albion_guild_id")
        if expected_guild and str(player["guild_id"]) != expected_guild:
            await interaction.followup.send(
                f"Seu personagem **{player['name']}** não pertence à guilda esperada por "
                f"este servidor (`{expected_guild}`) e por isso não pode ser registrado aqui.",
                ephemeral=True,
            )
            return

        async with self.pool.acquire() as conn:
            await conn.execute(MEMBER_UPSERT, guild.id, member.id, player["name"])

        role_applied = False
        nick_changed = False
        warnings: list[str] = []

        role = guild.get_role(member_role_id) if member_role_id else None
        if role is None:
            warnings.append(
                "O cargo de membro configurado não existe mais — peça a um administrador "
                "para rodar `/setup` novamente."
            )
        else:
            try:
                await member.add_roles(role, reason="Registro de personagem Albion")
                role_applied = True
            except discord.Forbidden:
                warnings.append(
                    "Não tenho permissão para aplicar o cargo de membro "
                    "(verifique a hierarquia de cargos e se o bot está acima dele)."
                )
            except discord.HTTPException as exc:
                warnings.append(f"Falha ao aplicar o cargo de membro: {exc}")

        try:
            await member.edit(nick=player["name"])
            nick_changed = True
        except discord.Forbidden:
            warnings.append("Não tenho permissão para alterar seu apelido no servidor.")
        except discord.HTTPException as exc:
            warnings.append(f"Falha ao alterar seu apelido: {exc}")

        embed = self._build_confirmation(
            player=player,
            warnings=warnings,
            role_applied=role_applied,
            nick_changed=nick_changed,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @staticmethod
    def _build_confirmation(
        player: dict,
        warnings: list[str],
        *,
        role_applied: bool,
        nick_changed: bool,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Registro confirmado",
            description=f"Personagem **{player['name']}** vinculado à sua conta Discord.",
            color=discord.Color.green() if not warnings else discord.Color.orange(),
        )
        embed.add_field(
            name="Cargo de membro",
            value="Aplicado" if role_applied else "Falha ao aplicar",
            inline=True,
        )
        embed.add_field(
            name="Apelido (nick)",
            value="Atualizado" if nick_changed else "Não alterado",
            inline=True,
        )
        if warnings:
            embed.add_field(
                name="Avisos",
                value="\n".join(f"- {warning}" for warning in warnings),
                inline=False,
            )
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RegistrationCog(bot))
