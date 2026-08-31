import html
import os
import secrets
from datetime import timezone
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
STATUSES = {"awaiting_responder": "Awaiting Responder", "en_route": "En Route", "on_scene": "On Scene", "backup_requested": "Backup Requested", "closed": "Closed"}

app = FastAPI(title="Star Citizen Rescue Dashboard")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET or secrets.token_urlsafe(48), https_only=COOKIE_SECURE, same_site="lax", max_age=60*60*12)
pool = None

@app.on_event("startup")
async def startup():
    global pool
    if not DATABASE_URL: raise RuntimeError("DATABASE_URL is required for the rescue dashboard.")
    if not BOT_TOKEN: raise RuntimeError("DISCORD_TOKEN is required for the rescue dashboard.")
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI or not SESSION_SECRET:
        raise RuntimeError("DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI, and DASHBOARD_SESSION_SECRET are required.")
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=15)
    async with pool.acquire() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS rescue_guild_settings (guild_id BIGINT PRIMARY KEY,responder_role_ids BIGINT[] NOT NULL DEFAULT '{}',request_channel_id BIGINT,incident_category_id BIGINT,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()); CREATE TABLE IF NOT EXISTS rescue_service_role_settings (guild_id BIGINT NOT NULL,service TEXT NOT NULL,role_ids BIGINT[] NOT NULL DEFAULT '{}',updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(guild_id, service));""")

@app.on_event("shutdown")
async def shutdown():
    if pool: await pool.close()

def esc(value): return html.escape(str(value or ""), quote=True)
def format_dt(value):
    if not value: return "—"
    if value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
def duration(seconds):
    if seconds is None: return "—"
    seconds=max(0,int(seconds))
    if seconds<60:return f"{seconds}s"
    minutes,seconds=divmod(seconds,60)
    if minutes<60:return f"{minutes}m {seconds}s"
    hours,minutes=divmod(minutes,60);return f"{hours}h {minutes}m"

def page(title,body,user=None):
    user_html=f'<div class="user">{esc(user.get("global_name") or user.get("username"))} · <a href="/logout">Sign out</a></div>' if user else ""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · SC Rescue</title><style>:root{{--bg:#090d14;--panel:#111827;--line:#263247;--text:#e7edf7;--muted:#91a0b8}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#080b11,#0d1420 55%,#0b1019);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;min-height:100vh}}a{{color:#8bc0ff;text-decoration:none}}.wrap{{max-width:1240px;margin:0 auto;padding:28px 20px 60px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:26px}}h1{{font-size:24px;margin:0}}h2{{font-size:17px;margin:0 0 14px}}.brand small,.muted,.user{{color:var(--muted)}}.user{{font-size:14px}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}.card{{background:rgba(17,24,39,.92);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 14px 35px rgba(0,0,0,.18)}}.span3{{grid-column:span 3}}.span6{{grid-column:span 6}}.span12{{grid-column:span 12}}.metric{{font-size:30px;font-weight:750;margin-top:4px}}.label{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}.pill,.status{{display:inline-block;padding:4px 8px;border-radius:999px;font-size:12px}}.p1{{background:#52242c;color:#ffb4bd}}.p2,.not-installed{{background:#50351d;color:#ffd49a}}.p3,.installed{{background:#153c32;color:#8df0c5}}label{{display:block;color:var(--muted);font-size:12px;margin:12px 0 6px}}select{{width:100%;background:#0c1320;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px;min-height:42px}}select[multiple]{{min-height:120px}}.btn{{display:inline-block;border:0;border-radius:9px;background:#2b74c8;color:white;padding:11px 16px;font-weight:650;cursor:pointer}}.btn.secondary{{background:#263247}}.btn.install{{background:#237a57}}.notice{{padding:12px 14px;border:1px solid #315f45;background:#122d22;border-radius:10px;color:#a8efc8;margin-bottom:16px}}.guild{{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 0;border-bottom:1px solid var(--line)}}.guild:last-child{{border:0}}.guild-actions{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.section-nav{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}.section-nav a{{padding:8px 10px;background:#111827;border:1px solid var(--line);border-radius:8px}}@media(max-width:850px){{.span3,.span6{{grid-column:span 12}}header,.guild{{align-items:flex-start;flex-direction:column}}}}</style></head><body><div class="wrap"><header><div class="brand"><h1>🚨 Star Citizen Rescue Dispatch</h1><small>Operations Dashboard</small></div>{user_html}</header>{body}</div></body></html>''')

def current_user(request):return request.session.get("user")
def manageable_guilds(request):return request.session.get("guilds") or []
def require_guild_access(request,guild_id):
    if not current_user(request):raise HTTPException(status_code=401)
    for guild in manageable_guilds(request):
        if str(guild.get("id"))==str(guild_id) and guild.get("can_manage"):return guild
    raise HTTPException(status_code=403,detail="Manage Server permission is required.")
def bot_install_url(guild_id):
    return "https://discord.com/oauth2/authorize?"+urlencode({"client_id":CLIENT_ID,"scope":"bot applications.commands","permissions":str(BOT_PERMISSIONS),"guild_id":str(guild_id),"disable_guild_select":"true"})
async def discord_get(path,token=None):
    headers={"Authorization":f"Bearer {token}"} if token else {"Authorization":f"Bot {BOT_TOKEN}"}
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.get(f"{DISCORD_API}{path}",headers=headers);response.raise_for_status();return response.json()
async def discord_post(path,payload):
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.post(f"{DISCORD_API}{path}",headers={"Authorization":f"Bot {BOT_TOKEN}"},json=payload);response.raise_for_status();return response.json()
async def bot_guild_ids():
    installed=set();after=None
    while True:
        path="/users/@me/guilds?limit=200"+(f"&after={after}" if after else "");guilds=await discord_get(path);installed.update(str(g["id"]) for g in guilds)
        if len(guilds)<200:break
        after=guilds[-1]["id"]
    return installed
async def require_bot_installed(guild_id):
    try:await discord_get(f"/guilds/{guild_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (403,404):raise HTTPException(status_code=409,detail="The rescue bot is not installed in this Discord server.")
        raise HTTPException(status_code=502,detail=f"Discord API error: {exc.response.status_code}")

@app.get("/health")
async def health():return {"ok":True}
@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    user=current_user(request)
    if not user:return page("Sign In",'<div class="card" style="max-width:620px"><h2>Rescue Operations Dashboard</h2><p class="muted">Sign in with Discord to view rescue operations and manage configuration. Server configuration requires Manage Server permission.</p><a class="btn" href="/login">Sign in with Discord</a></div>')
    guilds=[g for g in manageable_guilds(request) if g.get("can_manage")]
    try:installed_ids=await bot_guild_ids()
    except httpx.HTTPStatusError as exc:raise HTTPException(status_code=502,detail=f"Unable to check bot installations: Discord API {exc.response.status_code}")
    rows=[]
    for guild in guilds:
        guild_id=str(guild["id"])
        if guild_id in installed_ids:action=f'<div class="guild-actions"><span class="status installed">Bot Installed</span><a class="btn secondary" href="/guild/{esc(guild_id)}">Open Dashboard</a></div>'
        else:action=f'<div class="guild-actions"><span class="status not-installed">Bot Not Installed</span><a class="btn install" href="{esc(bot_install_url(guild_id))}" target="_blank" rel="noopener">Install Bot</a></div>'
        rows.append(f'<div class="guild"><div><strong>{esc(guild["name"])}</strong></div>{action}</div>')
    rows_html="".join(rows) or '<p class="muted">No servers with Manage Server permission were found for this login.</p>'
    return page("Servers",'<div class="card"><h2>Select a server</h2><p class="muted">Installed servers can be managed immediately. For another server you manage, install the bot first and then refresh this page.</p>'+rows_html+'</div>',user)
@app.get("/login")
async def login(request:Request):
    state=secrets.token_urlsafe(24);request.session["oauth_state"]=state
    return RedirectResponse("https://discord.com/oauth2/authorize?"+urlencode({"client_id":CLIENT_ID,"redirect_uri":REDIRECT_URI,"response_type":"code","scope":"identify guilds","state":state}))
@app.get("/oauth/callback")
async def oauth_callback(request:Request,code:str,state:str):
    if not secrets.compare_digest(state,request.session.pop("oauth_state","")):raise HTTPException(status_code=400,detail="Invalid OAuth state.")
    async with httpx.AsyncClient(timeout=15) as client:
        tr=await client.post("https://discord.com/api/oauth2/token",data={"client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"grant_type":"authorization_code","code":code,"redirect_uri":REDIRECT_URI},headers={"Content-Type":"application/x-www-form-urlencoded"});tr.raise_for_status();access_token=tr.json()["access_token"]
    user=await discord_get("/users/@me",access_token);guilds=await discord_get("/users/@me/guilds",access_token)
    request.session["user"]={"id":user["id"],"username":user.get("username"),"global_name":user.get("global_name")}
    request.session["guilds"]=[{"id":g["id"],"name":g["name"],"can_manage":bool(int(g.get("permissions","0"))&MANAGE_GUILD) or bool(g.get("owner"))} for g in guilds]
    request.session["csrf"]=secrets.token_urlsafe(24);return RedirectResponse("/")
@app.get("/logout")
async def logout(request:Request):request.session.clear();return RedirectResponse("/")

async def load_dashboard_data(guild_id):
    async with pool.acquire() as conn:
        settings=await conn.fetchrow("SELECT responder_role_ids,request_channel_id,incident_category_id FROM rescue_guild_settings WHERE guild_id=$1",guild_id);service_rows=await conn.fetch("SELECT service,role_ids FROM rescue_service_role_settings WHERE guild_id=$1",guild_id);log_channel=await conn.fetchval("SELECT channel_id FROM rescue_log_channels WHERE guild_id=$1",guild_id);dispatch_channel=await conn.fetchval("SELECT channel_id FROM rescue_dispatch_boards WHERE guild_id=$1",guild_id)
        active=await conn.fetch("SELECT incident_number,callsign,service,location,priority,status,primary_responder_id,created_at FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed' ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2 ELSE 3 END, incident_number ASC LIMIT 50",guild_id)
        history=await conn.fetch("SELECT incident_number,callsign,service,priority,primary_responder_id,created_at,responded_at,closed_at FROM rescue_incidents WHERE guild_id=$1 AND status='closed' ORDER BY closed_at DESC NULLS LAST LIMIT 20",guild_id)
        stats=await conn.fetchrow("SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE status<>'closed') AS active,COUNT(*) FILTER (WHERE status='closed') AS closed,COUNT(*) FILTER (WHERE priority='critical') AS p1,AVG(EXTRACT(EPOCH FROM (responded_at-created_at))) FILTER (WHERE responded_at IS NOT NULL) AS avg_response FROM rescue_incidents WHERE guild_id=$1",guild_id)
    return settings,{r["service"]:list(r["role_ids"] or []) for r in service_rows},log_channel,dispatch_channel,active,history,stats
def option(value,label,selected=False):return f'<option value="{esc(value)}"{" selected" if selected else ""}>{esc(label)}</option>'
@app.get("/guild/{guild_id}",response_class=HTMLResponse)
async def guild_dashboard(request:Request,guild_id:int,saved:int=0):
    guild_info=require_guild_access(request,guild_id);await require_bot_installed(guild_id)
    try:roles,channels=await discord_get(f"/guilds/{guild_id}/roles"),await discord_get(f"/guilds/{guild_id}/channels")
    except httpx.HTTPStatusError as exc:raise HTTPException(status_code=502,detail=f"Discord API error: {exc.response.status_code}")
    settings,service_roles,log_channel,dispatch_channel,active,history,stats=await load_dashboard_data(guild_id);selected_responder_ids=set(settings["responder_role_ids"] or []) if settings else set();request_channel_id=settings["request_channel_id"] if settings else None;incident_category_id=settings["incident_category_id"] if settings else None
    selectable_roles=[r for r in roles if r.get("name")!="@everyone" and not r.get("managed")];text_channels=[c for c in channels if c.get("type")==0];categories=[c for c in channels if c.get("type")==4]
    role_options="".join(option(r["id"],r["name"],int(r["id"]) in selected_responder_ids) for r in selectable_roles);category_options='<option value="">Use / create Active Incidents</option>'+"".join(option(c["id"],c["name"],int(c["id"])==incident_category_id) for c in categories)
    def channel_select(name,selected):return f'<select name="{name}"><option value="">Not configured</option>'+"".join(option(c["id"],f'#{c["name"]}',int(c["id"])==selected) for c in text_channels)+'</select>'
    service_html=""
    for key,label in SERVICES.items():
        selected=set(service_roles.get(key,[]));opts="".join(option(r["id"],r["name"],int(r["id"]) in selected) for r in selectable_roles);service_html+=f'<label>{esc(label)} paging roles</label><select multiple name="service_{esc(key)}">{opts}</select>'
    active_rows="".join(f'<tr><td>RESCUE-{r["incident_number"]:04d}</td><td><span class="pill {"p1" if r["priority"]=="critical" else "p2" if r["priority"]=="urgent" else "p3"}">{esc(PRIORITIES.get(r["priority"],r["priority"]))}</span></td><td>{esc(STATUSES.get(r["status"],r["status"]))}</td><td>{esc(SERVICES.get(r["service"],r["service"]))}</td><td>{esc(r["callsign"])}</td><td>{esc(r["location"])}</td><td>{"<@"+str(r["primary_responder_id"])+">" if r["primary_responder_id"] else "Unassigned"}</td></tr>' for r in active) or '<tr><td colspan="7" class="muted">No active incidents.</td></tr>'
    history_rows="".join(f'<tr><td>RESCUE-{r["incident_number"]:04d}</td><td>{esc(SERVICES.get(r["service"],r["service"]))}</td><td>{esc(PRIORITIES.get(r["priority"],r["priority"]))}</td><td>{esc(r["callsign"])}</td><td>{duration((r["responded_at"]-r["created_at"]).total_seconds()) if r["responded_at"] else "—"}</td><td>{format_dt(r["closed_at"])}</td></tr>' for r in history) or '<tr><td colspan="6" class="muted">No completed incidents.</td></tr>'
    notice='<div class="notice">Configuration saved. The Discord bot refreshes responder/category settings within about 10 seconds.</div>' if saved else "";csrf=esc(request.session.get("csrf"))
    body=f'''{notice}<div class="section-nav"><a href="#overview">Overview</a><a href="#active">Active Incidents</a><a href="#history">History</a><a href="#config">Configuration</a></div><div class="grid" id="overview"><div class="card span3"><div class="label">Total Incidents</div><div class="metric">{stats['total']}</div></div><div class="card span3"><div class="label">Active</div><div class="metric">{stats['active']}</div></div><div class="card span3"><div class="label">Completed</div><div class="metric">{stats['closed']}</div></div><div class="card span3"><div class="label">Avg Claim Time</div><div class="metric">{duration(stats['avg_response'])}</div></div><div class="card span12" id="active"><h2>Active Incidents</h2><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Priority</th><th>Status</th><th>Service</th><th>Callsign</th><th>Location</th><th>Primary</th></tr></thead><tbody>{active_rows}</tbody></table></div></div><div class="card span12" id="history"><h2>Recent Rescue History</h2><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Service</th><th>Priority</th><th>Callsign</th><th>Claim Time</th><th>Closed</th></tr></thead><tbody>{history_rows}</tbody></table></div></div><div class="card span12" id="config"><h2>Discord Configuration</h2><p class="muted">Only users with Manage Server permission can access this page. Role and incident-category changes are database-backed and picked up by the bot automatically.</p><form method="post" action="/guild/{guild_id}/config"><input type="hidden" name="csrf" value="{csrf}"><div class="grid"><div class="span6"><label>Responder roles (may use controls on any incident)</label><select multiple name="responder_roles">{role_options}</select><p class="muted">Select one or more roles.</p>{service_html}</div><div class="span6"><label>Request Assistance channel</label>{channel_select('request_channel',request_channel_id)}<label>Live Dispatch Board channel</label>{channel_select('dispatch_channel',dispatch_channel)}<label>Completed Rescue Log channel</label>{channel_select('log_channel',log_channel)}<label>Active Incident category</label><select name="incident_category">{category_options}</select><p class="muted">Moving the Request or Dispatch channel posts a fresh panel/board in the newly selected channel. Old messages are left in place so nothing is deleted unexpectedly.</p></div></div><div style="margin-top:18px"><button class="btn" type="submit">Save Configuration</button> <a class="btn secondary" href="/">Back to Servers</a></div></form></div></div>'''
    return page(guild_info["name"],body,current_user(request))

async def build_dispatch_embed(guild_id):
    async with pool.acquire() as conn:rows=await conn.fetch("SELECT incident_number,callsign,service,location,priority,status,primary_responder_id,created_at,channel_id FROM rescue_incidents WHERE guild_id=$1 AND status<>'closed' ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2 ELSE 3 END, incident_number ASC LIMIT 24",guild_id)
    fields=[]
    if not rows:fields.append({"name":"✅ No Active Incidents","value":"All rescue calls are currently clear.","inline":False})
    for row in rows:
        primary=f"<@{row['primary_responder_id']}>" if row["primary_responder_id"] else "Unassigned";fields.append({"name":f"{PRIORITIES.get(row['priority'],row['priority'])} • RESCUE-{row['incident_number']:04d}","value":f"**{SERVICES.get(row['service'],row['service'])}** • {STATUSES.get(row['status'],row['status'])}\n**Callsign:** {row['callsign'][:45]}\n**Location:** {row['location'][:90]}\n**Primary:** {primary}\n**Channel:** <#{row['channel_id']}>","inline":False})
    return {"title":"📡 STAR CITIZEN RESCUE — LIVE DISPATCH BOARD","description":"Active rescue operations are sorted by priority and update automatically.","color":5793266,"fields":fields,"footer":{"text":f"Active Incidents: {len(rows)} • P1 incidents appear first"}}
async def post_request_panel(channel_id):
    return await discord_post(f"/channels/{channel_id}/messages",{"embeds":[{"title":"🚨 STAR CITIZEN RESCUE DISPATCH","description":"Need assistance in the 'verse? Use the button below to open a rescue request. You will choose a service and request priority before entering the incident details.","color":15548997,"fields":[{"name":"Available Services","value":"🚑 Medical Rescue\n🔎 Search & Rescue\n🔧 Repair / Refuel\n🛡️ Security / Escort\n🚀 Recovery / Transport","inline":False},{"name":"Priority Levels","value":"🔴 P1 Critical — responder/management escalation only\n🟠 P2 Urgent — time-sensitive request\n🟢 P3 Standard — routine assistance","inline":False}]}],"components":[{"type":1,"components":[{"type":2,"style":4,"label":"Request Assistance","emoji":{"name":"🚨"},"custom_id":"rescue:request"}]}]})
@app.post("/guild/{guild_id}/config")
async def save_config(request:Request,guild_id:int):
    require_guild_access(request,guild_id);await require_bot_installed(guild_id);form=await request.form();csrf=str(form.get("csrf") or "");expected=str(request.session.get("csrf") or "")
    if not csrf or not expected or not secrets.compare_digest(csrf,expected):raise HTTPException(status_code=400,detail="Invalid CSRF token.")
    roles=await discord_get(f"/guilds/{guild_id}/roles");channels=await discord_get(f"/guilds/{guild_id}/channels");valid_role_ids={int(r["id"]) for r in roles if r.get("name")!="@everyone" and not r.get("managed")};valid_text_ids={int(c["id"]) for c in channels if c.get("type")==0};valid_category_ids={int(c["id"]) for c in channels if c.get("type")==4};responder_ids=[int(v) for v in form.getlist("responder_roles") if str(v).isdigit() and int(v) in valid_role_ids]
    if not responder_ids:raise HTTPException(status_code=400,detail="Select at least one responder role.")
    def selected_id(name,valid_ids):
        value=str(form.get(name) or "")
        if not value:return None
        value=int(value)
        if value not in valid_ids:raise HTTPException(status_code=400,detail=f"Invalid {name} selection.")
        return value
    request_channel=selected_id("request_channel",valid_text_ids);dispatch_channel=selected_id("dispatch_channel",valid_text_ids);log_channel=selected_id("log_channel",valid_text_ids);incident_category=selected_id("incident_category",valid_category_ids);service_map={service:[int(v) for v in form.getlist(f"service_{service}") if str(v).isdigit() and int(v) in valid_role_ids] for service in SERVICES}
    async with pool.acquire() as conn:
        previous=await conn.fetchrow("SELECT request_channel_id FROM rescue_guild_settings WHERE guild_id=$1",guild_id);previous_dispatch=await conn.fetchval("SELECT channel_id FROM rescue_dispatch_boards WHERE guild_id=$1",guild_id)
        async with conn.transaction():
            await conn.execute("INSERT INTO rescue_guild_settings(guild_id,responder_role_ids,request_channel_id,incident_category_id,updated_at) VALUES($1,$2,$3,$4,NOW()) ON CONFLICT(guild_id) DO UPDATE SET responder_role_ids=EXCLUDED.responder_role_ids,request_channel_id=EXCLUDED.request_channel_id,incident_category_id=EXCLUDED.incident_category_id,updated_at=NOW()",guild_id,responder_ids,request_channel,incident_category)
            for service,role_ids in service_map.items():await conn.execute("INSERT INTO rescue_service_role_settings(guild_id,service,role_ids,updated_at) VALUES($1,$2,$3,NOW()) ON CONFLICT(guild_id,service) DO UPDATE SET role_ids=EXCLUDED.role_ids,updated_at=NOW()",guild_id,service,role_ids)
            if log_channel:await conn.execute("INSERT INTO rescue_log_channels(guild_id,channel_id,updated_at) VALUES($1,$2,NOW()) ON CONFLICT(guild_id) DO UPDATE SET channel_id=EXCLUDED.channel_id,updated_at=NOW()",guild_id,log_channel)
            else:await conn.execute("DELETE FROM rescue_log_channels WHERE guild_id=$1",guild_id)
    previous_request=previous["request_channel_id"] if previous else None
    if request_channel and request_channel!=previous_request:await post_request_panel(request_channel)
    if dispatch_channel and dispatch_channel!=previous_dispatch:
        message=await discord_post(f"/channels/{dispatch_channel}/messages",{"embeds":[await build_dispatch_embed(guild_id)]})
        async with pool.acquire() as conn:await conn.execute("INSERT INTO rescue_dispatch_boards(guild_id,channel_id,message_id,updated_at) VALUES($1,$2,$3,NOW()) ON CONFLICT(guild_id) DO UPDATE SET channel_id=EXCLUDED.channel_id,message_id=EXCLUDED.message_id,updated_at=NOW()",guild_id,dispatch_channel,int(message["id"]))
    elif not dispatch_channel:
        async with pool.acquire() as conn:await conn.execute("DELETE FROM rescue_dispatch_boards WHERE guild_id=$1",guild_id)
    return RedirectResponse(f"/guild/{guild_id}?saved=1",status_code=303)
