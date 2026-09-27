"""Retention: generation folders contain uploaded screenshots, so they must not live forever.

RUNS_RETENTION_DAYS (default 7) is applied when the app/service starts and can be run any time.
Only folders that look like generations (gen_<hex>) are ever deleted.
"""

import os
import re
import shutil
import time
from pathlib import Path

GENERATION_DIR = re.compile(r"^gen_[0-9a-f]{12}$")


def retention_days():
    return float(os.getenv("RUNS_RETENTION_DAYS", "7"))


def purge_old_runs(runs_dir, days=None, now=None):
    """Delete generation folders older than `days`. Returns the names deleted."""
    days = retention_days() if days is None else days
    cutoff = (now or time.time()) - days * 86400
    runs_dir = Path(runs_dir)
    if days <= 0 or not runs_dir.exists():
        return []
    deleted = []
    for child in runs_dir.iterdir():
        if child.is_dir() and GENERATION_DIR.match(child.name) and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            deleted.append(child.name)
    return sorted(deleted)
