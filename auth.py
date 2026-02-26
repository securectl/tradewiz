"""
Authentication Blueprint — Google OAuth2 via Authlib + Flask-Login.
"""

import os
import logging
from datetime import datetime
from flask import Blueprint, redirect, url_for, session, request, jsonify, render_template
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from db import get_db, put_db, IS_POSTGRES

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# ---------------------------------------------------------------------------
# Flask-Login user model
# ---------------------------------------------------------------------------

login_manager = LoginManager()
login_manager.login_view = "auth.login"


class User(UserMixin):
    def __init__(self, id, google_id, email, name, picture_url, roles=None):
        self.id = id
        self.google_id = google_id
        self.email = email
        self.name = name
        self.picture_url = picture_url
        self.roles = roles or []

    def has_role(self, role):
        return role in self.roles

    @property
    def is_admin(self):
        return "admin" in self.roles

    @property
    def is_trader(self):
        return "admin" in self.roles or "trader" in self.roles

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "picture_url": self.picture_url,
            "roles": self.roles,
            "is_admin": self.is_admin,
            "is_trader": self.is_trader,
        }


@login_manager.user_loader
def load_user(user_id):
    p = "%s" if IS_POSTGRES else "?"
    conn = get_db()
    try:
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT * FROM users WHERE id = {p}", (int(user_id),))
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute(f"SELECT * FROM users WHERE id = {p}", (int(user_id),)).fetchone()
            if row:
                row = dict(row)

        if not row:
            return None

        # Load roles
        if IS_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT role FROM user_roles WHERE user_id = {p}", (row["id"],))
            roles = [r["role"] for r in cur.fetchall()]
            cur.close()
        else:
            roles = [r["role"] for r in conn.execute(
                f"SELECT role FROM user_roles WHERE user_id = {p}", (row["id"],)
            ).fetchall()]

        return User(
            id=row["id"],
            google_id=row["google_id"],
            email=row["email"],
            name=row["name"],
            picture_url=row["picture_url"],
            roles=roles,
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

    # Only register Google if credentials are configured
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

    if client_id and client_secret:
        oauth.register(
            name="google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    else:
        logger.warning("Google OAuth not configured (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing)")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect("/")

    # If OAuth not configured, allow dev bypass
    if not os.getenv("GOOGLE_CLIENT_ID"):
        return render_template("login.html", dev_mode=True)

    return render_template("login.html", dev_mode=False)


@auth_bp.route("/google")
def google_login():
    """Redirect to Google OAuth."""
    google = oauth.create_client("google")
    if not google:
        return jsonify({"error": "Google OAuth not configured"}), 500
    redirect_uri = url_for("auth.callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@auth_bp.route("/callback")
def callback():
    """Handle Google OAuth callback."""
    google = oauth.create_client("google")
    if not google:
        return jsonify({"error": "Google OAuth not configured"}), 500

    token = google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = google.userinfo()

    google_id = userinfo["sub"]
    email = userinfo["email"]
    name = userinfo.get("name", "")
    picture = userinfo.get("picture", "")

    user = _upsert_user(google_id, email, name, picture)
    login_user(user, remember=True)

    return redirect("/")


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
    p = "%s" if IS_POSTGRES else "?"
    conn = get_db()
    try:
        for role in ("admin", "trader"):
            if role not in user.roles:
                if IS_POSTGRES:
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO user_roles (user_id, role) VALUES ({p}, {p}) ON CONFLICT DO NOTHING",
                        (user.id, role),
                    )
                    cur.close()
                else:
                    conn.execute(
                        f"INSERT OR IGNORE INTO user_roles (user_id, role) VALUES ({p}, {p})",
                        (user.id, role),
                    )
        conn.commit()
        user.roles = list(set(user.roles + ["admin", "trader"]))
    finally:
        put_db(conn)

    login_user(user, remember=True)
    return redirect("/")


@auth_bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upsert_user(google_id, email, name, picture_url):
    """Create or update user, assign roles from invites, return User object."""
    p = "%s" if IS_POSTGRES else "?"
    conn = get_db()
    try:
        # Check if user exists
        if IS_POSTGRES:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT * FROM users WHERE google_id = {p}", (google_id,))
            existing = cur.fetchone()
            cur.close()
        else:
            existing = conn.execute(
                f"SELECT * FROM users WHERE google_id = {p}", (google_id,)
            ).fetchone()
            if existing:
                existing = dict(existing)

        now = datetime.utcnow().isoformat()

        if existing:
            user_id = existing["id"]
            # Update last_login
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(f"UPDATE users SET last_login = {p}, name = {p}, picture_url = {p} WHERE id = {p}",
                            (now, name, picture_url, user_id))
                cur.close()
            else:
                conn.execute(f"UPDATE users SET last_login = {p}, name = {p}, picture_url = {p} WHERE id = {p}",
                             (now, name, picture_url, user_id))
        else:
            # Insert new user
            if IS_POSTGRES:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO users (google_id, email, name, picture_url, last_login) "
                    f"VALUES ({p}, {p}, {p}, {p}, {p}) RETURNING id",
                    (google_id, email, name, picture_url, now),
                )
                user_id = cur.fetchone()[0]
                cur.close()
            else:
                cur = conn.execute(
                    f"INSERT INTO users (google_id, email, name, picture_url, last_login) "
                    f"VALUES ({p}, {p}, {p}, {p}, {p})",
                    (google_id, email, name, picture_url, now),
                )
                user_id = cur.lastrowid

            # Check if this is the admin email
            admin_email = os.getenv("ADMIN_EMAIL", "")
            if admin_email and email.lower() == admin_email.lower():
                if IS_POSTGRES:
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO user_roles (user_id, role) VALUES ({p}, {p}) ON CONFLICT DO NOTHING",
                        (user_id, "admin"),
                    )
                    cur.close()
                else:
                    conn.execute(
                        f"INSERT OR IGNORE INTO user_roles (user_id, role) VALUES ({p}, {p})",
                        (user_id, "admin"),
                    )

            # Check invites table
            if IS_POSTGRES:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(f"SELECT * FROM invites WHERE email = {p}", (email.lower(),))
                invite = cur.fetchone()
                cur.close()
            else:
                invite = conn.execute(
                    f"SELECT * FROM invites WHERE email = {p}", (email.lower(),)
                ).fetchone()
                if invite:
                    invite = dict(invite)

            if invite and not invite.get("accepted_at"):
                role = invite["role"]
                if IS_POSTGRES:
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO user_roles (user_id, role, granted_by) VALUES ({p}, {p}, {p}) ON CONFLICT DO NOTHING",
                        (user_id, role, invite.get("invited_by")),
                    )
                    cur.execute(f"UPDATE invites SET accepted_at = {p} WHERE email = {p}", (now, email.lower()))
                    cur.close()
                else:
                    conn.execute(
                        f"INSERT OR IGNORE INTO user_roles (user_id, role, granted_by) VALUES ({p}, {p}, {p})",
                        (user_id, role, invite.get("invited_by")),
                    )
                    conn.execute(f"UPDATE invites SET accepted_at = {p} WHERE email = {p}", (now, email.lower()))

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
            cur.execute(f"SELECT role FROM user_roles WHERE user_id = {p}", (user_id,))
            roles = [r["role"] for r in cur.fetchall()]
            cur.close()
        else:
            roles = [r["role"] for r in conn.execute(
                f"SELECT role FROM user_roles WHERE user_id = {p}", (user_id,)
            ).fetchall()]

        return User(
            id=user_id,
            google_id=google_id,
            email=email,
            name=name,
            picture_url=picture_url,
            roles=roles,
        )
    finally:
        put_db(conn)
