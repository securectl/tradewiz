"""Release notes feed for the in-app "What's New" panel.

Newest release first. On each ship, prepend an entry here — the frontend badge
keys off the top version, so users see a dot until they open the panel.

Each release: {version, date (YYYY-MM-DD), title, items:[{type, text}]}
where type is one of: "new", "improved", "fix".
"""

RELEASES = [
    {
        "version": "2.4.0",
        "date": "2026-05-28",
        "title": "Sector Radar — Auto Research Analyst",
        "items": [
            {"type": "new", "text": "Daily AI analyst that ranks every sector and calls the next 6–12 month leader, with conviction, catalysts, and leading tickers."},
            {"type": "new", "text": "Sector leaderboard scoring relative strength, breakout structure, and smart-money volume in one view."},
            {"type": "improved", "text": "The Research tab now opens straight to your daily market read."},
        ],
    },
    {
        "version": "2.3.0",
        "date": "2026-05-27",
        "title": "ThunderBot — smarter exits",
        "items": [
            {"type": "improved", "text": "Trailing stops let winners run toward the 4–7% target instead of bailing near 1%."},
            {"type": "fix", "text": "Hard catastrophic-loss floor caps tail losses, and nothing carries overnight."},
            {"type": "improved", "text": "Capital now concentrates into the highest-conviction setups."},
        ],
    },
    {
        "version": "2.2.0",
        "date": "2026-05-24",
        "title": "Screener & mobile",
        "items": [
            {"type": "new", "text": "Export any screener category to CSV / TXT / PDF with date ranges."},
            {"type": "improved", "text": "Full mobile sweep — every tab now fits iPhone & Android viewports."},
        ],
    },
]

_VALID_TYPES = {"new", "improved", "fix"}


def get_releases():
    """Return the release list, newest first."""
    return RELEASES


def latest_version():
    """Version string of the newest release (or '' if none)."""
    return RELEASES[0]["version"] if RELEASES else ""
