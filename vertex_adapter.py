"""
Vertex AI LLM adapter — direct Google Cloud calls replacing OpenRouter.
Supports Gemini (native) and Claude (partner models via Vertex AI Model Garden).
Falls back to OpenRouter if Vertex not configured.

Setup:
  1. pip install google-cloud-aiplatform anthropic[vertex]
  2. Set USE_VERTEX_AI=1, GCP_PROJECT_ID, GCP_REGION env vars
  3. Enable Vertex AI API + partner models in GCP console
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
REGION = os.getenv("GCP_REGION", "us-central1")
USE_VERTEX = os.getenv("USE_VERTEX_AI", "0") == "1"

# Map OpenRouter model names → Vertex AI model names
MODEL_MAP = {
    # Gemini (native Google, cheapest)
    "google/gemini-2.5-flash": "gemini-2.5-flash",
    "google/gemini-2.5-pro-preview": "gemini-2.5-pro",
    "google/gemini-2.5-pro": "gemini-2.5-pro",
    # Claude (partner models via Vertex AI Model Garden)
    "anthropic/claude-sonnet-4-6": "claude-sonnet-4-6@001",
    "anthropic/claude-opus-4-6": "claude-opus-4-6@001",
    # Models not on Vertex — map to closest equivalent
    "deepseek/deepseek-chat-v3-0324": "gemini-2.5-flash",
    "nvidia/nemotron-3-super-120b-a12b": "gemini-2.5-pro",
}

_initialized = False


def _ensure_init():
    global _initialized
    if _initialized:
        return
    if not PROJECT_ID:
        raise RuntimeError("GCP_PROJECT_ID not set. Cannot use Vertex AI.")
    try:
        import vertexai
        vertexai.init(project=PROJECT_ID, location=REGION)
        _initialized = True
        logger.info(f"Vertex AI initialized: project={PROJECT_ID}, region={REGION}")
    except Exception as e:
        logger.error(f"Vertex AI init failed: {e}")
        raise


def call_vertex(model_name, messages, max_tokens=4096, temperature=0.2,
                timeout=60, **kwargs):
    """Call a Vertex AI model. Compatible with OpenRouter message format.

    Args:
        model_name: OpenRouter-style name (auto-mapped to Vertex equivalent)
        messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
        max_tokens: Max output tokens
        temperature: 0.0-1.0

    Returns:
        str: Raw model response text
    """
    _ensure_init()

    vertex_model = MODEL_MAP.get(model_name, "gemini-2.5-flash")

    # Separate system prompt from conversation
    system = ""
    user_parts = []
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        elif msg["role"] == "user":
            user_parts.append(msg["content"])
        elif msg["role"] == "assistant":
            user_parts.append(f"[Previous response]: {msg['content']}")

    prompt = "\n\n".join(user_parts)

    try:
        if vertex_model.startswith("claude"):
            return _call_claude_vertex(vertex_model, system, prompt, max_tokens, temperature)
        else:
            return _call_gemini(vertex_model, system, prompt, max_tokens, temperature)
    except Exception as e:
        logger.error(f"Vertex AI call failed ({vertex_model}): {e}")
        # Fallback to OpenRouter if available
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        if openrouter_key:
            logger.info(f"Falling back to OpenRouter for {model_name}")
            from ai_validator import _call_openrouter
            return _call_openrouter(model_name, messages, max_tokens=max_tokens,
                                     temperature=temperature, timeout=timeout)
        raise


def _call_gemini(model_name, system, prompt, max_tokens, temperature):
    """Call Gemini natively via Vertex AI SDK."""
    from vertexai.generative_models import GenerativeModel

    model = GenerativeModel(
        model_name,
        system_instruction=system if system else None,
    )

    response = model.generate_content(
        prompt,
        generation_config={
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        },
    )

    return response.text


def _call_claude_vertex(model_name, system, prompt, max_tokens, temperature):
    """Call Claude via Anthropic's Vertex AI partner integration."""
    from anthropic import AnthropicVertex

    client = AnthropicVertex(region=REGION, project_id=PROJECT_ID)

    kwargs = {
        "model": model_name,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return response.content[0].text


def is_available():
    """Check if Vertex AI is configured and usable."""
    return USE_VERTEX and bool(PROJECT_ID)
