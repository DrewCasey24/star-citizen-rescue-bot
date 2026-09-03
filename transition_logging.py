"""Searchable operational logs around database-authoritative incident transitions."""

import logging

import incident_transitions as transitions

logger = logging.getLogger("star-citizen-rescue-bot.lifecycle")

_original_transition_incident = transitions.transition_incident
_original_transition_priority = transitions.transition_priority
_original_join_responder = transitions.join_responder
_original_leave_responder = transitions.leave_responder


async def _incident_context(bot, channel_id):
    if not getattr(bot, "db_pool", None):
        return None, None
    try:
        async with bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT guild_id,incident_number FROM rescue_incidents WHERE channel_id=$1",
                channel_id,
            )
        if row:
            return row["guild_id"], f"RESCUE-{int(row['incident_number']):04d}"
    except Exception:
        pass
    return None, None


def _log(action, channel_id, actor_id, result, reason, guild_id, incident):
    logger.info(
        "operational_event component=lifecycle action=%s guild_id=%s incident=%s channel_id=%s actor_id=%s result=%s reason=%s",
        action,
        guild_id if guild_id is not None else "-",
        incident or "-",
        channel_id,
        actor_id if actor_id is not None else "-",
        result,
        reason,
    )


async def transition_incident_logged(bot, channel_id, action, actor_id=None):
    changed, reason = await _original_transition_incident(bot, channel_id, action, actor_id)
    guild_id, incident = await _incident_context(bot, channel_id)
    _log(action, channel_id, actor_id, "changed" if changed else "rejected", reason, guild_id, incident)
    return changed, reason


async def transition_priority_logged(bot, channel_id, priority, actor_id, expected_priority=None):
    changed, reason = await _original_transition_priority(bot, channel_id, priority, actor_id, expected_priority)
    guild_id, incident = await _incident_context(bot, channel_id)
    _log(f"priority_{priority}", channel_id, actor_id, "changed" if changed else "rejected", reason, guild_id, incident)
    return changed, reason


async def join_responder_logged(bot, channel_id, user_id):
    changed, reason = await _original_join_responder(bot, channel_id, user_id)
    guild_id, incident = await _incident_context(bot, channel_id)
    _log("join_responder", channel_id, user_id, "changed" if changed else "rejected", reason, guild_id, incident)
    return changed, reason


async def leave_responder_logged(bot, channel_id, user_id):
    removed, was_primary, reason = await _original_leave_responder(bot, channel_id, user_id)
    guild_id, incident = await _incident_context(bot, channel_id)
    result = "primary_left" if removed and was_primary else "changed" if removed else "rejected"
    _log("leave_responder", channel_id, user_id, result, reason, guild_id, incident)
    return removed, was_primary, reason


transitions.transition_incident = transition_incident_logged
transitions.transition_priority = transition_priority_logged
transitions.join_responder = join_responder_logged
transitions.leave_responder = leave_responder_logged
