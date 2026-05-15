"""Contract test for the app container's self-healing healthcheck.

Audit Apr 2026: container twice landed in the gunicorn preload-zombie state
(listening on :5000 but no worker forked), site went unresponsive. Healthcheck
correctly marked container unhealthy but Docker doesn't auto-restart on
unhealthy by default — so the failing branch must SIGTERM PID 1 to actually
get the container recycled by `restart: unless-stopped`.

This test guards the docker-compose.yml so a future edit doesn't accidentally
remove the `kill -TERM 1` fallback and reintroduce the never-recovers bug.

Run: docker compose exec app python -m pytest tests/test_healthcheck_contract.py -v
"""
import os
import unittest

COMPOSE_PATH = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")


class TestHealthcheckSelfHealing(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(COMPOSE_PATH) as f:
            cls.compose = f.read()

    def test_app_has_healthcheck(self):
        # Find the app service block (rough heuristic — first `app:` then up
        # until next top-level service).
        self.assertIn("healthcheck:", self.compose,
            "app service must define a healthcheck")

    def test_healthcheck_kills_pid_1_on_failure(self):
        """The fallback `|| kill -TERM 1` is what makes the recovery actually
        happen. Without it, an unhealthy container stays up forever."""
        self.assertIn("kill -TERM 1", self.compose,
            "healthcheck must SIGTERM PID 1 on failure so restart: unless-stopped fires")

    def test_healthcheck_hits_healthz(self):
        self.assertIn("/healthz", self.compose,
            "healthcheck must probe /healthz, not just any port")

    def test_healthcheck_has_start_period(self):
        # 90s is what we use to give migrations + import time on cold rebuilds.
        # Anything significantly less risks failing the check before the worker
        # finishes booting.
        self.assertIn("start_period:", self.compose)
        self.assertIn("90s", self.compose,
            "start_period should be ≥90s — gunicorn --preload + migrations need it")


class TestGunicornNoPreload(unittest.TestCase):
    """Lock in the no-preload decision (Apr 2026).

    `--preload` is a memory-sharing optimization for multi-worker setups —
    with workers=1 it provides zero benefit and creates a hang risk: if the
    app import freezes (network call, deadlock), the gunicorn master is
    stuck forever and never forks a worker. Container appears "Up" while
    requests time out.

    Without --preload, each worker imports independently AFTER fork, so a
    hang gets killed by gunicorn's timeout and respawned automatically.
    """

    @classmethod
    def setUpClass(cls):
        with open(COMPOSE_PATH) as f:
            cls.compose = f.read()

    def test_no_preload_flag(self):
        # Check only on lines that actually invoke gunicorn — comments may
        # mention "--preload" historically without that being a problem.
        gunicorn_lines = [ln for ln in self.compose.splitlines()
                          if "gunicorn" in ln and not ln.lstrip().startswith("#")]
        self.assertTrue(gunicorn_lines, "expected at least one gunicorn invocation")
        for line in gunicorn_lines:
            self.assertNotIn("--preload", line,
                f"--preload was removed because it caused container-level "
                f"hangs when app import froze. Found in: {line.strip()!r}")

    def test_gunicorn_command_present(self):
        self.assertIn("gunicorn", self.compose)
        self.assertIn("app:app", self.compose)


if __name__ == "__main__":
    unittest.main(verbosity=2)
