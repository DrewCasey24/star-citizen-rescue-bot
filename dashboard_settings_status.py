"""Operational settings page with live Discord configuration validity at a glance."""

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
.config-check.stale{border-color:rgba(255,107,120,.25);background:rgba(82,25,34,.16)}
.config-check.stale .config-dot{background:#ff6b78;box-shadow:0 0 8px rgba(255,107,120,.28)}
.config-check.stale .config-value{color:#ffb1b9}
.config-health{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.config-health .status{font-size:11px}
.settings-card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}
.settings-card-head h2{margin-bottom:3px}
.settings-card-head .muted{font-size:12px}
.settings-service{padding:10px 0;border-top:1px solid rgba(116,153,196,.10)}
.settings-service:first-of-type{border-top:0}
.settings-actions{position:sticky;bottom:12px;z-index:8;display:flex;align-items:center;justify-content:space-between;gap:12px;background:rgba(8,17,29,.92);backdrop-filter:blur(14px)}
.status.stale-config{color:#ffb1b9;background:rgba(169,54,67,.18);border-color:rgba(255,107,120,.22)}
@media(max-width:1000px){.config-summary{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){.config-summary{grid-template-columns:1fr}.settings-actions{position:static;flex-wrap:wrap}}
'''


def _status_card(name, value, state):
    cls = "config-check" if state == "valid" else f"config-check {state}"
    return f'<div class="{cls}"><span class="config-name">{base.esc(name)}</span><span class="config-value"><span class="config-dot"></span>{base.esc(value)}</span></div>'


def _state_label(state):
    return {
        "valid": ("Configured & valid", "installed"),
        "stale": ("Configured but missing/stale", "stale-config"),
        "missing": ("Not configured", "not-installed"),
    }[state]


def _resource_state(saved_value, valid_ids):
    if not saved_value:
        return "missing"
    return "valid" if int(saved_value) in valid_ids else "stale"


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
    valid_role_ids = {int(r["id"]) for r in selectable_roles}
    valid_text_ids = {int(c["id"]) for c in text_channels}
    valid_category_ids = {int(c["id"]) for c in categories}
    role_names = {int(r["id"]): r["name"] for r in selectable_roles}
    channel_names = {int(c["id"]): c["name"] for c in text_channels}
    category_names = {int(c["id"]): c["name"] for c in categories}

    stale_responder_ids = sorted(int(rid) for rid in selected_responder_ids if int(rid) not in valid_role_ids)
    role_options = "".join(base.option(r["id"], r["name"], int(r["id"]) in selected_responder_ids) for r in selectable_roles)
    role_options += "".join(f'<option value="{rid}" selected>Missing role (ID {rid})</option>' for rid in stale_responder_ids)

    category_options = '<option value="">Use / create Active Incidents</option>'
    category_options += "".join(base.option(c["id"], c["name"], int(c["id"]) == incident_category_id) for c in categories)
    if incident_category_id and int(incident_category_id) not in valid_category_ids:
        category_options += f'<option value="{int(incident_category_id)}" selected>Missing category (ID {int(incident_category_id)})</option>'

    def channel_select(name, selected):
        opts = '<option value="">Not configured</option>' + "".join(base.option(c["id"], f'#{c["name"]}', int(c["id"]) == selected) for c in text_channels)
        if selected and int(selected) not in valid_text_ids:
            opts += f'<option value="{int(selected)}" selected>Missing channel (ID {int(selected)})</option>'
        return f'<select name="{name}">{opts}</select>'

    service_html = ""
    service_states = {}
    for key, label in base.SERVICES.items():
        selected = set(service_roles.get(key, []))
        valid_selected = {int(rid) for rid in selected if int(rid) in valid_role_ids}
        stale_selected = sorted(int(rid) for rid in selected if int(rid) not in valid_role_ids)
        if not selected:
            state = "missing"
        elif stale_selected:
            state = "stale"
        else:
            state = "valid"
        service_states[key] = state
        opts = "".join(base.option(r["id"], r["name"], int(r["id"]) in selected) for r in selectable_roles)
        opts += "".join(f'<option value="{rid}" selected>Missing role (ID {rid})</option>' for rid in stale_selected)
        label_text, label_class = _state_label(state)
        count_text = f"{len(valid_selected)} valid role{'s' if len(valid_selected) != 1 else ''}"
        if stale_selected:
            count_text += f", {len(stale_selected)} stale"
        service_html += f'<div class="settings-service"><div class="settings-card-head"><div><label>{base.esc(label)} paging roles</label><div class="muted">{base.esc(count_text)}</div></div><span class="status {label_class}">{base.esc(label_text)}</span></div><select multiple name="service_{base.esc(key)}">{opts}</select></div>'

    request_state = _resource_state(request_channel_id, valid_text_ids)
    dispatch_state = _resource_state(dispatch_channel, valid_text_ids)
    log_state = _resource_state(log_channel, valid_text_ids)
    responder_state = "missing" if not selected_responder_ids else ("stale" if stale_responder_ids else "valid")
    services_state = "valid" if all(v == "valid" for v in service_states.values()) else ("stale" if any(v == "stale" for v in service_states.values()) else "missing")
    category_state = "valid" if not incident_category_id or int(incident_category_id) in valid_category_ids else "stale"

    states = [request_state, dispatch_state, log_state, responder_state, services_state, category_state]
    valid_count = sum(state == "valid" for state in states)
    stale_count = sum(state == "stale" for state in states)
    fully_configured = all(state == "valid" for state in states)

    def saved_channel_text(saved, state):
        if state == "missing":
            return "Not configured"
        if state == "stale":
            return f"Missing channel (ID {int(saved)})"
        return f"#{channel_names[int(saved)]}"

    summary = "".join([
        _status_card("Request Assistance", saved_channel_text(request_channel_id, request_state), request_state),
        _status_card("Dispatch Board", saved_channel_text(dispatch_channel, dispatch_state), dispatch_state),
        _status_card("Rescue Log", saved_channel_text(log_channel, log_state), log_state),
        _status_card("Incident Category", "Active Incidents (automatic)" if not incident_category_id else (category_names.get(int(incident_category_id)) or f"Missing category (ID {int(incident_category_id)})"), category_state),
        _status_card("Responder Access", "Not configured" if responder_state == "missing" else (f"{len(selected_responder_ids)-len(stale_responder_ids)} valid, {len(stale_responder_ids)} stale" if stale_responder_ids else f"{len(selected_responder_ids)} role{'s' if len(selected_responder_ids) != 1 else ''}"), responder_state),
        _status_card("Service Paging", f"{sum(v == 'valid' for v in service_states.values())}/{len(base.SERVICES)} services valid" + (f" • {sum(v == 'stale' for v in service_states.values())} stale" if any(v == "stale" for v in service_states.values()) else ""), services_state),
    ])

    csrf = base.esc(request.session.get("csrf"))
    if fully_configured:
        health_label, health_class = "Ready for operations", "installed"
    elif stale_count:
        health_label, health_class = f"{stale_count} stale configuration area{'s' if stale_count != 1 else ''} need attention", "stale-config"
    else:
        health_label, health_class = f"{valid_count}/{len(states)} configuration checks ready", "not-installed"

    responder_label, responder_class = _state_label(responder_state)
    services_label, services_class = _state_label(services_state)
    body = f'''<div class="overview-head"><div><h2>Settings</h2><div class="muted">Discord routing, responder permissions, and paging configuration for {base.esc(guild_info['name'])}.</div></div><div class="config-health"><span class="status {health_class}">{base.esc(health_label)}</span></div></div>
<div class="config-summary">{summary}</div>
<form method="post" action="/guild/{guild_id}/config"><input type="hidden" name="csrf" value="{csrf}"><div class="grid">
<div class="card span6 settings-section"><div class="settings-card-head"><div><h2>Responder Access</h2><div class="muted">Who can operate incident response controls.</div></div><span class="status {responder_class}">{base.esc(responder_label)}</span></div><label>Responder roles</label><select multiple name="responder_roles">{role_options}</select><p class="muted">Members with these roles may use responder controls on any incident. Missing saved roles remain visible until replaced or repaired.</p></div>
<div class="card span6 settings-section"><div class="settings-card-head"><div><h2>Channels & Incident Routing</h2><div class="muted">Where requests, live dispatch, completed rescues, and incident channels live.</div></div></div><label>Request Assistance channel</label>{channel_select('request_channel', request_channel_id)}<label>Live Dispatch Board channel</label>{channel_select('dispatch_channel', dispatch_channel)}<label>Completed Rescue Log channel</label>{channel_select('log_channel', log_channel)}<label>Active Incident category</label><select name="incident_category">{category_options}</select><p class="muted">Saved resources are checked against Discord live. Missing channels/categories are marked stale instead of being treated as configured.</p></div>
<div class="card span12 settings-section"><div class="settings-card-head"><div><h2>Service Paging</h2><div class="muted">Choose the Discord roles paged for each rescue service.</div></div><span class="status {services_class}">{base.esc(services_label)}</span></div>{service_html}</div>
<div class="card span12 settings-actions"><div class="muted">Changes take effect after saving and are picked up by the bot automatically.</div><div><button class="btn" type="submit">Save Configuration</button> <a class="btn secondary" href="/guild/{guild_id}/repair-config">Repair Stale References</a> <a class="btn secondary" href="/guild/{guild_id}">Cancel</a></div></div>
</div></form>'''
    response = base.page(f"Settings · {guild_info['name']}", body, base.current_user(request))
    markup = response.body.decode("utf-8").replace("</style>", SETTINGS_CSS + "\n</style>", 1)
    return HTMLResponse(markup, status_code=response.status_code)


for route in list(base.app.router.routes):
    if getattr(route, "path", None) == "/guild/{guild_id}/settings" and "GET" in getattr(route, "methods", set()):
        base.app.router.routes.remove(route)
base.app.add_api_route("/guild/{guild_id}/settings", guild_settings_status, methods=["GET"], response_class=HTMLResponse)

app = base.app
