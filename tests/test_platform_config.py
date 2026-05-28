"""Tests for shared.platform_config (Docker vs Cloud Run detection).

Run: docker compose exec app python -m pytest tests/test_platform_config.py -v
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ph():
    from db import IS_POSTGRES
    return "%s" if IS_POSTGRES else "?"


def _clear_override():
    from db import execute
    ph = _ph()
    execute(f"DELETE FROM bot_config WHERE user_id = 0 AND key = {ph}",
            ("deployment_target",))


class TestPlatformDetect(unittest.TestCase):
    def setUp(self):
        _clear_override()

    def tearDown(self):
        _clear_override()

    def test_detects_cloud_run_via_k_service(self):
        from shared.platform_config import detect_platform
        with patch.dict(os.environ, {"K_SERVICE": "ai-stock-analyst"}, clear=False):
            self.assertEqual(detect_platform(), "cloud_run")

    def test_defaults_to_docker(self):
        from shared.platform_config import detect_platform
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("K_SERVICE", None)
            os.environ.pop("K_REVISION", None)
            self.assertEqual(detect_platform(), "docker")

    def test_db_override_beats_auto_detect(self):
        from shared.platform_config import get_platform
        from shared.runtime_config import set_setting
        set_setting("deployment_target", "cloud_run")
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("K_SERVICE", None)
                p = get_platform()
                self.assertEqual(p["effective"], "cloud_run")
                self.assertEqual(p["override"], "cloud_run")
                self.assertEqual(p["detected"], "docker")
        finally:
            _clear_override()

    def test_invalid_override_falls_back_to_detected(self):
        from shared.platform_config import get_platform
        from shared.runtime_config import set_setting
        set_setting("deployment_target", "garbage_value")
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("K_SERVICE", None)
                self.assertEqual(get_platform()["effective"], "docker")
        finally:
            _clear_override()

    def test_is_cloud_run_helper(self):
        from shared.platform_config import is_cloud_run
        with patch.dict(os.environ, {"K_SERVICE": "x"}, clear=False):
            self.assertTrue(is_cloud_run())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("K_SERVICE", None)
            os.environ.pop("K_REVISION", None)
            self.assertFalse(is_cloud_run())


if __name__ == "__main__":
    unittest.main()
