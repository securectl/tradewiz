# Mobile / Android Playwright Tests

End-to-end tests that boot the real Flask app, drive it in a real headless
Chromium at Android viewport sizes, and assert the layout doesn't break.
Lives outside the production image — only run locally or in CI.

## What they cover

Three Android viewport profiles are tested by default:

| Profile        | Width × Height | Notes                                     |
|----------------|----------------|-------------------------------------------|
| `pixel_5`      | 393 × 851      | Modern phone, ~60% of Android traffic     |
| `galaxy_s9`    | 360 × 740      | Older flagship, very common viewport      |
| `small_android`| 320 × 568      | Edge case (small / older / split-screen)  |

Every test runs once per profile via `@pytest.fixture(params=...)`.

### Assertions

- `assert_no_horizontal_scroll(page)` — document scrollWidth must not exceed
  the viewport (this is the #1 cause of "looks bad on Android" complaints).
- `assert_viewport_meta(page)` — `<meta name=viewport>` exists and includes
  `width=device-width`. Without it, Android Chrome uses 980 CSS px and the
  whole UI looks miniaturized.
- `collect_tap_targets_too_small(page, selector, min_px)` — returns elements
  shorter or narrower than `min_px`. Default floor: **40 px** (lower than
  Apple's 44 / Material's 48 to avoid false positives on utility chips).

### Files

| File                       | Scope                                                |
|----------------------------|------------------------------------------------------|
| `conftest.py`              | Fixtures + live server + login cookie injection      |
| `test_mobile_shell.py`     | Login page, SPA root, viewport meta, tab bar         |
| `test_mobile_tabs.py`      | Analyzer / Screener / Tracker / etc. per-tab checks  |

## One-time setup

```bash
pip install -r requirements-test.txt
python -m playwright install chromium
```

The first `playwright install` downloads ~170 MB of browser binaries. You
only need to do it once per machine. Don't add this to the Docker image —
that's the whole reason this lives in its own requirements file.

## Run

```bash
# All mobile tests (≈30 across 3 viewports = ~90 test runs)
python -m pytest tests/mobile/ -v

# Just one file
python -m pytest tests/mobile/test_mobile_shell.py -v

# Just one viewport — filter by the parametrize ID
python -m pytest tests/mobile/ -v -k pixel_5

# See a failed page (open Playwright trace)
python -m pytest tests/mobile/ -v --tracing on
```

## Adding a new test

```python
def test_my_thing_doesnt_overflow(authed_mobile_page):
    authed_mobile_page.goto("/")
    authed_mobile_page.wait_for_load_state("domcontentloaded")
    # navigate into the feature you care about, then:
    from tests.mobile.conftest import assert_no_horizontal_scroll
    assert_no_horizontal_scroll(authed_mobile_page)
```

Two fixtures are available:

- `mobile_page` — anonymous browser context. Use this for `/auth/*` pages.
- `authed_mobile_page` — a Flask session cookie is injected before the page
  loads, so `/` and any `@login_required` route renders as the seeded test
  user.

Both fixtures are parametrized over `android_profile` automatically.

## What's NOT covered (yet)

- Role-gated tabs (admin, trader, pro). The seeded test user is an ordinary
  account; `test_gated_tab_skipped_when_hidden` skips them. If you want to
  cover bot dashboards, grant the test user the `trader` role in
  `_seed_test_user()` in `conftest.py`.
- Visual regression (pixel diff). Add `--update-snapshots` and the Playwright
  snapshot API later if needed.
- iOS Safari quirks. We test Android viewports only because the user
  reported Android issues; iOS would need a different user-agent set.
- Network-dependent flows (LLM calls, broker APIs). We set `OPENROUTER_API_KEY=""`
  in conftest so any LLM path short-circuits to the rule-based fallback.
