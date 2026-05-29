"""
Broad "is anything too wide?" sweep across the SPA.

Why this exists, separate from the per-tab overflow check:
The per-tab tests in test_mobile_tabs.py ask the binary question "does the
document scroll horizontally?". That catches the worst case but doesn't tell
you WHICH element is the culprit, and it doesn't catch elements that get
clipped by an `overflow: hidden` ancestor (the parent doesn't scroll, but the
child is still cut off — which is exactly what the user reported as
"view is cutting" on Android/iPhone).

This sweep walks every visible element on every tab and reports any whose
right edge falls outside the viewport, sorted by how badly they overflow.
The output makes the actual offending selectors easy to find and fix.
"""
import pytest

from tests.mobile.conftest import find_overflowing_elements


# Elements that are SUPPOSED to scroll inside themselves don't count as bugs.
# Add selectors here as we discover them. Example: a carousel with
# overflow-x:auto can be wider than the viewport — that's the design.
ALLOWED_INNER_SCROLLERS = [
    # Past-scans table is intentionally wide & scrollable inside its container.
    "#screener-history-table",
    "#screener-trending-panel",
    # Reuse the same exception for any element marked with a class signal.
    ".allow-x-scroll",
]


# The tabs we sweep — must match the keys in _PANEL_SELECTOR in test_mobile_tabs.
SWEEP_TABS = ["analyzer", "qullamaggie", "tracker", "screener", "research", "finskills", "ipos"]


def _switch(page, tab):
    """Inline copy of _switch_tab so this test file is self-contained."""
    from tests.mobile.test_mobile_tabs import _PANEL_SELECTOR
    selector = _PANEL_SELECTOR.get(tab, f"#{tab}-content")
    page.evaluate(f"switchTab({tab!r})")
    page.wait_for_function(
        f"() => {{ const el = document.querySelector({selector!r}); "
        "return el && getComputedStyle(el).display !== 'none'; }",
        timeout=4000,
    )
    page.wait_for_timeout(250)


def _format_overflow_report(tab, viewport_w, overflows):
    """Render the offending elements as a readable assertion message."""
    lines = [f"Tab `{tab}` overflows viewport ({viewport_w}px) — {len(overflows)} offending element(s):"]
    for o in overflows[:10]:  # cap so failure msg stays scannable
        lines.append(
            f"  • +{o['overflow']}px → {o['selector']:32s}  "
            f"width={o['width']}px text={o['text']!r}"
        )
    if len(overflows) > 10:
        lines.append(f"  … and {len(overflows) - 10} more (truncated)")
    return "\n".join(lines)


@pytest.mark.parametrize("tab", SWEEP_TABS)
def test_tab_has_no_overflowing_elements(authed_mobile_page, tab):
    """Every element on the tab must fit horizontally within the viewport."""
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    authed_mobile_page.wait_for_timeout(300)
    _switch(authed_mobile_page, tab)
    overflows = find_overflowing_elements(
        authed_mobile_page,
        tolerance_px=4,
        ignore_selectors=ALLOWED_INNER_SCROLLERS,
    )
    vp = authed_mobile_page.viewport_size["width"]
    assert not overflows, _format_overflow_report(tab, vp, overflows)


def test_login_page_no_overflowing_elements(mobile_page):
    """Same sweep, but for the unauth login surface."""
    mobile_page.goto("/auth/login")
    mobile_page.wait_for_load_state("domcontentloaded")
    overflows = find_overflowing_elements(
        mobile_page,
        tolerance_px=4,
        ignore_selectors=ALLOWED_INNER_SCROLLERS,
    )
    vp = mobile_page.viewport_size["width"]
    assert not overflows, _format_overflow_report("login", vp, overflows)


# ── Audit-only test: prints the worst overflow per tab without failing. ─
# Useful when you want to map out all the issues in one report before
# deciding how to fix them. Run with: pytest -s tests/mobile/test_mobile_overflow_sweep.py::test_audit_overflow_report
def test_audit_overflow_report(authed_mobile_page, capsys):
    """Print every overflow on every tab in a single report. Always passes."""
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    authed_mobile_page.wait_for_timeout(300)
    vp = authed_mobile_page.viewport_size["width"]
    profile = getattr(authed_mobile_page, "_android_profile", "unknown")
    lines = [f"\n═══ Overflow audit: profile={profile} viewport={vp}px ═══"]
    for tab in SWEEP_TABS:
        try:
            _switch(authed_mobile_page, tab)
            overflows = find_overflowing_elements(
                authed_mobile_page,
                tolerance_px=4,
                ignore_selectors=ALLOWED_INNER_SCROLLERS,
            )
        except Exception as e:
            lines.append(f"  [{tab:15s}] switch failed: {e}")
            continue
        if not overflows:
            lines.append(f"  [{tab:15s}] OK — no overflow")
            continue
        lines.append(f"  [{tab:15s}] {len(overflows)} offender(s):")
        for o in overflows[:5]:
            lines.append(f"      +{o['overflow']:>4d}px  {o['selector']:30s}  "
                         f"w={o['width']}  text={o['text']!r}")
    print("\n".join(lines))
