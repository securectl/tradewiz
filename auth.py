"""
Authentication Blueprint — Email/Password + Google OAuth2 + Admin TOTP 2FA.

Regular users: email/password signup + login, OR Google SSO
Admin users: email/password + TOTP (Google Authenticator) only
"""

import os
import io
import uuid
import time
import base64
import hashlib
import logging
import secrets
from datetime import datetime
from flask import (
    Blueprint, redirect, url_for, session, request,
    jsonify, render_template, flash, abort,
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from db import get_db, put_db, IS_POSTGRES, query, query_one, execute

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


# ---------------------------------------------------------------------------
# CSRF protection (lightweight, no Flask-WTF dependency)
# ---------------------------------------------------------------------------

def _generate_csrf_token():
    """Generate or return existing CSRF token for the current session."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def _validate_csrf():
    """Validate CSRF token on form POST. Aborts 403 if invalid."""
    token = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(token, expected):
        abort(403)


# ---------------------------------------------------------------------------
# Flask-Login user model
# ---------------------------------------------------------------------------

login_manager = LoginManager()
login_manager.login_view = "auth.login"


class User(UserMixin):
    def __init__(self, id, google_id, email, name, picture_url,
                 roles=None, tier="free", password_hash=None, totp_secret=None):
        self.id = id
        self.google_id = google_id
        self.email = email
        self.name = name
        self.picture_url = picture_url
        self.roles = roles or []
        self.tier = tier
        self.password_hash = password_hash
        self.totp_secret = totp_secret

    def has_role(self, role):
        return role in self.roles

    @property
    def is_admin(self):
        return "admin" in self.roles

    @property
    def is_trader(self):
        return "admin" in self.roles or "trader" in self.roles or self.tier == "pro"

    @property
    def has_totp(self):
        return bool(self.totp_secret)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "picture_url": self.picture_url,
            "roles": self.roles,
            "is_admin": self.is_admin,
            "is_trader": self.is_trader,
            "tier": self.tier,
        }


# ---------------------------------------------------------------------------
# TOTP encryption helpers
# ---------------------------------------------------------------------------

def _get_fernet():
    """Get Fernet cipher for TOTP encryption. Uses ENCRYPTION_KEY (preferred) or derives from SECRET_KEY."""
    from cryptography.fernet import Fernet
    # Prefer dedicated ENCRYPTION_KEY (proper Fernet key)
    enc_key = os.getenv("ENCRYPTION_KEY", "")
    if enc_key:
        try:
            return Fernet(enc_key.encode() if isinstance(enc_key, str) else enc_key)
        except Exception:
            pass
    # Fallback: derive from SECRET_KEY (less secure but backwards compatible)
    key = os.getenv("SECRET_KEY", "")
    if not key or key == "dev-secret-key-change-me":
        raise RuntimeError("ENCRYPTION_KEY or a strong SECRET_KEY must be set for TOTP encryption")
    derived = hashlib.sha256(key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_totp_secret(secret):
    return _get_fernet().encrypt(secret.encode()).decode()


def decrypt_totp_secret(encrypted):
    return _get_fernet().decrypt(encrypted.encode()).decode()


# ---------------------------------------------------------------------------
# User loader
# ---------------------------------------------------------------------------

P = "%s" if IS_POSTGRES else "?"


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    try:
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT * FROM users WHERE id = {P}", (int(user_id),))
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute(f"SELECT * FROM users WHERE id = {P}", (int(user_id),)).fetchone()
            if row:
                row = dict(row)

        if not row:
            return None

        # Load roles
        if IS_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT role FROM user_roles WHERE user_id = {P}", (row["id"],))
            roles = [r["role"] for r in cur.fetchall()]
            cur.close()
        else:
            roles = [r["role"] for r in conn.execute(
                f"SELECT role FROM user_roles WHERE user_id = {P}", (row["id"],)
            ).fetchall()]

        # Load subscription tier
        if IS_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT tier FROM user_subscriptions WHERE user_id = {P} AND status = 'active'", (row["id"],))
            tier_row = cur.fetchone()
            cur.close()
        else:
            tier_row = conn.execute(
                f"SELECT tier FROM user_subscriptions WHERE user_id = {P} AND status = 'active'", (row["id"],)
            ).fetchone()
            if tier_row:
                tier_row = dict(tier_row)

        tier = tier_row["tier"] if tier_row else "free"

        return User(
            id=row["id"],
            google_id=row.get("google_id"),
            email=row["email"],
            name=row["name"],
            picture_url=row.get("picture_url", ""),
            roles=roles,
            tier=tier,
            password_hash=row.get("password_hash"),
            totp_secret=row.get("totp_secret"),
        )
    finally:
        put_db(conn)


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# OAuth setup
# ---------------------------------------------------------------------------

oauth = OAuth()


def init_auth(app):
    """Initialize OAuth and Login Manager with the Flask app."""
    app.config.setdefault("SECRET_KEY", os.getenv("SECRET_KEY", "dev-secret-key-change-me"))

    login_manager.init_app(app)
    oauth.init_app(app)

    # Make csrf_token() available in all templates
    app.jinja_env.globals["csrf_token"] = _generate_csrf_token

    # Only register Google if credentials are configured
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # Force HTTPS scheme for url_for behind reverse proxy
    app.config["PREFERRED_URL_SCHEME"] = "https"

    if client_id and client_secret:
        _google_redirect_uri = os.getenv(
            "GOOGLE_REDIRECT_URI",
            "https://tradewiz.market/auth/google/callback",
        )
        oauth.register(
            name="google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
            redirect_uri=_google_redirect_uri,
        )
        logger.info(f"Google OAuth configured — redirect_uri: {_google_redirect_uri}")
    else:
        logger.warning("Google OAuth not configured (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing)")


# ---------------------------------------------------------------------------
# Routes — Login / Signup
# ---------------------------------------------------------------------------

@auth_bp.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect("/")
    google_configured = bool(os.getenv("GOOGLE_CLIENT_ID"))
    # If no Google OAuth AND dev mode wanted, show dev bypass
    if not google_configured and os.getenv("DEV_MODE", "") == "1":
        return render_template("login.html", dev_mode=True, google_configured=False)
    return render_template("login.html", dev_mode=False, google_configured=google_configured)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Invite-only signup — user must have a pending invite to register."""
    if current_user.is_authenticated:
        return redirect("/")

    if request.method == "GET":
        return render_template("login.html", signup_mode=True,
                               dev_mode=False, google_configured=bool(os.getenv("GOOGLE_CLIENT_ID")))

    _validate_csrf()
    email = request.form.get("email", "").strip().lower()
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("auth.signup"))

    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.signup"))

    # Check if email already registered
    existing = query_one(f"SELECT id FROM users WHERE email = {P}", (email,))
    if existing:
        flash("An account with this email already exists. Please log in.", "error")
        return redirect(url_for("auth.login"))

    # INVITE-ONLY: must have a pending invite
    invite = query_one(
        f"SELECT * FROM invites WHERE email = {P} AND accepted_at IS NULL",
        (email,),
    )
    if not invite:
        flash("This platform is invite-only. You need an invitation to sign up. Contact an administrator.", "error")
        return redirect(url_for("auth.login"))

    # Create account
    from werkzeug.security import generate_password_hash
    password_hash = generate_password_hash(password)
    user = _create_email_user(email, name or email.split("@")[0], password_hash)

    if user:
        login_user(user)
        logger.info(f"New invite-only signup: {email} (tier={user.tier})")
        return redirect("/")
    else:
        flash("Failed to create account. Please try again.", "error")
        return redirect(url_for("auth.signup"))


# ─── Login Rate Limiting (in-memory, per IP) ──────────────────────────
_login_attempts = {}  # ip -> [(timestamp, email), ...]
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SEC = 300  # 5 minutes
_LOGIN_LOCKOUT_SEC = 900  # 15 minutes after exceeding

def _check_login_rate(ip):
    """Check if IP is rate limited. Returns (allowed, message)."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Clean old attempts
    attempts = [a for a in attempts if now - a[0] < _LOGIN_LOCKOUT_SEC]
    _login_attempts[ip] = attempts
    # Count recent attempts within window
    recent = [a for a in attempts if now - a[0] < _LOGIN_WINDOW_SEC]
    if len(recent) >= _LOGIN_MAX_ATTEMPTS:
        remaining = int(_LOGIN_WINDOW_SEC - (now - recent[0][0]))
        return False, f"Too many login attempts. Try again in {remaining // 60 + 1} minutes."
    return True, ""

def _record_login_attempt(ip, email):
    """Record a failed login attempt."""
    _login_attempts.setdefault(ip, []).append((time.time(), email))


@auth_bp.route("/email-login", methods=["POST"])
def email_login():
    _validate_csrf()

    # Rate limit check
    ip = request.remote_addr
    allowed, msg = _check_login_rate(ip)
    if not allowed:
        flash(msg, "error")
        return redirect(url_for("auth.login"))

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    row = query_one(f"SELECT * FROM users WHERE email = {P}", (email,))
    if not row or not row.get("password_hash"):
        _record_login_attempt(ip, email)
        flash("Invalid email or password", "error")
        return redirect(url_for("auth.login"))

    if not check_password_hash(row["password_hash"], password):
        _record_login_attempt(ip, email)
        flash("Invalid email or password", "error")
        return redirect(url_for("auth.login"))

    # Check if account is locked
    if row.get("is_locked"):
        flash("This account has been locked by an administrator. Contact support.", "error")
        return redirect(url_for("auth.login"))

    # Update last_login timestamp
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute(f"UPDATE users SET last_login = {P} WHERE id = {P}", (now, row["id"]))

    # Check if user is admin — redirect to TOTP flow
    roles = [r["role"] for r in query(
        f"SELECT role FROM user_roles WHERE user_id = {P}", (row["id"],))]
    if "admin" in roles:
        session["admin_pre_auth_user_id"] = row["id"]
        if row.get("totp_secret"):
            return redirect(url_for("auth.admin_totp_verify"))
        else:
            return redirect(url_for("auth.totp_setup"))

    user = load_user(row["id"])
    login_user(user, remember=True)
    return redirect("/")


# ---------------------------------------------------------------------------
# Routes — Google SSO
# ---------------------------------------------------------------------------

@auth_bp.route("/google")
def google_login():
    """Redirect to Google OAuth."""
    google = oauth.create_client("google")
    if not google:
        return jsonify({"error": "Google OAuth not configured"}), 500
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "https://tradewiz.market/auth/google/callback")
    logger.info(f"Google OAuth redirect_uri: {redirect_uri}")
    return google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
@auth_bp.route("/callback")
def callback():
    """Handle Google OAuth callback."""
    google = oauth.create_client("google")
    if not google:
        return jsonify({"error": "Google OAuth not configured"}), 500

    try:
        token = google.authorize_access_token()
    except Exception as e:
        logger.error(f"Google OAuth callback error: {e}")
        google_configured = bool(os.getenv("GOOGLE_CLIENT_ID"))
        return render_template("login.html", dev_mode=False,
                               google_configured=google_configured,
                               error=f"Google sign-in failed: {e}")
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = google.userinfo()

    google_id = userinfo["sub"]
    email = userinfo["email"]
    name = userinfo.get("name", "")
    picture = userinfo.get("picture", "")

    # Check if user already exists (returning user — always allow)
    existing = query_one(f"SELECT id FROM users WHERE email = {P}", (email.lower(),))
    if not existing:
        existing = query_one(f"SELECT id FROM users WHERE google_id = {P}", (google_id,))

    # Invite-only: block new users without an invite
    if not existing:
        admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        is_admin_email = admin_email and email.lower() == admin_email
        invite = query_one(f"SELECT * FROM invites WHERE email = {P} AND accepted_at IS NULL", (email.lower(),))
        if not is_admin_email and not invite:
            google_configured = bool(os.getenv("GOOGLE_CLIENT_ID"))
            return render_template("login.html", dev_mode=False,
                                   google_configured=google_configured,
                                   error="This platform is invite-only. Please contact an admin for access.")

    user = _upsert_user(google_id, email, name, picture)

    # Check if account is locked
    locked_row = query_one(f"SELECT is_locked FROM users WHERE id = {P}", (user.id,))
    if locked_row and locked_row.get("is_locked"):
        google_configured = bool(os.getenv("GOOGLE_CLIENT_ID"))
        return render_template("login.html", dev_mode=False,
                               google_configured=google_configured,
                               error="This account has been locked by an administrator. Contact support.")

    # Block admin accounts from Google SSO — must use admin login
    if user.is_admin:
        google_configured = bool(os.getenv("GOOGLE_CLIENT_ID"))
        return render_template("login.html", dev_mode=False,
                               google_configured=google_configured,
                               error="Admin accounts must use the Admin Login page with password + authenticator.")

    login_user(user, remember=True)
    return redirect("/")


# ---------------------------------------------------------------------------
# Routes — Admin Login + TOTP
# ---------------------------------------------------------------------------

@auth_bp.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if current_user.is_authenticated:
        return redirect("/")

    if request.method == "GET":
        return render_template("admin_login.html")

    _validate_csrf()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    row = query_one(f"SELECT * FROM users WHERE email = {P}", (email,))
    if not row or not row.get("password_hash"):
        return render_template("admin_login.html", error="Invalid credentials")

    if not check_password_hash(row["password_hash"], password):
        return render_template("admin_login.html", error="Invalid credentials")

    # Verify admin role
    roles = [r["role"] for r in query(
        f"SELECT role FROM user_roles WHERE user_id = {P}", (row["id"],))]
    if "admin" not in roles:
        return render_template("admin_login.html", error="Invalid credentials")

    session["admin_pre_auth_user_id"] = row["id"]
    if row.get("totp_secret"):
        return redirect(url_for("auth.admin_totp_verify"))
    else:
        return redirect(url_for("auth.totp_setup"))


@auth_bp.route("/totp-verify", methods=["GET", "POST"])
def admin_totp_verify():
    import pyotp
    user_id = session.get("admin_pre_auth_user_id")
    if not user_id:
        return redirect(url_for("auth.admin_login"))

    row = query_one(f"SELECT * FROM users WHERE id = {P}", (user_id,))
    if not row or not row.get("totp_secret"):
        return redirect(url_for("auth.totp_setup"))

    if request.method == "GET":
        return render_template("totp_verify.html")

    _validate_csrf()
    code = request.form.get("code", "").strip()
    try:
        secret = decrypt_totp_secret(row["totp_secret"])
        totp = pyotp.TOTP(secret)
        if not totp.verify(code, valid_window=1):
            return render_template("totp_verify.html", error="Invalid code. Try again.")
    except Exception:
        return render_template("totp_verify.html", error="Verification failed. Try again.")

    # TOTP verified — complete login
    session.pop("admin_pre_auth_user_id", None)
    user = load_user(user_id)
    login_user(user, remember=True)
    return redirect("/")


@auth_bp.route("/totp-setup", methods=["GET", "POST"])
def totp_setup():
    import pyotp
    import qrcode

    user_id = session.get("admin_pre_auth_user_id")
    # Also allow already-logged-in admins to re-setup
    if not user_id and current_user.is_authenticated and current_user.is_admin:
        user_id = current_user.id
    if not user_id:
        return redirect(url_for("auth.admin_login"))

    row = query_one(f"SELECT * FROM users WHERE id = {P}", (user_id,))
    if not row:
        return redirect(url_for("auth.admin_login"))

    if request.method == "GET":
        secret = pyotp.random_base32()
        session["pending_totp_secret"] = secret

        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(name=row["email"], issuer_name="AI Stock Analyst")

        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()

        return render_template("totp_setup.html", qr_code=qr_b64, secret=secret)

    # POST — verify the code to confirm setup
    _validate_csrf()
    code = request.form.get("code", "").strip()
    secret = session.get("pending_totp_secret")
    if not secret:
        return redirect(url_for("auth.totp_setup"))

    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        # Re-render with same QR
        uri = totp.provisioning_uri(name=row["email"], issuer_name="AI Stock Analyst")
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        return render_template("totp_setup.html", qr_code=qr_b64, secret=secret,
                               error="Invalid code. Scan QR code and try again.")

    # Save encrypted TOTP secret
    encrypted = encrypt_totp_secret(secret)
    execute(f"UPDATE users SET totp_secret = {P} WHERE id = {P}", (encrypted, user_id))
    session.pop("pending_totp_secret", None)

    # If this was during admin login flow, complete login
    if session.get("admin_pre_auth_user_id"):
        session.pop("admin_pre_auth_user_id", None)
        user = load_user(user_id)
        login_user(user, remember=True)
        return redirect("/")

    flash("Authenticator configured successfully", "success")
    return redirect("/")


# ---------------------------------------------------------------------------
# Routes — Admin Setup (first-time bootstrap)
# ---------------------------------------------------------------------------

@auth_bp.route("/admin-setup", methods=["GET", "POST"])
def admin_setup():
    """First-time admin password setup. Available when ADMIN_EMAIL user has no password."""
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if not admin_email:
        return render_template("admin_setup.html",
                               error="ADMIN_EMAIL not configured in environment")

    row = query_one(f"SELECT * FROM users WHERE email = {P}", (admin_email,))

    if request.method == "GET":
        if row and row.get("password_hash"):
            return render_template("admin_setup.html",
                                   error="Admin account already configured. Use Admin Login.",
                                   email=admin_email, already_setup=True)
        return render_template("admin_setup.html", email=admin_email,
                               create_new=not row)

    # POST
    _validate_csrf()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")
    name = request.form.get("name", "Admin").strip()

    if len(password) < 8:
        return render_template("admin_setup.html", email=admin_email,
                               error="Password must be at least 8 characters",
                               create_new=not row)
    if password != confirm:
        return render_template("admin_setup.html", email=admin_email,
                               error="Passwords do not match",
                               create_new=not row)

    pw_hash = generate_password_hash(password)

    if row:
        # Existing user (e.g. from Google SSO) — set password
        execute(f"UPDATE users SET password_hash = {P} WHERE id = {P}", (pw_hash, row["id"]))
        user_id = row["id"]
    else:
        # Create new admin user
        conn = get_db()
        try:
            now = datetime.utcnow().isoformat()
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO users (email, name, password_hash, last_login) "
                    f"VALUES ({P}, {P}, {P}, {P}) RETURNING id",
                    (admin_email, name, pw_hash, now),
                )
                user_id = cur.fetchone()[0]
                cur.close()
            else:
                cur = conn.execute(
                    f"INSERT INTO users (email, name, password_hash, last_login) "
                    f"VALUES ({P}, {P}, {P}, {P})",
                    (admin_email, name, pw_hash, now),
                )
                user_id = cur.lastrowid

            # Grant admin role
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO user_roles (user_id, role) VALUES ({P}, {P}) ON CONFLICT DO NOTHING",
                    (user_id, "admin"),
                )
                cur.execute(
                    f"INSERT INTO user_roles (user_id, role) VALUES ({P}, {P}) ON CONFLICT DO NOTHING",
                    (user_id, "trader"),
                )
                cur.close()
            else:
                conn.execute(
                    f"INSERT OR IGNORE INTO user_roles (user_id, role) VALUES ({P}, {P})",
                    (user_id, "admin"),
                )
                conn.execute(
                    f"INSERT OR IGNORE INTO user_roles (user_id, role) VALUES ({P}, {P})",
                    (user_id, "trader"),
                )

            # Seed subscription
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO user_subscriptions (user_id, tier, status) VALUES ({P}, 'admin', 'active') "
                    f"ON CONFLICT (user_id) DO UPDATE SET tier = 'admin'",
                    (user_id,),
                )
                cur.close()
            else:
                conn.execute(
                    f"INSERT OR REPLACE INTO user_subscriptions (user_id, tier, status) VALUES ({P}, 'admin', 'active')",
                    (user_id,),
                )

            conn.commit()
        finally:
            put_db(conn)

        from migrations import seed_defaults
        seed_defaults(user_id)

    session["admin_pre_auth_user_id"] = user_id
    return redirect(url_for("auth.totp_setup"))


# ---------------------------------------------------------------------------
# Routes — Dev login + Logout
# ---------------------------------------------------------------------------

@auth_bp.route("/dev-login", methods=["POST"])
def dev_login():
    """Development-only login bypass (when Google OAuth is not configured).
    Auto-grants admin+trader roles so all features are accessible locally."""
    if os.getenv("GOOGLE_CLIENT_ID"):
        return jsonify({"error": "Dev login disabled when OAuth is configured"}), 403

    email = request.form.get("email", "dev@localhost")
    name = request.form.get("name", "Developer")

    user = _upsert_user(f"dev-{email}", email, name, "")

    # In dev mode, auto-grant admin + trader roles for full access
    conn = get_db()
    try:
        for role in ("admin", "trader"):
            if role not in user.roles:
                if IS_POSTGRES:
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO user_roles (user_id, role) VALUES ({P}, {P}) ON CONFLICT DO NOTHING",
                        (user.id, role),
                    )
                    cur.close()
                else:
                    conn.execute(
                        f"INSERT OR IGNORE INTO user_roles (user_id, role) VALUES ({P}, {P})",
                        (user.id, role),
                    )
        conn.commit()
        user.roles = list(set(user.roles + ["admin", "trader"]))
    finally:
        put_db(conn)

    login_user(user, remember=True)
    return redirect("/")


@auth_bp.route("/support-request", methods=["POST"])
def support_request():
    """Handle support requests from the chatbot widget. Sends email + stores in DB."""
    data = request.get_json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    issue = (data.get("issue") or "").strip()
    message = (data.get("message") or "").strip()

    if not all([name, email, issue, message]):
        return jsonify({"ok": False, "error": "All fields are required"}), 400

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ticket_id = f"TW-{int(datetime.now().timestamp())}"

    # Store in DB
    try:
        execute(
            f"INSERT INTO support_requests (ticket_id, name, email, issue_type, message, created_at) "
            f"VALUES ({P}, {P}, {P}, {P}, {P}, {P})",
            (ticket_id, name, email, issue, message, now),
        )
    except Exception as e:
        logger.warning(f"Failed to store support request: {e}")

    # Send email notification
    support_email = os.getenv("SUPPORT_EMAIL", "abdul.khan@securectl.com")
    email_sent = False
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_host = os.getenv("SMTP_HOST", "")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")

        if smtp_host and smtp_user:
            msg = MIMEMultipart()
            msg["From"] = smtp_user
            msg["To"] = support_email
            msg["Subject"] = f"[TradeWiz Support] {ticket_id} — {issue.replace('_', ' ').title()}"
            msg["Reply-To"] = email

            body = (
                f"New Support Request\n"
                f"{'='*40}\n\n"
                f"Ticket:  {ticket_id}\n"
                f"Name:    {name}\n"
                f"Email:   {email}\n"
                f"Issue:   {issue.replace('_', ' ').title()}\n"
                f"Time:    {now}\n\n"
                f"Message:\n{message}\n\n"
                f"{'='*40}\n"
                f"Reply directly to this email to respond to the user.\n"
            )
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            email_sent = True
            logger.info(f"Support email sent for {ticket_id} to {support_email}")
        else:
            logger.info(f"SMTP not configured — support request {ticket_id} stored in DB only")
    except Exception as e:
        logger.warning(f"Failed to send support email: {e}")

    # If SMTP not configured, try Google Form submission as fallback
    if not email_sent:
        google_form_url = os.getenv("SUPPORT_GOOGLE_FORM_URL", "")
        if google_form_url:
            try:
                import requests as req
                req.post(google_form_url, data={
                    "entry.1": name,
                    "entry.2": email,
                    "entry.3": issue,
                    "entry.4": message,
                    "entry.5": ticket_id,
                }, timeout=5)
                logger.info(f"Support request {ticket_id} submitted to Google Form")
            except Exception as e:
                logger.warning(f"Google Form submission failed: {e}")

    return jsonify({"ok": True, "ticket_id": ticket_id, "email_sent": email_sent})


@auth_bp.route("/logout")
def logout():
    logout_user()
    session.pop("admin_pre_auth_user_id", None)
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_email_user(email, name, password_hash):
    """Create a new user via email/password registration."""
    conn = get_db()
    try:
        now = datetime.utcnow().isoformat()

        # For SQLite with NOT NULL google_id on old DBs, use a UUID placeholder
        google_id_val = None if IS_POSTGRES else f"email:{uuid.uuid4().hex}"

        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO users (email, name, password_hash, last_login) "
                f"VALUES ({P}, {P}, {P}, {P}) RETURNING id",
                (email, name, password_hash, now),
            )
            user_id = cur.fetchone()[0]
            cur.close()
        else:
            cur = conn.execute(
                f"INSERT INTO users (google_id, email, name, password_hash, last_login) "
                f"VALUES ({P}, {P}, {P}, {P}, {P})",
                (google_id_val, email, name, password_hash, now),
            )
            user_id = cur.lastrowid

        # Check invites
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT * FROM invites WHERE email = {P}", (email.lower(),))
            invite = cur.fetchone()
            cur.close()
        else:
            invite = conn.execute(
                f"SELECT * FROM invites WHERE email = {P}", (email.lower(),)
            ).fetchone()
            if invite:
                invite = dict(invite)

        roles = []
        invite_tier = "free"
        invite_bot_access = "none"
        if invite and not invite.get("accepted_at"):
            role = invite["role"]
            invite_tier = invite.get("tier", "free") or "free"
            invite_bot_access = invite.get("bot_access", "none") or "none"
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO user_roles (user_id, role, granted_by) VALUES ({P}, {P}, {P}) ON CONFLICT DO NOTHING",
                    (user_id, role, invite.get("invited_by")),
                )
                cur.execute(f"UPDATE invites SET accepted_at = {P} WHERE email = {P}", (now, email.lower()))
                cur.close()
            else:
                conn.execute(
                    f"INSERT OR IGNORE INTO user_roles (user_id, role, granted_by) VALUES ({P}, {P}, {P})",
                    (user_id, role, invite.get("invited_by")),
                )
                conn.execute(f"UPDATE invites SET accepted_at = {P} WHERE email = {P}", (now, email.lower()))
            roles.append(role)

        conn.commit()
        put_db(conn)

        from migrations import seed_defaults
        seed_defaults(user_id)

        conn = get_db()

        # Seed subscription with tier and bot_access from invite
        tier = invite_tier
        bot_access = invite_bot_access
        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO user_subscriptions (user_id, tier, bot_access, status) VALUES ({P}, {P}, {P}, 'active') "
                f"ON CONFLICT (user_id) DO NOTHING",
                (user_id, tier, bot_access),
            )
            cur.close()
        else:
            conn.execute(
                f"INSERT OR IGNORE INTO user_subscriptions (user_id, tier, bot_access, status) VALUES ({P}, {P}, {P}, 'active')",
                (user_id, tier, bot_access),
            )
        conn.commit()

        return User(id=user_id, google_id=None, email=email, name=name,
                    picture_url="", roles=roles, tier=tier,
                    password_hash=password_hash)
    finally:
        put_db(conn)


def _upsert_user(google_id, email, name, picture_url):
    """Create or update user (Google SSO), assign roles from invites, return User object."""
    conn = get_db()
    try:
        # Check if user exists
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT * FROM users WHERE google_id = {P}", (google_id,))
            existing = cur.fetchone()
            cur.close()
            # Also check by email (user may have signed up via email first)
            if not existing:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(f"SELECT * FROM users WHERE email = {P}", (email.lower(),))
                existing = cur.fetchone()
                cur.close()
        else:
            existing = conn.execute(
                f"SELECT * FROM users WHERE google_id = {P}", (google_id,)
            ).fetchone()
            if existing:
                existing = dict(existing)
            if not existing:
                existing = conn.execute(
                    f"SELECT * FROM users WHERE email = {P}", (email.lower(),)
                ).fetchone()
                if existing:
                    existing = dict(existing)

        now = datetime.utcnow().isoformat()
        invite_tier = "free"
        invite_bot_access = "none"

        if existing:
            user_id = existing["id"]
            # Update last_login and link google_id if not set
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE users SET last_login = {P}, name = {P}, picture_url = {P}, google_id = COALESCE(google_id, {P}) WHERE id = {P}",
                    (now, name, picture_url, google_id, user_id))
                cur.close()
            else:
                conn.execute(
                    f"UPDATE users SET last_login = {P}, name = {P}, picture_url = {P}, google_id = COALESCE(google_id, {P}) WHERE id = {P}",
                    (now, name, picture_url, google_id, user_id))
        else:
            # Insert new user
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO users (google_id, email, name, picture_url, last_login) "
                    f"VALUES ({P}, {P}, {P}, {P}, {P}) RETURNING id",
                    (google_id, email, name, picture_url, now),
                )
                user_id = cur.fetchone()[0]
                cur.close()
            else:
                cur = conn.execute(
                    f"INSERT INTO users (google_id, email, name, picture_url, last_login) "
                    f"VALUES ({P}, {P}, {P}, {P}, {P})",
                    (google_id, email, name, picture_url, now),
                )
                user_id = cur.lastrowid

            # Check if this is the admin email
            admin_email = os.getenv("ADMIN_EMAIL", "")
            if admin_email and email.lower() == admin_email.lower():
                if IS_POSTGRES:
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO user_roles (user_id, role) VALUES ({P}, {P}) ON CONFLICT DO NOTHING",
                        (user_id, "admin"),
                    )
                    cur.close()
                else:
                    conn.execute(
                        f"INSERT OR IGNORE INTO user_roles (user_id, role) VALUES ({P}, {P})",
                        (user_id, "admin"),
                    )

            # Check invites table
            if IS_POSTGRES:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(f"SELECT * FROM invites WHERE email = {P}", (email.lower(),))
                invite = cur.fetchone()
                cur.close()
            else:
                invite = conn.execute(
                    f"SELECT * FROM invites WHERE email = {P}", (email.lower(),)
                ).fetchone()
                if invite:
                    invite = dict(invite)

            invite_tier = "free"
            invite_bot_access = "none"
            if invite and not invite.get("accepted_at"):
                role = invite["role"]
                invite_tier = invite.get("tier", "free") or "free"
                invite_bot_access = invite.get("bot_access", "none") or "none"
                if IS_POSTGRES:
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO user_roles (user_id, role, granted_by) VALUES ({P}, {P}, {P}) ON CONFLICT DO NOTHING",
                        (user_id, role, invite.get("invited_by")),
                    )
                    cur.execute(f"UPDATE invites SET accepted_at = {P} WHERE email = {P}", (now, email.lower()))
                    cur.close()
                else:
                    conn.execute(
                        f"INSERT OR IGNORE INTO user_roles (user_id, role, granted_by) VALUES ({P}, {P}, {P})",
                        (user_id, role, invite.get("invited_by")),
                    )
                    conn.execute(f"UPDATE invites SET accepted_at = {P} WHERE email = {P}", (now, email.lower()))

            # Seed default config for new user
            conn.commit()
            put_db(conn)
            from migrations import seed_defaults
            seed_defaults(user_id)
            conn = get_db()

        conn.commit()

        # Load roles
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT role FROM user_roles WHERE user_id = {P}", (user_id,))
            roles = [r["role"] for r in cur.fetchall()]
            cur.close()
        else:
            roles = [r["role"] for r in conn.execute(
                f"SELECT role FROM user_roles WHERE user_id = {P}", (user_id,)
            ).fetchall()]

        # Determine tier and bot_access from invite (new users) or existing subscription
        tier = "free"
        bot_access = "none"
        if "admin" in roles:
            tier = "admin"
            bot_access = "crypto,stock"
        elif existing:
            # Returning user — keep existing subscription
            pass
        else:
            # New user — use invite tier/bot_access
            tier = invite_tier
            bot_access = invite_bot_access

        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO user_subscriptions (user_id, tier, bot_access, status) VALUES ({P}, {P}, {P}, 'active') "
                f"ON CONFLICT (user_id) DO UPDATE SET tier = CASE "
                f"WHEN user_subscriptions.tier = 'free' AND {P} != 'free' THEN {P} "
                f"ELSE user_subscriptions.tier END, "
                f"bot_access = CASE "
                f"WHEN user_subscriptions.bot_access = 'none' AND {P} != 'none' THEN {P} "
                f"ELSE user_subscriptions.bot_access END",
                (user_id, tier, bot_access, tier, tier, bot_access, bot_access),
            )
            cur.close()
        else:
            conn.execute(
                f"INSERT OR IGNORE INTO user_subscriptions (user_id, tier, bot_access, status) VALUES ({P}, {P}, {P}, 'active')",
                (user_id, tier, bot_access),
            )
        conn.commit()

        # Re-read actual tier from DB
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT tier FROM user_subscriptions WHERE user_id = {P} AND status = 'active'", (user_id,))
            tier_row = cur.fetchone()
            cur.close()
        else:
            tier_row = conn.execute(
                f"SELECT tier FROM user_subscriptions WHERE user_id = {P} AND status = 'active'", (user_id,)
            ).fetchone()
            if tier_row:
                tier_row = dict(tier_row)
        tier = tier_row["tier"] if tier_row else "free"

        return User(
            id=user_id,
            google_id=google_id,
            email=email,
            name=name,
            picture_url=picture_url,
            roles=roles,
            tier=tier,
        )
    finally:
        put_db(conn)
