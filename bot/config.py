import os

from dotenv import load_dotenv

load_dotenv()

BOT_NAME = "GuildForge"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
