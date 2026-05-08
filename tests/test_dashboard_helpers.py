from __future__ import annotations

import json
from pathlib import Path

from app import dashboard_helpers as helpers


def test_load_digest_info_uses_meta_when_available(tmp_path: Path) -> None:
    digest_path = tmp_path / "digest.md"
    meta_path = tmp_path / "digest_meta.json"
    digest_path.write_text("# Digest\n", encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "new_items": 2,
                "failed_sources": 1,
                "counts": {"A": 1, "B": 1, "C": 0},
                "priority_hits": 1,
                "highest_priority_level": 3,
                "failed_source_names": ["NANOG"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    info = helpers.load_digest_info(digest_path, meta_path)

    assert info["available"] is True
    assert info["new_items"] == 2
    assert info["counts"] == {"A": 1, "B": 1, "C": 0}
    assert info["run_state"] == "partial_failure_run"
    assert info["failed_source_names"] == ["NANOG"]


def test_select_morning_articles_prefers_watch_hits_and_a_category() -> None:
    articles = [
        {"id": "c", "category": "C", "collected_at": "2026-05-01T00:00:00+00:00"},
        {"id": "a", "category": "A", "collected_at": "2026-05-02T00:00:00+00:00"},
        {"id": "p", "category": "B", "watch_hits": ["Rule"], "collected_at": "2026-05-01T00:00:00+00:00"},
    ]

    selected = helpers.select_morning_articles(articles, limit=2)

    assert [article["id"] for article in selected] == ["p", "a"]
