"""Shared subscription entitlement rules for Discord guilds."""

from dataclasses import dataclass
from datetime import datetime, timezone

PLAN_ORDER = {"free": 0, "pro": 1, "command": 2}
PLAN_FEATURES = {
    "free": {
        "incident_core",
        "dispatch_board",
        "basic_history",
        "basic_dashboard",
    },
    "pro": {
        "incident_core",
        "dispatch_board",
        "basic_history",
        "basic_dashboard",
        "advanced_history",
        "advanced_analytics",
        "csv_exports",
        "custom_branding",
        "extended_retention",
        "admin_audit",
    },
    "command": {
        "incident_core",
        "dispatch_board",
        "basic_history",
        "basic_dashboard",
        "advanced_history",
        "advanced_analytics",
        "csv_exports",
        "custom_branding",
        "extended_retention",
        "admin_audit",
        "multiple_dispatch_boards",
        "advanced_permissions",
        "custom_services",
        "scheduled_reports",
        "api_webhooks",
    },
}

PAID_ACTIVE_STATUSES = {"active", "trialing"}


@dataclass(frozen=True)
class GuildEntitlement:
    guild_id: int
    plan: str = "free"
    billing_status: str = "none"
    grace_until: datetime | None = None
    source: str = "default"

    def effective_plan(self, now: datetime | None = None) -> str:
        if self.plan not in PLAN_ORDER:
            return "free"
        if self.source == "manual":
            return self.plan
        if self.billing_status in PAID_ACTIVE_STATUSES:
            return self.plan
        if self.billing_status == "past_due" and self.grace_until:
            now = now or datetime.now(timezone.utc)
            grace = self.grace_until
            if grace.tzinfo is None:
                grace = grace.replace(tzinfo=timezone.utc)
            if now <= grace:
                return self.plan
        return "free"

    def has(self, feature: str, now: datetime | None = None) -> bool:
        return feature in PLAN_FEATURES[self.effective_plan(now)]


def plan_at_least(plan: str, required: str) -> bool:
    return PLAN_ORDER.get(plan, 0) >= PLAN_ORDER.get(required, 0)


async def get_guild_entitlement(pool, guild_id: int) -> GuildEntitlement:
    if pool is None:
        return GuildEntitlement(guild_id=guild_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT guild_id,plan,billing_status,grace_until,source
            FROM rescue_guild_entitlements
            WHERE guild_id=$1
            """,
            guild_id,
        )
    if not row:
        return GuildEntitlement(guild_id=guild_id)
    return GuildEntitlement(
        guild_id=int(row["guild_id"]),
        plan=row["plan"],
        billing_status=row["billing_status"],
        grace_until=row["grace_until"],
        source=row["source"],
    )
