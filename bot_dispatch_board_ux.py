"""Polished live Discord dispatch board layered over the production bot stack."""

import discord

import bot as core


PRIORITY_BADGE = {
    "critical": "🔴 P1 CRITICAL",
    "urgent": "🟠 P2 URGENT",
    "standard": "🟢 P3 STANDARD",
}

STATUS_BADGE = {
    "awaiting_responder": "🔴 Awaiting Responder",
    "en_route": "🟡 En Route",
    "on_scene": "🟢 On Scene",
    "backup_requested": "🟠 Backup Requested",
}


async def build_dispatch_board_embed_ux(self, guild):
    incidents = await self.active_incidents(guild.id)

    team_counts = {}
    if self.db_pool and incidents:
        channel_ids = [row["channel_id"] for row in incidents if row["channel_id"]]
        if channel_ids:
            try:
                async with self.db_pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT channel_id, COUNT(*) AS responder_count
                        FROM rescue_incident_responders
                        WHERE channel_id = ANY($1::bigint[])
                        GROUP BY channel_id
                        """,
                        channel_ids,
                    )
                team_counts = {row["channel_id"]: int(row["responder_count"]) for row in rows}
            except Exception:
                core.logger.exception("Failed to load responder counts for dispatch board UX.")

    p1 = sum(1 for row in incidents if row["priority"] == "critical")
    p2 = sum(1 for row in incidents if row["priority"] == "urgent")
    p3 = sum(1 for row in incidents if row["priority"] == "standard")
    awaiting = sum(1 for row in incidents if row["status"] == "awaiting_responder")

    if p1:
        color = discord.Color.red()
    elif p2:
        color = discord.Color.orange()
    elif incidents:
        color = discord.Color.green()
    else:
        color = discord.Color.blurple()

    embed = discord.Embed(
        title="📡 STAR CITIZEN RESCUE • LIVE DISPATCH",
        description=(
            "Real-time rescue operations, ordered by priority.\n"
            "Open an incident channel for full details and operational controls."
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(name="Active", value=f"**{len(incidents)}**", inline=True)
    embed.add_field(name="Awaiting", value=f"**{awaiting}**", inline=True)
    embed.add_field(name="Priority Mix", value=f"🔴 {p1}  •  🟠 {p2}  •  🟢 {p3}", inline=True)

    if not incidents:
        embed.add_field(
            name="✅ ALL CLEAR",
            value="No active rescue incidents. Dispatch is standing by.",
            inline=False,
        )
    else:
        for row in incidents:
            incident_id = f"RESCUE-{row['incident_number']:04d}"
            priority = PRIORITY_BADGE.get(row["priority"], core.PRIORITY_DISPLAY.get(row["priority"], row["priority"]))
            status = STATUS_BADGE.get(row["status"], core.STATUS_DISPLAY.get(row["status"], row["status"]))
            service = core.SERVICE_NAMES.get(row["service"], row["service"])
            primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "**Unassigned**"
            created = int(row["created_at"].timestamp())
            team_count = team_counts.get(row["channel_id"], 0)
            team_text = f"{team_count} responder{'s' if team_count != 1 else ''}"

            attention = " ⚠️" if row["status"] == "awaiting_responder" else ""
            embed.add_field(
                name=f"{priority} • {incident_id}{attention}",
                value=(
                    f"**{service}**  •  {status}\n"
                    f"**Callsign:** {core.truncate(row['callsign'], 42)}\n"
                    f"**Location:** {core.truncate(row['location'], 82)}\n"
                    f"**Primary:** {primary}  •  **Team:** {team_text}\n"
                    f"**Opened:** <t:{created}:R>  •  **Channel:** <#{row['channel_id']}>"
                ),
                inline=False,
            )

    embed.set_footer(
        text="P1 incidents sort first • ⚠️ means awaiting primary • Updates automatically"
    )
    return embed


core.RescueBot.build_dispatch_board_embed = build_dispatch_board_embed_ux
