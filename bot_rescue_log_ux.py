"""Polished completed-incident archive records for Discord rescue logs."""

import logging

import discord

import bot as core

logger = logging.getLogger("star-citizen-rescue-bot.rescue-log-ux")


PRIORITY_FINAL = {
    "critical": "🔴 P1 CRITICAL",
    "urgent": "🟠 P2 URGENT",
    "standard": "🟢 P3 STANDARD",
}


def _duration(start, end):
    if not start or not end:
        return "Not recorded"
    return core.format_duration((end - start).total_seconds())


def _time(value):
    if not value:
        return "Not recorded"
    unix = int(value.timestamp())
    return f"<t:{unix}:f> • <t:{unix}:R>"


async def post_rescue_log_ux(self, guild, incident_channel_id):
    if not self.db_pool:
        return False
    try:
        async with self.db_pool.acquire() as conn:
            log_config = await conn.fetchrow(
                "SELECT channel_id FROM rescue_log_channels WHERE guild_id=$1",
                guild.id,
            )
            if not log_config:
                return False
            incident = await conn.fetchrow(
                """
                SELECT incident_number,channel_id,requester_id,callsign,service,location,
                       situation,priority,primary_responder_id,priority_changed_by,
                       priority_changed_at,created_at,responded_at,arrived_at,closed_at,
                       closed_by_id
                FROM rescue_incidents
                WHERE guild_id=$1 AND channel_id=$2
                """,
                guild.id,
                incident_channel_id,
            )
            responders = await conn.fetch(
                "SELECT user_id FROM rescue_incident_responders WHERE channel_id=$1 ORDER BY joined_at ASC",
                incident_channel_id,
            )
        if not incident:
            return False

        log_channel = guild.get_channel(log_config["channel_id"])
        if not isinstance(log_channel, discord.TextChannel):
            logger.warning("Configured rescue log channel is unavailable in guild %s.", guild.id)
            return False

        incident_id = f"RESCUE-{incident['incident_number']:04d}"
        service = core.SERVICE_NAMES.get(incident["service"], incident["service"])
        priority = PRIORITY_FINAL.get(
            incident["priority"],
            core.PRIORITY_DISPLAY.get(incident["priority"], incident["priority"]),
        )
        primary = f"<@{incident['primary_responder_id']}>" if incident["primary_responder_id"] else "Unassigned"
        closed_by = f"<@{incident['closed_by_id']}>" if incident["closed_by_id"] else "Unknown"
        responder_mentions = [f"<@{row['user_id']}>" for row in responders]
        team = ", ".join(responder_mentions) if responder_mentions else "No supporting responders recorded"

        response_duration = _duration(incident["created_at"], incident["responded_at"])
        arrival_duration = _duration(incident["created_at"], incident["arrived_at"])
        total_duration = _duration(incident["created_at"], incident["closed_at"])

        embed = discord.Embed(
            title=f"📁 {incident_id} • COMPLETED RESCUE RECORD",
            description=(
                f"**{service}** operation archived successfully.\n"
                "This record reflects the final database state at incident closure."
            ),
            color=discord.Color.dark_grey(),
            timestamp=incident["closed_at"] or discord.utils.utcnow(),
        )

        embed.add_field(name="Final Priority", value=priority, inline=True)
        embed.add_field(name="Final Status", value="⚫ CLOSED", inline=True)
        embed.add_field(name="Callsign", value=core.truncate(incident["callsign"], 80), inline=True)

        embed.add_field(name="Requester", value=f"<@{incident['requester_id']}>", inline=True)
        embed.add_field(name="Primary Responder", value=primary, inline=True)
        embed.add_field(name="Closed By", value=closed_by, inline=True)

        embed.add_field(name="📍 Location", value=core.truncate(incident["location"], 250), inline=False)
        embed.add_field(name="📝 Situation", value=core.truncate(incident["situation"], 600), inline=False)

        embed.add_field(
            name="⏱️ Response Metrics",
            value=(
                f"**Claimed:** {response_duration}\n"
                f"**On Scene:** {arrival_duration}\n"
                f"**Total Incident:** {total_duration}"
            ),
            inline=True,
        )
        embed.add_field(
            name=f"👥 Responder Team ({len(responder_mentions)})",
            value=team,
            inline=True,
        )

        timeline = (
            f"**Opened:** {_time(incident['created_at'])}\n"
            f"**Claimed:** {_time(incident['responded_at'])}\n"
            f"**On Scene:** {_time(incident['arrived_at'])}\n"
            f"**Closed:** {_time(incident['closed_at'])}"
        )
        embed.add_field(name="🕒 Mission Timeline", value=timeline, inline=False)

        if incident["priority_changed_by"]:
            changed_by = f"<@{incident['priority_changed_by']}>"
            changed_at = _time(incident["priority_changed_at"])
            embed.add_field(
                name="⚠️ Priority Audit",
                value=f"Final priority change by {changed_by}\n{changed_at}",
                inline=False,
            )

        embed.add_field(
            name="Archive State",
            value="🔒 Completed • Read-only • Preserved in rescue history",
            inline=False,
        )
        embed.set_footer(text=f"Rescue Dispatch Archive • Incident channel ID: {incident_channel_id}")
        await log_channel.send(embed=embed)
        return True
    except discord.Forbidden:
        logger.exception("Missing permission to post rescue log in guild %s.", guild.id)
        return False
    except Exception:
        logger.exception("Failed to post polished rescue log in guild %s.", guild.id)
        return False


core.RescueBot.post_rescue_log = post_rescue_log_ux
