"""Tests for the user feedback feature."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFeedback(unittest.TestCase):
    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/feedback", rules)
        self.assertIn("/api/admin/feedback", rules)

    def test_clamp_int(self):
        from features.feedback.routes import _clamp_int
        self.assertEqual(_clamp_int(7, 0, 10), 7)
        self.assertEqual(_clamp_int(15, 0, 10), 10)   # clamp high
        self.assertEqual(_clamp_int(-3, 0, 10), 0)    # clamp low
        self.assertIsNone(_clamp_int("x", 0, 10))
        self.assertIsNone(_clamp_int(None, 1, 5))

    def test_submit_requires_auth(self):
        from app import app
        resp = app.test_client().post("/api/feedback", json={"nps": 9})
        self.assertIn(resp.status_code, (401, 302))

    def test_admin_feedback_requires_admin(self):
        from app import app
        resp = app.test_client().get("/api/admin/feedback")
        self.assertIn(resp.status_code, (401, 302, 403))


if __name__ == "__main__":
    unittest.main()
