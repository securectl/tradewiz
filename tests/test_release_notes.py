"""Tests for the What's New release-notes feature (May 2026)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.release_notes import get_releases, latest_version, _VALID_TYPES  # noqa: E402


class TestReleaseData(unittest.TestCase):
    def test_non_empty(self):
        self.assertTrue(get_releases())

    def test_latest_is_first(self):
        self.assertEqual(latest_version(), get_releases()[0]["version"])

    def test_newest_first(self):
        dates = [r["date"] for r in get_releases()]
        self.assertEqual(dates, sorted(dates, reverse=True),
                         "Releases must be listed newest-first")

    def test_entries_well_formed(self):
        for r in get_releases():
            for k in ("version", "date", "title", "items"):
                self.assertIn(k, r)
            self.assertTrue(r["items"])
            for it in r["items"]:
                self.assertIn(it.get("type"), _VALID_TYPES)
                self.assertTrue(it.get("text"))


class TestRoute(unittest.TestCase):
    def test_route_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/release-notes", rules)

    def test_endpoint_shape(self):
        from app import app
        client = app.test_client()
        resp = client.get("/api/release-notes")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("releases", data)
        self.assertIn("latest_version", data)
        self.assertEqual(data["latest_version"], data["releases"][0]["version"])


if __name__ == "__main__":
    unittest.main()
