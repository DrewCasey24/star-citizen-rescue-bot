"""Surface Discord synchronization failures after successful dashboard actions."""

import logging

import httpx
from fastapi import Request
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


async def incident_action_consistent(request: Request, guild_id: int, incident_number: int):
    base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    form = await request.form()
    base.require_csrf(request, form.get("csrf"))
    action = str(form.get("action") or "")
    if action not in {"priority_up", "priority_down", "arrived", "backup", "close"}:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Unknown incident action.")
    actor_id = int(base.current_user(request)["id"])

    async with base.pool.acquire() as conn:
        async with conn.transaction():
            incident = await conn.fetchrow(
                "SELECT incident_number,channel_id,requester_id,priority,status,primary_responder_id,created_at,responded_at,arrived_at,closed_at FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2 FOR UPDATE",
                guild_id, incident_number,
            )
            from fastapi import HTTPException
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
                new_index = min(len(base.PRIORITY_ORDER)-1, index+1) if action == "priority_up" else max(0, index-1)
                new_priority = base.PRIORITY_ORDER[new_index]
                if new_priority == current:
                    boundary = "highest" if action == "priority_up" else "lowest"
                    raise HTTPException(status_code=409, detail=f"Incident is already at the {boundary} priority.")
                await conn.execute("UPDATE rescue_incidents SET priority=$3,priority_changed_by=$4,priority_changed_at=NOW() WHERE guild_id=$1 AND incident_number=$2", guild_id, incident_number, new_priority, actor_id)
                event_type, title = "priority_changed", "Priority Changed"
                details = f"Priority changed from {base.PRIORITIES.get(current,current)} to {base.PRIORITIES.get(new_priority,new_priority)} from the web dashboard."
            elif action == "arrived":
                await conn.execute("UPDATE rescue_incidents SET status='on_scene',arrived_at=COALESCE(arrived_at,NOW()) WHERE guild_id=$1 AND incident_number=$2", guild_id, incident_number)
                title, details = "Arrived On Scene", "Incident marked on scene from the web dashboard."
            elif action == "backup":
                await conn.execute("UPDATE rescue_incidents SET status='backup_requested',backup_requested_at=NOW() WHERE guild_id=$1 AND incident_number=$2", guild_id, incident_number)
                event_type, title, details = "backup_requested", "Backup Requested", "Additional responder support was requested from the web dashboard."
            else:
                await conn.execute("UPDATE rescue_incidents SET status='closed',closed_at=NOW(),closed_by_id=$3 WHERE guild_id=$1 AND incident_number=$2", guild_id, incident_number, actor_id)
                event_type, title, details, redirect_action = "closed", "Incident Closed", "Incident closed by server management from the web dashboard.", "closed"

            await conn.execute("INSERT INTO rescue_incident_events(guild_id,incident_number,channel_id,event_type,actor_id,title,details,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,NOW())", guild_id, incident_number, incident["channel_id"], event_type, actor_id, title, details)

    updated, _, _ = await base.load_incident(guild_id, incident_number)
    incident_id = f"RESCUE-{incident_number:04d}"
    role_ids = await base.responder_role_ids_for_guild(guild_id)
    failures = []
    if updated and updated["channel_id"]:
        channel_id = updated["channel_id"]
        if action in {"priority_up", "priority_down"}:
            await _attempt("incident channel notice", base.post_incident_message(channel_id, f"⚠️ **WEB DISPATCH:** {incident_id} priority changed to **{base.PRIORITY_DISCORD.get(updated['priority'],updated['priority'])}** by <@{actor_id}>."), failures)
            if updated["priority"] == "critical" and role_ids:
                mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
                await _attempt("P1 responder page", base.post_incident_message(channel_id, f"🔴 **ALL-SECTOR PRIORITY 1 PAGE:** {mentions}", role_ids), failures)
        elif action == "arrived":
            await _attempt("incident channel notice", base.post_incident_message(channel_id, f"📍 **WEB DISPATCH:** {incident_id} marked **On Scene** by <@{actor_id}>."), failures)
        elif action == "backup":
            mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
            text = f"🛡️ **BACKUP REQUESTED VIA WEB DISPATCH:** {mentions}" if mentions else "🛡️ **BACKUP REQUESTED VIA WEB DISPATCH.**"
            await _attempt("backup responder page", base.post_incident_message(channel_id, text, role_ids), failures)
        else:
            await _attempt("incident close notice", base.post_incident_message(channel_id, f"🔒 {incident_id} closed from the web dashboard by <@{actor_id}>."), failures)

        await _attempt("incident card", base.sync_incident_card(updated, incident_number, actor_id, close_controls=(action == "close")), failures)
        if action == "close":
            await _attempt("closed channel permissions", base.make_closed_channel_read_only(guild_id, updated, actor_id), failures)
            await _attempt("rescue log", base.post_rescue_log_record(guild_id, incident_number), failures)

    await _attempt("dispatch board", base.refresh_dispatch_board_rest(guild_id), failures)
    if failures:
        log.warning("Dashboard action committed but Discord sync was incomplete for %s: %s", incident_id, ", ".join(failures))
        return RedirectResponse(f"/guild/{guild_id}/incident/{incident_number}?action={redirect_action}&sync=warning", status_code=303)
    return RedirectResponse(f"/guild/{guild_id}/incident/{incident_number}?action={redirect_action}", status_code=303)


for route in list(base.app.router.routes):
    if getattr(route, "path", None) == "/guild/{guild_id}/incident/{incident_number}/action" and "POST" in getattr(route, "methods", set()):
        base.app.router.routes.remove(route)
        break
base.app.add_api_route("/guild/{guild_id}/incident/{incident_number}/action", incident_action_consistent, methods=["POST"])

app = base.app
