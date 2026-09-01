"""Streamlined incident detail presentation for the rescue dashboard."""

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


INCIDENT_CSS = r'''
.incident-hero{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap;margin-bottom:14px}
.incident-title{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.incident-title h1{margin:0;font-size:27px;letter-spacing:-.03em}
.incident-subtitle{margin-top:7px;color:var(--muted);font-size:13px}
.incident-badges{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.status-badge{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;border:1px solid rgba(116,153,196,.18);background:rgba(10,18,29,.72);font-size:12px;font-weight:700}
.incident-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:18px}
.summary-item{padding:12px 13px;border:1px solid rgba(116,153,196,.13);border-radius:12px;background:rgba(5,11,19,.38)}
.summary-item .label{margin-bottom:5px}.summary-item strong{display:block;font-size:14px;line-height:1.35}
.incident-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:14px;align-items:start}
.incident-main{display:grid;gap:14px}.incident-side{display:grid;gap:14px;position:sticky;top:94px}
.command-card .control-grid{grid-template-columns:1fr;gap:8px}.command-card .btn{padding:10px 12px;text-align:left}
.command-divider{height:1px;background:rgba(116,153,196,.12);margin:4px 0}
.command-danger{margin-top:3px}.closed-banner{padding:10px 12px;border-radius:10px;background:rgba(120,128,140,.10);border:1px solid rgba(150,160,175,.16);color:#aeb9c7;font-size:12px}
.situation-card .situation{font-size:14px;line-height:1.65}
.timeline-card .timeline{max-height:640px;overflow:auto;padding-right:8px}
.timeline-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-bottom:8px}
.timing-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.timing-item{padding:10px;border-radius:10px;background:rgba(5,11,19,.35);border:1px solid rgba(116,153,196,.11)}.timing-item .metric{font-size:20px;margin-top:2px}
.quick-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
@media(max-width:1050px){.incident-layout{grid-template-columns:1fr}.incident-side{position:static;grid-template-columns:1fr 1fr}.command-card{grid-column:1/-1}}
@media(max-width:760px){.incident-summary{grid-template-columns:1fr 1fr}.incident-side{grid-template-columns:1fr}.incident-title h1{font-size:23px}.timing-grid{grid-template-columns:1fr 1fr}}
@media(max-width:480px){.incident-summary{grid-template-columns:1fr}.timing-grid{grid-template-columns:1fr}}
'''


async def incident_detail_streamlined(request: Request, guild_id: int, incident_number: int, action: str = ""):
    guild_info = base.require_guild_access(request, guild_id)
    await base.require_bot_installed(guild_id)
    incident, responders, ledger = await base.load_incident(guild_id, incident_number)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")

    user_ids = [
        incident["requester_id"],
        incident["primary_responder_id"],
        incident["priority_changed_by"],
        incident["closed_by_id"],
    ]
    user_ids += [r["user_id"] for r in responders]
    user_ids += [e["actor_id"] for e in ledger]
    names = await base.member_names(guild_id, user_ids)

    def who(uid, fallback="—"):
        return base.esc(names.get(uid, fallback)) if uid else fallback

    support = [
        who(r["user_id"], "Discord User")
        for r in responders
        if r["user_id"] != incident["primary_responder_id"]
    ]
    support_text = ", ".join(support) if support else "None"

    if ledger:
        timeline_parts = []
        for event in ledger:
            actor = f' · {who(event["actor_id"], "Discord User")}' if event["actor_id"] else ""
            timeline_parts.append(
                f'<div class="event"><div class="event-title">{base.esc(event["title"])}<span class="ledger-badge">Recorded</span></div>'
                f'<div>{base.esc(event["details"])}</div><div class="event-meta">{base.format_dt(event["created_at"])}{actor}</div></div>'
            )
        timeline = "".join(timeline_parts)
        timeline_note = "Permanent event ledger"
    else:
        events = [
            (incident["created_at"], "Incident Created", f'Request submitted by {who(incident["requester_id"], "Requester")}')
        ]
        if incident["responded_at"]:
            events.append((incident["responded_at"], "Primary Responder Assigned", f'{who(incident["primary_responder_id"], "Responder")} accepted the call'))
        for responder in responders:
            if responder["user_id"] != incident["primary_responder_id"]:
                events.append((responder["joined_at"], "Responder Joined", f'{who(responder["user_id"], "Responder")} joined the response team'))
        if incident["priority_changed_at"]:
            events.append((incident["priority_changed_at"], "Priority Changed", f'Priority changed to {base.esc(base.PRIORITIES.get(incident["priority"], incident["priority"]))}'))
        if incident["arrived_at"]:
            events.append((incident["arrived_at"], "Arrived On Scene", "Response team reported arrival on scene"))
        if incident["backup_requested_at"]:
            events.append((incident["backup_requested_at"], "Backup Requested", "Additional responder support was requested"))
        if incident["closed_at"]:
            events.append((incident["closed_at"], "Incident Closed", f'Closed by {who(incident["closed_by_id"], "authorized user")}'))
        events.sort(key=lambda item: item[0])
        timeline = "".join(
            f'<div class="event"><div class="event-title">{base.esc(title)}</div><div>{text}</div><div class="event-meta">{base.format_dt(ts)}</div></div>'
            for ts, title, text in events
        )
        timeline_note = "Legacy incident timeline reconstructed from incident state"

    incident_id = f"RESCUE-{incident_number:04d}"
    priority_class = "p1" if incident["priority"] == "critical" else "p2" if incident["priority"] == "urgent" else "p3"
    status_label = base.STATUSES.get(incident["status"], incident["status"])
    service_label = base.SERVICES.get(incident["service"], incident["service"])
    closed = incident["status"] == "closed"
    disabled = " disabled" if closed else ""
    csrf = base.esc(request.session.get("csrf"))

    claim_seconds = (incident["responded_at"] - incident["created_at"]).total_seconds() if incident["responded_at"] else None
    scene_seconds = (incident["arrived_at"] - incident["created_at"]).total_seconds() if incident["arrived_at"] else None
    total_seconds = (incident["closed_at"] - incident["created_at"]).total_seconds() if incident["closed_at"] else None

    action_messages = {
        "priority_up": "Priority raised from the web dashboard.",
        "priority_down": "Priority lowered from the web dashboard.",
        "arrived": "Incident marked on scene from the web dashboard.",
        "backup": "Backup requested from the web dashboard.",
        "closed": "Incident closed from the web dashboard.",
    }
    action_notice = f'<div class="notice">{base.esc(action_messages[action])}</div>' if action in action_messages else ""

    channel_button = (
        f'<a class="btn" href="https://discord.com/channels/{guild_id}/{incident["channel_id"]}" target="_blank" rel="noopener">Open Discord Channel</a>'
        if incident["channel_id"]
        else ""
    )

    command_state = '<div class="closed-banner">This incident is closed. Command controls are disabled.</div>' if closed else ""
    controls = f'''<div class="card command-card"><h2>Command Controls</h2><p class="muted">Manager actions sync the incident, event ledger, Discord channel, and dispatch board.</p>{command_state}<div class="control-grid" style="margin-top:12px">
<form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="priority_up"><button class="btn danger" type="submit"{disabled}>⬆ Raise Priority</button></form>
<form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="priority_down"><button class="btn secondary" type="submit"{disabled}>⬇ Lower Priority</button></form>
<div class="command-divider"></div>
<form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="arrived"><button class="btn success" type="submit"{disabled}>📍 Mark Arrived</button></form>
<form method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="backup"><button class="btn warn" type="submit"{disabled}>🛡 Request Backup</button></form>
<div class="command-divider"></div>
<form class="command-danger" method="post" action="/guild/{guild_id}/incident/{incident_number}/action"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="close"><button class="btn danger" type="submit" onclick="return confirm('Close {incident_id}? This will archive the incident and disable its Discord controls.')"{disabled}>🔒 Close Incident</button></form>
</div><div class="control-note">Respond and Join Response remain Discord-only responder actions.</div></div>'''

    body = f'''<style>{INCIDENT_CSS}</style>{action_notice}
<div class="incident-hero"><div><div class="incident-title"><h1>{incident_id}</h1><div class="incident-badges"><span class="pill {priority_class}">{base.esc(base.PRIORITIES.get(incident['priority'], incident['priority']))}</span><span class="status-badge">{base.esc(status_label)}</span></div></div><div class="incident-subtitle">{base.esc(service_label)} · Opened {base.format_dt(incident['created_at'])}</div></div><div class="quick-actions">{channel_button}<a class="btn secondary" href="/guild/{guild_id}/history">History</a></div></div>
<div class="incident-layout"><main class="incident-main"><div class="card"><div class="incident-summary"><div class="summary-item"><div class="label">Callsign</div><strong>{base.esc(incident['callsign'])}</strong></div><div class="summary-item"><div class="label">Location</div><strong>{base.esc(incident['location'])}</strong></div><div class="summary-item"><div class="label">Primary Responder</div><strong>{who(incident['primary_responder_id'], 'Unassigned')}</strong></div><div class="summary-item"><div class="label">Support</div><strong>{support_text}</strong></div></div><div class="kv" style="margin-top:18px"><div>Requester</div><div>{who(incident['requester_id'], 'Requester')}</div><div>Service</div><div>{base.esc(service_label)}</div><div>Claimed</div><div>{base.format_dt(incident['responded_at'])}</div><div>Arrived</div><div>{base.format_dt(incident['arrived_at'])}</div><div>Closed</div><div>{base.format_dt(incident['closed_at'])}</div></div></div>
<div class="card situation-card"><h2>Situation</h2><div class="situation">{base.esc(incident['situation'])}</div></div>
<div class="card timeline-card"><div class="timeline-head"><div><h2>Incident Timeline</h2><p class="muted">{base.esc(timeline_note)}</p></div><span class="ledger-badge">{len(ledger) if ledger else 'Legacy'} events</span></div><div class="timeline">{timeline}</div></div></main>
<aside class="incident-side"><div class="card"><h2>Response Timing</h2><div class="timing-grid"><div class="timing-item"><div class="label">Claim</div><div class="metric">{base.duration(claim_seconds)}</div></div><div class="timing-item"><div class="label">On Scene</div><div class="metric">{base.duration(scene_seconds)}</div></div><div class="timing-item"><div class="label">Duration</div><div class="metric">{base.duration(total_seconds) if total_seconds is not None else 'Active'}</div></div><div class="timing-item"><div class="label">Status</div><div style="margin-top:5px;font-weight:700">{base.esc(status_label)}</div></div></div></div>{controls}</aside></div>'''
    return base.page(f"{incident_id} · {guild_info['name']}", body, base.current_user(request))


for route in base.app.routes:
    if getattr(route, "path", None) == "/guild/{guild_id}/incident/{incident_number}" and "GET" in getattr(route, "methods", set()):
        route.endpoint = incident_detail_streamlined
        break

app = base.app
