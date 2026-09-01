"""Persist Discord incident-card message IDs so web actions avoid repeated channel scans."""

import httpx

import dashboard_core as base


_original_find_incident_card = base.find_incident_card


@base.app.on_event("startup")
async def ensure_incident_message_id_column():
    async with base.pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE rescue_incidents ADD COLUMN IF NOT EXISTS incident_message_id BIGINT"
        )


async def find_incident_card_persisted(channel_id, incident_number):
    """Fetch the known card directly; discover it once for older incidents and remember it."""
    message_id = None
    async with base.pool.acquire() as conn:
        message_id = await conn.fetchval(
            "SELECT incident_message_id FROM rescue_incidents WHERE channel_id=$1 AND incident_number=$2",
            channel_id,
            incident_number,
        )

    if message_id:
        try:
            return await base.discord_get(f"/channels/{channel_id}/messages/{message_id}")
        except httpx.HTTPStatusError as exc:
            # A deleted/replaced card can self-heal through one discovery scan.
            if exc.response.status_code not in (403, 404):
                return None

    card = await _original_find_incident_card(channel_id, incident_number)
    if not card or not card.get("id"):
        return card

    try:
        card_id = int(card["id"])
    except (TypeError, ValueError):
        return card

    async with base.pool.acquire() as conn:
        await conn.execute(
            "UPDATE rescue_incidents SET incident_message_id=$3 WHERE channel_id=$1 AND incident_number=$2",
            channel_id,
            incident_number,
            card_id,
        )
    return card


base.find_incident_card = find_incident_card_persisted

app = base.app
