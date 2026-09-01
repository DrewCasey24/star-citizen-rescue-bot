"""Register final dashboard GET handlers after all presentation modules are loaded."""

from fastapi.responses import HTMLResponse

import dashboard_core as base
import dashboard_history
import dashboard_incident_detail
import dashboard_live_refresh


def replace_get(path, endpoint):
    """Remove every existing GET registration for path and add the final handler once."""
    for route in list(base.app.router.routes):
        if getattr(route, "path", None) == path and "GET" in getattr(route, "methods", set()):
            base.app.router.routes.remove(route)
    base.app.add_api_route(path, endpoint, methods=["GET"], response_class=HTMLResponse)


# FastAPI compiles an APIRoute handler when the route is created. Presentation
# modules therefore only define/wrap handlers; this module owns final GET route
# registration and guarantees there is exactly one compiled route per path.
replace_get("/guild/{guild_id}", dashboard_live_refresh.live_overview)
replace_get("/guild/{guild_id}/history", dashboard_history.rescue_history_page_ledger)
replace_get("/guild/{guild_id}/incident/{incident_number}", dashboard_incident_detail.incident_detail_streamlined)

app = base.app
