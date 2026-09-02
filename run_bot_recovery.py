"""Production entry point with atomic controls, CAS priority, Discord recovery, and incident UX."""

import bot as core
import run_bot_cas  # noqa: F401 - installs atomic/CAS patches
import bot_discord_recovery  # noqa: F401 - installs periodic Discord reconciliation
import bot_incident_ux  # noqa: F401 - installs polished incident cards and state-aware controls


if __name__ == "__main__":
    core.main()
