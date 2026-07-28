"""Tests for the AI-validation accuracy harness + confidence gate.

Covers the pure aggregation/bucketing/gate logic (no LLM calls): bucket
boundaries, hit-rate aggregation, and annotate_verdict's actionable gate.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.ai_validation_accuracy as acc


def _rec(score, verdict, correct):
    return {"score": score, "verdict": verdict, "correct": correct, "fwd_pct": 1.0}


class TestBucket(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(acc._bucket(50), (50, 60))
        self.assertEqual(acc._bucket(69.9), (60, 70))
        self.assertEqual(acc._bucket(80), (80, 90))
        self.assertEqual(acc._bucket(100), (90, 101))
        self.assertIsNone(acc._bucket(40))   # below the lowest bucket


class TestAggregate(unittest.TestCase):
    def test_hit_rates(self):
        recs = [
            _rec(82, "BUY", True), _rec(85, "BUY", True), _rec(88, "STRONG BUY", False),
            _rec(62, "BUY", False), _rec(65, "BUY", True),
        ]
        table = acc._aggregate(recs, horizon=10)
        b80 = next(b for b in table["buckets"] if b["lo"] == 80)
        self.assertEqual(b80["n"], 3)
        self.assertAlmostEqual(b80["hit_rate"], 66.7, places=1)   # 2/3
        b60 = next(b for b in table["buckets"] if b["lo"] == 60)
        self.assertEqual(b60["n"], 2)
        self.assertEqual(b60["hit_rate"], 50.0)                    # 1/2
        self.assertEqual(table["overall"]["bullish"]["n"], 5)
        self.assertEqual(table["horizon"], 10)
        self.assertEqual(table["target"], 75)

    def test_empty_bucket_is_none(self):
        table = acc._aggregate([_rec(55, "BUY", True)], horizon=10)
        b90 = next(b for b in table["buckets"] if b["lo"] == 90)
        self.assertIsNone(b90["hit_rate"])
        self.assertEqual(b90["n"], 0)


class TestAnnotateGate(unittest.TestCase):
    def _table(self, hit, n):
        return {"horizon": 10, "target": 75, "min_sample": 20,
                "buckets": [{"lo": 80, "hi": 90, "hit_rate": hit, "n": n}],
                "overall": {}, "sample_size": 100}

    def test_no_calibration(self):
        with mock.patch.object(acc, "load_calibration", return_value=None):
            out = acc.annotate_verdict({"final_verdict": "BUY", "composite_score": 85})
            self.assertFalse(out["measured"])
            self.assertIsNone(out["actionable"])

    def test_actionable_when_bucket_clears_target(self):
        with mock.patch.object(acc, "load_calibration", return_value=self._table(80.0, 40)):
            out = acc.annotate_verdict({"final_verdict": "BUY", "composite_score": 85})
            self.assertTrue(out["measured"])
            self.assertTrue(out["actionable"])
            self.assertEqual(out["hit_rate"], 80.0)

    def test_not_actionable_below_target(self):
        with mock.patch.object(acc, "load_calibration", return_value=self._table(60.0, 40)):
            out = acc.annotate_verdict({"final_verdict": "BUY", "composite_score": 85})
            self.assertFalse(out["actionable"])

    def test_not_actionable_thin_sample(self):
        with mock.patch.object(acc, "load_calibration", return_value=self._table(90.0, 5)):
            out = acc.annotate_verdict({"final_verdict": "BUY", "composite_score": 85})
            self.assertFalse(out["actionable"])   # n < min_sample

    def test_non_directional_never_actionable(self):
        with mock.patch.object(acc, "load_calibration", return_value=self._table(90.0, 40)):
            out = acc.annotate_verdict({"final_verdict": "WAIT", "composite_score": 85})
            self.assertFalse(out["directional"])
            self.assertFalse(out["actionable"])


class TestPersistence(unittest.TestCase):
    def test_save_load_roundtrip(self):
        table = {"horizon": 10, "target": 75, "buckets": [], "overall": {}, "sample_size": 3}
        acc.save_calibration(table)
        loaded = acc.load_calibration()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["sample_size"], 3)


class TestRoutes(unittest.TestCase):
    def test_calibration_routes_registered(self):
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn("/api/admin/ai-validation/calibrate", rules)
        self.assertIn("/api/admin/ai-validation/calibration", rules)


if __name__ == "__main__":
    unittest.main()
