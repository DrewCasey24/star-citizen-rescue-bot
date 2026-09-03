"""Versioned PostgreSQL migrations shared by the bot and dashboard.

Migrations are intentionally additive and idempotent. The migration table is
locked so multiple Railway services can start safely against the same database.
"""

import logging

logger = logging.getLogger("star-citizen-rescue-bot.migrations")

MIGRATIONS = [
    (
        1,
        "operational_hardening",
        """
        ALTER TABLE rescue_incidents ADD COLUMN IF NOT EXISTS incident_message_id BIGINT;
        ALTER TABLE rescue_incidents ADD COLUMN IF NOT EXISTS discord_channel_missing_at TIMESTAMPTZ;

        CREATE TABLE IF NOT EXISTS rescue_incident_events (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            incident_number BIGINT NOT NULL,
            channel_id BIGINT,
            event_type TEXT NOT NULL,
            actor_id BIGINT,
            title TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS rescue_admin_audit_events (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT,
            actor_id BIGINT,
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT 'success',
            details TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS rescue_admin_audit_events_guild_created_idx
        ON rescue_admin_audit_events(guild_id, created_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS rescue_retention_settings (
            guild_id BIGINT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            closed_incident_days INTEGER,
            admin_audit_days INTEGER,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (closed_incident_days IS NULL OR closed_incident_days >= 30),
            CHECK (admin_audit_days IS NULL OR admin_audit_days >= 30)
        );

        CREATE INDEX IF NOT EXISTS rescue_incidents_channel_missing_idx
        ON rescue_incidents(guild_id, discord_channel_missing_at)
        WHERE discord_channel_missing_at IS NOT NULL;
        """,
    ),
    (
        2,
        "operational_indexes",
        """
        CREATE INDEX IF NOT EXISTS rescue_incidents_guild_priority_status_idx
        ON rescue_incidents(guild_id, status, priority, created_at);

        CREATE INDEX IF NOT EXISTS rescue_incident_events_incident_idx
        ON rescue_incident_events(guild_id, incident_number, created_at, id);

        CREATE INDEX IF NOT EXISTS rescue_incident_events_guild_created_idx
        ON rescue_incident_events(guild_id, created_at DESC, id DESC);
        """,
    ),
]


async def apply_migrations(pool):
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rescue_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        async with conn.transaction():
            # One transaction-level advisory lock prevents the bot and dashboard
            # from applying the same migration concurrently during a deploy.
            await conn.execute("SELECT pg_advisory_xact_lock(7263726573637565)")
            rows = await conn.fetch("SELECT version FROM rescue_schema_migrations")
            applied_versions = {int(row["version"]) for row in rows}
            for version, name, sql in MIGRATIONS:
                if version in applied_versions:
                    continue
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO rescue_schema_migrations(version,name) VALUES($1,$2)",
                    version,
                    name,
                )
                logger.info("Applied database migration version=%s name=%s", version, name)
