#!/usr/bin/env python3
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "logs"
CACHE_DIR = ROOT / "backend" / "cache"


def _env_int(key, default):
    try:
        return int(os.environ.get(key, str(default)))
    except Exception:
        return default


def _env_bool(key, default):
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _cutoff(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def _delete_file(path):
    try:
        size = path.stat().st_size
        path.unlink()
        return 1, size
    except Exception:
        return 0, 0


def _delete_dir(path):
    try:
        size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except Exception:
        size = 0
    try:
        shutil.rmtree(path)
        return 1, size
    except Exception:
        return 0, 0


def _clean_older_than(base, pattern, days):
    if days <= 0 or not base.exists():
        return 0, 0
    cutoff = _cutoff(days)
    deleted = 0
    freed = 0
    for path in base.rglob(pattern):
        try:
            if not path.is_file():
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if mtime <= cutoff:
                d, f = _delete_file(path)
                deleted += d
                freed += f
        except Exception:
            continue
    return deleted, freed


def _truncate_log(path, max_bytes, keep_lines):
    try:
        if path.stat().st_size <= max_bytes:
            return 0
        lines = path.read_text(errors="ignore").splitlines()
        tail = "\n".join(lines[-keep_lines:]) + "\n"
        path.write_text(tail)
        return 1
    except Exception:
        return 0


def _clean_pycache(root):
    deleted = 0
    freed = 0
    for path in root.rglob("__pycache__"):
        d, f = _delete_dir(path)
        deleted += d
        freed += f
    for path in root.rglob("*.pyc"):
        d, f = _delete_file(path)
        deleted += d
        freed += f
    return deleted, freed


def _delete_dirs_named(root, names):
    deleted = 0
    freed = 0
    name_set = set(names)
    for path in root.rglob("*"):
        if path.is_dir() and path.name in name_set:
            d, f = _delete_dir(path)
            deleted += d
            freed += f
    return deleted, freed


def _delete_files_named(root, names):
    deleted = 0
    freed = 0
    name_set = set(names)
    for path in root.rglob("*"):
        if path.is_file() and path.name in name_set:
            d, f = _delete_file(path)
            deleted += d
            freed += f
    return deleted, freed


def main():
    log_days = _env_int("CLEAN_LOG_DAYS", 7)
    cache_days = _env_int("CLEAN_CACHE_DAYS", 2)
    tmp_days = _env_int("CLEAN_TMP_DAYS", 2)
    max_log_bytes = _env_int("MAX_LOG_BYTES", 5_000_000)
    keep_log_lines = _env_int("KEEP_LOG_LINES", 2000)
    clean_pycache = _env_bool("CLEAN_PYCACHE", True)
    clean_misc = _env_bool("CLEAN_MISC", True)

    total_deleted = 0
    total_freed = 0

    d, f = _clean_older_than(LOG_DIR, "*.log", log_days)
    total_deleted += d
    total_freed += f

    d, f = _clean_older_than(LOG_DIR, "*.log.*", log_days)
    total_deleted += d
    total_freed += f

    if LOG_DIR.exists():
        for log in LOG_DIR.glob("*.log"):
            _truncate_log(log, max_log_bytes, keep_log_lines)

    d, f = _clean_older_than(CACHE_DIR, "*.json", cache_days)
    total_deleted += d
    total_freed += f

    d, f = _clean_older_than(CACHE_DIR, "*.pkl", cache_days)
    total_deleted += d
    total_freed += f

    tmp_dir = ROOT / "backend" / "tmp"
    d, f = _clean_older_than(tmp_dir, "*", tmp_days)
    total_deleted += d
    total_freed += f

    if clean_pycache:
        d, f = _clean_pycache(ROOT)
        total_deleted += d
        total_freed += f

    if clean_misc:
        d, f = _delete_dirs_named(
            ROOT,
            [".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints"],
        )
        total_deleted += d
        total_freed += f
        d, f = _delete_files_named(ROOT, [".DS_Store"])
        total_deleted += d
        total_freed += f

    freed_mb = total_freed / (1024 * 1024)
    print(
        f"[CLEANUP] deleted_files={total_deleted} freed_mb={freed_mb:.2f} "
        f"log_days={log_days} cache_days={cache_days} tmp_days={tmp_days}"
    )


if __name__ == "__main__":
    main()
