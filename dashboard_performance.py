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
        performance_rows = await conn.fetch(
            f"""
            WITH filtered AS (
                SELECT ri.* FROM rescue_incidents ri WHERE {where_sql}
            ), participation AS (
                SELECT primary_responder_id AS user_id, incident_number
                FROM filtered WHERE primary_responder_id IS NOT NULL
                UNION
                SELECT rr.user_id, f.incident_number
                FROM rescue_incident_responders rr
                JOIN filtered f ON f.channel_id=rr.channel_id
                UNION
                SELECT e.actor_id AS user_id, f.incident_number
                FROM rescue_incident_events e
                JOIN filtered f ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL AND e.event_type IN ('primary_assigned','responder_joined')
            ), primary_participation AS (
                SELECT primary_responder_id AS user_id, incident_number
                FROM filtered WHERE primary_responder_id IS NOT NULL
                UNION
                SELECT e.actor_id AS user_id, f.incident_number
                FROM rescue_incident_events e
                JOIN filtered f ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL AND e.event_type='primary_assigned'
            ), primary_claims AS (
                SELECT e.actor_id AS user_id, f.incident_number,
                       EXTRACT(EPOCH FROM (MIN(e.created_at)-f.created_at)) AS seconds
                FROM rescue_incident_events e
                JOIN filtered f ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL AND e.event_type='primary_assigned'
                GROUP BY e.actor_id,f.incident_number,f.created_at
                UNION ALL
                SELECT f.primary_responder_id AS user_id, f.incident_number,
                       EXTRACT(EPOCH FROM (f.responded_at-f.created_at)) AS seconds
                FROM filtered f
                WHERE f.primary_responder_id IS NOT NULL AND f.responded_at IS NOT NULL
            ), claim_dedup AS (
                SELECT user_id,incident_number,MIN(seconds) AS seconds
                FROM primary_claims
                WHERE seconds>=0
                GROUP BY user_id,incident_number
            ), participant_stats AS (
                SELECT p.user_id,
                       COUNT(*) AS total_calls,
                       COUNT(*) FILTER (WHERE pp.incident_number IS NOT NULL) AS primary_calls,
                       COUNT(*) FILTER (WHERE pp.incident_number IS NULL) AS support_calls
                FROM participation p
                LEFT JOIN primary_participation pp
                  ON pp.user_id=p.user_id AND pp.incident_number=p.incident_number
                GROUP BY p.user_id
            ), withdrawal_stats AS (
                SELECT e.actor_id AS user_id,COUNT(*) AS withdrawals
                FROM rescue_incident_events e
                JOIN filtered f ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL AND e.event_type='responder_left'
                GROUP BY e.actor_id
            ), claim_stats AS (
                SELECT user_id,AVG(seconds) AS avg_claim
                FROM claim_dedup
                GROUP BY user_id
            ), arrival_stats AS (
                SELECT e.actor_id AS user_id,
                       AVG(EXTRACT(EPOCH FROM (e.created_at-f.created_at))) AS avg_scene
                FROM rescue_incident_events e
                JOIN filtered f ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL AND e.event_type='arrived' AND e.created_at>=f.created_at
                GROUP BY e.actor_id
            ), users AS (
                SELECT user_id FROM participant_stats
                UNION SELECT user_id FROM withdrawal_stats
                UNION SELECT user_id FROM claim_stats
                UNION SELECT user_id FROM arrival_stats
            )
            SELECT u.user_id,
                   COALESCE(ps.total_calls,0) AS total_calls,
                   COALESCE(ps.primary_calls,0) AS primary_calls,
                   COALESCE(ps.support_calls,0) AS support_calls,
                   COALESCE(ws.withdrawals,0) AS withdrawals,
                   cs.avg_claim,
                   ars.avg_scene
            FROM users u
            LEFT JOIN participant_stats ps ON ps.user_id=u.user_id
            LEFT JOIN withdrawal_stats ws ON ws.user_id=u.user_id
            LEFT JOIN claim_stats cs ON cs.user_id=u.user_id
            LEFT JOIN arrival_stats ars ON ars.user_id=u.user_id
            """,
            *args,
        )

    stats = {
        int(row["user_id"]): {
            "total_calls": int(row["total_calls"] or 0),
            "primary_calls": int(row["primary_calls"] or 0),
            "support_calls": int(row["support_calls"] or 0),
            "withdrawals": int(row["withdrawals"] or 0),
            "avg_claim": row["avg_claim"],
            "avg_scene": row["avg_scene"],
        }
        for row in performance_rows
    }

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
        f'''<tr><td><a class="incident-link" href="/guild/{guild_id}/responder/{uid}">{esc(names.get(uid, "Discord User"))}</a></td><td>{s.get("total_calls",0)}</td><td>{s.get("primary_calls",0)}</td><td>{s.get("support_calls",0)}</td><td>{s.get("withdrawals",0)}</td><td>{base.duration(s.get("avg_claim"))}</td><td>{base.duration(s.get("avg_scene"))}</td></tr>'''
        for uid, s in ranked
    ) or '<tr><td colspan="7" class="muted">No responder activity in this period.</td></tr>'

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}">← Back to Dashboard</a><a href="/guild/{guild_id}/history">Search History</a></div>
<div class="card"><div style="display:flex;justify-content:space-between;gap:24px;align-items:flex-start;flex-wrap:wrap"><div style="flex:1 1 420px"><h2>Responder Performance</h2><p class="muted">Participation includes both primary and support response activity. Select a responder to open their detailed profile.</p></div><form method="get" action="/guild/{guild_id}/performance" style="display:grid;grid-template-columns:minmax(170px,1fr) minmax(170px,1fr);column-gap:28px;row-gap:12px;align-items:end;min-width:min(100%,390px);max-width:460px;width:100%"><div><label>From</label><input style="width:100%" type="date" name="date_from" value="{esc(date_from)}"></div><div><label>To</label><input style="width:100%" type="date" name="date_to" value="{esc(date_to)}"></div><div style="grid-column:1/-1;display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap"><button class="btn" type="submit">Apply</button><a class="btn secondary" href="/guild/{guild_id}/performance">Clear</a></div></form></div></div>
<div class="grid" style="margin-top:16px"><div class="card span3"><div class="label">Incidents</div><div class="metric">{int(summary['incidents'] or 0)}</div></div><div class="card span3"><div class="label">Completed</div><div class="metric">{int(summary['completed'] or 0)}</div></div><div class="card span3"><div class="label">Avg Claim</div><div class="metric">{base.duration(summary['avg_claim'])}</div></div><div class="card span3"><div class="label">Avg On Scene</div><div class="metric">{base.duration(summary['avg_scene'])}</div></div>
<div class="card span12"><h2>Responder Activity</h2><p class="muted">Total Responses, Primary, Support, Withdrawals, and responder timing use the permanent event ledger with current incident state retained as a compatibility fallback for older incidents.</p><div style="overflow:auto"><table><thead><tr><th>Responder</th><th>Total Responses</th><th>Primary</th><th>Support</th><th>Withdrawals</th><th>Avg Claim</th><th>Avg On Scene</th></tr></thead><tbody>{rows_html}</tbody></table></div></div></div>'''
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
    ) or '<tr><td colspan="7" class="muted">No incidents recorded.</td></tr>'

    event_html = "".join(
        f'<div class="timeline-item"><div><strong>{esc(row["title"])}</strong> · <a class="incident-link" href="/guild/{guild_id}/incident/{row["incident_number"]}">RESCUE-{row["incident_number"]:04d}</a></div><div>{esc(row["details"])}</div><div class="muted">{base.format_dt(row["created_at"])}</div></div>'
        for row in events
    ) or '<p class="muted">No ledger activity recorded.</p>'

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}/performance">← Responder Performance</a><a href="/guild/{guild_id}">Dashboard</a><a href="/guild/{guild_id}/history">Search History</a></div>
<div class="card"><h2>{esc(name)}</h2><p class="muted">Responder performance profile based on permanent incident participation and event history.</p></div>
<div class="grid" style="margin-top:16px"><div class="card span2"><div class="label">Total Responses</div><div class="metric">{total_calls}</div></div><div class="card span2"><div class="label">Primary</div><div class="metric">{int(metrics['primary_calls'] or 0)}</div></div><div class="card span2"><div class="label">Support</div><div class="metric">{int(metrics['support_only_calls'] or 0)}</div></div><div class="card span2"><div class="label">Withdrawals</div><div class="metric">{int(metrics['withdrawals'] or 0)}</div></div><div class="card span2"><div class="label">Avg Primary Claim</div><div class="metric">{base.duration(timing['avg_claim'])}</div></div><div class="card span2"><div class="label">Arrivals</div><div class="metric">{int(metrics['arrivals_reported'] or 0)}</div></div>
<div class="card span4"><h2>Service Breakdown</h2><div style="overflow:auto"><table><thead><tr><th>Service</th><th>Responses</th></tr></thead><tbody>{service_html}</tbody></table></div></div>
<div class="card span8"><h2>Recent Incidents</h2><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Role</th><th>Service</th><th>Priority</th><th>Status</th><th>Callsign</th><th>Created</th></tr></thead><tbody>{recent_html}</tbody></table></div></div>
<div class="card span12"><h2>Recent Activity</h2><p class="muted">Backup Requests: {int(metrics['backup_requests'] or 0)}</p><div class="timeline">{event_html}</div></div></div>'''
    return base.page(f"{name} · Responder Performance", body, base.current_user(request))
