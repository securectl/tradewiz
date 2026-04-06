"""
Documentation Blueprint — GitBooks-style docs served at /docs.
"""

from flask import Blueprint, render_template, abort

docs_bp = Blueprint(
    "docs",
    __name__,
    url_prefix="/docs",
    template_folder="templates",
)

# ── Sidebar navigation structure ──────────────────────────────────────────
# Each section has a title and a list of (slug, label) pages.
# The slug maps to templates/docs/pages/<slug>.html

NAV_SECTIONS = [
    {
        "title": "Overview",
        "pages": [
            ("index", "Welcome"),
            ("getting-started", "Getting Started"),
        ],
    },
    {
        "title": "Configuration",
        "pages": [
            ("configuration", "Configuration"),
            ("authentication", "Authentication"),
        ],
    },
    {
        "title": "Features",
        "pages": [
            ("analyzer", "Analyzer"),
            ("stock-analysis", "Stock Analysis"),
            ("ai-validation", "AI Validation"),
            ("screener", "Screener & Scanner"),
            ("tracker", "Trade Tracker"),
        ],
    },
    {
        "title": "Trading Bots",
        "pages": [
            ("crypto-bot", "Crypto Trading Bot"),
            ("stock-bot", "Stock Trading Bot"),
        ],
    },
    {
        "title": "Reference",
        "pages": [
            ("admin-guide", "Admin Guide"),
            ("api-reference", "API Reference"),
            ("deployment", "Deployment"),
        ],
    },
]

# Flat list for prev/next navigation
ALL_PAGES = []
for section in NAV_SECTIONS:
    for slug, label in section["pages"]:
        ALL_PAGES.append((slug, label))

VALID_SLUGS = {slug for slug, _ in ALL_PAGES}


def _page_context(slug):
    """Build template context: nav, current page, prev/next links."""
    idx = next(i for i, (s, _) in enumerate(ALL_PAGES) if s == slug)
    prev_page = ALL_PAGES[idx - 1] if idx > 0 else None
    next_page = ALL_PAGES[idx + 1] if idx < len(ALL_PAGES) - 1 else None
    title = ALL_PAGES[idx][1]

    return dict(
        nav_sections=NAV_SECTIONS,
        current_slug=slug,
        page_title=title,
        prev_page=prev_page,
        next_page=next_page,
    )


@docs_bp.route("/")
def index():
    return render_template("docs/pages/index.html", **_page_context("index"))


@docs_bp.route("/<path:page>")
def page(page):
    slug = page.rstrip("/")
    if slug not in VALID_SLUGS:
        abort(404)
    return render_template(f"docs/pages/{slug}.html", **_page_context(slug))
