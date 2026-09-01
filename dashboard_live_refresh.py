"""Live-refresh the operations overview without reloading the full dashboard."""

from fastapi.responses import HTMLResponse

import dashboard_core as base


LIVE_CSS = r'''
.live-sync{display:inline-flex;align-items:center;gap:7px;min-height:34px;padding:7px 10px;border:1px solid rgba(69,212,155,.18);border-radius:10px;background:rgba(20,104,76,.10);color:#8fe3c1;font-size:11px;font-weight:700;white-space:nowrap}
.live-sync::before{content:"";width:7px;height:7px;border-radius:50%;background:#45d49b;box-shadow:0 0 0 3px rgba(69,212,155,.10)}
.live-sync.syncing{color:#9fc8f4;border-color:rgba(86,168,255,.18);background:rgba(45,103,163,.10)}
.live-sync.syncing::before{background:#56a8ff;animation:live-pulse 1s ease-in-out infinite}
.live-sync.error{color:#ffc27a;border-color:rgba(255,180,93,.22);background:rgba(166,91,24,.12)}
.live-sync.error::before{background:#ffb45d}
.live-flash{animation:live-flash .55s ease}
@keyframes live-pulse{0%,100%{opacity:.45}50%{opacity:1}}
@keyframes live-flash{0%{background-color:rgba(86,168,255,.10)}100%{background-color:transparent}}
@media(max-width:760px){.live-sync{order:-1;width:100%;justify-content:center}}
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


async def live_overview(request, guild_id: int, saved: int = 0):
    response = await _previous_overview(request, guild_id, saved)
    markup = response.body.decode("utf-8")
    markup = _mark_live_regions(markup)
    markup = markup.replace("</style>", LIVE_CSS + "\n</style>", 1)
    markup = markup.replace(
        '<div class="overview-actions">',
        '<div class="overview-actions"><span class="live-sync" id="live-sync" title="Overview updates automatically every 30 seconds">Live · updated just now</span>',
        1,
    )

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
    setStatus("syncing", "Live · refreshing");
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
      setStatus("", `Live · updated ${{now.toLocaleTimeString([], {{hour: "numeric", minute: "2-digit"}})}}`);
    }} catch (error) {{
      setStatus("error", "Live · refresh delayed");
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
