"""Regression test: psycopg2 LIKE-pattern escaping.

Apr 2026 incident: crypto bot was sitting idle, throwing
  Failed to fetch daily data for X-USDT: tuple index out of range
on every coin. The misleading error came from a `query_one()` call inside
a try-block that was re-using yfinance's exception logger.

Root cause: the SQL had `LIKE 'swing_%'` with a bare percent sign. psycopg2
treats `%` as a parameter token; combined with the `%s` placeholder for
user_id this asks for 2 params but gets 1 → "tuple index out of range".

Fix: escape literal `%` as `%%` in the query string so it stays literal.

This test scans every Python file for the bug pattern. If it fires after
this date, someone re-introduced bare `LIKE '...%'` in a parameterized query.

Run: docker compose exec app python -m pytest tests/test_sql_like_escaping.py -v
"""
import os
import re
import unittest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _walk_py_files(roots):
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, root)):
            # Skip third-party + venv
            dirnames[:] = [d for d in dirnames if d not in (".git", "venv", "__pycache__", "node_modules")]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


class TestLikeEscaping(unittest.TestCase):
    """Bare `%` inside a LIKE pattern crashes any query that also uses %s
    for parameters under psycopg2. Always escape literal % as %%."""

    # Pattern: LIKE '....%' where the % is followed by ' (end of pattern) and
    # NOT preceded by another % (escaped). i.e. a SINGLE percent sign.
    BAD_PATTERN = re.compile(r"LIKE\s+'[^']*[^%]%'")

    def test_no_unescaped_percent_in_like_patterns(self):
        offenders = []
        for path in _walk_py_files(["crypto_bot", "stock_bot", "claude_bot",
                                      "features", "shared", "screener.py",
                                      "analysis_engine.py", "ai_validator.py"]):
            try:
                with open(path) as f:
                    src = f.read()
            except Exception:
                continue
            for line_no, line in enumerate(src.splitlines(), start=1):
                # Skip comments
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if self.BAD_PATTERN.search(line):
                    offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{line_no}: {line.strip()}")
        self.assertEqual(offenders, [],
            "Bare '%' inside LIKE pattern collides with psycopg2 %s placeholders. "
            "Escape as '%%'. Offenders:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)
