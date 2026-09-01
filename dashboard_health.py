"""Operational health page for database, Discord, configuration, and sync state."""

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


HEALTH_CSS = r'''
.health-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;flex-wrap:wrap;margin-bottom:18px}
.health-head h2{font-size:22px;margin-bottom:4px}.health-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.health-card{padding:17px}.health-row{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.health-name{font-weight:760}.health-detail{margin-top:6px;color:var(--muted);font-size:12px;line-height:1.55}.health-state{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:780;white-space:nowrap}.health-ok{color:#9cecc9;background:rgba(35,122,87,.18);border:1px solid rgba(69,212,155,.22)}.health-warn{color:#ffd09a;background:rgba(166,91,24,.16);border:1px solid rgba(255,180,93,.22)}.health-bad{color:#ffb1b9;background:rgba(169,54,67,.18);border:1px solid rgba(255,107,120,.22)}.health-summary{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.health-note{margin-top:14px;padding:12px 14px;border-radius:10px;border:1px solid rgba(116,153,196,.13);background:rgba(5,11,19,.35);font-size:12px;color:var(--muted)}
@media(max-width:760px){.health-grid{grid-template-columns:1fr}}
'''


def _state(ok, warning=False):
    if ok:
        return "Operational", "health-ok"
    if warning:
        return "Attention", "health-warn"
    return "Unavailable", "health-bad"


def _card(name, ok, detail, warning=False):
    label, css = _state(ok, warning)
    return f'''<div class="card health-card"><div class="health-row"><div><div class="health-name">{base.esc(name)}</div><div class="health-detail">{base.esc(detail)}</div></div><span class="health-state {css}">{label}</span></div></div>'''


@base.app.get("/guild/{guild_id}/health", response_class=HTMLResponse)
async def guild_health(request: Request, guild_id: int):
    guild_info = base.require_guild_access(request, guild_id)

    db_ok = False
    db_detail = "Database check did not complete."
    settings = None
    service_rows = []
    dispatch = None
    log_channel = None
    active_total = 0
    active_without_card = 0
    try:
        async with base.pool.acquire() as conn:
            db_ok = bool(await conn.fetchval("SELECT 1"))
            settings = await conn.fetchrow(
                "SELECT responder_role_ids,request_channel_id,incident_category_id FROM rescue_guild_settings WHERE guild_id=$1",
                guild_id,
            )
            service_rows = await conn.fetch(
                "SELECT service,role_ids FROM rescue_service_role_settings WHERE guild_id=$1",
                guild_id,
            )
            dispatch = await conn.fetchrow(
                "SELECT channel_id,message_id FROM rescue_dispatch_boards WHERE guild_id=$1",
                guild_id,
            )
            log_channel = await conn.fetchval(
                "SELECT channel_id FROM rescue_log_channels WHERE guild_id=$1",
                guild_id,
            )
            active_total = int(await conn.fetchval(
                "SELECT COUNT(*) FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed'",
                guild_id,
            ) or 0)
            active_without_card = int(await conn.fetchval(
                "SELECT COUNT(*) FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed' AND incident_message_id IS NULL",
                guild_id,
            ) or 0)
        db_detail = "PostgreSQL connection and rescue schema are responding."
    except Exception:
        db_detail = "PostgreSQL could not complete the health query."

    discord_ok = False
    discord_detail = "Discord API check did not complete."
    channels = []
    roles = []
    try:
        guild = await base.discord_get(f"/guilds/{guild_id}")
        channels = await base.discord_get(f"/guilds/{guild_id}/channels")
        roles = await base.discord_get(f"/guilds/{guild_id}/roles")
        discord_ok = True
        discord_detail = f"Bot can reach {guild.get('name') or guild_info['name']} and read server resources."
    except httpx.HTTPStatusError as exc:
        discord_detail = f"Discord API returned HTTP {exc.response.status_code}."
    except Exception:
        discord_detail = "Discord API could not be reached by the dashboard."

    channel_ids = {int(c["id"]) for c in channels if c.get("id")}
    role_ids = {int(r["id"]) for r in roles if r.get("id")}

    responder_ids = list(settings["responder_role_ids"] or []) if settings else []
    request_channel = settings["request_channel_id"] if settings else None
    incident_category = settings["incident_category_id"] if settings else None
    configured_service_roles = {r["service"]: list(r["role_ids"] or []) for r in service_rows}

    required_channels = [request_channel, dispatch["channel_id"] if dispatch else None, log_channel]
    configured_channels = sum(1 for value in required_channels if value)
    valid_channels = sum(1 for value in required_channels if value and int(value) in channel_ids) if discord_ok else 0
    category_ok = not incident_category or (discord_ok and int(incident_category) in channel_ids)
    responder_ok = bool(responder_ids) and (not discord_ok or all(int(v) in role_ids for v in responder_ids))
    service_ready = sum(1 for service in base.SERVICES if configured_service_roles.get(service))
    service_roles_valid = not discord_ok or all(
        int(role_id) in role_ids
        for ids in configured_service_roles.values()
        for role_id in ids
    )
    config_ok = bool(settings) and responder_ok and configured_channels == 3 and (not discord_ok or valid_channels == 3) and category_ok and service_ready == len(base.SERVICES) and service_roles_valid

    dispatch_ok = False
    dispatch_detail = "No live dispatch board is configured."
    if dispatch and discord_ok:
        try:
            await base.discord_get(f"/channels/{dispatch['channel_id']}/messages/{dispatch['message_id']}")
            dispatch_ok = True
            dispatch_detail = "Configured dispatch board message is reachable and ready for updates."
        except httpx.HTTPStatusError as exc:
            dispatch_detail = f"Configured dispatch board message returned HTTP {exc.response.status_code}."
        except Exception:
            dispatch_detail = "Configured dispatch board message could not be verified."
    elif dispatch:
        dispatch_detail = "Dispatch board is configured, but Discord connectivity is unavailable."

    card_ok = active_without_card == 0
    card_warning = bool(active_without_card)
    if active_total == 0:
        card_detail = "No active incidents require Discord card synchronization."
    elif card_ok:
        card_detail = f"All {active_total} active incident card IDs are persisted for direct Discord updates."
    else:
        card_detail = f"{active_without_card} of {active_total} active incidents still need card-ID discovery; they will self-heal on the next dashboard sync."

    config_detail = (
        f"{valid_channels if discord_ok else configured_channels}/3 operational channels, "
        f"{service_ready}/{len(base.SERVICES)} service paging groups, "
        f"{len(responder_ids)} responder role{'s' if len(responder_ids) != 1 else ''}."
    )
    if not category_ok:
        config_detail += " The configured incident category is unavailable."
    if not service_roles_valid or (discord_ok and responder_ids and not responder_ok):
        config_detail += " One or more configured Discord roles no longer exist."

    checks = [
        ("PostgreSQL", db_ok, False),
        ("Discord API / Bot", discord_ok, False),
        ("Configuration", config_ok, not config_ok),
        ("Dispatch Board", dispatch_ok, bool(dispatch)),
        ("Incident Card Sync", card_ok, card_warning),
    ]
    healthy = sum(1 for _, ok, _ in checks if ok)
    all_ok = healthy == len(checks)
    overall_label = "All systems operational" if all_ok else f"{healthy}/{len(checks)} checks operational"
    overall_class = "health-ok" if all_ok else "health-warn"

    body = f'''<style>{HEALTH_CSS}</style><div class="health-head"><div><h2>System Health</h2><div class="muted">Live operational checks for {base.esc(guild_info['name'])}.</div></div><div class="health-summary"><span class="health-state {overall_class}">{base.esc(overall_label)}</span><a class="btn secondary" href="/guild/{guild_id}/health">Refresh Checks</a></div></div>
<div class="health-grid">
{_card('PostgreSQL', db_ok, db_detail)}
{_card('Discord API / Bot', discord_ok, discord_detail)}
{_card('Configuration', config_ok, config_detail, warning=not config_ok)}
{_card('Live Dispatch Board', dispatch_ok, dispatch_detail, warning=bool(dispatch) and not dispatch_ok)}
{_card('Incident Card Synchronization', card_ok, card_detail, warning=card_warning)}
</div><div class="health-note">Health checks are read-only. A warning means the dashboard remains available but part of Discord synchronization or server configuration may need attention.</div>'''
    return base.page(f"System Health · {guild_info['name']}", body, base.current_user(request))


app = base.app
