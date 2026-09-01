"""Repair saved Discord configuration references that no longer exist."""

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

import dashboard_core as base


@base.app.post("/guild/{guild_id}/repair-config")
async def repair_stale_config(request: Request, guild_id: int):
    base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    form = await request.form()
    base.require_csrf(request, form.get("csrf"))

    try:
        roles = await base.discord_get(f"/guilds/{guild_id}/roles")
        channels = await base.discord_get(f"/guilds/{guild_id}/channels")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Discord API error: {exc.response.status_code}")

    valid_roles = {
        int(role["id"])
        for role in roles
        if role.get("id") and role.get("name") != "@everyone" and not role.get("managed")
    }
    valid_text = {int(channel["id"]) for channel in channels if channel.get("id") and channel.get("type") == 0}
    valid_categories = {int(channel["id"]) for channel in channels if channel.get("id") and channel.get("type") == 4}

    repaired = 0
    async with base.pool.acquire() as conn:
        async with conn.transaction():
            settings = await conn.fetchrow(
                "SELECT responder_role_ids,request_channel_id,incident_category_id FROM rescue_guild_settings WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            if settings:
                old_roles = list(settings["responder_role_ids"] or [])
                new_roles = [role_id for role_id in old_roles if int(role_id) in valid_roles]
                request_channel = settings["request_channel_id"]
                category = settings["incident_category_id"]
                new_request = request_channel if request_channel and int(request_channel) in valid_text else None
                new_category = category if category and int(category) in valid_categories else None
                repaired += len(old_roles) - len(new_roles)
                repaired += int(bool(request_channel and new_request is None))
                repaired += int(bool(category and new_category is None))
                await conn.execute(
                    "UPDATE rescue_guild_settings SET responder_role_ids=$2,request_channel_id=$3,incident_category_id=$4,updated_at=NOW() WHERE guild_id=$1",
                    guild_id, new_roles, new_request, new_category,
                )

            service_rows = await conn.fetch(
                "SELECT service,role_ids FROM rescue_service_role_settings WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            for row in service_rows:
                old_roles = list(row["role_ids"] or [])
                new_roles = [role_id for role_id in old_roles if int(role_id) in valid_roles]
                if new_roles != old_roles:
                    repaired += len(old_roles) - len(new_roles)
                    await conn.execute(
                        "UPDATE rescue_service_role_settings SET role_ids=$3,updated_at=NOW() WHERE guild_id=$1 AND service=$2",
                        guild_id, row["service"], new_roles,
                    )

            dispatch = await conn.fetchrow(
                "SELECT channel_id FROM rescue_dispatch_boards WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            if dispatch and int(dispatch["channel_id"]) not in valid_text:
                repaired += 1
                await conn.execute("DELETE FROM rescue_dispatch_boards WHERE guild_id=$1", guild_id)

            log_channel = await conn.fetchval(
                "SELECT channel_id FROM rescue_log_channels WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            if log_channel and int(log_channel) not in valid_text:
                repaired += 1
                await conn.execute("DELETE FROM rescue_log_channels WHERE guild_id=$1", guild_id)

    return RedirectResponse(
        f"/guild/{guild_id}/health?repair={'done' if repaired else 'clean'}&count={repaired}",
        status_code=303,
    )


app = base.app
