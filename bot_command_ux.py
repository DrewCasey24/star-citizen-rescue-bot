"""Polished Discord command responses and persistent rescue request panel."""

import discord

import bot as core


def _system_color():
    if not core.bot.db_pool or core.bot.db_error:
        return discord.Color.orange()
    return discord.Color.green()


def _status_badge(ok, success="ONLINE", failure="DEGRADED"):
    return f"🟢 {success}" if ok else f"🟠 {failure}"


async def ping_ux(interaction):
    latency_ms = round(core.bot.latency * 1000) if core.bot.latency >= 0 else 0
    embed = discord.Embed(
        title="🛰️ Rescue Dispatch • Online",
        description="Command link to Star Citizen Rescue Dispatch is operational.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Discord Gateway", value="🟢 Connected", inline=True)
    embed.add_field(name="Latency", value=f"{latency_ms} ms", inline=True)
    embed.add_field(name="Database", value=_status_badge(bool(core.bot.db_pool), "Connected", "Unavailable"), inline=True)
    embed.set_footer(text="Star Citizen Rescue Dispatch • System check")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def rescue_status_ux(interaction):
    db_online = bool(core.bot.db_pool)
    database = "🟢 Connected" if db_online else ("🔴 Connection Error" if core.bot.db_error else "🟠 Not Configured")
    embed = discord.Embed(
        title="📡 Rescue Dispatch • System Status",
        description="Current operational state of the rescue, paging, persistence, and dispatch systems.",
        color=_system_color(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Discord", value="🟢 Online", inline=True)
    embed.add_field(name="Incident Control", value="🟢 Online", inline=True)
    embed.add_field(name="Sector Paging", value="🟢 Online", inline=True)
    embed.add_field(name="Priority Controls", value="🟢 Safeguarded", inline=True)
    embed.add_field(name="Recovery Loop", value="🟢 Active", inline=True)
    embed.add_field(name="Database", value=database, inline=True)
    embed.add_field(
        name="Database-backed Features",
        value="🟢 History, statistics, dispatch persistence, and incident recovery available." if db_online else "🟠 History, statistics, persistence, and recovery require PostgreSQL.",
        inline=False,
    )
    embed.set_footer(text="Star Citizen Rescue Dispatch • Operational status")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def rescue_history_ux(interaction, limit=5):
    if interaction.guild is None:
        return await interaction.response.send_message("This command must be used inside a server.", ephemeral=True)
    if not core.bot.db_pool:
        return await interaction.response.send_message("Rescue history requires PostgreSQL to be online.", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)
    incidents = await core.bot.recent_closed_incidents(interaction.guild.id, limit)
    embed = discord.Embed(
        title="📚 Rescue Dispatch • Completed Incidents",
        description=f"Most recent completed rescue operations for **{interaction.guild.name}**.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not incidents:
        embed.add_field(name="✅ No Archived Incidents", value="No completed rescue incidents are currently recorded.", inline=False)
    else:
        for row in incidents:
            incident_id = f"RESCUE-{row['incident_number']:04d}"
            priority = core.PRIORITY_DISPLAY.get(row["priority"], core.PRIORITY_DISPLAY["standard"])
            service = core.SERVICE_NAMES.get(row["service"], row["service"])
            primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned"
            claim_seconds = (row["responded_at"] - row["created_at"]).total_seconds() if row["responded_at"] else None
            total_seconds = (row["closed_at"] - row["created_at"]).total_seconds() if row["closed_at"] else None
            closed_time = int(row["closed_at"].timestamp()) if row["closed_at"] else None
            closed_text = f"<t:{closed_time}:R>" if closed_time else "Unknown"
            channel_text = f"<#{row['channel_id']}>" if row["channel_id"] else "Archived"
            embed.add_field(
                name=f"{priority} • {incident_id}",
                value=(
                    f"**{service}** • `{core.truncate(row['callsign'], 40)}`\n"
                    f"📍 {core.truncate(row['location'], 80)}\n"
                    f"👤 **Primary:** {primary} • ⏱️ **Claim:** {core.format_duration(claim_seconds)}\n"
                    f"🕒 **Duration:** {core.format_duration(total_seconds)} • **Closed:** {closed_text}\n"
                    f"📁 **Record:** {channel_text}"
                ),
                inline=False,
            )

    embed.set_footer(text=f"Showing up to {limit} completed incidents • Newest first")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def rescue_stats_ux(interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("This command must be used inside a server.", ephemeral=True)
    if not core.bot.db_pool:
        return await interaction.response.send_message("Rescue statistics require PostgreSQL to be online.", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await core.bot.rescue_statistics(interaction.guild.id)
    if not result:
        return await interaction.followup.send("I could not calculate rescue statistics right now.", ephemeral=True)

    summary, services, responders = result
    embed = discord.Embed(
        title="📊 Rescue Dispatch • Operations Snapshot",
        description=f"Database-backed operational summary for **{interaction.guild.name}**.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="All Incidents", value=f"**{summary['total']}**", inline=True)
    embed.add_field(name="Active", value=f"🟡 **{summary['active']}**", inline=True)
    embed.add_field(name="Completed", value=f"✅ **{summary['closed']}**", inline=True)
    embed.add_field(name="🔴 P1 Critical", value=str(summary["p1"]), inline=True)
    embed.add_field(name="🟠 P2 Urgent", value=str(summary["p2"]), inline=True)
    embed.add_field(name="🟢 P3 Standard", value=str(summary["p3"]), inline=True)
    embed.add_field(name="Avg. Claim", value=core.format_duration(summary["avg_response_seconds"]), inline=True)
    embed.add_field(name="Avg. On Scene", value=core.format_duration(summary["avg_arrival_seconds"]), inline=True)
    embed.add_field(name="Avg. Total", value=core.format_duration(summary["avg_close_seconds"]), inline=True)

    service_lines = [f"• **{core.SERVICE_NAMES.get(row['service'], row['service'])}:** {row['count']}" for row in services]
    embed.add_field(name="Service Activity", value="\n".join(service_lines) if service_lines else "No incident data yet.", inline=False)
    responder_lines = [f"• <@{row['user_id']}> — **{row['count']}** primary response(s)" for row in responders]
    embed.add_field(name="Top Primary Responders", value="\n".join(responder_lines) if responder_lines else "No primary responders recorded yet.", inline=False)
    embed.set_footer(text="Averages exclude incidents without the corresponding timestamp")
    await interaction.followup.send(embed=embed, ephemeral=True)


def request_panel_embed():
    embed = discord.Embed(
        title="🚨 STAR CITIZEN RESCUE DISPATCH",
        description=(
            "**Need assistance in the 'verse?**\n"
            "Open a private request workflow below. Select the service, choose P2/P3 priority, then provide your callsign, location, and situation."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(
        name="Available Response Services",
        value=(
            "🚑 **Medical Rescue** — injury or medical extraction\n"
            "🔎 **Search & Rescue** — lost, stranded, or missing\n"
            "🔧 **Repair / Refuel** — disabled, damaged, or out of fuel\n"
            "🛡️ **Security / Escort** — hostile-area support or protection\n"
            "🚀 **Recovery / Transport** — personnel, vehicle, cargo, or transport recovery"
        ),
        inline=False,
    )
    embed.add_field(
        name="Priority Guide",
        value=(
            "🔴 **P1 Critical** — responder/management escalation only\n"
            "🟠 **P2 Urgent** — time-sensitive; prompt response needed\n"
            "🟢 **P3 Standard** — routine assistance; no immediate threat"
        ),
        inline=False,
    )
    embed.add_field(
        name="What Happens Next",
        value="Your incident gets a dedicated response channel, service-specific paging, live dispatch-board tracking, and responder controls.",
        inline=False,
    )
    embed.set_footer(text="Star Citizen Rescue Dispatch • Press Request Assistance to begin")
    return embed


async def rescue_setup_ux(interaction):
    await interaction.response.defer(ephemeral=True)
    message = await interaction.channel.send(embed=request_panel_embed(), view=core.RequestAssistanceView())
    await interaction.followup.send(f"✅ Rescue request panel posted: {message.jump_url}", ephemeral=True)


def _patch_command(name, callback):
    command = core.bot.tree.get_command(name)
    if command is not None:
        command._callback = callback


_patch_command("ping", ping_ux)
_patch_command("rescue-status", rescue_status_ux)
_patch_command("rescue-history", rescue_history_ux)
_patch_command("rescue-stats", rescue_stats_ux)
_patch_command("rescue-setup", rescue_setup_ux)
