"""Final bot entry point adding compare-and-set priority protection."""

import discord
import bot as core
import run_bot_atomic as atomic
from incident_transitions import transition_priority


async def update_priority_cas(self, channel_id, priority, user_id, expected_priority=None):
    return await transition_priority(self, channel_id, priority, user_id, expected_priority)


async def change_priority_cas(self, interaction, direction):
    if not interaction.message or not interaction.message.embeds:
        return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    manager = bool(member and member.guild_permissions.manage_guild)
    if not core.is_responder(member) and not manager:
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

    changed, reason = await core.bot.update_priority(interaction.channel.id, new_priority, interaction.user.id, expected_priority=current)
    if not changed:
        messages = {
            "closed": "This incident is already closed.",
            "unchanged": "The incident priority was already changed by another operator.",
            "stale_priority": "The priority changed since this card was loaded. Refresh the incident card and try again.",
            "database_unavailable": "The rescue database is unavailable; priority was not changed.",
            "database_error": "The database could not confirm the priority change; Discord was left unchanged.",
        }
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


core.RescueBot.update_priority = update_priority_cas
atomic.AtomicIncidentControlsView.change_priority = change_priority_cas
core.IncidentControlsView.change_priority = change_priority_cas

if __name__ == "__main__":
    core.main()
