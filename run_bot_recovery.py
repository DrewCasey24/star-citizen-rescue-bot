"""Production entry point adding Discord self-healing to the atomic/CAS bot.

PostgreSQL remains authoritative.  This layer periodically reconciles active
incident cards and dispatch boards so a transient Discord API failure after a
successful database transaction does not leave the visible UI stale forever.
"""

import asyncio
import logging

import discord

import bot as core
import run_bot as extended
import run_bot_cas  # noqa: F401 - installs CAS/atomic patches before recovery

logger = logging.getLogger("star-citizen-rescue-bot.recovery")

_original_setup_database = core.RescueBot.setup_database
_original_setup_hook = core.RescueBot.setup_hook
_original_close = core.RescueBot.close


def _status_text(row):
    primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else None
    status = row["status"]
    if status == "en_route":
        return f"🟡 En Route — {primary}" if primary else "🟡 En Route"
    if status == "on_scene":
        return f"🟢 On Scene — {primary}" if primary else "🟢 On Scene"
    if status == "backup_requested":
        return f"🟠 Backup Requested — {primary}" if primary else "🟠 Backup Requested"
    return "🔴 Awaiting Responder"


def _priority_color(priority):
    if priority == "critical":
        return discord.Color.red()
    if priority == "urgent":
        return discord.Color.orange()
    return discord.Color.green()


def _incident_embed(row):
    incident_id = f"RESCUE-{row['incident_number']:04d}"
    service = core.SERVICE_NAMES.get(row["service"], row["service"])
    priority = core.PRIORITY_DISPLAY.get(row["priority"], core.PRIORITY_DISPLAY["standard"])
    primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned"
    embed = discord.Embed(
        title=f"🚨 {incident_id} — ACTIVE RESCUE REQUEST",
        description="A new Star Citizen rescue incident has been opened.",
        color=_priority_color(row["priority"]),
    )
    for name, value, inline in [
        ("Priority", priority, True),
        ("Status", _status_text(row), True),
        ("Primary Responder", primary, True),
        ("Service", service, True),
        ("Requester", f"<@{row['requester_id']}>", True),
        ("Callsign", row["callsign"], True),
        ("Location", row["location"], False),
        ("Situation", row["situation"], False),
    ]:
        embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text=f"Requester ID: {row['requester_id']} | Incident: {incident_id}")
    return embed


async def setup_database_with_recovery(self):
    await _original_setup_database(self)
    if not self.db_pool:
        return
    async with self.db_pool.acquire() as conn:
        await conn.execute("ALTER TABLE rescue_incidents ADD COLUMN IF NOT EXISTS incident_message_id BIGINT")


async def _save_card_id(bot_instance, channel_id, message_id):
    async with bot_instance.db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE rescue_incidents SET incident_message_id=$2 WHERE channel_id=$1",
            channel_id,
            message_id,
        )


async def reconcile_incident(bot_instance, row):
    guild = bot_instance.get_guild(row["guild_id"])
    if guild is None:
        return False
    channel = guild.get_channel(row["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        logger.warning("Active incident RESCUE-%04d has no accessible Discord channel %s.", row["incident_number"], row["channel_id"])
        return False

    embed = _incident_embed(row)
    view = core.IncidentControlsView()
    message = None
    message_id = row["incident_message_id"]
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            message = None
        except discord.Forbidden:
            logger.warning("Cannot access incident card %s in channel %s.", message_id, channel.id)
            return False
        except discord.HTTPException:
            logger.exception("Discord failed while fetching incident card %s.", message_id)
            return False

    if message is None:
        try:
            message = await channel.send(
                content="♻️ **Dispatch recovery:** incident controls were restored from the rescue database.",
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await _save_card_id(bot_instance, channel.id, message.id)
            logger.warning("Recreated missing incident card for RESCUE-%04d as message %s.", row["incident_number"], message.id)
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Could not recreate incident card for RESCUE-%04d.", row["incident_number"])
            return False

    try:
        # Editing is idempotent: it repairs stale status, primary, priority,
        # content fields, colors, and persistent controls from DB truth.
        await message.edit(embed=embed, view=view)
        return True
    except (discord.Forbidden, discord.HTTPException):
        logger.exception("Could not reconcile incident card for RESCUE-%04d.", row["incident_number"])
        return False


async def reconcile_discord_state(bot_instance):
    if not bot_instance.db_pool or not bot_instance.is_ready():
        return
    async with bot_instance.db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id,incident_number,channel_id,requester_id,callsign,
                   service,location,situation,priority,status,primary_responder_id,
                   incident_message_id
            FROM rescue_incidents
            WHERE status <> 'closed' AND channel_id IS NOT NULL
            ORDER BY guild_id,incident_number
            """
        )
    touched_guilds = set()
    for row in rows:
        if await reconcile_incident(bot_instance, row):
            touched_guilds.add(row["guild_id"])
        await asyncio.sleep(0.15)
    # Board rendering already comes from PostgreSQL; refreshing it after card
    # reconciliation repairs a failed/stale board update as well.
    for guild_id in touched_guilds | {row["guild_id"] for row in rows}:
        guild = bot_instance.get_guild(guild_id)
        if guild:
            await bot_instance.refresh_dispatch_board(guild)


async def recovery_loop(bot_instance):
    await bot_instance.wait_until_ready()
    while not bot_instance.is_closed():
        try:
            await reconcile_discord_state(bot_instance)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Discord recovery pass failed.")
        await asyncio.sleep(60)


async def setup_hook_with_recovery(self):
    await _original_setup_hook(self)
    if self.db_pool and not getattr(self, "_discord_recovery_task", None):
        self._discord_recovery_task = asyncio.create_task(recovery_loop(self))
        logger.info("Discord incident recovery loop enabled (60 second interval).")


async def close_with_recovery(self):
    task = getattr(self, "_discord_recovery_task", None)
    if task:
        task.cancel()
    await _original_close(self)


core.RescueBot.setup_database = setup_database_with_recovery
core.RescueBot.setup_hook = setup_hook_with_recovery
core.RescueBot.close = close_with_recovery


if __name__ == "__main__":
    core.main()
