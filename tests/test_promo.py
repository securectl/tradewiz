"""Tests for admin-generated promo / access codes."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPromoLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # SQLite in-memory-ish: ensure schema + a user exist.
        import migrations
        migrations.run_migrations()
        from db import execute, query_one, IS_POSTGRES
        cls.P = "%s" if IS_POSTGRES else "?"
        row = query_one("SELECT id FROM users LIMIT 1")
        if not row:
            execute(f"INSERT INTO users (google_id, email, name) VALUES ({cls.P}, {cls.P}, {cls.P})",
                    ("promo-tester-gid", "promo-tester@example.com", "Promo Tester"))
            row = query_one("SELECT id FROM users ORDER BY id DESC LIMIT 1")
        cls.uid = row["id"]

    def test_generate_unique_codes(self):
        import promo
        codes = promo.create_codes(tier="starter", days=14, max_uses=1, quantity=3)
        self.assertEqual(len(codes), 3)
        self.assertEqual(len(set(codes)), 3)
        self.assertTrue(all(c.startswith("TW-") for c in codes))

    def test_redeem_happy_path_and_double_block(self):
        import promo
        code = promo.create_codes(tier="pro", days=10, max_uses=1, quantity=1)[0]
        ok, msg, info = promo.redeem_code(self.uid, code)
        self.assertTrue(ok, msg)
        self.assertEqual(info["tier"], "pro")
        self.assertEqual(info["days"], 10)
        # tier actually granted
        from db import query_one
        row = query_one(f"SELECT tier, status FROM user_subscriptions WHERE user_id = {self.P}", (self.uid,))
        self.assertEqual(row["tier"], "pro")
        # second redemption by same user is blocked
        ok2, _, _ = promo.redeem_code(self.uid, code)
        self.assertFalse(ok2)

    def test_max_uses_enforced(self):
        import promo
        from db import execute
        code = promo.create_codes(tier="starter", days=5, max_uses=1, quantity=1)[0]
        # a different user redeems it, exhausting the single use (idempotent create)
        from db import query_one
        row = query_one("SELECT id FROM users WHERE email = " + self.P, ("promo-b@example.com",))
        if not row:
            execute(f"INSERT INTO users (google_id, email, name) VALUES ({self.P}, {self.P}, {self.P})",
                    ("promo-b-gid", "promo-b@example.com", "B"))
            row = query_one("SELECT id FROM users WHERE email = " + self.P, ("promo-b@example.com",))
        other = row["id"]
        ok, _, _ = promo.redeem_code(other, code)
        self.assertTrue(ok)
        # now this user can't — fully redeemed
        ok2, msg2, _ = promo.redeem_code(self.uid, code)
        self.assertFalse(ok2)
        self.assertIn("redeem", msg2.lower())

    def test_invalid_and_inactive(self):
        import promo
        ok, _, _ = promo.redeem_code(self.uid, "TW-NOTREAL9")
        self.assertFalse(ok)
        code = promo.create_codes(tier="starter", days=5, max_uses=5, quantity=1)[0]
        promo.set_active(code, False)
        ok2, msg2, _ = promo.redeem_code(self.uid, code)
        self.assertFalse(ok2)


class TestRoutes(unittest.TestCase):
    def test_admin_routes_require_admin(self):
        from app import app
        c = app.test_client()
        self.assertIn(c.get("/api/admin/promo-codes").status_code, (401, 403, 302))
        self.assertIn(c.post("/api/admin/promo-codes").status_code, (401, 403, 302))

    def test_redeem_requires_auth(self):
        from app import app
        self.assertIn(app.test_client().post("/api/promo/redeem", json={"code": "X"}).status_code, (401, 302))

    def test_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/admin/promo-codes", rules)
        self.assertIn("/api/promo/redeem", rules)


if __name__ == "__main__":
    unittest.main()
