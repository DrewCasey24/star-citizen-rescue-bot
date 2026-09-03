"""Production hardening hooks for migrations, environment safety, and logs."""

import logging
import os

import bot as core
from db_migrations import apply_migrations

logger = logging.getLogger("star-citizen-rescue-bot.hardening")

APP_ENV = os.getenv("APP_ENV", "production").strip().lower()

_original_setup_database = core.RescueBot.setup_database


async def setup_database_hardened(self):
    await _original_setup_database(self)
    if self.db_pool:
        await apply_migrations(self.db_pool)
        logger.info(
            "operational_event component=database action=migrations result=ready environment=%s",
            APP_ENV,
        )


core.RescueBot.setup_database = setup_database_hardened


def log_operation(action, *, guild_id=None, incident=None, actor_id=None, result="success", detail=""):
    """Emit one searchable key/value operational event for Railway logs."""
    logger.info(
        "operational_event action=%s guild_id=%s incident=%s actor_id=%s result=%s detail=%s",
        action,
        guild_id if guild_id is not None else "-",
        incident if incident is not None else "-",
        actor_id if actor_id is not None else "-",
        result,
        str(detail).replace("\n", " ")[:300],
    )
