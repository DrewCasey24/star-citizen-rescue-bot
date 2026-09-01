"""Authoritative database state transitions for dashboard incident actions."""

from fastapi import HTTPException

import dashboard_core as base

ALLOWED_ACTIONS = {"priority_up", "priority_down", "arrived", "backup", "close"}


async def apply_incident_action(guild_id: int, incident_number: int, action: str, actor_id: int):
    """Apply one dashboard incident action atomically and record exactly one ledger event.

    Discord side effects intentionally live outside this function so a committed database
    transition can be retried safely when Discord synchronization is incomplete.
    """
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown incident action.")

    async with base.pool.acquire() as conn:
        async with conn.transaction():
            incident = await conn.fetchrow(
                "SELECT incident_number,channel_id,requester_id,priority,status,"
                "primary_responder_id,created_at,responded_at,arrived_at,"
                "backup_requested_at,closed_at "
                "FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2 FOR UPDATE",
                guild_id,
                incident_number,
            )
            if not incident:
                raise HTTPException(status_code=404, detail="Incident not found.")
            if incident["status"] == "closed":
                raise HTTPException(status_code=409, detail="This incident is already closed.")

            event_type = action
            title = ""
            details = ""
            redirect_action = action

            if action in {"priority_up", "priority_down"}:
                current = incident["priority"] if incident["priority"] in base.PRIORITY_ORDER else "standard"
                index = base.PRIORITY_ORDER.index(current)
                new_index = (
                    min(len(base.PRIORITY_ORDER) - 1, index + 1)
                    if action == "priority_up"
                    else max(0, index - 1)
                )
                new_priority = base.PRIORITY_ORDER[new_index]
                if new_priority == current:
                    boundary = "highest" if action == "priority_up" else "lowest"
                    raise HTTPException(
                        status_code=409,
                        detail=f"Incident is already at the {boundary} priority.",
                    )
                await conn.execute(
                    "UPDATE rescue_incidents SET priority=$3,priority_changed_by=$4,"
                    "priority_changed_at=NOW() WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                    new_priority,
                    actor_id,
                )
                event_type = "priority_changed"
                title = "Priority Changed"
                details = (
                    f"Priority changed from {base.PRIORITIES.get(current, current)} to "
                    f"{base.PRIORITIES.get(new_priority, new_priority)} from the web dashboard."
                )
            elif action == "arrived":
                if incident["arrived_at"] is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="This incident has already been marked on scene.",
                    )
                await conn.execute(
                    "UPDATE rescue_incidents SET status='on_scene',arrived_at=NOW() "
                    "WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                )
                title = "Arrived On Scene"
                details = "Incident marked on scene from the web dashboard."
            elif action == "backup":
                if incident["backup_requested_at"] is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="Backup has already been requested for this incident.",
                    )
                await conn.execute(
                    "UPDATE rescue_incidents SET status='backup_requested',backup_requested_at=NOW() "
                    "WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                )
                event_type = "backup_requested"
                title = "Backup Requested"
                details = "Additional responder support was requested from the web dashboard."
            else:
                await conn.execute(
                    "UPDATE rescue_incidents SET status='closed',closed_at=NOW(),closed_by_id=$3 "
                    "WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                    actor_id,
                )
                event_type = "closed"
                title = "Incident Closed"
                details = "Incident closed by server management from the web dashboard."
                redirect_action = "closed"

            await conn.execute(
                "INSERT INTO rescue_incident_events("
                "guild_id,incident_number,channel_id,event_type,actor_id,title,details,created_at"
                ") VALUES($1,$2,$3,$4,$5,$6,$7,NOW())",
                guild_id,
                incident_number,
                incident["channel_id"],
                event_type,
                actor_id,
                title,
                details,
            )

    updated, _, _ = await base.load_incident(guild_id, incident_number)
    return updated, redirect_action


app = base.app
