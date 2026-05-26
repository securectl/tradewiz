"""
Shared fixtures for Playwright mobile tests.

The setup:
  1. We isolate test state by pointing the app at a tempfile SQLite DB
     (DATABASE_URL is intentionally NOT set, so app.py falls through to
     the SQLite branch in db.py).
  2. The Flask app is run in a daemon thread via werkzeug.serving.make_server
     so a real browser (Playwright) can hit a real URL.
  3. We seed one test user, log it in through Flask's test_client to capture
     the signed session cookie, then add that cookie to the Playwright
     browser context. All authed pages then load as that user.

Run: python -m pytest tests/mobile/ -v
Prereq: pip install -r requirements-test.txt && playwright install chromium
"""
import os
import socket
import sys
import tempfile
import threading
import time

import pytest

# Ensure repo root is on path before any app imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Force SQLite + a fixed dev secret BEFORE the app is imported.
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("SECRET_KEY", "mobile-tests-secret-key")
os.environ.setdefault("OPENROUTER_API_KEY", "")  # disable LLM calls

# Use a unique SQLite path per test session.
_TMP_DB = tempfile.NamedTemporaryFile(delete=False, suffix="_mobiletest.db")
_TMP_DB.close()
os.environ["SQLITE_DB_PATH_OVERRIDE"] = _TMP_DB.name  # documented, even if app ignores it


# ─── Common mobile viewport profiles ──────────────────────────────
# Covers the spread the user reports issues on: modern Android + older Android +
# small-screen edge cases + iPhone (Safari renders differently from Chrome).
ANDROID_VIEWPORTS = {
    "pixel_5": {"width": 393, "height": 851, "device_scale_factor": 2.75,
                "user_agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"},
    "galaxy_s9": {"width": 360, "height": 740, "device_scale_factor": 3.0,
                  "user_agent": "Mozilla/5.0 (Linux; Android 9; SM-G960F) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"},
    "small_android": {"width": 320, "height": 568, "device_scale_factor": 2.0,
                      "user_agent": "Mozilla/5.0 (Linux; Android 10; SM-J260F) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"},
    "iphone_se": {"width": 375, "height": 667, "device_scale_factor": 2.0,
                  "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                                "Mobile/15E148 Safari/604.1"},
    "iphone_13": {"width": 390, "height": 844, "device_scale_factor": 3.0,
                  "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                                "Mobile/15E148 Safari/604.1"},
}

TEST_EMAIL = "mobile-test@example.com"
TEST_PASSWORD = "mobile-test-password-123"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_test_user(app):
    """Insert one test user + an admin role so all gated pages render."""
    from werkzeug.security import generate_password_hash
    from db import execute, query_one, IS_POSTGRES
    P = "%s" if IS_POSTGRES else "?"
    with app.app_context():
        row = query_one(f"SELECT id FROM users WHERE email = {P}", (TEST_EMAIL,))
        if row:
            return row["id"]
        pw_hash = generate_password_hash(TEST_PASSWORD)
        if IS_POSTGRES:
            from db import get_db
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO users (email, name, password_hash) VALUES ({P},{P},{P}) RETURNING id",
                (TEST_EMAIL, "Mobile Tester", pw_hash),
            )
            uid = cur.fetchone()[0]
            cur.close()
        else:
            import uuid
            execute(
                f"INSERT INTO users (google_id, email, name, password_hash) VALUES ({P},{P},{P},{P})",
                (f"email:{uuid.uuid4().hex}", TEST_EMAIL, "Mobile Tester", pw_hash),
            )
            uid = query_one(f"SELECT id FROM users WHERE email = {P}", (TEST_EMAIL,))["id"]
        return uid


@pytest.fixture(scope="session")
def live_server():
    """Spin up the real Flask app on a free localhost port for Playwright."""
    from werkzeug.serving import make_server
    # Ensure DB is initialized first.
    import migrations
    try:
        migrations.run_migrations()
    except AttributeError:
        # Older API
        if hasattr(migrations, "init_db"):
            migrations.init_db()

    from app import app
    # Tests run over plain http://127.0.0.1, so disable the Secure flag that
    # production sets (Playwright rejects Secure cookies on non-HTTPS origins).
    app.config["SESSION_COOKIE_SECURE"] = False
    _seed_test_user(app)

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Wait for the server to actually accept connections.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.05)

    base_url = f"http://127.0.0.1:{port}"
    yield {"app": app, "base_url": base_url, "port": port}

    server.shutdown()


@pytest.fixture(scope="session")
def session_cookie(live_server):
    """Programmatically log in via test_client and return the session cookie tuple."""
    app = live_server["app"]
    client = app.test_client()

    # Fetch login page to get a CSRF token in the session.
    r = client.get("/auth/login")
    assert r.status_code == 200, f"Login page returned {r.status_code}"

    # Grab CSRF from the rendered form.
    import re
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.get_data(as_text=True))
    csrf = m.group(1) if m else ""

    r2 = client.post(
        "/auth/email-login",
        data={"email": TEST_EMAIL, "password": TEST_PASSWORD, "csrf_token": csrf},
        follow_redirects=False,
    )
    # Successful login → 302 to "/". If we get back 200 it means the
    # form re-rendered (CSRF/credential failure) — fail loudly.
    assert r2.status_code in (302, 303), f"Login POST returned {r2.status_code}"

    # Werkzeug 3 test client exposes cookies via get_cookie(name).
    cookie_obj = client.get_cookie("session")
    assert cookie_obj is not None, "No 'session' cookie set after login"

    return {"name": "session", "value": cookie_obj.value, "path": "/"}


# ─── Browser context factories ────────────────────────────────────
@pytest.fixture(params=list(ANDROID_VIEWPORTS.keys()))
def android_profile(request):
    """Yields each Android viewport profile name, one per parametrize pass."""
    return request.param


@pytest.fixture
def mobile_page(browser, live_server, android_profile):
    """Anonymous mobile page (login screen, public surface)."""
    vp = ANDROID_VIEWPORTS[android_profile]
    context = browser.new_context(
        viewport={"width": vp["width"], "height": vp["height"]},
        device_scale_factor=vp["device_scale_factor"],
        user_agent=vp["user_agent"],
        is_mobile=True,
        has_touch=True,
        base_url=live_server["base_url"],
    )
    page = context.new_page()
    page._android_profile = android_profile  # attach for asserts
    yield page
    context.close()


@pytest.fixture
def authed_mobile_page(browser, live_server, session_cookie, android_profile):
    """Logged-in mobile page (the full SPA)."""
    vp = ANDROID_VIEWPORTS[android_profile]
    context = browser.new_context(
        viewport={"width": vp["width"], "height": vp["height"]},
        device_scale_factor=vp["device_scale_factor"],
        user_agent=vp["user_agent"],
        is_mobile=True,
        has_touch=True,
        base_url=live_server["base_url"],
    )
    # Inject session cookie so the app sees us as logged-in.
    # Playwright wants EITHER (url) OR (domain + path) — pick the url form.
    context.add_cookies([{
        "name": session_cookie["name"],
        "value": session_cookie["value"],
        "url": live_server["base_url"],
    }])
    page = context.new_page()
    page._android_profile = android_profile
    yield page
    context.close()


# ─── Shared mobile assertion helpers ──────────────────────────────
def assert_no_horizontal_scroll(page, allow_overflow_px=2):
    """The body's scrollWidth should not exceed viewport width (tolerate sub-pixel rounding)."""
    measurements = page.evaluate("""
        () => ({
            docWidth: document.documentElement.scrollWidth,
            innerWidth: window.innerWidth,
            bodyOverflow: getComputedStyle(document.body).overflowX,
        })
    """)
    overflow = measurements["docWidth"] - measurements["innerWidth"]
    assert overflow <= allow_overflow_px, (
        f"Horizontal overflow: doc {measurements['docWidth']}px > viewport "
        f"{measurements['innerWidth']}px (over by {overflow}px). "
        f"body overflow-x={measurements['bodyOverflow']}"
    )


def assert_viewport_meta(page):
    content = page.evaluate("""
        () => {
            const m = document.querySelector('meta[name=viewport]');
            return m ? m.getAttribute('content') : null;
        }
    """)
    assert content, "Missing <meta name=viewport>"
    assert "width=device-width" in content, f"viewport meta lacks width=device-width: {content!r}"


def find_overflowing_elements(page, tolerance_px=4, ignore_selectors=None):
    """Walk every visible element and return those whose RIGHT edge exceeds the
    viewport — i.e. content that would force horizontal scroll or be clipped
    by overflow:hidden. Returns a sorted list of:
        {tag, id, cls, selector, overflow_px, width, x}
    `ignore_selectors` lets callers skip known-OK containers (e.g. inner
    scrollable carousels with overflow-x:auto that legitimately scroll).
    """
    ignore_selectors = ignore_selectors or []
    return page.evaluate(
        """
        ({tolerance, ignore}) => {
            const vp = window.innerWidth;
            const out = [];
            // Build a short, unique-ish selector path for diagnostic output.
            const selectorFor = (el) => {
                if (el.id) return '#' + el.id;
                let s = el.tagName.toLowerCase();
                if (el.classList && el.classList.length) {
                    s += '.' + Array.from(el.classList).slice(0, 2).join('.');
                }
                return s;
            };
            const ignoreSet = new Set(ignore);
            const matchesIgnore = (el) => {
                for (const sel of ignoreSet) {
                    try { if (el.matches(sel) || el.closest(sel)) return true; } catch (_) {}
                }
                return false;
            };
            for (const el of document.querySelectorAll('*')) {
                const s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                if (s.position === 'fixed') continue;       // fixed elements are tracked separately
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                const right = r.left + r.width;
                if (right > vp + tolerance) {
                    if (matchesIgnore(el)) continue;
                    out.push({
                        tag: el.tagName,
                        id: el.id || '',
                        cls: typeof el.className === 'string'
                            ? el.className.slice(0, 60) : '',
                        selector: selectorFor(el),
                        text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 40),
                        width: Math.round(r.width),
                        x: Math.round(r.left),
                        right: Math.round(right),
                        overflow: Math.round(right - vp),
                    });
                }
            }
            // Sort widest overflow first; cap to keep output readable.
            out.sort((a, b) => b.overflow - a.overflow);
            return out.slice(0, 30);
        }
        """,
        {"tolerance": tolerance_px, "ignore": ignore_selectors},
    )


def collect_tap_targets_too_small(page, selector, min_px=40):
    """Return a list of {tag, text, w, h} for elements smaller than min_px in either dimension.

    44px is Apple's HIG; 48px is Material; we settle on 40 as a soft floor so
    a few utility chips don't trip us. Callers can tighten.
    """
    return page.evaluate(
        """
        ({selector, minPx}) => {
            const out = [];
            for (const el of document.querySelectorAll(selector)) {
                const style = getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                if (r.width < minPx || r.height < minPx) {
                    out.push({
                        tag: el.tagName,
                        id: el.id || '',
                        cls: el.className || '',
                        text: (el.innerText || '').slice(0, 40),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                    });
                }
            }
            return out;
        }
        """,
        {"selector": selector, "minPx": min_px},
    )
