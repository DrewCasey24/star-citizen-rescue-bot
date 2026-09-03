"""Dashboard production hardening: migrations, Discord retries, and admin audit."""

import asyncio
import logging
import re

import httpx
from fastapi import Request

import dashboard_core as base
from db_migrations import apply_migrations

logger = logging.getLogger("star-citizen-rescue-dashboard.hardening")

_original_discord_get = base.discord_get


async def discord_get_with_retry(path, token=None):
    """Retry safe Discord GETs on rate limits and transient server failures."""
    attempts = 4
    for attempt in range(attempts):
        try:
            return await _original_discord_get(path, token=token)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise
            if attempt == attempts - 1:
                raise
            retry_after = exc.response.headers.get("Retry-After")
            try:
                delay = min(8.0, max(0.25, float(retry_after))) if retry_after else min(8.0, 0.5 * (2 ** attempt))
            except (TypeError, ValueError):
                delay = min(8.0, 0.5 * (2 ** attempt))
            logger.warning(
                "operational_event component=discord_api action=get_retry path=%s status=%s attempt=%s delay=%.2f",
                path,
                status,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == attempts - 1:
                raise
            delay = min(8.0, 0.5 * (2 ** attempt))
            logger.warning(
                "operational_event component=discord_api action=get_retry path=%s status=network attempt=%s delay=%.2f",
                path,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)


base.discord_get = discord_get_with_retry


@base.app.on_event("startup")
async def dashboard_apply_migrations():
    await apply_migrations(base.pool)
    logger.info("operational_event component=database action=migrations result=ready")


def _admin_action(path):
    if path.endswith("/config"):
        return "configuration_saved"
    if path.endswith("/repair-config"):
        return "configuration_repaired"
    if path.endswith("/retry-sync"):
        return "discord_sync_retried"
    if path.endswith("/action") and "/incident/" in path:
        return "incident_action"
    if path.endswith("/retention"):
        return "retention_updated"
    return None


@base.app.middleware("http")
async def audit_admin_mutations(request: Request, call_next):
    response = await call_next(request)
    if request.method != "POST":
        return response
    action = _admin_action(request.url.path)
    if not action or base.pool is None:
        return response

    guild_match = re.search(r"/guild/(\d+)/", request.url.path)
    guild_id = int(guild_match.group(1)) if guild_match else None
    user = base.current_user(request) or {}
    raw_actor = user.get("id")
    try:
        actor_id = int(raw_actor) if raw_actor is not None else None
    except (TypeError, ValueError):
        actor_id = None
    result = "success" if response.status_code < 400 else f"http_{response.status_code}"
    try:
        async with base.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rescue_admin_audit_events(guild_id,actor_id,action,target,result,details)
                VALUES($1,$2,$3,$4,$5,$6)
                """,
                guild_id,
                actor_id,
                action,
                request.url.path,
                result,
                "Dashboard administrative mutation.",
            )
    except Exception:
        logger.exception("Failed to persist dashboard admin audit event path=%s", request.url.path)
    logger.info(
        "operational_event component=dashboard action=%s guild_id=%s actor_id=%s result=%s target=%s",
        action,
        guild_id if guild_id is not None else "-",
        actor_id if actor_id is not None else "-",
        result,
        request.url.path,
    )
    return response


app = base.app
