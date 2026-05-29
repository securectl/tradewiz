"""
Mobile shell + auth-page tests.

What we cover here:
- Every public surface (login page) renders without horizontal scroll on the
  three Android viewport profiles defined in conftest.
- The viewport meta tag is present (Android Chrome treats its absence as
  desktop = 980 CSS px, which makes the whole site look bad).
- Primary call-to-action buttons clear a 40 px tap-target floor.
"""
from tests.mobile.conftest import (
    assert_no_horizontal_scroll,
    assert_viewport_meta,
    collect_tap_targets_too_small,
)


def test_login_page_has_viewport_meta(mobile_page):
    mobile_page.goto("/auth/login")
    assert_viewport_meta(mobile_page)


def test_login_page_no_horizontal_scroll(mobile_page):
    mobile_page.goto("/auth/login")
    mobile_page.wait_for_load_state("domcontentloaded")
    assert_no_horizontal_scroll(mobile_page)


def test_login_page_primary_buttons_are_tappable(mobile_page):
    mobile_page.goto("/auth/login")
    mobile_page.wait_for_load_state("domcontentloaded")
    # Submit button on the email/password form is the critical tap target.
    bad = collect_tap_targets_too_small(mobile_page, "form button[type=submit]", min_px=40)
    assert not bad, f"Login submit button too small: {bad}"


def test_login_inputs_are_tappable(mobile_page):
    mobile_page.goto("/auth/login")
    mobile_page.wait_for_load_state("domcontentloaded")
    # 36px is the de facto floor for form inputs (less strict than the 44px CTA
    # button threshold — users approach inputs deliberately rather than tapping in flight).
    bad = collect_tap_targets_too_small(mobile_page, "form input[type=email], form input[type=password]", min_px=36)
    assert not bad, f"Login inputs too small (<36px): {bad}"


def test_spa_root_renders_without_horizontal_scroll(authed_mobile_page):
    """The full SPA at / must not horizontally overflow on Android."""
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    # Give CSS animations/font-loading a tick to settle so measurements aren't racy.
    authed_mobile_page.wait_for_timeout(300)
    assert_no_horizontal_scroll(authed_mobile_page)


def test_spa_root_has_viewport_meta(authed_mobile_page):
    authed_mobile_page.goto("/")
    assert_viewport_meta(authed_mobile_page)


def test_spa_tab_bar_visible_on_mobile(authed_mobile_page):
    """The .tab-btn row is how a mobile user navigates — at least one must be visible."""
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    authed_mobile_page.wait_for_timeout(300)
    visible = authed_mobile_page.evaluate("""
        () => {
            const btns = Array.from(document.querySelectorAll('.tab-btn'));
            return btns.filter(b => {
                const r = b.getBoundingClientRect();
                const s = getComputedStyle(b);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }).length;
        }
    """)
    assert visible >= 1, "No .tab-btn visible — mobile nav appears broken"
