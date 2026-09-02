"""Production entry point with atomic controls, CAS priority, and Discord recovery."""

import bot as core
import run_bot_cas  # noqa: F401 - installs atomic/CAS patches
import bot_discord_recovery  # noqa: F401 - installs periodic Discord reconciliation


if __name__ == "__main__":
    core.main()
