from dashboard_core import *
import dashboard_performance  # registers Responder Performance route and navigation link
import dashboard_service_rankings  # registers per-service responder leaderboards
import dashboard_history  # replaces Search History with ledger-aware responder filtering
import dashboard_theme  # applies modern dashboard presentation layer
import dashboard_streamline  # adds app navigation, streamlined overview, search, and settings
import dashboard_streamline_phase2  # compacts the live incident queue and prioritizes attention calls
import dashboard_filter_panels  # collapses optional filters on data-heavy dashboard views
from dashboard_core import app
