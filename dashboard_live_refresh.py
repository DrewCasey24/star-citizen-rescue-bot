"""Live-refresh the operations overview without reloading the full dashboard."""

from fastapi import Request
from fastapi.responses import HTMLResponse

import dashboard_core as base


LIVE_CSS = r'''
.live-sync{display:inline-flex;align-items:center;gap:8px;min-height:38px;padding:8px 12px;border:1px solid rgba(69,212,155,.32);border-radius:10px;background:rgba(20,104,76,.18);color:#a8f0d2;font-size:12px;font-weight:800;letter-spacing:.02em;white-space:nowrap;box-shadow:0 0 0 1px rgba(69,212,155,.04),0 0 18px rgba(69,212,155,.07)}
.live-sync::before{content:"";width:8px;height:8px;border-radius:50%;background:#45d49b;box-shadow:0 0 0 4px rgba(69,212,155,.12),0 0 10px rgba(69,212,155,.45)}
.live-sync.syncing{color:#b9dcff;border-color:rgba(86,168,255,.30);background:rgba(45,103,163,.16)}
.live-sync.syncing::before{background:#56a8ff;animation:live-pulse 1s ease-in-out infinite;box-shadow:0 0 0 4px rgba(86,168,255,.12),0 0 10px rgba(86,168,255,.45)}
.live-sync.error{color:#ffd39a;border-color:rgba(255,180,93,.32);background:rgba(166,91,24,.18)}
.live-sync.error::before{background:#ffb45d;box-shadow:0 0 0 4px rgba(255,180,93,.12)}
.live-flash{animation:live-flash .55s ease}
@keyframes live-pulse{0%,100%{opacity:.45}50%{opacity:1}}
@keyframes live-flash{0%{background-color:rgba(86,168,255,.10)}100%{background-color:transparent}}
@media(max-width:760px){.live-sync{width:100%;justify-content:center}}
'''


def _current_overview_endpoint():
    for route in base.app.routes:
        if getattr(route, "path", None) == "/guild/{guild_id}" and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError("Guild overview route not found")


_previous_overview = _current_overview_endpoint()


def _mark_live_regions(markup: str) -> str:
    replacements = (
        (
            '<div class="grid"><div class="card span3"><div class="label">Active Incidents</div>',
            '<div class="grid"><div class="card span3" id="metric-active"><div class="label">Active Incidents</div>',
        ),
        (
            '<div class="card span3"><div class="label">Awaiting Responder</div>',
            '<div class="card span3" id="metric-awaiting"><div class="label">Awaiting Responder</div>',
        ),
        (
            '<div class="card span3"><div class="label">P1 / P2 Active</div>',
            '<div class="card span3" id="metric-priority"><div class="label">P1 / P2 Active</div>',
        ),
        (
            '<div class="card span3"><div class="label">Responders Active</div>',
            '<div class="card span3" id="metric-responders"><div class="label">Responders Active</div>',
        ),
        (
            '<div class="card span4"><h2>Recent Activity</h2>',
            '<div class="card span4" id="recent-activity"><h2>Recent Activity</h2>',
        ),
    )
    for old, new in replacements:
        markup = markup.replace(old, new, 1)
    return markup


def _add_live_indicator(markup: str) -> str:
    indicator = '<span class="live-sync" id="live-sync" title="Overview automatically updates every 30 seconds">LIVE · Auto-refresh 30s</span>'
    marker = '<div class="overview-actions">'
    if marker in markup:
        return markup.replace(marker, marker + indicator, 1)
    grid_marker = '<div class="grid">'
    if grid_marker in markup:
        return markup.replace(grid_marker, f'<div style="display:flex;justify-content:flex-end;margin-bottom:12px">{indicator}</div>{grid_marker}', 1)
    return markup


async def live_overview(request: Request, guild_id: int, saved: int = 0):
    response = await _previous_overview(request, guild_id, saved)
    markup = response.body.decode("utf-8")
    markup = _mark_live_regions(markup)
    markup = markup.replace("</style>", LIVE_CSS + "\n</style>", 1)
    markup = _add_live_indicator(markup)

    script = f'''<script>
(() => {{
  const liveUrl = "/guild/{guild_id}";
  const regionIds = ["metric-active", "metric-awaiting", "metric-priority", "metric-responders", "active", "recent-activity"];
  const status = document.getElementById("live-sync");
  let running = false;

  function setStatus(state, text) {{
    if (!status) return;
    status.classList.remove("syncing", "error");
    if (state) status.classList.add(state);
    status.textContent = text;
  }}

  async function refreshOverview() {{
    if (running || document.hidden) return;
    running = true;
    setStatus("syncing", "LIVE · Refreshing…");
    try {{
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      const response = await fetch(liveUrl, {{
        cache: "no-store",
        credentials: "same-origin",
        headers: {{"X-Requested-With": "dashboard-live-refresh"}},
        signal: controller.signal
      }});
      clearTimeout(timeout);
      if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      let replaced = 0;
      for (const id of regionIds) {{
        const current = document.getElementById(id);
        const fresh = doc.getElementById(id);
        if (!current || !fresh) continue;
        if (current.innerHTML !== fresh.innerHTML) {{
          current.innerHTML = fresh.innerHTML;
          current.classList.remove("live-flash");
          void current.offsetWidth;
          current.classList.add("live-flash");
        }}
        replaced += 1;
      }}
      if (!replaced) throw new Error("Live regions unavailable");
      const now = new Date();
      setStatus("", `LIVE · Updated ${{now.toLocaleTimeString([], {{hour: "numeric", minute: "2-digit"}})}}`);
    }} catch (error) {{
      setStatus("error", "LIVE · Refresh delayed");
      console.warn("Dashboard live refresh failed", error);
    }} finally {{
      running = false;
    }}
  }}

  window.setInterval(refreshOverview, 30000);
  document.addEventListener("visibilitychange", () => {{
    if (!document.hidden) refreshOverview();
  }});
}})();
</script>'''
    markup = markup.replace("</body>", script + "\n</body>", 1)
    return HTMLResponse(markup, status_code=response.status_code)


for route in base.app.routes:
    if getattr(route, "path", None) == "/guild/{guild_id}" and "GET" in getattr(route, "methods", set()):
        route.endpoint = live_overview
        break

app = base.app
