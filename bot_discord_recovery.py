"""Automatic Discord reconciliation for database-authoritative rescue incidents.

The database is the source of truth.  This module periodically repairs incident
cards, closed-channel state, and dispatch boards after transient Discord API
failures or restarts.
"""

import asyncio
import logging

import discord

import bot as core

logger = logging.getLogger("star-citizen-rescue-bot.recovery")
RECOVERY_INTERVAL_SECONDS = 30
RECENT_CLOSED_HOURS = 6


def _set_field(embed, name, value):
    for index, field in enumerate(embed.fields):
        if field.name == name:
            if field.value != value:
                embed.set_field_at(index, name=name, value=value, inline=field.inline)
                return True
            return False
    embed.add_field(name=name, value=value, inline=True)
    return True


def _status_text(row):
    status = row["status"]
    primary = row["primary_responder_id"]
    actor = row["closed_by_id"]
    if status == "awaiting_responder":
        return "🔴 Awaiting Responder"
    if status == "en_route":
        return f"🟡 En Route — <@{primary}>" if primary else "🟡 En Route"
    if status == "on_scene":
        return f"🟢 On Scene — <@{primary}>" if primary else "🟢 On Scene"
    if status == "backup_requested":
        return f"🟠 Backup Requested — <@{primary}>" if primary else "🟠 Backup Requested"
    if status == "closed":
        return f"⚫ Closed — <@{actor}>" if actor else "⚫ Closed"
    return core.STATUS_DISPLAY.get(status, status)


def _desired_color(row):
    if row["status"] == "closed":
        return discord.Color.dark_grey()
    if row["status"] == "on_scene":
        return discord.Color.green()
    if row["status"] == "backup_requested":
        return discord.Color.orange()
    if row["priority"] == "critical":
        return discord.Color.red()
    if row["priority"] == "urgent":
        return discord.Color.orange()
    return discord.Color.green()


def _new_embed(row):
    incident_id = f"RESCUE-{row['incident_number']:04d}"
    service = core.SERVICE_NAMES.get(row["service"], row["service"])
    embed = discord.Embed(
        title=f"🚨 {incident_id} — ACTIVE RESCUE REQUEST" if row["status"] != "closed" else f"🔒 {incident_id} — CLOSED RESCUE REQUEST",
        description="Recovered from the rescue database after Discord synchronization was interrupted.",
        color=_desired_color(row),
    )
    embed.add_field(name="Priority", value=core.PRIORITY_DISPLAY.get(row["priority"], row["priority"]), inline=True)
    embed.add_field(name="Status", value=_status_text(row), inline=True)
    primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned"
    embed.add_field(name="Primary Responder", value=primary, inline=True)
    embed.add_field(name="Service", value=service, inline=True)
    embed.add_field(name="Requester", value=f"<@{row['requester_id']}>", inline=True)
    embed.add_field(name="Callsign", value=row["callsign"], inline=True)
    embed.add_field(name="Location", value=row["location"], inline=False)
    embed.add_field(name="Situation", value=row["situation"], inline=False)
    embed.set_footer(text=f"Requester ID: {row['requester_id']} | Incident: {incident_id}")
    return embed


async def _find_or_recreate_card(bot, channel, row):
    message = None
    message_id = row["incident_message_id"]
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            message = None

    if message is None:
        incident_id = f"RESCUE-{row['incident_number']:04d}"
        try:
            async for candidate in channel.history(limit=50):
                if any(incident_id in (embed.title or "") for embed in candidate.embeds):
                    message = candidate
                    break
        except discord.Forbidden:
            return None

    if message is None:
        view = core.IncidentControlsView()
        if row["status"] == "closed":
            for child in view.children:
                child.disabled = True
        message = await channel.send(embed=_new_embed(row), view=view)
        logger.warning("Recreated missing incident card for RESCUE-%04d in channel %s.", row["incident_number"], channel.id)

    if message.id != message_id:
        async with bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE rescue_incidents SET incident_message_id=$2 WHERE channel_id=$1", channel.id, message.id)
    return message


async def _repair_card(bot, channel, row):
    message = await _find_or_recreate_card(bot, channel, row)
    if message is None:
        return False

    embed = message.embeds[0].copy() if message.embeds else _new_embed(row)
    changed = False
    changed |= _set_field(embed, "Priority", core.PRIORITY_DISPLAY.get(row["priority"], row["priority"]))
    changed |= _set_field(embed, "Status", _status_text(row))
    primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned"
    changed |= _set_field(embed, "Primary Responder", primary)
    desired_color = _desired_color(row)
    if embed.color != desired_color:
        embed.color = desired_color
        changed = True

    view = core.IncidentControlsView()
    if row["status"] == "closed":
        for child in view.children:
            child.disabled = True

    # Re-attach the persistent controls even if the embed itself is already correct.
    await message.edit(embed=embed, view=view)
    return changed


async def _repair_closed_channel(channel, row):
    changed = False
    requester = channel.guild.get_member(row["requester_id"])
    targets = []
    if requester:
        targets.append(requester)
    targets.extend(core.all_responder_roles(channel.guild))

    for target in targets:
        overwrite = channel.overwrites_for(target)
        if overwrite.view_channel is not True or overwrite.send_messages is not False or overwrite.read_message_history is not True:
            await channel.set_permissions(target, view_channel=True, send_messages=False, read_message_history=True)
            changed = True

    desired_name = channel.name if channel.name.startswith("closed-") else f"closed-{channel.name}"
    desired_name = desired_name[:100]
    topic = channel.topic or f"RESCUE-{row['incident_number']:04d}"
    desired_topic = topic if topic.startswith("CLOSED |") else f"CLOSED | {topic}"
    if channel.name != desired_name or channel.topic != desired_topic:
        await channel.edit(name=desired_name, topic=desired_topic)
        changed = True
    return changed


async def _retire_missing_channel_incident(bot, row):
    """Retire an incident only after Discord confirms its channel no longer exists."""
    async with bot.db_pool.acquire() as conn:
        async with conn.transaction():
            incident = await conn.fetchrow(
                "SELECT status FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2 FOR UPDATE",
                row["guild_id"], row["incident_number"],
            )
            if not incident:
                return False
            if incident["status"] != "closed":
                await conn.execute(
                    """UPDATE rescue_incidents
                       SET status='closed', closed_at=COALESCE(closed_at,NOW()),
                           incident_message_id=NULL, discord_channel_missing_at=NOW()
                       WHERE guild_id=$1 AND incident_number=$2""",
                    row["guild_id"], row["incident_number"],
                )
                await conn.execute(
                    """INSERT INTO rescue_incident_events
                       (guild_id,incident_number,channel_id,event_type,actor_id,title,details,created_at)
                       VALUES($1,$2,$3,'discord_channel_missing',NULL,
                              'Discord Channel Missing',
                              'Incident was automatically retired after Discord confirmed the saved incident channel no longer exists.',NOW())""",
                    row["guild_id"], row["incident_number"], row["channel_id"],
                )
            else:
                await conn.execute(
                    """UPDATE rescue_incidents
                       SET incident_message_id=NULL, discord_channel_missing_at=COALESCE(discord_channel_missing_at,NOW())
                       WHERE guild_id=$1 AND incident_number=$2""",
                    row["guild_id"], row["incident_number"],
                )
    logger.warning(
        "Retired RESCUE-%04d from Discord recovery because channel %s no longer exists.",
        row["incident_number"], row["channel_id"],
    )
    return True


async def repair_incident(bot, row):
    guild = bot.get_guild(row["guild_id"])
    if guild is None:
        return False
    channel = guild.get_channel(row["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        try:
            fetched = await guild.fetch_channel(row["channel_id"])
        except discord.NotFound:
            await _retire_missing_channel_incident(bot, row)
            return False
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Recovery could not verify Discord channel %s for RESCUE-%04d; leaving the database unchanged.",
                row["channel_id"], row["incident_number"],
            )
            return False
        channel = fetched if isinstance(fetched, discord.TextChannel) else None
        if channel is None:
            logger.warning(
                "Recovery found channel %s for RESCUE-%04d, but it is not a text channel.",
                row["channel_id"], row["incident_number"],
            )
            return False

    await _repair_card(bot, channel, row)
    if row["status"] == "closed":
        await _repair_closed_channel(channel, row)
    return True


async def recovery_pass(bot):
    if not bot.db_pool:
        return
    async with bot.db_pool.acquire() as conn:
        await conn.execute("ALTER TABLE rescue_incidents ADD COLUMN IF NOT EXISTS incident_message_id BIGINT")
        await conn.execute("ALTER TABLE rescue_incidents ADD COLUMN IF NOT EXISTS discord_channel_missing_at TIMESTAMPTZ")
        rows = await conn.fetch(
            """
            SELECT guild_id,incident_number,channel_id,requester_id,callsign,service,
                   location,situation,priority,status,primary_responder_id,closed_by_id,
                   incident_message_id,closed_at
            FROM rescue_incidents
            WHERE channel_id IS NOT NULL
              AND discord_channel_missing_at IS NULL
              AND (status <> 'closed' OR closed_at >= NOW() - ($1 * INTERVAL '1 hour'))
            ORDER BY created_at DESC
            LIMIT 100
            """,
            RECENT_CLOSED_HOURS,
        )

    touched_guilds = set()
    for row in rows:
        try:
            if await repair_incident(bot, row):
                touched_guilds.add(row["guild_id"])
        except Exception:
            logger.exception("Automatic Discord recovery failed for RESCUE-%04d.", row["incident_number"])

    for guild_id in touched_guilds:
        guild = bot.get_guild(guild_id)
        if guild:
            try:
                await bot.refresh_dispatch_board(guild)
            except Exception:
                logger.exception("Automatic dispatch-board recovery failed for guild %s.", guild_id)


async def recovery_loop(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await recovery_pass(bot)
        except Exception:
            logger.exception("Discord recovery pass failed.")
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)


async def recovery_on_ready():
    bot = core.bot
    task = getattr(bot, "_discord_recovery_task", None)
    if task is None or task.done():
        bot._discord_recovery_task = asyncio.create_task(recovery_loop(bot))
        logger.info("Automatic Discord recovery loop started (%ss interval).", RECOVERY_INTERVAL_SECONDS)


core.bot.add_listener(recovery_on_ready, "on_ready")
