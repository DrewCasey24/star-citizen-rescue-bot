"""Paddle-ready guild billing page and signed subscription webhook handler."""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import dashboard_core as base
from entitlements import GuildEntitlement, PLAN_FEATURES, get_guild_entitlement

logger = logging.getLogger("star-citizen-rescue-dashboard.billing")

PADDLE_ENVIRONMENT = os.getenv("PADDLE_ENVIRONMENT", "sandbox").strip().lower()
PADDLE_CLIENT_TOKEN = os.getenv("PADDLE_CLIENT_TOKEN", "").strip()
PADDLE_WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET", "").strip()
PADDLE_PRO_PRICE_ID = os.getenv("PADDLE_PRO_PRICE_ID", "").strip()
PADDLE_COMMAND_PRICE_ID = os.getenv("PADDLE_COMMAND_PRICE_ID", "").strip()
PADDLE_SIGNATURE_TOLERANCE_SECONDS = 30
PAST_DUE_GRACE_DAYS = 7

PRICE_TO_PLAN = {price: plan for price, plan in ((PADDLE_PRO_PRICE_ID, "pro"), (PADDLE_COMMAND_PRICE_ID, "command")) if price}

PLAN_COPY = {
    "free": ("Free", "$0", "Core rescue operations for small organizations and evaluation."),
    "pro": ("Rescue Pro", "$7/mo", "Advanced administration, analytics, export, branding, retention, and audit tools."),
    "command": ("Rescue Command", "$15/mo", "Higher-scale operations with advanced permissions, custom services, reports, and integrations."),
}


def _verify_signature(raw_body: bytes, header: str, secret: str, now: int | None = None) -> bool:
    if not header or not secret:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts.setdefault(key.strip(), []).append(value.strip())
    try:
        timestamp = int(parts.get("ts", [""])[0])
    except ValueError:
        return False
    signatures = parts.get("h1", [])
    if not signatures:
        return False
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > PADDLE_SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = str(timestamp).encode("utf-8") + b":" + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, supplied) for supplied in signatures)


def _subscription_plan(data: dict) -> str | None:
    for item in data.get("items") or []:
        price = item.get("price") or {}
        price_id = price.get("id") or item.get("price_id")
        if price_id in PRICE_TO_PLAN:
            return PRICE_TO_PLAN[price_id]
    return None


def _period_end(data: dict):
    period = data.get("current_billing_period") or {}
    value = period.get("ends_at") or data.get("next_billed_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _checkout_ready(plan: str) -> bool:
    price = PADDLE_PRO_PRICE_ID if plan == "pro" else PADDLE_COMMAND_PRICE_ID
    return bool(PADDLE_CLIENT_TOKEN and price)


@base.app.get("/guild/{guild_id}/billing", response_class=HTMLResponse)
async def billing_page(request: Request, guild_id: int):
    guild = base.require_guild_access(request, guild_id)
    entitlement = await get_guild_entitlement(base.pool, guild_id)
    effective = entitlement.effective_plan()
    billing_ready = bool(PADDLE_CLIENT_TOKEN and PADDLE_PRO_PRICE_ID and PADDLE_COMMAND_PRICE_ID and PADDLE_WEBHOOK_SECRET)

    cards = ""
    for key in ("free", "pro", "command"):
        name, price, description = PLAN_COPY[key]
        current = key == effective
        features = sorted(PLAN_FEATURES[key])
        feature_text = "".join(f"<li>{base.esc(feature.replace('_', ' ').title())}</li>" for feature in features)
        if current:
            button = '<span class="status installed">Current plan</span>'
        elif key == "free":
            button = '<span class="muted">Core rescue access is always available.</span>'
        elif _checkout_ready(key):
            button = f'<button class="btn" type="button" onclick="openPaddleCheckout(\'{key}\')">Choose {base.esc(name)}</button>'
        else:
            button = '<button class="btn secondary" type="button" disabled>Checkout not configured</button>'
        cards += f'''<div class="card billing-card{' current' if current else ''}"><div class="billing-plan"><div><h2>{base.esc(name)}</h2><div class="metric">{base.esc(price)}</div></div>{'<span class="status installed">Active</span>' if current else ''}</div><p class="muted">{base.esc(description)}</p><ul>{feature_text}</ul><div class="billing-action">{button}</div></div>'''

    status_detail = f"Billing status: {entitlement.billing_status}. Source: {entitlement.source}."
    if entitlement.grace_until and entitlement.billing_status == "past_due":
        status_detail += f" Grace access through {base.format_dt(entitlement.grace_until)}."
    mode = "Sandbox" if PADDLE_ENVIRONMENT == "sandbox" else "Live"
    setup_notice = ""
    if not billing_ready:
        setup_notice = '<div class="notice" style="border-color:#79551e;background:#2e2312;color:#ffd39a">Paddle checkout is not enabled yet. The entitlement layer is active, but payment credentials and price IDs still need to be configured.</div>'

    paddle_script = ""
    if PADDLE_CLIENT_TOKEN:
        env_js = "Paddle.Environment.set('sandbox');" if PADDLE_ENVIRONMENT == "sandbox" else ""
        prices = json.dumps({"pro": PADDLE_PRO_PRICE_ID, "command": PADDLE_COMMAND_PRICE_ID})
        paddle_script = f'''<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script><script>{env_js}Paddle.Initialize({{token:{json.dumps(PADDLE_CLIENT_TOKEN)}}});const rescuePrices={prices};function openPaddleCheckout(plan){{const priceId=rescuePrices[plan];if(!priceId)return;Paddle.Checkout.open({{items:[{{priceId:priceId,quantity:1}}],customData:{{guild_id:{json.dumps(str(guild_id))},requested_plan:plan,product:"sc-rescue"}},settings:{{displayMode:"overlay",theme:"dark"}}}});}}</script>'''

    body = f'''<style>.billing-head{{display:flex;justify-content:space-between;gap:14px;align-items:flex-end;flex-wrap:wrap;margin-bottom:16px}}.billing-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.billing-card{{display:flex;flex-direction:column;min-height:390px}}.billing-card.current{{border-color:rgba(69,212,155,.42)}}.billing-plan{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}}.billing-card ul{{padding-left:18px;color:var(--muted);font-size:13px;line-height:1.75;flex:1}}.billing-action{{margin-top:12px}}.billing-status{{margin-bottom:14px}}@media(max-width:900px){{.billing-grid{{grid-template-columns:1fr}}}}</style><div class="billing-head"><div><h2>Billing & Plans</h2><div class="muted">Subscription management for {base.esc(guild['name'])}.</div></div><div><span class="status {'installed' if billing_ready else 'not-installed'}">Paddle {mode}</span> <a class="btn secondary" href="/guild/{guild_id}/operations">Operations Center</a></div></div>{setup_notice}<div class="card billing-status"><strong>Current entitlement: {base.esc(PLAN_COPY[effective][0])}</strong><div class="muted" style="margin-top:5px">{base.esc(status_detail)}</div></div><div class="billing-grid">{cards}</div><div class="card" style="margin-top:14px"><h2>Billing safety</h2><p class="muted">Core rescue request, response, dispatch, and closure functions are never disabled by billing. Paid plans unlock administrative and scale features only. Failed renewals receive a seven-day grace period before paid entitlements fall back to Free.</p></div>{paddle_script}'''
    return base.page(f"Billing · {guild['name']}", body, base.current_user(request))


@base.app.post("/billing/paddle/webhook")
async def paddle_webhook(request: Request):
    if not PADDLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Paddle webhook is not configured.")
    raw_body = await request.body()
    signature = request.headers.get("Paddle-Signature", "")
    if not _verify_signature(raw_body, signature, PADDLE_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid Paddle signature.")
    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    event_id = event.get("event_id")
    event_type = event.get("event_type") or "unknown"
    data = event.get("data") or {}
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing Paddle event ID.")

    subscription_id = data.get("id") if str(event_type).startswith("subscription.") else data.get("subscription_id")
    customer_id = data.get("customer_id")
    custom = data.get("custom_data") or {}
    raw_guild_id = custom.get("guild_id")
    try:
        guild_id = int(raw_guild_id) if raw_guild_id is not None else None
    except (TypeError, ValueError):
        guild_id = None
    plan = _subscription_plan(data)
    status = data.get("status") or "unknown"
    period_end = _period_end(data)

    async with base.pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchval(
                """
                INSERT INTO rescue_billing_webhook_events(event_id,event_type,subscription_id,guild_id,result,occurred_at)
                VALUES($1,$2,$3,$4,'processing',COALESCE($5,NOW()))
                ON CONFLICT(event_id) DO NOTHING
                RETURNING event_id
                """,
                event_id,
                event_type,
                subscription_id,
                guild_id,
                event.get("occurred_at"),
            )
            if not inserted:
                return JSONResponse({"ok": True, "duplicate": True})

            if guild_id is None and subscription_id:
                guild_id = await conn.fetchval(
                    "SELECT guild_id FROM rescue_guild_entitlements WHERE paddle_subscription_id=$1",
                    subscription_id,
                )
            if plan is None and subscription_id:
                plan = await conn.fetchval(
                    "SELECT plan FROM rescue_guild_entitlements WHERE paddle_subscription_id=$1",
                    subscription_id,
                )

            if not str(event_type).startswith("subscription."):
                await conn.execute(
                    "UPDATE rescue_billing_webhook_events SET result='ignored',processed_at=NOW() WHERE event_id=$1",
                    event_id,
                )
                return JSONResponse({"ok": True, "ignored": True})

            if guild_id is None or plan not in {"pro", "command"}:
                await conn.execute(
                    "UPDATE rescue_billing_webhook_events SET guild_id=$2,result='unmapped',processed_at=NOW() WHERE event_id=$1",
                    event_id,
                    guild_id,
                )
                logger.warning("operational_event component=billing action=webhook_unmapped event_id=%s event_type=%s subscription_id=%s", event_id, event_type, subscription_id)
                return JSONResponse({"ok": True, "unmapped": True})

            grace_until = datetime.now(timezone.utc) + timedelta(days=PAST_DUE_GRACE_DAYS) if status == "past_due" else None
            source = "paddle"
            await conn.execute(
                """
                INSERT INTO rescue_guild_entitlements(
                    guild_id,plan,billing_status,paddle_customer_id,paddle_subscription_id,
                    paddle_price_id,current_period_end,grace_until,source,updated_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
                ON CONFLICT(guild_id) DO UPDATE SET
                    plan=EXCLUDED.plan,
                    billing_status=EXCLUDED.billing_status,
                    paddle_customer_id=COALESCE(EXCLUDED.paddle_customer_id,rescue_guild_entitlements.paddle_customer_id),
                    paddle_subscription_id=COALESCE(EXCLUDED.paddle_subscription_id,rescue_guild_entitlements.paddle_subscription_id),
                    paddle_price_id=COALESCE(EXCLUDED.paddle_price_id,rescue_guild_entitlements.paddle_price_id),
                    current_period_end=EXCLUDED.current_period_end,
                    grace_until=CASE
                        WHEN EXCLUDED.billing_status='past_due' AND rescue_guild_entitlements.billing_status='past_due' AND rescue_guild_entitlements.grace_until IS NOT NULL
                        THEN rescue_guild_entitlements.grace_until
                        ELSE EXCLUDED.grace_until
                    END,
                    source=EXCLUDED.source,
                    updated_at=NOW()
                """,
                guild_id,
                plan,
                status,
                customer_id,
                subscription_id,
                PADDLE_PRO_PRICE_ID if plan == "pro" else PADDLE_COMMAND_PRICE_ID,
                period_end,
                grace_until,
                source,
            )
            await conn.execute(
                "UPDATE rescue_billing_webhook_events SET guild_id=$2,result='processed',processed_at=NOW() WHERE event_id=$1",
                event_id,
                guild_id,
            )

    logger.info("operational_event component=billing action=subscription_sync guild_id=%s plan=%s status=%s event_type=%s event_id=%s", guild_id, plan, status, event_type, event_id)
    return JSONResponse({"ok": True})


_previous_page = base.page


def page_with_billing_link(title, body, user=None):
    if title.startswith("Operations Center ·") and "Billing & Plans" not in body:
        import re
        match = re.search(r'/guild/(\d+)/operations', body)
        if match:
            guild_id = match.group(1)
            needle = f'<a class="btn secondary" href="/guild/{guild_id}/admin-audit">Administrative Audit</a>'
            replacement = needle + f'<a class="btn secondary" href="/guild/{guild_id}/billing">Billing & Plans</a>'
            body = body.replace(needle, replacement, 1)
    return _previous_page(title, body, user)


base.page = page_with_billing_link
app = base.app
