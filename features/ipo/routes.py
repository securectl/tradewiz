import json
import logging
from flask import Blueprint, jsonify, request
from shared.helpers import _uid, P
from shared.prompts.ipo import build_ipo_discovery_prompt, build_ipo_retry_prompt
from decorators import login_required, llm_rate_limit
from rate_limiter import set_llm_user

logger = logging.getLogger(__name__)
bp = Blueprint("ipo", __name__)


def _scrape_ipo_intel():
    """Scrape Reddit, X/Twitter, and Substack in 3 parallel streams for IPO/VC/startup intel.

    Returns dict with keys: reddit, x, substack, news — each a list of posts.
    Also returns cross_referenced: company names found in 2+ sources.
    """
    import requests as req
    import re
    import concurrent.futures
    from collections import Counter

    ua = {"User-Agent": "Mozilla/5.0 (TradeWiz/1.0; Market Intelligence Bot)"}
    ipo_vc_kw = ["ipo", "s-1", "pre-ipo", "going public", "spac", "series ", "venture",
                 "unicorn", "private market", "listing", "startup", "funding", "round",
                 "valuation", "pre ipo", "secondary", "growth stage", "emerging tech",
                 "hidden gem", "ai startup", "deep tech", "climate tech", "biotech",
                 "quantum", "robotics", "space tech", "defense tech"]

    def _fetch_reddit(sub, sort, limit=20):
        results = []
        try:
            url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}&t=month"
            resp = req.get(url, headers=ua, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                for child in data.get("data", {}).get("children", []):
                    p = child.get("data", {})
                    results.append({
                        "source": f"r/{sub}",
                        "channel": "reddit",
                        "title": p.get("title", ""),
                        "text": (p.get("selftext", "") or "")[:300],
                        "score": p.get("score", 0),
                        "comments": p.get("num_comments", 0),
                        "engagement": p.get("score", 0) + p.get("num_comments", 0) * 3,
                    })
        except Exception:
            pass
        return results

    def _fetch_reddit_search(query):
        results = []
        try:
            url = f"https://www.reddit.com/search.json?q={query}&sort=relevance&t=month&limit=15"
            resp = req.get(url, headers=ua, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                for child in data.get("data", {}).get("children", []):
                    p = child.get("data", {})
                    results.append({
                        "source": f"reddit:search",
                        "channel": "reddit",
                        "title": p.get("title", ""),
                        "text": (p.get("selftext", "") or "")[:300],
                        "score": p.get("score", 0),
                        "comments": p.get("num_comments", 0),
                        "engagement": p.get("score", 0) + p.get("num_comments", 0) * 3,
                    })
        except Exception:
            pass
        return results

    def _fetch_rss(feed_url, source_name, keywords=None):
        results = []
        try:
            resp = req.get(feed_url, timeout=12, headers=ua)
            if resp.status_code != 200:
                return results
            text = resp.text
            items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            if not items:
                items = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)
            for item in items[:15]:
                title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item) or re.search(r"<title>(.*?)</title>", item)
                desc_m = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>", item, re.DOTALL) or \
                         re.search(r"<description>(.*?)</description>", item, re.DOTALL) or \
                         re.search(r"<content[^>]*>(.*?)</content>", item, re.DOTALL) or \
                         re.search(r"<summary>(.*?)</summary>", item, re.DOTALL)
                title = title_m.group(1).strip() if title_m else ""
                desc = re.sub(r"<[^>]+>", "", (desc_m.group(1) if desc_m else ""))[:500]
                text_lower = (title + " " + desc).lower()
                if keywords is None or any(kw in text_lower for kw in keywords):
                    channel = "substack" if "substack" in source_name else "news"
                    results.append({
                        "source": source_name,
                        "channel": channel,
                        "title": title,
                        "text": desc[:300],
                        "score": 0,
                        "comments": 0,
                        "engagement": 15,
                    })
        except Exception:
            pass
        return results

    def _fetch_x_nitter(query):
        """Fetch X/Twitter posts via Nitter RSS instances."""
        results = []
        nitter_instances = [
            "nitter.privacydev.net",
            "nitter.poast.org",
            "nitter.woodland.cafe",
        ]
        for instance in nitter_instances:
            try:
                url = f"https://{instance}/search/rss?f=tweets&q={query}&since_time=2592000"
                resp = req.get(url, timeout=10, headers=ua)
                if resp.status_code != 200:
                    continue
                items = re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL)
                for item in items[:10]:
                    title_m = re.search(r"<title>(.*?)</title>", item)
                    desc_m = re.search(r"<description>(.*?)</description>", item, re.DOTALL)
                    title = title_m.group(1).strip() if title_m else ""
                    desc = re.sub(r"<[^>]+>", "", (desc_m.group(1) if desc_m else ""))[:300]
                    if title:
                        results.append({
                            "source": f"x:{query}",
                            "channel": "x",
                            "title": title,
                            "text": desc,
                            "score": 0,
                            "comments": 0,
                            "engagement": 20,
                        })
                if results:
                    break  # Got data from one instance, don't hit others
            except Exception:
                continue
        return results

    # ── 3 parallel streams ────────────────────────────────────────
    all_posts = {"reddit": [], "x": [], "substack": [], "news": []}

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
        futures_map = {}  # future -> channel

        # STREAM 1: Reddit (subreddits + searches)
        reddit_subs = ["ipos", "wallstreetbets", "stocks", "investing", "spacs",
                       "SecurityAnalysis", "venturecapital", "startups", "preipo",
                       "crowdfunding", "DeepTech", "ClimateTech"]
        for sub in reddit_subs:
            f = pool.submit(_fetch_reddit, sub, "hot", 20)
            futures_map[f] = "reddit"
            f = pool.submit(_fetch_reddit, sub, "new", 10)
            futures_map[f] = "reddit"

        reddit_searches = [
            "upcoming IPO 2026", "S-1 filing 2026", "pre-IPO shares",
            "Series C funding", "Series D funding", "unicorn IPO",
            "emerging tech startup invest", "hidden gem startup 2026",
            "AI startup funding", "deep tech IPO", "climate tech startup",
            "quantum computing startup", "robotics startup invest",
            "defense tech startup", "space tech IPO", "biotech hidden gem",
            "Forge Global pre-IPO", "Republic startup invest",
        ]
        for q in reddit_searches:
            f = pool.submit(_fetch_reddit_search, q)
            futures_map[f] = "reddit"

        # STREAM 2: X/Twitter via Nitter
        x_queries = [
            "upcoming IPO 2026", "pre-IPO investment",
            "Series C funding round", "startup going public",
            "emerging tech startup", "hidden gem startup",
            "AI startup Series", "deep tech funding",
            "climate tech invest", "defense tech startup",
            "biotech IPO filing", "fintech unicorn",
        ]
        for q in x_queries:
            f = pool.submit(_fetch_x_nitter, q)
            futures_map[f] = "x"

        # STREAM 3: Substack + tech/VC RSS feeds
        substack_feeds = [
            ("https://ipoedge.substack.com/feed", "substack:ipoedge"),
            ("https://thegeneralist.substack.com/feed", "substack:thegeneralist"),
            ("https://newcomer.co/feed", "substack:newcomer"),
            ("https://stockmarketnerd.substack.com/feed", "substack:stockmarketnerd"),
            ("https://ventureunlocked.substack.com/feed", "substack:ventureunlocked"),
            ("https://platformer.news/feed", "substack:platformer"),
            ("https://chamath.substack.com/feed", "substack:chamath"),
            ("https://investing1012dot0.substack.com/feed", "substack:investing1012"),
            ("https://aaronbush.substack.com/feed", "substack:aaronbush"),
            ("https://ark-invest.com/feed", "substack:ark"),
        ]
        for url, name in substack_feeds:
            f = pool.submit(_fetch_rss, url, name, ipo_vc_kw)
            futures_map[f] = "substack"

        tech_feeds = [
            ("https://techcrunch.com/category/venture/feed/", "techcrunch:venture", None),
            ("https://techcrunch.com/tag/ipo/feed/", "techcrunch:ipo", None),
            ("https://news.crunchbase.com/feed/", "crunchbase:news", None),
            ("https://pitchbook.com/rss/news", "pitchbook:news", None),
            ("https://www.axios.com/pro/fintech-deals/feed", "axios:fintech", None),
            ("https://fortune.com/tag/ipo/feed/", "fortune:ipo", None),
            ("https://www.renaissancecapital.com/IPOHome/RSS", "renaissance:ipo", None),
            ("https://sifted.eu/feed", "sifted:eu", ipo_vc_kw),
        ]
        for url, name, kw in tech_feeds:
            f = pool.submit(_fetch_rss, url, name, kw)
            futures_map[f] = "news"

        # Collect into streams
        for future in concurrent.futures.as_completed(futures_map, timeout=35):
            try:
                results = future.result(timeout=5)
                channel = futures_map[future]
                all_posts[channel].extend(results)
            except Exception:
                pass

    # Deduplicate per channel
    def _dedup(posts_list):
        seen = set()
        unique = []
        for p in posts_list:
            key = re.sub(r'[^a-z0-9]', '', p["title"].lower())[:50]
            if key and key not in seen:
                seen.add(key)
                unique.append(p)
        unique.sort(key=lambda x: x["engagement"], reverse=True)
        return unique

    for ch in all_posts:
        all_posts[ch] = _dedup(all_posts[ch])

    # Cross-reference: extract company names mentioned across multiple channels
    # Build a simple mention map
    company_pattern = re.compile(r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)*(?:\s(?:AI|Labs|Tech|Bio|Health|Energy|Space))?)\b')
    channel_mentions = {}  # company_name_lower -> set of channels
    for ch, posts_list in all_posts.items():
        for p in posts_list[:50]:
            text = p["title"] + " " + p["text"]
            names = company_pattern.findall(text)
            for name in names:
                if len(name) > 3:  # Skip short words
                    key = name.lower()
                    if key not in channel_mentions:
                        channel_mentions[key] = set()
                    channel_mentions[key].add(ch)

    cross_referenced = [name for name, channels in channel_mentions.items()
                        if len(channels) >= 2]

    # Flatten all posts for total count
    flat = []
    for ch in all_posts:
        flat.extend(all_posts[ch][:40])
    flat.sort(key=lambda x: x["engagement"], reverse=True)

    return {
        "streams": all_posts,
        "flat": flat[:120],
        "cross_referenced": cross_referenced[:30],
        "counts": {ch: len(posts) for ch, posts in all_posts.items()},
    }


IPO_VC_PLATFORMS = [
    # ── IPO Access Platforms ──
    {"name": "Robinhood IPO Access", "url": "robinhood.com", "category": "ipo_access",
     "focus": "IPO share allocation for retail investors", "min_investment": "$0 (fractional)",
     "accreditation": "none", "fees": "Commission-free", "liquidity": "High (post-IPO)",
     "description": "Get IPO shares at the offering price before they start trading. No minimum investment, available to all Robinhood users."},
    {"name": "Fidelity IPO Access", "url": "fidelity.com", "category": "ipo_access",
     "focus": "IPO share allocation via established brokerage", "min_investment": "$100-$500",
     "accreditation": "none", "fees": "Commission-free trades", "liquidity": "High",
     "description": "Access IPO shares through Fidelity's IPO program. Requires an eligible Fidelity account with minimum balance."},
    {"name": "SoFi IPO Investing", "url": "sofi.com", "category": "ipo_access",
     "focus": "IPO access for SoFi members", "min_investment": "$0 (fractional)",
     "accreditation": "none", "fees": "No commission", "liquidity": "High (post-IPO)",
     "description": "Reserve IPO shares before they go public. SoFi members get early access to select IPOs with no minimum."},
    {"name": "Webull IPO Access", "url": "webull.com", "category": "ipo_access",
     "focus": "IPO share access for retail traders", "min_investment": "$100",
     "accreditation": "none", "fees": "Commission-free", "liquidity": "High",
     "description": "Participate in IPOs directly through Webull with early access to upcoming offerings."},
    # ── Pre-IPO / Secondary Market ──
    {"name": "Forge Global", "url": "forgeglobal.com", "category": "pre_ipo",
     "focus": "Pre-IPO secondary market for late-stage private companies", "min_investment": "$10,000",
     "accreditation": "accredited", "fees": "5% transaction fee", "liquidity": "Low (private shares)",
     "description": "Buy and sell shares of private companies like SpaceX, Stripe, Databricks before they IPO. Accredited investors only."},
    {"name": "EquityZen", "url": "equityzen.com", "category": "pre_ipo",
     "focus": "Pre-IPO investment funds for private companies", "min_investment": "$10,000",
     "accreditation": "accredited", "fees": "5% placement fee", "liquidity": "Low",
     "description": "Invest in pre-IPO companies through single-company funds. Access to 400+ private companies including AI, fintech, biotech."},
    {"name": "Linqto", "url": "linqto.com", "category": "pre_ipo",
     "focus": "Fractional pre-IPO shares at lower minimums", "min_investment": "$2,500",
     "accreditation": "accredited", "fees": "Built into share price", "liquidity": "Low-Medium",
     "description": "Buy fractional shares of pre-IPO unicorns with lower minimums than other platforms. Mobile-first experience."},
    {"name": "Hiive", "url": "hiive.com", "category": "pre_ipo",
     "focus": "Private share marketplace", "min_investment": "$10,000",
     "accreditation": "accredited", "fees": "Transaction-based", "liquidity": "Low",
     "description": "Marketplace connecting buyers and sellers of private company shares. Transparent pricing and broader selection."},
    {"name": "EquityBee", "url": "equitybee.com", "category": "pre_ipo",
     "focus": "Fund employee stock options in private companies", "min_investment": "$10,000",
     "accreditation": "accredited", "fees": "Profit share agreement", "liquidity": "Low",
     "description": "Fund employees' stock options in exchange for a share of future proceeds. Unique access path to private companies."},
    {"name": "MicroVentures", "url": "microventures.com", "category": "pre_ipo",
     "focus": "Pre-IPO and early-stage investments", "min_investment": "$1,000",
     "accreditation": "varies", "fees": "Varies by deal", "liquidity": "Very Low",
     "description": "Access both early-stage startups and pre-IPO companies. Some deals open to non-accredited investors."},
    # ── Equity Crowdfunding / Startup Platforms ──
    {"name": "Republic", "url": "republic.com", "category": "startup",
     "focus": "Equity crowdfunding for startups, crypto, real estate, and gaming", "min_investment": "$50",
     "accreditation": "none", "fees": "2% processing fee", "liquidity": "Very Low",
     "description": "Invest as little as $50 in vetted startups. Open to everyone. Covers tech, gaming, crypto, climate, and real estate."},
    {"name": "Wefunder", "url": "wefunder.com", "category": "startup",
     "focus": "Community-driven startup investing", "min_investment": "$100",
     "accreditation": "none", "fees": "2% fee at exit", "liquidity": "Very Low",
     "description": "The largest Reg CF crowdfunding platform. Invest in early-stage companies alongside lead investors. 2,000+ companies funded."},
    {"name": "StartEngine", "url": "startengine.com", "category": "startup",
     "focus": "Reg A+ and Reg CF startup offerings", "min_investment": "$100",
     "accreditation": "none", "fees": "Varies by issuer", "liquidity": "Low (secondary market available)",
     "description": "Invest in startups through Reg A+ and Reg CF offerings. Has a secondary market for trading startup shares."},
    {"name": "AngelList", "url": "angellist.com", "category": "startup",
     "focus": "Venture fund access and syndicate investing", "min_investment": "$1,000",
     "accreditation": "accredited", "fees": "Carry (typically 20%)", "liquidity": "Very Low",
     "description": "Invest alongside top angel investors through syndicates and rolling funds. Access to curated deal flow from experienced VCs."},
    {"name": "SeedInvest", "url": "seedinvest.com", "category": "startup",
     "focus": "Curated startup investment opportunities", "min_investment": "$500",
     "accreditation": "varies", "fees": "2% placement fee", "liquidity": "Very Low",
     "description": "Highly curated startup deals (only ~1% of applicants accepted). Focus on quality over quantity."},
    {"name": "FundersClub", "url": "fundersclub.com", "category": "startup",
     "focus": "Online VC fund investing in top startups", "min_investment": "$1,000",
     "accreditation": "accredited", "fees": "Management + carry", "liquidity": "Very Low",
     "description": "Access to high-quality startup deals through an SEC-registered online venture capital fund."},
    {"name": "Mainvest", "url": "mainvest.com", "category": "startup",
     "focus": "Invest in local small businesses", "min_investment": "$100",
     "accreditation": "none", "fees": "No investor fees", "liquidity": "Low",
     "description": "Revenue-sharing investments in local brick-and-mortar businesses. Earn returns as businesses generate revenue."},
    # ── VC Fund Access ──
    {"name": "Titan", "url": "titan.com", "category": "vc_fund",
     "focus": "Managed venture capital and private equity portfolios", "min_investment": "$500",
     "accreditation": "none", "fees": "1% annual advisory fee", "liquidity": "Low-Medium",
     "description": "Access VC-style returns through professionally managed portfolios including venture capital and private credit strategies."},
    {"name": "Destiny Tech100 (DXYZ)", "url": "destinyfunds.com", "category": "vc_fund",
     "focus": "Publicly traded fund holding top private companies", "min_investment": "1 share (~$10-50)",
     "accreditation": "none", "fees": "2.5% management fee", "liquidity": "High (NYSE listed)",
     "description": "Buy shares on NYSE to get exposure to SpaceX, Stripe, OpenAI, Discord, and other top private companies."},
    {"name": "ARK Venture Fund (ARKVX)", "url": "ark-ventures.com", "category": "vc_fund",
     "focus": "Disruptive innovation private + public blend", "min_investment": "$500",
     "accreditation": "none", "fees": "2.75% management + incentive", "liquidity": "Low (quarterly redemptions)",
     "description": "Cathie Wood's venture fund investing in disruptive private companies alongside public equities. Open to all investors."},
]

STARTUP_GUIDE = {
    "getting_started": [
        {"step": 1, "title": "Understand the Risk",
         "description": "Startup investing is high-risk, high-reward. 90% of startups fail, but winners can return 10-100x. Never invest more than 5-10% of your portfolio in startups.",
         "tips": ["Start small ($50-$500 per deal)", "Diversify across 10-20+ startups", "Only invest money you can afford to lose", "Expect 5-10 year hold periods"]},
        {"step": 2, "title": "Choose Your Investor Type",
         "description": "Your access depends on your accreditation status and how much you can invest.",
         "tips": ["Non-accredited: Republic, Wefunder, StartEngine ($50-$1K minimums)", "Accredited ($200K+ income or $1M+ net worth): AngelList, FundersClub, Forge, EquityZen", "Want diversification? Try VC funds like ARK Venture, Destiny Tech100, or Titan"]},
        {"step": 3, "title": "Pick Your Platforms",
         "description": "Sign up for 2-3 platforms to get regular deal flow. Each platform has different strengths.",
         "tips": ["Republic + Wefunder for broad startup access (open to all)", "AngelList for syndicate deals (accredited)", "Forge/Linqto for pre-IPO unicorns (accredited)", "StartEngine for Reg A+ offerings with secondary market"]},
        {"step": 4, "title": "Evaluate Deals",
         "description": "Look for strong founding teams, large addressable markets, clear revenue models, and traction metrics.",
         "tips": ["Check team background (LinkedIn, prior exits)", "Look for revenue growth, not just user growth", "Read the offering documents (risks section)", "Check valuation vs. comparable companies", "Look for lead investors with track records"]},
        {"step": 5, "title": "Build Your Portfolio",
         "description": "Diversify across sectors, stages, and platforms. Track your investments and follow company updates.",
         "tips": ["Aim for 15-25 startup investments over time", "Mix early-stage (higher risk/reward) with late-stage (lower risk)", "Set a quarterly investment budget", "Follow portfolio companies on social media for updates"]},
    ],
    "connecting_with_startups": [
        {"channel": "Equity Crowdfunding Platforms", "icon": "globe",
         "description": "Republic, Wefunder, and StartEngine let you invest directly in startups and often include founder Q&A sessions, investor updates, and community forums.",
         "action": "Sign up, browse live campaigns, ask founders questions before investing"},
        {"channel": "AngelList & Syndicates", "icon": "users",
         "description": "Follow experienced angels who share deal flow. Syndicates pool capital to invest alongside lead investors who do the diligence.",
         "action": "Create an AngelList account, follow syndicates in your sectors of interest"},
        {"channel": "Startup Events & Demo Days", "icon": "calendar",
         "description": "Y Combinator Demo Day, TechCrunch Disrupt, Web Summit, and local startup meetups are prime networking opportunities.",
         "action": "Attend YC Demo Day (virtual), TechCrunch events, local startup weekends"},
        {"channel": "LinkedIn & Twitter/X", "icon": "social",
         "description": "Follow founders, VCs, and startup ecosystem leaders. Many founders post fundraising updates and product launches.",
         "action": "Follow VCs (a16z, Sequoia, Y Combinator) and founders in sectors you track"},
        {"channel": "Accelerator Programs", "icon": "rocket",
         "description": "Y Combinator, Techstars, 500 Global, and other accelerators showcase their portfolio companies to investors.",
         "action": "Sign up for accelerator newsletters and demo day invites"},
        {"channel": "Local Angel Groups", "icon": "pin",
         "description": "Angel investor networks like Golden Seeds, Tech Coast Angels, and New York Angels provide vetted deal flow and co-investment opportunities.",
         "action": "Search for angel groups in your city/region and apply for membership"},
        {"channel": "Venture Scout Programs", "icon": "search",
         "description": "Some VC firms hire venture scouts who source deals. This gives you early access to startups and potential carry.",
         "action": "Apply to scout programs at firms like Sequoia Scout, First Round Angel Track"},
        {"channel": "Crunchbase & PitchBook", "icon": "database",
         "description": "Research startup funding history, investors, and growth metrics before investing. Track companies you're interested in.",
         "action": "Set up Crunchbase alerts for sectors and companies you follow"},
    ],
}


@bp.route("/api/ipos")
@login_required
@llm_rate_limit(call_source="ipo_scanner", call_count=1)
def api_ipos():
    """3-stream parallel scan (Reddit + X + Substack), cross-reference, then Sonnet vets."""
    from ai_validator import _call_openrouter, _parse_json_response, LLM_RESEARCH
    import concurrent.futures

    uid = _uid()
    if uid:
        set_llm_user(uid, "ipo_scanner")

    # ── Step 1: Parallel 3-stream social scrape ───────────────────
    scrape_data = {"streams": {}, "flat": [], "cross_referenced": [], "counts": {}}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_scrape_ipo_intel)
            scrape_data = future.result(timeout=45)
    except Exception as e:
        logger.warning(f"IPO social scrape failed: {e}")

    streams = scrape_data.get("streams", {})
    flat_posts = scrape_data.get("flat", [])
    cross_ref = scrape_data.get("cross_referenced", [])
    counts = scrape_data.get("counts", {})
    total_posts = sum(counts.values())

    logger.info("IPO scrape: reddit=%d, x=%d, substack=%d, news=%d, cross_ref=%d",
                counts.get("reddit", 0), counts.get("x", 0),
                counts.get("substack", 0), counts.get("news", 0), len(cross_ref))

    # ── Step 2: Build per-stream context ──────────────────────────
    social_context = ""
    if total_posts > 0:
        social_context = "\n\n## LIVE SOCIAL INTELLIGENCE (3 parallel streams scraped just now)\n\n"

        for ch_name, ch_label in [("reddit", "Reddit"), ("x", "X / Twitter"), ("substack", "Substack & Newsletters"), ("news", "TechCrunch / Crunchbase / PitchBook")]:
            ch_posts = streams.get(ch_name, [])
            if ch_posts:
                social_context += f"### {ch_label} ({len(ch_posts)} posts):\n"
                for i, post in enumerate(ch_posts[:20], 1):
                    social_context += f"{i}. [{post['source']}] (eng:{post['engagement']}) \"{post['title']}\"\n"
                    if post["text"]:
                        social_context += f"   {post['text'][:120]}\n"
                social_context += "\n"

        if cross_ref:
            social_context += f"\n### CROSS-REFERENCED (mentioned on 2+ platforms): {', '.join(cross_ref[:20])}\n"
        social_context += f"\nTotal: {total_posts} posts across {len([c for c in counts.values() if c > 0])} channels\n"
    else:
        social_context = "\n\n## NOTE: Social scrape returned no results. Use your training knowledge of current IPO filings, Crunchbase funding data, and known VC deals.\n"

    platform_names = ", ".join(p["name"] for p in IPO_VC_PLATFORMS)

    # ── Step 3: Sonnet vetting call ───────────────────────────────
    # Use LLM_RESEARCH (Claude Sonnet) for higher quality vetting
    discovery_prompt = build_ipo_discovery_prompt(social_context, platform_names)

    def _extract_ipos_from_truncated(raw):
        """Salvage partial IPO entries from truncated JSON."""
        import re
        if not raw:
            return []
        entries = []
        pattern = r'\{[^{}]*"company_name"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        for m in re.findall(pattern, raw, re.DOTALL):
            try:
                obj = json.loads(m)
                if obj.get("company_name"):
                    entries.append(obj)
            except (json.JSONDecodeError, ValueError):
                continue
        return entries

    def _normalize_ipos(raw_ipos):
        """Normalize and filter IPO entries."""
        filtered = []
        for ipo in raw_ipos:
            ot = str(ipo.get("opportunity_type", "ipo")).lower().strip().replace("-", "_").replace(" ", "_")
            if ot in ("vc", "vc_deal", "vc_backed", "venture", "venture_capital"):
                ot = "vc"
            elif ot in ("pre_ipo", "preipo", "pre_ipo_access", "secondary"):
                ot = "pre_ipo"
            elif ot in ("startup", "startups", "crowdfund", "early_stage", "seed"):
                ot = "startup"
            else:
                ot = "ipo"
            ipo["opportunity_type"] = ot
            rating = ipo.get("overall_rating", 0)
            buzz = ipo.get("social_buzz", 0)
            if rating >= 2 or buzz >= 3:
                for key in ["social_buzz", "institutional_interest", "market_fit", "moat", "risk_level", "overall_rating"]:
                    val = ipo.get(key, 3)
                    ipo[key] = max(1, min(5, int(val) if isinstance(val, (int, float)) else 3))
                filtered.append(ipo)
        filtered.sort(key=lambda x: (x.get("overall_rating", 0), x.get("social_buzz", 0)), reverse=True)
        return filtered

    sources_list = list(set(
        p["source"].split(":")[0] for p in flat_posts
    )) if flat_posts else []

    try:
        logger.info("IPO scan: calling Sonnet (model=%s, prompt_len=%d)", LLM_RESEARCH, len(discovery_prompt))
        response = _call_openrouter(
            LLM_RESEARCH,  # Claude Sonnet for quality vetting
            [{"role": "user", "content": discovery_prompt}],
            temperature=0.3,
            max_tokens=10000,
            timeout=150,
        )
        logger.info("IPO scan: Sonnet responded, length=%d", len(response) if response else 0)

        if not response:
            raise ValueError("Empty LLM response")

        parsed = _parse_json_response(response, "ipo_scanner")
        raw_ipos = parsed.get("ipos", [])

        # Fallback: salvage from truncated
        if parsed.get("error") or not raw_ipos:
            logger.warning("IPO scan: parse issue, salvaging truncated entries")
            raw_ipos = _extract_ipos_from_truncated(response)
            logger.info("IPO scan: salvaged %d entries", len(raw_ipos))

        if not raw_ipos and isinstance(parsed, list):
            raw_ipos = parsed

        # Retry with simpler prompt if nothing
        if not raw_ipos:
            logger.info("IPO scan: retrying with simplified prompt")
            retry_prompt = build_ipo_retry_prompt()
            retry_resp = _call_openrouter(
                LLM_RESEARCH, [{"role": "user", "content": retry_prompt}],
                temperature=0.3, max_tokens=6000, timeout=90,
            )
            retry_parsed = _parse_json_response(retry_resp, "ipo_retry")
            raw_ipos = retry_parsed.get("ipos", [])
            if not raw_ipos:
                raw_ipos = _extract_ipos_from_truncated(retry_resp or "")
            logger.info("IPO retry: got %d entries", len(raw_ipos))

        filtered = _normalize_ipos(raw_ipos)

        if not filtered:
            logger.warning("IPO scan: 0 entries after filtering")
            return jsonify({
                "ipos": [], "count": 0, "source": "fallback",
                "social_posts_found": total_posts, "social_sources": sources_list,
                "stream_counts": counts, "cross_referenced": cross_ref,
                "platforms": IPO_VC_PLATFORMS, "startup_guide": STARTUP_GUIDE,
                "fallback_reason": "AI returned no valid entries — browse platforms and startup guide below",
            }), 200

        logger.info("IPO scan: returning %d opportunities", len(filtered))
        return jsonify({
            "ipos": filtered,
            "count": len(filtered),
            "source": "reddit_x_substack_sonnet",
            "social_posts_found": total_posts,
            "social_sources": sources_list,
            "stream_counts": counts,
            "cross_referenced": cross_ref,
            "platforms": IPO_VC_PLATFORMS,
            "startup_guide": STARTUP_GUIDE,
        })

    except Exception as e:
        logger.error(f"IPO scan failed: {e}", exc_info=True)
        return jsonify({
            "ipos": [], "count": 0, "source": "fallback",
            "social_posts_found": total_posts, "social_sources": sources_list,
            "stream_counts": counts, "cross_referenced": cross_ref,
            "platforms": IPO_VC_PLATFORMS, "startup_guide": STARTUP_GUIDE,
            "fallback_reason": "AI scan encountered an error — browse platforms and startup guide below",
        }), 200


@bp.route("/api/ipo-platforms")
@login_required
def api_ipo_platforms():
    """Return curated platforms + startup guide (no LLM, instant)."""
    return jsonify({
        "platforms": IPO_VC_PLATFORMS,
        "startup_guide": STARTUP_GUIDE,
    })
