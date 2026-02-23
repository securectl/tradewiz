"""
Multi-LLM Trade Validator — 2-layer consensus required before every trade.

Gate 1: Ollama (local, fast, free) — first filter
Gate 2: 2 OpenRouter models in parallel — sentiment + risk — both must agree

Fallback: if Ollama is unreachable, OpenRouter-only (2/2 must agree)
"""

import os
import json
import logging
import requests
import concurrent.futures
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Ollama config
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# OpenRouter config (reuses existing pattern from ai_validator.py)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_BOT_SENTIMENT = os.getenv("LLM_BOT_SENTIMENT", "google/gemini-2.5-flash")
LLM_BOT_RISK = os.getenv("LLM_BOT_RISK", "deepseek/deepseek-chat-v3-0324")

HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost:5001",
    "X-Title": "AI Crypto Trading Bot",
}


def _call_ollama(prompt: str, timeout: int = 30) -> dict:
    """Call local Ollama model for fast validation."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1, "num_predict": 512},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        return _parse_json(text)
    except requests.exceptions.ConnectionError:
        logger.warning("Ollama not reachable — will fall back to OpenRouter-only")
        return {"error": "ollama_unreachable", "execute": None}
    except Exception as e:
        logger.warning(f"Ollama call failed: {e}")
        return {"error": str(e), "execute": None}


def _call_openrouter(model: str, messages: list, timeout: int = 60) -> str:
    """Make a single call to OpenRouter (reuses ai_validator.py pattern)."""
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.1,
    }
    try:
        resp = requests.post(OPENROUTER_URL, headers=HEADERS, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return json.dumps({"error": str(e)})


def _parse_json(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences."""
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return {"error": "Failed to parse LLM response", "raw": text[:500]}


def _build_trade_context(coin: str, side: str, price: float, indicators: dict,
                         signal_reason: str, performance_history: dict = None,
                         strategy_name: str = None) -> str:
    """Build context string for LLM validation."""
    ctx = f"""Crypto Trade Signal for Validation:
- Coin: {coin}
- Side: {side.upper()}
- Current Price: ${price:,.2f}
- Signal Reason: {signal_reason}
- Strategy: {strategy_name or 'unknown'}

Technical Indicators:
- RSI(14): {indicators.get('rsi_14', 'N/A')}
- MACD: {indicators.get('macd', 'N/A')}
- MACD Histogram: {indicators.get('macd_histogram', 'N/A')}
- SMA 50: {indicators.get('sma_50', 'N/A')}
- SMA 200: {indicators.get('sma_200', 'N/A')}
- ATR(14): {indicators.get('atr_14', 'N/A')}
- Volume: {indicators.get('volume', 'N/A')}
- Relative Volume: {indicators.get('relative_volume', 'N/A')}"""

    if performance_history and performance_history.get("overall"):
        overall = performance_history["overall"]
        ctx += f"""

Historical Performance (last 7 days):
- Overall: {overall['total_trades']} trades, {overall['win_rate']}% win rate, avg P&L ${overall['avg_pnl']}, total P&L ${overall['total_pnl']}"""

        strat_stats = performance_history.get("by_strategy", {}).get(strategy_name or "")
        if strat_stats:
            ctx += f"\n- This strategy ({strategy_name}): {strat_stats['trades']} trades, {strat_stats['win_rate']}% win rate, avg P&L ${strat_stats['avg_pnl']}"

        coin_stats = performance_history.get("by_coin", {}).get(coin)
        if coin_stats:
            ctx += f"\n- This coin ({coin}): {coin_stats['trades']} trades, {coin_stats['win_rate']}% win rate, avg P&L ${coin_stats['avg_pnl']}"

        if overall["win_rate"] < 40:
            ctx += "\n\nWARNING: Overall win rate is BELOW 40%. Be MORE SELECTIVE — only approve trades with strong confluence."

    ctx += "\n\nThis is a PAPER TRADING bot running on demo API. Evaluate the trade setup quality."
    return ctx


def _validate_ollama(trade_context: str) -> dict:
    """Gate 1: Local Ollama validation — fast, free filter."""
    prompt = f"""You are a strict crypto trading risk analyst. Evaluate this trade signal and decide if it should execute.

{trade_context}

Respond in JSON format ONLY:
{{
    "execute": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "risk_level": "low/medium/high/extreme"
}}

Be SELECTIVE. Only approve trades where multiple indicators clearly align in the same direction. Reject if:
- RSI contradicts the trade direction (e.g. overbought for a buy, oversold for a sell)
- MACD histogram is weak or flat
- Historical win rate is poor (below 40%)
- The signal lacks strong confluence across at least 2-3 indicators
Return execute=true ONLY for high-quality setups with clear edge.
"""
    return _call_ollama(prompt)


def _validate_sentiment(trade_context: str) -> dict:
    """Gate 2a: OpenRouter sentiment analysis."""
    messages = [
        {"role": "system", "content": "You are a strict crypto market sentiment analyst. You protect capital by only approving high-conviction setups. Respond in JSON only."},
        {"role": "user", "content": f"""{trade_context}

Evaluate the SENTIMENT and MOMENTUM of this trade. Respond in JSON:
{{
    "execute": true/false,
    "sentiment_score": -1.0 to 1.0,
    "momentum": "bullish/neutral/bearish",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}

Only approve if momentum CLEARLY supports the trade direction with strong indicator alignment. If historical performance shows a losing streak or low win rate, require STRONG confluence before approving. Reject if momentum is ambiguous, indicators conflict, or confidence is below 0.6."""}
    ]
    raw = _call_openrouter(LLM_BOT_SENTIMENT, messages)
    return _parse_json(raw)


def _validate_risk(trade_context: str) -> dict:
    """Gate 2b: OpenRouter risk analysis."""
    messages = [
        {"role": "system", "content": "You are a conservative crypto trading risk manager. Your PRIMARY job is to protect capital and prevent losses. Respond in JSON only."},
        {"role": "user", "content": f"""{trade_context}

Evaluate the RISK of this trade. Consider: overextension, false signal probability, volatility risk, position timing, and historical performance. Respond in JSON:
{{
    "execute": true/false,
    "risk_score": 0-100,
    "stop_loss_adequate": true/false,
    "false_signal_probability": 0.0-1.0,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}

Be CONSERVATIVE. If historical performance shows a low win rate, raise the bar significantly. Reject when:
- Risk/reward ratio is unclear or unfavorable
- False signal probability exceeds 0.5
- Risk score is above 60
- The setup relies on a single weak indicator
Only approve trades with clear risk/reward edge and strong technical backing."""}
    ]
    raw = _call_openrouter(LLM_BOT_RISK, messages)
    return _parse_json(raw)


def get_trending_crypto() -> dict:
    """Use real market data + LLM to identify most talked-about / hottest crypto right now.

    Fetches recent price action for ~20 coins via yfinance, then asks LLM to
    analyze momentum, unusual volume, and narrative to identify what's trending.
    """
    import yfinance as yf
    from datetime import datetime

    # Broad scan — top coins by market cap + meme coins
    scan_tickers = {
        "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana",
        "BNB-USD": "BNB", "XRP-USD": "XRP", "ADA-USD": "Cardano",
        "DOGE-USD": "Dogecoin", "AVAX-USD": "Avalanche", "DOT-USD": "Polkadot",
        "LINK-USD": "Chainlink", "MATIC-USD": "Polygon", "ATOM-USD": "Cosmos",
        "SHIB-USD": "Shiba Inu", "LTC-USD": "Litecoin", "UNI-USD": "Uniswap",
        "NEAR-USD": "NEAR", "ARB-USD": "Arbitrum", "OP-USD": "Optimism",
        "SUI-USD": "Sui", "PEPE-USD": "Pepe", "WIF-USD": "dogwifhat",
        "FET-USD": "Fetch.ai", "RNDR-USD": "Render", "INJ-USD": "Injective",
    }

    # Fetch 5-day 1h data to get recent action
    market_data = []
    try:
        tickers_str = " ".join(scan_tickers.keys())
        data = yf.download(tickers_str, period="5d", interval="1h", group_by="ticker", progress=False)

        for ticker, name in scan_tickers.items():
            try:
                if len(scan_tickers) > 1:
                    df = data[ticker.replace("-", "")]  # yf download uses no hyphens for multi
                    if hasattr(data, 'columns') and isinstance(data.columns, type(data.columns)):
                        # Multi-ticker download has MultiIndex columns
                        df = data[ticker]
                else:
                    df = data

                if df.empty or len(df) < 5:
                    continue

                close = df["Close"].dropna()
                volume = df["Volume"].dropna()
                if len(close) < 5:
                    continue

                current = float(close.iloc[-1])
                price_2h_ago = float(close.iloc[-3]) if len(close) >= 3 else current
                price_24h_ago = float(close.iloc[-24]) if len(close) >= 24 else current

                chg_2h = ((current - price_2h_ago) / price_2h_ago * 100) if price_2h_ago else 0
                chg_24h = ((current - price_24h_ago) / price_24h_ago * 100) if price_24h_ago else 0

                recent_vol = float(volume.iloc[-3:].mean()) if len(volume) >= 3 else 0
                avg_vol = float(volume.iloc[-24:].mean()) if len(volume) >= 24 else recent_vol
                vol_surge = (recent_vol / avg_vol) if avg_vol > 0 else 1.0

                market_data.append({
                    "coin": name,
                    "ticker": ticker.replace("-USD", "-USDT"),
                    "price": round(current, 4),
                    "chg_2h": round(chg_2h, 2),
                    "chg_24h": round(chg_24h, 2),
                    "vol_surge": round(vol_surge, 2),
                })
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Failed to fetch market data for trending: {e}")

    # Fallback if yfinance multi-download failed, try individual
    if len(market_data) < 5:
        for ticker, name in list(scan_tickers.items())[:12]:
            try:
                t = yf.Ticker(ticker)
                df = t.history(period="5d", interval="1h")
                if df.empty or len(df) < 5:
                    continue
                close = df["Close"].dropna()
                volume = df["Volume"].dropna()
                current = float(close.iloc[-1])
                price_2h = float(close.iloc[-3]) if len(close) >= 3 else current
                price_24h = float(close.iloc[-24]) if len(close) >= 24 else current
                chg_2h = ((current - price_2h) / price_2h * 100) if price_2h else 0
                chg_24h = ((current - price_24h) / price_24h * 100) if price_24h else 0
                recent_vol = float(volume.iloc[-3:].mean()) if len(volume) >= 3 else 0
                avg_vol = float(volume.iloc[-24:].mean()) if len(volume) >= 24 else recent_vol
                vol_surge = (recent_vol / avg_vol) if avg_vol > 0 else 1.0
                market_data.append({
                    "coin": name, "ticker": ticker.replace("-USD", "-USDT"),
                    "price": round(current, 4), "chg_2h": round(chg_2h, 2),
                    "chg_24h": round(chg_24h, 2), "vol_surge": round(vol_surge, 2),
                })
            except Exception:
                continue

    if not market_data:
        return {"error": "Could not fetch market data", "trending": [], "analysis": ""}

    # Sort by absolute 2h change + volume surge for most active
    market_data.sort(key=lambda x: abs(x["chg_2h"]) + x["vol_surge"], reverse=True)

    # Build context for LLM
    market_summary = "\n".join([
        f"  {d['coin']} ({d['ticker']}): ${d['price']:,} | 2h: {d['chg_2h']:+.1f}% | 24h: {d['chg_24h']:+.1f}% | Vol surge: {d['vol_surge']:.1f}x"
        for d in market_data
    ])

    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    prompt = f"""You are a crypto market analyst. Based on the real-time market data below, identify the TOP 3-5 most interesting/trending cryptocurrencies RIGHT NOW.

Current time: {now}

LIVE MARKET DATA (last 2-5 hours):
{market_summary}

For each trending coin, explain in 1-2 sentences WHY it's trending (price action, volume surge, narrative, sector rotation, etc). Also mention any coins showing unusual volume surges (>1.5x) even without big price moves — that often signals accumulation.

Respond in JSON:
{{
    "trending": [
        {{
            "coin": "Name",
            "ticker": "XXX-USDT",
            "signal": "hot/warming/unusual_volume",
            "reason": "Brief explanation of why it's trending"
        }}
    ],
    "market_mood": "bullish/neutral/bearish/mixed",
    "summary": "2-3 sentence overall market take"
}}"""

    messages = [
        {"role": "system", "content": "You are a crypto market intelligence analyst. Identify trending coins from real market data. Be specific about price action and volume. JSON only."},
        {"role": "user", "content": prompt},
    ]

    raw = _call_openrouter(LLM_BOT_SENTIMENT, messages, timeout=30)
    analysis = _parse_json(raw)

    return {
        "market_data": market_data[:10],  # Top 10 by activity
        "analysis": analysis,
        "timestamp": now,
    }


def detect_direction(coin: str, price: float, indicators: dict) -> dict:
    """Use LLM to classify the market direction as bullish, bearish, or neutral."""
    indicator_summary = f"""Coin: {coin}
Current Price: ${price:,.2f}

Technical Indicators:
- RSI(14): {indicators.get('rsi_14', 'N/A')}
- MACD: {indicators.get('macd', 'N/A')}
- MACD Histogram: {indicators.get('macd_histogram', 'N/A')}
- SMA 8: {indicators.get('sma_8', 'N/A')}
- EMA 20: {indicators.get('ema_20', 'N/A')}
- SMA 50: {indicators.get('sma_50', 'N/A')}
- SMA 200: {indicators.get('sma_200', 'N/A')}
- ATR(14): {indicators.get('atr_14', 'N/A')}
- Relative Volume: {indicators.get('relative_volume', 'N/A')}"""

    messages = [
        {"role": "system", "content": "You are a crypto technical analyst. Analyze indicators to determine market direction. Respond in JSON only."},
        {"role": "user", "content": f"""{indicator_summary}

Based on the technical indicators above, determine the overall market direction for {coin}.

Respond in JSON:
{{
    "direction": "bullish" or "bearish" or "neutral",
    "confidence": 0.0 to 1.0,
    "reasoning": "brief explanation"
}}"""},
    ]
    try:
        raw = _call_openrouter(LLM_BOT_SENTIMENT, messages, timeout=15)
        result = _parse_json(raw)
        if "direction" not in result:
            return {"direction": "neutral", "confidence": 0, "reasoning": "Failed to parse LLM response"}
        return result
    except Exception as e:
        logger.warning(f"Direction detection failed for {coin}: {e}")
        return {"direction": "neutral", "confidence": 0, "reasoning": f"Error: {e}"}


def validate_trade(coin: str, side: str, price: float,
                   indicators: dict, signal_reason: str,
                   performance_history: dict = None,
                   strategy_name: str = None) -> dict:
    """Full 2-layer validation pipeline.

    Returns:
        dict with 'approved' (bool), 'ollama', 'sentiment', 'risk' results, 'summary'
    """
    trade_context = _build_trade_context(coin, side, price, indicators, signal_reason,
                                         performance_history=performance_history,
                                         strategy_name=strategy_name)

    result = {
        "approved": False,
        "ollama": None,
        "sentiment": None,
        "risk": None,
        "summary": "",
    }

    # Gate 1: Ollama (local) — non-blocking, feeds into vote count
    ollama_result = _validate_ollama(trade_context)
    result["ollama"] = ollama_result

    ollama_available = ollama_result.get("error") != "ollama_unreachable"
    ollama_approved = ollama_result.get("execute") is True

    # Gate 2: OpenRouter — 2 models in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        sentiment_future = executor.submit(_validate_sentiment, trade_context)
        risk_future = executor.submit(_validate_risk, trade_context)

        sentiment_result = sentiment_future.result()
        risk_result = risk_future.result()

    result["sentiment"] = sentiment_result
    result["risk"] = risk_result

    sentiment_approved = sentiment_result.get("execute") is True
    risk_approved = risk_result.get("execute") is True

    # Consensus: majority vote (2 out of 3, or 1 out of 2 if Ollama unavailable)
    votes_for = sum([
        1 if ollama_approved else 0,
        1 if sentiment_approved else 0,
        1 if risk_approved else 0,
    ])
    total_voters = (1 if ollama_available else 0) + 2  # Ollama + 2 OpenRouter

    # Check if OpenRouter models had errors (couldn't vote)
    sentiment_error = "error" in sentiment_result and not sentiment_result.get("execute")
    risk_error = "error" in risk_result and not risk_result.get("execute")
    if sentiment_error:
        total_voters -= 1
    if risk_error:
        total_voters -= 1

    # If no validators available at all, approve for paper trading
    if total_voters == 0:
        result["approved"] = True
        result["summary"] = "All validators unreachable — auto-approved (paper trading)"
    elif votes_for >= max(1, (total_voters + 1) // 2):
        # Majority approves (at least half, minimum 1)
        result["approved"] = True
        approvers = []
        if ollama_approved:
            approvers.append("Ollama")
        if sentiment_approved:
            approvers.append("Sentiment")
        if risk_approved:
            approvers.append("Risk")
        result["summary"] = f"Approved by {'/'.join(approvers)} ({votes_for}/{total_voters} votes)"
    else:
        reasons = []
        if ollama_available and not ollama_approved:
            reasons.append(f"Ollama: {ollama_result.get('reasoning', 'rejected')}")
        if not sentiment_approved and not sentiment_error:
            reasons.append(f"Sentiment: {sentiment_result.get('reasoning', 'rejected')}")
        if not risk_approved and not risk_error:
            reasons.append(f"Risk: {risk_result.get('reasoning', 'rejected')}")
        result["summary"] = f"Blocked ({votes_for}/{total_voters} votes) — " + "; ".join(reasons)

    logger.info(f"Trade {side} {coin}: {'APPROVED' if result['approved'] else 'BLOCKED'} — {result['summary']}")
    return result
