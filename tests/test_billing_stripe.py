"""Tests for Stripe billing: webhook idempotency, period-field parsing, tier sync,
and public checkout access."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeStripe:
    class error:
        class SignatureVerificationError(Exception):
            pass

    class Webhook:
        _event = None

        @classmethod
        def construct_event(cls, payload, sig, secret):
            return cls._event


class TestWebhookIdempotency(unittest.TestCase):
    def test_duplicate_event_skipped(self):
        import subscriptions as s
        seen = set()
        _FakeStripe.Webhook._event = {
            "id": "evt_1", "type": "customer.updated", "data": {"object": {}},
        }
        with mock.patch.object(s, "_get_stripe", return_value=_FakeStripe), \
             mock.patch.object(s, "_event_already_processed", side_effect=lambda e: e in seen), \
             mock.patch.object(s, "_mark_event_processed", side_effect=lambda e, t: seen.add(e)):
            r1 = s.handle_webhook("{}", "sig")
            r2 = s.handle_webhook("{}", "sig")
        self.assertEqual(r1.get("status"), "ok")
        self.assertNotIn("duplicate", r1)
        self.assertTrue(r2.get("duplicate"))

    def test_bad_signature_raises(self):
        import subscriptions as s

        class Boom(_FakeStripe):
            class Webhook:
                @staticmethod
                def construct_event(payload, sig, secret):
                    raise _FakeStripe.error.SignatureVerificationError("bad")
        with mock.patch.object(s, "_get_stripe", return_value=Boom):
            with self.assertRaises(_FakeStripe.error.SignatureVerificationError):
                s.handle_webhook("{}", "badsig")


class TestSyncSubscription(unittest.TestCase):
    def _run(self, status="active", price="price_pro"):
        import subscriptions as s
        captured = {}
        sub = {
            "customer": "cus_1", "status": status, "id": "sub_1",
            "cancel_at_period_end": False,
            "items": {"data": [{
                "price": {"id": price},
                "current_period_start": 1747000000,
                "current_period_end": 1750000000,
            }]},
        }
        with mock.patch.object(s, "query_one", return_value={"user_id": 7}), \
             mock.patch.object(s, "PRICE_TO_TIER", {"price_pro": "pro", "price_starter": "starter"}), \
             mock.patch.object(s, "_upsert_subscription",
                               side_effect=lambda *a, **k: captured.update({"user_id": a[0] if a else None}, **k)):
            s._sync_subscription(sub)
        return captured

    def test_active_maps_price_to_tier(self):
        c = self._run(status="active", price="price_pro")
        self.assertEqual(c["tier"], "pro")

    def test_period_end_read_from_items(self):
        # Regression for the Basil API move of period fields onto items.
        c = self._run(status="active")
        self.assertIsNotNone(c["current_period_end"])
        self.assertIsNotNone(c["current_period_start"])

    def test_non_active_reverts_to_free(self):
        c = self._run(status="canceled", price="price_pro")
        self.assertEqual(c["tier"], "free")


class TestPublicCheckout(unittest.TestCase):
    def test_checkout_no_longer_invite_gated(self):
        # The invite-only query must be gone from the checkout path.
        import inspect, billing_bp
        src = inspect.getsource(billing_bp.billing_checkout)
        self.assertNotIn("invited users only", src)
        self.assertNotIn("accepted_at IS NOT NULL", src)

    def test_checkout_requires_auth(self):
        from app import app
        resp = app.test_client().post("/billing/checkout/pro")
        self.assertIn(resp.status_code, (401, 302))

    def test_invalid_tier_rejected(self):
        from app import app
        # login gate runs first; either way it must not 500.
        resp = app.test_client().post("/billing/checkout/bogus")
        self.assertIn(resp.status_code, (400, 401, 302))


if __name__ == "__main__":
    unittest.main()
