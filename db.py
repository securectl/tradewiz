"""
Centralized database connection module.
Supports PostgreSQL (via DATABASE_URL env var) with connection pooling,
falls back to SQLite for local development.
"""

import os
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

# ---------------------------------------------------------------------------
# PostgreSQL path (production / Railway)
# ---------------------------------------------------------------------------
if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import pool as _pool

    _pg_pool = None

    def _get_pool():
        global _pg_pool
        if _pg_pool is None or _pg_pool.closed:
            _pg_pool = _pool.ThreadedConnectionPool(
                minconn=2, maxconn=10, dsn=DATABASE_URL,
            )
        return _pg_pool

    def get_db():
        """Return a psycopg2 connection with RealDictCursor (drop-in for sqlite3.Row)."""
        conn = _get_pool().getconn()
        conn.autocommit = False
        return conn

    def put_db(conn):
        """Return a connection to the pool."""
        try:
            _get_pool().putconn(conn)
        except Exception:
            pass

    def close_pool():
        global _pg_pool
        if _pg_pool and not _pg_pool.closed:
            _pg_pool.closeall()
            _pg_pool = None

    def dict_cursor(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    IS_POSTGRES = True

# ---------------------------------------------------------------------------
# SQLite path (local development)
# ---------------------------------------------------------------------------
else:
    import sqlite3

    _SQLITE_PATH = os.path.join(os.path.dirname(__file__), "data", "searches.db")

    def get_db():
        """Return a sqlite3 connection with Row factory."""
        os.makedirs(os.path.dirname(_SQLITE_PATH), exist_ok=True)
        conn = sqlite3.connect(_SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def put_db(conn):
        """No-op for SQLite (no pool)."""
        try:
            conn.close()
        except Exception:
            pass

    def close_pool():
        pass

    def dict_cursor(conn):
        """SQLite already returns Row objects; return a dummy wrapper."""
        return conn

    IS_POSTGRES = False


# ---------------------------------------------------------------------------
# Helper: execute a query and return all rows as list-of-dicts
# Works identically for both backends.
# ---------------------------------------------------------------------------
def query(sql, params=None):
    """Execute a SELECT and return list of dicts."""
    conn = get_db()
    try:
        if IS_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(sql, params or ()).fetchall()
            rows = [dict(r) for r in rows]
        return rows
    finally:
        put_db(conn)


def query_one(sql, params=None):
    """Execute a SELECT and return a single dict or None."""
    conn = get_db()
    try:
        if IS_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or ())
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute(sql, params or ()).fetchone()
            if row:
                row = dict(row)
        return row
    finally:
        put_db(conn)


def execute(sql, params=None):
    """Execute an INSERT/UPDATE/DELETE and commit. Returns lastrowid for inserts."""
    conn = get_db()
    try:
        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(sql, params or ())
            # Try to get the inserted id if the query has RETURNING
            lastrowid = None
            if cur.description:
                row = cur.fetchone()
                if row:
                    lastrowid = row[0]
            conn.commit()
            cur.close()
            return lastrowid
        else:
            cur = conn.execute(sql, params or ())
            conn.commit()
            return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        put_db(conn)


def execute_many(statements):
    """Execute multiple SQL statements in a single transaction.
    statements: list of (sql, params) tuples.
    """
    conn = get_db()
    try:
        if IS_POSTGRES:
            cur = conn.cursor()
            for sql, params in statements:
                cur.execute(sql, params or ())
            conn.commit()
            cur.close()
        else:
            for sql, params in statements:
                conn.execute(sql, params or ())
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_db(conn)
