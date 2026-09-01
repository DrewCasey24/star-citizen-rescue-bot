import html
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


def esc(value):
    return html.escape(str(value or ""), quote=True)


def parse_date(value, end=False):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid performance date filter.")
    return result + timedelta(days=1) if end else result


_original_page = base.page


def page_with_performance_link(title, body, user=None):
    if 'class="section-nav"' in body and '/performance' not in body:
        import re
        match = re.search(r'href="/guild/(\d+)', body)
        if match:
            guild_id = match.group(1)
            body = body.replace(
                '<div class="section-nav">',
                f'<div class="section-nav"><a href="/guild/{guild_id}/performance">Responder Performance</a>',
                1,
            )
    return _original_page(title, body, user)


base.page = page_with_performance_link
app = base.app


@app.get("/guild/{guild_id}/performance", response_class=HTMLResponse)
async def responder_performance(request: Request, guild_id: int, date_from: str = "", date_to: str = ""):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    from_dt = parse_date(date_from)
    to_dt = parse_date(date_to, end=True)

    conditions = ["ri.guild_id=$1"]
    args = [guild_id]
    if from_dt:
        args.append(from_dt)
        conditions.append(f"ri.created_at>=${len(args)}")
    if to_dt:
        args.append(to_dt)
        conditions.append(f"ri.created_at<${len(args)}")
    where_sql = " AND ".join(conditions)

    async with base.pool.acquire() as conn:
        summary = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS incidents,
                   COUNT(*) FILTER (WHERE status='closed') AS completed,
                   AVG(EXTRACT(EPOCH FROM (responded_at-created_at))) FILTER (WHERE responded_at IS NOT NULL) AS avg_claim,
                   AVG(EXTRACT(EPOCH FROM (arrived_at-created_at))) FILTER (WHERE arrived_at IS NOT NULL) AS avg_scene
            FROM rescue_incidents ri WHERE {where_sql}
            """,
            *args,
        )
        primary_rows = await conn.fetch(
            f"""
            SELECT ri.primary_responder_id AS user_id,
                   COUNT(*) AS primary_calls,
                   COUNT(*) FILTER (WHERE ri.status='closed') AS completed_primary,
                   AVG(EXTRACT(EPOCH FROM (ri.responded_at-ri.created_at))) FILTER (WHERE ri.responded_at IS NOT NULL) AS avg_claim,
                   AVG(EXTRACT(EPOCH FROM (ri.arrived_at-ri.created_at))) FILTER (WHERE ri.arrived_at IS NOT NULL) AS avg_scene
            FROM rescue_incidents ri
            WHERE {where_sql} AND ri.primary_responder_id IS NOT NULL
            GROUP BY ri.primary_responder_id
            """,
            *args,
        )
        participation_rows = await conn.fetch(
            f"""
            WITH filtered AS (
                SELECT ri.* FROM rescue_incidents ri WHERE {where_sql}
            ), participants AS (
                SELECT primary_responder_id AS user_id, channel_id FROM filtered WHERE primary_responder_id IS NOT NULL
                UNION
                SELECT rr.user_id, rr.channel_id
                FROM rescue_incident_responders rr
                JOIN filtered f ON f.channel_id=rr.channel_id
                UNION
                SELECT e.actor_id AS user_id, f.channel_id
                FROM rescue_incident_events e
                JOIN filtered f ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL AND e.event_type IN ('primary_assigned','responder_joined')
            )
            SELECT user_id, COUNT(DISTINCT channel_id) AS total_calls
            FROM participants
            GROUP BY user_id
            """,
            *args,
        )
        ledger_rows = await conn.fetch(
            f"""
            WITH filtered AS (
                SELECT guild_id,incident_number FROM rescue_incidents ri WHERE {where_sql}
            )
            SELECT e.actor_id AS user_id,
                   COUNT(*) FILTER (WHERE e.event_type='responder_left') AS withdrawals
            FROM rescue_incident_events e
            JOIN filtered f ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
            WHERE e.actor_id IS NOT NULL
            GROUP BY e.actor_id
            """,
            *args,
        )

    stats = {}
    for row in participation_rows:
        stats[int(row["user_id"])] = {"total_calls": int(row["total_calls"] or 0)}
    for row in primary_rows:
        item = stats.setdefault(int(row["user_id"]), {"total_calls": 0})
        item.update(
            primary_calls=int(row["primary_calls"] or 0),
            completed_primary=int(row["completed_primary"] or 0),
            avg_claim=row["avg_claim"],
            avg_scene=row["avg_scene"],
        )
    for row in ledger_rows:
        item = stats.setdefault(int(row["user_id"]), {"total_calls": 0})
        item["withdrawals"] = int(row["withdrawals"] or 0)

    names = await base.member_names(guild_id, list(stats))
    ranked = sorted(
        stats.items(),
        key=lambda pair: (
            -pair[1].get("total_calls", 0),
            -pair[1].get("primary_calls", 0),
            names.get(pair[0], "").lower(),
        ),
    )
    rows_html = "".join(
        f'''<tr><td><a class="incident-link" href="/guild/{guild_id}/responder/{uid}">{esc(names.get(uid, "Discord User"))}</a></td><td>{s.get("total_calls",0)}</td><td>{s.get("primary_calls",0)}</td><td>{max(0,s.get("total_calls",0)-s.get("primary_calls",0))}</td><td>{s.get("withdrawals",0)}</td><td>{base.duration(s.get("avg_claim"))}</td><td>{base.duration(s.get("avg_scene"))}</td></tr>'''
        for uid, s in ranked
    ) or '<tr><td colspan="7" class="muted">No responder activity in this period.</td></tr>'

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}">← Back to Dashboard</a><a href="/guild/{guild_id}/history">Search History</a></div>
<div class="card"><div style="display:flex;justify-content:space-between;gap:24px;align-items:flex-start;flex-wrap:wrap"><div style="flex:1 1 420px"><h2>Responder Performance</h2><p class="muted">Participation includes both primary and support response activity. Select a responder to open their detailed profile.</p></div><form method="get" action="/guild/{guild_id}/performance" style="display:grid;grid-template-columns:minmax(170px,1fr) minmax(170px,1fr);column-gap:28px;row-gap:12px;align-items:end;min-width:min(100%,390px);max-width:460px;width:100%"><div><label>From</label><input style="width:100%" type="date" name="date_from" value="{esc(date_from)}"></div><div><label>To</label><input style="width:100%" type="date" name="date_to" value="{esc(date_to)}"></div><div style="grid-column:1/-1;display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap"><button class="btn" type="submit">Apply</button><a class="btn secondary" href="/guild/{guild_id}/performance">Clear</a></div></form></div></div>
<div class="grid" style="margin-top:16px"><div class="card span3"><div class="label">Incidents</div><div class="metric">{int(summary['incidents'] or 0)}</div></div><div class="card span3"><div class="label">Completed</div><div class="metric">{int(summary['completed'] or 0)}</div></div><div class="card span3"><div class="label">Avg Claim</div><div class="metric">{base.duration(summary['avg_claim'])}</div></div><div class="card span3"><div class="label">Avg On Scene</div><div class="metric">{base.duration(summary['avg_scene'])}</div></div>
<div class="card span12"><h2>Responder Activity</h2><p class="muted">Total Responses counts distinct incidents in which the member served as primary or support. Withdrawals are preserved from the permanent event ledger.</p><div style="overflow:auto"><table><thead><tr><th>Responder</th><th>Total Responses</th><th>Primary</th><th>Support</th><th>Withdrawals</th><th>Avg Claim</th><th>Avg On Scene</th></tr></thead><tbody>{rows_html}</tbody></table></div></div></div>'''
    return base.page(f"Responder Performance · {guild_info['name']}", body, base.current_user(request))


@app.get("/guild/{guild_id}/responder/{user_id}", response_class=HTMLResponse)
async def responder_profile(request: Request, guild_id: int, user_id: int):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)

    async with base.pool.acquire() as conn:
        metrics = await conn.fetchrow(
            """
            WITH participation AS (
                SELECT ri.incident_number
                FROM rescue_incidents ri
                WHERE ri.guild_id=$1 AND ri.primary_responder_id=$2
                UNION
                SELECT ri.incident_number
                FROM rescue_incident_responders rr
                JOIN rescue_incidents ri ON ri.channel_id=rr.channel_id
                WHERE ri.guild_id=$1 AND rr.user_id=$2
                UNION
                SELECT e.incident_number
                FROM rescue_incident_events e
                WHERE e.guild_id=$1 AND e.actor_id=$2
                  AND e.event_type IN ('primary_assigned','responder_joined')
            ), primary_incidents AS (
                SELECT ri.incident_number
                FROM rescue_incidents ri
                WHERE ri.guild_id=$1 AND ri.primary_responder_id=$2
                UNION
                SELECT e.incident_number
                FROM rescue_incident_events e
                WHERE e.guild_id=$1 AND e.actor_id=$2 AND e.event_type='primary_assigned'
            )
            SELECT
                (SELECT COUNT(*) FROM participation) AS total_calls,
                (SELECT COUNT(*) FROM primary_incidents) AS primary_calls,
                (SELECT COUNT(*) FROM participation p WHERE NOT EXISTS (SELECT 1 FROM primary_incidents pi WHERE pi.incident_number=p.incident_number)) AS support_only_calls,
                (SELECT COUNT(*) FROM rescue_incident_events e WHERE e.guild_id=$1 AND e.actor_id=$2 AND e.event_type='responder_left') AS withdrawals,
                (SELECT COUNT(*) FROM rescue_incident_events e WHERE e.guild_id=$1 AND e.actor_id=$2 AND e.event_type='arrived') AS arrivals_reported,
                (SELECT COUNT(*) FROM rescue_incident_events e WHERE e.guild_id=$1 AND e.actor_id=$2 AND e.event_type='backup_requested') AS backup_requests
            """,
            guild_id,
            user_id,
        )
        timing = await conn.fetchrow(
            """
            WITH claims AS (
                SELECT ri.incident_number,
                       EXTRACT(EPOCH FROM (ri.responded_at-ri.created_at)) AS seconds
                FROM rescue_incidents ri
                WHERE ri.guild_id=$1 AND ri.primary_responder_id=$2 AND ri.responded_at IS NOT NULL
                UNION ALL
                SELECT ri.incident_number,
                       EXTRACT(EPOCH FROM (MIN(e.created_at)-ri.created_at)) AS seconds
                FROM rescue_incident_events e
                JOIN rescue_incidents ri ON ri.guild_id=e.guild_id AND ri.incident_number=e.incident_number
                WHERE e.guild_id=$1 AND e.actor_id=$2 AND e.event_type='primary_assigned'
                GROUP BY ri.incident_number,ri.created_at
            ), dedup AS (
                SELECT incident_number, MIN(seconds) AS seconds FROM claims WHERE seconds >= 0 GROUP BY incident_number
            )
            SELECT AVG(seconds) AS avg_claim FROM dedup
            """,
            guild_id,
            user_id,
        )
        services = await conn.fetch(
            """
            WITH participation AS (
                SELECT ri.incident_number
                FROM rescue_incidents ri
                WHERE ri.guild_id=$1 AND ri.primary_responder_id=$2
                UNION
                SELECT ri.incident_number
                FROM rescue_incident_responders rr
                JOIN rescue_incidents ri ON ri.channel_id=rr.channel_id
                WHERE ri.guild_id=$1 AND rr.user_id=$2
                UNION
                SELECT e.incident_number
                FROM rescue_incident_events e
                WHERE e.guild_id=$1 AND e.actor_id=$2
                  AND e.event_type IN ('primary_assigned','responder_joined')
            )
            SELECT ri.service, COUNT(*) AS total
            FROM participation p
            JOIN rescue_incidents ri ON ri.guild_id=$1 AND ri.incident_number=p.incident_number
            GROUP BY ri.service
            ORDER BY total DESC, ri.service
            """,
            guild_id,
            user_id,
        )
        recent = await conn.fetch(
            """
            WITH participation AS (
                SELECT ri.incident_number
                FROM rescue_incidents ri
                WHERE ri.guild_id=$1 AND ri.primary_responder_id=$2
                UNION
                SELECT ri.incident_number
                FROM rescue_incident_responders rr
                JOIN rescue_incidents ri ON ri.channel_id=rr.channel_id
                WHERE ri.guild_id=$1 AND rr.user_id=$2
                UNION
                SELECT e.incident_number
                FROM rescue_incident_events e
                WHERE e.guild_id=$1 AND e.actor_id=$2
                  AND e.event_type IN ('primary_assigned','responder_joined')
            ), primary_incidents AS (
                SELECT ri.incident_number
                FROM rescue_incidents ri
                WHERE ri.guild_id=$1 AND ri.primary_responder_id=$2
                UNION
                SELECT e.incident_number
                FROM rescue_incident_events e
                WHERE e.guild_id=$1 AND e.actor_id=$2 AND e.event_type='primary_assigned'
            )
            SELECT ri.incident_number,ri.callsign,ri.service,ri.priority,ri.status,ri.created_at,
                   EXISTS (SELECT 1 FROM primary_incidents pi WHERE pi.incident_number=ri.incident_number) AS was_primary
            FROM participation p
            JOIN rescue_incidents ri ON ri.guild_id=$1 AND ri.incident_number=p.incident_number
            ORDER BY ri.created_at DESC
            LIMIT 20
            """,
            guild_id,
            user_id,
        )
        events = await conn.fetch(
            """
            SELECT incident_number,event_type,title,details,created_at
            FROM rescue_incident_events
            WHERE guild_id=$1 AND actor_id=$2
            ORDER BY created_at DESC,id DESC
            LIMIT 25
            """,
            guild_id,
            user_id,
        )

    names = await base.member_names(guild_id, [user_id])
    name = names.get(user_id, "Discord User")
    total_calls = int(metrics["total_calls"] or 0)
    if total_calls == 0 and not events:
        raise HTTPException(status_code=404, detail="No responder activity found for this member.")

    service_html = "".join(
        f'<tr><td>{esc(base.SERVICES.get(row["service"], row["service"]))}</td><td>{int(row["total"] or 0)}</td></tr>'
        for row in services
    ) or '<tr><td colspan="2" class="muted">No service activity recorded.</td></tr>'

    recent_html = "".join(
        f'<tr><td><a class="incident-link" href="/guild/{guild_id}/incident/{row["incident_number"]}">RESCUE-{row["incident_number"]:04d}</a></td><td>{"Primary" if row["was_primary"] else "Support"}</td><td>{esc(base.SERVICES.get(row["service"], row["service"]))}</td><td>{esc(base.PRIORITIES.get(row["priority"], row["priority"]))}</td><td>{esc(base.STATUSES.get(row["status"], row["status"]))}</td><td>{esc(row["callsign"])}</td><td>{base.format_dt(row["created_at"])}</td></tr>'
        for row in recent
    ) or '<tr><td colspan="7" class="muted">No incident participation recorded.</td></tr>'

    event_html = "".join(
        f'<div class="event"><div class="event-title">{esc(row["title"])}</div><div>{esc(row["details"])}</div><div class="event-meta">{base.format_dt(row["created_at"])} · <a href="/guild/{guild_id}/incident/{row["incident_number"]}">RESCUE-{row["incident_number"]:04d}</a></div></div>'
        for row in events
    ) or '<div class="muted">No ledger events recorded for this responder yet.</div>'

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}/performance">← Back to Responder Performance</a><a href="/guild/{guild_id}">Dashboard</a><a href="/guild/{guild_id}/history?responder={user_id}">Search This Responder</a></div>
<div class="card"><div class="label">Responder Profile</div><h1 style="margin:6px 0 8px">{esc(name)}</h1><p class="muted">Performance history is built from current incident records plus the permanent responder event ledger, so withdrawn participation remains visible.</p></div>
<div class="grid" style="margin-top:16px"><div class="card span3"><div class="label">Total Responses</div><div class="metric">{total_calls}</div></div><div class="card span3"><div class="label">Primary</div><div class="metric">{int(metrics['primary_calls'] or 0)}</div></div><div class="card span3"><div class="label">Support Only</div><div class="metric">{int(metrics['support_only_calls'] or 0)}</div></div><div class="card span3"><div class="label">Withdrawals</div><div class="metric">{int(metrics['withdrawals'] or 0)}</div></div>
<div class="card span4"><h2>Response Metrics</h2><div class="kv"><div>Avg primary claim</div><div>{base.duration(timing['avg_claim'])}</div><div>Arrivals reported</div><div>{int(metrics['arrivals_reported'] or 0)}</div><div>Backup requests</div><div>{int(metrics['backup_requests'] or 0)}</div></div><p class="control-note">Arrival and backup counts only attribute ledger events where this responder is recorded as the actor.</p></div>
<div class="card span8"><h2>Service Breakdown</h2><div style="overflow:auto"><table><thead><tr><th>Service</th><th>Responses</th></tr></thead><tbody>{service_html}</tbody></table></div></div>
<div class="card span12"><h2>Recent Incidents</h2><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Role</th><th>Service</th><th>Priority</th><th>Status</th><th>Callsign</th><th>Opened</th></tr></thead><tbody>{recent_html}</tbody></table></div></div>
<div class="card span12"><h2>Recent Responder Activity</h2><p class="muted">Most recent permanent ledger events attributed to this responder.</p><div class="timeline">{event_html}</div></div></div>'''
    return base.page(f"{name} · Responder Profile · {guild_info['name']}", body, base.current_user(request))
