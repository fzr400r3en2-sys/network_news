from __future__ import annotations

import json
from pathlib import Path

import scripts.watch_network_news as news


def make_source(name: str = "Example Network Blog") -> news.Source:
    return news.Source(
        name=name,
        type="rss",
        url="https://example.com/feed.xml",
        category_hints=("routing", "automation"),
    )


def make_item(title: str, summary: str = "", source: news.Source | None = None) -> dict[str, str]:
    resolved_source = source or make_source()
    return news.build_item(
        resolved_source,
        title=title,
        link=f"https://example.com/{news.normalize_title(title).replace(' ', '-')}",
        summary=summary,
        published="May 1, 2026",
        published_at="2026-05-01T00:00:00+00:00",
    )


def test_classify_item_returns_network_buckets() -> None:
    assert news.classify_item(make_item("BGP route leak causes outage", "RPKI validation details")) == "A"
    assert news.classify_item(make_item("Network automation with telemetry", "NetDevOps best practices")) == "B"
    assert news.classify_item(make_item("Conference recap and webinar schedule", "")) == "C"


def test_keyword_matches_handles_protocol_tokens() -> None:
    text = "QUIC and HTTP/3 deployment with Wi-Fi 7 edge networking"

    assert news.keyword_matches(text, "HTTP/3")
    assert news.keyword_matches(text, "Wi-Fi 7")
    assert not news.keyword_matches(text, "BGP")


def test_load_sources_and_watch_rules_from_repo_config() -> None:
    sources = news.load_sources(news.CONFIG_PATH)
    rules = news.load_watch_rules(news.CONFIG_PATH)

    assert any(source.name == "Cloudflare Blog" for source in sources)
    assert any(rule.name == "Critical routing and internet stability" for rule in rules)


def test_evaluate_watch_rules_reports_priority_metadata() -> None:
    rule = news.WatchRule(
        name="Critical routing",
        keywords=("BGP", "RPKI"),
        categories=("A",),
        priority_level=3,
        notify=True,
    )
    item = {**make_item("BGP route leak", "RPKI helped contain the incident"), "category": "A"}

    evaluation = news.evaluate_watch_rules(item, [rule])

    assert evaluation.names == ("Critical routing",)
    assert evaluation.notify_names == ("Critical routing",)
    assert evaluation.highest_priority_level == 3
    assert evaluation.should_notify is True


def test_dedupe_items_prefers_first_shared_url() -> None:
    source_a = make_source("A")
    source_b = make_source("B")
    first = news.build_item(source_a, "One", "https://example.com/shared")
    second = news.build_item(source_b, "Two", "https://example.com/shared")

    assert news.dedupe_items([first, second]) == [first]


def test_run_watch_writes_state_archive_and_reports(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "sources.yml"
    config_path.write_text(
        "\n".join(
            [
                "sources:",
                "  - name: Example Network Blog",
                "    type: rss",
                "    url: https://example.com/feed.xml",
                "    category_hints:",
                "      - routing",
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
    state_path = tmp_path / "data" / "latest.json"
    archive_dir = tmp_path / "data" / "archive"
    report_path = tmp_path / "reports" / "latest.md"
    digest_path = tmp_path / "reports" / "digest.md"
    meta_path = tmp_path / "reports" / "digest_meta.json"

    def fake_fetch_all_sources(sources, *, timeout=20):
        source = sources[0]
        item = news.build_item(
            source,
            title="BGP route leak outage",
            link="https://example.com/bgp-route-leak",
            summary="RPKI and routing operations analysis",
            published="May 1, 2026",
            published_at="2026-05-01T00:00:00+00:00",
        )
        return (
            [item],
            [],
            {
                source.name: news.SourceFetchResult(
                    name=source.name,
                    status="ok",
                    fetch_method="rss",
                    raw_fetched=1,
                    after_filter=1,
                )
            },
        )

    monkeypatch.setattr(news, "fetch_all_sources", fake_fetch_all_sources)

    result = news.run_watch(
        config=config_path,
        state=state_path,
        archive_dir=archive_dir,
        report=report_path,
        digest=digest_path,
        digest_meta=meta_path,
        limit=5,
    )

    assert result["exit_code"] == 0
    assert result["new_items_count"] == 1
    assert result["priority_hits"] == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["seen_ids"]
    archive_files = list(archive_dir.glob("articles-*.jsonl"))
    assert len(archive_files) == 1
    archived = json.loads(archive_files[0].read_text(encoding="utf-8").splitlines()[0])
    assert archived["category"] == "A"
    assert archived["watch_hits"] == ["Critical routing"]
    assert "Network Tech Watch Report" in report_path.read_text(encoding="utf-8")
    assert json.loads(meta_path.read_text(encoding="utf-8"))["priority_hits"] == 1
