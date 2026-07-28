"""Tests for the account panel backend (change-password + billing status shape)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestChangePassword(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/user/change-password", rules)

    def test_requires_auth(self):
        from app import app
        resp = app.test_client().post("/api/user/change-password",
                                      json={"current_password": "x", "new_password": "abcdefgh"})
        self.assertIn(resp.status_code, (401, 302))


class TestBillingStatusShape(unittest.TestCase):
    def test_billing_status_requires_auth(self):
        from app import app
        resp = app.test_client().get("/billing/status")
        self.assertIn(resp.status_code, (401, 302))

    def test_status_source_includes_subscription(self):
        # The handler must build a 'subscription' key for the account panel.
        import inspect, billing_bp
        src = inspect.getsource(billing_bp.billing_status)
        self.assertIn("subscription", src)


if __name__ == "__main__":
    unittest.main()
