from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import scripts.browse_network_archive as browse


def make_record(item_id: str, *, category: str = "A", source: str = "Cloudflare Blog") -> dict[str, object]:
    return {
        "id": item_id,
        "collected_at": f"2026-05-0{item_id}T00:00:00+00:00",
        "source": source,
        "category": category,
        "title": f"BGP article {item_id}",
        "summary": "RPKI and route leak analysis",
        "link": f"https://example.com/{item_id}",
        "watch_hits": ["Critical routing and internet stability"] if category == "A" else [],
    }


def test_load_archive_records_skips_invalid_lines(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "articles-2026-05.jsonl").write_text(
        json.dumps(make_record("1"), ensure_ascii=False) + "\nnot json\n",
        encoding="utf-8",
    )

    records = browse.load_archive_records(archive_dir)

    assert [record["id"] for record in records] == ["1"]


def test_filter_records_supports_priority_watch_rule_and_since_days() -> None:
    records = [
        make_record("1", category="A"),
        make_record("2", category="B", source="Cilium Blog"),
    ]

    filtered = browse.filter_records(
        records,
        category="A",
        watch_rule="Critical routing and internet stability",
        priority_only=True,
        since_days=10,
        now=datetime(2026, 5, 5, tzinfo=UTC),
    )

    assert [record["id"] for record in filtered] == ["1"]


def test_render_markdown_includes_filters_and_watch_hits() -> None:
    markdown = browse.render_markdown(
        [make_record("1")],
        category="A",
        query="bgp",
        priority_only=True,
    )

    assert "# Network Tech Watch Archive" in markdown
    assert "category=A" in markdown
    assert "priority_only=true" in markdown
    assert "watch=Critical routing and internet stability" in markdown
