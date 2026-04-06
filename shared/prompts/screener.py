"""LLM prompt constants for the stock screener vetting functions."""

# ─── System Prompts ───────────────────────────────────────────

LOWCAP_SYSTEM = "You are a small-cap risk analyst. Focus on: dilution risk, liquidity, bankruptcy, insider activity, reverse splits, pump-and-dump. Respond with ONLY valid JSON."

LOWCAP_USER_TEMPLATE = """Vet this low-cap stock for investment. Risk management is #1. Never allocate >2% per position for small-caps.

{summary}

Check: 1) Dilution risk (share offerings, ATM programs) 2) Liquidity (can you exit?) 3) Bankruptcy/restructuring risk 4) Insider selling 5) Reverse split history 6) Pump-and-dump signals 7) Revenue trajectory 8) Cash runway

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"survival_12m":80,"growth_catalysts":["catalyst1"],"red_flags":["flag1"],"dilution_risk":"LOW","liquidity_risk":"LOW","fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":2,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""

MIDCAP_SYSTEM = "You are a mid-cap growth/value analyst. Balance growth potential with valuation discipline. Respond with ONLY valid JSON."

MIDCAP_USER_TEMPLATE = """Evaluate this mid-cap stock for investment. Look for strong growth at reasonable valuations.

{summary}

Evaluate: 1) Revenue growth sustainability 2) Earnings trajectory 3) Competitive position 4) Valuation vs growth rate 5) Margin expansion potential 6) Institutional interest 7) Key risks 8) Total addressable market

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"growth_catalysts":["catalyst1"],"red_flags":["flag1"],"revenue_growth_trend":"ACCELERATING","earnings_momentum":"POSITIVE","moat_strength":"MODERATE","fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""

LARGECAP_SYSTEM = "You are a large-cap growth investor. Focus on revenue acceleration, earnings growth trajectory, market share gains, competitive moat, and TAM expansion. Respond with ONLY valid JSON."

LARGECAP_USER_TEMPLATE = """Evaluate this large-cap stock for growth investing. Focus on whether this company is accelerating growth and can sustain market leadership.

{summary}

Evaluate: 1) Revenue acceleration vs deceleration 2) Earnings growth trajectory 3) Market share gains 4) Competitive moat strength 5) TAM expansion opportunities 6) Margin expansion potential 7) Growth catalysts in next 12 months 8) Valuation relative to growth rate

Respond with ONLY this JSON:
{{"verdict":"STRONG GROWTH","confidence":70,"revenue_growth_trend":"ACCELERATING","earnings_momentum":"STRONG","moat_strength":"WIDE","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":15,"position_limit_pct":10,"summary":"1-2 sentences"}}

verdict must be one of: STRONG GROWTH, STEADY, SLOWING"""

ETF_SYSTEM = "You are a growth ETF analyst. Focus on sector momentum, thematic tailwinds, expense efficiency, liquidity, and holdings quality. Respond with ONLY valid JSON."

ETF_USER_TEMPLATE = """Evaluate this ETF for growth investing. Focus on whether this ETF captures strong secular growth themes.

{summary}

Evaluate: 1) Sector/theme momentum 2) Thematic tailwinds vs headwinds 3) Expense ratio efficiency 4) Liquidity (AUM, volume) 5) Holdings quality and concentration 6) Growth catalysts 7) Correlation to overall growth theme 8) Risk-adjusted returns

Respond with ONLY this JSON:
{{"verdict":"STRONG BUY","confidence":70,"sector_momentum":"STRONG","thematic_strength":"HIGH","expense_efficiency":"GOOD","top_holdings_quality":"HIGH","growth_catalysts":["catalyst1"],"risks":["risk1"],"target_allocation_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: STRONG BUY, ACCUMULATE, HOLD"""

METALS_MINING_SYSTEM = "You are a metals & mining analyst. Focus on commodity price sensitivity, production costs, reserve life, and jurisdictional risk. Respond with ONLY valid JSON."

METALS_MINING_USER_TEMPLATE = """Evaluate this metals/mining stock for investment.

{summary}

Evaluate: 1) Commodity price sensitivity and outlook 2) Production growth trajectory 3) Reserve quality and mine life 4) Cost structure (AISC for gold/silver, C1 for copper) 5) Balance sheet / debt load 6) Political/jurisdictional risk 7) Exploration pipeline 8) Dividend sustainability (if any)

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"commodity_outlook":"BULLISH","production_trend":"GROWING","reserve_quality":"STRONG","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""

CRYPTO_SYSTEM = "You are a crypto analyst. Focus on network adoption, tokenomics, developer activity, regulatory risk, and market cycle positioning. Respond with ONLY valid JSON."

CRYPTO_USER_TEMPLATE = """Evaluate this cryptocurrency for investment.

{summary}

Evaluate: 1) Network adoption and usage metrics 2) Developer activity / ecosystem growth 3) TVL and DeFi metrics (if applicable) 4) Tokenomics (supply schedule, inflation, staking yield) 5) Market cycle position (accumulation, markup, distribution) 6) BTC correlation and narrative strength 7) Regulatory risk / exchange listing breadth 8) Transaction volume trends

Respond with ONLY this JSON:
{{"verdict":"BULLISH","confidence":65,"network_strength":"STRONG","adoption_trend":"GROWING","tokenomics_rating":"GOOD","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":25,"summary":"1-2 sentences"}}

verdict must be one of: BULLISH, NEUTRAL, BEARISH"""

AI_SYSTEM = "You are an AI/Artificial Intelligence sector analyst. Focus on AI revenue exposure, competitive moat in AI, GPU/compute positioning, and valuation relative to AI growth trajectory. Respond with ONLY valid JSON."

AI_USER_TEMPLATE = """Evaluate this AI-related stock for investment.

{summary}

Evaluate: 1) AI revenue as % of total revenue 2) AI product pipeline and roadmap 3) Competitive moat in AI (data, models, distribution) 4) GPU/compute dependency and costs 5) Partnership ecosystem (cloud, enterprise) 6) Enterprise AI adoption rate 7) Valuation relative to AI growth trajectory 8) Key risks (competition, regulation, commoditization)

Respond with ONLY this JSON:
{{"verdict":"OPPORTUNITY","confidence":65,"ai_exposure":"HIGH","growth_trajectory":"ACCELERATING","competitive_position":"STRONG","growth_catalysts":["catalyst1"],"risks":["risk1"],"fair_value":0.00,"upside_pct":25,"risk_score":40,"position_limit_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: OPPORTUNITY, RISKY, AVOID"""

GAINERS_SYSTEM = "You are a momentum analyst. Evaluate whether a top-gaining stock has sustainable momentum or is overbought and due for reversal. Respond with ONLY valid JSON."

GAINERS_USER_TEMPLATE = """This stock gained {pct_change:+.2f}% over the past {period_label}. Evaluate if the momentum is sustainable.

{summary}

Evaluate: 1) Catalyst behind the move 2) Volume confirmation 3) Overbought risk (RSI, distance from moving averages) 4) Reversal danger 5) Continuation probability 6) Sector momentum alignment 7) Short squeeze potential 8) Entry risk at current levels

Respond with ONLY this JSON:
{{"verdict":"MOMENTUM BUY","confidence":65,"momentum_quality":"STRONG","catalyst":"reason for move","overbought_risk":"LOW","reversal_probability":20,"continuation_target":0.00,"support_level":0.00,"growth_catalysts":["catalyst1"],"risks":["risk1"],"position_limit_pct":5,"summary":"1-2 sentences"}}

verdict must be one of: MOMENTUM BUY, WATCH, AVOID"""

LOSERS_SYSTEM = "You are a contrarian/value analyst. Evaluate whether a top-losing stock is oversold and ripe for recovery or a falling knife. Respond with ONLY valid JSON."

LOSERS_USER_TEMPLATE = """This stock dropped {pct_change:+.2f}% over the past {period_label}. Evaluate if it's a recovery opportunity or a falling knife.

{summary}

Evaluate: 1) Reason for the decline 2) Oversold indicators 3) Balance sheet survival ability 4) Support level proximity 5) Institutional buying/selling 6) Sector headwinds 7) Recovery catalyst potential 8) Downside risk remaining

Respond with ONLY this JSON:
{{"verdict":"RECOVERY BUY","confidence":65,"decline_reason":"reason","oversold_level":"DEEPLY","balance_sheet":"STRONG","support_level":0.00,"recovery_catalyst":"potential catalyst","downside_remaining":10,"growth_catalysts":["catalyst1"],"risks":["risk1"],"position_limit_pct":3,"summary":"1-2 sentences"}}

verdict must be one of: RECOVERY BUY, WATCH, AVOID"""

HOT_SECTORS_SYSTEM = "You are a senior market strategist. Return ONLY valid JSON."

HOT_SECTORS_USER_TEMPLATE = """Identify the top 6 hottest stock market sectors/themes over the {label}.
Return JSON: {{"sectors": [
  {{"rank":1,"name":"...","trend":"bullish|bearish|neutral",
    "momentum":"strong|moderate|fading",
    "catalysts":["...","..."],
    "top_tickers":["SYM1","SYM2","SYM3"],
    "outlook":"1-2 sentence forward outlook"}}
]}}
Consider: sector rotation, earnings trends, macro catalysts, fund flows, regulatory changes."""
