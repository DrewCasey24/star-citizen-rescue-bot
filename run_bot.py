import asyncio
import logging
import os

import discord

import bot as core
from incident_transitions import transition_incident

logger = logging.getLogger("star-citizen-rescue-bot.config")

CONFIG_CACHE = {}
DASHBOARD_PUBLIC_URL = os.getenv(
    "DASHBOARD_PUBLIC_URL",
    "https://dashboard-production-c2b3.up.railway.app",
).rstrip("/")


def dispatch_board_view():
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Open Web Dashboard",
            style=discord.ButtonStyle.link,
            emoji="🌐",
            url=DASHBOARD_PUBLIC_URL,
        )
    )
    return view


async def refresh_config_cache(bot_instance):
    if not bot_instance.db_pool:
        return
    try:
        async with bot_instance.db_pool.acquire() as conn:
            guild_rows = await conn.fetch(
                """
                SELECT guild_id, responder_role_ids, request_channel_id, incident_category_id
                FROM rescue_guild_settings
                """
            )
            service_rows = await conn.fetch(
                """
                SELECT guild_id, service, role_ids
                FROM rescue_service_role_settings
                """
            )
        next_cache = {}
        for row in guild_rows:
            next_cache[row["guild_id"]] = {
                "responder_role_ids": list(row["responder_role_ids"] or []),
                "request_channel_id": row["request_channel_id"],
                "incident_category_id": row["incident_category_id"],
                "service_role_ids": {},
            }
        for row in service_rows:
            entry = next_cache.setdefault(
                row["guild_id"],
                {
                    "responder_role_ids": [],
                    "request_channel_id": None,
                    "incident_category_id": None,
                    "service_role_ids": {},
                },
            )
            entry["service_role_ids"][row["service"]] = list(row["role_ids"] or [])
        CONFIG_CACHE.clear()
        CONFIG_CACHE.update(next_cache)
    except Exception:
        logger.exception("Failed to refresh dashboard configuration cache.")


async def config_refresh_loop(bot_instance):
    while not bot_instance.is_closed():
        await asyncio.sleep(10)
        await refresh_config_cache(bot_instance)


def configured_all_responder_roles(guild):
    config = CONFIG_CACHE.get(guild.id, {})
    role_ids = config.get("responder_role_ids") or []
    if role_ids:
        return [role for role_id in role_ids if (role := guild.get_role(role_id))]
    return [
        role
        for name in core.RESPONDER_ROLE_NAMES
        if (role := discord.utils.get(guild.roles, name=name))
    ]


def configured_responder_roles(guild, service):
    config = CONFIG_CACHE.get(guild.id, {})
    service_map = config.get("service_role_ids", {})
    if service in service_map:
        ids = service_map.get(service) or []
        roles = [role for role_id in ids if (role := guild.get_role(role_id))]
        missing = [f"Role ID {role_id}" for role_id in ids if guild.get_role(role_id) is None]
        return roles, missing

    roles, missing = [], []
    for name in core.SERVICE_ROLE_NAMES.get(service, []):
        role = discord.utils.get(guild.roles, name=name)
        if role:
            roles.append(role)
        else:
            missing.append(name)
    return roles, missing


def configured_is_responder(member):
    if not isinstance(member, discord.Member):
        return False
    config = CONFIG_CACHE.get(member.guild.id, {})
    role_ids = set(config.get("responder_role_ids") or [])
    if role_ids:
        return any(role.id in role_ids for role in member.roles)
    return any(role.name in core.RESPONDER_ROLE_NAMES for role in member.roles)


_original_setup_database = core.RescueBot.setup_database
_original_close = core.RescueBot.close
_original_create_incident_record = core.RescueBot.create_incident_record
_original_update_priority = core.RescueBot.update_priority
_original_add_responder = core.RescueBot.add_responder
_original_refresh_dispatch_board = core.RescueBot.refresh_dispatch_board


async def refresh_dispatch_board_with_dashboard(self, guild):
    refreshed = await _original_refresh_dispatch_board(self, guild)
    if not refreshed or not self.db_pool:
        return refreshed
    try:
        async with self.db_pool.acquire() as conn:
            board = await conn.fetchrow(
                "SELECT channel_id,message_id FROM rescue_dispatch_boards WHERE guild_id=$1",
                guild.id,
            )
        if not board:
            return refreshed
        channel = guild.get_channel(board["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return refreshed
        message = await channel.fetch_message(board["message_id"])
        await message.edit(view=dispatch_board_view())
    except Exception:
        logger.exception("Failed to attach web dashboard button to dispatch board.")
    return refreshed


async def record_incident_event(self, channel_id, event_type, title, details="", actor_id=None):
    """Append an immutable event to the incident ledger."""
    if not self.db_pool:
        return
    try:
        async with self.db_pool.acquire() as conn:
            incident = await conn.fetchrow(
                "SELECT guild_id, incident_number FROM rescue_incidents WHERE channel_id=$1",
                channel_id,
            )
            if not incident:
                return
            await conn.execute(
                """
                INSERT INTO rescue_incident_events(
                    guild_id, incident_number, channel_id, event_type,
                    actor_id, title, details, created_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7,NOW())
                """,
                incident["guild_id"], incident["incident_number"], channel_id,
                event_type, actor_id, title, details,
            )
    except Exception:
        logger.exception("Failed to append incident event %s for channel %s.", event_type, channel_id)


async def create_incident_record_with_ledger(self, **values):
    await _original_create_incident_record(self, **values)
    await record_incident_event(
        self, values["channel_id"], "created", "Incident Created",
        f"{core.SERVICE_NAMES.get(values['service'], values['service'])} request opened at {values['location']}.",
        values["requester_id"],
    )


async def update_incident_atomic(self, channel_id, action, user_id=None):
    """Use the shared locked transition engine for Discord lifecycle actions."""
    return await transition_incident(self, channel_id, action, user_id)


async def update_priority_with_ledger(self, channel_id, priority, user_id):
    previous = None
    if self.db_pool:
        try:
            async with self.db_pool.acquire() as conn:
                previous = await conn.fetchval("SELECT priority FROM rescue_incidents WHERE channel_id=$1", channel_id)
        except Exception:
            logger.exception("Failed to read priority before ledger update.")
    await _original_update_priority(self, channel_id, priority, user_id)
    if previous is not None and previous != priority:
        old_label = core.PRIORITY_DISPLAY.get(previous, previous)
        new_label = core.PRIORITY_DISPLAY.get(priority, priority)
        await record_incident_event(self, channel_id, "priority_changed", "Priority Changed", f"Priority changed from {old_label} to {new_label}.", user_id)


async def add_responder_with_ledger(self, channel_id, user_id):
    existed = False
    if self.db_pool:
        try:
            async with self.db_pool.acquire() as conn:
                existed = bool(await conn.fetchval(
                    "SELECT 1 FROM rescue_incident_responders WHERE channel_id=$1 AND user_id=$2", channel_id, user_id
                ))
        except Exception:
            logger.exception("Failed to inspect responder before ledger update.")
    await _original_add_responder(self, channel_id, user_id)
    if not existed:
        primary_id = None
        if self.db_pool:
            try:
                async with self.db_pool.acquire() as conn:
                    primary_id = await conn.fetchval("SELECT primary_responder_id FROM rescue_incidents WHERE channel_id=$1", channel_id)
            except Exception:
                logger.exception("Failed to inspect primary responder after responder join.")
        if primary_id != user_id:
            await record_incident_event(self, channel_id, "responder_joined", "Responder Joined", "An additional responder joined the response team.", user_id)


async def remove_responder_with_ledger(self, channel_id, user_id):
    """Remove a responder from the active response and return (removed, was_primary)."""
    if not self.db_pool:
        return False, False
    removed = False
    was_primary = False
    try:
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                incident = await conn.fetchrow(
                    "SELECT status,primary_responder_id FROM rescue_incidents WHERE channel_id=$1 FOR UPDATE", channel_id
                )
                if not incident or incident["status"] == "closed":
                    return False, False
                listed = bool(await conn.fetchval(
                    "SELECT 1 FROM rescue_incident_responders WHERE channel_id=$1 AND user_id=$2", channel_id, user_id
                ))
                was_primary = incident["primary_responder_id"] == user_id
                if not listed and not was_primary:
                    return False, False
                await conn.execute("DELETE FROM rescue_incident_responders WHERE channel_id=$1 AND user_id=$2", channel_id, user_id)
                if was_primary:
                    await conn.execute(
                        "UPDATE rescue_incidents SET primary_responder_id=NULL,status='awaiting_responder' WHERE channel_id=$1", channel_id
                    )
                removed = True
    except Exception:
        logger.exception("Failed to remove responder %s from incident channel %s.", user_id, channel_id)
        return False, False
    if removed:
        details = (
            "The primary responder left the response; the incident returned to Awaiting Responder."
            if was_primary else "A support responder left the active response team."
        )
        await record_incident_event(self, channel_id, "responder_left", "Responder Left Response", details, user_id)
    return removed, was_primary


async def remaining_responder_ids(bot_instance, channel_id):
    if not bot_instance.db_pool:
        return []
    try:
        async with bot_instance.db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM rescue_incident_responders WHERE channel_id=$1 ORDER BY joined_at ASC",
                channel_id,
            )
        return [row["user_id"] for row in rows]
    except Exception:
        logger.exception("Failed to load remaining responders for incident channel %s.", channel_id)
        return []


class IncidentControlsViewWithLeave(core.IncidentControlsView):
    @discord.ui.button(label="Need Backup", style=discord.ButtonStyle.secondary, emoji="🛡️", custom_id="rescue:backup", row=0)
    async def backup(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)

        changed, reason = await core.bot.update_incident(interaction.channel.id, "backup", interaction.user.id)
        if not changed:
            messages = {
                "backup_already_requested": "Backup has already been requested for this incident.",
                "closed": "This incident is already closed.",
                "no_primary": "A primary responder must be assigned before requesting backup.",
                "database_unavailable": "The rescue database is unavailable; backup was not recorded.",
            }
            return await interaction.response.send_message(
                messages.get(reason, "The backup request could not be recorded. Please try again."),
                ephemeral=True,
            )

        embed = interaction.message.embeds[0].copy()
        self.set_field(embed, "Status", f"🟠 Backup Requested — {interaction.user.mention}")
        await interaction.response.edit_message(embed=embed, view=self)
        await core.bot.refresh_dispatch_board(interaction.guild)
        roles = core.all_responder_roles(interaction.guild)
        await interaction.followup.send(
            "🛡️ **BACKUP REQUESTED:** " + " ".join(role.mention for role in roles),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    @discord.ui.button(label="Leave Response", style=discord.ButtonStyle.secondary, emoji="↩️", custom_id="rescue:leave", row=1)
    async def leave_response(self, interaction, button):
        if not isinstance(interaction.channel, discord.TextChannel) or interaction.guild is None:
            return await interaction.response.send_message("This control is only available inside an active rescue incident channel.", ephemeral=True)
        if not core.bot.db_pool:
            return await interaction.response.send_message("Responder withdrawal requires the rescue database to be online.", ephemeral=True)

        removed, was_primary = await core.bot.remove_responder(interaction.channel.id, interaction.user.id)
        if not removed:
            return await interaction.response.send_message("You are not currently listed as a responder on this incident.", ephemeral=True)

        incident_id = core.incident_id_from_channel(interaction.channel)
        if was_primary and interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0].copy()
            self.set_field(embed, "Primary Responder", "Unassigned")
            self.set_field(embed, "Status", "🔴 Awaiting Responder")
            priority = core.priority_from_embed(embed)
            if priority == "critical":
                embed.color = discord.Color.red()
            elif priority == "urgent":
                embed.color = discord.Color.orange()
            else:
                embed.color = discord.Color.green()
            await interaction.response.edit_message(embed=embed, view=self)

            remaining = await remaining_responder_ids(core.bot, interaction.channel.id)
            remaining_text = ", ".join(f"<@{user_id}>" for user_id in remaining) if remaining else "None"
            await interaction.followup.send(
                f"⚠️ **PRIMARY RESPONDER WITHDREW**\n"
                f"{interaction.user.mention} released primary responsibility for **{incident_id}**.\n"
                f"**Remaining responders:** {remaining_text}\n"
                f"**A new primary responder is required.** Use **Respond** to accept primary responsibility.",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        else:
            await interaction.response.send_message(f"↩️ {interaction.user.mention} left the response for {incident_id}.")

        await core.bot.refresh_dispatch_board(interaction.guild)


async def setup_database_with_dashboard(self):
    await _original_setup_database(self)
    if not self.db_pool:
        return
    async with self.db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rescue_guild_settings (
                guild_id BIGINT PRIMARY KEY,
                responder_role_ids BIGINT[] NOT NULL DEFAULT '{}',
                request_channel_id BIGINT,
                incident_category_id BIGINT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS rescue_service_role_settings (
                guild_id BIGINT NOT NULL,
                service TEXT NOT NULL,
                role_ids BIGINT[] NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY(guild_id, service)
            );
            CREATE TABLE IF NOT EXISTS rescue_incident_events (
                id BIGSERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                incident_number BIGINT NOT NULL,
                channel_id BIGINT,
                event_type TEXT NOT NULL,
                actor_id BIGINT,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS rescue_incident_events_incident_idx
            ON rescue_incident_events(guild_id, incident_number, created_at, id);
            """
        )
    await refresh_config_cache(self)


async def setup_hook_multi_guild(self):
    """Initialize persistence/views and globally sync commands for every installed guild."""
    await self.setup_database()
    self.add_view(core.RequestAssistanceView())
    self.add_view(core.IncidentControlsView())
    await self.tree.sync()
    logger.info("Global application commands synced for multi-guild operation.")
    if self.db_pool and not getattr(self, "_dashboard_config_task", None):
        self._dashboard_config_task = asyncio.create_task(config_refresh_loop(self))


async def close_with_dashboard(self):
    task = getattr(self, "_dashboard_config_task", None)
    if task:
        task.cancel()
    await _original_close(self)


async def dynamic_request_submit(self, interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Rescue requests must be submitted inside a server.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    config = CONFIG_CACHE.get(guild.id, {})
    category = None
    category_id = config.get("incident_category_id")
    if category_id:
        candidate = guild.get_channel(category_id)
        if isinstance(candidate, discord.CategoryChannel):
            category = candidate
    if category is None:
        category = discord.utils.get(guild.categories, name="Active Incidents") or await guild.create_category("Active Incidents", reason="Star Citizen rescue dispatch setup")

    paged_roles, missing = configured_responder_roles(guild, self.service)
    bot_member = guild.me
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    for role in configured_all_responder_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    if bot_member:
        overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)

    number = await core.bot.allocate_incident_number(guild.id) or core.next_channel_incident_number(guild)
    incident_id = f"RESCUE-{number:04d}"
    service = core.SERVICE_NAMES.get(self.service, self.service)
    priority_display = core.PRIORITY_DISPLAY.get(self.priority, core.PRIORITY_DISPLAY["standard"])
    channel = await guild.create_text_channel(
        f"rescue-{number:04d}-{core.safe_channel_name(str(self.callsign))}", category=category, overwrites=overwrites,
        topic=f"{incident_id} | {priority_display} | {service} | Requester: {interaction.user.id}",
    )
    await core.bot.create_incident_record(
        guild_id=guild.id, incident_number=number, channel_id=channel.id, requester_id=interaction.user.id,
        callsign=str(self.callsign), service=self.service, location=str(self.location), situation=str(self.situation), priority=self.priority,
    )
    color = discord.Color.orange() if self.priority == "urgent" else discord.Color.green()
    embed = discord.Embed(title=f"🚨 {incident_id} — ACTIVE RESCUE REQUEST", description="A new Star Citizen rescue incident has been opened.", color=color)
    fields = [
        ("Priority", priority_display, True), ("Status", "🔴 Awaiting Responder", True), ("Primary Responder", "Unassigned", True),
        ("Service", service, True), ("Requester", interaction.user.mention, True), ("Callsign", str(self.callsign), True),
        ("Location", str(self.location), False), ("Situation", str(self.situation), False),
    ]
    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text=f"Requester ID: {interaction.user.id} | Incident: {incident_id}")
    mentions = " ".join(role.mention for role in paged_roles)
    await channel.send(
        content=f"🚨 **DISPATCH:** {mentions or 'No responder role was found.'}\n{interaction.user.mention} your rescue channel is ready.",
        embed=embed, view=core.IncidentControlsView(), allowed_mentions=discord.AllowedMentions(roles=True, users=True),
    )
    if missing:
        await channel.send("⚠️ I could not find: " + ", ".join(f"`{name}`" for name in missing))
    await core.bot.refresh_dispatch_board(guild)
    await interaction.followup.send(f"{incident_id} created: {channel.mention}", ephemeral=True)


core.IncidentControlsView = IncidentControlsViewWithLeave
core.RescueBot.setup_database = setup_database_with_dashboard
core.RescueBot.setup_hook = setup_hook_multi_guild
core.RescueBot.close = close_with_dashboard
core.RescueBot.create_incident_record = create_incident_record_with_ledger
core.RescueBot.update_incident = update_incident_atomic
core.RescueBot.update_priority = update_priority_with_ledger
core.RescueBot.add_responder = add_responder_with_ledger
core.RescueBot.remove_responder = remove_responder_with_ledger
core.RescueBot.refresh_dispatch_board = refresh_dispatch_board_with_dashboard
core.responder_roles = configured_responder_roles
core.all_responder_roles = configured_all_responder_roles
core.is_responder = configured_is_responder
core.RescueDetailsModal.on_submit = dynamic_request_submit


if __name__ == "__main__":
    core.main()