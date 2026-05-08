"""Locking wrapper for running Network Tech Watch from the dashboard."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.watch_network_news import run_watch  # noqa: E402


LOCK_PATH = ROOT_DIR / ".network_watch_running.lock"
STALE_LOCK_SECONDS = 1800

EMPTY_RESULT: dict[str, Any] = {
    "run_state": "unknown",
    "run_state_reason": "",
    "new_items_count": 0,
    "failed_sources": 0,
    "fallback_attempted": 0,
    "fallback_succeeded": 0,
    "priority_hits": 0,
    "priority_notify_hits": 0,
    "highest_priority_level": 0,
    "exit_code": -1,
    "error": "",
}


def _remove_stale_lock_if_needed() -> bool:
    if not LOCK_PATH.exists():
        return False
    try:
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age > STALE_LOCK_SECONDS:
            LOCK_PATH.unlink(missing_ok=True)
            return True
    except OSError:
        return False
    return False


def _acquire_lock() -> bool:
    _remove_stale_lock_if_needed()
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def is_running() -> bool:
    _remove_stale_lock_if_needed()
    return LOCK_PATH.exists()


def run_once(**kwargs: Any) -> dict[str, Any]:
    if not _acquire_lock():
        return {
            **EMPTY_RESULT,
            "status": "already_running",
            "error": "Another Network Tech Watch run is already in progress",
        }
    try:
        result = run_watch(**kwargs)
        result["status"] = "completed"
        return result
    except Exception as exc:  # noqa: BLE001
        return {
            **EMPTY_RESULT,
            "status": "error",
            "error": str(exc),
        }
    finally:
        _release_lock()
