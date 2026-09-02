"""Repair saved Discord configuration references that no longer exist."""

import re
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import dashboard_core as base


@base.app.get("/guild/{guild_id}/repair-config", response_class=HTMLResponse)
async def repair_config_confirmation(request: Request, guild_id: int):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    csrf = base.esc(request.session.get("csrf"))
    body = f'''<div class="overview-head"><div><h2>Repair Discord Configuration</h2><div class="muted">Clean stale saved references for {base.esc(guild_info['name'])}.</div></div></div>
<div class="card"><h2>Safe configuration cleanup</h2><p>This checks the server's saved channels, category, responder roles, service-paging roles, and live dispatch-board message against Discord. References to resources that no longer exist are removed. Valid configuration is left unchanged.</p><p class="muted">The repair does not delete Discord channels, roles, or messages. After cleanup, return to Settings to select replacements for anything that was removed.</p><form method="post" action="/guild/{guild_id}/repair-config"><input type="hidden" name="csrf" value="{csrf}"><button class="btn warn" type="submit">Repair Stale References</button> <a class="btn secondary" href="/guild/{guild_id}/health">Cancel</a></form></div>'''
    return base.page(f"Repair Configuration · {guild_info['name']}", body, base.current_user(request))


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

    valid_roles = {int(r["id"]) for r in roles if r.get("id") and r.get("name") != "@everyone" and not r.get("managed")}
    valid_text = {int(c["id"]) for c in channels if c.get("id") and c.get("type") == 0}
    valid_categories = {int(c["id"]) for c in channels if c.get("id") and c.get("type") == 4}

    # Verify the saved dispatch message outside the DB transaction. Only a 404 is
    # treated as stale; permission/network failures should not destroy a valid config.
    async with base.pool.acquire() as conn:
        dispatch_snapshot = await conn.fetchrow(
            "SELECT channel_id,message_id FROM rescue_dispatch_boards WHERE guild_id=$1",
            guild_id,
        )
    dispatch_message_missing = False
    if dispatch_snapshot and int(dispatch_snapshot["channel_id"]) in valid_text:
        try:
            await base.discord_get(f"/channels/{dispatch_snapshot['channel_id']}/messages/{dispatch_snapshot['message_id']}")
        except httpx.HTTPStatusError as exc:
            dispatch_message_missing = exc.response.status_code == 404
        except Exception:
            dispatch_message_missing = False

    repaired = 0
    async with base.pool.acquire() as conn:
        async with conn.transaction():
            settings = await conn.fetchrow(
                "SELECT responder_role_ids,request_channel_id,incident_category_id FROM rescue_guild_settings WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            if settings:
                old_roles = list(settings["responder_role_ids"] or [])
                new_roles = [rid for rid in old_roles if int(rid) in valid_roles]
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

            for row in await conn.fetch(
                "SELECT service,role_ids FROM rescue_service_role_settings WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            ):
                old_roles = list(row["role_ids"] or [])
                new_roles = [rid for rid in old_roles if int(rid) in valid_roles]
                if new_roles != old_roles:
                    repaired += len(old_roles) - len(new_roles)
                    await conn.execute(
                        "UPDATE rescue_service_role_settings SET role_ids=$3,updated_at=NOW() WHERE guild_id=$1 AND service=$2",
                        guild_id, row["service"], new_roles,
                    )

            dispatch = await conn.fetchrow(
                "SELECT channel_id,message_id FROM rescue_dispatch_boards WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            if dispatch and (
                int(dispatch["channel_id"]) not in valid_text
                or (
                    dispatch_message_missing
                    and dispatch_snapshot
                    and int(dispatch["channel_id"]) == int(dispatch_snapshot["channel_id"])
                    and int(dispatch["message_id"]) == int(dispatch_snapshot["message_id"])
                )
            ):
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


_previous_page = base.page


def page_with_repair_shortcut(title, body, user=None):
    if title.startswith("System Health ·") and "Repair Configuration" not in body:
        match = re.search(r'href="(/guild/\d+/health)"', body)
        if match:
            root = match.group(1).rsplit('/health', 1)[0]
            marker = '</div></div>\n<div class="health-grid">'
            body = body.replace(
                marker,
                f'<a class="btn secondary" href="{root}/repair-config">Repair Configuration</a></div></div>\n<div class="health-grid">',
                1,
            )
    return _previous_page(title, body, user)


base.page = page_with_repair_shortcut
app = base.app
