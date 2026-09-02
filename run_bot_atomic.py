"""Final bot entry point for atomic incident operations and creation recovery."""

import logging

import discord

import bot as core
import run_bot as extended
from incident_transitions import join_responder, leave_responder, transition_incident, transition_priority

logger = logging.getLogger("star-citizen-rescue-bot.atomic")


async def update_incident_atomic(self, channel_id, action, user_id=None):
    return await transition_incident(self, channel_id, action, user_id)


async def update_priority_atomic(self, channel_id, priority, user_id):
    return await transition_priority(self, channel_id, priority, user_id)


async def add_responder_atomic(self, channel_id, user_id):
    return await join_responder(self, channel_id, user_id)


async def remove_responder_atomic(self, channel_id, user_id):
    removed, was_primary, _reason = await leave_responder(self, channel_id, user_id)
    return removed, was_primary


async def create_incident_record_atomic(self, **values):
    if not self.db_pool:
        return False, "database_unavailable"
    try:
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO rescue_incidents(guild_id,incident_number,channel_id,requester_id,callsign,service,location,situation,priority,status)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'awaiting_responder')""",
                    values["guild_id"], values["incident_number"], values["channel_id"], values["requester_id"], values["callsign"], values["service"], values["location"], values["situation"], values["priority"],
                )
                await conn.execute(
                    """INSERT INTO rescue_incident_events(guild_id,incident_number,channel_id,event_type,actor_id,title,details,created_at)
                    VALUES($1,$2,$3,'created',$4,'Incident Created',$5,NOW())""",
                    values["guild_id"], values["incident_number"], values["channel_id"], values["requester_id"],
                    f"{core.SERVICE_NAMES.get(values['service'], values['service'])} request opened at {values['location']}.",
                )
        return True, "created"
    except Exception:
        logger.exception("Atomic incident creation failed for channel %s.", values.get("channel_id"))
        return False, "database_error"


async def save_incident_message_id(self, channel_id, message_id):
    if not self.db_pool:
        return False
    try:
        async with self.db_pool.acquire() as conn:
            result = await conn.execute("UPDATE rescue_incidents SET incident_message_id=$2 WHERE channel_id=$1", channel_id, message_id)
        return result == "UPDATE 1"
    except Exception:
        logger.exception("Failed to persist incident card message %s for channel %s.", message_id, channel_id)
        return False


async def delete_incident_after_card_failure(self, channel_id):
    if not self.db_pool:
        return
    try:
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                incident = await conn.fetchrow("SELECT guild_id,incident_number FROM rescue_incidents WHERE channel_id=$1 FOR UPDATE", channel_id)
                if not incident:
                    return
                await conn.execute("DELETE FROM rescue_incident_events WHERE guild_id=$1 AND incident_number=$2", incident["guild_id"], incident["incident_number"])
                await conn.execute("DELETE FROM rescue_incidents WHERE channel_id=$1", channel_id)
    except Exception:
        logger.exception("Failed to roll back incident after card creation failure for channel %s.", channel_id)


class AtomicIncidentControlsView(extended.IncidentControlsViewWithLeave):
    async def _transition_rejected(self, interaction, reason, action):
        messages = {
            "closed": "This incident is already closed.",
            "primary_already_assigned": "Another responder claimed primary responsibility first. Use **Join Response** to assist.",
            "already_primary": "You are already the primary responder for this incident.",
            "no_primary": "A primary responder must claim this incident first.",
            "already_arrived": "This response has already been marked on scene.",
            "database_unavailable": "The rescue database is unavailable; no Discord changes were made.",
            "database_error": "The database could not confirm this action; no Discord changes were made.",
            "not_found": "This incident is no longer present in the rescue database.",
        }
        await interaction.response.send_message(messages.get(reason, f"The {action} action was not accepted. Please refresh and try again."), ephemeral=True)

    async def change_priority(self, interaction, direction):
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        manager = bool(member and member.guild_permissions.manage_guild)
        responder = core.is_responder(member)
        if not responder and not manager:
            return await interaction.response.send_message("Priority controls are limited to responder-sector members and server managers.", ephemeral=True)
        embed = interaction.message.embeds[0].copy()
        current = core.priority_from_embed(embed)
        index = core.PRIORITY_ORDER.index(current)
        new_index = max(0, min(len(core.PRIORITY_ORDER) - 1, index + direction))
        new_priority = core.PRIORITY_ORDER[new_index]
        if new_priority == current:
            boundary = "highest" if direction > 0 else "lowest"
            return await interaction.response.send_message(f"This incident is already at the {boundary} priority level.", ephemeral=True)
        if current == "urgent" and new_priority == "critical":
            primary = core.primary_id_from_embed(embed)
            if interaction.user.id != primary and not manager:
                primary_text = f"<@{primary}>" if primary else "the assigned primary responder"
                return await interaction.response.send_message(f"P1 Critical can only be declared by {primary_text} or someone with Manage Server permission.", ephemeral=True)
        changed, reason = await core.bot.update_priority(interaction.channel.id, new_priority, interaction.user.id)
        if not changed:
            messages = {"closed": "This incident is already closed.", "unchanged": "The incident priority was already changed by another operator.", "database_unavailable": "The rescue database is unavailable; priority was not changed."}
            return await interaction.response.send_message(messages.get(reason, "The priority change could not be recorded. Please refresh and try again."), ephemeral=True)
        self.set_field(embed, "Priority", core.PRIORITY_DISPLAY[new_priority])
        embed.color = discord.Color.red() if new_priority == "critical" else discord.Color.orange() if new_priority == "urgent" else discord.Color.green()
        await interaction.response.edit_message(embed=embed, view=self)
        await core.bot.refresh_dispatch_board(interaction.guild)
        if new_priority == "critical":
            await interaction.followup.send(f"🚨 **P1 CRITICAL DECLARED:** {core.incident_id_from_channel(interaction.channel)} was escalated to **{core.PRIORITY_DISPLAY[new_priority]}** by {interaction.user.mention}.")
            roles = core.all_responder_roles(interaction.guild)
            if roles:
                await interaction.followup.send("🔴 **ALL-SECTOR PRIORITY 1 PAGE:** " + " ".join(role.mention for role in roles), allowed_mentions=discord.AllowedMentions(roles=True))
        else:
            await interaction.followup.send(f"⚠️ {core.incident_id_from_channel(interaction.channel)} priority changed to **{core.PRIORITY_DISPLAY[new_priority]}** by {interaction.user.mention}.")

    @discord.ui.button(label="Respond", style=discord.ButtonStyle.success, emoji="🚀", custom_id="rescue:respond", row=0)
    async def respond(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        if not interaction.message or not interaction.message.embeds or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)
        changed, reason = await core.bot.update_incident(interaction.channel.id, "respond", interaction.user.id)
        if not changed:
            return await self._transition_rejected(interaction, reason, "respond")
        # Primary membership is secondary bookkeeping; the primary assignment above is authoritative.
        joined, join_reason = await core.bot.add_responder(interaction.channel.id, interaction.user.id)
        if not joined and join_reason not in {"already_joined"}:
            logger.warning("Primary %s assigned to channel %s but responder membership returned %s.", interaction.user.id, interaction.channel.id, join_reason)
        embed = interaction.message.embeds[0].copy()
        self.set_field(embed, "Primary Responder", interaction.user.mention)
        self.set_field(embed, "Status", f"🟡 En Route — {interaction.user.mention}")
        await interaction.response.edit_message(embed=embed, view=self)
        await core.bot.refresh_dispatch_board(interaction.guild)
        await interaction.followup.send(f"🚀 {interaction.user.mention} has claimed {core.incident_id_from_channel(interaction.channel)} and is responding.")

    @discord.ui.button(label="Join Response", style=discord.ButtonStyle.secondary, emoji="➕", custom_id="rescue:join", row=0)
    async def join_response(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        changed, reason = await core.bot.add_responder(interaction.channel.id, interaction.user.id)
        if not changed:
            messages = {"already_joined": "You are already listed on this response.", "closed": "This incident is already closed.", "database_unavailable": "The rescue database is unavailable; you were not added to the response."}
            return await interaction.response.send_message(messages.get(reason, "You could not be added to this response. Please try again."), ephemeral=True)
        await interaction.response.send_message(f"➕ {interaction.user.mention} joined the response for {core.incident_id_from_channel(interaction.channel)}.")

    @discord.ui.button(label="Arrived", style=discord.ButtonStyle.primary, emoji="📍", custom_id="rescue:arrived", row=0)
    async def arrived(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        if not interaction.message or not interaction.message.embeds or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)
        changed, reason = await core.bot.update_incident(interaction.channel.id, "arrived", interaction.user.id)
        if not changed:
            return await self._transition_rejected(interaction, reason, "arrived")
        embed = interaction.message.embeds[0].copy()
        self.set_field(embed, "Status", f"🟢 On Scene — {interaction.user.mention}")
        embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=embed, view=self)
        await core.bot.refresh_dispatch_board(interaction.guild)

    @discord.ui.button(label="Close Incident", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="rescue:close", row=0)
    async def close_incident(self, interaction, button):
        if not interaction.message or not interaction.message.embeds or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("I could not close this incident.", ephemeral=True)
        embed = interaction.message.embeds[0].copy()
        requester = core.requester_id_from_embed(embed)
        primary = core.primary_id_from_embed(embed)
        manager = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild
        if interaction.user.id not in {requester, primary} and not manager:
            return await interaction.response.send_message("Only the requester, primary responder, or a server manager can close this incident.", ephemeral=True)
        channel = interaction.channel
        changed, reason = await core.bot.update_incident(channel.id, "close", interaction.user.id)
        if not changed:
            return await self._transition_rejected(interaction, reason, "close")
        self.set_field(embed, "Status", f"⚫ Closed — {interaction.user.mention}")
        embed.color = discord.Color.dark_grey()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await core.bot.refresh_dispatch_board(channel.guild)
        await core.bot.post_rescue_log(channel.guild, channel.id)
        await channel.send(f"🔒 {core.incident_id_from_channel(channel)} closed by {interaction.user.mention}. This incident is now read-only.")
        if requester and (member := channel.guild.get_member(requester)):
            await channel.set_permissions(member, view_channel=True, send_messages=False, read_message_history=True)
        for role in core.all_responder_roles(channel.guild):
            await channel.set_permissions(role, view_channel=True, send_messages=False, read_message_history=True)
        await channel.edit(name=f"closed-{channel.name}"[:100], topic=f"CLOSED | {channel.topic or core.incident_id_from_channel(channel)}")


async def incident_submit_atomic(self, interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Rescue requests must be submitted inside a server.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    config = extended.CONFIG_CACHE.get(guild.id, {})
    category = None
    category_id = config.get("incident_category_id")
    if category_id:
        candidate = guild.get_channel(category_id)
        if isinstance(candidate, discord.CategoryChannel):
            category = candidate
    if category is None:
        category = discord.utils.get(guild.categories, name="Active Incidents") or await guild.create_category("Active Incidents", reason="Star Citizen rescue dispatch setup")
    paged_roles, missing = extended.configured_responder_roles(guild, self.service)
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)}
    for role in extended.configured_all_responder_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
    number = await core.bot.allocate_incident_number(guild.id)
    if number is None:
        return await interaction.followup.send("The rescue database could not allocate an incident number. No incident was created.", ephemeral=True)
    incident_id = f"RESCUE-{number:04d}"
    service = core.SERVICE_NAMES.get(self.service, self.service)
    priority_display = core.PRIORITY_DISPLAY.get(self.priority, core.PRIORITY_DISPLAY["standard"])
    channel = None
    try:
        channel = await guild.create_text_channel(f"rescue-{number:04d}-{core.safe_channel_name(str(self.callsign))}", category=category, overwrites=overwrites, topic=f"{incident_id} | {priority_display} | {service} | Requester: {interaction.user.id}")
        created, _reason = await core.bot.create_incident_record(guild_id=guild.id, incident_number=number, channel_id=channel.id, requester_id=interaction.user.id, callsign=str(self.callsign), service=self.service, location=str(self.location), situation=str(self.situation), priority=self.priority)
        if not created:
            try:
                await channel.delete(reason=f"Rolling back {incident_id}: database creation failed")
            except Exception:
                logger.exception("Could not delete orphan channel %s after DB creation failure.", channel.id)
            return await interaction.followup.send("The rescue database could not create the incident. The new Discord channel was rolled back.", ephemeral=True)
        color = discord.Color.orange() if self.priority == "urgent" else discord.Color.green()
        embed = discord.Embed(title=f"🚨 {incident_id} — ACTIVE RESCUE REQUEST", description="A new Star Citizen rescue incident has been opened.", color=color)
        for name, value, inline in [("Priority", priority_display, True), ("Status", "🔴 Awaiting Responder", True), ("Primary Responder", "Unassigned", True), ("Service", service, True), ("Requester", interaction.user.mention, True), ("Callsign", str(self.callsign), True), ("Location", str(self.location), False), ("Situation", str(self.situation), False)]:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text=f"Requester ID: {interaction.user.id} | Incident: {incident_id}")
        mentions = " ".join(role.mention for role in paged_roles)
        try:
            message = await channel.send(content=f"🚨 **DISPATCH:** {mentions or 'No responder role was found.'}\n{interaction.user.mention} your rescue channel is ready.", embed=embed, view=core.IncidentControlsView(), allowed_mentions=discord.AllowedMentions(roles=True, users=True))
        except Exception:
            logger.exception("Incident card post failed for %s; rolling back.", incident_id)
            await delete_incident_after_card_failure(core.bot, channel.id)
            try:
                await channel.delete(reason=f"Rolling back {incident_id}: incident card creation failed")
            except Exception:
                logger.exception("Could not delete incident channel %s after card failure.", channel.id)
            return await interaction.followup.send("The incident card could not be created, so the incident was rolled back safely. Please try again.", ephemeral=True)
        if not await save_incident_message_id(core.bot, channel.id, message.id):
            logger.error("Incident %s is live but its card message ID was not persisted; dashboard recovery can repair it.", incident_id)
            await channel.send("⚠️ **Dispatch system warning:** the incident is active, but dashboard card synchronization needs repair.")
        if missing:
            await channel.send("⚠️ I could not find: " + ", ".join(f"`{name}`" for name in missing))
        await core.bot.refresh_dispatch_board(guild)
        await interaction.followup.send(f"{incident_id} created: {channel.mention}", ephemeral=True)
    except Exception:
        logger.exception("Unexpected incident creation failure for %s.", incident_id)
        if channel is not None:
            await delete_incident_after_card_failure(core.bot, channel.id)
            try:
                await channel.delete(reason=f"Rolling back {incident_id}: unexpected creation failure")
            except Exception:
                logger.exception("Could not clean up channel %s after unexpected creation failure.", channel.id)
        await interaction.followup.send("The rescue incident could not be completed and partial resources were cleaned up. Please try again.", ephemeral=True)


async def dispatch_board_setup_with_dashboard(interaction):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Run this command in a server text channel.", ephemeral=True)
    if not core.bot.db_pool:
        return await interaction.response.send_message("The dispatch board requires PostgreSQL online.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    message = await interaction.channel.send(embed=await core.bot.build_dispatch_board_embed(interaction.guild), view=extended.dispatch_board_view())
    await core.bot.save_dispatch_board(interaction.guild.id, interaction.channel.id, message.id)
    await interaction.followup.send(f"Live dispatch board created: {message.jump_url}", ephemeral=True)


core.dispatch_board_setup._callback = dispatch_board_setup_with_dashboard
core.RescueBot.create_incident_record = create_incident_record_atomic
core.RescueBot.update_incident = update_incident_atomic
core.RescueBot.update_priority = update_priority_atomic
core.RescueBot.add_responder = add_responder_atomic
core.RescueBot.remove_responder = remove_responder_atomic
core.IncidentControlsView = AtomicIncidentControlsView
core.RescueDetailsModal.on_submit = incident_submit_atomic


if __name__ == "__main__":
    core.main()
