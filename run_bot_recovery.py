"""Production entry point with atomic controls, CAS priority, recovery, and Discord UX."""

import bot as core
import run_bot_cas  # noqa: F401 - installs atomic/CAS patches
import bot_discord_recovery  # noqa: F401 - installs periodic Discord reconciliation
import bot_incident_ux  # noqa: F401 - installs polished incident cards and state-aware controls
import bot_dispatch_board_ux  # noqa: F401 - installs polished live dispatch board
import bot_request_ux  # noqa: F401 - installs polished request-assistance flow


if __name__ == "__main__":
    core.main()
