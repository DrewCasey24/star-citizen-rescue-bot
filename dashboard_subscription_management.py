"""Paid-plan changes and authoritative Paddle entitlement reconciliation."""

import logging
import os

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import dashboard_billing as billing
import dashboard_core as base
from entitlements import PLAN_ORDER

logger = logging.getLogger("star-citizen-rescue-dashboard.subscription-management")

PADDLE_API_KEY = os.getenv("PADDLE_API_KEY", "").strip()
PADDLE_API_BASE = "https://sandbox-api.paddle.com" if billing.PADDLE_ENVIRONMENT == "sandbox" else "https://api.paddle.com"


def _target_price(plan: str) -> str:
    if plan == "pro":
        return billing.PADDLE_PRO_PRICE_ID
    if plan == "command":
        return billing.PADDLE_COMMAND_PRICE_ID
    return ""


def _proration_mode(current_plan: str, target_plan: str) -> str:
    if PLAN_ORDER.get(target_plan, 0) > PLAN_ORDER.get(current_plan, 0):
        return "prorated_immediately"
    return "prorated_next_billing_period"


def _headers():
    if not PADDLE_API_KEY:
        raise HTTPException(status_code=503, detail="Paddle server API key is not configured.")
    return {
        "Authorization": f"Bearer {PADDLE_API_KEY}",
        "Content-Type": "application/json",
    }


async def _change_paddle_subscription(subscription_id: str, target_plan: str, current_plan: str):
    price_id = _target_price(target_plan)
    if not price_id:
        raise HTTPException(status_code=503, detail="The selected Paddle price is not configured.")

    payload = {
        "items": [{"price_id": price_id, "quantity": 1}],
        "proration_billing_mode": _proration_mode(current_plan, target_plan),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.patch(
            f"{PADDLE_API_BASE}/subscriptions/{subscription_id}",
            headers=_headers(),
            json=payload,
        )
    if response.status_code >= 400:
        logger.error(
            "operational_event component=billing action=plan_change_failed subscription_id=%s target_plan=%s status=%s body=%s",
            subscription_id,
            target_plan,
            response.status_code,
            response.text[:500],
        )
        raise HTTPException(status_code=502, detail="Paddle could not update the subscription. Please try again shortly.")
    return response.json()


def _plan_from_subscription(data: dict) -> str | None:
    for item in data.get("items") or []:
        price = item.get("price") or {}
        price_id = price.get("id") or item.get("price_id")
        if price_id == billing.PADDLE_PRO_PRICE_ID:
            return "pro"
        if price_id == billing.PADDLE_COMMAND_PRICE_ID:
            return "command"
    return None


def _subscription_belongs_to_guild(data: dict, guild_id: int) -> bool:
    custom = data.get("custom_data") or {}
    try:
        return int(custom.get("guild_id")) == int(guild_id)
    except (TypeError, ValueError):
        return False


async def _find_authoritative_subscription(guild_id: int):
    """Ask Paddle for active-like subscriptions and choose this guild's highest paid plan."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{PADDLE_API_BASE}/subscriptions",
            headers=_headers(),
            params={"status": "active,trialing,past_due", "per_page": 200},
        )
    if response.status_code >= 400:
        logger.error(
            "operational_event component=billing action=resync_list_failed guild_id=%s status=%s body=%s",
            guild_id,
            response.status_code,
            response.text[:500],
        )
        raise HTTPException(status_code=502, detail="Paddle could not be queried for active subscriptions.")

    candidates = []
    for sub in (response.json().get("data") or []):
        if not _subscription_belongs_to_guild(sub, guild_id):
            continue
        plan = _plan_from_subscription(sub)
        if plan not in {"pro", "command"}:
            continue
        candidates.append((PLAN_ORDER[plan], sub, plan))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, sub, plan = candidates[0]
    return sub, plan


async def _store_authoritative_subscription(guild_id: int, data: dict, plan: str):
    status = data.get("status") or "unknown"
    period_end = billing._period_end(data)
    customer_id = data.get("customer_id")
    subscription_id = data.get("id")
    price_id = billing.PADDLE_PRO_PRICE_ID if plan == "pro" else billing.PADDLE_COMMAND_PRICE_ID
    grace_until = None
    if status == "past_due":
        from datetime import datetime, timedelta, timezone
        grace_until = datetime.now(timezone.utc) + timedelta(days=billing.PAST_DUE_GRACE_DAYS)

    async with base.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rescue_guild_entitlements(
                guild_id,plan,billing_status,paddle_customer_id,paddle_subscription_id,
                paddle_price_id,current_period_end,grace_until,source,updated_at
            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'paddle',NOW())
            ON CONFLICT(guild_id) DO UPDATE SET
                plan=EXCLUDED.plan,
                billing_status=EXCLUDED.billing_status,
                paddle_customer_id=EXCLUDED.paddle_customer_id,
                paddle_subscription_id=EXCLUDED.paddle_subscription_id,
                paddle_price_id=EXCLUDED.paddle_price_id,
                current_period_end=EXCLUDED.current_period_end,
                grace_until=EXCLUDED.grace_until,
                source='paddle',
                updated_at=NOW()
            """,
            guild_id,
            plan,
            status,
            customer_id,
            subscription_id,
            price_id,
            period_end,
            grace_until,
        )


@base.app.post("/guild/{guild_id}/billing/resync")
async def resync_billing(request: Request, guild_id: int):
    base.require_guild_access(request, guild_id)
    form = await request.form()
    base.require_csrf(request, form.get("csrf"))
    subscription, plan = await _find_authoritative_subscription(guild_id)
    if not subscription or not plan:
        return RedirectResponse(f"/guild/{guild_id}/billing?sync=none", status_code=303)
    await _store_authoritative_subscription(guild_id, subscription, plan)
    logger.info(
        "operational_event component=billing action=authoritative_resync guild_id=%s subscription_id=%s plan=%s status=%s",
        guild_id,
        subscription.get("id"),
        plan,
        subscription.get("status"),
    )
    return RedirectResponse(f"/guild/{guild_id}/billing?sync=ok", status_code=303)


@base.app.post("/guild/{guild_id}/billing/change-plan")
async def change_paid_plan(request: Request, guild_id: int):
    base.require_guild_access(request, guild_id)
    form = await request.form()
    base.require_csrf(request, form.get("csrf"))
    target_plan = str(form.get("plan") or "").strip().lower()
    if target_plan not in {"pro", "command"}:
        raise HTTPException(status_code=400, detail="Unknown paid plan.")

    async with base.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT plan,billing_status,paddle_subscription_id,source
            FROM rescue_guild_entitlements
            WHERE guild_id=$1
            """,
            guild_id,
        )
    if not row or row["source"] != "paddle" or not row["paddle_subscription_id"]:
        raise HTTPException(status_code=409, detail="No Paddle subscription is linked to this server. Start with checkout instead.")
    if row["billing_status"] == "past_due":
        raise HTTPException(status_code=409, detail="Past-due subscriptions must be brought current before changing plans.")
    if row["billing_status"] not in {"active", "trialing"}:
        raise HTTPException(status_code=409, detail="This subscription is not currently eligible for a plan change.")
    current_plan = row["plan"]
    if current_plan == target_plan:
        return RedirectResponse(f"/guild/{guild_id}/billing?change=unchanged", status_code=303)

    await _change_paddle_subscription(row["paddle_subscription_id"], target_plan, current_plan)
    logger.info(
        "operational_event component=billing action=plan_change_requested guild_id=%s subscription_id=%s from_plan=%s to_plan=%s proration=%s",
        guild_id,
        row["paddle_subscription_id"],
        current_plan,
        target_plan,
        _proration_mode(current_plan, target_plan),
    )
    return RedirectResponse(f"/guild/{guild_id}/billing?change=pending", status_code=303)


_original_billing_page = billing.billing_page


async def billing_page_with_subscription_changes(request: Request, guild_id: int):
    response = await _original_billing_page(request, guild_id)

    async with base.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT plan,billing_status,paddle_subscription_id,source
            FROM rescue_guild_entitlements WHERE guild_id=$1
            """,
            guild_id,
        )
        active_subscription_count = int(await conn.fetchval(
            """
            SELECT COUNT(DISTINCT created.subscription_id)
            FROM rescue_billing_webhook_events created
            WHERE created.guild_id=$1
              AND created.subscription_id IS NOT NULL
              AND created.event_type='subscription.created'
              AND NOT EXISTS (
                  SELECT 1 FROM rescue_billing_webhook_events canceled
                  WHERE canceled.guild_id=created.guild_id
                    AND canceled.subscription_id=created.subscription_id
                    AND canceled.event_type='subscription.canceled'
              )
            """,
            guild_id,
        ) or 0)

    html = response.body.decode("utf-8")
    csrf = base.esc(request.session.get("csrf"))
    paid_current = bool(
        row
        and row["source"] == "paddle"
        and row["paddle_subscription_id"]
        and row["billing_status"] in {"active", "trialing"}
        and row["plan"] in {"pro", "command"}
    )

    if paid_current:
        current_plan = row["plan"]
        target_plan = "command" if current_plan == "pro" else "pro"
        target_name = billing.PLAN_COPY[target_plan][0]
        old_button = (
            f'<button class="btn" type="button" onclick="openPaddleCheckout(\'{target_plan}\')">'
            f'Choose {base.esc(target_name)}</button>'
        )
        label = "Upgrade" if PLAN_ORDER[target_plan] > PLAN_ORDER[current_plan] else "Downgrade"
        new_button = (
            f'<form method="post" action="/guild/{guild_id}/billing/change-plan">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="plan" value="{target_plan}">'
            f'<button class="btn" type="submit">{label} to {base.esc(target_name)}</button>'
            f'</form>'
        ) if PADDLE_API_KEY else '<button class="btn secondary" type="button" disabled>Plan changes need Paddle API key</button>'
        html = html.replace(old_button, new_button, 1)
    elif PADDLE_API_KEY and row and row["source"] == "paddle":
        sync_form = (
            f'<form method="post" action="/guild/{guild_id}/billing/resync" style="margin-top:10px">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<button class="btn secondary" type="submit">Sync Active Plan from Paddle</button>'
            '</form>'
        )
        html = html.replace('</div><div class="billing-grid">', sync_form + '</div><div class="billing-grid">', 1)

    if request.query_params.get("change") == "pending":
        notice = '<div class="notice"><strong>Plan change submitted to Paddle.</strong> The page will update when the signed subscription webhook arrives. Refresh in a few seconds if the new plan is not visible yet.</div>'
        html = html.replace('<div class="card billing-status">', notice + '<div class="card billing-status">', 1)
    if request.query_params.get("sync") == "ok":
        notice = '<div class="notice"><strong>Billing synchronized with Paddle.</strong> The active subscription below was refreshed directly from Paddle.</div>'
        html = html.replace('<div class="card billing-status">', notice + '<div class="card billing-status">', 1)
    elif request.query_params.get("sync") == "none":
        notice = '<div class="notice" style="border-color:#79551e;background:#2e2312;color:#ffd39a"><strong>No active paid subscription was found in Paddle for this server.</strong></div>'
        html = html.replace('<div class="card billing-status">', notice + '<div class="card billing-status">', 1)

    if active_subscription_count > 1:
        duplicate_notice = (
            '<div class="notice" style="border-color:#79551e;background:#2e2312;color:#ffd39a">'
            '<strong>Multiple active Paddle subscriptions detected for this server.</strong> '
            'Cancel the older duplicate subscription in Paddle so only the current plan remains active.'
            '</div>'
        )
        html = html.replace('<div class="card billing-status">', duplicate_notice + '<div class="card billing-status">', 1)

    return HTMLResponse(html, status_code=response.status_code)


for route in list(base.app.router.routes):
    if getattr(route, "path", None) == "/guild/{guild_id}/billing" and "GET" in getattr(route, "methods", set()):
        base.app.router.routes.remove(route)
base.app.add_api_route("/guild/{guild_id}/billing", billing_page_with_subscription_changes, methods=["GET"], response_class=HTMLResponse)

app = base.app
