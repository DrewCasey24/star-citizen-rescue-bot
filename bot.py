import logging
import os
import re

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("star-citizen-rescue-bot")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

RESPONDER_ROLE_NAMES = ["S.E.R.E. Sector", "Military Sector", "Logistics Sector"]

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
    "repair-refuel": ["Logistics Sector"],
    "security": ["Military Sector"],
    "recovery-transport": ["Logistics Sector", "S.E.R.E. Sector"],
}

# Requesters may choose P2 or P3. P1 is reserved for responder/management escalation.
PRIORITY_CHOICES = [
    discord.SelectOption(
        label="Priority 2 — Urgent",
        value="urgent",
        emoji="🟠",
        description="Time-sensitive assistance requiring prompt response.",
    ),
    discord.SelectOption(
        label="Priority 3 — Standard",
        value="standard",
        emoji="🟢",
        description="Routine assistance with no immediate threat.",
    ),
]

PRIORITY_DISPLAY = {
    "critical": "🔴 P1 — Critical",
    "urgent": "🟠 P2 — Urgent",
    "standard": "🟢 P3 — Standard",
}

PRIORITY_ORDER = ["standard", "urgent", "critical"]

STATUS_DISPLAY = {
    "awaiting_responder": "🔴 Awaiting Responder",
    "en_route": "🟡 En Route",
    "on_scene": "🟢 On Scene",
    "backup_requested": "🟠 Backup Requested",
}


def safe_channel_name(value):
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower().strip())
    return re.sub(r"-+", "-", value).strip("-")[:35] or "incident"


def responder_roles(guild, service):
    roles, missing = [], []
    for name in SERVICE_ROLE_NAMES.get(service, []):
        role = discord.utils.get(guild.roles, name=name)
        if role:
            roles.append(role)
        else:
            missing.append(name)
    return roles, missing


def all_responder_roles(guild):
    return [role for name in RESPONDER_ROLE_NAMES if (role := discord.utils.get(guild.roles, name=name))]


def is_responder(member):
    return isinstance(member, discord.Member) and any(role.name in RESPONDER_ROLE_NAMES for role in member.roles)


def next_channel_incident_number(guild):
    highest = 0
    for channel in guild.text_channels:
        match = re.match(r"^(?:closed-)?rescue-(\d{4,})-", channel.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def incident_number_from_channel(channel):
    match = re.search(r"rescue-(\d{4,})-", channel.name)
    return int(match.group(1)) if match else None


def incident_id_from_channel(channel):
    number = incident_number_from_channel(channel)
    return f"RESCUE-{number:04d}" if number is not None else "RESCUE"


def requester_id_from_embed(embed):
    if not embed.footer or not embed.footer.text:
        return None
    match = re.search(r"Requester ID: (\d+)", embed.footer.text)
    return int(match.group(1)) if match else None


def primary_id_from_embed(embed):
    field = next((field.value for field in embed.fields if field.name == "Primary Responder"), "")
    match = re.search(r"<@(\d+)>", field)
    return int(match.group(1)) if match else None


def priority_from_embed(embed):
    field = next((field.value for field in embed.fields if field.name == "Priority"), "")
    if "P1" in field:
        return "critical"
    if "P2" in field:
        return "urgent"
    return "standard"


def truncate(value, length):
    value = value.strip()
    return value if len(value) <= length else value[: length - 1] + "…"


class RescueBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.db_pool = None
        self.db_error = None

    async def setup_database(self):
        if not DATABASE_URL:
            logger.info("DATABASE_URL is not configured; using Discord channel numbering fallback.")
            return
        try:
            self.db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=15)
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rescue_incident_counters (
                        guild_id BIGINT PRIMARY KEY,
                        last_number BIGINT NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS rescue_incidents (
                        id BIGSERIAL PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        incident_number BIGINT NOT NULL,
                        channel_id BIGINT,
                        requester_id BIGINT NOT NULL,
                        callsign TEXT NOT NULL,
                        service TEXT NOT NULL,
                        location TEXT NOT NULL,
                        situation TEXT NOT NULL,
                        priority TEXT NOT NULL DEFAULT 'standard',
                        priority_changed_by BIGINT,
                        priority_changed_at TIMESTAMPTZ,
                        status TEXT NOT NULL DEFAULT 'awaiting_responder',
                        primary_responder_id BIGINT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        responded_at TIMESTAMPTZ,
                        arrived_at TIMESTAMPTZ,
                        backup_requested_at TIMESTAMPTZ,
                        closed_at TIMESTAMPTZ,
                        closed_by_id BIGINT,
                        UNIQUE(guild_id, incident_number),
                        UNIQUE(channel_id)
                    );

                    ALTER TABLE rescue_incidents
                    ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'standard';

                    ALTER TABLE rescue_incidents
                    ADD COLUMN IF NOT EXISTS priority_changed_by BIGINT;

                    ALTER TABLE rescue_incidents
                    ADD COLUMN IF NOT EXISTS priority_changed_at TIMESTAMPTZ;

                    CREATE INDEX IF NOT EXISTS rescue_incidents_guild_status_idx
                    ON rescue_incidents(guild_id, status);

                    CREATE TABLE IF NOT EXISTS rescue_dispatch_boards (
                        guild_id BIGINT PRIMARY KEY,
                        channel_id BIGINT NOT NULL,
                        message_id BIGINT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS rescue_incident_responders (
                        channel_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY(channel_id, user_id)
                    );
                    """
                )
            self.db_error = None
            logger.info("PostgreSQL connected and rescue schema is ready.")
        except Exception as exc:
            self.db_pool = None
            self.db_error = str(exc)
            logger.exception("PostgreSQL initialization failed; continuing with Discord fallback.")

    async def allocate_incident_number(self, guild_id):
        if not self.db_pool:
            return None
        try:
            async with self.db_pool.acquire() as conn:
                return await conn.fetchval(
                    """
                    INSERT INTO rescue_incident_counters(guild_id, last_number)
                    VALUES($1, 1)
                    ON CONFLICT(guild_id)
                    DO UPDATE SET last_number=rescue_incident_counters.last_number+1
                    RETURNING last_number
                    """,
                    guild_id,
                )
        except Exception:
            logger.exception("Failed to allocate incident number.")
            return None

    async def create_incident_record(self, **values):
        if not self.db_pool:
            return
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO rescue_incidents(
                        guild_id, incident_number, channel_id, requester_id,
                        callsign, service, location, situation, priority, status
                    )
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'awaiting_responder')
                    """,
                    values["guild_id"],
                    values["incident_number"],
                    values["channel_id"],
                    values["requester_id"],
                    values["callsign"],
                    values["service"],
                    values["location"],
                    values["situation"],
                    values["priority"],
                )
        except Exception:
            logger.exception("Failed to save incident.")

    async def update_incident(self, channel_id, action, user_id=None):
        if not self.db_pool:
            return
        query = {
            "respond": "UPDATE rescue_incidents SET status='en_route',primary_responder_id=$2,responded_at=COALESCE(responded_at,NOW()) WHERE channel_id=$1",
            "arrived": "UPDATE rescue_incidents SET status='on_scene',arrived_at=COALESCE(arrived_at,NOW()) WHERE channel_id=$1",
            "backup": "UPDATE rescue_incidents SET status='backup_requested',backup_requested_at=NOW() WHERE channel_id=$1",
            "close": "UPDATE rescue_incidents SET status='closed',closed_at=NOW(),closed_by_id=$2 WHERE channel_id=$1",
        }.get(action)
        if not query:
            return
        try:
            async with self.db_pool.acquire() as conn:
                if action in {"respond", "close"}:
                    await conn.execute(query, channel_id, user_id)
                else:
                    await conn.execute(query, channel_id)
        except Exception:
            logger.exception("Failed to update incident %s.", channel_id)

    async def update_priority(self, channel_id, priority, user_id):
        if not self.db_pool:
            return
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE rescue_incidents
                    SET priority=$2,
                        priority_changed_by=$3,
                        priority_changed_at=NOW()
                    WHERE channel_id=$1
                    """,
                    channel_id,
                    priority,
                    user_id,
                )
        except Exception:
            logger.exception("Failed to update priority for incident %s.", channel_id)

    async def add_responder(self, channel_id, user_id):
        if not self.db_pool:
            return
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO rescue_incident_responders(channel_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    channel_id,
                    user_id,
                )
        except Exception:
            logger.exception("Failed to add responder to incident %s.", channel_id)

    async def active_incidents(self, guild_id):
        if not self.db_pool:
            return []
        try:
            async with self.db_pool.acquire() as conn:
                return await conn.fetch(
                    """
                    SELECT incident_number, channel_id, callsign, service, location,
                           priority, status, primary_responder_id, created_at
                    FROM rescue_incidents
                    WHERE guild_id=$1 AND status<>'closed'
                    ORDER BY CASE priority
                        WHEN 'critical' THEN 1
                        WHEN 'urgent' THEN 2
                        ELSE 3
                    END, incident_number ASC
                    LIMIT 24
                    """,
                    guild_id,
                )
        except Exception:
            logger.exception("Failed to load active incidents.")
            return []

    async def build_dispatch_board_embed(self, guild):
        incidents = await self.active_incidents(guild.id)
        embed = discord.Embed(
            title="📡 STAR CITIZEN RESCUE — LIVE DISPATCH BOARD",
            description="Active rescue operations are sorted by priority and update automatically.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        if not incidents:
            embed.add_field(name="✅ No Active Incidents", value="All rescue calls are currently clear.", inline=False)
        for row in incidents:
            incident_id = f"RESCUE-{row['incident_number']:04d}"
            service = SERVICE_NAMES.get(row["service"], row["service"])
            status = STATUS_DISPLAY.get(row["status"], row["status"])
            priority = PRIORITY_DISPLAY.get(row["priority"], PRIORITY_DISPLAY["standard"])
            responder = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned"
            channel = f"<#{row['channel_id']}>"
            created = int(row["created_at"].timestamp())
            embed.add_field(
                name=f"{priority} • {incident_id}",
                value=(
                    f"**{service}** • {status}\n"
                    f"**Callsign:** {truncate(row['callsign'],45)}\n"
                    f"**Location:** {truncate(row['location'],90)}\n"
                    f"**Primary:** {responder} • **Opened:** <t:{created}:R>\n"
                    f"**Channel:** {channel}"
                ),
                inline=False,
            )
        embed.set_footer(text=f"Active Incidents: {len(incidents)} • P1 incidents appear first • Last refreshed")
        return embed

    async def save_dispatch_board(self, guild_id, channel_id, message_id):
        if not self.db_pool:
            return
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rescue_dispatch_boards(guild_id,channel_id,message_id,updated_at)
                VALUES($1,$2,$3,NOW())
                ON CONFLICT(guild_id)
                DO UPDATE SET channel_id=EXCLUDED.channel_id,
                              message_id=EXCLUDED.message_id,
                              updated_at=NOW()
                """,
                guild_id,
                channel_id,
                message_id,
            )

    async def refresh_dispatch_board(self, guild):
        if not self.db_pool:
            return False
        try:
            async with self.db_pool.acquire() as conn:
                board = await conn.fetchrow(
                    "SELECT channel_id,message_id FROM rescue_dispatch_boards WHERE guild_id=$1",
                    guild.id,
                )
            if not board:
                return False
            channel = guild.get_channel(board["channel_id"])
            if not isinstance(channel, discord.TextChannel):
                return False
            message = await channel.fetch_message(board["message_id"])
            await message.edit(embed=await self.build_dispatch_board_embed(guild))
            return True
        except Exception:
            logger.exception("Failed to refresh dispatch board.")
            return False

    async def setup_hook(self):
        await self.setup_database()
        self.add_view(RequestAssistanceView())
        self.add_view(IncidentControlsView())
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self):
        if self.db_pool:
            await self.db_pool.close()
        await super().close()


bot = RescueBot()


class RescueDetailsModal(discord.ui.Modal, title="Request Assistance"):
    callsign = discord.ui.TextInput(
        label="In-game name / callsign",
        placeholder="Your Star Citizen callsign",
        max_length=50,
    )
    location = discord.ui.TextInput(
        label="Current location",
        placeholder="Example: Daymar - Shubin Mining Facility SCD-1",
        max_length=100,
    )
    situation = discord.ui.TextInput(
        label="Situation",
        placeholder="Tell responders what happened and what you need.",
        style=discord.TextStyle.paragraph,
        max_length=700,
    )

    def __init__(self, service, priority):
        super().__init__()
        self.service = service
        self.priority = priority

    async def on_submit(self, interaction):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "Rescue requests must be submitted inside a server.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Active Incidents") or await guild.create_category(
            "Active Incidents",
            reason="Star Citizen rescue dispatch setup",
        )

        paged_roles, missing = responder_roles(guild, self.service)
        bot_member = guild.me
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        # Every responder sector can work every incident; only service-specific sectors are paged initially.
        for role in all_responder_roles(guild):
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                read_message_history=True,
            )

        number = await bot.allocate_incident_number(guild.id) or next_channel_incident_number(guild)
        incident_id = f"RESCUE-{number:04d}"
        service = SERVICE_NAMES.get(self.service, self.service)
        priority_display = PRIORITY_DISPLAY.get(self.priority, PRIORITY_DISPLAY["standard"])

        channel = await guild.create_text_channel(
            f"rescue-{number:04d}-{safe_channel_name(str(self.callsign))}",
            category=category,
            overwrites=overwrites,
            topic=f"{incident_id} | {priority_display} | {service} | Requester: {interaction.user.id}",
        )

        await bot.create_incident_record(
            guild_id=guild.id,
            incident_number=number,
            channel_id=channel.id,
            requester_id=interaction.user.id,
            callsign=str(self.callsign),
            service=self.service,
            location=str(self.location),
            situation=str(self.situation),
            priority=self.priority,
        )

        color = discord.Color.orange() if self.priority == "urgent" else discord.Color.green()
        embed = discord.Embed(
            title=f"🚨 {incident_id} — ACTIVE RESCUE REQUEST",
            description="A new Star Citizen rescue incident has been opened.",
            color=color,
        )
        fields = [
            ("Priority", priority_display, True),
            ("Status", "🔴 Awaiting Responder", True),
            ("Primary Responder", "Unassigned", True),
            ("Service", service, True),
            ("Requester", interaction.user.mention, True),
            ("Callsign", str(self.callsign), True),
            ("Location", str(self.location), False),
            ("Situation", str(self.situation), False),
        ]
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text=f"Requester ID: {interaction.user.id} | Incident: {incident_id}")

        mentions = " ".join(role.mention for role in paged_roles)
        await channel.send(
            content=f"🚨 **DISPATCH:** {mentions or 'No responder role was found.'}\n{interaction.user.mention} your rescue channel is ready.",
            embed=embed,
            view=IncidentControlsView(),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
        if missing:
            await channel.send("⚠️ I could not find: " + ", ".join(f"`{name}`" for name in missing))

        await bot.refresh_dispatch_board(guild)
        await interaction.followup.send(f"{incident_id} created: {channel.mention}", ephemeral=True)


class PrioritySelect(discord.ui.Select):
    def __init__(self, service):
        self.service = service
        super().__init__(
            placeholder="Select incident priority...",
            min_values=1,
            max_values=1,
            options=PRIORITY_CHOICES,
        )

    async def callback(self, interaction):
        await interaction.response.send_modal(RescueDetailsModal(self.service, self.values[0]))


class ServiceSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Select the type of assistance you need...",
            min_values=1,
            max_values=1,
            options=SERVICE_CHOICES,
            custom_id="rescue:service-select",
        )

    async def callback(self, interaction):
        view = discord.ui.View(timeout=120)
        view.add_item(PrioritySelect(self.values[0]))
        await interaction.response.edit_message(
            content="Now select the priority of your incident. P1 Critical is assigned only by the primary responder or server management.",
            view=view,
        )


class RequestAssistanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request Assistance", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="rescue:request")
    async def request_assistance(self, interaction, button):
        view = discord.ui.View(timeout=120)
        view.add_item(ServiceSelect())
        await interaction.response.send_message(
            "Select the service you need. You will choose P2 Urgent or P3 Standard next.",
            view=view,
            ephemeral=True,
        )


class IncidentControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def set_field(self, embed, name, value):
        for index, field in enumerate(embed.fields):
            if field.name == name:
                embed.set_field_at(index, name=name, value=value, inline=field.inline)
                return

    async def require_responder(self, interaction):
        if is_responder(interaction.user):
            return True
        await interaction.response.send_message(
            "These operational controls are limited to S.E.R.E., Military, and Logistics Sector members.",
            ephemeral=True,
        )
        return False

    async def update_status(self, interaction, status, color, action):
        if not await self.require_responder(interaction):
            return
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)
        embed = interaction.message.embeds[0].copy()
        self.set_field(embed, "Status", status)
        embed.color = color
        await interaction.response.edit_message(embed=embed, view=self)
        await bot.update_incident(interaction.channel.id, action, interaction.user.id)
        await bot.refresh_dispatch_board(interaction.guild)

    async def change_priority(self, interaction, direction):
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        manager = bool(member and member.guild_permissions.manage_guild)
        responder = is_responder(member)
        if not responder and not manager:
            return await interaction.response.send_message(
                "Priority controls are limited to responder-sector members and server managers.",
                ephemeral=True,
            )

        embed = interaction.message.embeds[0].copy()
        current = priority_from_embed(embed)
        index = PRIORITY_ORDER.index(current)
        new_index = max(0, min(len(PRIORITY_ORDER) - 1, index + direction))
        new_priority = PRIORITY_ORDER[new_index]

        if new_priority == current:
            boundary = "highest" if direction > 0 else "lowest"
            return await interaction.response.send_message(
                f"This incident is already at the {boundary} priority level.",
                ephemeral=True,
            )

        # P1 is deliberately protected: only the current primary responder or a server manager may create it.
        if current == "urgent" and new_priority == "critical":
            primary = primary_id_from_embed(embed)
            if interaction.user.id != primary and not manager:
                primary_text = f"<@{primary}>" if primary else "the assigned primary responder"
                return await interaction.response.send_message(
                    f"P1 Critical can only be declared by {primary_text} or someone with Manage Server permission.",
                    ephemeral=True,
                )

        self.set_field(embed, "Priority", PRIORITY_DISPLAY[new_priority])
        if new_priority == "critical":
            embed.color = discord.Color.red()
        elif new_priority == "urgent":
            embed.color = discord.Color.orange()
        else:
            embed.color = discord.Color.green()

        await interaction.response.edit_message(embed=embed, view=self)
        await bot.update_priority(interaction.channel.id, new_priority, interaction.user.id)
        await bot.refresh_dispatch_board(interaction.guild)

        if new_priority == "critical":
            await interaction.followup.send(
                f"🚨 **P1 CRITICAL DECLARED:** {incident_id_from_channel(interaction.channel)} was escalated to **{PRIORITY_DISPLAY[new_priority]}** by {interaction.user.mention}."
            )
            roles = all_responder_roles(interaction.guild)
            if roles:
                await interaction.followup.send(
                    "🔴 **ALL-SECTOR PRIORITY 1 PAGE:** " + " ".join(role.mention for role in roles),
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
        else:
            await interaction.followup.send(
                f"⚠️ {incident_id_from_channel(interaction.channel)} priority changed to **{PRIORITY_DISPLAY[new_priority]}** by {interaction.user.mention}."
            )

    @discord.ui.button(label="Respond", style=discord.ButtonStyle.success, emoji="🚀", custom_id="rescue:respond", row=0)
    async def respond(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        embed = interaction.message.embeds[0].copy()
        primary = next((field.value for field in embed.fields if field.name == "Primary Responder"), "Unassigned")
        if primary != "Unassigned" and interaction.user.mention not in primary:
            return await interaction.response.send_message(
                f"This incident is already claimed by {primary}. Use **Join Response** to assist.",
                ephemeral=True,
            )
        self.set_field(embed, "Primary Responder", interaction.user.mention)
        self.set_field(embed, "Status", f"🟡 En Route — {interaction.user.mention}")
        await interaction.response.edit_message(embed=embed, view=self)
        await bot.update_incident(interaction.channel.id, "respond", interaction.user.id)
        await bot.add_responder(interaction.channel.id, interaction.user.id)
        await bot.refresh_dispatch_board(interaction.guild)
        await interaction.followup.send(
            f"🚀 {interaction.user.mention} has claimed {incident_id_from_channel(interaction.channel)} and is responding."
        )

    @discord.ui.button(label="Join Response", style=discord.ButtonStyle.secondary, emoji="➕", custom_id="rescue:join", row=0)
    async def join_response(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        await bot.add_responder(interaction.channel.id, interaction.user.id)
        await interaction.response.send_message(
            f"➕ {interaction.user.mention} joined the response for {incident_id_from_channel(interaction.channel)}."
        )

    @discord.ui.button(label="Arrived", style=discord.ButtonStyle.primary, emoji="📍", custom_id="rescue:arrived", row=0)
    async def arrived(self, interaction, button):
        await self.update_status(
            interaction,
            f"🟢 On Scene — {interaction.user.mention}",
            discord.Color.green(),
            "arrived",
        )

    @discord.ui.button(label="Need Backup", style=discord.ButtonStyle.secondary, emoji="🛡️", custom_id="rescue:backup", row=0)
    async def backup(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        embed = interaction.message.embeds[0].copy()
        self.set_field(embed, "Status", f"🟠 Backup Requested — {interaction.user.mention}")
        await interaction.response.edit_message(embed=embed, view=self)
        await bot.update_incident(interaction.channel.id, "backup")
        await bot.refresh_dispatch_board(interaction.guild)
        roles = all_responder_roles(interaction.guild)
        await interaction.followup.send(
            "🛡️ **BACKUP REQUESTED:** " + " ".join(role.mention for role in roles),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    @discord.ui.button(label="Close Incident", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="rescue:close", row=0)
    async def close_incident(self, interaction, button):
        if not interaction.message or not interaction.message.embeds or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("I could not close this incident.", ephemeral=True)

        embed = interaction.message.embeds[0].copy()
        requester = requester_id_from_embed(embed)
        primary = primary_id_from_embed(embed)
        manager = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild
        if interaction.user.id not in {requester, primary} and not manager:
            return await interaction.response.send_message(
                "Only the requester, primary responder, or a server manager can close this incident.",
                ephemeral=True,
            )

        channel = interaction.channel
        self.set_field(embed, "Status", f"⚫ Closed — {interaction.user.mention}")
        embed.color = discord.Color.dark_grey()
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)
        await bot.update_incident(channel.id, "close", interaction.user.id)
        await bot.refresh_dispatch_board(channel.guild)
        await channel.send(
            f"🔒 {incident_id_from_channel(channel)} closed by {interaction.user.mention}. This incident is now read-only."
        )

        if requester and (member := channel.guild.get_member(requester)):
            await channel.set_permissions(member, view_channel=True, send_messages=False, read_message_history=True)
        for role in all_responder_roles(channel.guild):
            await channel.set_permissions(role, view_channel=True, send_messages=False, read_message_history=True)
        await channel.edit(
            name=f"closed-{channel.name}"[:100],
            topic=f"CLOSED | {channel.topic or incident_id_from_channel(channel)}",
        )

    @discord.ui.button(label="Escalate Priority", style=discord.ButtonStyle.danger, emoji="⬆️", custom_id="rescue:priority-up", row=1)
    async def escalate_priority(self, interaction, button):
        await self.change_priority(interaction, 1)

    @discord.ui.button(label="Lower Priority", style=discord.ButtonStyle.secondary, emoji="⬇️", custom_id="rescue:priority-down", row=1)
    async def lower_priority(self, interaction, button):
        await self.change_priority(interaction, -1)


@bot.event
async def on_ready():
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")
    logger.info("Database: %s", "online" if bot.db_pool else ("error" if bot.db_error else "not configured"))
    if bot.db_pool:
        for guild in bot.guilds:
            await bot.refresh_dispatch_board(guild)


@bot.tree.command(name="ping", description="Check whether the rescue bot is online.")
async def ping(interaction):
    await interaction.response.send_message(
        embed=discord.Embed(
            title="Rescue Dispatch Online",
            description="Star Citizen Rescue Bot is operational.",
            color=discord.Color.green(),
        ),
        ephemeral=True,
    )


@bot.tree.command(name="rescue-status", description="Show the current rescue-system status.")
async def rescue_status(interaction):
    database = "Online" if bot.db_pool else ("Connection Error" if bot.db_error else "Not Configured")
    embed = discord.Embed(
        title="Star Citizen Rescue Dispatch",
        description="Rescue requests, cross-sector response, safeguarded priority escalation, persistence, live dispatch board, and controls are online.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Discord", value="Online")
    embed.add_field(name="Incident System", value="Online")
    embed.add_field(name="Sector Paging", value="Online")
    embed.add_field(name="Priority System", value="Safeguarded")
    embed.add_field(name="Database", value=database)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rescue-setup", description="Post the permanent rescue request panel in this channel.")
@app_commands.checks.has_permissions(manage_guild=True)
async def rescue_setup(interaction):
    embed = discord.Embed(
        title="🚨 STAR CITIZEN RESCUE DISPATCH",
        description=(
            "Need assistance in the 'verse? Use the button below to open a rescue request. "
            "You will choose a service and request priority before entering the incident details."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(
        name="Available Services",
        value="🚑 Medical Rescue\n🔎 Search & Rescue\n🔧 Repair / Refuel\n🛡️ Security / Escort\n🚀 Recovery / Transport",
        inline=False,
    )
    embed.add_field(
        name="Priority Levels",
        value=(
            "🔴 P1 Critical — responder/management escalation only\n"
            "🟠 P2 Urgent — time-sensitive request\n"
            "🟢 P3 Standard — routine assistance"
        ),
        inline=False,
    )
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(embed=embed, view=RequestAssistanceView())
    await interaction.followup.send("Rescue request panel posted.", ephemeral=True)


@bot.tree.command(name="dispatch-board-setup", description="Create or move the live rescue dispatch board to this channel.")
@app_commands.checks.has_permissions(manage_guild=True)
async def dispatch_board_setup(interaction):
    if not bot.db_pool:
        return await interaction.response.send_message(
            "The dispatch board requires PostgreSQL online.",
            ephemeral=True,
        )
    await interaction.response.defer(ephemeral=True)
    message = await interaction.channel.send(embed=await bot.build_dispatch_board_embed(interaction.guild))
    await bot.save_dispatch_board(interaction.guild.id, interaction.channel.id, message.id)
    await interaction.followup.send(f"Live dispatch board created: {message.jump_url}", ephemeral=True)


def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set.")
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
