import html
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET")
COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "true").lower() != "false"

DISCORD_API = "https://discord.com/api/v10"
MANAGE_GUILD = 0x20
BOT_PERMISSIONS = 397284568080

SERVICES = {
    "medical": "Medical Rescue",
    "search-rescue": "Search & Rescue",
    "repair-refuel": "Repair / Refuel",
    "security": "Security / Escort",
    "recovery-transport": "Recovery / Transport",
}
PRIORITIES = {"critical": "P1 Critical", "urgent": "P2 Urgent", "standard": "P3 Standard"}
PRIORITY_ORDER = ["standard", "urgent", "critical"]
PRIORITY_DISCORD = {
    "critical": "🔴 P1 — Critical",
    "urgent": "🟠 P2 — Urgent",
    "standard": "🟢 P3 — Standard",
}
STATUSES = {
    "awaiting_responder": "Awaiting Responder",
    "en_route": "En Route",
    "on_scene": "On Scene",
    "backup_requested": "Backup Requested",
    "closed": "Closed",
}
STATUS_DISCORD = {
    "awaiting_responder": "🔴 Awaiting Responder",
    "en_route": "🟡 En Route",
    "on_scene": "🟢 On Scene",
    "backup_requested": "🟠 Backup Requested",
    "closed": "⚫ Closed",
}

app = FastAPI(title="Star Citizen Rescue Dashboard")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET or secrets.token_urlsafe(48),
    https_only=COOKIE_SECURE,
    same_site="lax",
    max_age=60 * 60 * 12,
)
pool = None


def dashboard_base_url():
    if not REDIRECT_URI:
        return ""
    suffix = "/oauth/callback"
    return REDIRECT_URI[:-len(suffix)].rstrip("/") if REDIRECT_URI.endswith(suffix) else REDIRECT_URI.rstrip("/")


def dashboard_button_components():
    base = dashboard_base_url()
    if not base:
        return []
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 5,
                    "label": "Open Web Dashboard",
                    "emoji": {"name": "🌐"},
                    "url": base,
                }
            ],
        }
    ]


@app.on_event("startup")
async def startup():
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required for the rescue dashboard.")
    if not BOT_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is required for the rescue dashboard.")
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI or not SESSION_SECRET:
        raise RuntimeError(
            "DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI, and DASHBOARD_SESSION_SECRET are required."
        )
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=15)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rescue_guild_settings (
                guild_id BIGINT PRIMARY KEY,
                responder_role_ids BIGINT[] NOT NULL DEFAULT '{}',
                request_channel_id BIGINT,
                incident_category_id BIGINT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS rescue_service_role_settings (
                guild_id BIGINT NOT NULL,
                service TEXT NOT NULL,
                role_ids BIGINT[] NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY(guild_id, service)
            );
            CREATE TABLE IF NOT EXISTS rescue_incident_events (
                id BIGSERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                incident_number BIGINT NOT NULL,
                channel_id BIGINT,
                event_type TEXT NOT NULL,
                actor_id BIGINT,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS rescue_incident_events_incident_idx
            ON rescue_incident_events(guild_id, incident_number, created_at, id);
            """
        )


@app.on_event("shutdown")
async def shutdown():
    if pool:
        await pool.close()


def esc(value):
    return html.escape(str(value or ""), quote=True)


def format_dt(value):
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def duration(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def page(title, body, user=None):
    user_html = ""
    if user:
        user_html = f'<div class="user">{esc(user.get("global_name") or user.get("username"))} · <a href="/logout">Sign out</a></div>'
    return HTMLResponse(
        f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · SC Rescue</title><style>
:root{{--bg:#090d14;--panel:#111827;--line:#263247;--text:#e7edf7;--muted:#91a0b8}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#080b11,#0d1420 55%,#0b1019);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;min-height:100vh}}a{{color:#8bc0ff;text-decoration:none}}.wrap{{max-width:1240px;margin:0 auto;padding:28px 20px 60px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:26px}}h1{{font-size:24px;margin:0}}h2{{font-size:17px;margin:0 0 14px}}.brand small,.muted,.user{{color:var(--muted)}}.user{{font-size:14px}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}.card{{background:rgba(17,24,39,.92);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 14px 35px rgba(0,0,0,.18)}}.span3{{grid-column:span 3}}.span4{{grid-column:span 4}}.span6{{grid-column:span 6}}.span8{{grid-column:span 8}}.span12{{grid-column:span 12}}.metric{{font-size:30px;font-weight:750;margin-top:4px}}.label{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}.pill,.status{{display:inline-block;padding:4px 8px;border-radius:999px;font-size:12px}}.p1{{background:#52242c;color:#ffb4bd}}.p2,.not-installed{{background:#50351d;color:#ffd49a}}.p3,.installed{{background:#153c32;color:#8df0c5}}label{{display:block;color:var(--muted);font-size:12px;margin:12px 0 6px}}select,input{{width:100%;background:#0c1320;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px;min-height:42px}}select[multiple]{{min-height:120px}}.btn{{display:inline-block;border:0;border-radius:9px;background:#2b74c8;color:white;padding:11px 16px;font-weight:650;cursor:pointer}}.btn.secondary{{background:#263247}}.btn.install,.btn.success{{background:#237a57}}.btn.warn{{background:#a65b18}}.btn.danger{{background:#a93643}}.btn:disabled{{opacity:.45;cursor:not-allowed}}.notice{{padding:12px 14px;border:1px solid #315f45;background:#122d22;border-radius:10px;color:#a8efc8;margin-bottom:16px}}.guild{{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 0;border-bottom:1px solid var(--line)}}.guild:last-child{{border:0}}.guild-actions{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.section-nav{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}.section-nav a{{padding:8px 10px;background:#111827;border:1px solid var(--line);border-radius:8px}}.kv{{display:grid;grid-template-columns:150px 1fr;gap:9px 16px;font-size:14px}}.kv div:nth-child(odd){{color:var(--muted)}}.timeline{{border-left:2px solid var(--line);padding-left:22px;margin:6px 0 0 8px}}.event{{position:relative;padding:0 0 18px 4px}}.event:before{{content:'';position:absolute;width:10px;height:10px;border-radius:50%;background:#5da8ff;left:-28px;top:5px;box-shadow:0 0 0 4px #111827}}.event-title{{font-weight:700}}.event-meta{{font-size:12px;color:var(--muted);margin-top:3px}}.situation{{white-space:pre-wrap;line-height:1.55}}.incident-link{{font-weight:700}}.ledger-badge{{font-size:11px;padding:3px 7px;border-radius:999px;background:#1b3150;color:#9fcaff;margin-left:7px}}.control-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.control-grid form{{margin:0}}.control-grid .btn{{width:100%}}.control-note{{margin-top:12px;font-size:12px;color:var(--muted);line-height:1.5}}.filter-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.filter-actions{{display:flex;gap:10px;align-items:end;flex-wrap:wrap}}.pagination{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:16px;flex-wrap:wrap}}.pagination .pages{{display:flex;gap:8px;align-items:center}}@media(max-width:850px){{.span3,.span4,.span6,.span8{{grid-column:span 12}}header,.guild{{align-items:flex-start;flex-direction:column}}.kv{{grid-template-columns:1fr}}.control-grid,.filter-grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap"><header><div class="brand"><h1>🚨 Star Citizen Rescue Dispatch</h1><small>Operations Dashboard</small></div>{user_html}</header>{body}</div></body></html>'''
    )


def current_user(request):
    return request.session.get("user")


def manageable_guilds(request):
    return request.session.get("guilds") or []


def require_guild_access(request, guild_id):
    if not current_user(request):
        raise HTTPException(status_code=401)
    for guild in manageable_guilds(request):
        if str(guild.get("id")) == str(guild_id) and guild.get("can_manage"):
            return guild
    raise HTTPException(status_code=403, detail="Manage Server permission is required.")


def require_csrf(request, submitted):
    expected = str(request.session.get("csrf") or "")
    submitted = str(submitted or "")
    if not submitted or not expected or not secrets.compare_digest(submitted, expected):
        raise HTTPException(status_code=400, detail="Invalid CSRF token.")


def bot_install_url(guild_id):
    return "https://discord.com/oauth2/authorize?" + urlencode(
        {
            "client_id": CLIENT_ID,
            "scope": "bot applications.commands",
            "permissions": str(BOT_PERMISSIONS),
            "guild_id": str(guild_id),
            "disable_guild_select": "true",
        }
    )


async def discord_get(path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {"Authorization": f"Bot {BOT_TOKEN}"}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{DISCORD_API}{path}", headers=headers)
        response.raise_for_status()
        return response.json()


async def discord_post(path, payload):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def discord_patch(path, payload):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(
            f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json() if response.content else None


async def discord_put(path, payload):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.put(
            f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json() if response.content else None


async def bot_guild_ids():
    installed = set()
    after = None
    while True:
        path = "/users/@me/guilds?limit=200" + (f"&after={after}" if after else "")
        guilds = await discord_get(path)
        installed.update(str(g["id"]) for g in guilds)
        if len(guilds) < 200:
            break
        after = guilds[-1]["id"]
    return installed


async def require_bot_installed(guild_id):
    try:
        await discord_get(f"/guilds/{guild_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (403, 404):
            raise HTTPException(status_code=409, detail="The rescue bot is not installed in this Discord server.")
        raise HTTPException(status_code=502, detail=f"Discord API error: {exc.response.status_code}")


async def member_names(guild_id, user_ids):
    names = {}
    for uid in {int(v) for v in user_ids if v}:
        try:
            member = await discord_get(f"/guilds/{guild_id}/members/{uid}")
            user = member.get("user", {})
            names[uid] = member.get("nick") or user.get("global_name") or user.get("username") or "Discord User"
        except httpx.HTTPStatusError:
            names[uid] = "Discord User"
    return names


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = current_user(request)
    if not user:
        return page(
            "Sign In",
            '<div class="card" style="max-width:620px"><h2>Rescue Operations Dashboard</h2><p class="muted">Sign in with Discord to view rescue operations and manage configuration. Server configuration requires Manage Server permission.</p><a class="btn" href="/login">Sign in with Discord</a></div>',
        )
    guilds = [g for g in manageable_guilds(request) if g.get("can_manage")]
    try:
        installed_ids = await bot_guild_ids()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Unable to check bot installations: Discord API {exc.response.status_code}")
    rows = []
    for guild in guilds:
        guild_id = str(guild["id"])
        if guild_id in installed_ids:
            action = f'<div class="guild-actions"><span class="status installed">Bot Installed</span><a class="btn secondary" href="/guild/{esc(guild_id)}">Open Dashboard</a></div>'
        else:
            action = f'<div class="guild-actions"><span class="status not-installed">Bot Not Installed</span><a class="btn install" href="{esc(bot_install_url(guild_id))}" target="_blank" rel="noopener">Install Bot</a></div>'
        rows.append(f'<div class="guild"><div><strong>{esc(guild["name"])}</strong></div>{action}</div>')
    rows_html = "".join(rows) or '<p class="muted">No servers with Manage Server permission were found for this login.</p>'
    return page(
        "Servers",
        '<div class="card"><h2>Select a server</h2><p class="muted">Installed servers can be managed immediately. For another server you manage, install the bot first and then refresh this page.</p>' + rows_html + '</div>',
        user,
    )


@app.get("/login")
async def login(request: Request):
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
        }
    )
    return RedirectResponse("https://discord.com/oauth2/authorize?" + params)


@app.get("/oauth/callback")
async def oauth_callback(request: Request, code: str, state: str):
    if not secrets.compare_digest(state, request.session.pop("oauth_state", "")):
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")
    async with httpx.AsyncClient(timeout=15) as client:
        token_response = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]
    user = await discord_get("/users/@me", access_token)
    guilds = await discord_get("/users/@me/guilds", access_token)
    request.session["user"] = {
        "id": user["id"],
        "username": user.get("username"),
        "global_name": user.get("global_name"),
    }
    request.session["guilds"] = [
        {
            "id": g["id"],
            "name": g["name"],
            "can_manage": bool(int(g.get("permissions", "0")) & MANAGE_GUILD) or bool(g.get("owner")),
        }
        for g in guilds
    ]
    request.session["csrf"] = secrets.token_urlsafe(24)
    return RedirectResponse("/")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


async def load_dashboard_data(guild_id):
    async with pool.acquire() as conn:
        settings = await conn.fetchrow(
            "SELECT responder_role_ids,request_channel_id,incident_category_id FROM rescue_guild_settings WHERE guild_id=$1",
            guild_id,
        )
        service_rows = await conn.fetch(
            "SELECT service,role_ids FROM rescue_service_role_settings WHERE guild_id=$1",
            guild_id,
        )
        log_channel = await conn.fetchval("SELECT channel_id FROM rescue_log_channels WHERE guild_id=$1", guild_id)
        dispatch_channel = await conn.fetchval("SELECT channel_id FROM rescue_dispatch_boards WHERE guild_id=$1", guild_id)
        active = await conn.fetch(
            "SELECT incident_number,callsign,service,location,priority,status,primary_responder_id,created_at FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed' ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2 ELSE 3 END, incident_number ASC LIMIT 50",
            guild_id,
        )
        history = await conn.fetch(
            "SELECT incident_number,callsign,service,priority,primary_responder_id,created_at,responded_at,closed_at FROM rescue_incidents WHERE guild_id=$1 AND status='closed' ORDER BY closed_at DESC NULLS LAST LIMIT 20",
            guild_id,
        )
        stats = await conn.fetchrow(
            "SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE status<>'closed') AS active,COUNT(*) FILTER (WHERE status='closed') AS closed,COUNT(*) FILTER (WHERE priority='critical') AS p1,AVG(EXTRACT(EPOCH FROM (responded_at-created_at))) FILTER (WHERE responded_at IS NOT NULL) AS avg_response FROM rescue_incidents WHERE guild_id=$1",
            guild_id,
        )
    return settings, {r["service"]: list(r["role_ids"] or []) for r in service_rows}, log_channel, dispatch_channel, active, history, stats


def option(value, label, selected=False):
    return f'<option value="{esc(value)}"{" selected" if selected else ""}>{esc(label)}</option>'


@app.get("/guild/{guild_id}", response_class=HTMLResponse)
async def guild_dashboard(request: Request, guild_id: int, saved: int = 0):
    guild_info = require_guild_access(request, guild_id)
    await require_bot_installed(guild_id)
    try:
        roles = await discord_get(f"/guilds/{guild_id}/roles")
        channels = await discord_get(f"/guilds/{guild_id}/channels")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Discord API error: {exc.response.status_code}")
    settings, service_roles, log_channel, dispatch_channel, active, history, stats = await load_dashboard_data(guild_id)
    selected_responder_ids = set(settings["responder_role_ids"] or []) if settings else set()
    request_channel_id = settings["request_channel_id"] if settings else None
    incident_category_id = settings["incident_category_id"] if settings else None
    selectable_roles = [r for r in roles if r.get("name") != "@everyone" and not r.get("managed")]
    text_channels = [c for c in channels if c.get("type") == 0]
    categories = [c for c in channels if c.get("type") == 4]
    role_options = "".join(option(r["id"], r["name"], int(r["id"]) in selected_responder_ids) for r in selectable_roles)
    category_options = '<option value="">Use / create Active Incidents</option>' + "".join(option(c["id"], c["name"], int(c["id"]) == incident_category_id) for c in categories)

    def channel_select(name, selected):
        opts = '<option value="">Not configured</option>' + "".join(option(c["id"], f'#{c["name"]}', int(c["id"]) == selected) for c in text_channels)
        return f'<select name="{name}">{opts}</select>'

    service_html = ""
    for key, label in SERVICES.items():
        selected = set(service_roles.get(key, []))
        opts = "".join(option(r["id"], r["name"], int(r["id"]) in selected) for r in selectable_roles)
        service_html += f'<label>{esc(label)} paging roles</label><select multiple name="service_{esc(key)}">{opts}</select>'

    active_rows = "".join(
        f'<tr><td><a class="incident-link" href="/guild/{guild_id}/incident/{r["incident_number"]}">RESCUE-{r["incident_number"]:04d}</a></td><td><span class="pill {"p1" if r["priority"]=="critical" else "p2" if r["priority"]=="urgent" else "p3"}">{esc(PRIORITIES.get(r["priority"], r["priority"]))}</span></td><td>{esc(STATUSES.get(r["status"], r["status"]))}</td><td>{esc(SERVICES.get(r["service"], r["service"]))}</td><td>{esc(r["callsign"])}</td><td>{esc(r["location"])}</td><td>{"Assigned" if r["primary_responder_id"] else "Unassigned"}</td></tr>'
        for r in active
    ) or '<tr><td colspan="7" class="muted">No active incidents.</td></tr>'
    history_rows = "".join(
        f'<tr><td><a class="incident-link" href="/guild/{guild_id}/incident/{r["incident_number"]}">RESCUE-{r["incident_number"]:04d}</a></td><td>{esc(SERVICES.get(r["service"], r["service"]))}</td><td>{esc(PRIORITIES.get(r["priority"], r["priority"]))}</td><td>{esc(r["callsign"])}</td><td>{duration((r["responded_at"]-r["created_at"]).total_seconds()) if r["responded_at"] else "—"}</td><td>{format_dt(r["closed_at"])}</td></tr>'
        for r in history
    ) or '<tr><td colspan="6" class="muted">No completed incidents.</td></tr>'
    notice = '<div class="notice">Configuration saved. The Discord bot refreshes responder/category settings within about 10 seconds.</div>' if saved else ""
    csrf = esc(request.session.get("csrf"))
    body = f'''{notice}<div class="section-nav"><a href="#overview">Overview</a><a href="#active">Active Incidents</a><a href="/guild/{guild_id}/history">Search History</a><a href="#config">Configuration</a></div><div class="grid" id="overview"><div class="card span3"><div class="label">Total Incidents</div><div class="metric">{stats['total']}</div></div><div class="card span3"><div class="label">Active</div><div class="metric">{stats['active']}</div></div><div class="card span3"><div class="label">Completed</div><div class="metric">{stats['closed']}</div></div><div class="card span3"><div class="label">Avg Claim Time</div><div class="metric">{duration(stats['avg_response'])}</div></div><div class="card span12" id="active"><h2>Active Incidents</h2><p class="muted">Click an incident number to open its detailed command view.</p><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Priority</th><th>Status</th><th>Service</th><th>Callsign</th><th>Location</th><th>Primary</th></tr></thead><tbody>{active_rows}</tbody></table></div></div><div class="card span12" id="history"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap"><h2 style="margin:0">Recent Rescue History</h2><a class="btn secondary" href="/guild/{guild_id}/history">Search Full History</a></div><div style="overflow:auto;margin-top:12px"><table><thead><tr><th>Incident</th><th>Service</th><th>Priority</th><th>Callsign</th><th>Claim Time</th><th>Closed</th></tr></thead><tbody>{history_rows}</tbody></table></div></div><div class="card span12" id="config"><h2>Discord Configuration</h2><p class="muted">Only users with Manage Server permission can access this page. Role and incident-category changes are database-backed and picked up by the bot automatically.</p><form method="post" action="/guild/{guild_id}/config"><input type="hidden" name="csrf" value="{csrf}"><div class="grid"><div class="span6"><label>Responder roles (may use controls on any incident)</label><select multiple name="responder_roles">{role_options}</select><p class="muted">Select one or more roles.</p>{service_html}</div><div class="span6"><label>Request Assistance channel</label>{channel_select('request_channel', request_channel_id)}<label>Live Dispatch Board channel</label>{channel_select('dispatch_channel', dispatch_channel)}<label>Completed Rescue Log channel</label>{channel_select('log_channel', log_channel)}<label>Active Incident category</label><select name="incident_category">{category_options}</select><p class="muted">Moving the Request or Dispatch channel posts a fresh panel/board in the newly selected channel. Old messages are left in place so nothing is deleted unexpectedly.</p></div></div><div style="margin-top:18px"><button class="btn" type="submit">Save Configuration</button> <a class="btn secondary" href="/">Back to Servers</a></div></form></div></div>'''
    return page(guild_info["name"], body, current_user(request))


@app.get("/guild/{guild_id}/history", response_class=HTMLResponse)
async def rescue_history_page(
    request: Request,
    guild_id: int,
    q: str = "",
    service: str = "",
    priority: str = "",
    status: str = "",
    responder: str = "",
    date_from: str = "",
    date_to: str = "",
    page_num: int = 1,
):
    guild_info = require_guild_access(request, guild_id)
    await require_bot_installed(guild_id)
    q = q.strip()[:120]
    service = service if service in SERVICES else ""
    priority = priority if priority in PRIORITIES else ""
    status = status if status in STATUSES else ""
    page_num = max(1, page_num)
    responder_id = int(responder) if responder.isdigit() else None
    from_dt = None
    to_dt = None
    try:
        if date_from:
            from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        if date_to:
            to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid history date filter.")

    conditions = ["ri.guild_id=$1"]
    args = [guild_id]

    def add_arg(value):
        args.append(value)
        return f"${len(args)}"

    if q:
        p = add_arg(f"%{q}%")
        conditions.append(f"(ri.incident_number::text ILIKE {p} OR ri.callsign ILIKE {p} OR ri.location ILIKE {p} OR ri.situation ILIKE {p})")
    if service:
        p = add_arg(service)
        conditions.append(f"ri.service={p}")
    if priority:
        p = add_arg(priority)
        conditions.append(f"ri.priority={p}")
    if status:
        p = add_arg(status)
        conditions.append(f"ri.status={p}")
    if responder_id:
        p = add_arg(responder_id)
        conditions.append(f"(ri.primary_responder_id={p} OR EXISTS (SELECT 1 FROM rescue_incident_responders rr WHERE rr.channel_id=ri.channel_id AND rr.user_id={p}))")
    if from_dt:
        p = add_arg(from_dt)
        conditions.append(f"ri.created_at>={p}")
    if to_dt:
        p = add_arg(to_dt)
        conditions.append(f"ri.created_at<{p}")

    where_sql = " AND ".join(conditions)
    page_size = 25
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM rescue_incidents ri WHERE {where_sql}", *args)
        limit_p = f"${len(args)+1}"
        offset_p = f"${len(args)+2}"
        rows = await conn.fetch(
            f"SELECT ri.incident_number,ri.callsign,ri.service,ri.location,ri.priority,ri.status,ri.primary_responder_id,ri.created_at,ri.responded_at,ri.closed_at FROM rescue_incidents ri WHERE {where_sql} ORDER BY ri.created_at DESC,ri.incident_number DESC LIMIT {limit_p} OFFSET {offset_p}",
            *args,
            page_size,
            (page_num - 1) * page_size,
        )
        responder_rows = await conn.fetch(
            """
            SELECT DISTINCT user_id FROM (
                SELECT primary_responder_id AS user_id FROM rescue_incidents WHERE guild_id=$1 AND primary_responder_id IS NOT NULL
                UNION
                SELECT rr.user_id FROM rescue_incident_responders rr JOIN rescue_incidents ri ON ri.channel_id=rr.channel_id WHERE ri.guild_id=$1
            ) responders
            WHERE user_id IS NOT NULL
            ORDER BY user_id
            """,
            guild_id,
        )

    responder_ids = [r["user_id"] for r in responder_rows]
    names = await member_names(guild_id, responder_ids + [r["primary_responder_id"] for r in rows])
    responder_options = '<option value="">Any responder</option>' + "".join(
        option(uid, names.get(uid, "Discord User"), uid == responder_id) for uid in sorted(responder_ids, key=lambda uid: names.get(uid, "").lower())
    )
    service_options = '<option value="">Any service</option>' + "".join(option(k, v, k == service) for k, v in SERVICES.items())
    priority_options = '<option value="">Any priority</option>' + "".join(option(k, v, k == priority) for k, v in PRIORITIES.items())
    status_options = '<option value="">Any status</option>' + "".join(option(k, v, k == status) for k, v in STATUSES.items())

    result_rows = "".join(
        f'<tr><td><a class="incident-link" href="/guild/{guild_id}/incident/{r["incident_number"]}">RESCUE-{r["incident_number"]:04d}</a></td><td><span class="pill {"p1" if r["priority"]=="critical" else "p2" if r["priority"]=="urgent" else "p3"}">{esc(PRIORITIES.get(r["priority"], r["priority"]))}</span></td><td>{esc(STATUSES.get(r["status"], r["status"]))}</td><td>{esc(SERVICES.get(r["service"], r["service"]))}</td><td>{esc(r["callsign"])}</td><td>{esc(r["location"])}</td><td>{esc(names.get(r["primary_responder_id"], "Unassigned") if r["primary_responder_id"] else "Unassigned")}</td><td>{format_dt(r["created_at"])}</td></tr>'
        for r in rows
    ) or '<tr><td colspan="8" class="muted">No incidents match the selected filters.</td></tr>'

    total_pages = max(1, (int(total) + page_size - 1) // page_size)
    if page_num > total_pages and total:
        page_num = total_pages
    base_params = {"q": q, "service": service, "priority": priority, "status": status, "responder": responder, "date_from": date_from, "date_to": date_to}
    prev_params = {**base_params, "page_num": max(1, page_num - 1)}
    next_params = {**base_params, "page_num": min(total_pages, page_num + 1)}
    prev_button = f'<a class="btn secondary" href="/guild/{guild_id}/history?{urlencode(prev_params)}">← Previous</a>' if page_num > 1 else '<span></span>'
    next_button = f'<a class="btn secondary" href="/guild/{guild_id}/history?{urlencode(next_params)}">Next →</a>' if page_num < total_pages else '<span></span>'
    shown_start = ((page_num - 1) * page_size + 1) if total else 0
    shown_end = min(page_num * page_size, int(total))

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}">← Back to Dashboard</a></div><div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap"><div><h2>Rescue History Search</h2><p class="muted">Search the complete incident database and filter by service, priority, status, responder, or date.</p></div><div class="status installed">{int(total)} match{'es' if int(total) != 1 else ''}</div></div><form method="get" action="/guild/{guild_id}/history"><div class="filter-grid"><div><label>Search</label><input type="search" name="q" value="{esc(q)}" placeholder="Incident #, callsign, location, situation"></div><div><label>Service</label><select name="service">{service_options}</select></div><div><label>Priority</label><select name="priority">{priority_options}</select></div><div><label>Status</label><select name="status">{status_options}</select></div><div><label>Responder</label><select name="responder">{responder_options}</select></div><div><label>From date</label><input type="date" name="date_from" value="{esc(date_from)}"></div><div><label>To date</label><input type="date" name="date_to" value="{esc(date_to)}"></div><div class="filter-actions"><button class="btn" type="submit">Apply Filters</button><a class="btn secondary" href="/guild/{guild_id}/history">Clear</a></div></div></form></div><div class="card" style="margin-top:16px"><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Priority</th><th>Status</th><th>Service</th><th>Callsign</th><th>Location</th><th>Primary</th><th>Opened</th></tr></thead><tbody>{result_rows}</tbody></table></div><div class="pagination"><div class="muted">Showing {shown_start}–{shown_end} of {int(total)}</div><div class="pages">{prev_button}<span class="muted">Page {page_num} of {total_pages}</span>{next_button}</div></div></div>'''
    return page(f"Rescue History · {guild_info['name']}", body, current_user(request))


async def load_incident(guild_id, incident_number):
    async with pool.acquire() as conn:
        incident = await conn.fetchrow(
            "SELECT incident_number,channel_id,requester_id,callsign,service,location,situation,priority,priority_changed_by,priority_changed_at,status,primary_responder_id,created_at,responded_at,arrived_at,backup_requested_at,closed_at,closed_by_id FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2",
            guild_id,
            incident_number,
        )
        if not incident:
            return None, [], []
        responders = await conn.fetch(
            "SELECT user_id,joined_at FROM rescue_incident_responders WHERE channel_id=$1 ORDER BY joined_at ASC",
            incident["channel_id"],
        )
        ledger = await conn.fetch(
            "SELECT id,event_type,actor_id,title,details,created_at FROM rescue_incident_events WHERE guild_id=$1 AND incident_number=$2 ORDER BY created_at ASC,id ASC",
            guild_id,
            incident_number,
        )
    return incident, responders, ledger


@app.get("/guild/{guild_id}/incident/{incident_number}", response_class=HTMLResponse)
async def incident_detail(request: Request, guild_id: int, incident_number: int, action: str = ""):
    guild_info = require_guild_access(request, guild_id)
    await require_bot_installed(guild_id)
    incident, responders, ledger = await load_incident(guild_id, incident_number)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")

    user_ids = [incident["requester_id"], incident["primary_responder_id"], incident["priority_changed_by"], incident["closed_by_id"]]
    user_ids += [r["user_id"] for r in responders]
    user_ids += [e["actor_id"] for e in ledger]
    names = await member_names(guild_id, user_ids)

    def who(uid, fallback="—"):
        return esc(names.get(uid, fallback)) if uid else fallback

    support = [who(r["user_id"], "Discord User") for r in responders if r["user_id"] != incident["primary_responder_id"]]
    support_text = ", ".join(support) if support else "None"

    if ledger:
        timeline_parts = []
        for event in ledger:
            actor = f' · {who(event["actor_id"], "Discord User")}' if event["actor_id"] else ""
            timeline_parts.append(
                f'<div class="event"><div class="event-title">{esc(event["title"])}<span class="ledger-badge">Recorded</span></div><div>{esc(event["details"])}</div><div class="event-meta">{format_dt(event["created_at"])}{actor}</div></div>'
            )
        timeline = "".join(timeline_parts)
        timeline_note = "Permanent event ledger — every recorded action is preserved in order."
    else:
        events = [(incident["created_at"], "Incident Created", f'Request submitted by {who(incident["requester_id"], "Requester")}')]
        if incident["responded_at"]:
            events.append((incident["responded_at"], "Primary Responder Assigned", f'{who(incident["primary_responder_id"], "Responder")} accepted the call'))
        for r in responders:
            if r["user_id"] != incident["primary_responder_id"]:
                events.append((r["joined_at"], "Responder Joined", f'{who(r["user_id"], "Responder")} joined the response team'))
        if incident["priority_changed_at"]:
            events.append((incident["priority_changed_at"], "Priority Changed", f'Priority changed to {esc(PRIORITIES.get(incident["priority"], incident["priority"]))}'))
        if incident["arrived_at"]:
            events.append((incident["arrived_at"], "Arrived On Scene", "Response team reported arrival on scene"))
        if incident["backup_requested_at"]:
            events.append((incident["backup_requested_at"], "Backup Requested", "Additional responder support was requested"))
        if incident["closed_at"]:
            events.append((incident["closed_at"], "Incident Closed", f'Closed by {who(incident["closed_by_id"], "authorized user")}'))
        events.sort(key=lambda e: e[0])
        timeline = "".join(f'<div class="event"><div class="event-title">{esc(title)}</div><div>{text}</div><div class="event-meta">{format_dt(ts)}</div></div>' for ts, title, text in events)
        timeline_note = "Legacy incident — timeline reconstructed from the original incident record. New activity will use the permanent event ledger."

    incident_id = f"RESCUE-{incident_number:04d}"
    pc = "p1" if incident["priority"] == "critical" else "p2" if incident["priority"] == "urgent" else "p3"
    channel_button = f'<a class="btn" href="https://discord.com/channels/{guild_id}/{incident["channel_id"]}" target="_blank" rel="noopener">Open Discord Channel</a>' if incident["channel_id"] else ""
    action_messages = {
        "priority_up": "Priority raised from the web dashboard.",
        "priority_down": "Priority lowered from the web dashboard.",
        "arrived": "Incident marked on scene from the web dashboard.",
        "backup": "Backup requested from the web dashboard.",
        "closed": "Incident closed from the web dashboard.",
    }
    action_notice = f'<div class="notice">{esc(action_messages[action])}</div>' if action in action_messages else ""
    csrf = esc(request.session.get("csrf"))
    closed = incident["status"] == "closed"
    disabled = " disabled" if closed else ""
    controls = f'''<div class="card span4"><h2>Command Controls</h2><p class="muted">Manager actions update the database, permanent event ledger, Discord incident channel, and live dispatch board.</p><div class="control-grid"><form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="priority_up"><button class="btn danger" type="submit"{disabled}>⬆ Raise Priority</button></form><form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="priority_down"><button class="btn secondary" type="submit"{disabled}>⬇ Lower Priority</button></form><form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="arrived"><button class="btn success" type="submit"{disabled}>📍 Mark Arrived</button></form><form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="backup"><button class="btn warn" type="submit"{disabled}>🛡 Request Backup</button></form><form method="post" action="/guild/{guild_id}/incident/{incident_number}/action" style="grid-column:1/-1"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="close"><button class="btn danger" type="submit" onclick="return confirm('Close {incident_id}? This will archive the incident and disable its Discord controls.')"{disabled}>🔒 Close Incident</button></form></div><div class="control-note">Web controls are limited to users who have Discord Manage Server permission. Respond and Join Response remain Discord-only responder actions.</div></div>'''
    body = f'''{action_notice}<div class="section-nav"><a href="/guild/{guild_id}">← Back to Dashboard</a><a href="/guild/{guild_id}/history">Search History</a></div><div class="grid"><div class="card span8"><div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap"><div><div class="label">Incident</div><h1 style="margin-top:5px">{incident_id}</h1></div><div><span class="pill {pc}">{esc(PRIORITIES.get(incident['priority'], incident['priority']))}</span> <span class="pill">{esc(STATUSES.get(incident['status'], incident['status']))}</span></div></div><div class="kv" style="margin-top:22px"><div>Callsign</div><div><strong>{esc(incident['callsign'])}</strong></div><div>Service</div><div>{esc(SERVICES.get(incident['service'], incident['service']))}</div><div>Location</div><div>{esc(incident['location'])}</div><div>Requester</div><div>{who(incident['requester_id'], 'Requester')}</div><div>Primary responder</div><div>{who(incident['primary_responder_id'], 'Unassigned')}</div><div>Support responders</div><div>{support_text}</div><div>Created</div><div>{format_dt(incident['created_at'])}</div><div>Claimed</div><div>{format_dt(incident['responded_at'])}</div><div>Arrived</div><div>{format_dt(incident['arrived_at'])}</div><div>Closed</div><div>{format_dt(incident['closed_at'])}</div></div><div style="margin-top:20px">{channel_button}</div></div>{controls}<div class="card span4"><h2>Response Timing</h2><div class="kv"><div>Claim time</div><div>{duration((incident['responded_at']-incident['created_at']).total_seconds()) if incident['responded_at'] else '—'}</div><div>On-scene time</div><div>{duration((incident['arrived_at']-incident['created_at']).total_seconds()) if incident['arrived_at'] else '—'}</div><div>Total duration</div><div>{duration((incident['closed_at']-incident['created_at']).total_seconds()) if incident['closed_at'] else 'Active'}</div></div></div><div class="card span12"><h2>Situation</h2><div class="situation">{esc(incident['situation'])}</div></div><div class="card span12"><h2>Incident Timeline</h2><p class="muted">{esc(timeline_note)}</p><div class="timeline">{timeline}</div></div></div>'''
    return page(f"{incident_id} · {guild_info['name']}", body, current_user(request))


async def build_dispatch_embed(guild_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT incident_number,callsign,service,location,priority,status,primary_responder_id,created_at,channel_id FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed' ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2 ELSE 3 END, incident_number ASC LIMIT 24",
            guild_id,
        )
    fields = []
    if not rows:
        fields.append({"name": "✅ No Active Incidents", "value": "All rescue calls are currently clear.", "inline": False})
    for row in rows:
        primary = f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned"
        fields.append(
            {
                "name": f"{PRIORITIES.get(row['priority'], row['priority'])} • RESCUE-{row['incident_number']:04d}",
                "value": f"**{SERVICES.get(row['service'], row['service'])}** • {STATUSES.get(row['status'], row['status'])}\n**Callsign:** {row['callsign'][:45]}\n**Location:** {row['location'][:90]}\n**Primary:** {primary}\n**Channel:** <#{row['channel_id']}>",
                "inline": False,
            }
        )
    return {
        "title": "📡 STAR CITIZEN RESCUE — LIVE DISPATCH BOARD",
        "description": "Active rescue operations are sorted by priority and update automatically.",
        "color": 5793266,
        "fields": fields,
        "footer": {"text": f"Active Incidents: {len(rows)} • P1 incidents appear first"},
    }


async def refresh_dispatch_board_rest(guild_id):
    async with pool.acquire() as conn:
        board = await conn.fetchrow(
            "SELECT channel_id,message_id FROM rescue_dispatch_boards WHERE guild_id=$1",
            guild_id,
        )
    if not board:
        return False
    try:
        await discord_patch(
            f"/channels/{board['channel_id']}/messages/{board['message_id']}",
            {"embeds": [await build_dispatch_embed(guild_id)], "components": dashboard_button_components()},
        )
        return True
    except httpx.HTTPStatusError:
        return False


async def find_incident_card(channel_id, incident_number):
    incident_id = f"RESCUE-{incident_number:04d}"
    try:
        messages = await discord_get(f"/channels/{channel_id}/messages?limit=50")
    except httpx.HTTPStatusError:
        return None
    for message in messages:
        for embed in message.get("embeds", []):
            footer = (embed.get("footer") or {}).get("text", "")
            title = embed.get("title", "")
            if incident_id in footer or incident_id in title:
                return message
    return None


async def sync_incident_card(incident, incident_number, actor_id, close_controls=False):
    if not incident["channel_id"]:
        return
    card = await find_incident_card(incident["channel_id"], incident_number)
    if not card or not card.get("embeds"):
        return
    embed = card["embeds"][0]
    fields = embed.get("fields", [])
    for field in fields:
        if field.get("name") == "Priority":
            field["value"] = PRIORITY_DISCORD.get(incident["priority"], incident["priority"])
        elif field.get("name") == "Status":
            status = STATUS_DISCORD.get(incident["status"], incident["status"])
            if incident["status"] in {"on_scene", "backup_requested", "closed"} and actor_id:
                status += f" — <@{actor_id}>"
            field["value"] = status
    if incident["status"] == "closed":
        embed["color"] = 0x2F3136
    elif incident["status"] == "on_scene":
        embed["color"] = 0x57F287
    elif incident["status"] == "backup_requested":
        embed["color"] = 0xFEE75C
    elif incident["priority"] == "critical":
        embed["color"] = 0xED4245
    elif incident["priority"] == "urgent":
        embed["color"] = 0xFAA61A
    else:
        embed["color"] = 0x57F287
    payload = {"embeds": [embed]}
    if close_controls:
        payload["components"] = []
    try:
        await discord_patch(f"/channels/{incident['channel_id']}/messages/{card['id']}", payload)
    except httpx.HTTPStatusError:
        pass


async def responder_role_ids_for_guild(guild_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT responder_role_ids FROM rescue_guild_settings WHERE guild_id=$1", guild_id)
    return [int(v) for v in (row["responder_role_ids"] or [])] if row else []


async def post_incident_message(channel_id, content, role_ids=None):
    payload = {"content": content}
    if role_ids:
        payload["allowed_mentions"] = {"parse": [], "roles": [str(v) for v in role_ids], "users": []}
    try:
        await discord_post(f"/channels/{channel_id}/messages", payload)
    except httpx.HTTPStatusError:
        pass


async def make_closed_channel_read_only(guild_id, incident, actor_id):
    channel_id = incident["channel_id"]
    if not channel_id:
        return
    try:
        channel = await discord_get(f"/channels/{channel_id}")
        name = channel.get("name", "rescue-incident")
        topic = channel.get("topic") or f"RESCUE-{incident['incident_number']:04d}"
        await discord_patch(
            f"/channels/{channel_id}",
            {
                "name": (name if name.startswith("closed-") else f"closed-{name}")[:100],
                "topic": topic if topic.startswith("CLOSED |") else f"CLOSED | {topic}",
            },
        )
    except httpx.HTTPStatusError:
        pass

    allow_read = str(1024 | 65536)
    deny_send = str(2048)
    targets = [(int(incident["requester_id"]), 1)]
    for role_id in await responder_role_ids_for_guild(guild_id):
        targets.append((role_id, 0))
    for overwrite_id, overwrite_type in targets:
        try:
            await discord_put(
                f"/channels/{channel_id}/permissions/{overwrite_id}",
                {"type": overwrite_type, "allow": allow_read, "deny": deny_send},
            )
        except httpx.HTTPStatusError:
            pass


async def post_rescue_log_record(guild_id, incident_number):
    async with pool.acquire() as conn:
        log_channel = await conn.fetchval("SELECT channel_id FROM rescue_log_channels WHERE guild_id=$1", guild_id)
        incident = await conn.fetchrow(
            "SELECT incident_number,channel_id,requester_id,callsign,service,location,situation,priority,primary_responder_id,created_at,responded_at,arrived_at,closed_at,closed_by_id FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2",
            guild_id,
            incident_number,
        )
        responders = await conn.fetch(
            "SELECT user_id FROM rescue_incident_responders WHERE channel_id=$1 ORDER BY joined_at ASC",
            incident["channel_id"] if incident else 0,
        )
    if not log_channel or not incident:
        return False
    claim_seconds = (incident["responded_at"] - incident["created_at"]).total_seconds() if incident["responded_at"] else None
    arrival_seconds = (incident["arrived_at"] - incident["created_at"]).total_seconds() if incident["arrived_at"] else None
    total_seconds = (incident["closed_at"] - incident["created_at"]).total_seconds() if incident["closed_at"] else None
    responder_mentions = [f"<@{r['user_id']}>" for r in responders]
    embed = {
        "title": f"📁 RESCUE-{incident_number:04d} — RESCUE RECORD",
        "description": "Completed rescue incident archived by Star Citizen Rescue Dispatch.",
        "color": 0x2F3136,
        "fields": [
            {"name": "Service", "value": SERVICES.get(incident["service"], incident["service"]), "inline": True},
            {"name": "Priority", "value": PRIORITY_DISCORD.get(incident["priority"], incident["priority"]), "inline": True},
            {"name": "Callsign", "value": str(incident["callsign"])[:80], "inline": True},
            {"name": "Requester", "value": f"<@{incident['requester_id']}>", "inline": True},
            {"name": "Primary Responder", "value": f"<@{incident['primary_responder_id']}>" if incident["primary_responder_id"] else "Unassigned", "inline": True},
            {"name": "Closed By", "value": f"<@{incident['closed_by_id']}>" if incident["closed_by_id"] else "Unknown", "inline": True},
            {"name": "Location", "value": str(incident["location"])[:200], "inline": False},
            {"name": "Situation", "value": str(incident["situation"])[:500], "inline": False},
            {"name": "Timing", "value": f"**Claimed:** {duration(claim_seconds)}\n**On Scene:** {duration(arrival_seconds)}\n**Total Incident:** {duration(total_seconds)}", "inline": True},
            {"name": "Responders", "value": ", ".join(responder_mentions) if responder_mentions else "None recorded", "inline": True},
        ],
        "footer": {"text": "Database archive • Closed from web dashboard"},
    }
    try:
        await discord_post(f"/channels/{log_channel}/messages", {"embeds": [embed]})
        return True
    except httpx.HTTPStatusError:
        return False


@app.post("/guild/{guild_id}/incident/{incident_number}/action")
async def incident_action(request: Request, guild_id: int, incident_number: int):
    require_guild_access(request, guild_id)
    await require_bot_installed(guild_id)
    form = await request.form()
    require_csrf(request, form.get("csrf"))
    action = str(form.get("action") or "")
    if action not in {"priority_up", "priority_down", "arrived", "backup", "close"}:
        raise HTTPException(status_code=400, detail="Unknown incident action.")
    actor_id = int(current_user(request)["id"])

    async with pool.acquire() as conn:
        async with conn.transaction():
            incident = await conn.fetchrow(
                "SELECT incident_number,channel_id,requester_id,priority,status,primary_responder_id,created_at,responded_at,arrived_at,closed_at FROM rescue_incidents WHERE guild_id=$1 AND incident_number=$2 FOR UPDATE",
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
                current = incident["priority"] if incident["priority"] in PRIORITY_ORDER else "standard"
                index = PRIORITY_ORDER.index(current)
                new_index = min(len(PRIORITY_ORDER) - 1, index + 1) if action == "priority_up" else max(0, index - 1)
                new_priority = PRIORITY_ORDER[new_index]
                if new_priority == current:
                    boundary = "highest" if action == "priority_up" else "lowest"
                    raise HTTPException(status_code=409, detail=f"Incident is already at the {boundary} priority.")
                await conn.execute(
                    "UPDATE rescue_incidents SET priority=$3,priority_changed_by=$4,priority_changed_at=NOW() WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                    new_priority,
                    actor_id,
                )
                event_type = "priority_changed"
                title = "Priority Changed"
                details = f"Priority changed from {PRIORITIES.get(current, current)} to {PRIORITIES.get(new_priority, new_priority)} from the web dashboard."
            elif action == "arrived":
                await conn.execute(
                    "UPDATE rescue_incidents SET status='on_scene',arrived_at=COALESCE(arrived_at,NOW()) WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                )
                title = "Arrived On Scene"
                details = "Incident marked on scene from the web dashboard."
            elif action == "backup":
                await conn.execute(
                    "UPDATE rescue_incidents SET status='backup_requested',backup_requested_at=NOW() WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                )
                event_type = "backup_requested"
                title = "Backup Requested"
                details = "Additional responder support was requested from the web dashboard."
            elif action == "close":
                await conn.execute(
                    "UPDATE rescue_incidents SET status='closed',closed_at=NOW(),closed_by_id=$3 WHERE guild_id=$1 AND incident_number=$2",
                    guild_id,
                    incident_number,
                    actor_id,
                )
                event_type = "closed"
                title = "Incident Closed"
                details = "Incident closed by server management from the web dashboard."
                redirect_action = "closed"

            await conn.execute(
                "INSERT INTO rescue_incident_events(guild_id,incident_number,channel_id,event_type,actor_id,title,details,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,NOW())",
                guild_id,
                incident_number,
                incident["channel_id"],
                event_type,
                actor_id,
                title,
                details,
            )

    updated, _, _ = await load_incident(guild_id, incident_number)
    incident_id = f"RESCUE-{incident_number:04d}"
    role_ids = await responder_role_ids_for_guild(guild_id)
    if updated and updated["channel_id"]:
        if action == "priority_up" or action == "priority_down":
            await post_incident_message(
                updated["channel_id"],
                f"⚠️ **WEB DISPATCH:** {incident_id} priority changed to **{PRIORITY_DISCORD.get(updated['priority'], updated['priority'])}** by <@{actor_id}>.",
            )
            if updated["priority"] == "critical" and role_ids:
                mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids)
                await post_incident_message(
                    updated["channel_id"],
                    f"🔴 **ALL-SECTOR PRIORITY 1 PAGE:** {mentions}",
                    role_ids,
                )
        elif action == "arrived":
            await post_incident_message(updated["channel_id"], f"📍 **WEB DISPATCH:** {incident_id} marked **On Scene** by <@{actor_id}>.")
        elif action == "backup":
            mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids)
            await post_incident_message(
                updated["channel_id"],
                f"🛡️ **BACKUP REQUESTED VIA WEB DISPATCH:** {mentions}" if mentions else "🛡️ **BACKUP REQUESTED VIA WEB DISPATCH.**",
                role_ids,
            )
        elif action == "close":
            await post_incident_message(updated["channel_id"], f"🔒 {incident_id} closed from the web dashboard by <@{actor_id}>.")

        await sync_incident_card(updated, incident_number, actor_id, close_controls=(action == "close"))
        if action == "close":
            await make_closed_channel_read_only(guild_id, updated, actor_id)
            await post_rescue_log_record(guild_id, incident_number)

    await refresh_dispatch_board_rest(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/incident/{incident_number}?action={redirect_action}", status_code=303)


async def post_request_panel(channel_id):
    return await discord_post(
        f"/channels/{channel_id}/messages",
        {
            "embeds": [
                {
                    "title": "🚨 STAR CITIZEN RESCUE DISPATCH",
                    "description": "Need assistance in the 'verse? Use the button below to open a rescue request. You will choose a service and request priority before entering the incident details.",
                    "color": 15548997,
                    "fields": [
                        {"name": "Available Services", "value": "🚑 Medical Rescue\n🔎 Search & Rescue\n🔧 Repair / Refuel\n🛡️ Security / Escort\n🚀 Recovery / Transport", "inline": False},
                        {"name": "Priority Levels", "value": "🔴 P1 Critical — responder/management escalation only\n🟠 P2 Urgent — time-sensitive request\n🟢 P3 Standard — routine assistance", "inline": False},
                    ],
                }
            ],
            "components": [
                {
                    "type": 1,
                    "components": [
                        {"type": 2, "style": 4, "label": "Request Assistance", "emoji": {"name": "🚨"}, "custom_id": "rescue:request"}
                    ],
                }
            ],
        },
    )


@app.post("/guild/{guild_id}/config")
async def save_config(request: Request, guild_id: int):
    require_guild_access(request, guild_id)
    await require_bot_installed(guild_id)
    form = await request.form()
    require_csrf(request, form.get("csrf"))

    roles = await discord_get(f"/guilds/{guild_id}/roles")
    channels = await discord_get(f"/guilds/{guild_id}/channels")
    valid_role_ids = {int(r["id"]) for r in roles if r.get("name") != "@everyone" and not r.get("managed")}
    valid_text_ids = {int(c["id"]) for c in channels if c.get("type") == 0}
    valid_category_ids = {int(c["id"]) for c in channels if c.get("type") == 4}
    responder_ids = [int(v) for v in form.getlist("responder_roles") if str(v).isdigit() and int(v) in valid_role_ids]
    if not responder_ids:
        raise HTTPException(status_code=400, detail="Select at least one responder role.")

    def selected_id(name, valid_ids):
        value = str(form.get(name) or "")
        if not value:
            return None
        value = int(value)
        if value not in valid_ids:
            raise HTTPException(status_code=400, detail=f"Invalid {name} selection.")
        return value

    request_channel = selected_id("request_channel", valid_text_ids)
    dispatch_channel = selected_id("dispatch_channel", valid_text_ids)
    log_channel = selected_id("log_channel", valid_text_ids)
    incident_category = selected_id("incident_category", valid_category_ids)
    service_map = {
        service: [int(v) for v in form.getlist(f"service_{service}") if str(v).isdigit() and int(v) in valid_role_ids]
        for service in SERVICES
    }

    async with pool.acquire() as conn:
        previous = await conn.fetchrow("SELECT request_channel_id FROM rescue_guild_settings WHERE guild_id=$1", guild_id)
        previous_dispatch = await conn.fetchval("SELECT channel_id FROM rescue_dispatch_boards WHERE guild_id=$1", guild_id)
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO rescue_guild_settings(guild_id,responder_role_ids,request_channel_id,incident_category_id,updated_at) VALUES($1,$2,$3,$4,NOW()) ON CONFLICT(guild_id) DO UPDATE SET responder_role_ids=EXCLUDED.responder_role_ids,request_channel_id=EXCLUDED.request_channel_id,incident_category_id=EXCLUDED.incident_category_id,updated_at=NOW()",
                guild_id,
                responder_ids,
                request_channel,
                incident_category,
            )
            for service, role_ids in service_map.items():
                await conn.execute(
                    "INSERT INTO rescue_service_role_settings(guild_id,service,role_ids,updated_at) VALUES($1,$2,$3,NOW()) ON CONFLICT(guild_id,service) DO UPDATE SET role_ids=EXCLUDED.role_ids,updated_at=NOW()",
                    guild_id,
                    service,
                    role_ids,
                )
            if log_channel:
                await conn.execute(
                    "INSERT INTO rescue_log_channels(guild_id,channel_id,updated_at) VALUES($1,$2,NOW()) ON CONFLICT(guild_id) DO UPDATE SET channel_id=EXCLUDED.channel_id,updated_at=NOW()",
                    guild_id,
                    log_channel,
                )
            else:
                await conn.execute("DELETE FROM rescue_log_channels WHERE guild_id=$1", guild_id)

    previous_request = previous["request_channel_id"] if previous else None
    if request_channel and request_channel != previous_request:
        await post_request_panel(request_channel)
    if dispatch_channel and dispatch_channel != previous_dispatch:
        message = await discord_post(
            f"/channels/{dispatch_channel}/messages",
            {"embeds": [await build_dispatch_embed(guild_id)], "components": dashboard_button_components()},
        )
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO rescue_dispatch_boards(guild_id,channel_id,message_id,updated_at) VALUES($1,$2,$3,NOW()) ON CONFLICT(guild_id) DO UPDATE SET channel_id=EXCLUDED.channel_id,message_id=EXCLUDED.message_id,updated_at=NOW()",
                guild_id,
                dispatch_channel,
                int(message["id"]),
            )
    elif not dispatch_channel:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM rescue_dispatch_boards WHERE guild_id=$1", guild_id)

    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)
