import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

import dashboard as core

app = core.app
_original_page = core.page


def page_with_performance(title, body, user=None):
    if 'class="section-nav"' in body and '/performance' not in body:
        match = re.search(r'href="/guild/(\d+)', body)
        if match:
            guild_id = match.group(1)
            body = body.replace(
                '<div class="section-nav">',
                f'<div class="section-nav"><a href="/guild/{guild_id}/performance">Responder Performance</a>',
                1,
            )
    return _original_page(title, body, user)


core.page = page_with_performance


def parse_date(value, end=False):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid performance date filter.")
    return dt + timedelta(days=1) if end else dt


@app.get("/guild/{guild_id}/performance", response_class=HTMLResponse)
async def responder_performance(request: Request, guild_id: int, date_from: str = "", date_to: str = ""):
    guild_info = core.require_guild_access(request, guild_id)
    await core.require_bot_installed(guild_id)
    from_dt = parse_date(date_from)
    to_dt = parse_date(date_to, end=True)

    conditions = ["ri.guild_id=$1"]
    args = [guild_id]
    if from_dt:
        args.append(from_dt)
        conditions.append(f"ri.created_at >= ${len(args)}")
    if to_dt:
        args.append(to_dt)
        conditions.append(f"ri.created_at < ${len(args)}")
    incident_where = " AND ".join(conditions)

    async with core.pool.acquire() as conn:
        overview = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS incidents,
                   COUNT(*) FILTER (WHERE ri.status='closed') AS completed,
                   AVG(EXTRACT(EPOCH FROM (ri.responded_at-ri.created_at))) FILTER (WHERE ri.responded_at IS NOT NULL) AS avg_claim,
                   AVG(EXTRACT(EPOCH FROM (ri.arrived_at-ri.created_at))) FILTER (WHERE ri.arrived_at IS NOT NULL) AS avg_arrival
            FROM rescue_incidents ri
            WHERE {incident_where}
            """,
            *args,
        )

        rows = await conn.fetch(
            f"""
            WITH filtered_incidents AS (
                SELECT ri.* FROM rescue_incidents ri WHERE {incident_where}
            ),
            ledger_participation AS (
                SELECT e.actor_id AS user_id,
                       e.incident_number,
                       COUNT(*) FILTER (WHERE e.event_type='primary_assigned') AS primary_count,
                       COUNT(*) FILTER (WHERE e.event_type='responder_joined') AS support_count,
                       COUNT(*) FILTER (WHERE e.event_type='responder_left') AS withdrawal_count,
                       AVG(EXTRACT(EPOCH FROM (e.created_at-fi.created_at))) FILTER (WHERE e.event_type='primary_assigned') AS claim_seconds,
                       AVG(EXTRACT(EPOCH FROM (e.created_at-fi.created_at))) FILTER (WHERE e.event_type='arrived') AS arrival_seconds
                FROM rescue_incident_events e
                JOIN filtered_incidents fi ON fi.incident_number=e.incident_number AND fi.guild_id=e.guild_id
                WHERE e.actor_id IS NOT NULL
                  AND e.event_type IN ('primary_assigned','responder_joined','responder_left','arrived')
                GROUP BY e.actor_id,e.incident_number
            ),
            legacy_participation AS (
                SELECT fi.primary_responder_id AS user_id,
                       fi.incident_number,
                       1::bigint AS primary_count,
                       0::bigint AS support_count,
                       0::bigint AS withdrawal_count,
                       EXTRACT(EPOCH FROM (fi.responded_at-fi.created_at)) AS claim_seconds,
                       EXTRACT(EPOCH FROM (fi.arrived_at-fi.created_at)) AS arrival_seconds
                FROM filtered_incidents fi
                WHERE fi.primary_responder_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM rescue_incident_events e
                      WHERE e.guild_id=fi.guild_id AND e.incident_number=fi.incident_number
                        AND e.event_type IN ('primary_assigned','responder_joined')
                  )
                UNION ALL
                SELECT rr.user_id,
                       fi.incident_number,
                       0::bigint,
                       1::bigint,
                       0::bigint,
                       NULL::numeric,
                       NULL::numeric
                FROM rescue_incident_responders rr
                JOIN filtered_incidents fi ON fi.channel_id=rr.channel_id
                WHERE rr.user_id IS DISTINCT FROM fi.primary_responder_id
                  AND NOT EXISTS (
                      SELECT 1 FROM rescue_incident_events e
                      WHERE e.guild_id=fi.guild_id AND e.incident_number=fi.incident_number
                        AND e.event_type IN ('primary_assigned','responder_joined')
                  )
            ),
            participation AS (
                SELECT * FROM ledger_participation
                UNION ALL
                SELECT * FROM legacy_participation
            )
            SELECT user_id,
                   COUNT(DISTINCT incident_number) AS responses,
                   SUM(primary_count)::bigint AS primary_assignments,
                   SUM(support_count)::bigint AS support_joins,
                   SUM(withdrawal_count)::bigint AS withdrawals,
                   AVG(claim_seconds) FILTER (WHERE claim_seconds IS NOT NULL) AS avg_claim,
                   AVG(arrival_seconds) FILTER (WHERE arrival_seconds IS NOT NULL) AS avg_arrival
            FROM participation
            WHERE user_id IS NOT NULL
            GROUP BY user_id
            ORDER BY responses DESC, primary_assignments DESC, support_joins DESC, user_id
            """,
            *args,
        )

    names = await core.member_names(guild_id, [row["user_id"] for row in rows])
    total_responders = len(rows)
    total_responses = sum(int(row["responses"] or 0) for row in rows)

    table_rows = "".join(
        f'<tr><td><strong>{core.esc(names.get(row["user_id"], "Discord User"))}</strong></td>'
        f'<td>{int(row["responses"] or 0)}</td>'
        f'<td>{int(row["primary_assignments"] or 0)}</td>'
        f'<td>{int(row["support_joins"] or 0)}</td>'
        f'<td>{int(row["withdrawals"] or 0)}</td>'
        f'<td>{core.duration(row["avg_claim"])}</td>'
        f'<td>{core.duration(row["avg_arrival"])}</td></tr>'
        for row in rows
    ) or '<tr><td colspan="7" class="muted">No responder activity is recorded for this date range.</td></tr>'

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}">← Back to Dashboard</a><a href="/guild/{guild_id}/history">Search History</a></div>
<div class="card"><div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap"><div><h2>Responder Performance</h2><p class="muted">Participation includes both primary and support responders. Withdrawal totals come from the permanent incident event ledger.</p></div></div>
<form method="get" action="/guild/{guild_id}/performance"><div class="filter-grid"><div><label>From date</label><input type="date" name="date_from" value="{core.esc(date_from)}"></div><div><label>To date</label><input type="date" name="date_to" value="{core.esc(date_to)}"></div><div class="filter-actions"><button class="btn" type="submit">Apply Dates</button><a class="btn secondary" href="/guild/{guild_id}/performance">Clear</a></div></div></form></div>
<div class="grid" style="margin-top:16px"><div class="card span3"><div class="label">Responders</div><div class="metric">{total_responders}</div></div><div class="card span3"><div class="label">Responder Participations</div><div class="metric">{total_responses}</div></div><div class="card span3"><div class="label">Incidents</div><div class="metric">{int(overview['incidents'] or 0)}</div></div><div class="card span3"><div class="label">Completed</div><div class="metric">{int(overview['completed'] or 0)}</div></div>
<div class="card span12"><h2>Responder Activity</h2><p class="muted">Claim time measures when that responder accepted primary responsibility. On-scene time is based on arrival actions attributed to that responder. Legacy incidents use the original incident record where ledger events are unavailable.</p><div style="overflow:auto"><table><thead><tr><th>Responder</th><th>Responses</th><th>Primary</th><th>Support</th><th>Withdrawals</th><th>Avg Claim</th><th>Avg On Scene</th></tr></thead><tbody>{table_rows}</tbody></table></div></div></div>'''
    return core.page(f"Responder Performance · {guild_info['name']}", body, core.current_user(request))
