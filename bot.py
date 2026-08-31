import logging
import os
import re

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

SERVICE_CHOICES = [
    discord.SelectOption(label="Medical Rescue", value="medical", emoji="🚑"),
    discord.SelectOption(label="Search & Rescue", value="search-rescue", emoji="🔎"),
    discord.SelectOption(label="Repair / Refuel", value="repair-refuel", emoji="🔧"),
    discord.SelectOption(label="Security / Escort", value="security", emoji="🛡️"),
    discord.SelectOption(label="Recovery / Transport", value="recovery-transport", emoji="🚀"),
]

SERVICE_NAMES = {
    "medical": "Medical Rescue",
    "search-rescue": "Search & Rescue",
    "repair-refuel": "Repair / Refuel",
    "security": "Security / Escort",
    "recovery-transport": "Recovery / Transport",
}

SERVICE_ROLE_NAMES = {
    "medical": ["S.E.R.E. Sector"],
    "search-rescue": ["S.E.R.E. Sector", "Military Sector"],
    "repair-refuel": ["Logistic Sector"],
    "security": ["Military Sector"],
    "recovery-transport": ["Logistics Sector", "S.E.R.E. Sector"],
}


def safe_channel_name(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:45] or "incident"


def responder_roles(guild: discord.Guild, service: str) -> tuple[list[discord.Role], list[str]]:
    roles = []
    missing = []
    for role_name in SERVICE_ROLE_NAMES.get(service, []):
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            missing.append(role_name)
        else:
            roles.append(role)
    return roles, missing


class RescueBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        self.add_view(RequestAssistanceView())
        self.add_view(IncidentControlsView())
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s command(s) to development guild %s", len(synced), GUILD_ID)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global command(s)", len(synced))


bot = RescueBot()


class RescueDetailsModal(discord.ui.Modal, title="Request Assistance"):
    callsign = discord.ui.TextInput(label="In-game name / callsign", placeholder="Your Star Citizen callsign", max_length=50)
    location = discord.ui.TextInput(label="Current location", placeholder="Example: Daymar - Shubin Mining Facility SCD-1", max_length=100)
    situation = discord.ui.TextInput(label="Situation", placeholder="Tell responders what happened and what you need.", style=discord.TextStyle.paragraph, max_length=700)

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Rescue requests must be submitted inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Active Incidents")
        if category is None:
            category = await guild.create_category("Active Incidents", reason="Star Citizen rescue dispatch setup")
        roles, missing_roles = responder_roles(guild, self.service)
        bot_member = guild.me
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        for role in roles:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
        service_name = SERVICE_NAMES.get(self.service, self.service.replace("-", " ").title())
        channel_name = f"rescue-{safe_channel_name(str(self.callsign))}"
        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites, topic=f"Rescue request for {self.callsign} | {service_name}", reason=f"Rescue request submitted by {interaction.user}")
        embed = discord.Embed(title="🚨 ACTIVE RESCUE REQUEST", description="A new Star Citizen rescue incident has been opened.", color=discord.Color.red())
        embed.add_field(name="Status", value="🔴 Awaiting Responder", inline=True)
        embed.add_field(name="Service", value=service_name, inline=True)
        embed.add_field(name="Requester", value=interaction.user.mention, inline=True)
        embed.add_field(name="Callsign", value=str(self.callsign), inline=True)
        embed.add_field(name="Location", value=str(self.location), inline=False)
        embed.add_field(name="Situation", value=str(self.situation), inline=False)
        embed.set_footer(text=f"Requester ID: {interaction.user.id}")
        role_mentions = " ".join(role.mention for role in roles)
        paging_text = f"🚨 **DISPATCH:** {role_mentions}" if role_mentions else "🚨 **DISPATCH:** No responder role was found."
        content = f"{paging_text}\n{interaction.user.mention} your rescue channel is ready. Responders can coordinate here."
        await channel.send(content=content, embed=embed, view=IncidentControlsView(), allowed_mentions=discord.AllowedMentions(roles=True, users=True))
        if missing_roles:
            await channel.send("⚠️ Dispatch configuration warning: I could not find the following Discord role(s): " + ", ".join(f"`{name}`" for name in missing_roles) + ". Check that the role names match exactly.")
            logger.warning("Missing responder role(s) in guild %s: %s", guild.id, ", ".join(missing_roles))
        await interaction.followup.send(f"Rescue request created: {channel.mention}", ephemeral=True)


class ServiceSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Select the type of assistance you need...", min_values=1, max_values=1, options=SERVICE_CHOICES, custom_id="rescue:service-select")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RescueDetailsModal(self.values[0]))


class RequestAssistanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request Assistance", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="rescue:request")
    async def request_assistance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = discord.ui.View(timeout=120)
        view.add_item(ServiceSelect())
        await interaction.response.send_message("Select the service you need. A rescue form will open next.", view=view, ephemeral=True)


class IncidentControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_status(self, interaction: discord.Interaction, status: str, color: discord.Color) -> None:
        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("I could not find the incident card.", ephemeral=True)
            return
        embed = interaction.message.embeds[0].copy()
        for index, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(index, name="Status", value=status, inline=True)
                break
        embed.color = color
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Respond", style=discord.ButtonStyle.success, emoji="🚀", custom_id="rescue:respond")
    async def respond(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.update_status(interaction, f"🟡 En Route — {interaction.user.mention}", discord.Color.gold())

    @discord.ui.button(label="Arrived", style=discord.ButtonStyle.primary, emoji="📍", custom_id="rescue:arrived")
    async def arrived(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.update_status(interaction, f"🟢 On Scene — {interaction.user.mention}", discord.Color.green())

    @discord.ui.button(label="Need Backup", style=discord.ButtonStyle.secondary, emoji="🛡️", custom_id="rescue:backup")
    async def backup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.update_status(interaction, f"🟠 Backup Requested — {interaction.user.mention}", discord.Color.orange())

    @discord.ui.button(label="Close Incident", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="rescue:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(f"Incident closed by {interaction.user.mention}. This channel is now read-only.")
        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False, send_messages=False)
            await interaction.channel.edit(name=f"closed-{interaction.channel.name}"[:100])


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")
    logger.info("Database configured: %s", "yes" if DATABASE_URL else "no")


@bot.tree.command(name="ping", description="Check whether the rescue bot is online.")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    embed = discord.Embed(title="Rescue Dispatch Online", description="Star Citizen Rescue Bot is operational.", color=discord.Color.green())
    embed.add_field(name="Gateway latency", value=f"{latency_ms} ms")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rescue-status", description="Show the current rescue-system status.")
async def rescue_status(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="Star Citizen Rescue Dispatch", description="Rescue request, sector paging, and incident controls are online.", color=discord.Color.blurple())
    embed.add_field(name="Discord", value="Online", inline=True)
    embed.add_field(name="Incident System", value="Online", inline=True)
    embed.add_field(name="Sector Paging", value="Online", inline=True)
    embed.add_field(name="Database", value="Configured" if DATABASE_URL else "Not attached yet", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rescue-setup", description="Post the permanent rescue request panel in this channel.")
@app_commands.checks.has_permissions(manage_guild=True)
async def rescue_setup(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="🚨 STAR CITIZEN RESCUE DISPATCH", description="Need assistance in the 'verse? Use the button below to open a rescue request.\n\nYou will choose the service you need and provide your callsign, location, and situation. A private incident channel will then be created and the appropriate sector will be paged.", color=discord.Color.red())
    embed.add_field(name="Available Services", value="🚑 Medical Rescue\n🔎 Search & Rescue\n🔧 Repair / Refuel\n🛡️ Security / Escort\n🚀 Recovery / Transport", inline=False)
    embed.set_footer(text="Star Citizen Rescue Dispatch • Emergency Assistance Network")
    await interaction.channel.send(embed=embed, view=RequestAssistanceView())
    await interaction.response.send_message("Rescue request panel posted.", ephemeral=True)


@rescue_setup.error
async def rescue_setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You need the Manage Server permission to run this command.", ephemeral=True)
        return
    if isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        if interaction.response.is_done():
            await interaction.followup.send("I do not have permission to post in this channel.", ephemeral=True)
        else:
            await interaction.response.send_message("I do not have permission to post in this channel.", ephemeral=True)
        return
    raise error


def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to your environment variables.")
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
