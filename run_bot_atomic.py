"""Final bot entry point that hardens priority and responder transitions.

Importing run_bot installs the existing dashboard/config extensions without
starting the process. This layer then replaces the remaining non-atomic
priority and responder mutations before core.main() starts Discord.
"""

import discord

import bot as core
import run_bot as extended
from incident_transitions import join_responder, leave_responder, transition_priority


async def update_priority_atomic(self, channel_id, priority, user_id):
    return await transition_priority(self, channel_id, priority, user_id)


async def add_responder_atomic(self, channel_id, user_id):
    return await join_responder(self, channel_id, user_id)


async def remove_responder_atomic(self, channel_id, user_id):
    removed, was_primary, _reason = await leave_responder(self, channel_id, user_id)
    return removed, was_primary


class AtomicIncidentControlsView(extended.IncidentControlsViewWithLeave):
    async def change_priority(self, interaction, direction):
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("I could not find the incident card.", ephemeral=True)

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        manager = bool(member and member.guild_permissions.manage_guild)
        responder = core.is_responder(member)
        if not responder and not manager:
            return await interaction.response.send_message(
                "Priority controls are limited to responder-sector members and server managers.", ephemeral=True
            )

        embed = interaction.message.embeds[0].copy()
        current = core.priority_from_embed(embed)
        index = core.PRIORITY_ORDER.index(current)
        new_index = max(0, min(len(core.PRIORITY_ORDER) - 1, index + direction))
        new_priority = core.PRIORITY_ORDER[new_index]
        if new_priority == current:
            boundary = "highest" if direction > 0 else "lowest"
            return await interaction.response.send_message(
                f"This incident is already at the {boundary} priority level.", ephemeral=True
            )

        if current == "urgent" and new_priority == "critical":
            primary = core.primary_id_from_embed(embed)
            if interaction.user.id != primary and not manager:
                primary_text = f"<@{primary}>" if primary else "the assigned primary responder"
                return await interaction.response.send_message(
                    f"P1 Critical can only be declared by {primary_text} or someone with Manage Server permission.",
                    ephemeral=True,
                )

        changed, reason = await core.bot.update_priority(interaction.channel.id, new_priority, interaction.user.id)
        if not changed:
            messages = {
                "closed": "This incident is already closed.",
                "unchanged": "The incident priority was already changed by another operator.",
                "database_unavailable": "The rescue database is unavailable; priority was not changed.",
            }
            return await interaction.response.send_message(
                messages.get(reason, "The priority change could not be recorded. Please refresh and try again."),
                ephemeral=True,
            )

        self.set_field(embed, "Priority", core.PRIORITY_DISPLAY[new_priority])
        if new_priority == "critical":
            embed.color = discord.Color.red()
        elif new_priority == "urgent":
            embed.color = discord.Color.orange()
        else:
            embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=embed, view=self)
        await core.bot.refresh_dispatch_board(interaction.guild)

        if new_priority == "critical":
            await interaction.followup.send(
                f"🚨 **P1 CRITICAL DECLARED:** {core.incident_id_from_channel(interaction.channel)} was escalated to **{core.PRIORITY_DISPLAY[new_priority]}** by {interaction.user.mention}."
            )
            roles = core.all_responder_roles(interaction.guild)
            if roles:
                await interaction.followup.send(
                    "🔴 **ALL-SECTOR PRIORITY 1 PAGE:** " + " ".join(role.mention for role in roles),
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
        else:
            await interaction.followup.send(
                f"⚠️ {core.incident_id_from_channel(interaction.channel)} priority changed to **{core.PRIORITY_DISPLAY[new_priority]}** by {interaction.user.mention}."
            )

    @discord.ui.button(label="Join Response", style=discord.ButtonStyle.secondary, emoji="➕", custom_id="rescue:join", row=0)
    async def join_response(self, interaction, button):
        if not await self.require_responder(interaction):
            return
        changed, reason = await core.bot.add_responder(interaction.channel.id, interaction.user.id)
        if not changed:
            messages = {
                "already_joined": "You are already listed on this response.",
                "closed": "This incident is already closed.",
                "database_unavailable": "The rescue database is unavailable; you were not added to the response.",
            }
            return await interaction.response.send_message(
                messages.get(reason, "You could not be added to this response. Please try again."), ephemeral=True
            )
        await interaction.response.send_message(
            f"➕ {interaction.user.mention} joined the response for {core.incident_id_from_channel(interaction.channel)}."
        )


core.RescueBot.update_priority = update_priority_atomic
core.RescueBot.add_responder = add_responder_atomic
core.RescueBot.remove_responder = remove_responder_atomic
core.IncidentControlsView = AtomicIncidentControlsView


if __name__ == "__main__":
    core.main()
