from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

try:
    from scripts.browse_network_archive import ARCHIVE_DIR, load_archive_records, parse_datetime, sort_records
    from scripts.watch_network_news import (
        CONFIG_PATH,
        WatchRule,
        evaluate_watch_rules,
        format_watch_match_labels,
        load_watch_rules,
        serialize_watch_matches,
    )
except ModuleNotFoundError:
    from browse_network_archive import ARCHIVE_DIR, load_archive_records, parse_datetime, sort_records  # type: ignore[no-redef]
    from watch_network_news import (  # type: ignore[no-redef]
        CONFIG_PATH,
        WatchRule,
        evaluate_watch_rules,
        format_watch_match_labels,
        load_watch_rules,
        serialize_watch_matches,
    )


ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT_DIR / "reports" / "archive_index.md"
SUMMARY_JSON_PATH = ROOT_DIR / "reports" / "archive_summary.json"
VIEWER_HTML_PATH = ROOT_DIR / "reports" / "archive_viewer.html"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Network Tech Watch archive index files.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Source config YAML.")
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR, help="Directory that contains archive JSONL files.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Markdown index output path.")
    parser.add_argument("--summary-json", type=Path, default=SUMMARY_JSON_PATH, help="Machine-readable summary JSON output path.")
    parser.add_argument("--viewer-html", type=Path, default=VIEWER_HTML_PATH, help="Self-contained HTML viewer output path.")
    parser.add_argument("--overall-limit", type=int, default=15, help="Number of recent articles to show overall.")
    parser.add_argument("--per-category-limit", type=int, default=5, help="Number of recent articles to show per category.")
    parser.add_argument("--per-source-limit", type=int, default=3, help="Number of recent articles to show per featured source.")
    parser.add_argument("--featured-source-limit", type=int, default=8, help="Maximum number of configured sources to feature.")
    return parser.parse_args()


def count_by_category(records: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(record.get("category", "")).strip() for record in records)
    return {category: counter.get(category, 0) for category in ("A", "B", "C")}


def get_latest_collected_at(records: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for record in records:
        collected_at = parse_datetime(str(record.get("collected_at", "")))
        if collected_at is None:
            continue
        if latest is None or collected_at > latest:
            latest = collected_at
    return latest


def format_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def load_configured_source_names(config_path: Path) -> list[str]:
    if not config_path.exists():
        return []
    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(parsed, dict):
        return []
    raw_sources = parsed.get("sources", [])
    if not isinstance(raw_sources, list):
        return []
    names: list[str] = []
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            continue
        name = str(raw_source.get("name", "")).strip()
        if name:
            names.append(name)
    return names


def build_record_preview(
    record: dict[str, Any],
    *,
    watch_rules: list[WatchRule] | tuple[WatchRule, ...] = (),
) -> dict[str, Any]:
    evaluation = evaluate_watch_rules(record, watch_rules)
    watch_hits = list(evaluation.names) or [str(value).strip() for value in record.get("watch_hits", []) if str(value).strip()]
    watch_matches = serialize_watch_matches(evaluation.matches) or list(record.get("watch_matches", []))
    watch_notify_hits = list(evaluation.notify_names) or [str(value).strip() for value in record.get("watch_notify_hits", []) if str(value).strip()]
    watch_priority_level = evaluation.highest_priority_level or int(record.get("watch_priority_level", 0) or 0)
    return {
        "id": str(record.get("id", "")).strip(),
        "title": str(record.get("title", "")).strip() or "(untitled)",
        "link": str(record.get("link", "")).strip(),
        "source": str(record.get("source", "")).strip() or "-",
        "category": str(record.get("category", "")).strip() or "-",
        "published": str(record.get("published", "")).strip(),
        "published_at": str(record.get("published_at", "")).strip(),
        "collected_at": str(record.get("collected_at", "")).strip(),
        "summary": str(record.get("summary", "")).strip(),
        "watch_hits": watch_hits,
        "watch_matches": watch_matches,
        "watch_notify_hits": watch_notify_hits,
        "watch_priority_level": watch_priority_level,
        "watch_notify": bool(evaluation.should_notify or record.get("watch_notify", False)),
    }


def filter_recent_records(
    records: list[dict[str, Any]],
    *,
    since_days: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if since_days <= 0:
        return list(records)
    reference_now = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = reference_now - timedelta(days=since_days)
    filtered: list[dict[str, Any]] = []
    for record in records:
        collected_at = parse_datetime(str(record.get("collected_at", "")))
        if collected_at is not None and collected_at >= cutoff:
            filtered.append(record)
    return filtered


def build_counts_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = count_by_category(records)
    priority_items = sum(1 for record in records if record.get("watch_hits"))
    notify_items = sum(1 for record in records if record.get("watch_notify"))
    return {
        "total_items": len(records),
        "sources": len({str(record.get("source", "")).strip() for record in records if str(record.get("source", "")).strip()}),
        "counts": counts,
        "priority_items": priority_items,
        "priority_notify_items": notify_items,
        "highest_priority_level": max((int(record.get("watch_priority_level", 0) or 0) for record in records), default=0),
        "latest_collected_at": (get_latest_collected_at(records) or datetime.min.replace(tzinfo=UTC)).isoformat() if records else "",
    }


def build_featured_sources(
    previews: list[dict[str, Any]],
    configured_source_names: list[str],
    *,
    per_source_limit: int = 3,
    featured_source_limit: int = 8,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for preview in previews:
        grouped.setdefault(str(preview.get("source", "-")), []).append(preview)
    source_names = configured_source_names[: max(0, featured_source_limit)]
    extras = [
        name for name, _ in Counter(str(p.get("source", "-")) for p in previews).most_common()
        if name not in source_names
    ]
    source_names.extend(extras[: max(0, featured_source_limit - len(source_names))])

    featured: list[dict[str, Any]] = []
    for source_name in source_names:
        records = sort_records(grouped.get(source_name, []))
        counts = count_by_category(records)
        featured.append(
            {
                "source": source_name,
                "total_items": len(records),
                "counts": counts,
                "priority_items": sum(1 for record in records if record.get("watch_hits")),
                "priority_notify_items": sum(1 for record in records if record.get("watch_notify")),
                "latest_collected_at": (get_latest_collected_at(records) or datetime.min.replace(tzinfo=UTC)).isoformat() if records else "",
                "recent_items": records[: max(0, per_source_limit)],
            }
        )
    return featured


def build_watch_rule_summaries(
    previews: list[dict[str, Any]],
    watch_rules: list[WatchRule] | tuple[WatchRule, ...],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for rule in watch_rules:
        matched = [
            preview
            for preview in previews
            if rule.name in [str(value).strip() for value in preview.get("watch_hits", []) if str(value).strip()]
        ]
        notify_matched = [preview for preview in matched if rule.name in preview.get("watch_notify_hits", [])]
        source_counter = Counter(str(preview.get("source", "-")) for preview in matched)
        category_counter = Counter(str(preview.get("category", "-")) for preview in matched)
        latest = get_latest_collected_at(matched) if matched else None
        summaries.append(
            {
                "name": rule.name,
                "priority_level": int(rule.priority_level),
                "notify": bool(rule.notify),
                "notify_cooldown_hours": float(rule.notify_cooldown_hours),
                "exclude_keywords": list(rule.exclude_keywords),
                "override_count": len(rule.source_overrides),
                "source_overrides": [
                    {
                        "source": override.source,
                        "keywords": list(override.keywords),
                        "categories": list(override.categories),
                        "exclude_keywords": list(override.exclude_keywords),
                    }
                    for override in rule.source_overrides
                ],
                "matched_items": len(matched),
                "notify_items": len(notify_matched),
                "matches_7d": len(filter_recent_records(matched, since_days=7, now=now)),
                "matches_30d": len(filter_recent_records(matched, since_days=30, now=now)),
                "latest_matched_at": latest.isoformat() if latest else "",
                "category_counts": {category: category_counter.get(category, 0) for category in ("A", "B", "C")},
                "top_sources": [
                    {"source": source, "count": count}
                    for source, count in source_counter.most_common(3)
                ],
                "recent_matches": matched[:2],
            }
        )
    return summaries


def build_watch_activity_summary(previews: list[dict[str, Any]], watch_rule_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    top_rules = sorted(
        [rule for rule in watch_rule_summaries if int(rule.get("matches_7d", 0) or 0) > 0],
        key=lambda rule: (-int(rule.get("matches_7d", 0) or 0), -int(rule.get("priority_level", 0) or 0), str(rule.get("name", "")).casefold()),
    )[:5]
    source_counter: Counter[str] = Counter()
    source_match_counter: Counter[str] = Counter()
    latest_by_source: dict[str, str] = {}
    for preview in previews:
        watch_hits = [str(value).strip() for value in preview.get("watch_hits", []) if str(value).strip()]
        if not watch_hits:
            continue
        source = str(preview.get("source", "-"))
        source_counter[source] += 1
        source_match_counter[source] += len(watch_hits)
        collected_at = str(preview.get("collected_at", ""))
        if collected_at > latest_by_source.get(source, ""):
            latest_by_source[source] = collected_at
    return {
        "top_rules_7d": [
            {
                "name": str(rule.get("name", "")),
                "priority_level": int(rule.get("priority_level", 0) or 0),
                "notify": bool(rule.get("notify")),
                "matched_items": int(rule.get("matched_items", 0) or 0),
                "notify_items": int(rule.get("notify_items", 0) or 0),
                "matches_7d": int(rule.get("matches_7d", 0) or 0),
                "matches_30d": int(rule.get("matches_30d", 0) or 0),
                "top_source": str((rule.get("top_sources") or [{}])[0].get("source", "")).strip()
                if isinstance(rule.get("top_sources"), list) and rule.get("top_sources")
                else "",
            }
            for rule in top_rules
        ],
        "source_ranking": [
            {
                "source": source,
                "matched_items": int(source_counter[source]),
                "watch_rule_matches": int(source_match_counter[source]),
                "latest_matched_at": latest_by_source.get(source, ""),
            }
            for source, _count in source_counter.most_common(5)
        ],
    }


def build_archive_summary_payload(
    records: list[dict[str, Any]],
    *,
    config_path: Path = CONFIG_PATH,
    now: datetime | None = None,
    overall_limit: int = 15,
    per_category_limit: int = 5,
    per_source_limit: int = 3,
    featured_source_limit: int = 8,
) -> dict[str, Any]:
    reference_now = now or _now_utc()
    watch_rules = load_watch_rules(config_path) if config_path.exists() else []
    previews = [
        build_record_preview(record, watch_rules=watch_rules)
        for record in sort_records(records)
    ]
    recent_by_category = {
        category: [preview for preview in previews if preview.get("category") == category][: max(0, per_category_limit)]
        for category in ("A", "B", "C")
    }
    priority_hits = [preview for preview in previews if preview.get("watch_hits")]
    watch_rule_summaries = build_watch_rule_summaries(previews, watch_rules, now=reference_now)
    configured_source_names = load_configured_source_names(config_path)
    archive_summary = build_counts_summary(previews)
    archive_summary.update(
        {
            "configured_watch_rules": len(watch_rules),
            "rules_with_excludes": sum(1 for rule in watch_rules if rule.exclude_keywords),
            "rules_with_source_overrides": sum(1 for rule in watch_rules if rule.source_overrides),
        }
    )
    return {
        "generated_at": reference_now.isoformat(),
        "archive": archive_summary,
        "windows": {
            "7d": build_counts_summary(filter_recent_records(previews, since_days=7, now=reference_now)),
            "30d": build_counts_summary(filter_recent_records(previews, since_days=30, now=reference_now)),
        },
        "records": previews,
        "recent_overall": previews[: max(0, overall_limit)],
        "recent_by_category": recent_by_category,
        "priority_hits": priority_hits[: max(0, overall_limit)],
        "featured_sources": build_featured_sources(
            previews,
            configured_source_names,
            per_source_limit=per_source_limit,
            featured_source_limit=featured_source_limit,
        ),
        "watch_activity": build_watch_activity_summary(previews, watch_rule_summaries),
        "watch_rules": watch_rule_summaries,
    }


def render_preview_line(preview: dict[str, Any], *, include_source: bool = False, include_category: bool = False) -> str:
    title = str(preview.get("title", "")).strip() or "(untitled)"
    link = str(preview.get("link", "")).strip()
    parts: list[str] = []
    if include_source:
        parts.append(str(preview.get("source", "-")))
    if include_category:
        parts.append(str(preview.get("category", "-")))
    published = str(preview.get("published", "")).strip()
    if published:
        parts.append(published)
    suffix = f" - {' / '.join(parts)}" if parts else ""
    return f"- [{title}]({link}){suffix}" if link else f"- {title}{suffix}"


def render_priority_preview_line(preview: dict[str, Any]) -> str:
    labels = format_watch_match_labels(preview.get("watch_matches", []))
    suffix = f" - Priority: {', '.join(labels)}" if labels else ""
    return render_preview_line(preview, include_source=True, include_category=True) + suffix


def build_archive_index_text(summary: dict[str, Any]) -> str:
    archive = summary.get("archive", {})
    counts = archive.get("counts", {}) if isinstance(archive.get("counts"), dict) else {}
    windows = summary.get("windows", {}) if isinstance(summary.get("windows"), dict) else {}
    window_7d = windows.get("7d", {}) if isinstance(windows.get("7d"), dict) else {}
    window_30d = windows.get("30d", {}) if isinstance(windows.get("30d"), dict) else {}
    lines = [
        "# Network Tech Watch Archive Index",
        "",
        f"- Generated at: **{format_timestamp(parse_datetime(str(summary.get('generated_at', ''))))}**",
        f"- Total archived items: **{int(archive.get('total_items', 0) or 0)}**",
        f"- Sources: **{int(archive.get('sources', 0) or 0)}**",
        f"- A / B / C: **{int(counts.get('A', 0) or 0)} / {int(counts.get('B', 0) or 0)} / {int(counts.get('C', 0) or 0)}**",
        f"- Priority hits: **{int(archive.get('priority_items', 0) or 0)}**",
        f"- Priority notify hits: **{int(archive.get('priority_notify_items', 0) or 0)}**",
        f"- Highest priority level: **{int(archive.get('highest_priority_level', 0) or 0)}**",
        f"- Configured watch rules: **{int(archive.get('configured_watch_rules', 0) or 0)}**",
        f"- Rules with excludes: **{int(archive.get('rules_with_excludes', 0) or 0)}**",
        f"- Rules with source overrides: **{int(archive.get('rules_with_source_overrides', 0) or 0)}**",
        f"- Last 7 days: **{int(window_7d.get('total_items', 0) or 0)}**",
        f"- Last 30 days: **{int(window_30d.get('total_items', 0) or 0)}**",
        "",
        "## Priority Hits",
        "",
    ]
    priority_hits = summary.get("priority_hits", [])
    if isinstance(priority_hits, list) and priority_hits:
        lines.extend(render_priority_preview_line(item) for item in priority_hits)
    else:
        lines.append("- No priority hits yet.")
    lines.append("")

    lines.extend(["## Watch Activity", ""])
    activity = summary.get("watch_activity", {}) if isinstance(summary.get("watch_activity"), dict) else {}
    top_rules = activity.get("top_rules_7d", []) if isinstance(activity.get("top_rules_7d"), list) else []
    source_ranking = activity.get("source_ranking", []) if isinstance(activity.get("source_ranking"), list) else []
    if top_rules:
        lines.append("### Hottest Rules (7d)")
        lines.append("")
        for rule in top_rules:
            lines.append(
                f"- **{rule.get('name', '-')}**: 7d **{int(rule.get('matches_7d', 0) or 0)}**, "
                f"30d **{int(rule.get('matches_30d', 0) or 0)}**, total **{int(rule.get('matched_items', 0) or 0)}**, "
                f"top source **{rule.get('top_source', '-') or '-'}**"
            )
        lines.append("")
    else:
        lines.append("- No recent watch rule matches.")
        lines.append("")
    if source_ranking:
        lines.append("### Match Sources")
        lines.append("")
        for source in source_ranking:
            lines.append(
                f"- **{source.get('source', '-')}**: matched items **{int(source.get('matched_items', 0) or 0)}**, "
                f"rule matches **{int(source.get('watch_rule_matches', 0) or 0)}**"
            )
        lines.append("")

    lines.extend(["## Recent Overall", ""])
    recent_overall = summary.get("recent_overall", [])
    if isinstance(recent_overall, list) and recent_overall:
        lines.extend(render_preview_line(item, include_source=True, include_category=True) for item in recent_overall)
    else:
        lines.append("- No archived items yet.")
    lines.append("")

    lines.extend(["## Recent By Category", ""])
    recent_by_category = summary.get("recent_by_category", {}) if isinstance(summary.get("recent_by_category"), dict) else {}
    for category in ("A", "B", "C"):
        items = recent_by_category.get(category, []) if isinstance(recent_by_category.get(category), list) else []
        lines.append(f"### {category} ({len(items)})")
        lines.append("")
        if items:
            lines.extend(render_preview_line(item, include_source=True) for item in items)
        else:
            lines.append("- No archived items yet.")
        lines.append("")

    lines.extend(["## Featured Sources", ""])
    featured_sources = summary.get("featured_sources", [])
    if isinstance(featured_sources, list) and featured_sources:
        for source in featured_sources:
            lines.append(f"### {source.get('source', '-')} ({int(source.get('total_items', 0) or 0)})")
            lines.append("")
            recent_items = source.get("recent_items", []) if isinstance(source.get("recent_items"), list) else []
            if recent_items:
                lines.extend(render_preview_line(item, include_category=True) for item in recent_items)
            else:
                lines.append("- No archived items yet for this source.")
            lines.append("")
    else:
        lines.append("- No featured source data.")
        lines.append("")

    lines.extend(["## Watch Rules", ""])
    watch_rules = summary.get("watch_rules", [])
    if isinstance(watch_rules, list) and watch_rules:
        for rule in watch_rules:
            top_sources = ", ".join(
                f"{item.get('source', '-')} {int(item.get('count', 0) or 0)}"
                for item in rule.get("top_sources", [])
                if isinstance(item, dict)
            ) or "-"
            lines.append(f"### {rule.get('name', '-')}")
            lines.append("")
            lines.append(f"- Matches: **{int(rule.get('matched_items', 0) or 0)}**")
            lines.append(f"- Notify matches: **{int(rule.get('notify_items', 0) or 0)}**")
            lines.append(f"- Top sources: **{top_sources}**")
            lines.append("")
    else:
        lines.append("- No watch rules configured.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_archive_index(output_path: Path, summary: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_archive_index_text(summary), encoding="utf-8")


def write_archive_summary_json(output_path: Path, summary: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_archive_viewer_html(summary: dict[str, Any]) -> str:
    payload = json.dumps(summary, ensure_ascii=False).replace("</", "<\\/")
    title = "Network Tech Watch Archive Viewer"
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>
      body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f7f8fb; color: #172033; }}
      header {{ padding: 24px; background: #ffffff; border-bottom: 1px solid #d9deea; }}
      main {{ max-width: 1120px; margin: 0 auto; padding: 24px; }}
      .controls {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 20px; }}
      input, select {{ width: 100%; box-sizing: border-box; padding: 9px 10px; border: 1px solid #c9cfdd; border-radius: 6px; background: #fff; }}
      .cards {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }}
      article {{ background: #fff; border: 1px solid #d9deea; border-radius: 8px; padding: 16px; }}
      h1, h2, h3 {{ margin-top: 0; }}
      a {{ color: #0b5cad; }}
      .meta {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }}
      .pill {{ border: 1px solid #c9cfdd; border-radius: 999px; padding: 3px 8px; font-size: 12px; background: #f7f8fb; }}
      .priority {{ border-color: #b45309; color: #7c2d12; background: #fff7ed; }}
      .empty {{ padding: 20px; background: #fff; border: 1px dashed #c9cfdd; border-radius: 8px; }}
    </style>
  </head>
  <body>
    <header>
      <h1>{title}</h1>
      <div id="snapshot"></div>
    </header>
    <main>
      <section class="controls" aria-label="filters">
        <input id="queryInput" placeholder="Search title, summary, source, watch rule">
        <select id="categorySelect"><option value="">All categories</option><option>A</option><option>B</option><option>C</option></select>
        <select id="sourceSelect"><option value="">All sources</option></select>
        <select id="prioritySelect"><option value="">All articles</option><option value="priority">Priority only</option></select>
      </section>
      <section id="results" class="cards"></section>
    </main>
    <script id="archive-viewer-data" type="application/json">{payload}</script>
    <script>
      const data = JSON.parse(document.getElementById("archive-viewer-data").textContent);
      const records = Array.isArray(data.records) ? data.records : [];
      const sourceSelect = document.getElementById("sourceSelect");
      const queryInput = document.getElementById("queryInput");
      const categorySelect = document.getElementById("categorySelect");
      const prioritySelect = document.getElementById("prioritySelect");
      const results = document.getElementById("results");
      const snapshot = document.getElementById("snapshot");
      const archive = data.archive || {{}};
      snapshot.textContent = `${{archive.total_items || 0}} archived articles, ${{archive.priority_items || 0}} priority hits`;
      [...new Set(records.map((record) => record.source).filter(Boolean))].sort().forEach((source) => {{
        const option = document.createElement("option");
        option.value = source;
        option.textContent = source;
        sourceSelect.appendChild(option);
      }});
      function escapeHtml(value) {{
        return String(value || "").replace(/[&<>"']/g, (char) => ({{
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;"
        }}[char]));
      }}
      function safeUrl(value) {{
        try {{
          const url = new URL(String(value || ""), window.location.href);
          return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
        }} catch (_error) {{
          return "#";
        }}
      }}
      function textFor(record) {{
        return [record.title, record.summary, record.source, record.category, ...(record.watch_hits || [])].join(" ").toLowerCase();
      }}
      function render() {{
        const query = queryInput.value.trim().toLowerCase();
        const category = categorySelect.value;
        const source = sourceSelect.value;
        const priority = prioritySelect.value;
        const filtered = records.filter((record) => {{
          if (category && record.category !== category) return false;
          if (source && record.source !== source) return false;
          if (priority === "priority" && !(record.watch_hits || []).length) return false;
          if (query && !textFor(record).includes(query)) return false;
          return true;
        }}).slice(0, 100);
        if (!filtered.length) {{
          results.innerHTML = '<div class="empty">No matching archived articles.</div>';
          return;
        }}
        results.innerHTML = filtered.map((record) => {{
          const watches = (record.watch_hits || []).map((item) => `<span class="pill priority">${{escapeHtml(item)}}</span>`).join("");
          return `<article><h3><a href="${{escapeHtml(safeUrl(record.link))}}" target="_blank" rel="noreferrer noopener">${{escapeHtml(record.title || "(untitled)")}}</a></h3><div class="meta"><span class="pill">${{escapeHtml(record.category || "-")}}</span><span class="pill">${{escapeHtml(record.source || "-")}}</span>${{watches}}</div><p>${{escapeHtml(record.summary || "")}}</p></article>`;
        }}).join("");
      }}
      [queryInput, categorySelect, sourceSelect, prioritySelect].forEach((node) => node.addEventListener("input", render));
      render();
    </script>
  </body>
</html>
"""


def write_archive_viewer_html(output_path: Path, summary: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_archive_viewer_html(summary), encoding="utf-8")


def main() -> int:
    args = parse_args()
    records = load_archive_records(args.archive_dir)
    summary = build_archive_summary_payload(
        records,
        config_path=args.config,
        now=_now_utc(),
        overall_limit=max(0, int(args.overall_limit)),
        per_category_limit=max(0, int(args.per_category_limit)),
        per_source_limit=max(0, int(args.per_source_limit)),
        featured_source_limit=max(0, int(args.featured_source_limit)),
    )
    write_archive_index(args.output, summary)
    write_archive_summary_json(args.summary_json, summary)
    write_archive_viewer_html(args.viewer_html, summary)
    print(args.output)
    print(args.summary_json)
    print(args.viewer_html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
