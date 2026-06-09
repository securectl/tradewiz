"""Rough LLM cost estimation for the admin AI-usage dashboard.

OpenRouter/OpenAI-style responses return token counts but not always a billed
cost, so we estimate from a small per-model price table (USD per 1M tokens,
split prompt vs completion). These are approximations for *internal cost
visibility* — not billing. Unknown models fall back to ``DEFAULT_PRICE``.

Prices can be overridden without a redeploy via the ``LLM_PRICE_OVERRIDES`` env
var (JSON: ``{"model": {"prompt": <usd_per_1m>, "completion": <usd_per_1m>}}``).
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# USD per 1,000,000 tokens. Keep this list short and obvious; it's an estimate.
_PRICES = {
    # OpenRouter / common routes (prompt, completion)
    "openai/gpt-4o":            {"prompt": 2.50, "completion": 10.00},
    "openai/gpt-4o-mini":       {"prompt": 0.15, "completion": 0.60},
    "anthropic/claude-3.5-sonnet": {"prompt": 3.00, "completion": 15.00},
    "anthropic/claude-3-haiku": {"prompt": 0.25, "completion": 1.25},
    "google/gemini-2.5-flash":  {"prompt": 0.30, "completion": 2.50},
    "google/gemini-2.0-flash":  {"prompt": 0.10, "completion": 0.40},
    "google/gemini-2.5-pro":    {"prompt": 1.25, "completion": 10.00},
    # Ollama Cloud — self-hosted-style flat estimate (very cheap)
    "gpt-oss:20b":              {"prompt": 0.05, "completion": 0.05},
}

DEFAULT_PRICE = {"prompt": 0.50, "completion": 1.50}


def _overrides():
    raw = os.getenv("LLM_PRICE_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"Bad LLM_PRICE_OVERRIDES JSON: {e}")
        return {}


def _price_for(model):
    model = (model or "").strip()
    ov = _overrides()
    if model in ov:
        return ov[model]
    if model in _PRICES:
        return _PRICES[model]
    # Strip an `ollama/` routing prefix before the lookup.
    if model.startswith("ollama/") and model[len("ollama/"):] in _PRICES:
        return _PRICES[model[len("ollama/"):]]
    return DEFAULT_PRICE


def estimate_cost(model, prompt_tokens, completion_tokens):
    """Estimated USD cost for a single call. Always returns a float >= 0."""
    try:
        p = _price_for(model)
        pt = max(0, int(prompt_tokens or 0))
        ct = max(0, int(completion_tokens or 0))
        return round((pt * p["prompt"] + ct * p["completion"]) / 1_000_000.0, 6)
    except Exception:
        return 0.0
