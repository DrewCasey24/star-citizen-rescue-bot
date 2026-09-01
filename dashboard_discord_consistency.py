"""Surface Discord synchronization failures and provide safe state re-sync."""

import logging

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

import dashboard_core as base

log = logging.getLogger(__name__)


async def _attempt(label, operation, failures):
    try:
        result = await operation
        if result is False:
            failures.append(label)
        return result
    except httpx.HTTPError:
        log.exception("Discord synchronization failed: %s", label)
        failures.append(label)
        return None
    except Exception:
        log.exception("Unexpected Discord synchronization failure: %s", label)
        failures.append(label)
        return None


async def _post_channel_message(channel_id, content, role_ids=None):
    payload = {"content": content}
    if role_ids:
        payload["allowed_mentions"] = {
            "parse": [],
            "roles": [str(v) for v in role_ids],
            "users": [],
        }
    await base.discord_post(f"/channels/{channel_id}/messages", payload)
    return True


async def _sync_incident_card(incident, incident_number, actor_id=None, close_controls=False):
    if not incident or not incident["channel_id"]:
        return False
    card = await base.find_incident_card(incident["channel_id"], incident_number)
    if not card or not card.get("embeds"):
        return False

    embed = card["embeds"][0]
    for field in embed.get("fields", []):
        if field.get("name") == "Priority":
            field["value"] = base.PRIORITY_DISCORD.get(incident["priority"], incident["priority"])
        elif field.get("name") == "Status":
            status = base.STATUS_DISCORD.get(incident["status"], incident["status"])
            if actor_id and incident["status"] in {"on_scene", "backup_requested", "closed"}:
                status += f" — <@{actor_id}>"
            field["value"] = status

    if incident["status"] == "closed":
        embed["color"] = 0x2F3136
    elif incident["status"] == "on_scene":
        embed["color"] = 0x57F287
    elif incident["status"] == "backup_requested":
        embed["color"] = 0xFEE75C
    elif incident["priority"] == "critical":
        embed["color"] = 0xED4245
    elif incident["priority"] == "urgent":
        embed["color"] = 0xFAA61A
    else:
        embed["color"] = 0x57F287

    payload = {"embeds": [embed]}
    if close_controls:
        payload["components"] = []
    await base.discord_patch(
        f"/channels/{incident['channel_id']}/messages/{card['id']}",
        payload,
    )
    return True


async def _sync_closed_channel(guild_id, incident):
    channel_id = incident["channel_id"]
    if not channel_id:
        return False

    channel = await base.discord_get(f"/channels/{channel_id}")
    name = channel.get("name", "rescue-incident")
    topic = channel.get("topic") or f"RESCUE-{incident['incident_number']:04d}"
    await base.discord_patch(
        f"/channels/{channel_id}",
        {
            "name": (name if name.startswith("closed-") else f"closed-{name}")[:100],
            "topic": topic if topic.startswith("CLOSED |") else f"CLOSED | {topic}",
        },
    )

    allow_read = str(1024 | 65536)
    deny_send = str(2048)
    targets = [(int(incident["requester_id"]), 1)]
    for role_id in await base.responder_role_ids_for_guild(guild_id):
        targets.append((role_id, 0))
    for overwrite_id, overwrite_type in targets:
        await base.discord_put(
            f"/channels/{channel_id}/permissions/{overwrite_id}",
            {"type": overwrite_type, "allow": allow_read, "deny": deny_send},
        )
    return True


async def _sync_state(guild_id, incident_number, actor_id=None):
    incident, _, _ = await base.load_incident(guild_id, incident_number)
    if not incident:
        return ["incident record"]

    failures = []
    await _attempt(
        "incident card",
        _sync_incident_card(
            incident,
            incident_number,
            actor_id,
            close_controls=(incident["status"] == "closed"),
        ),
        failures,
    )
    if incident["status"] == "closed":
        await _attempt("closed channel permissions", _sync_closed_channel(guild_id, incident), failures)
    await _attempt("dispatch board", base.refresh_dispatch_board_rest(guild_id), failures)
    return failures


async def incident_action_consistent(request: Request, guild_id: int, incident_number: int):
    base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    form = await request.form()
    base.require_csrf(request, form.get("csrf"))
    action = str(form.get("action") or "")
    if action not in {"priority_up", "priority_down", "arrived", "backup", "close"}:
        raise HTTPException(status_code=400, detail="Unknown incident action.")
    actor_id = int(base.current_user(request)["id"])

    async with base.pool.acquire() as conn:
        async with conn.transaction():
            incident = await conn.fetchrow(
                "SELECT incident_number,channel_id,requester_id,priority,status,primary_responder_id,created_at,responded_at,arrived_at,closed_at FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2 FOR UPDATE",
                guild_id,
                incident_number,
            )
            if not incident:
                raise HTTPException(status_code=404, detail="Incident not found.")
            if incident["status"] == "closed":
                raise HTTPException(status_code=409, detail="This incident is already closed.")

            event_type = action
            title = details = ""
            redirect_action = action
            if action in {"priority_up", "priority_down"}:
                current = incident["priority"] if incident["priority"] in base.PRIORITY_ORDER else "standard"
                index = base.PRIORITY_ORDER.index(current)
                new_index = min(len(base.PRIORITY_ORDER) - 1, index + 1) if action == "priority_up" else max(0, index - 1)
                new_priority = base.PRIORITY_ORDER[new_index]
                if new_priority == current:
                    boundary = "highest" if action == "priority_up" else "lowest"
                    raise HTTPException(status_code=409, detail=f"Incident is already at the {boundary} priority.")
                await conn.execute(
                    "UPDATE rescue_incidents SET priority=$3,priority_changed_by=$4,priority_changed_at=NOW() WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                    new_priority,
                    actor_id,
                )
                event_type, title = "priority_changed", "Priority Changed"
                details = f"Priority changed from {base.PRIORITIES.get(current,current)} to {base.PRIORITIES.get(new_priority,new_priority)} from the web dashboard."
            elif action == "arrived":
                await conn.execute(
                    "UPDATE rescue_incidents SET status='on_scene',arrived_at=COALESCE(arrived_at,NOW()) WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                )
                title, details = "Arrived On Scene", "Incident marked on scene from the web dashboard."
            elif action == "backup":
                await conn.execute(
                    "UPDATE rescue_incidents SET status='backup_requested',backup_requested_at=NOW() WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                )
                event_type, title, details = "backup_requested", "Backup Requested", "Additional responder support was requested from the web dashboard."
            else:
                await conn.execute(
                    "UPDATE rescue_incidents SET status='closed',closed_at=NOW(),closed_by_id=$3 WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                    actor_id,
                )
                event_type, title, details, redirect_action = "closed", "Incident Closed", "Incident closed by server management from the web dashboard.", "closed"

            await conn.execute(
                "INSERT INTO rescue_incident_events(guild_id,incident_number,channel_id,event_type,actor_id,title,details,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,NOW())",
                guild_id,
                incident_number,
                incident["channel_id"],
                event_type,
                actor_id,
                title,
                details,
            )

    updated, _, _ = await base.load_incident(guild_id, incident_number)
    incident_id = f"RESCUE-{incident_number:04d}"
    role_ids = await base.responder_role_ids_for_guild(guild_id)
    failures = []
    if updated and updated["channel_id"]:
        channel_id = updated["channel_id"]
        if action in {"priority_up", "priority_down"}:
            await _attempt(
                "incident channel notice",
                _post_channel_message(channel_id, f"⚠️ **WEB DISPATCH:** {incident_id} priority changed to **{base.PRIORITY_DISCORD.get(updated['priority'],updated['priority'])}** by <@{actor_id}>."),
                failures,
            )
            if updated["priority"] == "critical" and role_ids:
                mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
                await _attempt("P1 responder page", _post_channel_message(channel_id, f"🔴 **ALL-SECTOR PRIORITY 1 PAGE:** {mentions}", role_ids), failures)
        elif action == "arrived":
            await _attempt("incident channel notice", _post_channel_message(channel_id, f"📍 **WEB DISPATCH:** {incident_id} marked **On Scene** by <@{actor_id}>."), failures)
        elif action == "backup":
            mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
            text = f"🛡️ **BACKUP REQUESTED VIA WEB DISPATCH:** {mentions}" if mentions else "🛡️ **BACKUP REQUESTED VIA WEB DISPATCH.**"
            await _attempt("backup responder page", _post_channel_message(channel_id, text, role_ids), failures)
        else:
            await _attempt("incident close notice", _post_channel_message(channel_id, f"🔒 {incident_id} closed from the web dashboard by <@{actor_id}>."), failures)

        state_failures = await _sync_state(guild_id, incident_number, actor_id)
        failures.extend(value for value in state_failures if value not in failures)
        if action == "close":
            await _attempt("rescue log", base.post_rescue_log_record(guild_id, incident_number), failures)
    else:
        failures.append("incident channel")
        await _attempt("dispatch board", base.refresh_dispatch_board_rest(guild_id), failures)

    if failures:
        log.warning("Dashboard action committed but Discord sync was incomplete for %s: %s", incident_id, ", ".join(failures))
        return RedirectResponse(
            f"/guild/{guild_id}/incident/{incident_number}?action={redirect_action}&sync=warning",
            status_code=303,
        )
    return RedirectResponse(f"/guild/{guild_id}/incident/{incident_number}?action={redirect_action}", status_code=303)


async def retry_incident_sync(request: Request, guild_id: int, incident_number: int):
    base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    form = await request.form()
    base.require_csrf(request, form.get("csrf"))
    actor_id = int(base.current_user(request)["id"])

    incident, _, _ = await base.load_incident(guild_id, incident_number)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")

    failures = await _sync_state(guild_id, incident_number, actor_id)
    if failures:
        log.warning(
            "Manual Discord re-sync remains incomplete for RESCUE-%04d: %s",
            incident_number,
            ", ".join(failures),
        )
        return RedirectResponse(
            f"/guild/{guild_id}/incident/{incident_number}?sync=warning",
            status_code=303,
        )

    log.info("Manual Discord state re-sync completed for RESCUE-%04d", incident_number)
    return RedirectResponse(
        f"/guild/{guild_id}/incident/{incident_number}?sync=restored",
        status_code=303,
    )


for route in list(base.app.router.routes):
    if getattr(route, "path", None) == "/guild/{guild_id}/incident/{incident_number}/action" and "POST" in getattr(route, "methods", set()):
        base.app.router.routes.remove(route)
        break
base.app.add_api_route(
    "/guild/{guild_id}/incident/{incident_number}/action",
    incident_action_consistent,
    methods=["POST"],
)
base.app.add_api_route(
    "/guild/{guild_id}/incident/{incident_number}/retry-sync",
    retry_incident_sync,
    methods=["POST"],
)

app = base.app
