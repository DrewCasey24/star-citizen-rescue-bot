"""Polished request-assistance flow layered over the production bot stack."""

import discord

import bot as core


SERVICE_META = {
    "medical": ("🚑", "Medical Rescue", "Injury, incapacitation, or urgent medical extraction."),
    "search-rescue": ("🔎", "Search & Rescue", "Lost, stranded, missing, or unable to self-recover."),
    "repair-refuel": ("🔧", "Repair / Refuel", "Disabled, damaged, out of fuel, or unable to continue."),
    "security": ("🛡️", "Security / Escort", "Threat protection, escort, or hostile-area support."),
    "recovery-transport": ("🚀", "Recovery / Transport", "Vehicle, cargo, personnel, or transport recovery."),
}


def _step_embed(step, title, description, *, service=None):
    embed = discord.Embed(
        title=f"🚨 Rescue Request • Step {step} of 3",
        description=description,
        color=discord.Color.blurple(),
    )
    if service:
        emoji, label, detail = SERVICE_META.get(service, ("🛰️", core.SERVICE_NAMES.get(service, service), ""))
        embed.add_field(name="Selected Service", value=f"{emoji} **{label}**\n{detail}", inline=False)
    embed.set_footer(text="Star Citizen Rescue Dispatch • Your selections are private until the incident is created")
    return embed


class RequestPrioritySelect(discord.ui.Select):
    def __init__(self, service):
        self.service = service
        options = [
            discord.SelectOption(
                label="P2 — Urgent",
                value="urgent",
                emoji="🟠",
                description="Time-sensitive; prompt response needed.",
            ),
            discord.SelectOption(
                label="P3 — Standard",
                value="standard",
                emoji="🟢",
                description="Routine assistance; no immediate threat.",
            ),
        ]
        super().__init__(
            placeholder="Choose P2 Urgent or P3 Standard…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rescue:request-priority",
        )

    async def callback(self, interaction):
        priority = self.values[0]
        modal = core.RescueDetailsModal(self.service, priority)
        modal.title = f"{core.SERVICE_NAMES.get(self.service, 'Rescue')} • {'P2 Urgent' if priority == 'urgent' else 'P3 Standard'}"
        await interaction.response.send_modal(modal)


class BackToServiceButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="rescue:request-back-service")

    async def callback(self, interaction):
        await interaction.response.edit_message(
            embed=_step_embed(
                1,
                "Select Assistance Type",
                "Choose the service that best matches what you need. The selected service determines which responder sectors are paged first.",
            ),
            view=RequestServiceView(),
            content=None,
        )


class RequestPriorityView(discord.ui.View):
    def __init__(self, service):
        super().__init__(timeout=180)
        self.add_item(RequestPrioritySelect(service))
        self.add_item(BackToServiceButton())


class RequestServiceSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key, emoji=emoji, description=detail[:100])
            for key, (emoji, label, detail) in SERVICE_META.items()
        ]
        super().__init__(
            placeholder="What type of assistance do you need?",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rescue:service-select",
        )

    async def callback(self, interaction):
        service = self.values[0]
        await interaction.response.edit_message(
            embed=_step_embed(
                2,
                "Set Incident Priority",
                "Choose the urgency of your request. **P1 Critical cannot be self-declared**; it is reserved for escalation by the primary responder or server management.",
                service=service,
            ),
            view=RequestPriorityView(service),
            content=None,
        )


class RequestServiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(RequestServiceSelect())


class RequestAssistanceUXView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request Assistance",
        style=discord.ButtonStyle.danger,
        emoji="🚨",
        custom_id="rescue:request",
    )
    async def request_assistance(self, interaction, button):
        await interaction.response.send_message(
            embed=_step_embed(
                1,
                "Select Assistance Type",
                "Choose the service that best matches your situation. You will set priority next, then provide your callsign, location, and incident details.",
            ),
            view=RequestServiceView(),
            ephemeral=True,
        )


# Anything that instantiates the request panel after startup now uses the polished flow.
core.ServiceSelect = RequestServiceSelect
core.PrioritySelect = RequestPrioritySelect
core.RequestAssistanceView = RequestAssistanceUXView
