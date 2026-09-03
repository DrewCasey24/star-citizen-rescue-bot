"""Manage-Server-only viewer for dashboard administrative audit events."""

import re

from fastapi import Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


@base.app.get("/guild/{guild_id}/admin-audit", response_class=HTMLResponse)
async def admin_audit(request: Request, guild_id: int):
    guild = base.require_guild_access(request, guild_id)
    async with base.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT actor_id,action,target,result,details,created_at
            FROM rescue_admin_audit_events
            WHERE guild_id=$1
            ORDER BY created_at DESC,id DESC
            LIMIT 200
            """,
            guild_id,
        )
    names = await base.member_names(guild_id, [row["actor_id"] for row in rows if row["actor_id"]])
    entries = ""
    for row in rows:
        actor = names.get(row["actor_id"], f"User {row['actor_id']}") if row["actor_id"] else "System/Unknown"
        result_class = "installed" if row["result"] == "success" else "not-installed"
        entries += f'''<tr><td>{base.format_dt(row['created_at'])}</td><td>{base.esc(actor)}</td><td><strong>{base.esc(row['action'])}</strong><div class="muted">{base.esc(row['target'])}</div></td><td><span class="status {result_class}">{base.esc(row['result'])}</span></td><td>{base.esc(row['details'])}</td></tr>'''
    if not entries:
        entries = '<tr><td colspan="5" class="muted">No dashboard administrative mutations have been recorded yet.</td></tr>'
    body = f'''<div class="overview-head"><div><h2>Administrative Audit</h2><div class="muted">Dashboard mutation history for {base.esc(guild['name'])}. Incident lifecycle events remain in each rescue record.</div></div><div><a class="btn secondary" href="/guild/{guild_id}/operations">Operations Center</a></div></div><div class="card"><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Result</th><th>Details</th></tr></thead><tbody>{entries}</tbody></table></div></div>'''
    return base.page(f"Administrative Audit · {guild['name']}", body, base.current_user(request))


_previous_page = base.page


def page_with_admin_tools(title, body, user=None):
    if title.startswith("Operations Center ·"):
        match = re.search(r'/guild/(\d+)/', body)
        if match and "Administrative Audit" not in body:
            guild_id = match.group(1)
            needle = f'<a class="btn secondary" href="/guild/{guild_id}/repair-config">Repair Configuration</a>'
            replacement = needle + f'<a class="btn secondary" href="/guild/{guild_id}/admin-audit">Administrative Audit</a><a class="btn secondary" href="/guild/{guild_id}/retention">Data Retention</a>'
            body = body.replace(needle, replacement, 1)
    return _previous_page(title, body, user)


base.page = page_with_admin_tools
app = base.app
