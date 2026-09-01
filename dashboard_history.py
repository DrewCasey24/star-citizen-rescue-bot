from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


def option(value, label, selected=False):
    return f'<option value="{base.esc(value)}"{" selected" if selected else ""}>{base.esc(label)}</option>'


async def rescue_history_page_ledger(
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
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    q = q.strip()[:120]
    service = service if service in base.SERVICES else ""
    priority = priority if priority in base.PRIORITIES else ""
    status = status if status in base.STATUSES else ""
    page_num = max(1, page_num)
    responder_id = int(responder) if responder.isdigit() else None

    try:
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc) if date_from else None
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1) if date_to else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid history date filter.")

    conditions = ["ri.guild_id=$1"]
    args = [guild_id]

    def add_arg(value):
        args.append(value)
        return f"${len(args)}"

    if q:
        p = add_arg(f"%{q}%")
        conditions.append(
            f"(ri.incident_number::text ILIKE {p} OR ri.callsign ILIKE {p} OR ri.location ILIKE {p} OR ri.situation ILIKE {p})"
        )
    if service:
        conditions.append(f"ri.service={add_arg(service)}")
    if priority:
        conditions.append(f"ri.priority={add_arg(priority)}")
    if status:
        conditions.append(f"ri.status={add_arg(status)}")
    if responder_id:
        p = add_arg(responder_id)
        conditions.append(
            f"(" 
            f"ri.primary_responder_id={p} "
            f"OR EXISTS (SELECT 1 FROM rescue_incident_responders rr WHERE rr.channel_id=ri.channel_id AND rr.user_id={p}) "
            f"OR EXISTS (SELECT 1 FROM rescue_incident_events e WHERE e.guild_id=ri.guild_id AND e.incident_number=ri.incident_number AND e.actor_id={p} AND e.event_type IN ('primary_assigned','responder_joined'))"
            f")"
        )
    if from_dt:
        conditions.append(f"ri.created_at>={add_arg(from_dt)}")
    if to_dt:
        conditions.append(f"ri.created_at<{add_arg(to_dt)}")

    where_sql = " AND ".join(conditions)
    page_size = 25

    async with base.pool.acquire() as conn:
        total = int(await conn.fetchval(f"SELECT COUNT(*) FROM rescue_incidents ri WHERE {where_sql}", *args) or 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page_num = min(page_num, total_pages)
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
                SELECT primary_responder_id AS user_id
                FROM rescue_incidents
                WHERE guild_id=$1 AND primary_responder_id IS NOT NULL
                UNION
                SELECT rr.user_id
                FROM rescue_incident_responders rr
                JOIN rescue_incidents ri ON ri.channel_id=rr.channel_id
                WHERE ri.guild_id=$1
                UNION
                SELECT e.actor_id AS user_id
                FROM rescue_incident_events e
                WHERE e.guild_id=$1
                  AND e.actor_id IS NOT NULL
                  AND e.event_type IN ('primary_assigned','responder_joined')
            ) responders
            WHERE user_id IS NOT NULL
            ORDER BY user_id
            """,
            guild_id,
        )

    responder_ids = [int(r["user_id"]) for r in responder_rows]
    names = await base.member_names(guild_id, responder_ids + [r["primary_responder_id"] for r in rows])
    responder_options = '<option value="">Any responder</option>' + "".join(
        option(uid, names.get(uid, "Discord User"), uid == responder_id)
        for uid in sorted(responder_ids, key=lambda uid: names.get(uid, "").lower())
    )
    service_options = '<option value="">Any service</option>' + "".join(option(k, v, k == service) for k, v in base.SERVICES.items())
    priority_options = '<option value="">Any priority</option>' + "".join(option(k, v, k == priority) for k, v in base.PRIORITIES.items())
    status_options = '<option value="">Any status</option>' + "".join(option(k, v, k == status) for k, v in base.STATUSES.items())

    result_rows = "".join(
        f'<tr><td><a class="incident-link" href="/guild/{guild_id}/incident/{r["incident_number"]}">RESCUE-{r["incident_number"]:04d}</a></td><td><span class="pill {"p1" if r["priority"]=="critical" else "p2" if r["priority"]=="urgent" else "p3"}">{base.esc(base.PRIORITIES.get(r["priority"], r["priority"]))}</span></td><td>{base.esc(base.STATUSES.get(r["status"], r["status"]))}</td><td>{base.esc(base.SERVICES.get(r["service"], r["service"]))}</td><td>{base.esc(r["callsign"])}</td><td>{base.esc(r["location"])}</td><td>{base.esc(names.get(r["primary_responder_id"], "Unassigned") if r["primary_responder_id"] else "Unassigned")}</td><td>{base.format_dt(r["created_at"])}</td></tr>'
        for r in rows
    ) or '<tr><td colspan="8" class="muted">No incidents match the selected filters.</td></tr>'

    base_params = {
        "q": q,
        "service": service,
        "priority": priority,
        "status": status,
        "responder": responder,
        "date_from": date_from,
        "date_to": date_to,
    }
    prev_params = {**base_params, "page_num": max(1, page_num - 1)}
    next_params = {**base_params, "page_num": min(total_pages, page_num + 1)}
    prev_button = f'<a class="btn secondary" href="/guild/{guild_id}/history?{urlencode(prev_params)}">← Previous</a>' if page_num > 1 else '<span></span>'
    next_button = f'<a class="btn secondary" href="/guild/{guild_id}/history?{urlencode(next_params)}">Next →</a>' if page_num < total_pages else '<span></span>'
    shown_start = ((page_num - 1) * page_size + 1) if total else 0
    shown_end = min(page_num * page_size, total)

    body = f'''<div class="section-nav"><a href="/guild/{guild_id}">← Back to Dashboard</a></div><div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap"><div><h2>Rescue History Search</h2><p class="muted">Search the complete incident database and filter by service, priority, status, responder, or date. Responder history includes past participation preserved in the event ledger, even after a responder withdraws.</p></div><div class="status installed">{total} match{'es' if total != 1 else ''}</div></div><form method="get" action="/guild/{guild_id}/history"><div class="filter-grid"><div><label>Search</label><input type="search" name="q" value="{base.esc(q)}" placeholder="Incident #, callsign, location, situation"></div><div><label>Service</label><select name="service">{service_options}</select></div><div><label>Priority</label><select name="priority">{priority_options}</select></div><div><label>Status</label><select name="status">{status_options}</select></div><div><label>Responder</label><select name="responder">{responder_options}</select></div><div><label>From date</label><input type="date" name="date_from" value="{base.esc(date_from)}"></div><div><label>To date</label><input type="date" name="date_to" value="{base.esc(date_to)}"></div><div class="filter-actions"><button class="btn" type="submit">Apply Filters</button><a class="btn secondary" href="/guild/{guild_id}/history">Clear</a></div></div></form></div><div class="card" style="margin-top:16px"><div style="overflow:auto"><table><thead><tr><th>Incident</th><th>Priority</th><th>Status</th><th>Service</th><th>Callsign</th><th>Location</th><th>Primary</th><th>Opened</th></tr></thead><tbody>{result_rows}</tbody></table></div><div class="pagination"><div class="muted">Showing {shown_start}–{shown_end} of {total}</div><div class="pages">{prev_button}<span class="muted">Page {page_num} of {total_pages}</span>{next_button}</div></div></div>'''
    return base.page(f"Rescue History · {guild_info['name']}", body, base.current_user(request))


for route in base.app.routes:
    if getattr(route, "path", None) == "/guild/{guild_id}/history" and "GET" in getattr(route, "methods", set()):
        route.endpoint = rescue_history_page_ledger
        break

app = base.app
