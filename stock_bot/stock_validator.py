"""
Stock Trade Validator — Thin wrapper over crypto_validator with stock-specific context.
Uses the same 2-layer LLM consensus pipeline (Ollama + 2x OpenRouter).
"""

import logging
import concurrent.futures

from crypto_bot.crypto_validator import (
    _call_ollama, _call_openrouter, _parse_json,
    OLLAMA_URL, OLLAMA_MODEL,
    OPENROUTER_URL, OPENROUTER_API_KEY,
    LLM_BOT_SENTIMENT, LLM_BOT_RISK,
)

logger = logging.getLogger(__name__)


def _build_stock_trade_context(symbol: str, side: str, price: float, indicators: dict,
                                signal_reason: str, performance_history: dict = None,
                                strategy_name: str = None) -> str:
    """Build context string for stock trade LLM validation."""
    ctx = f"""Stock Trade Signal for Validation:
- Market: US Equities
- Symbol: {symbol}
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
- Relative Volume: {indicators.get('relative_volume', 'N/A')}

Stock-Specific Risks:
- Earnings proximity: Check if earnings are imminent (gap risk)
- PDT rule: Pattern Day Trader restrictions may apply
- Market hours: Only trades during regular 9:30AM-4PM ET hours
- Sector rotation and macro conditions affect equities differently than crypto"""

    if performance_history and performance_history.get("overall"):
        overall = performance_history["overall"]
        ctx += f"""

Historical Performance (last 7 days):
- Overall: {overall['total_trades']} trades, {overall['win_rate']}% win rate, avg P&L ${overall['avg_pnl']}, total P&L ${overall['total_pnl']}"""

        strat_stats = performance_history.get("by_strategy", {}).get(strategy_name or "")
        if strat_stats:
            ctx += f"\n- This strategy ({strategy_name}): {strat_stats['trades']} trades, {strat_stats['win_rate']}% win rate, avg P&L ${strat_stats['avg_pnl']}"

        symbol_stats = performance_history.get("by_coin", {}).get(symbol)
        if symbol_stats:
            ctx += f"\n- This stock ({symbol}): {symbol_stats['trades']} trades, {symbol_stats['win_rate']}% win rate, avg P&L ${symbol_stats['avg_pnl']}"

        if overall["win_rate"] < 30:
            ctx += "\n\nNOTE: Overall win rate is below 30%. Look for reasonable confluence before approving."

    # News-agent buzz + sentiment (informational context only — does NOT change
    # any risk gate; helps the LLM weigh news-driven moves).
    try:
        from features.news_agent.agent import ticker_signal
        sig = ticker_signal(symbol, hours=48)
        if sig["mentions"]:
            ctx += (f"\n\nNews/Reddit buzz (48h): {sig['mentions']} articles "
                    f"({sig['reddit_mentions']} from Reddit), net sentiment "
                    f"{sig['sentiment_score']:+.2f} (-1 bearish … +1 bullish).")
    except Exception:
        pass

    ctx += "\n\nThis is a PAPER TRADING bot on Alpaca paper. The goal is to ACTIVELY TRADE swing opportunities. Approve trades with reasonable technical confluence (2+ indicators). Only reject if indicators clearly conflict or setup is fundamentally flawed."
    return ctx


def _validate_stock_ollama(trade_context: str) -> dict:
    """Gate 1: Ollama Cloud validation for stocks."""
    prompt = f"""You are a stock trading analyst evaluating swing trade signals for a paper trading bot. Evaluate this trade signal and decide if it should execute.

{trade_context}

Respond in JSON format ONLY:
{{
    "execute": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "risk_level": "low/medium/high/extreme"
}}

Approve trades where the primary indicators support the trade direction. Consider equity-specific risks:
- Earnings dates can cause large gaps
- PDT restrictions limit day trading below $25K
- Sector and macro conditions matter
Reject only if indicators clearly conflict or the setup is fundamentally flawed. Moderate-confidence setups with decent risk/reward should be approved for swing trading.
"""
    return _call_ollama(prompt)


def _validate_stock_sentiment(trade_context: str) -> dict:
    """Gate 2a: OpenRouter sentiment analysis for stocks."""
    messages = [
        {"role": "system", "content": "You are a stock market sentiment analyst evaluating swing trade setups for a paper trading bot. Respond in JSON only."},
        {"role": "user", "content": f"""{trade_context}

Evaluate the SENTIMENT and MOMENTUM of this stock trade. Respond in JSON:
{{
    "execute": true/false,
    "sentiment_score": -1.0 to 1.0,
    "momentum": "bullish/neutral/bearish",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}

Approve if momentum generally supports the trade direction. If historical performance shows a losing streak, require reasonable confluence. Reject only if momentum clearly contradicts the trade direction or indicators significantly conflict."""}
    ]
    raw = _call_openrouter(LLM_BOT_SENTIMENT, messages, role="bot_sentiment")
    return _parse_json(raw)


def _validate_stock_risk(trade_context: str) -> dict:
    """Gate 2b: OpenRouter risk analysis for stocks."""
    messages = [
        {"role": "system", "content": "You are a stock trading risk manager evaluating swing trade setups for a paper trading bot. Consider earnings risk, market hours, PDT rules, and sector rotation. Respond in JSON only."},
        {"role": "user", "content": f"""{trade_context}

Evaluate the RISK of this stock trade. Consider: earnings gap risk, false signal probability, volatility, position timing, market regime, and historical performance. Respond in JSON:
{{
    "execute": true/false,
    "risk_score": 0-100,
    "stop_loss_adequate": true/false,
    "false_signal_probability": 0.0-1.0,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}

Approve trades with acceptable risk/reward ratios. Reject only when risk/reward is clearly unfavorable or false signal probability exceeds 0.65. This is paper trading — err toward taking the trade to gather performance data."""}
    ]
    raw = _call_openrouter(LLM_BOT_RISK, messages, role="bot_risk")
    return _parse_json(raw)


def validate_stock_trade(symbol: str, side: str, price: float,
                          indicators: dict, signal_reason: str,
                          performance_history: dict = None,
                          strategy_name: str = None) -> dict:
    """Full 2-layer validation pipeline for stock trades.

    Returns:
        dict with 'approved' (bool), 'ollama', 'sentiment', 'risk' results, 'summary'
    """
    trade_context = _build_stock_trade_context(
        symbol, side, price, indicators, signal_reason,
        performance_history=performance_history,
        strategy_name=strategy_name,
    )

    result = {
        "approved": False,
        "ollama": None,
        "sentiment": None,
        "risk": None,
        "summary": "",
    }

    # Gate 1: Ollama (local) — non-blocking, feeds into vote count
    ollama_result = _validate_stock_ollama(trade_context)
    result["ollama"] = ollama_result

    ollama_available = ollama_result.get("error") != "ollama_unreachable"
    ollama_approved = ollama_result.get("execute") is True

    # Gate 2: OpenRouter — 2 models in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        sentiment_future = executor.submit(_validate_stock_sentiment, trade_context)
        risk_future = executor.submit(_validate_stock_risk, trade_context)

        try:
            sentiment_result = sentiment_future.result(timeout=90)
        except Exception as e:
            logger.warning(f"Sentiment future failed/timed out: {e}")
            sentiment_result = {"execute": True, "confidence": 0.25, "reasoning": "Fallback — sentiment validator timed out (paper trading auto-approve, low confidence)"}

        try:
            risk_result = risk_future.result(timeout=90)
        except Exception as e:
            logger.warning(f"Risk future failed/timed out: {e}")
            risk_result = {"execute": True, "confidence": 0.25, "reasoning": "Fallback — risk validator timed out (paper trading auto-approve, low confidence)"}

    result["sentiment"] = sentiment_result
    result["risk"] = risk_result

    sentiment_approved = sentiment_result.get("execute") is True
    risk_approved = risk_result.get("execute") is True

    # Consensus: majority vote
    votes_for = sum([
        1 if ollama_approved else 0,
        1 if sentiment_approved else 0,
        1 if risk_approved else 0,
    ])
    total_voters = (1 if ollama_available else 0) + 2

    sentiment_error = "error" in sentiment_result and not sentiment_result.get("execute")
    risk_error = "error" in risk_result and not risk_result.get("execute")
    if sentiment_error:
        total_voters -= 1
    if risk_error:
        total_voters -= 1

    if total_voters == 0:
        result["approved"] = True
        result["summary"] = "All validators unreachable — auto-approved (paper trading)"
    elif votes_for >= max(1, (total_voters + 1) // 2):
        approvers = []
        if ollama_approved:
            approvers.append("Ollama")
        if sentiment_approved:
            approvers.append("Sentiment")
        if risk_approved:
            approvers.append("Risk")
        result["approved"] = True
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

    # Aggregate confidence from individual validators
    conf_values = []
    for v in [ollama_result, sentiment_result, risk_result]:
        c = v.get("confidence")
        if c is not None and isinstance(c, (int, float)):
            conf_values.append(float(c))
    result["confidence"] = round(sum(conf_values) / len(conf_values), 2) if conf_values else 0.5

    # Supervisor gate — only if trade approved and LLM_SUPERVISOR configured
    if result["approved"]:
        try:
            from ai_validator import _validate_supervisor, LLM_SUPERVISOR
            if LLM_SUPERVISOR:
                supervisor = _validate_supervisor(
                    trade_context[:1000],
                    {"ollama": ollama_result.get("execute"), "sentiment": sentiment_result.get("execute"),
                     "risk": risk_result.get("execute"), "approved": True, "summary": result["summary"]},
                    layer="bot_stock",
                )
                result["supervisor"] = supervisor
                if supervisor.get("override") is True:
                    result["approved"] = False
                    result["summary"] = f"Supervisor veto: {supervisor.get('reasoning', 'overridden')} (was: {result['summary']})"
                    logger.warning(f"Supervisor VETOED stock trade {side} {symbol}: {supervisor.get('reasoning')}")
        except Exception as e:
            logger.warning(f"Supervisor check failed (graceful skip): {e}")

    logger.info(f"Stock trade {side} {symbol}: {'APPROVED' if result['approved'] else 'BLOCKED'} — {result['summary']} (confidence={result['confidence']:.0%})")
    return result
