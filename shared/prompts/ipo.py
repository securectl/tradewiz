"""LLM prompt templates for IPO discovery and vetting."""


def build_ipo_discovery_prompt(social_context, platform_names):
    return f"""You are an elite IPO, pre-IPO, and venture capital analyst. You have LIVE social data from 3 parallel streams (Reddit, X/Twitter, Substack + tech news).

{social_context}

FOCUS: Emerging technology and hidden gems. Prioritize companies in:
- AI/ML, quantum computing, robotics, autonomous systems
- Climate tech, clean energy, fusion, carbon capture
- Space tech, defense tech, cybersecurity
- Deep tech, biotech/synthetic bio, neurotech
- Web3 infrastructure (NOT meme coins)

Companies mentioned across MULTIPLE sources (cross-referenced) should get HIGHER social_buzz scores.

INVESTMENT PLATFORMS (use in investment_vehicles): {platform_names}

Return FOUR categories. JSON only: {{"ipos": [...]}}

## CATEGORY 1: UPCOMING IPOs (opportunity_type: "ipo") — min 5
S-1 filed or announced IPO plans. Focus on $500M+ valuations in emerging tech.
Include hidden gems — not just the obvious mega-unicorns everyone knows.

## CATEGORY 2: PRE-IPO ACCESS (opportunity_type: "pre_ipo") — min 4
Buy shares NOW via Forge Global, EquityZen, Linqto, Hiive, or IPO access (Robinhood, Fidelity, SoFi, Webull). Include Destiny Tech100 (DXYZ) and ARK Venture Fund (ARKVX).

## CATEGORY 3: VC-BACKED DEALS (opportunity_type: "vc") — min 5
Late-stage private companies (Series C+), $500M+ valuation. Top-tier VC backed.
Emphasize emerging tech and hidden gems — companies most retail investors haven't heard of.
Access via: Republic, Wefunder, StartEngine, AngelList, Forge, EquityZen, Linqto, MicroVentures.

## CATEGORY 4: STARTUP OPPORTUNITIES (opportunity_type: "startup") — min 4
Early/mid-stage (Seed-Series B) on crowdfunding platforms. High-growth emerging tech.
Active or recent campaigns on Republic, Wefunder, StartEngine, SeedInvest.

Per entry fields: company_name, ticker (or "TBD"/"PRIVATE"), sector,
opportunity_type, expected_date, expected_valuation, expected_price_range,
description, social_buzz (1-5), social_signals, institutional_interest (1-5),
market_fit (1-5), moat (1-5), risk_level (1-5), overall_rating (1-5),
rating_reason, key_risks [2-3], catalysts [2-3],
investment_vehicles [{{platform, type, min_investment, accreditation, expected_return, access_notes}}],
vc_backers [], last_funding_round, reddit_mentions (int).

RULES: Only private/pre-public companies. Real companies only. Every entry needs investment_vehicles.
Sort each category by overall_rating desc. Minimum 18 total entries."""


def build_ipo_retry_prompt():
    return (
        "List 14 upcoming IPO/pre-IPO/VC/startup opportunities in emerging tech as JSON. "
        "Focus on hidden gems in AI, climate tech, deep tech, space, defense, biotech. "
        "Return: {\"ipos\": [{\"company_name\": str, \"ticker\": str, \"sector\": str, "
        "\"opportunity_type\": \"ipo\"|\"pre_ipo\"|\"vc\"|\"startup\", "
        "\"expected_date\": str, \"expected_valuation\": str, \"description\": str, "
        "\"social_buzz\": 1-5, \"institutional_interest\": 1-5, \"market_fit\": 1-5, "
        "\"moat\": 1-5, \"risk_level\": 1-5, \"overall_rating\": 1-5, "
        "\"rating_reason\": str, \"key_risks\": [str], \"catalysts\": [str], "
        "\"investment_vehicles\": [{\"platform\": str, \"type\": str, \"min_investment\": str, "
        "\"accreditation\": \"none\"|\"accredited\", \"expected_return\": str, \"access_notes\": str}], "
        "\"vc_backers\": [str], \"last_funding_round\": str}]}. "
        "5 IPOs, 3 pre-IPO, 4 VC, 2 startups. Only private companies. JSON only."
    )
