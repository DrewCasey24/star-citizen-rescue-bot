import html

from fastapi import Request
from fastapi.responses import HTMLResponse

import dashboard_core as base
import dashboard_performance as performance


def esc(value):
    return html.escape(str(value or ""), quote=True)


_previous_page = base.page


def page_with_service_rankings_link(title, body, user=None):
    if 'class="section-nav"' in body and "Service Rankings" not in body:
        import re

        match = re.search(r'href="/guild/(\d+)', body)
        if match and ("Responder Performance" in body or "/performance" in body):
            guild_id = match.group(1)
            body = body.replace(
                '<div class="section-nav">',
                f'<div class="section-nav"><a href="/guild/{guild_id}/performance/services">Service Rankings</a>',
                1,
            )
    return _previous_page(title, body, user)


base.page = page_with_service_rankings_link
app = base.app


@app.get("/guild/{guild_id}/performance/services", response_class=HTMLResponse)
async def service_responder_rankings(
    request: Request,
    guild_id: int,
    date_from: str = "",
    date_to: str = "",
):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)

    from_dt = performance.parse_date(date_from)
    to_dt = performance.parse_date(date_to, end=True)

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
        rows = await conn.fetch(
            f"""
            WITH filtered AS (
                SELECT ri.*
                FROM rescue_incidents ri
                WHERE {where_sql}
            ), participation AS (
                SELECT primary_responder_id AS user_id, incident_number, service
                FROM filtered
                WHERE primary_responder_id IS NOT NULL
                UNION
                SELECT rr.user_id, f.incident_number, f.service
                FROM rescue_incident_responders rr
                JOIN filtered f ON f.channel_id=rr.channel_id
                UNION
                SELECT e.actor_id AS user_id, f.incident_number, f.service
                FROM rescue_incident_events e
                JOIN filtered f
                  ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL
                  AND e.event_type IN ('primary_assigned','responder_joined')
            ), primary_participation AS (
                SELECT primary_responder_id AS user_id, incident_number, service
                FROM filtered
                WHERE primary_responder_id IS NOT NULL
                UNION
                SELECT e.actor_id AS user_id, f.incident_number, f.service
                FROM rescue_incident_events e
                JOIN filtered f
                  ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL
                  AND e.event_type='primary_assigned'
            ), primary_claims AS (
                SELECT e.actor_id AS user_id, f.incident_number, f.service,
                       EXTRACT(EPOCH FROM (MIN(e.created_at)-f.created_at)) AS seconds
                FROM rescue_incident_events e
                JOIN filtered f
                  ON f.guild_id=e.guild_id AND f.incident_number=e.incident_number
                WHERE e.actor_id IS NOT NULL
                  AND e.event_type='primary_assigned'
                GROUP BY e.actor_id,f.incident_number,f.service,f.created_at
                UNION ALL
                SELECT f.primary_responder_id AS user_id, f.incident_number, f.service,
                       EXTRACT(EPOCH FROM (f.responded_at-f.created_at)) AS seconds
                FROM filtered f
                WHERE f.primary_responder_id IS NOT NULL
                  AND f.responded_at IS NOT NULL
            ), claim_dedup AS (
                SELECT user_id,incident_number,service,MIN(seconds) AS seconds
                FROM primary_claims
                WHERE seconds>=0
                GROUP BY user_id,incident_number,service
            ), ranked AS (
                SELECT p.service,p.user_id,
                       COUNT(*) AS total_calls,
                       COUNT(*) FILTER (WHERE pp.incident_number IS NOT NULL) AS primary_calls,
                       COUNT(*) FILTER (WHERE pp.incident_number IS NULL) AS support_calls,
                       AVG(cd.seconds) AS avg_claim
                FROM participation p
                LEFT JOIN primary_participation pp
                  ON pp.user_id=p.user_id
                 AND pp.incident_number=p.incident_number
                 AND pp.service=p.service
                LEFT JOIN claim_dedup cd
                  ON cd.user_id=p.user_id
                 AND cd.incident_number=p.incident_number
                 AND cd.service=p.service
                GROUP BY p.service,p.user_id
            )
            SELECT service,user_id,total_calls,primary_calls,support_calls,avg_claim
            FROM ranked
            ORDER BY service,total_calls DESC,primary_calls DESC,user_id
            """,
            *args,
        )

    user_ids = sorted({int(row["user_id"]) for row in rows})
    names = await base.member_names(guild_id, user_ids)

    by_service = {}
    for row in rows:
        by_service.setdefault(row["service"], []).append(row)

    service_cards = []
    for service_key, service_name in base.SERVICES.items():
        service_rows = sorted(
            by_service.get(service_key, []),
            key=lambda row: (
                -int(row["total_calls"] or 0),
                -int(row["primary_calls"] or 0),
                names.get(int(row["user_id"]), "").lower(),
            ),
        )[:5]

        if service_rows:
            table_rows = "".join(
                f'''<tr><td>{rank}</td><td><a class="incident-link" href="/guild/{guild_id}/responder/{int(row['user_id'])}">{esc(names.get(int(row['user_id']), "Discord User"))}</a></td><td>{int(row['total_calls'] or 0)}</td><td>{int(row['primary_calls'] or 0)}</td><td>{int(row['support_calls'] or 0)}</td><td>{base.duration(row['avg_claim'])}</td></tr>'''
                for rank, row in enumerate(service_rows, start=1)
            )
        else:
            table_rows = '<tr><td colspan="6" class="muted">No responder activity for this service in the selected period.</td></tr>'

        service_cards.append(
            f'''<div class="card span12"><h2>{esc(service_name)}</h2><div style="overflow:auto"><table><thead><tr><th>Rank</th><th>Responder</th><th>Responses</th><th>Primary</th><th>Support</th><th>Avg Primary Claim</th></tr></thead><tbody>{table_rows}</tbody></table></div></div>'''
        )

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}/performance">← Responder Performance</a><a href="/guild/{guild_id}">Dashboard</a><a href="/guild/{guild_id}/history">Search History</a></div>
<div class="card"><div style="display:flex;justify-content:space-between;gap:24px;align-items:flex-start;flex-wrap:wrap"><div style="flex:1 1 420px"><h2>Service Rankings</h2><p class="muted">Top responders by service. Rankings use distinct historical participation from the permanent event ledger, with current incident state retained as a fallback for older incidents.</p></div><form method="get" action="/guild/{guild_id}/performance/services" style="display:grid;grid-template-columns:minmax(170px,1fr) minmax(170px,1fr);column-gap:28px;row-gap:12px;align-items:end;min-width:min(100%,390px);max-width:460px;width:100%"><div><label>From</label><input style="width:100%" type="date" name="date_from" value="{esc(date_from)}"></div><div><label>To</label><input style="width:100%" type="date" name="date_to" value="{esc(date_to)}"></div><div style="grid-column:1/-1;display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap"><button class="btn" type="submit">Apply</button><a class="btn secondary" href="/guild/{guild_id}/performance/services">Clear</a></div></form></div></div>
<div class="grid" style="margin-top:16px">{''.join(service_cards)}</div>'''
    return base.page(f"Service Rankings · {guild_info['name']}", body, base.current_user(request))
