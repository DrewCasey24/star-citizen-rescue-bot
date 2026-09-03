"""Unified operations center for health, active incidents, audit, and responder configuration."""

from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse

import dashboard_core as base
import dashboard_health as health


OPS_CSS = r'''
.ops-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-end;flex-wrap:wrap;margin-bottom:16px}
.ops-actions{display:flex;gap:8px;flex-wrap:wrap}.ops-summary{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:14px}
.ops-stat{padding:13px;border:1px solid rgba(116,153,196,.14);border-radius:11px;background:rgba(5,11,19,.38)}.ops-stat .label{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:800}.ops-stat strong{display:block;font-size:21px;margin-top:5px}.ops-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(330px,.75fr);gap:14px;align-items:start}.ops-stack{display:grid;gap:14px}.ops-filter{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;align-items:end;margin-bottom:12px}.ops-filter label{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:800}.ops-filter select{width:100%}.ops-filter .btn{width:100%;text-align:center}.incident-row{padding:12px 0;border-top:1px solid rgba(116,153,196,.10);display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,.8fr) auto;gap:12px;align-items:center}.incident-row:first-of-type{border-top:0}.incident-name{font-weight:780}.incident-meta{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.45}.incident-status{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.ops-pill{display:inline-flex;padding:4px 7px;border-radius:999px;border:1px solid rgba(116,153,196,.16);font-size:10px;font-weight:800}.ops-pill.bad{color:#ffb1b9;border-color:rgba(255,107,120,.28);background:rgba(169,54,67,.16)}.ops-pill.warn{color:#ffd09a;border-color:rgba(255,180,93,.24);background:rgba(166,91,24,.14)}.ops-pill.ok{color:#9cecc9;border-color:rgba(69,212,155,.22);background:rgba(35,122,87,.14)}.audit-row{padding:10px 0;border-top:1px solid rgba(116,153,196,.10)}.audit-row:first-of-type{border-top:0}.audit-title{display:flex;justify-content:space-between;gap:10px;font-weight:720}.audit-meta{font-size:11px;color:var(--muted);margin-top:4px}.role-block{padding:10px 0;border-top:1px solid rgba(116,153,196,.10)}.role-block:first-of-type{border-top:0}.role-head{display:flex;justify-content:space-between;gap:10px}.role-list{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.5}.ops-alert{padding:12px 14px;border-radius:10px;margin-bottom:12px}.ops-alert.bad{background:rgba(169,54,67,.13);border:1px solid rgba(255,107,120,.24);color:#ffb1b9}.ops-alert.ok{background:rgba(35,122,87,.13);border:1px solid rgba(69,212,155,.22);color:#9cecc9}.ops-empty{padding:12px;color:var(--muted);font-size:12px}
@media(max-width:1050px){.ops-summary{grid-template-columns:repeat(3,minmax(0,1fr))}.ops-grid{grid-template-columns:1fr}}@media(max-width:720px){.ops-summary{grid-template-columns:1fr 1fr}.ops-filter{grid-template-columns:1fr 1fr}.incident-row{grid-template-columns:1fr}.incident-row .btn{width:100%;text-align:center}}@media(max-width:480px){.ops-summary,.ops-filter{grid-template-columns:1fr}}
'''


def _option(value, label, selected):
    return f'<option value="{base.esc(value)}"{" selected" if selected else ""}>{base.esc(label)}</option>'


def _priority_class(value):
    return "p1" if value == "critical" else "p2" if value == "urgent" else "p3"


@base.app.get("/guild/{guild_id}/operations", response_class=HTMLResponse)
async def operations_center(request: Request, guild_id: int, priority: str = "", status: str = "", service: str = ""):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)

    priority = priority if priority in {"critical", "urgent", "standard"} else ""
    status = status if status in {"awaiting_responder", "en_route", "on_scene", "backup_requested"} else ""
    service = service if service in base.SERVICES else ""

    async with base.pool.acquire() as conn:
        incidents = await conn.fetch(
            """
            SELECT incident_number,channel_id,callsign,service,location,priority,status,
                   primary_responder_id,created_at,incident_message_id
            FROM rescue_incidents
            WHERE guild_id=$1 AND status<>'closed'
              AND ($2='' OR priority=$2)
              AND ($3='' OR status=$3)
              AND ($4='' OR service=$4)
            ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2 ELSE 3 END,
                     created_at ASC
            LIMIT 100
            """,
            guild_id, priority, status, service,
        )
        totals = await conn.fetchrow(
            """
            SELECT COUNT(*) FILTER (WHERE status<>'closed') AS active,
                   COUNT(*) FILTER (WHERE status='awaiting_responder') AS awaiting,
                   COUNT(*) FILTER (WHERE status='backup_requested') AS backup,
                   COUNT(*) FILTER (WHERE status<>'closed' AND incident_message_id IS NULL) AS no_card,
                   COUNT(*) FILTER (WHERE status='closed') AS closed
            FROM rescue_incidents WHERE guild_id=$1
            """,
            guild_id,
        )
        events = await conn.fetch(
            """
            SELECT incident_number,event_type,actor_id,title,details,created_at
            FROM rescue_incident_events
            WHERE guild_id=$1
            ORDER BY created_at DESC
            LIMIT 25
            """,
            guild_id,
        )
        settings = await conn.fetchrow(
            "SELECT responder_role_ids,request_channel_id,incident_category_id FROM rescue_guild_settings WHERE guild_id=$1",
            guild_id,
        )
        service_rows = await conn.fetch(
            "SELECT service,role_ids FROM rescue_service_role_settings WHERE guild_id=$1",
            guild_id,
        )
        dispatch = await conn.fetchrow("SELECT channel_id,message_id FROM rescue_dispatch_boards WHERE guild_id=$1", guild_id)
        log_channel = await conn.fetchval("SELECT channel_id FROM rescue_log_channels WHERE guild_id=$1", guild_id)

    discord_ok = False
    channels = []
    roles = []
    try:
        channels = await base.discord_get(f"/guilds/{guild_id}/channels")
        roles = await base.discord_get(f"/guilds/{guild_id}/roles")
        discord_ok = True
    except Exception:
        pass

    channel_ids = {int(c["id"]) for c in channels if c.get("id")}
    role_names = {int(r["id"]): r.get("name", f"Role {r['id']}") for r in roles if r.get("id")}
    active_channel_ids = [int(row["channel_id"]) for row in incidents if row["channel_id"]]
    missing_channels = {cid for cid in active_channel_ids if discord_ok and cid not in channel_ids}
    actor_ids = [row["actor_id"] for row in events if row["actor_id"]] + [row["primary_responder_id"] for row in incidents if row["primary_responder_id"]]
    names = await base.member_names(guild_id, actor_ids)

    responder_ids = list(settings["responder_role_ids"] or []) if settings else []
    missing_responder_roles = [int(rid) for rid in responder_ids if discord_ok and int(rid) not in role_names]
    service_map = {row["service"]: list(row["role_ids"] or []) for row in service_rows}
    stale_service_role_count = sum(1 for ids in service_map.values() for rid in ids if discord_ok and int(rid) not in role_names)

    config_channels = [settings["request_channel_id"] if settings else None, dispatch["channel_id"] if dispatch else None, log_channel]
    stale_config_channels = [int(cid) for cid in config_channels if cid and discord_ok and int(cid) not in channel_ids]
    config_issues = len(stale_config_channels) + len(missing_responder_roles) + stale_service_role_count
    no_card = int(totals["no_card"] or 0)
    attention = len(missing_channels) + no_card + config_issues

    summary = "".join([
        f'<div class="ops-stat"><div class="label">Active</div><strong>{int(totals["active"] or 0)}</strong></div>',
        f'<div class="ops-stat"><div class="label">Awaiting Primary</div><strong>{int(totals["awaiting"] or 0)}</strong></div>',
        f'<div class="ops-stat"><div class="label">Backup Requested</div><strong>{int(totals["backup"] or 0)}</strong></div>',
        f'<div class="ops-stat"><div class="label">Completed</div><strong>{int(totals["closed"] or 0)}</strong></div>',
        f'<div class="ops-stat"><div class="label">Needs Attention</div><strong>{attention}</strong></div>',
    ])

    filter_query = urlencode({k: v for k, v in {"priority": priority, "status": status, "service": service}.items() if v})
    priority_options = _option("", "All priorities", not priority) + "".join(_option(k, base.PRIORITIES.get(k, k), priority == k) for k in ("critical", "urgent", "standard"))
    status_options = _option("", "All statuses", not status) + "".join(_option(k, base.STATUSES.get(k, k), status == k) for k in ("awaiting_responder", "en_route", "on_scene", "backup_requested"))
    service_options = _option("", "All services", not service) + "".join(_option(k, label, service == k) for k, label in base.SERVICES.items())

    incident_html = ""
    for row in incidents:
        iid = f"RESCUE-{row['incident_number']:04d}"
        primary = names.get(row["primary_responder_id"], "Unassigned") if row["primary_responder_id"] else "Unassigned"
        channel_missing = bool(row["channel_id"] and int(row["channel_id"]) in missing_channels)
        pills = [f'<span class="pill {_priority_class(row["priority"])}">{base.esc(base.PRIORITIES.get(row["priority"], row["priority"]))}</span>', f'<span class="ops-pill">{base.esc(base.STATUSES.get(row["status"], row["status"]))}</span>']
        if channel_missing:
            pills.append('<span class="ops-pill bad">Discord channel missing</span>')
        if not row["incident_message_id"]:
            pills.append('<span class="ops-pill warn">Card ID missing</span>')
        incident_html += f'''<div class="incident-row"><div><div class="incident-name">{iid} · {base.esc(row['callsign'])}</div><div class="incident-meta">{base.esc(base.SERVICES.get(row['service'],row['service']))} · {base.esc(row['location'])}<br>Primary: {base.esc(primary)} · Opened {base.format_dt(row['created_at'])}</div></div><div class="incident-status">{"".join(pills)}</div><a class="btn secondary" href="/guild/{guild_id}/incident/{row['incident_number']}">Open Incident</a></div>'''
    if not incident_html:
        incident_html = '<div class="ops-empty">No active incidents match these filters.</div>'

    audit_html = ""
    for event in events:
        actor = names.get(event["actor_id"], "System") if event["actor_id"] else "System"
        audit_html += f'''<div class="audit-row"><div class="audit-title"><a href="/guild/{guild_id}/incident/{event['incident_number']}">RESCUE-{event['incident_number']:04d} · {base.esc(event['title'])}</a><span class="ops-pill">{base.esc(event['event_type'])}</span></div><div>{base.esc(event['details'])}</div><div class="audit-meta">{base.format_dt(event['created_at'])} · {base.esc(actor)}</div></div>'''
    if not audit_html:
        audit_html = '<div class="ops-empty">No ledger events recorded yet.</div>'

    responder_role_text = []
    for rid in responder_ids:
        rid_int = int(rid)
        responder_role_text.append(role_names.get(rid_int, f"Missing role (ID {rid_int})"))
    role_sections = f'''<div class="role-block"><div class="role-head"><strong>Responder Access</strong><span class="ops-pill {'bad' if missing_responder_roles else 'ok'}">{len(responder_ids)} configured</span></div><div class="role-list">{base.esc(', '.join(responder_role_text) if responder_role_text else 'No responder roles configured')}</div></div>'''
    for key, label in base.SERVICES.items():
        ids = service_map.get(key, [])
        labels = [role_names.get(int(rid), f"Missing role (ID {int(rid)})") for rid in ids]
        stale = any(discord_ok and int(rid) not in role_names for rid in ids)
        role_sections += f'''<div class="role-block"><div class="role-head"><strong>{base.esc(label)}</strong><span class="ops-pill {'bad' if stale or not ids else 'ok'}">{len(ids)} paging role{'s' if len(ids)!=1 else ''}</span></div><div class="role-list">{base.esc(', '.join(labels) if labels else 'Not configured')}</div></div>'''

    if not discord_ok:
        alert = '<div class="ops-alert bad"><strong>Discord verification unavailable.</strong> Resource-existence checks could not be completed; database data is still shown below.</div>'
    elif missing_channels:
        ids = ", ".join(str(cid) for cid in sorted(missing_channels))
        alert = f'<div class="ops-alert bad"><strong>{len(missing_channels)} active incident channel(s) are missing from Discord.</strong> Recovery will safely retire confirmed-deleted incident channels. IDs: {base.esc(ids)}</div>'
    elif attention:
        alert = f'<div class="ops-alert bad"><strong>{attention} operational item(s) need attention.</strong> Review card persistence or stale configuration below.</div>'
    else:
        alert = '<div class="ops-alert ok"><strong>Operational integrity looks good.</strong> Active incident channels, saved card IDs, and configured Discord resources are consistent.</div>'

    body = f'''<style>{OPS_CSS}</style><div class="ops-head"><div><h2>Operations Center</h2><div class="muted">Live incident management, audit trail, responder routing, and configuration integrity for {base.esc(guild_info['name'])}.</div></div><div class="ops-actions"><a class="btn secondary" href="/guild/{guild_id}/health">System Health</a><a class="btn secondary" href="/guild/{guild_id}/settings">Responder & Routing Settings</a><a class="btn secondary" href="/guild/{guild_id}/repair-config">Repair Configuration</a></div></div>{alert}<div class="ops-summary">{summary}</div><div class="ops-grid"><div class="ops-stack"><div class="card"><div class="overview-head"><div><h2>Active Incident Management</h2><div class="muted">Priority-first queue with state and Discord integrity indicators.</div></div><span class="status installed">{len(incidents)} shown</span></div><form class="ops-filter" method="get"><div><label>Priority</label><select name="priority">{priority_options}</select></div><div><label>Status</label><select name="status">{status_options}</select></div><div><label>Service</label><select name="service">{service_options}</select></div><div><button class="btn" type="submit">Apply Filters</button></div></form>{incident_html}</div><div class="card"><div class="overview-head"><div><h2>Recent Audit Activity</h2><div class="muted">Latest authoritative incident ledger events.</div></div><a class="btn secondary" href="/guild/{guild_id}/history">Full History</a></div>{audit_html}</div></div><div class="ops-stack"><div class="card"><div class="overview-head"><div><h2>Responder Routing</h2><div class="muted">Who can operate incidents and which sectors are paged first.</div></div><a class="btn secondary" href="/guild/{guild_id}/settings">Edit</a></div>{role_sections}</div><div class="card"><h2>Integrity Summary</h2><div class="kv"><div>Discord verification</div><div>{'Available' if discord_ok else 'Unavailable'}</div><div>Missing active channels</div><div>{len(missing_channels)}</div><div>Active card IDs missing</div><div>{no_card}</div><div>Stale config references</div><div>{config_issues}</div><div>Current filters</div><div>{base.esc(filter_query or 'None')}</div></div></div></div></div>'''
    return base.page(f"Operations Center · {guild_info['name']}", body, base.current_user(request))


_original_health = health.guild_health


async def guild_health_with_incident_integrity(request: Request, guild_id: int):
    response = await _original_health(request, guild_id)
    missing = []
    discord_verified = False
    try:
        async with base.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT incident_number,channel_id FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed' AND channel_id IS NOT NULL ORDER BY incident_number",
                guild_id,
            )
        channels = await base.discord_get(f"/guilds/{guild_id}/channels")
        channel_ids = {int(c["id"]) for c in channels if c.get("id")}
        discord_verified = True
        missing = [row for row in rows if int(row["channel_id"]) not in channel_ids]
    except Exception:
        pass

    html = response.body.decode("utf-8")
    if discord_verified and not missing:
        card = health._card("Incident Channel Integrity", True, "All active database incidents point to Discord channels that currently exist.")
    elif discord_verified:
        ids = ", ".join(f"RESCUE-{row['incident_number']:04d}" for row in missing)
        card = health._card("Incident Channel Integrity", False, f"{len(missing)} active incident channel(s) are missing from Discord: {ids}. Recovery will safely retire confirmed-deleted channels.", warning=True)
    else:
        card = health._card("Incident Channel Integrity", False, "Discord channel existence could not be verified during this health check.", warning=True)
    marker = '</div><div class="health-note">'
    html = html.replace(marker, card + '</div><div class="health-note">', 1)
    html = html.replace('>Refresh Checks</a>', '>Refresh Checks</a><a class="btn secondary" href="/guild/%d/operations">Operations Center</a>' % guild_id, 1)
    return HTMLResponse(html, status_code=response.status_code)


for route in list(base.app.router.routes):
    if getattr(route, "path", None) == "/guild/{guild_id}/health" and "GET" in getattr(route, "methods", set()):
        base.app.router.routes.remove(route)
base.app.add_api_route("/guild/{guild_id}/health", guild_health_with_incident_integrity, methods=["GET"], response_class=HTMLResponse)


_previous_page = base.page


def page_with_operations_link(title, body, user=None):
    if title.startswith("Settings ·") and "Operations Center" not in body:
        body = body.replace("Repair Stale References</a>", "Repair Stale References</a> <a class=\"btn secondary\" href=\"/guild/" + title.split(" · ", 1)[-1] + "\">", 0) if False else body
    return _previous_page(title, body, user)


app = base.app
