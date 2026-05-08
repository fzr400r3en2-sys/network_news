"""Pure helpers for the Network Tech Watch dashboard."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


_DIGEST_UNAVAILABLE: dict[str, Any] = {
    "available": False,
    "text": "",
    "new_items": 0,
    "failed_sources": 0,
    "counts": {"A": 0, "B": 0, "C": 0},
    "priority_hits": 0,
    "priority_notify_hits": 0,
    "highest_priority_level": 0,
    "fallback_attempted": 0,
    "fallback_succeeded": 0,
    "failed_source_names": [],
    "fallback_rescued_names": [],
    "run_state": "unknown",
    "run_state_reason": "",
}


def load_digest_info(digest_md_path: Path, digest_meta_path: Path | None = None) -> dict[str, Any]:
    if digest_meta_path is None:
        digest_meta_path = digest_md_path.parent / "digest_meta.json"

    meta: dict[str, Any] | None = None
    if digest_meta_path.exists():
        try:
            parsed = json.loads(digest_meta_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                meta = parsed
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            meta = None

    text = ""
    md_available = False
    if digest_md_path.exists():
        try:
            text = digest_md_path.read_text(encoding="utf-8")
            md_available = True
        except (OSError, UnicodeDecodeError):
            pass

    if meta is None and not md_available:
        return dict(_DIGEST_UNAVAILABLE)

    def _int(key: str) -> int:
        if meta is None:
            return 0
        try:
            return int(meta.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    counts_raw = meta.get("counts", {}) if isinstance(meta, dict) else {}
    counts = {
        "A": int(counts_raw.get("A", 0)) if isinstance(counts_raw, dict) else 0,
        "B": int(counts_raw.get("B", 0)) if isinstance(counts_raw, dict) else 0,
        "C": int(counts_raw.get("C", 0)) if isinstance(counts_raw, dict) else 0,
    }
    new_items = _int("new_items")
    failed_sources = _int("failed_sources")
    if failed_sources == 0 and new_items == 0:
        run_state = "quiet_run"
        run_state_reason = "new=0, failed_sources=0"
    elif failed_sources > 0 and new_items == 0:
        run_state = "partial_failure_run"
        run_state_reason = f"new=0, failed_sources={failed_sources}"
    elif failed_sources > 0:
        run_state = "partial_failure_run"
        run_state_reason = f"new={new_items}, failed_sources={failed_sources}"
    else:
        run_state = "new_items_run"
        run_state_reason = f"new={new_items}, failed_sources=0"

    failed_source_names = meta.get("failed_source_names", []) if isinstance(meta, dict) else []
    fallback_rescued_names = meta.get("fallback_rescued_names", []) if isinstance(meta, dict) else []
    return {
        "available": True,
        "text": text,
        "new_items": new_items,
        "failed_sources": failed_sources,
        "counts": counts,
        "priority_hits": _int("priority_hits"),
        "priority_notify_hits": _int("priority_notify_hits"),
        "highest_priority_level": _int("highest_priority_level"),
        "fallback_attempted": _int("fallback_attempted"),
        "fallback_succeeded": _int("fallback_succeeded"),
        "failed_source_names": [str(value) for value in failed_source_names] if isinstance(failed_source_names, list) else [],
        "fallback_rescued_names": [str(value) for value in fallback_rescued_names] if isinstance(fallback_rescued_names, list) else [],
        "run_state": run_state,
        "run_state_reason": run_state_reason,
    }


def load_state_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "updated_at": None, "seen_count": 0}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"available": False, "updated_at": None, "seen_count": 0}
    if not isinstance(parsed, dict):
        return {"available": False, "updated_at": None, "seen_count": 0}
    seen_ids = parsed.get("seen_ids", [])
    return {
        "available": True,
        "updated_at": parsed.get("updated_at") if isinstance(parsed.get("updated_at"), str) else None,
        "seen_count": len(seen_ids) if isinstance(seen_ids, list) else 0,
    }


def load_archive_summary(path: Path) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "available": False,
        "archive": {"total_items": 0, "counts": {"A": 0, "B": 0, "C": 0}},
        "records": [],
        "priority_hits": [],
        "recent_overall": [],
        "featured_sources": [],
        "watch_rules": [],
        "watch_activity": {"top_rules_7d": [], "source_ranking": []},
    }
    if not path.exists():
        return empty
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return empty
    if not isinstance(parsed, dict):
        return empty
    parsed["available"] = True
    return parsed


def load_text_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_datetime(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_recent_articles(archive_dir: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not archive_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(archive_dir.glob("articles-*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        if len(records) >= limit * 3:
            break
    records.sort(key=lambda record: str(record.get("collected_at", "") or record.get("published_at", "")), reverse=True)
    return records[: max(0, limit)]


def load_articles_by_days(archive_dir: Path, days: int = 14, limit: int = 500) -> list[dict[str, Any]]:
    if not archive_dir.exists():
        return []
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=max(0, days))
    records = [
        record
        for record in load_recent_articles(archive_dir, limit=limit * 3)
        if (_parse_datetime(str(record.get("collected_at", "") or record.get("published_at", ""))) or datetime.min.replace(tzinfo=UTC)) >= cutoff
    ]
    return records[: max(0, limit)]


def select_morning_articles(articles: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    for article in articles:
        watch_hits = article.get("watch_hits") or []
        category = str(article.get("category", "")).upper()
        category_rank = {"A": 2, "B": 1}.get(category, 0)
        if not watch_hits and category_rank == 0:
            continue
        timestamp = str(article.get("collected_at", "") or article.get("published_at", ""))
        ranked.append(((1 if watch_hits else 0, category_rank, timestamp), dict(article)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [article for _, article in ranked[: max(0, limit)]]
