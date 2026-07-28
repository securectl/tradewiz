"""Ollama Cloud fallback for OpenRouter.

When an OpenRouter call fails — out of credits (HTTP 402), rate-limited (429),
timeout, or any transport error — and the admin has turned on
`ollama_fallback_enabled`, the app retries the SAME chat prompt against Ollama
Cloud (ollama.com) so the platform keeps working instead of degrading to
rule-based logic. Config (url / api_key / model) is resolved through the same
runtime_config store the rest of the Ollama settings use.

The public call returns an assistant-content string on success, or a
`json.dumps({"error": ...})` string on failure — mirroring
ai_validator._call_openrouter's contract so callers need no special handling.
"""

import json
import logging
import time

import requests

from shared.runtime_config import get_setting

logger = logging.getLogger(__name__)

# Lightweight telemetry so admins can see the backup is actually being used.
_stats = {"fallback_calls": 0, "last_used": None, "last_reason": None}

_TRUE = ("1", "true", "yes", "on")


def fallback_enabled():
    """Is the OpenRouter→Ollama failover switched on (admin toggle / env)?"""
    val = get_setting("ollama_fallback_enabled", "0",
                      env_aliases=("OLLAMA_FALLBACK_ENABLED",))
    return str(val).strip().lower() in _TRUE


def _cfg():
    url = get_setting("ollama_url", "https://ollama.com", env_aliases=("OLLAMA_URL",))
    key = get_setting("ollama_api_key", "", env_aliases=("OLLAMA_API_KEY",))
    model = get_setting("ollama_model", "gpt-oss:20b", env_aliases=("OLLAMA_MODEL",))
    return (url or "https://ollama.com").rstrip("/"), (key or ""), (model or "gpt-oss:20b")


def is_configured():
    """True when an Ollama Cloud API key is available to fall back onto."""
    _, key, _ = _cfg()
    return bool(key)


def get_stats():
    """Snapshot of fallback usage for the admin panel."""
    return dict(_stats)


def call_ollama_chat(messages, temperature=None, max_tokens=None, timeout=None,
                     reason="openrouter_failure", model=None):
    """Call Ollama Cloud /api/chat with an OpenRouter-style `messages` list.

    `model` overrides the configured default (e.g. a role resolved to
    'ollama/llama3.1:70b'). Returns the assistant content string, or a
    json.dumps({"error": ...}) string on failure. Increments telemetry only on
    a real answer."""
    url, key, cfg_model = _cfg()
    model = model or cfg_model
    if not key:
        return json.dumps({"error": "Ollama fallback not configured (no OLLAMA_API_KEY)."})
    if model.startswith("ollama/"):          # model override may carry the routing prefix
        model = model[len("ollama/"):]

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature if temperature is not None else 0.4},
    }
    if max_tokens:
        payload["options"]["num_predict"] = int(max_tokens)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        resp = requests.post(f"{url}/api/chat", headers=headers, json=payload,
                             timeout=timeout or 90)
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("message") or {}).get("content", "")
        if not content:
            return json.dumps({"error": "Ollama returned empty content."})
        _stats["fallback_calls"] += 1
        _stats["last_reason"] = reason
        try:
            _stats["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            pass
        logger.warning(f"LLM fallback: served via Ollama Cloud ({model}) after {reason}")
        return content
    except Exception as e:
        return json.dumps({"error": f"Ollama fallback error: {str(e)}"})


def try_fallback(messages, temperature=None, max_tokens=None, timeout=None,
                 reason="openrouter_failure"):
    """Attempt a fallback ONLY if enabled + configured. Returns the answer
    string on success, or None when fallback is off, unconfigured, or Ollama
    itself errored — so callers can keep their original OpenRouter error."""
    try:
        if not (fallback_enabled() and is_configured()):
            return None
        out = call_ollama_chat(messages, temperature, max_tokens, timeout, reason=reason)
        # Reject an error payload so the caller surfaces the real failure.
        if isinstance(out, str) and out.strip().startswith("{"):
            try:
                j = json.loads(out)
                if isinstance(j, dict) and j.get("error"):
                    return None
            except Exception:
                pass  # not JSON → treat as a genuine answer
        return out
    except Exception:
        return None
