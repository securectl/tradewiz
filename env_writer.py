"""
Utility for reading and writing .env file safely.
Handles quoting, preserves comments and ordering, updates os.environ.
"""

import os
import re

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def update_env(updates: dict):
    """
    Write multiple key=value pairs to .env.
    - Preserves comments and ordering
    - Quotes all values for safe parsing
    - Updates os.environ for the running process
    - Skips keys with empty/None values
    """
    try:
        with open(ENV_PATH, "r") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""

    for key, val in updates.items():
        if val is None or val == "":
            continue
        os.environ[key] = val
        quoted = f'{key}="{val}"'
        pattern = re.compile(rf'^{re.escape(key)}=.*$', re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(quoted, content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"{quoted}\n"

    with open(ENV_PATH, "w") as f:
        f.write(content)
