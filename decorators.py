"""
Route protection decorators for role-based access control.
"""

from functools import wraps
from flask import jsonify
from flask_login import current_user, login_required as _login_required


def login_required(f):
    """Require any authenticated user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        if not current_user.is_admin:
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def trader_required(f):
    """Require admin or trader role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        if not current_user.is_trader:
            return jsonify({"error": "Trader access required"}), 403
        return f(*args, **kwargs)
    return decorated
