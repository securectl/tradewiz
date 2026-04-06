"""
Shared helper utilities used across the application.
Extracted from app.py during modular restructure.
"""

import json
from datetime import datetime, timedelta

import numpy as np
from flask_login import current_user

from db import IS_POSTGRES, execute, query_one

# SQL placeholder — use in f-strings for parameterized queries
P = "%s" if IS_POSTGRES else "?"


class NumpyEncoder(json.JSONEncoder):
    """Handle numpy types and NaN in JSON serialization."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            val = float(obj)
            if val != val:  # NaN check
                return None
            return val
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _uid():
    """Get current user ID, or None if not authenticated."""
    if current_user.is_authenticated:
        return current_user.id
    return None


def _require_uid():
    """Get current user ID, raise 401 if not authenticated."""
    if not current_user.is_authenticated:
        return None
    return current_user.id


def get_week_start(dt=None):
    if dt is None:
        dt = datetime.now()
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def cleanup_expired():
    """Remove searches older than 3 days."""
    execute(f"DELETE FROM searches WHERE expires_at < {P}", (datetime.now().isoformat(),))


def _upsert_bot_config(user_id, key, value):
    if IS_POSTGRES:
        execute(
            f"INSERT INTO bot_config (user_id, key, value) VALUES ({P}, {P}, {P}) "
            f"ON CONFLICT(user_id, key) DO UPDATE SET value = {P}",
            (user_id, key, value, value),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO bot_config (user_id, key, value) VALUES ({P}, {P}, {P})",
            (user_id, key, value),
        )


def _pnl_summary(asset_type):
    """Compute P&L summary for a given asset type (crypto or stock). Shared by bot routes."""
    uid = _uid()
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    base = (
        f"SELECT COALESCE(SUM(pnl),0) as total, COUNT(*) as trades, "
        f"SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins "
        f"FROM bot_trades WHERE status='closed' AND user_id={P} AND asset_type={P}"
    )

    def _q(extra_where, params):
        row = query_one(f"{base} {extra_where}", params)
        return {"pnl": round(row["total"], 2), "trades": row["trades"], "wins": row["wins"] or 0}

    return {
        "day": _q(f"AND DATE(closed_at)={P}", (uid, asset_type, today)),
        "week": _q(f"AND closed_at>={P}", (uid, asset_type, week_ago)),
        "month": _q(f"AND closed_at>={P}", (uid, asset_type, month_ago)),
        "year": _q(f"AND closed_at>={P}", (uid, asset_type, year_ago)),
        "all": _q("", (uid, asset_type)),
    }


def _upsert_api_key(user_id, provider, key_name, encrypted_value):
    if IS_POSTGRES:
        execute(
            f"INSERT INTO user_api_keys (user_id, provider, key_name, encrypted_value, updated_at) "
            f"VALUES ({P}, {P}, {P}, {P}, NOW()) "
            f"ON CONFLICT(user_id, provider, key_name) DO UPDATE SET encrypted_value = {P}, updated_at = NOW()",
            (user_id, provider, key_name, encrypted_value, encrypted_value),
        )
    else:
        execute(
            f"INSERT OR REPLACE INTO user_api_keys (user_id, provider, key_name, encrypted_value, updated_at) "
            f"VALUES ({P}, {P}, {P}, {P}, datetime('now'))",
            (user_id, provider, key_name, encrypted_value),
        )
