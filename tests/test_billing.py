import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import dashboard_billing as billing
import dashboard_subscription_management as subscriptions
from entitlements import GuildEntitlement, plan_at_least


class EntitlementTests(unittest.TestCase):
    def test_active_paid_plan_is_effective(self):
        entitlement = GuildEntitlement(1, plan="pro", billing_status="active", source="paddle")
        self.assertEqual(entitlement.effective_plan(), "pro")
        self.assertTrue(entitlement.has("advanced_analytics"))

    def test_past_due_keeps_access_during_grace(self):
        now = datetime.now(timezone.utc)
        entitlement = GuildEntitlement(
            1,
            plan="command",
            billing_status="past_due",
            grace_until=now + timedelta(days=2),
            source="paddle",
        )
        self.assertEqual(entitlement.effective_plan(now), "command")

    def test_expired_or_canceled_falls_back_to_free(self):
        now = datetime.now(timezone.utc)
        expired = GuildEntitlement(
            1,
            plan="pro",
            billing_status="past_due",
            grace_until=now - timedelta(seconds=1),
            source="paddle",
        )
        canceled = GuildEntitlement(1, plan="command", billing_status="canceled", source="paddle")
        self.assertEqual(expired.effective_plan(now), "free")
        self.assertEqual(canceled.effective_plan(now), "free")

    def test_manual_entitlement_does_not_depend_on_billing_status(self):
        entitlement = GuildEntitlement(1, plan="command", billing_status="none", source="manual")
        self.assertEqual(entitlement.effective_plan(), "command")

    def test_plan_order(self):
        self.assertTrue(plan_at_least("command", "pro"))
        self.assertFalse(plan_at_least("free", "pro"))


class PaddleWebhookTests(unittest.TestCase):
    def test_signature_verification_uses_raw_body(self):
        secret = "pdl_ntfset_test"
        body = b'{"event_id":"evt_test","event_type":"subscription.created"}'
        timestamp = 1_700_000_000
        signed = str(timestamp).encode() + b":" + body
        signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        header = f"ts={timestamp};h1={signature}"
        self.assertTrue(billing._verify_signature(body, header, secret, now=timestamp))
        self.assertFalse(billing._verify_signature(body + b" ", header, secret, now=timestamp))

    def test_signature_rejects_old_timestamp(self):
        secret = "secret"
        body = b"{}"
        timestamp = 100
        signature = hmac.new(secret.encode(), b"100:{}", hashlib.sha256).hexdigest()
        self.assertFalse(billing._verify_signature(body, f"ts=100;h1={signature}", secret, now=1000))

    def test_price_controls_plan_not_custom_data(self):
        data = {
            "items": [{"price": {"id": "pri_real_pro"}}],
            "custom_data": {"requested_plan": "command"},
        }
        with patch.object(billing, "PRICE_TO_PLAN", {"pri_real_pro": "pro"}):
            self.assertEqual(billing._subscription_plan(data), "pro")

    def test_event_for_current_subscription_is_accepted(self):
        self.assertTrue(
            billing._accept_subscription_event("sub_command", "active", "sub_command")
        )

    def test_old_subscription_event_cannot_replace_active_current_subscription(self):
        self.assertFalse(
            billing._accept_subscription_event("sub_command", "active", "sub_old_pro")
        )
        self.assertFalse(
            billing._accept_subscription_event("sub_command", "past_due", "sub_old_pro")
        )

    def test_new_subscription_can_replace_inactive_old_subscription(self):
        self.assertTrue(
            billing._accept_subscription_event("sub_old_pro", "canceled", "sub_command")
        )


class PaddleSubscriptionManagementTests(unittest.TestCase):
    def test_upgrade_prorates_immediately(self):
        self.assertEqual(
            subscriptions._proration_mode("pro", "command"),
            "prorated_immediately",
        )

    def test_downgrade_prorates_next_billing_period(self):
        self.assertEqual(
            subscriptions._proration_mode("command", "pro"),
            "prorated_next_billing_period",
        )

    def test_target_price_uses_configured_price_ids(self):
        with patch.object(billing, "PADDLE_PRO_PRICE_ID", "pri_pro"), patch.object(
            billing, "PADDLE_COMMAND_PRICE_ID", "pri_command"
        ):
            self.assertEqual(subscriptions._target_price("pro"), "pri_pro")
            self.assertEqual(subscriptions._target_price("command"), "pri_command")
            self.assertEqual(subscriptions._target_price("free"), "")


if __name__ == "__main__":
    unittest.main()
