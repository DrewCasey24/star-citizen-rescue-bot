"""Phase 2 dashboard streamlining: compact operational incident queue."""

from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


_original_page = base.page

PHASE2_CSS = r'''
.incident-queue{display:flex;flex-direction:column;gap:8px;margin-top:14px}
.incident-item{display:grid;grid-template-columns:minmax(150px,.9fr) minmax(150px,1fr) minmax(120px,.8fr) minmax(150px,1fr) auto;gap:14px;align-items:center;padding:13px 14px;border:1px solid rgba(116,153,196,.13);border-left:3px solid transparent;border-radius:12px;background:rgba(6,12,20,.38);transition:background .14s ease,border-color .14s ease,transform .14s ease}
.incident-item:hover{background:rgba(86,168,255,.055);border-color:rgba(86,168,255,.20);transform:translateY(-1px)}
.incident-item.priority-critical{border-left-color:#ff6b78}.incident-item.priority-urgent{border-left-color:#ffb45d}.incident-item.priority-standard{border-left-color:#45d49b}
.incident-main{min-width:0}.incident-number{font-size:14px;font-weight:780}.incident-callsign{margin-top:3px;color:#dcecff;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.incident-meta{min-width:0}.incident-meta .label{margin-bottom:4px}.incident-value{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.incident-age{text-align:right;white-space:nowrap}.incident-age strong{display:block;font-size:13px;color:#dbeaff}.incident-age span{font-size:10px;color:#7189a5;text-transform:uppercase;letter-spacing:.08em}
.queue-badges{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:5px}.attention-badge{display:inline-flex;align-items:center;padding:3px 7px;border-radius:999px;font-size:10px;font-weight:760;letter-spacing:.04em;border:1px solid rgba(255,180,93,.20);background:rgba(166,91,24,.16);color:#ffd09a}.attention-badge.critical{border-color:rgba(255,107,120,.22);background:rgba(166,54,68,.18);color:#ffb1b9}
.queue-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}.queue-summary{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.queue-empty{text-align:center;padding:30px 16px;color:#7f93aa;border:1px dashed rgba(116,153,196,.18);border-radius:12px;background:rgba(5,10,17,.22)}
@media(max-width:1050px){.incident-item{grid-template-columns:minmax(150px,1fr) minmax(130px,.8fr) minmax(140px,1fr) auto}.incident-service{display:none}}
@media(max-width:760px){.incident-item{grid-template-columns:1fr auto;gap:9px 12px}.incident-service,.incident-location{display:none}.incident-primary{grid-column:1/2}.incident-age{grid-column:2/3;grid-row:1/3;align-self:center}}
'''


def _age_text(created_at):
    if not created_at:
        return "—"
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - created_at).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def phase2_page(title, body, user=None):
    response = _original_page(title, body, user)
    markup = response.body.decode("utf-8")
    markup = markup.replace("</style>", PHASE2_CSS + "\n</style>", 1)
    return HTMLResponse(markup, status_code=response.status_code)


base.page = phase2_page


async def guild_overview_v2(request: Request, guild_id: int, saved: int = 0):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)

    async with base.pool.acquire() as conn:
        active = await conn.fetch(
            """
            SELECT incident_number,callsign,service,location,priority,status,primary_responder_id,created_at
            FROM rescue_incidents
            WHERE guild_id=$1 AND status<>'closed'
            ORDER BY CASE WHEN status='awaiting_responder' AND priority='critical' THEN 0
                          WHEN priority='critical' THEN 1
                          WHEN status='awaiting_responder' AND priority='urgent' THEN 2
                          WHEN priority='urgent' THEN 3
                          WHEN status='awaiting_responder' THEN 4 ELSE 5 END,
                     created_at ASC
            LIMIT 50
            """,
            guild_id,
        )
        metrics = await conn.fetchrow(
            """
            SELECT COUNT(*) FILTER (WHERE status<>'closed') AS active,
                   COUNT(*) FILTER (WHERE status='awaiting_responder') AS awaiting,
                   COUNT(*) FILTER (WHERE status<>'closed' AND priority IN ('critical','urgent')) AS urgent,
                   COUNT(*) FILTER (WHERE status='closed') AS completed
            FROM rescue_incidents WHERE guild_id=$1
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
    queue_items = []
    for r in active:
        priority_class = f"priority-{r['priority']}"
        pill_class = "p1" if r["priority"] == "critical" else "p2" if r["priority"] == "urgent" else "p3"
        badges = []
        if r["status"] == "awaiting_responder":
            badges.append('<span class="attention-badge">Needs Responder</span>')
        if r["priority"] == "critical":
            badges.append('<span class="attention-badge critical">Priority Attention</span>')
        badge_html = "".join(badges)
        primary = names.get(r["primary_responder_id"], "Assigned") if r["primary_responder_id"] else "Unassigned"
        primary_class = "status-assigned" if r["primary_responder_id"] else "status-unassigned"
        queue_items.append(
            f'''<a class="incident-item {priority_class}" href="/guild/{guild_id}/incident/{r["incident_number"]}">
<div class="incident-main"><div class="incident-number">RESCUE-{r["incident_number"]:04d}</div><div class="incident-callsign">{base.esc(r["callsign"])}</div><div class="queue-badges"><span class="pill {pill_class}">{base.esc(base.PRIORITIES.get(r["priority"], r["priority"]))}</span>{badge_html}</div></div>
<div class="incident-meta incident-service"><div class="label">Service</div><div class="incident-value">{base.esc(base.SERVICES.get(r["service"], r["service"]))}</div></div>
<div class="incident-meta incident-location"><div class="label">Location</div><div class="incident-value">{base.esc(r["location"])}</div></div>
<div class="incident-meta incident-primary"><div class="label">Status · Primary</div><div class="incident-value">{base.esc(base.STATUSES.get(r["status"], r["status"]))} · <span class="{primary_class}">{base.esc(primary)}</span></div></div>
<div class="incident-age"><strong>{_age_text(r["created_at"])}</strong><span>Open</span></div></a>'''
        )
    queue_html = "".join(queue_items) or '<div class="queue-empty">No active incidents. Operations are clear.</div>'

    event_html = "".join(
        f'<div class="event"><div class="event-title">{base.esc(r["title"])}</div><div class="event-meta"><a href="/guild/{guild_id}/incident/{r["incident_number"]}">RESCUE-{r["incident_number"]:04d}</a> · {base.format_dt(r["created_at"])}</div></div>'
        for r in recent_events
    ) or '<p class="muted">No recent activity.</p>'

    notice = '<div class="notice">Configuration saved. Bot configuration refreshes automatically.</div>' if saved else ""
    body = f'''{notice}<div class="overview-head"><div><h2>{base.esc(guild_info['name'])}</h2><div class="muted">Live rescue operations overview</div></div><div class="overview-actions"><a class="btn secondary" href="/guild/{guild_id}/history">Search History</a><a class="btn secondary" href="/guild/{guild_id}/settings">Settings</a></div></div>
<div class="grid"><div class="card span3"><div class="label">Active Incidents</div><div class="metric">{int(metrics['active'] or 0)}</div></div><div class="card span3"><div class="label">Awaiting Responder</div><div class="metric">{int(metrics['awaiting'] or 0)}</div></div><div class="card span3"><div class="label">P1 / P2 Active</div><div class="metric">{int(metrics['urgent'] or 0)}</div></div><div class="card span3"><div class="label">Responders Active</div><div class="metric">{int(responder_count or 0)}</div></div>
<div class="card span8" id="active"><div class="queue-head"><div><h2>Active Incident Queue</h2><p class="muted">Critical, urgent, and unassigned calls are surfaced first. Select any incident to open command view.</p></div><div class="queue-summary"><span class="status installed">{int(metrics['active'] or 0)} active</span><span class="status {'not-installed' if int(metrics['awaiting'] or 0) else 'installed'}">{int(metrics['awaiting'] or 0)} awaiting</span></div></div><div class="incident-queue">{queue_html}</div></div>
<div class="card span4"><h2>Recent Activity</h2><div class="timeline">{event_html}</div><a class="btn secondary" href="/guild/{guild_id}/history" style="margin-top:8px">View Full History</a></div></div>'''
    return base.page(f"{guild_info['name']} · Operations", body, base.current_user(request))


for route in base.app.routes:
    if getattr(route, "path", None) == "/guild/{guild_id}" and "GET" in getattr(route, "methods", set()):
        route.endpoint = guild_overview_v2
        break

app = base.app
