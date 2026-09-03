import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import dashboard_billing as billing
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


if __name__ == "__main__":
    unittest.main()
