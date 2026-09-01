"""Rebind overridden FastAPI routes so the compiled request handlers use the latest endpoints."""

from fastapi.responses import HTMLResponse

import dashboard_core as base
import dashboard_live_refresh


# APIRoute compiles its request handler when the route is created. Simply assigning
# route.endpoint later does not rebuild that handler, so replace the original route
# with a freshly registered route after all dashboard modules have loaded.
for route in list(base.app.router.routes):
    if getattr(route, "path", None) == "/guild/{guild_id}" and "GET" in getattr(route, "methods", set()):
        base.app.router.routes.remove(route)
        break

base.app.add_api_route(
    "/guild/{guild_id}",
    dashboard_live_refresh.live_overview,
    methods=["GET"],
    response_class=HTMLResponse,
)

app = base.app
