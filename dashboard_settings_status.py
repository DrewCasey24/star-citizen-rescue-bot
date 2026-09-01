"""Operational settings page with configuration health at a glance."""

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


SETTINGS_CSS = r'''
.config-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:16px}
.config-check{padding:12px 13px;border:1px solid rgba(116,153,196,.15);border-radius:11px;background:rgba(8,17,29,.52)}
.config-check .config-name{display:block;color:#8199b4;font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px}
.config-check .config-value{display:flex;align-items:center;gap:7px;color:#dbe9f7;font-size:13px;font-weight:700}
.config-dot{width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:#45d49b;box-shadow:0 0 8px rgba(69,212,155,.35)}
.config-check.missing .config-dot{background:#ffb45d;box-shadow:0 0 8px rgba(255,180,93,.25)}
.config-check.missing .config-value{color:#ffd39a}
.config-health{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.config-health .status{font-size:11px}
.settings-card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}
.settings-card-head h2{margin-bottom:3px}
.settings-card-head .muted{font-size:12px}
.settings-service{padding:10px 0;border-top:1px solid rgba(116,153,196,.10)}
.settings-service:first-of-type{border-top:0}
.settings-actions{position:sticky;bottom:12px;z-index:8;display:flex;align-items:center;justify-content:space-between;gap:12px;background:rgba(8,17,29,.92);backdrop-filter:blur(14px)}
@media(max-width:1000px){.config-summary{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){.config-summary{grid-template-columns:1fr}.settings-actions{position:static;flex-wrap:wrap}}
'''


def _configured(value):
    return value is not None and value != "" and value != [] and value != set()


def _status_card(name, value, configured):
    cls = "config-check" if configured else "config-check missing"
    return f'<div class="{cls}"><span class="config-name">{base.esc(name)}</span><span class="config-value"><span class="config-dot"></span>{base.esc(value)}</span></div>'


async def guild_settings_status(request: Request, guild_id: int):
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

    role_names = {int(r["id"]): r["name"] for r in selectable_roles}
    channel_names = {int(c["id"]): c["name"] for c in text_channels}
    category_names = {int(c["id"]): c["name"] for c in categories}

    role_options = "".join(base.option(r["id"], r["name"], int(r["id"]) in selected_responder_ids) for r in selectable_roles)
    category_options = '<option value="">Use / create Active Incidents</option>' + "".join(base.option(c["id"], c["name"], int(c["id"]) == incident_category_id) for c in categories)

    def channel_select(name, selected):
        opts = '<option value="">Not configured</option>' + "".join(base.option(c["id"], f'#{c["name"]}', int(c["id"]) == selected) for c in text_channels)
        return f'<select name="{name}">{opts}</select>'

    service_html = ""
    configured_services = 0
    for key, label in base.SERVICES.items():
        selected = set(service_roles.get(key, []))
        if selected:
            configured_services += 1
        opts = "".join(base.option(r["id"], r["name"], int(r["id"]) in selected) for r in selectable_roles)
        state = f'{len(selected)} role{"s" if len(selected) != 1 else ""}' if selected else "Not configured"
        service_html += f'<div class="settings-service"><div class="settings-card-head"><div><label>{base.esc(label)} paging roles</label></div><span class="status {"installed" if selected else "not-installed"}">{base.esc(state)}</span></div><select multiple name="service_{base.esc(key)}">{opts}</select></div>'

    request_ok = _configured(request_channel_id)
    dispatch_ok = _configured(dispatch_channel)
    log_ok = _configured(log_channel)
    responder_ok = bool(selected_responder_ids)
    services_ok = configured_services == len(base.SERVICES)
    # A blank category intentionally means the bot may use/create the default Active Incidents category.
    category_ok = True
    checks = [request_ok, dispatch_ok, log_ok, responder_ok, services_ok, category_ok]
    complete_count = sum(checks)
    fully_configured = all(checks)

    summary = "".join([
        _status_card("Request Assistance", f'#{channel_names.get(int(request_channel_id), "Configured")}' if request_ok else "Not configured", request_ok),
        _status_card("Dispatch Board", f'#{channel_names.get(int(dispatch_channel), "Configured")}' if dispatch_ok else "Not configured", dispatch_ok),
        _status_card("Rescue Log", f'#{channel_names.get(int(log_channel), "Configured")}' if log_ok else "Not configured", log_ok),
        _status_card("Incident Category", category_names.get(int(incident_category_id), "Active Incidents") if incident_category_id else "Active Incidents (automatic)", True),
        _status_card("Responder Access", f'{len(selected_responder_ids)} role{"s" if len(selected_responder_ids) != 1 else ""}' if responder_ok else "Not configured", responder_ok),
        _status_card("Service Paging", f'{configured_services}/{len(base.SERVICES)} services configured', services_ok),
    ])

    csrf = base.esc(request.session.get("csrf"))
    health_label = "Ready for operations" if fully_configured else f"{complete_count}/{len(checks)} configuration checks ready"
    health_class = "installed" if fully_configured else "not-installed"
    body = f'''<div class="overview-head"><div><h2>Settings</h2><div class="muted">Discord routing, responder permissions, and paging configuration for {base.esc(guild_info['name'])}.</div></div><div class="config-health"><span class="status {health_class}">{base.esc(health_label)}</span></div></div>
<div class="config-summary">{summary}</div>
<form method="post" action="/guild/{guild_id}/config"><input type="hidden" name="csrf" value="{csrf}"><div class="grid">
<div class="card span6 settings-section"><div class="settings-card-head"><div><h2>Responder Access</h2><div class="muted">Who can operate incident response controls.</div></div><span class="status {"installed" if responder_ok else "not-installed"}">{len(selected_responder_ids)} selected</span></div><label>Responder roles</label><select multiple name="responder_roles">{role_options}</select><p class="muted">Members with these roles may use responder controls on any incident.</p></div>
<div class="card span6 settings-section"><div class="settings-card-head"><div><h2>Channels & Incident Routing</h2><div class="muted">Where requests, live dispatch, completed rescues, and incident channels live.</div></div></div><label>Request Assistance channel</label>{channel_select('request_channel', request_channel_id)}<label>Live Dispatch Board channel</label>{channel_select('dispatch_channel', dispatch_channel)}<label>Completed Rescue Log channel</label>{channel_select('log_channel', log_channel)}<label>Active Incident category</label><select name="incident_category">{category_options}</select><p class="muted">Moving Request Assistance or Dispatch Board posts a fresh panel/board in the selected channel. Existing messages are left in place.</p></div>
<div class="card span12 settings-section"><div class="settings-card-head"><div><h2>Service Paging</h2><div class="muted">Choose the Discord roles paged for each rescue service.</div></div><span class="status {"installed" if services_ok else "not-installed"}">{configured_services}/{len(base.SERVICES)} configured</span></div>{service_html}</div>
<div class="card span12 settings-actions"><div class="muted">Changes take effect after saving and are picked up by the bot automatically.</div><div><button class="btn" type="submit">Save Configuration</button> <a class="btn secondary" href="/guild/{guild_id}">Cancel</a></div></div>
</div></form>'''
    response = base.page(f"Settings · {guild_info['name']}", body, base.current_user(request))
    markup = response.body.decode("utf-8").replace("</style>", SETTINGS_CSS + "\n</style>", 1)
    return HTMLResponse(markup, status_code=response.status_code)


# Re-register the route so FastAPI's compiled handler uses this endpoint.
for route in list(base.app.router.routes):
    if getattr(route, "path", None) == "/guild/{guild_id}/settings" and "GET" in getattr(route, "methods", set()):
        base.app.router.routes.remove(route)
        break
base.app.add_api_route("/guild/{guild_id}/settings", guild_settings_status, methods=["GET"], response_class=HTMLResponse)

app = base.app
