"""Deployment platform detection + admin override.

The app can run on Docker compose (current self-hosted setup) or GCP Cloud Run
(see Dockerfile.cloudrun, deploy-gcp.sh, cloudbuild.yaml). On Cloud Run the
`K_SERVICE` env var is set by the runtime — we use that as the auto-detect
signal. Admins can override via the UI; the override persists in bot_config
so it survives container restarts.

Initial concrete effects:
- Surfaced in /healthz and the admin status badge.
- Other modules can branch via is_cloud_run() — e.g. to prefer Secret Manager
  for new secrets, or to skip writes to the local filesystem.

Resolution precedence:
  1. DB row: bot_config(user_id=0, key='deployment_target') in {docker, cloud_run, auto}
  2. Auto-detect: K_SERVICE env var presence → cloud_run; else → docker

Related: [[project-ollama-cloud]] which moved Ollama settings to the same
runtime_config plumbing so /env-less Cloud Run is viable.
"""

import os

from shared.runtime_config import get_setting

VALID_TARGETS = ("docker", "cloud_run", "auto")


def detect_platform() -> str:
    """Auto-detect from runtime env. Returns 'docker' or 'cloud_run'."""
    # Cloud Run sets K_SERVICE; also K_REVISION and K_CONFIGURATION.
    if os.getenv("K_SERVICE") or os.getenv("K_REVISION"):
        return "cloud_run"
    return "docker"


def get_platform() -> dict:
    """Return {detected, override, effective}. `override` is what the admin set
    (one of VALID_TARGETS or empty for not-set). `effective` is what callers
    should branch on."""
    detected = detect_platform()
    override = (get_setting("deployment_target", "") or "").strip().lower()
    if override not in VALID_TARGETS or override == "auto":
        effective = detected
    else:
        effective = override
    return {
        "detected": detected,
        "override": override if override in VALID_TARGETS else "",
        "effective": effective,
        "k_service": os.getenv("K_SERVICE", ""),
        "k_revision": os.getenv("K_REVISION", ""),
    }


def is_cloud_run() -> bool:
    """Convenience: True when the effective platform is Cloud Run."""
    return get_platform()["effective"] == "cloud_run"
