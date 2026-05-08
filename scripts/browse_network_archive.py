from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT_DIR / "data" / "archive"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse archived Network Tech Watch articles.")
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR, help="Directory that contains articles-YYYY-MM.jsonl files.")
    parser.add_argument("--category", choices=("A", "B", "C"), default="", help="Filter by category.")
    parser.add_argument("--source", default="", help="Filter by exact source name.")
    parser.add_argument("--query", default="", help="Case-insensitive search across source, title, summary, and URL.")
    parser.add_argument("--watch-rule", default="", help="Filter by exact watch rule name.")
    parser.add_argument("--priority-only", action="store_true", help="Only include articles that matched any watch rule.")
    parser.add_argument("--since-days", type=int, default=0, help="Only include records collected in the last N days.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of records to print.")
    parser.add_argument("--json", action="store_true", help="Print matching records as JSON.")
    return parser.parse_args()


def parse_datetime(value: str) -> datetime | None:
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


def list_archive_files(archive_dir: Path) -> list[Path]:
    if not archive_dir.exists():
        return []
    return sorted(archive_dir.glob("articles-*.jsonl"), reverse=True)


def load_archive_records(archive_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in list_archive_files(archive_dir):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def build_searchable_text(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("source", "")),
        str(record.get("category", "")),
        str(record.get("title", "")),
        str(record.get("summary", "")),
        str(record.get("link", "")),
        " ".join(str(value) for value in record.get("watch_hits", []) if str(value).strip()),
    ]
    return " ".join(parts).casefold()


def filter_records(
    records: list[dict[str, Any]],
    *,
    category: str = "",
    source: str = "",
    query: str = "",
    watch_rule: str = "",
    priority_only: bool = False,
    since_days: int = 0,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    cutoff: datetime | None = None
    if since_days > 0:
        cutoff = (now or datetime.now(UTC)).astimezone(UTC) - timedelta(days=since_days)
    normalized_query = query.strip().casefold()
    normalized_watch_rule = watch_rule.strip()

    filtered: list[dict[str, Any]] = []
    for record in records:
        if category and str(record.get("category", "")).strip() != category:
            continue
        if source and str(record.get("source", "")).strip() != source:
            continue
        watch_hits = [str(value).strip() for value in record.get("watch_hits", []) if str(value).strip()]
        if priority_only and not watch_hits:
            continue
        if normalized_watch_rule and normalized_watch_rule not in watch_hits:
            continue
        if cutoff is not None:
            collected_at = parse_datetime(str(record.get("collected_at", "")))
            if collected_at is None or collected_at < cutoff:
                continue
        if normalized_query and normalized_query not in build_searchable_text(record):
            continue
        filtered.append(record)
    return filtered


def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(record: dict[str, Any]) -> tuple[datetime, datetime, str]:
        collected_at = parse_datetime(str(record.get("collected_at", ""))) or datetime.min.replace(tzinfo=UTC)
        published_at = parse_datetime(str(record.get("published_at", ""))) or datetime.min.replace(tzinfo=UTC)
        return (collected_at, published_at, str(record.get("id", "")))

    return sorted(records, key=sort_key, reverse=True)


def render_markdown(
    records: list[dict[str, Any]],
    *,
    category: str = "",
    source: str = "",
    query: str = "",
    watch_rule: str = "",
    priority_only: bool = False,
    since_days: int = 0,
) -> str:
    lines = ["# Network Tech Watch Archive", ""]
    filters: list[str] = []
    if category:
        filters.append(f"category={category}")
    if source:
        filters.append(f"source={source}")
    if query:
        filters.append(f"query={query}")
    if watch_rule:
        filters.append(f"watch_rule={watch_rule}")
    if priority_only:
        filters.append("priority_only=true")
    if since_days > 0:
        filters.append(f"since_days={since_days}")
    if filters:
        lines.append(f"- Filters: {', '.join(filters)}")
    lines.append(f"- Matches: {len(records)}")
    lines.append("")

    if not records:
        lines.append("- No archived articles matched the current filters.")
        return "\n".join(lines) + "\n"

    for record in records:
        title = str(record.get("title", "")).strip() or "(untitled)"
        link = str(record.get("link", "")).strip()
        source_name = str(record.get("source", "")).strip() or "-"
        category_name = str(record.get("category", "")).strip() or "-"
        published = str(record.get("published", "")).strip()
        watch_hits = [str(value).strip() for value in record.get("watch_hits", []) if str(value).strip()]
        lines.append(f"- [{title}]({link})" if link else f"- {title}")
        meta = f"  source={source_name} | category={category_name}"
        if published:
            meta += f" | published={published}"
        if watch_hits:
            meta += f" | watch={', '.join(watch_hits)}"
        lines.append(meta)
        summary = str(record.get("summary", "")).strip()
        if summary:
            lines.append(f"  summary={summary}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    records = load_archive_records(args.archive_dir)
    filtered = filter_records(
        records,
        category=args.category,
        source=args.source,
        query=args.query,
        watch_rule=args.watch_rule,
        priority_only=bool(args.priority_only),
        since_days=max(0, int(args.since_days)),
    )
    selected = sort_records(filtered)[: max(0, int(args.limit))]

    if args.json:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return 0

    print(
        render_markdown(
            selected,
            category=args.category,
            source=args.source,
            query=args.query,
            watch_rule=args.watch_rule,
            priority_only=bool(args.priority_only),
            since_days=max(0, int(args.since_days)),
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
