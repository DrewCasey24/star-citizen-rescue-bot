import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("star-citizen-rescue-bot")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")


class RescueBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s command(s) to development guild %s", len(synced), GUILD_ID)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global command(s)", len(synced))


bot = RescueBot()


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")
    logger.info("Database configured: %s", "yes" if DATABASE_URL else "no")


@bot.tree.command(name="ping", description="Check whether the rescue bot is online.")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    embed = discord.Embed(
        title="Rescue Dispatch Online",
        description="Star Citizen Rescue Bot is operational.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Gateway latency", value=f"{latency_ms} ms")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rescue-status", description="Show the current rescue-system status.")
async def rescue_status(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="Star Citizen Rescue Dispatch",
        description="Dispatch foundation is online. Incident paging is the next build phase.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Discord", value="Online", inline=True)
    embed.add_field(
        name="Database",
        value="Configured" if DATABASE_URL else "Not attached yet",
        inline=True,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to your environment variables.")
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
