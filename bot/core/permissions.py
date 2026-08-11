import discord


def has_member_role(config: dict | None, member: discord.Member) -> bool:
    if config is None or not config.get("member_role_id"):
        return False
    return member.get_role(config["member_role_id"]) is not None
