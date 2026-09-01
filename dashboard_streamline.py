"""Streamlined application shell, overview, search, and settings routes."""

import re
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import dashboard_core as base


_original_page = base.page

STREAMLINE_CSS = r'''
.app-shell{display:grid;grid-template-columns:220px minmax(0,1fr);min-height:100vh}
.app-shell>.wrap{width:100%;max-width:1500px;margin:0;padding-left:24px}
.sidebar{position:sticky;top:0;height:100vh;padding:24px 14px;border-right:1px solid rgba(116,153,196,.14);background:rgba(5,10,17,.72);backdrop-filter:blur(18px);z-index:30}
.sidebar-title{padding:5px 10px 16px;color:#7189a5;font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}
.sidebar-nav{display:flex;flex-direction:column;gap:5px}
.sidebar-nav a{display:flex;align-items:center;gap:10px;padding:10px 11px;border-radius:10px;color:#9eb4cc;font-size:13px;font-weight:680;border:1px solid transparent}
.sidebar-nav a:hover{background:rgba(86,168,255,.08);border-color:rgba(86,168,255,.13);color:#e4f2ff;transform:none}
.sidebar-nav .divider{height:1px;background:rgba(116,153,196,.12);margin:8px 7px}
.global-search{display:flex;align-items:center;gap:7px;flex:1;max-width:380px;margin-left:auto;margin-right:10px}
.global-search input{min-height:36px;height:36px;padding:7px 10px;font-size:12px}
.global-search button{height:36px;padding:7px 11px}
.section-nav{display:none!important}
.overview-head{display:flex;justify-content:space-between;align-items:flex-end;gap:18px;margin:4px 0 18px}
.overview-head h2{font-size:22px;margin-bottom:4px}
.overview-actions{display:flex;gap:8px;flex-wrap:wrap}
.ops-row td:first-child{border-left:3px solid transparent}
.ops-row.p1-row td:first-child{border-left-color:#ff6b78}.ops-row.p2-row td:first-child{border-left-color:#ffb45d}.ops-row.p3-row td:first-child{border-left-color:#45d49b}
.status-unassigned{color:#ffb45d;font-weight:700}.status-assigned{color:#8fe3c1;font-weight:650}
.settings-section+.settings-section{margin-top:16px}
@media(max-width:1000px){
 .app-shell{display:block}.sidebar{position:sticky;top:0;height:auto;padding:8px 12px;border-right:0;border-bottom:1px solid rgba(116,153,196,.14);overflow-x:auto;background:rgba(5,10,17,.94)}
 .sidebar-title{display:none}.sidebar-nav{flex-direction:row;min-width:max-content}.sidebar-nav a{padding:8px 10px}.sidebar-nav .divider{width:1px;height:28px;margin:3px 5px}
 .app-shell>.wrap{padding-left:14px}.global-search{max-width:none;order:3;width:100%;margin:8px 0 0}header{flex-wrap:wrap}
}
'''


def _guild_id_from_body(body):
    # The root server-selection page contains links to every accessible guild.
    # It is not a guild-scoped page, so never infer a sidebar from those links.
    if "Select a server" in body:
        return None
    match = re.search(r'(?:href|action)="/guild/(\d+)', body)
    return match.group(1) if match else None


def streamlined_page(title, body, user=None):
    response = _original_page(title, body, user)
    guild_id = _guild_id_from_body(body)
    if not guild_id:
        return response

    markup = response.body.decode("utf-8")
    sidebar = f'''<aside class="sidebar"><div class="sidebar-title">Operations</div><nav class="sidebar-nav">
<a href="/guild/{guild_id}">◫ Overview</a><a href="/guild/{guild_id}#active">◉ Active Incidents</a><a href="/guild/{guild_id}/history">⌕ History</a><div class="divider"></div><a href="/guild/{guild_id}/performance">▥ Performance</a><a href="/guild/{guild_id}/performance/services">≡ Service Rankings</a><div class="divider"></div><a href="/guild/{guild_id}/settings">⚙ Settings</a><a href="/">↩ Servers</a></nav></aside>'''
    search = f'''<form class="global-search" method="get" action="/guild/{guild_id}/find"><input type="search" name="q" aria-label="Find incident" placeholder="Find RESCUE-0042 or callsign"><button class="btn secondary" type="submit">Find</button></form>'''

    markup = markup.replace("</style>", STREAMLINE_CSS + "\n</style>", 1)
    markup = markup.replace('<body><div class="wrap">', f'<body><div class="app-shell">{sidebar}<div class="wrap">', 1)
    markup = markup.replace('</div></body></html>', '</div></div></body></html>', 1)
    if '<div class="user">' in markup:
        markup = markup.replace('<div class="user">', search + '<div class="user">', 1)
    else:
        markup = markup.replace('</header>', search + '</header>', 1)
    return HTMLResponse(markup, status_code=response.status_code)


base.page = streamlined_page


async def guild_overview(request: Request, guild_id: int, saved: int = 0):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)

    async with base.pool.acquire() as conn:
        active = await conn.fetch(
            """
            SELECT incident_number,callsign,service,location,priority,status,primary_responder_id,created_at
            FROM rescue_incidents
            WHERE guild_id=$1 AND status<>'closed'
            ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2 ELSE 3 END,
                     CASE status WHEN 'awaiting_responder' THEN 1 ELSE 2 END,
                     created_at ASC
            LIMIT 50
            """,
            guild_id,
        )
        metrics = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status<>'closed') AS active,
                COUNT(*) FILTER (WHERE status='awaiting_responder') AS awaiting,
                COUNT(*) FILTER (WHERE status<>'closed' AND priority IN ('critical','urgent')) AS urgent,
                COUNT(*) FILTER (WHERE status='closed') AS completed
            FROM rescue_incidents
            WHERE guild_id=$1
            """,
            guild_id,
        )
        responder_count = await conn.fetchval(
            """
            WITH active_incidents AS (
                SELECT channel_id,primary_responder_id FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed'
            ), responders AS (
                SELECT primary_responder_id AS user_id FROM active_incidents WHERE primary_responder_id IS NOT NULL
                UNION
                SELECT rr.user_id FROM rescue_incident_responders rr JOIN active_incidents ai ON ai.channel_id=rr.channel_id
            )
            SELECT COUNT(*) FROM responders
            """,
            guild_id,
        )
        recent_events = await conn.fetch(
            """
            SELECT incident_number,title,created_at
            FROM rescue_incident_events
            WHERE guild_id=$1
            ORDER BY created_at DESC,id DESC
            LIMIT 8
            """,
            guild_id,
        )

    names = await base.member_names(guild_id, [r["primary_responder_id"] for r in active if r["primary_responder_id"]])
    active_rows = "".join(
        f'''<tr class="ops-row {"p1-row" if r["priority"]=="critical" else "p2-row" if r["priority"]=="urgent" else "p3-row"}"><td><a class="incident-link" href="/guild/{guild_id}/incident/{r["incident_number"]}">RESCUE-{r["incident_number"]:04d}</a></td><td><span class="pill {"p1" if r["priority"]=="critical" else "p2" if r["priority"]=="urgent" else "p3"}">{base.esc(base.PRIORITIES.get(r["priority"],r["priority"]))}</span></td><td>{base.esc(base.STATUSES.get(r["status"],r["status"]))}</td><td>{base.esc(base.SERVICES.get(r["service"],r["service"]))}</td><td><strong>{base.esc(r["callsign"])}</strong></td><td>{base.esc(r["location"])}</td><td class="{"status-assigned" if r["primary_responder_id"] else "status-unassigned"}">{base.esc(names.get(r["primary_responder_id"],"Assigned") if r["primary_responder_id"] else "Unassigned")}</td></tr>'''
        for r in active
    ) or '<tr><td colspan="7" class="muted">No active incidents. Operations are clear.</td></tr>'

    event_html = "".join(
        f'<div class="event"><div class="event-title">{base.esc(r["title"])}</div><div class="event-meta"><a href="/guild/{guild_id}/incident/{r["incident_number"]}">RESCUE-{r["incident_number"]:04d}</a> · {base.format_dt(r["created_at"])}</div></div>'
        for r in recent_events
    ) or '<p class="muted">No recent activity.</p>'

    notice = '<div class="notice">Configuration saved. Bot configuration refreshes automatically.</div>' if saved else ""
    body = f'''{notice}<div class="overview-head"><div><h2>{base.esc(guild_info['name'])}</h2><div class="muted">Live rescue operations overview</div></div><div class="overview-actions"><a class="btn secondary" href="/guild/{guild_id}/history">Search History</a><a class="btn secondary" href="/guild/{guild_id}/settings">Settings</a></div></div>
<div class="grid"><div class="card span3"><div class="label">Active Incidents</div><div class="metric">{int(metrics['active'] or 0)}</div></div><div class="card span3"><div class="label">Awaiting Responder</div><div class="metric">{int(metrics['awaiting'] or 0)}</div></div><div class="card span3"><div class="label">P1 / P2 Active</div><div class="metric">{int(metrics['urgent'] or 0)}</div></div><div class="card span3"><div class="label">Responders Active</div><div class="metric">{int(responder_count or 0)}</div></div>
<div class="card span8" id="active"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap"><div><h2>Active Incidents</h2><p class="muted">Priority and unassigned calls are surfaced first.</p></div><span class="status installed">{int(metrics['completed'] or 0)} completed</span></div><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Priority</th><th>Status</th><th>Service</th><th>Callsign</th><th>Location</th><th>Primary</th></tr></thead><tbody>{active_rows}</tbody></table></div></div>
<div class="card span4"><h2>Recent Activity</h2><div class="timeline">{event_html}</div><a class="btn secondary" href="/guild/{guild_id}/history" style="margin-top:8px">View Full History</a></div></div>'''
    return base.page(f"{guild_info['name']} · Operations", body, base.current_user(request))


async def guild_settings(request: Request, guild_id: int):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    try:
        roles = await base.discord_get(f"/guilds/{guild_id}/roles")
        channels = await base.discord_get(f"/guilds/{guild_id}/channels")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Discord API error: {exc.response.status_code}")

    settings, service_roles, log_channel, dispatch_channel, _active, _history, _stats = await base.load_dashboard_data(guild_id)
    selected_responder_ids = set(settings["responder_role_ids"] or []) if settings else set()
    request_channel_id = settings["request_channel_id"] if settings else None
    incident_category_id = settings["incident_category_id"] if settings else None
    selectable_roles = [r for r in roles if r.get("name") != "@everyone" and not r.get("managed")]
    text_channels = [c for c in channels if c.get("type") == 0]
    categories = [c for c in channels if c.get("type") == 4]

    role_options = "".join(base.option(r["id"], r["name"], int(r["id"]) in selected_responder_ids) for r in selectable_roles)
    category_options = '<option value="">Use / create Active Incidents</option>' + "".join(base.option(c["id"], c["name"], int(c["id"]) == incident_category_id) for c in categories)

    def channel_select(name, selected):
        opts = '<option value="">Not configured</option>' + "".join(base.option(c["id"], f'#{c["name"]}', int(c["id"]) == selected) for c in text_channels)
        return f'<select name="{name}">{opts}</select>'

    service_html = ""
    for key, label in base.SERVICES.items():
        selected = set(service_roles.get(key, []))
        opts = "".join(base.option(r["id"], r["name"], int(r["id"]) in selected) for r in selectable_roles)
        service_html += f'<label>{base.esc(label)} paging roles</label><select multiple name="service_{base.esc(key)}">{opts}</select>'

    csrf = base.esc(request.session.get("csrf"))
    body = f'''<div class="overview-head"><div><h2>Settings</h2><div class="muted">Discord routing, responder permissions, and paging configuration for {base.esc(guild_info['name'])}.</div></div></div><form method="post" action="/guild/{guild_id}/config"><input type="hidden" name="csrf" value="{csrf}"><div class="grid"><div class="card span6 settings-section"><h2>Responder & Paging Roles</h2><label>Responder roles</label><select multiple name="responder_roles">{role_options}</select><p class="muted">Members with these roles may use responder controls on any incident.</p>{service_html}</div><div class="card span6 settings-section"><h2>Channels & Incident Routing</h2><label>Request Assistance channel</label>{channel_select('request_channel', request_channel_id)}<label>Live Dispatch Board channel</label>{channel_select('dispatch_channel', dispatch_channel)}<label>Completed Rescue Log channel</label>{channel_select('log_channel', log_channel)}<label>Active Incident category</label><select name="incident_category">{category_options}</select><p class="muted">Moving Request Assistance or Dispatch Board posts a fresh panel/board in the selected channel. Existing messages are left in place.</p></div><div class="card span12"><button class="btn" type="submit">Save Configuration</button> <a class="btn secondary" href="/guild/{guild_id}">Cancel</a></div></div></form>'''
    return base.page(f"Settings · {guild_info['name']}", body, base.current_user(request))


async def find_incident(request: Request, guild_id: int, q: str = ""):
    base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    query = q.strip()[:120]
    if not query:
        return RedirectResponse(f"/guild/{guild_id}", status_code=303)

    match = re.fullmatch(r"(?:RESCUE[- ]?)?(\d+)", query, flags=re.IGNORECASE)
    if match:
        number = int(match.group(1))
        async with base.pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2",
                guild_id,
                number,
            )
        if exists:
            return RedirectResponse(f"/guild/{guild_id}/incident/{number}", status_code=303)

    return RedirectResponse(f"/guild/{guild_id}/history?q={quote(query)}", status_code=303)


for route in base.app.routes:
    if getattr(route, "path", None) == "/guild/{guild_id}" and "GET" in getattr(route, "methods", set()):
        route.endpoint = guild_overview
        break

base.app.add_api_route("/guild/{guild_id}/settings", guild_settings, methods=["GET"], response_class=HTMLResponse)
base.app.add_api_route("/guild/{guild_id}/find", find_incident, methods=["GET"])

app = base.app