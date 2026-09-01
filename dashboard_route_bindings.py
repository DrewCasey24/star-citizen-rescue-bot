"""Rebind overridden FastAPI routes so compiled request handlers use the latest endpoints."""

from fastapi.responses import HTMLResponse

import dashboard_core as base
import dashboard_history
import dashboard_incident_detail
import dashboard_live_refresh


def rebind_get(path, endpoint):
    """Replace the compiled GET route for a path with its final dashboard handler."""
    for route in list(base.app.router.routes):
        if getattr(route, "path", None) == path and "GET" in getattr(route, "methods", set()):
            base.app.router.routes.remove(route)
            break
    base.app.add_api_route(path, endpoint, methods=["GET"], response_class=HTMLResponse)


# APIRoute compiles its request handler when the route is created. Assigning
# route.endpoint later does not rebuild that compiled handler, so every route
# customized through a presentation module is freshly registered here.
rebind_get("/guild/{guild_id}", dashboard_live_refresh.live_overview)
rebind_get("/guild/{guild_id}/history", dashboard_history.rescue_history_page_ledger)
rebind_get("/guild/{guild_id}/incident/{incident_number}", dashboard_incident_detail.incident_detail_streamlined)

app = base.app
