"""Atomic database transitions for Discord incident controls.

Lifecycle mutations lock the incident row and write their ledger event in the
same PostgreSQL transaction. Returned results let Discord controls reject stale
or duplicate clicks without repeating notifications.
"""

import logging

logger = logging.getLogger("star-citizen-rescue-bot.transitions")


async def _event(conn, incident, event_type, actor_id, title, details):
    await conn.execute(
        """
        INSERT INTO rescue_incident_events(
            guild_id, incident_number, channel_id, event_type,
            actor_id, title, details, created_at
        ) VALUES($1,$2,$3,$4,$5,$6,$7,NOW())
        """,
        incident["guild_id"], incident["incident_number"], incident["channel_id"],
        event_type, actor_id, title, details,
    )


async def _locked_incident(conn, channel_id):
    return await conn.fetchrow(
        """
        SELECT guild_id, incident_number, channel_id, status, priority,
               primary_responder_id, responded_at, arrived_at,
               backup_requested_at, closed_at
        FROM rescue_incidents
        WHERE channel_id=$1
        FOR UPDATE
        """,
        channel_id,
    )


async def transition_incident(bot, channel_id, action, actor_id=None):
    """Apply Respond/Arrived/Backup/Close atomically; return (changed, reason)."""
    if not bot.db_pool:
        return False, "database_unavailable"
    if action not in {"respond", "arrived", "backup", "close"}:
        return False, "invalid_action"

    try:
        async with bot.db_pool.acquire() as conn:
            async with conn.transaction():
                incident = await _locked_incident(conn, channel_id)
                if not incident:
                    return False, "not_found"
                if incident["status"] == "closed" or incident["closed_at"] is not None:
                    return False, "closed"

                if action == "respond":
                    if actor_id is None:
                        return False, "actor_required"
                    if incident["primary_responder_id"] is not None:
                        if incident["primary_responder_id"] == actor_id:
                            return False, "already_primary"
                        return False, "primary_already_assigned"
                    await conn.execute(
                        """UPDATE rescue_incidents SET status='en_route',primary_responder_id=$2,
                           responded_at=COALESCE(responded_at,NOW()) WHERE channel_id=$1""",
                        channel_id, actor_id,
                    )
                    await _event(conn, incident, "primary_assigned", actor_id, "Primary Responder Assigned", "A responder accepted primary responsibility for the incident.")
                    return True, "responded"

                if action == "arrived":
                    if incident["primary_responder_id"] is None:
                        return False, "no_primary"
                    if incident["arrived_at"] is not None:
                        return False, "already_arrived"
                    await conn.execute("UPDATE rescue_incidents SET status='on_scene',arrived_at=NOW() WHERE channel_id=$1", channel_id)
                    await _event(conn, incident, "arrived", actor_id, "Arrived On Scene", "The response team reported arrival on scene.")
                    return True, "arrived"

                if action == "backup":
                    if incident["primary_responder_id"] is None:
                        return False, "no_primary"
                    if incident["backup_requested_at"] is not None:
                        return False, "backup_already_requested"
                    await conn.execute("UPDATE rescue_incidents SET status='backup_requested',backup_requested_at=NOW() WHERE channel_id=$1", channel_id)
                    await _event(conn, incident, "backup_requested", actor_id, "Backup Requested", "Additional responder support was requested.")
                    return True, "backup_requested"

                if actor_id is None:
                    return False, "actor_required"
                await conn.execute("UPDATE rescue_incidents SET status='closed',closed_at=NOW(),closed_by_id=$2 WHERE channel_id=$1", channel_id, actor_id)
                await _event(conn, incident, "closed", actor_id, "Incident Closed", "The incident was closed by an authorized user.")
                return True, "closed_now"
    except Exception:
        logger.exception("Atomic incident transition failed: channel=%s action=%s", channel_id, action)
        return False, "database_error"


async def transition_priority(bot, channel_id, priority, actor_id):
    """Change priority and ledger it under the incident lock."""
    if not bot.db_pool:
        return False, "database_unavailable"
    if priority not in {"critical", "urgent", "standard"}:
        return False, "invalid_priority"
    try:
        async with bot.db_pool.acquire() as conn:
            async with conn.transaction():
                incident = await _locked_incident(conn, channel_id)
                if not incident:
                    return False, "not_found"
                if incident["status"] == "closed" or incident["closed_at"] is not None:
                    return False, "closed"
                previous = incident["priority"]
                if previous == priority:
                    return False, "unchanged"
                await conn.execute(
                    "UPDATE rescue_incidents SET priority=$2,priority_changed_by=$3,priority_changed_at=NOW() WHERE channel_id=$1",
                    channel_id, priority, actor_id,
                )
                labels = {"critical": "P1 Critical", "urgent": "P2 Urgent", "standard": "P3 Standard"}
                await _event(conn, incident, "priority_changed", actor_id, "Priority Changed", f"Priority changed from {labels.get(previous, previous)} to {labels[priority]}.")
                return True, "priority_changed"
    except Exception:
        logger.exception("Atomic priority transition failed: channel=%s", channel_id)
        return False, "database_error"


async def join_responder(bot, channel_id, user_id):
    """Join an active incident once and ledger the join atomically."""
    if not bot.db_pool:
        return False, "database_unavailable"
    try:
        async with bot.db_pool.acquire() as conn:
            async with conn.transaction():
                incident = await _locked_incident(conn, channel_id)
                if not incident:
                    return False, "not_found"
                if incident["status"] == "closed" or incident["closed_at"] is not None:
                    return False, "closed"
                exists = await conn.fetchval("SELECT 1 FROM rescue_incident_responders WHERE channel_id=$1 AND user_id=$2", channel_id, user_id)
                if exists:
                    return False, "already_joined"
                await conn.execute("INSERT INTO rescue_incident_responders(channel_id,user_id) VALUES($1,$2)", channel_id, user_id)
                if incident["primary_responder_id"] != user_id:
                    await _event(conn, incident, "responder_joined", user_id, "Responder Joined", "An additional responder joined the response team.")
                return True, "joined"
    except Exception:
        logger.exception("Atomic responder join failed: channel=%s user=%s", channel_id, user_id)
        return False, "database_error"


async def leave_responder(bot, channel_id, user_id):
    """Leave an active incident and ledger the handoff atomically.

    Returns (removed, was_primary, reason).
    """
    if not bot.db_pool:
        return False, False, "database_unavailable"
    try:
        async with bot.db_pool.acquire() as conn:
            async with conn.transaction():
                incident = await _locked_incident(conn, channel_id)
                if not incident:
                    return False, False, "not_found"
                if incident["status"] == "closed" or incident["closed_at"] is not None:
                    return False, False, "closed"
                listed = bool(await conn.fetchval("SELECT 1 FROM rescue_incident_responders WHERE channel_id=$1 AND user_id=$2", channel_id, user_id))
                was_primary = incident["primary_responder_id"] == user_id
                if not listed and not was_primary:
                    return False, False, "not_responder"
                await conn.execute("DELETE FROM rescue_incident_responders WHERE channel_id=$1 AND user_id=$2", channel_id, user_id)
                if was_primary:
                    await conn.execute("UPDATE rescue_incidents SET primary_responder_id=NULL,status='awaiting_responder' WHERE channel_id=$1", channel_id)
                details = "The primary responder left the response; the incident returned to Awaiting Responder." if was_primary else "A support responder left the active response team."
                await _event(conn, incident, "responder_left", user_id, "Responder Left Response", details)
                return True, was_primary, "left"
    except Exception:
        logger.exception("Atomic responder leave failed: channel=%s user=%s", channel_id, user_id)
        return False, False, "database_error"
