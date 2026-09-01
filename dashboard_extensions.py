import html
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

import dashboard as base

app = base.app


def esc(value):
    return html.escape(str(value or ""), quote=True)


def fmt_duration(seconds):
    return base.duration(seconds)


def parse_date(value, end=False):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid performance date filter.")
    return result + timedelta(days=1) if end else result


@app.get("/guild/{guild_id}/performance", response_class=HTMLResponse)
async def responder_performance(
    request: Request,
    guild_id: int,
    date_from: str = "",
    date_to: str = "",
):
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
                   COUNT(*) FILTER (WHERE e.event_type='responder_joined') AS joins,
                   COUNT(*) FILTER (WHERE e.event_type='responder_left') AS withdrawals,
                   COUNT(*) FILTER (WHERE e.event_type='primary_assigned') AS primary_accepts,
                   COUNT(*) FILTER (WHERE e.event_type='arrived') AS arrivals,
                   COUNT(*) FILTER (WHERE e.event_type='backup_requested') AS backup_requests
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
        item.update(
            joins=int(row["joins"] or 0),
            withdrawals=int(row["withdrawals"] or 0),
            primary_accepts=int(row["primary_accepts"] or 0),
            arrivals=int(row["arrivals"] or 0),
            backup_requests=int(row["backup_requests"] or 0),
        )

    names = await base.member_names(guild_id, list(stats))
    ranked = sorted(stats.items(), key=lambda pair: (-pair[1].get("total_calls", 0), -pair[1].get("primary_calls", 0), names.get(pair[0], "").lower()))
    rows_html = "".join(
        f'''<tr><td><strong>{esc(names.get(uid, "Discord User"))}</strong></td><td>{s.get("total_calls",0)}</td><td>{s.get("primary_calls",0)}</td><td>{max(0,s.get("total_calls",0)-s.get("primary_calls",0))}</td><td>{s.get("withdrawals",0)}</td><td>{fmt_duration(s.get("avg_claim"))}</td><td>{fmt_duration(s.get("avg_scene"))}</td></tr>'''
        for uid, s in ranked
    ) or '<tr><td colspan="7" class="muted">No responder activity in this period.</td></tr>'

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}">← Back to Dashboard</a><a href="/guild/{guild_id}/history">Search History</a></div>
<div class="card"><div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap"><div><h2>Responder Performance</h2><p class="muted">Participation includes both primary and support response activity. Timing metrics apply to incidents where the responder is recorded as primary.</p></div><form method="get" action="/guild/{guild_id}/performance" style="display:flex;gap:8px;align-items:end;flex-wrap:wrap"><div><label>From</label><input type="date" name="date_from" value="{esc(date_from)}"></div><div><label>To</label><input type="date" name="date_to" value="{esc(date_to)}"></div><button class="btn" type="submit">Apply</button><a class="btn secondary" href="/guild/{guild_id}/performance">Clear</a></form></div></div>
<div class="grid" style="margin-top:16px"><div class="card span3"><div class="label">Incidents</div><div class="metric">{int(summary['incidents'] or 0)}</div></div><div class="card span3"><div class="label">Completed</div><div class="metric">{int(summary['completed'] or 0)}</div></div><div class="card span3"><div class="label">Avg Claim</div><div class="metric">{fmt_duration(summary['avg_claim'])}</div></div><div class="card span3"><div class="label">Avg On Scene</div><div class="metric">{fmt_duration(summary['avg_scene'])}</div></div>
<div class="card span12"><h2>Responder Activity</h2><p class="muted">Total Responses counts distinct incidents in which the member served as primary or support. Withdrawals are preserved from the permanent event ledger.</p><div style="overflow:auto"><table><thead><tr><th>Responder</th><th>Total Responses</th><th>Primary</th><th>Support</th><th>Withdrawals</th><th>Avg Claim</th><th>Avg On Scene</th></tr></thead><tbody>{rows_html}</tbody></table></div></div></div>'''
    return base.page(f"Responder Performance · {guild_info['name']}", body, base.current_user(request))


# Add a Performance link to the existing server dashboard without duplicating its implementation.
for route in app.routes:
    if getattr(route, "path", None) == "/guild/{guild_id}" and "GET" in getattr(route, "methods", set()):
        original_endpoint = route.endpoint

        async def dashboard_with_performance_link(request: Request, guild_id: int, saved: int = 0, _original=original_endpoint):
            response = await _original(request, guild_id, saved)
            if isinstance(response, HTMLResponse):
                text = response.body.decode("utf-8")
                marker = f'<a href="/guild/{guild_id}/history">Search History</a>'
                replacement = marker + f'<a href="/guild/{guild_id}/performance">Responder Performance</a>'
                if marker in text and f'/guild/{guild_id}/performance' not in text:
                    response.body = text.replace(marker, replacement, 1).encode("utf-8")
                    response.headers["content-length"] = str(len(response.body))
            return response

        route.endpoint = dashboard_with_performance_link
        break
