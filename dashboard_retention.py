"""Optional, disabled-by-default retention controls for closed rescue data."""

import asyncio
import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

import dashboard_core as base

logger = logging.getLogger("star-citizen-rescue-dashboard.retention")
_cleanup_task = None


async def run_retention_once():
    if base.pool is None:
        return
    async with base.pool.acquire() as conn:
        settings = await conn.fetch(
            "SELECT guild_id,enabled,closed_incident_days,admin_audit_days FROM rescue_retention_settings WHERE enabled=TRUE"
        )
    for row in settings:
        guild_id = int(row["guild_id"])
        try:
            async with base.pool.acquire() as conn:
                async with conn.transaction():
                    incident_days = row["closed_incident_days"]
                    if incident_days:
                        old_channels = await conn.fetch(
                            """
                            SELECT channel_id FROM rescue_incidents
                            WHERE guild_id=$1 AND status='closed' AND closed_at < NOW() - ($2 * INTERVAL '1 day')
                            """,
                            guild_id,
                            int(incident_days),
                        )
                        channel_ids = [r["channel_id"] for r in old_channels if r["channel_id"]]
                        await conn.execute(
                            """
                            DELETE FROM rescue_incident_events
                            WHERE guild_id=$1 AND incident_number IN (
                                SELECT incident_number FROM rescue_incidents
                                WHERE guild_id=$1 AND status='closed' AND closed_at < NOW() - ($2 * INTERVAL '1 day')
                            )
                            """,
                            guild_id,
                            int(incident_days),
                        )
                        if channel_ids:
                            await conn.execute(
                                "DELETE FROM rescue_incident_responders WHERE channel_id = ANY($1::bigint[])",
                                channel_ids,
                            )
                        await conn.execute(
                            """
                            DELETE FROM rescue_incidents
                            WHERE guild_id=$1 AND status='closed' AND closed_at < NOW() - ($2 * INTERVAL '1 day')
                            """,
                            guild_id,
                            int(incident_days),
                        )
                    audit_days = row["admin_audit_days"]
                    if audit_days:
                        await conn.execute(
                            "DELETE FROM rescue_admin_audit_events WHERE guild_id=$1 AND created_at < NOW() - ($2 * INTERVAL '1 day')",
                            guild_id,
                            int(audit_days),
                        )
            logger.info("operational_event component=retention guild_id=%s result=completed", guild_id)
        except Exception:
            logger.exception("Retention cleanup failed guild_id=%s", guild_id)


async def _retention_loop():
    while True:
        await asyncio.sleep(60 * 60 * 24)
        await run_retention_once()


@base.app.on_event("startup")
async def start_retention_loop():
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_retention_loop())


@base.app.on_event("shutdown")
async def stop_retention_loop():
    if _cleanup_task:
        _cleanup_task.cancel()


@base.app.get("/guild/{guild_id}/retention", response_class=HTMLResponse)
async def retention_settings(request: Request, guild_id: int):
    guild = base.require_guild_access(request, guild_id)
    async with base.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled,closed_incident_days,admin_audit_days FROM rescue_retention_settings WHERE guild_id=$1",
            guild_id,
        )
    enabled = bool(row and row["enabled"])
    incident_days = row["closed_incident_days"] if row else None
    audit_days = row["admin_audit_days"] if row else None
    csrf = base.esc(request.session.get("csrf"))
    body = f'''<div class="overview-head"><div><h2>Data Retention</h2><div class="muted">Retention policy for {base.esc(guild['name'])}. Disabled by default.</div></div></div>
<div class="card"><h2>Closed incident & audit retention</h2><p>When enabled, cleanup runs daily. Minimum retention is 30 days. Leave a field blank to keep that data indefinitely.</p><div class="notice" style="border-color:#6b542e;background:#2b2212;color:#ffd49a">Deleting old rescue records is permanent. Discord channels/messages are never deleted by this cleanup.</div><form method="post" action="/guild/{guild_id}/retention"><input type="hidden" name="csrf" value="{csrf}"><label><input style="width:auto;min-height:auto" type="checkbox" name="enabled" value="1" {'checked' if enabled else ''}> Enable automatic retention cleanup</label><label>Closed incident retention (days, minimum 30)</label><input name="closed_incident_days" type="number" min="30" value="{base.esc(incident_days or '')}" placeholder="Blank = keep forever"><label>Dashboard admin-audit retention (days, minimum 30)</label><input name="admin_audit_days" type="number" min="30" value="{base.esc(audit_days or '')}" placeholder="Blank = keep forever"><div style="margin-top:16px"><button class="btn warn" type="submit">Save Retention Policy</button> <a class="btn secondary" href="/guild/{guild_id}/operations">Cancel</a></div></form></div>'''
    return base.page(f"Data Retention · {guild['name']}", body, base.current_user(request))


@base.app.post("/guild/{guild_id}/retention")
async def save_retention_settings(request: Request, guild_id: int):
    base.require_guild_access(request, guild_id)
    form = await request.form()
    base.require_csrf(request, form.get("csrf"))
    enabled = form.get("enabled") == "1"

    def parse_days(name):
        raw = str(form.get(name) or "").strip()
        if not raw:
            return None
        value = int(raw)
        if value < 30:
            raise ValueError(f"{name} must be at least 30 days")
        return value

    try:
        incident_days = parse_days("closed_incident_days")
        audit_days = parse_days("admin_audit_days")
    except (TypeError, ValueError):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Retention values must be blank or at least 30 days.")

    async with base.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rescue_retention_settings(guild_id,enabled,closed_incident_days,admin_audit_days,updated_at)
            VALUES($1,$2,$3,$4,NOW())
            ON CONFLICT(guild_id) DO UPDATE SET enabled=$2,closed_incident_days=$3,admin_audit_days=$4,updated_at=NOW()
            """,
            guild_id,
            enabled,
            incident_days,
            audit_days,
        )
    return RedirectResponse(f"/guild/{guild_id}/retention", status_code=303)


app = base.app
