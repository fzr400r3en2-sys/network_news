from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import scripts.build_network_archive_index as index


def make_record(item_id: str, *, category: str = "A", title: str = "BGP route leak") -> dict[str, object]:
    return {
        "id": item_id,
        "collected_at": "2026-05-04T00:00:00+00:00",
        "source": "Cloudflare Blog",
        "category": category,
        "title": title,
        "summary": "RPKI and BGP stability notes",
        "link": f"https://example.com/{item_id}",
        "published": "May 4, 2026",
        "published_at": "2026-05-04T00:00:00+00:00",
        "watch_hits": [],
        "watch_matches": [],
        "watch_notify_hits": [],
        "watch_priority_level": 0,
        "watch_notify": False,
    }


def write_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "sources:",
                "  - name: Cloudflare Blog",
                "watch_rules:",
                "  - name: Critical routing",
                "    priority_level: 3",
                "    notify: true",
                "    categories:",
                "      - A",
                "    keywords:",
                "      - BGP",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_build_archive_summary_payload_recomputes_watch_rules(tmp_path: Path) -> None:
    config_path = tmp_path / "sources.yml"
    write_config(config_path)

    summary = index.build_archive_summary_payload(
        [make_record("one")],
        config_path=config_path,
        now=datetime(2026, 5, 5, tzinfo=UTC),
    )

    assert summary["archive"]["total_items"] == 1
    assert summary["archive"]["priority_items"] == 1
    assert summary["priority_hits"][0]["watch_hits"] == ["Critical routing"]
    assert summary["watch_activity"]["top_rules_7d"][0]["name"] == "Critical routing"


def test_build_archive_index_text_contains_expected_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "sources.yml"
    write_config(config_path)
    summary = index.build_archive_summary_payload(
        [make_record("one")],
        config_path=config_path,
        now=datetime(2026, 5, 5, tzinfo=UTC),
    )

    content = index.build_archive_index_text(summary)

    assert "# Network Tech Watch Archive Index" in content
    assert "## Priority Hits" in content
    assert "## Watch Activity" in content
    assert "## Featured Sources" in content
    assert "[BGP route leak](https://example.com/one)" in content


def test_write_summary_and_viewer(tmp_path: Path) -> None:
    summary = {
        "archive": {"total_items": 0, "sources": 0, "counts": {"A": 0, "B": 0, "C": 0}},
        "records": [],
    }
    summary_path = tmp_path / "archive_summary.json"
    viewer_path = tmp_path / "archive_viewer.html"

    index.write_archive_summary_json(summary_path, summary)
    index.write_archive_viewer_html(viewer_path, summary)

    assert json.loads(summary_path.read_text(encoding="utf-8")) == summary
    viewer = viewer_path.read_text(encoding="utf-8")
    assert "Network Tech Watch Archive Viewer" in viewer
    assert 'id="archive-viewer-data"' in viewer


def test_archive_viewer_escapes_record_fields_before_rendering() -> None:
    summary = {
        "archive": {"total_items": 1, "priority_items": 1},
        "records": [
            {
                "title": '<img src=x onerror="alert(1)">',
                "link": "javascript:alert(1)",
                "source": "<b>Source</b>",
                "category": "A",
                "summary": "<script>alert(2)</script>",
                "watch_hits": ["<mark>Rule</mark>"],
            }
        ],
    }

    viewer = index.build_archive_viewer_html(summary)

    assert "function escapeHtml" in viewer
    assert "function safeUrl" in viewer
    assert 'rel="noreferrer noopener"' in viewer
    assert "${escapeHtml(record.title || \"(untitled)\")}" in viewer
    assert "${escapeHtml(safeUrl(record.link))}" in viewer
    assert "${record.title}" not in viewer
    assert "${record.link}" not in viewer
