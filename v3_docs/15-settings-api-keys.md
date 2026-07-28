# 15 · Settings & API Keys

Open **Settings** from the header **⚙** icon. This is where you connect your own API keys,
set preferences, and configure the local-LLM option.

> 🔐 Keys you enter are stored **encrypted** and used only to power your features. Trading
> keys should be **paper/demo** keys — the bots run in paper mode by default.

---

## API keys

| Provider | Powers | Fields |
|----------|--------|--------|
| **OpenRouter** | All AI features (analysis validation, predictions, screener vetting, bots) | API key |
| **BloFin** | Crypto Trading bot | API key · secret · passphrase |
| **Alpaca** | Stock Trading bot (paper) | API key · secret |
| **Webull** | Stock Trading bot (sandbox) | app key · app secret · account ID |

Each row shows a status indicator (**✓ Connected** / **— Not configured**). Collapsible
**Setup instructions** walk you through obtaining each set of keys.

> If you don't set a key, TradeWiz falls back to any server-level key the deployment
> provides (for OpenRouter) — but trading bots need *your* broker keys to run.

---

## Preferences

- **Fast Mode** — use faster/cheaper AI models for research and health checks (also
  toggleable from the header). Great for conserving your daily AI quota.
- **Theme** — Aurora · Terminal · Daylight (header).
- **Font size** — Small · Medium · Large (header).
- **Alert emails** — opt in to the [daily digest](14-alerts-notifications.md).

---

## Ollama (local / cloud LLM, optional)

TradeWiz can use an Ollama endpoint as a fast first-pass validator for the bots.

- **URL** and **model** fields (cloud Ollama at `ollama.com` with an API key is the
  supported setup; the default model is `gpt-oss:20b`).
- If Ollama is unavailable, the bots simply fall through to the OpenRouter validators — no
  trades are blocked by its absence.

*(Admins manage the global Ollama config and can live-test connectivity — see
[Admin Guide](16-admin-guide.md).)*

---

## Saving

Settings save in place (a status indicator confirms). Trading-key changes take effect the
next time a bot starts a cycle; restart a running bot to pick them up immediately.

---

## Getting your keys

- **OpenRouter** — create a key at openrouter.ai; it routes to Claude, Gemini, DeepSeek,
  etc. behind one API.
- **BloFin** — create **demo** API credentials in your BloFin account.
- **Alpaca** — use **paper trading** API keys from the Alpaca dashboard.
- **Webull** — sandbox credentials (Webull stays sandbox-only in TradeWiz).
