from dashboard_core import *
import dashboard_performance  # registers Responder Performance route and navigation link
import dashboard_service_rankings  # registers per-service responder leaderboards
import dashboard_history  # replaces Search History with ledger-aware responder filtering
import dashboard_theme  # applies modern dashboard presentation layer
import dashboard_streamline  # adds app navigation, streamlined overview, search, and settings
import dashboard_streamline_phase2  # compacts the live incident queue and prioritizes attention calls
import dashboard_filter_panels  # collapses optional filters on data-heavy dashboard views
import dashboard_incident_detail  # streamlines incident command view and timeline
import dashboard_live_refresh  # refreshes overview metrics, queue, and activity without full reloads
import dashboard_route_bindings  # re-registers overridden routes so FastAPI serves the latest handlers
import dashboard_settings_status  # adds configuration health and streamlined settings sections
import dashboard_incident_card_persistence  # remembers Discord incident card IDs for direct updates
import dashboard_incident_actions  # authoritative atomic incident state transitions and ledger writes
import dashboard_discord_consistency  # surfaces incomplete Discord synchronization after web actions
import dashboard_health  # adds live database, Discord, configuration, board, and incident-card checks
import dashboard_config_repair  # removes saved Discord role/channel references that no longer exist
import dashboard_operations_center  # unifies incident management, audit, responder routing, and channel integrity
import dashboard_health_nav  # adds System Health to the shared sidebar
import dashboard_error_pages  # replaces raw FastAPI/JSON failures with dashboard-styled recovery pages
from dashboard_core import app
