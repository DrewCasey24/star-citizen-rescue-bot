"""Discord incident-card UX polish layered over the atomic/recovery stack."""

import discord

import bot as core
import run_bot_atomic as atomic
import bot_discord_recovery as recovery


PRIORITY_SHORT = {
    "critical": "🔴 P1 CRITICAL",
    "urgent": "🟠 P2 URGENT",
    "standard": "🟢 P3 STANDARD",
}


class IncidentUXView(atomic.AtomicIncidentControlsView):
    """Same atomic callbacks, with clearer labels/rows and DB-state button disabling."""

    def __init__(self, *, status=None, priority=None, primary_id=None, arrived_at=None, backup_requested_at=None):
        super().__init__()
        closed = status == "closed"
        has_primary = primary_id is not None
        arrived = bool(arrived_at) or status == "on_scene"
        backup_requested = bool(backup_requested_at) or status == "backup_requested"

        for item in self.children:
            cid = getattr(item, "custom_id", None)
            if cid == "rescue:respond":
                item.label = "Take Primary"
                item.disabled = closed or has_primary
            elif cid == "rescue:join":
                item.label = "Join Team"
                item.disabled = closed
            elif cid == "rescue:arrived":
                item.label = "Mark On Scene"
                item.disabled = closed or not has_primary or arrived
            elif cid == "rescue:backup":
                item.label = "Request Backup"
                item.disabled = closed or not has_primary or backup_requested
            elif cid == "rescue:close":
                item.label = "Close Incident"
                item.disabled = closed
            elif cid == "rescue:leave":
                item.label = "Leave Team"
                item.disabled = closed
            elif cid == "rescue:priority-up":
                item.label = "Raise Priority"
                item.disabled = closed or priority == "critical"
            elif cid == "rescue:priority-down":
                item.label = "Lower Priority"
                item.disabled = closed or priority == "standard"


def incident_view_for_row(row):
    return IncidentUXView(
        status=row["status"],
        priority=row["priority"],
        primary_id=row["primary_responder_id"],
        arrived_at=row.get("arrived_at") if hasattr(row, "get") else row["arrived_at"],
        backup_requested_at=row.get("backup_requested_at") if hasattr(row, "get") else row["backup_requested_at"],
    )


def _set_or_add(embed, name, value, inline):
    for index, field in enumerate(embed.fields):
        if field.name == name:
            embed.set_field_at(index, name=name, value=value, inline=inline)
            return
    embed.add_field(name=name, value=value, inline=inline)


def _status_heading(row):
    status = row["status"]
    return {
        "awaiting_responder": "AWAITING RESPONDER",
        "en_route": "RESPONDER EN ROUTE",
        "on_scene": "TEAM ON SCENE",
        "backup_requested": "BACKUP REQUESTED",
        "closed": "INCIDENT CLOSED",
    }.get(status, status.replace("_", " ").upper())


def build_incident_embed(row, responder_ids=()):
    incident_id = f"RESCUE-{row['incident_number']:04d}"
    service = core.SERVICE_NAMES.get(row["service"], row["service"])
    priority = PRIORITY_SHORT.get(row["priority"], core.PRIORITY_DISPLAY.get(row["priority"], row["priority"]))
    closed = row["status"] == "closed"
    title_icon = "🔒" if closed else "🚨"
    embed = discord.Embed(
        title=f"{title_icon} {incident_id} • {_status_heading(row)}",
        description=f"**{service}** rescue operation\nUse the controls below to update this incident. Database state is authoritative.",
        color=recovery._desired_color(row),
    )
    embed.add_field(name="Priority", value=priority, inline=True)
    embed.add_field(name="Status", value=recovery._status_text(row), inline=True)
    primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned"
    embed.add_field(name="Primary Responder", value=primary, inline=True)
    embed.add_field(name="Callsign", value=str(row["callsign"]), inline=True)
    embed.add_field(name="Requester", value=f"<@{row['requester_id']}>", inline=True)
    embed.add_field(name="Service", value=service, inline=True)
    team = ", ".join(f"<@{user_id}>" for user_id in responder_ids) if responder_ids else "No responders assigned"
    embed.add_field(name="Responder Team", value=team, inline=False)
    embed.add_field(name="Location", value=str(row["location"]), inline=False)
    embed.add_field(name="Situation", value=str(row["situation"]), inline=False)
    if closed:
        embed.add_field(name="Archive State", value="🔒 Closed and read-only", inline=False)
    embed.set_footer(text=f"Requester ID: {row['requester_id']} | Incident: {incident_id} | Rescue Dispatch")
    return embed


async def _responder_ids(bot, channel_id):
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM rescue_incident_responders WHERE channel_id=$1 ORDER BY joined_at ASC",
            channel_id,
        )
    return [row["user_id"] for row in rows]


async def find_or_recreate_card_ux(bot, channel, row):
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
        responders = await _responder_ids(bot, channel.id)
        message = await channel.send(
            embed=build_incident_embed(row, responders),
            view=incident_view_for_row(row),
        )
        recovery.logger.warning("Recreated missing incident card for RESCUE-%04d in channel %s.", row["incident_number"], channel.id)

    if message.id != message_id:
        async with bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE rescue_incidents SET incident_message_id=$2 WHERE channel_id=$1", channel.id, message.id)
    return message


async def repair_card_ux(bot, channel, row):
    message = await find_or_recreate_card_ux(bot, channel, row)
    if message is None:
        return False
    responders = await _responder_ids(bot, channel.id)
    desired = build_incident_embed(row, responders)
    await message.edit(embed=desired, view=incident_view_for_row(row))
    return True


# New incidents instantiate this class through core.IncidentControlsView.
# Existing incidents are restyled by the recovery loop on its next pass.
core.IncidentControlsView = IncidentUXView
recovery._find_or_recreate_card = find_or_recreate_card_ux
recovery._repair_card = repair_card_ux
