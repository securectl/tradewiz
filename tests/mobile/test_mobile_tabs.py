"""
Per-tab mobile rendering tests.

The SPA exposes its primary navigation through `data-tab=...` buttons that
call switchTab(). For each major tab we:
  1. Click the tab via its JS hook (faster than clicking the DOM button which
     may be hidden behind a role gate).
  2. Wait for its `*-content` panel to flip to display !== 'none'.
  3. Assert the document never starts horizontally scrolling.
  4. Assert tab-specific UI didn't blow out of the viewport.

Tabs gated behind admin/trader/pro roles are skipped when not visible to the
test user. We seed an ordinary user (no admin role) in conftest so role-gated
tabs are intentionally out of scope here; if you want to cover them, grant
the seeded user the relevant role and re-run.
"""
import pytest

from tests.mobile.conftest import assert_no_horizontal_scroll


# The tab→panel-id mapping is not uniform in this SPA — most tabs are
# `<tab>-content` but a few are special (e.g. analyzer = .main-content,
# ipos = #ipo-content singular, smart-money has a hyphen).
_PANEL_SELECTOR = {
    "analyzer": ".main-content",
    "qullamaggie": "#qullamaggie-content",
    "tracker": "#tracker-content",
    "screener": "#screener-content",
    "research": "#research-content",
    "finskills": "#finskills-content",
    "ipos": "#ipo-content",          # NB: tab is "ipos", panel is singular
    "predictions": "#predictions-content",
    "congress": "#congress-content",
    "smart-money": "#smart-money-content",
    "trump": "#trump-content",
    "watchdog": "#watchdog-content",
    "claude-bot": "#claude-bot-content",
    "autotrading": "#autotrading-content",
    "stocktrading": "#stocktrading-content",
    "admin": "#admin-content",
    "status": "#status-content",
}


def _switch_tab(page, tab):
    """Use the SPA's own router to switch tabs and wait for the panel to show."""
    selector = _PANEL_SELECTOR.get(tab, f"#{tab}-content")
    page.evaluate(f"switchTab({tab!r})")
    page.wait_for_function(
        f"() => {{ const el = document.querySelector({selector!r}); "
        "return el && getComputedStyle(el).display !== 'none'; }",
        timeout=4000,
    )
    page.wait_for_timeout(200)  # let layout settle


# Tabs that an ordinary (non-trader, non-admin) signed-in user can see.
PUBLIC_TABS = ["analyzer", "qullamaggie", "tracker", "screener", "research", "finskills", "ipos"]


@pytest.mark.parametrize("tab", PUBLIC_TABS)
def test_each_public_tab_no_horizontal_scroll(authed_mobile_page, tab):
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    authed_mobile_page.wait_for_timeout(300)
    _switch_tab(authed_mobile_page, tab)
    assert_no_horizontal_scroll(authed_mobile_page)


# ── Screener-specific tests ────────────────────────────────────────────
def test_screener_category_bar_fits_within_viewport(authed_mobile_page):
    """The category bar may scroll horizontally inside itself (mobile.css does
    this intentionally so the 10 buttons stay on one row + swipeable), but the
    bar's *visible* width must not exceed the viewport — otherwise it would push
    the whole page wide."""
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    _switch_tab(authed_mobile_page, "screener")
    info = authed_mobile_page.evaluate("""
        () => {
            const bar = document.querySelector('.screener-category-bar');
            if (!bar) return null;
            const r = bar.getBoundingClientRect();
            return {
                client: bar.clientWidth, scroll: bar.scrollWidth,
                rectWidth: Math.round(r.width),
                overflowX: getComputedStyle(bar).overflowX,
            };
        }
    """)
    assert info, "Screener category bar not found"
    vp = authed_mobile_page.viewport_size["width"]
    assert info["rectWidth"] <= vp + 1, (
        f"Screener category bar visible width {info['rectWidth']}px exceeds viewport {vp}px"
    )
    # If the bar isn't a horizontal scroller and its content still overflows,
    # that's the real bug — wrap or scroll, never silent clip.
    if info["overflowX"] not in ("auto", "scroll"):
        assert info["scroll"] <= info["client"] + 1, (
            f"Screener bar has no overflow-x scroll but content overflows by "
            f"{info['scroll'] - info['client']}px — flex-wrap broken"
        )


def test_screener_export_bar_fits_viewport(authed_mobile_page):
    """The export bar (date inputs + 3 buttons) must not push the page wide."""
    authed_mobile_page.goto("/")
    _switch_tab(authed_mobile_page, "screener")
    info = authed_mobile_page.evaluate("""
        () => {
            const bar = document.getElementById('screener-export-bar');
            if (!bar) return null;
            const r = bar.getBoundingClientRect();
            return { width: r.width, viewport: window.innerWidth };
        }
    """)
    assert info, "Export bar not found in screener tab"
    assert info["width"] <= info["viewport"] + 1, (
        f"Export bar width {info['width']}px exceeds viewport {info['viewport']}px"
    )


def test_screener_export_buttons_are_tappable(authed_mobile_page):
    """CSV/TXT/PDF buttons should clear a 36px tap target."""
    authed_mobile_page.goto("/")
    _switch_tab(authed_mobile_page, "screener")
    too_small = authed_mobile_page.evaluate("""
        () => {
            const out = [];
            for (const b of document.querySelectorAll('#screener-export-bar button')) {
                const r = b.getBoundingClientRect();
                if (r.height < 36 || r.width < 36) {
                    out.push({ text: b.innerText, h: Math.round(r.height), w: Math.round(r.width) });
                }
            }
            return out;
        }
    """)
    assert not too_small, f"Screener export buttons too small: {too_small}"


# ── Analyzer tests ─────────────────────────────────────────────────────
def test_analyzer_ticker_input_visible(authed_mobile_page):
    authed_mobile_page.goto("/")
    _switch_tab(authed_mobile_page, "analyzer")
    visible = authed_mobile_page.evaluate("""
        () => {
            const el = document.getElementById('ticker-input');
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
    """)
    assert visible, "Analyzer ticker input not visible on mobile"


def test_analyzer_input_meets_min_tap_size(authed_mobile_page):
    authed_mobile_page.goto("/")
    _switch_tab(authed_mobile_page, "analyzer")
    h = authed_mobile_page.evaluate(
        "() => { const el = document.getElementById('ticker-input'); "
        "return el ? Math.round(el.getBoundingClientRect().height) : 0; }"
    )
    assert h >= 36, f"Analyzer ticker input height {h}px is below 36px tap floor"


# ── Bot dashboard tests (admin/trader-gated, so we skip if not present) ─
@pytest.mark.parametrize("tab", ["autotrading", "stocktrading", "claude-bot", "watchdog", "admin"])
def test_gated_tab_skipped_when_hidden(authed_mobile_page, tab):
    """Sanity: role-gated tabs should be hidden for a non-trader user; if they're
    visible, run the same horizontal-scroll assertion on them too."""
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    visible = authed_mobile_page.evaluate(
        f"() => {{ const b = document.querySelector('.tab-btn[data-tab=\"{tab}\"]'); "
        "if (!b) return false; const s = getComputedStyle(b); "
        "return s.display !== 'none' && s.visibility !== 'hidden'; }"
    )
    if not visible:
        pytest.skip(f"{tab} tab is role-gated and not visible to this test user")
    _switch_tab(authed_mobile_page, tab)
    assert_no_horizontal_scroll(authed_mobile_page)
