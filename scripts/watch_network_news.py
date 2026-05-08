from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "sources.yml"
STATE_PATH = ROOT_DIR / "data" / "latest.json"
ARCHIVE_DIR = ROOT_DIR / "data" / "archive"
REPORT_PATH = ROOT_DIR / "reports" / "latest.md"
DIGEST_PATH = ROOT_DIR / "reports" / "digest.md"
DIGEST_META_PATH = ROOT_DIR / "reports" / "digest_meta.json"

USER_AGENT = "network-tech-watch/0.1"
TIMEOUT_SECONDS = 20
DEFAULT_SOURCE_MAX_ITEMS = 80
DEFAULT_SOURCE_RECENT_DAYS = 45
MAX_SEEN_IDS = 5000

HTML_FALLBACK_SELECTOR_GROUPS = (
    ("article", "article"),
    ("post_container", "div.post, div.blog-post, section.post, div.blog-card, li.post"),
    ("heading_link", "h1 a[href], h2 a[href], h3 a[href], a[href]"),
)
HTML_FALLBACK_SELECTOR_GROUPS_BY_NAME = dict(HTML_FALLBACK_SELECTOR_GROUPS)

A_KEYWORDS = {
    "bgp",
    "rpki",
    "route leak",
    "routing outage",
    "outage",
    "ddos",
    "dnssec",
    "dns",
    "doh",
    "dot",
    "ipv6",
    "nat64",
    "quic",
    "http/3",
    "tls",
    "pki",
    "zero trust",
    "ztna",
    "sase",
    "sd-wan",
    "evpn",
    "vxlan",
    "srv6",
    "segment routing",
    "wifi 7",
    "wi-fi 7",
    "5g",
    "6g",
    "private 5g",
    "cve",
    "vulnerability",
}
B_KEYWORDS = {
    "automation",
    "netdevops",
    "observability",
    "telemetry",
    "opentelemetry",
    "ebpf",
    "cilium",
    "cni",
    "kubernetes networking",
    "service mesh",
    "best practice",
    "reference architecture",
    "design",
    "case study",
    "implementation",
    "operations",
    "troubleshooting",
    "vpc",
    "load balancer",
    "transit gateway",
    "private link",
    "cloud networking",
    "cdn",
    "edge",
    "waf",
}
C_KEYWORDS = {
    "webinar",
    "event",
    "conference",
    "meetup",
    "podcast",
    "recap",
    "announcement",
    "customer story",
    "partner",
    "award",
    "hiring",
    "survey",
    "training",
    "course",
}


@dataclass(frozen=True)
class Source:
    name: str
    type: str
    url: str
    category_hints: tuple[str, ...] = ()
    max_items: int = DEFAULT_SOURCE_MAX_ITEMS
    recent_days: int = DEFAULT_SOURCE_RECENT_DAYS
    site_url: str | None = None
    html_fallback_enabled: bool = True
    html_fallback_selector_groups: tuple[str, ...] = ()
    same_host_only: bool = True


@dataclass(frozen=True)
class SourceFetchResult:
    name: str
    status: str
    fetch_method: str
    raw_fetched: int
    after_filter: int
    reason: str = ""
    fallback_attempted: bool = False
    fallback_selector: str = ""
    used_fallback: bool = False


@dataclass(frozen=True)
class WatchRuleSourceOverride:
    source: str
    keywords: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class WatchRule:
    name: str
    keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    source_overrides: tuple[WatchRuleSourceOverride, ...] = ()
    priority_level: int = 1
    notify: bool = False
    notify_cooldown_hours: float = 0.0


@dataclass(frozen=True)
class WatchMatch:
    name: str
    priority_level: int
    notify: bool


@dataclass(frozen=True)
class WatchEvaluation:
    names: tuple[str, ...] = ()
    notify_names: tuple[str, ...] = ()
    highest_priority_level: int = 0
    should_notify: bool = False
    matches: tuple[WatchMatch, ...] = ()


@dataclass(frozen=True)
class ClassificationDecision:
    bucket: str
    scores: dict[str, int]
    reasons: dict[str, list[str]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch network technology feeds and report new articles."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to sources.yml.")
    parser.add_argument("--state", type=Path, default=STATE_PATH, help="Persistent seen-id state JSON.")
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR, help="Directory for monthly JSONL archive files.")
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="Markdown report output path.")
    parser.add_argument("--digest", type=Path, default=DIGEST_PATH, help="Short Markdown digest output path.")
    parser.add_argument(
        "--digest-meta",
        type=Path,
        default=None,
        help="Machine-readable digest metadata JSON. Defaults to digest_meta.json next to --digest.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Max new items to show per category.")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS, help="HTTP timeout seconds per request.")
    return parser.parse_args()


def resolve_digest_meta_path(digest_path: Path, explicit_digest_meta: Path | None = None) -> Path:
    if explicit_digest_meta is not None:
        return explicit_digest_meta
    return digest_path.parent / "digest_meta.json"


def parse_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _read_string_list(raw: Mapping[str, Any], key: str, *, context: str) -> tuple[str, ...]:
    values = raw.get(key, [])
    if values is None:
        return ()
    if not isinstance(values, list):
        raise ValueError(f"{context}: '{key}' must be a list.")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{context}: '{key}' entries must be strings.")
        normalized = value.strip()
        if normalized:
            result.append(normalized)
    return tuple(result)


def load_config(path: Path) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if parsed is None:
        raise ValueError(f"Config file {path} is empty.")
    if not isinstance(parsed, dict):
        raise ValueError(f"Config root in {path} must be a mapping.")
    return parsed


def load_sources(path: Path) -> list[Source]:
    parsed = load_config(path)
    raw_sources = parsed.get("sources")
    if raw_sources is None:
        raise ValueError(f"Config file {path} must contain a top-level 'sources' list.")
    if not isinstance(raw_sources, list):
        raise ValueError(f"Config key 'sources' in {path} must be a list.")

    sources: list[Source] = []
    for index, raw_source in enumerate(raw_sources, start=1):
        context = f"Source entry #{index} in {path}"
        if not isinstance(raw_source, dict):
            raise ValueError(f"{context} must be a mapping.")
        name = str(raw_source.get("name", "")).strip()
        url = str(raw_source.get("url", "")).strip()
        if not name:
            raise ValueError(f"{context} is missing required key: name")
        if not url:
            raise ValueError(f"{context} is missing required key: url")

        selector_groups = _read_string_list(
            raw_source,
            "html_fallback_selector_groups",
            context=context,
        )
        unsupported_groups = [
            value for value in selector_groups if value not in HTML_FALLBACK_SELECTOR_GROUPS_BY_NAME
        ]
        if unsupported_groups:
            allowed = ", ".join(HTML_FALLBACK_SELECTOR_GROUPS_BY_NAME)
            raise ValueError(f"{context}: unsupported html_fallback_selector_groups {unsupported_groups}; allowed: {allowed}")

        sources.append(
            Source(
                name=name,
                type=str(raw_source.get("type", "rss")).strip() or "rss",
                url=url,
                category_hints=_read_string_list(raw_source, "category_hints", context=context),
                max_items=parse_positive_int(raw_source.get("max_items"), DEFAULT_SOURCE_MAX_ITEMS),
                recent_days=parse_non_negative_int(raw_source.get("recent_days"), DEFAULT_SOURCE_RECENT_DAYS),
                site_url=str(raw_source.get("site_url", "")).strip() or None,
                html_fallback_enabled=bool(raw_source.get("html_fallback_enabled", True)),
                html_fallback_selector_groups=selector_groups,
                same_host_only=bool(raw_source.get("same_host_only", True)),
            )
        )
    return sources


def _read_source_overrides(raw_rule: Mapping[str, Any], *, path: Path, index: int) -> tuple[WatchRuleSourceOverride, ...]:
    raw_overrides = raw_rule.get("source_overrides", [])
    if raw_overrides is None:
        return ()
    if not isinstance(raw_overrides, list):
        raise ValueError(f"Watch rule entry #{index} in {path}: source_overrides must be a list.")

    overrides: list[WatchRuleSourceOverride] = []
    for override_index, raw_override in enumerate(raw_overrides, start=1):
        context = f"Watch rule entry #{index} override #{override_index} in {path}"
        if not isinstance(raw_override, dict):
            raise ValueError(f"{context} must be a mapping.")
        source = str(raw_override.get("source", "")).strip()
        if not source:
            raise ValueError(f"{context} is missing required key: source")
        categories = tuple(value.upper() for value in _read_string_list(raw_override, "categories", context=context))
        invalid = [value for value in categories if value not in {"A", "B", "C"}]
        if invalid:
            raise ValueError(f"{context}: unsupported categories {invalid}; allowed: A, B, C.")
        overrides.append(
            WatchRuleSourceOverride(
                source=source,
                keywords=_read_string_list(raw_override, "keywords", context=context),
                categories=categories,
                exclude_keywords=_read_string_list(raw_override, "exclude_keywords", context=context),
            )
        )
    return tuple(overrides)


def load_watch_rules(path: Path) -> list[WatchRule]:
    parsed = load_config(path)
    raw_rules = parsed.get("watch_rules", [])
    if raw_rules is None:
        return []
    if not isinstance(raw_rules, list):
        raise ValueError(f"Config key 'watch_rules' in {path} must be a list.")

    rules: list[WatchRule] = []
    for index, raw_rule in enumerate(raw_rules, start=1):
        context = f"Watch rule entry #{index} in {path}"
        if not isinstance(raw_rule, dict):
            raise ValueError(f"{context} must be a mapping.")
        name = str(raw_rule.get("name", "")).strip()
        if not name:
            raise ValueError(f"{context} is missing required key: name")
        categories = tuple(value.upper() for value in _read_string_list(raw_rule, "categories", context=context))
        invalid = [value for value in categories if value not in {"A", "B", "C"}]
        if invalid:
            raise ValueError(f"{context}: unsupported categories {invalid}; allowed: A, B, C.")
        priority_level = parse_positive_int(raw_rule.get("priority_level"), 1)
        notify = bool(raw_rule.get("notify", False))
        notify_cooldown_hours = float(raw_rule.get("notify_cooldown_hours", 0) or 0)
        if notify_cooldown_hours < 0:
            raise ValueError(f"{context}: notify_cooldown_hours must be non-negative.")
        rule = WatchRule(
            name=name,
            keywords=_read_string_list(raw_rule, "keywords", context=context),
            exclude_keywords=_read_string_list(raw_rule, "exclude_keywords", context=context),
            sources=_read_string_list(raw_rule, "sources", context=context),
            categories=categories,
            source_overrides=_read_source_overrides(raw_rule, path=path, index=index),
            priority_level=priority_level,
            notify=notify,
            notify_cooldown_hours=notify_cooldown_hours,
        )
        if not (rule.keywords or rule.sources or rule.categories or rule.source_overrides):
            raise ValueError(f"{context} must define at least one match condition.")
        rules.append(rule)
    return rules


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_ids": [], "updated_at": None}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"seen_ids": [], "updated_at": None}
    if not isinstance(parsed, dict):
        return {"seen_ids": [], "updated_at": None}
    seen_ids = parsed.get("seen_ids", [])
    return {
        "seen_ids": seen_ids if isinstance(seen_ids, list) else [],
        "updated_at": parsed.get("updated_at") if isinstance(parsed.get("updated_at"), str) else None,
    }


def save_state(path: Path, seen_ids: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "seen_ids": list(seen_ids),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_archive_path(base_dir: Path, collected_at: datetime) -> Path:
    return base_dir / f"articles-{collected_at.strftime('%Y-%m')}.jsonl"


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


def normalize_link(link: str) -> str:
    stripped = str(link).strip()
    if not stripped:
        return ""
    parts = urlsplit(stripped)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title).strip()).casefold()


def build_entry_id(source_name: str, title: str, link: str) -> str:
    raw = f"{source_name}\n{normalize_title(title)}\n{normalize_link(link)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def clean_text(text: Any) -> str:
    raw = "" if text is None else str(text)
    if "<" in raw and ">" in raw:
        raw = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", raw).strip()


def _struct_time_to_iso(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime(*value[:6], tzinfo=UTC).isoformat()
    except (TypeError, ValueError):
        return ""


def pick_published_at(entry: Any) -> str:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        iso = _struct_time_to_iso(parsed)
        if iso:
            return iso
    for key in ("published", "updated", "created"):
        parsed_dt = parse_datetime(str(entry.get(key, "") or ""))
        if parsed_dt is not None:
            return parsed_dt.isoformat()
    return ""


def pick_published_text(entry: Any) -> str:
    for key in ("published", "updated", "created"):
        text = clean_text(entry.get(key, ""))
        if text:
            return text
    return ""


def extract_summary(entry: Any) -> str:
    for key in ("summary", "description", "subtitle"):
        text = clean_text(entry.get(key, ""))
        if text:
            return text
    content = entry.get("content", [])
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return clean_text(first.get("value", ""))
    return ""


def build_item(source: Source, title: str, link: str, summary: str = "", published: str = "", published_at: str = "") -> dict[str, str]:
    normalized_link = normalize_link(link)
    normalized_title = normalize_title(title)
    return {
        "source": source.name,
        "title": clean_text(title) or "(untitled)",
        "link": link.strip(),
        "normalized_link": normalized_link,
        "normalized_title": normalized_title,
        "summary": clean_text(summary),
        "published": clean_text(published),
        "published_at": published_at.strip(),
        "id": build_entry_id(source.name, title, normalized_link or link),
        "hint_text": " ".join(source.category_hints),
    }


def build_http_session() -> requests.Session:
    retry = Retry(
        total=2,
        read=2,
        connect=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def fetch_rss_feed(source: Source, session: requests.Session, *, timeout: float = TIMEOUT_SECONDS) -> tuple[list[dict[str, str]], dict[str, int]]:
    if source.type != "rss":
        raise ValueError(f"Unsupported source type for {source.name}: {source.type}")
    response = session.get(source.url, timeout=timeout)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    entries = list(parsed.entries or [])
    if getattr(parsed, "bozo", False) and not entries:
        raise ValueError(f"Feed parse failed for {source.name}: {getattr(parsed, 'bozo_exception', 'unknown parse error')}")

    items = [
        build_item(
            source,
            title=entry.get("title", ""),
            link=entry.get("link", ""),
            summary=extract_summary(entry),
            published=pick_published_text(entry),
            published_at=pick_published_at(entry),
        )
        for entry in entries
        if clean_text(entry.get("title", "")) and str(entry.get("link", "")).strip()
    ]
    return items, {"raw_fetched": len(entries)}


def derive_html_fallback_url(source: Source) -> str:
    if source.site_url:
        return source.site_url
    parts = urlsplit(source.url)
    path = parts.path
    for suffix in ("/feed/", "/feed", "/rss/", "/rss", "/rss.xml", "/feed.xml", "/index.xml"):
        if path.endswith(suffix):
            path = path[: -len(suffix)] or "/"
            break
    return urlunsplit((parts.scheme, parts.netloc, path or "/", "", ""))


def resolve_html_fallback_selector_groups(source: Source) -> tuple[tuple[str, str], ...]:
    if not source.html_fallback_selector_groups:
        return HTML_FALLBACK_SELECTOR_GROUPS
    return tuple(
        (name, HTML_FALLBACK_SELECTOR_GROUPS_BY_NAME[name])
        for name in source.html_fallback_selector_groups
    )


def _find_anchor(candidate: Any, selector_name: str) -> Any | None:
    if selector_name == "heading_link" and getattr(candidate, "name", "") == "a":
        return candidate
    for selector in ("h1 a[href]", "h2 a[href]", "h3 a[href]", "a[href]"):
        found = candidate.select_one(selector) if hasattr(candidate, "select_one") else None
        if found is not None:
            return found
    return None


def _summary_from_html_candidate(candidate: Any, anchor: Any) -> str:
    if not hasattr(candidate, "select"):
        return ""
    paragraphs = [clean_text(p.get_text(" ", strip=True)) for p in candidate.select("p")]
    anchor_text = clean_text(anchor.get_text(" ", strip=True))
    usable = [p for p in paragraphs if p and p != anchor_text]
    return usable[0] if usable else ""


def fetch_html_fallback(source: Source, session: requests.Session, *, timeout: float = TIMEOUT_SECONDS) -> tuple[list[dict[str, str]], dict[str, Any]]:
    fallback_url = derive_html_fallback_url(source)
    response = session.get(fallback_url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    base_host = urlsplit(fallback_url).netloc.casefold()
    selected_group = ""
    items: list[dict[str, str]] = []

    for selector_name, selector in resolve_html_fallback_selector_groups(source):
        seen_links: set[str] = set()
        group_items: list[dict[str, str]] = []
        for candidate in soup.select(selector):
            anchor = _find_anchor(candidate, selector_name)
            if anchor is None:
                continue
            raw_link = str(anchor.get("href", "")).strip()
            title = clean_text(anchor.get_text(" ", strip=True))
            if not raw_link or not title:
                continue
            link = urljoin(fallback_url, raw_link)
            if source.same_host_only and urlsplit(link).netloc.casefold() != base_host:
                continue
            normalized_link = normalize_link(link)
            if not normalized_link or normalized_link in seen_links:
                continue
            seen_links.add(normalized_link)
            group_items.append(
                build_item(
                    source,
                    title=title,
                    link=link,
                    summary=_summary_from_html_candidate(candidate, anchor),
                    published="",
                    published_at="",
                )
            )
        if group_items:
            selected_group = selector_name
            items = group_items
            break

    return items, {
        "fallback_url": fallback_url,
        "selector_group": selected_group,
        "raw_fetched": len(items),
    }


def filter_source_items(items: list[dict[str, str]], source: Source, *, now: datetime | None = None) -> list[dict[str, str]]:
    reference_now = now or datetime.now(UTC)
    cutoff = reference_now - timedelta(days=source.recent_days)
    filtered: list[dict[str, str]] = []
    seen_links: set[str] = set()
    seen_titles: set[str] = set()

    for item in items:
        published_at = parse_datetime(item.get("published_at", ""))
        if published_at is not None and published_at < cutoff:
            continue
        normalized_link = item.get("normalized_link", "")
        normalized_title = item.get("normalized_title", "")
        if normalized_link and normalized_link in seen_links:
            continue
        if normalized_title and normalized_title in seen_titles:
            continue
        if normalized_link:
            seen_links.add(normalized_link)
        if normalized_title:
            seen_titles.add(normalized_title)
        filtered.append(item)
        if len(filtered) >= source.max_items:
            break
    return filtered


def fetch_all_sources(sources: Sequence[Source], *, timeout: float = TIMEOUT_SECONDS) -> tuple[list[dict[str, str]], list[str], dict[str, SourceFetchResult]]:
    fetched_items: list[dict[str, str]] = []
    errors: list[str] = []
    stats_by_source: dict[str, SourceFetchResult] = {}

    with build_http_session() as session:
        for source in sources:
            try:
                rss_items, rss_meta = fetch_rss_feed(source, session, timeout=timeout)
                if not rss_items:
                    raise ValueError("RSS returned no usable entries")
                filtered = filter_source_items(rss_items, source)
                fetched_items.extend(filtered)
                stats_by_source[source.name] = SourceFetchResult(
                    name=source.name,
                    status="ok",
                    fetch_method="rss",
                    raw_fetched=int(rss_meta.get("raw_fetched", len(rss_items))),
                    after_filter=len(filtered),
                )
                continue
            except Exception as exc:  # noqa: BLE001
                rss_error = f"{type(exc).__name__}: {exc}"

            if not source.html_fallback_enabled:
                errors.append(f"{source.name}: {rss_error}")
                stats_by_source[source.name] = SourceFetchResult(
                    name=source.name,
                    status="failed",
                    fetch_method="rss",
                    raw_fetched=0,
                    after_filter=0,
                    reason=rss_error,
                )
                continue

            try:
                fallback_items, fallback_meta = fetch_html_fallback(source, session, timeout=timeout)
                filtered = filter_source_items(fallback_items, source)
                fetched_items.extend(filtered)
                used_fallback = bool(filtered)
                if not used_fallback:
                    errors.append(f"{source.name}: RSS failed and HTML fallback returned no items ({rss_error})")
                stats_by_source[source.name] = SourceFetchResult(
                    name=source.name,
                    status="ok_html_fallback" if used_fallback else "failed",
                    fetch_method="html_fallback",
                    raw_fetched=int(fallback_meta.get("raw_fetched", len(fallback_items))),
                    after_filter=len(filtered),
                    reason=f"RSS failed: {rss_error}",
                    fallback_attempted=True,
                    fallback_selector=str(fallback_meta.get("selector_group", "")),
                    used_fallback=used_fallback,
                )
            except Exception as fallback_exc:  # noqa: BLE001
                reason = f"RSS failed: {rss_error}; HTML fallback failed: {type(fallback_exc).__name__}: {fallback_exc}"
                errors.append(f"{source.name}: {reason}")
                stats_by_source[source.name] = SourceFetchResult(
                    name=source.name,
                    status="failed",
                    fetch_method="rss",
                    raw_fetched=0,
                    after_filter=0,
                    reason=reason,
                    fallback_attempted=True,
                )
    return fetched_items, errors, stats_by_source


def dedupe_items(items: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    for item in items:
        normalized_link = item.get("normalized_link", "")
        normalized_title = item.get("normalized_title", "")
        if normalized_link and normalized_link in seen_links:
            continue
        if normalized_title and normalized_title in seen_titles:
            continue
        if normalized_link:
            seen_links.add(normalized_link)
        if normalized_title:
            seen_titles.add(normalized_title)
        deduped.append(dict(item))
    return deduped


def keyword_matches(text: str, keyword: str) -> bool:
    normalized_keyword = keyword.strip().casefold()
    if not normalized_keyword:
        return False
    normalized_text = text.casefold()
    if re.search(r"[^a-z0-9_ ]", normalized_keyword) or " " in normalized_keyword:
        return normalized_keyword in normalized_text
    return re.search(r"\b" + re.escape(normalized_keyword) + r"\b", normalized_text) is not None


def find_matching_keywords(text: str, keywords: set[str] | Sequence[str], limit: int = 5) -> list[str]:
    matched = [keyword for keyword in sorted(keywords, key=str.casefold) if keyword_matches(text, keyword)]
    return matched[:limit]


def build_classification_text(item: Mapping[str, Any]) -> str:
    return " ".join(
        [
            str(item.get("title", "")),
            str(item.get("summary", "")),
        ]
    ).casefold()


def build_classification_hint_text(item: Mapping[str, Any]) -> str:
    return " ".join(
        [
            str(item.get("hint_text", "")),
            str(item.get("source", "")),
        ]
    ).casefold()


def build_classification_decision(item: Mapping[str, Any]) -> ClassificationDecision:
    text = build_classification_text(item)
    hint_text = build_classification_hint_text(item)
    combined = f"{text} {hint_text}"
    scores = {"A": 0, "B": 0, "C": 0}
    reasons = {"A": [], "B": [], "C": []}

    a_matches = find_matching_keywords(combined, A_KEYWORDS)
    b_matches = find_matching_keywords(combined, B_KEYWORDS)
    c_matches = find_matching_keywords(text, C_KEYWORDS)

    if a_matches:
        scores["A"] += 2 + min(len(a_matches), 3)
        reasons["A"].append("network protocol/security/stability keywords: " + ", ".join(a_matches))
    if b_matches:
        scores["B"] += 1 + min(len(b_matches), 3)
        reasons["B"].append("operations/tooling/cloud networking keywords: " + ", ".join(b_matches))
    if c_matches:
        scores["C"] += 1 + min(len(c_matches), 3)
        reasons["C"].append("event/company/update keywords: " + ", ".join(c_matches))

    title = str(item.get("title", "")).casefold()
    if find_matching_keywords(title, {"outage", "route leak", "ddos", "vulnerability", "cve", "dnssec", "rpki"}):
        scores["A"] += 3
        reasons["A"].append("high-signal keyword appears in title")
    if find_matching_keywords(title, {"how to", "best practice", "observability", "automation", "case study"}):
        scores["B"] += 2
        reasons["B"].append("implementation or operations signal appears in title")
    if scores["A"] == 0 and scores["B"] == 0:
        scores["C"] += 1
        reasons["C"].append("no strong network priority signal")

    if scores["C"] >= 3 and scores["A"] <= 2 and scores["B"] <= 2:
        bucket = "C"
    elif scores["A"] >= scores["B"] and scores["A"] > 0:
        bucket = "A"
    elif scores["B"] > 0:
        bucket = "B"
    else:
        bucket = "C"

    return ClassificationDecision(bucket=bucket, scores=scores, reasons=reasons)


def classify_item(item: Mapping[str, Any]) -> str:
    return build_classification_decision(item).bucket


def _find_source_override(rule: WatchRule, normalized_source: str) -> WatchRuleSourceOverride | None:
    for override in rule.source_overrides:
        if override.source.casefold() == normalized_source:
            return override
    return None


def build_watch_search_text(item: Mapping[str, Any]) -> str:
    return " ".join(
        [
            str(item.get("title", "")),
            str(item.get("summary", "")),
            str(item.get("source", "")),
            str(item.get("category", "")),
        ]
    ).casefold()


def evaluate_watch_rules(item: Mapping[str, Any], rules: Sequence[WatchRule]) -> WatchEvaluation:
    if not rules:
        return WatchEvaluation()

    normalized_source = str(item.get("source", "")).strip().casefold()
    normalized_category = str(item.get("category", "")).strip().upper()
    search_text = build_watch_search_text(item)
    matched_rules: list[WatchMatch] = []

    for rule in rules:
        override = _find_source_override(rule, normalized_source)
        allowed_sources = {source.casefold() for source in rule.sources}
        allowed_sources.update(item.source.casefold() for item in rule.source_overrides)
        if allowed_sources and normalized_source not in allowed_sources:
            continue

        categories = override.categories if override and override.categories else rule.categories
        if categories and normalized_category not in {category.upper() for category in categories}:
            continue

        keywords = override.keywords if override and override.keywords else rule.keywords
        if keywords and not any(keyword_matches(search_text, keyword) for keyword in keywords):
            continue

        exclude_keywords = list(rule.exclude_keywords)
        if override and override.exclude_keywords:
            exclude_keywords.extend(override.exclude_keywords)
        if exclude_keywords and any(keyword_matches(search_text, keyword) for keyword in exclude_keywords):
            continue

        matched_rules.append(
            WatchMatch(
                name=rule.name,
                priority_level=rule.priority_level,
                notify=rule.notify,
            )
        )

    if not matched_rules:
        return WatchEvaluation()

    notify_names = tuple(match.name for match in matched_rules if match.notify)
    highest_priority_level = max((match.priority_level for match in matched_rules), default=0)
    return WatchEvaluation(
        names=tuple(match.name for match in matched_rules),
        notify_names=notify_names,
        highest_priority_level=highest_priority_level,
        should_notify=bool(notify_names),
        matches=tuple(matched_rules),
    )


def serialize_watch_matches(matches: Sequence[WatchMatch | Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for match in matches:
        if isinstance(match, WatchMatch):
            result.append(
                {
                    "name": match.name,
                    "priority_level": int(match.priority_level),
                    "notify": bool(match.notify),
                }
            )
        else:
            result.append(
                {
                    "name": str(match.get("name", "")).strip(),
                    "priority_level": int(match.get("priority_level", 0) or 0),
                    "notify": bool(match.get("notify", False)),
                }
            )
    return [item for item in result if item["name"]]


def format_watch_match_labels(matches: Sequence[WatchMatch | Mapping[str, Any]]) -> list[str]:
    labels: list[str] = []
    for match in serialize_watch_matches(matches):
        label = f"{match['name']} (L{match['priority_level']}"
        if match["notify"]:
            label += " notify"
        label += ")"
        labels.append(label)
    return labels


def build_archive_record(item: Mapping[str, Any], collected_at: datetime) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")),
        "collected_at": collected_at.isoformat(),
        "source": str(item.get("source", "")),
        "title": str(item.get("title", "")),
        "link": str(item.get("link", "")),
        "normalized_link": str(item.get("normalized_link", "")),
        "summary": str(item.get("summary", "")),
        "published": str(item.get("published", "")),
        "published_at": str(item.get("published_at", "")),
        "category": str(item.get("category", "")),
        "category_reasons": list(item.get("category_reasons", [])),
        "watch_hits": list(item.get("watch_hits", [])),
        "watch_matches": list(item.get("watch_matches", [])),
        "watch_notify_hits": list(item.get("watch_notify_hits", [])),
        "watch_priority_level": int(item.get("watch_priority_level", 0) or 0),
        "watch_notify": bool(item.get("watch_notify", False)),
    }


def load_archive_ids(archive_dir: Path) -> set[str]:
    ids: set[str] = set()
    if not archive_dir.exists():
        return ids
    for path in archive_dir.glob("articles-*.jsonl"):
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
                item_id = str(parsed.get("id", "")).strip()
                if item_id:
                    ids.add(item_id)
    return ids


def append_archive_records(path: Path, records: Sequence[dict[str, Any]], *, existing_ids: set[str] | None = None) -> int:
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    seen_ids = set(existing_ids or set())
    lines: list[str] = []
    for record in records:
        item_id = str(record.get("id", "")).strip()
        if item_id and item_id in seen_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    if not lines:
        return 0
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return len(lines)


def build_source_log(
    sources: Sequence[Source],
    new_items: Sequence[Mapping[str, Any]],
    stats_by_source: Mapping[str, SourceFetchResult],
) -> dict[str, dict[str, Any]]:
    new_counts: dict[str, int] = {}
    for item in new_items:
        source_name = str(item.get("source", ""))
        new_counts[source_name] = new_counts.get(source_name, 0) + 1

    summary: dict[str, dict[str, Any]] = {}
    for source in sources:
        stats = stats_by_source.get(source.name)
        summary[source.name] = {
            "status": stats.status if stats else "failed",
            "fetch_method": stats.fetch_method if stats else "rss",
            "fallback_attempted": bool(stats.fallback_attempted) if stats else False,
            "fallback_selector": stats.fallback_selector if stats else "",
            "used_fallback": bool(stats.used_fallback) if stats else False,
            "reason": stats.reason if stats else "No result recorded",
            "raw_fetched": int(stats.raw_fetched) if stats else 0,
            "fetched": int(stats.after_filter) if stats else 0,
            "new": new_counts.get(source.name, 0),
        }
    return summary


def merge_seen_ids(previous_ids: Sequence[str], items: Sequence[Mapping[str, Any]], max_items: int = MAX_SEEN_IDS) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in items:
        item_id = str(item.get("id", "")).strip()
        if not item_id or item_id in seen:
            continue
        merged.append(item_id)
        seen.add(item_id)
        if len(merged) >= max_items:
            return merged
    for item_id in previous_ids:
        normalized = str(item_id).strip()
        if not normalized or normalized in seen:
            continue
        merged.append(normalized)
        seen.add(normalized)
        if len(merged) >= max_items:
            break
    return merged


def get_display_title(item: Mapping[str, Any]) -> str:
    return str(item.get("title", "")).strip() or "(untitled)"


def get_display_summary(item: Mapping[str, Any]) -> str:
    return str(item.get("summary", "")).strip()


def render_source_table(source_log: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = [
        "| Source | Status | New | Fetched | Raw | Fallback |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for source_name, stats in source_log.items():
        fallback = "yes" if stats.get("used_fallback") else ("attempted" if stats.get("fallback_attempted") else "-")
        reason = str(stats.get("reason", "")).strip()
        if reason and stats.get("status") == "failed":
            fallback += f" ({reason[:80]})"
        lines.append(
            f"| {source_name} | {stats.get('status', '-')} | {int(stats.get('new', 0) or 0)} | "
            f"{int(stats.get('fetched', 0) or 0)} | {int(stats.get('raw_fetched', 0) or 0)} | {fallback} |"
        )
    return lines


def render_item_lines(items: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        title = get_display_title(item)
        link = str(item.get("link", "")).strip()
        source = str(item.get("source", "")).strip() or "-"
        published = str(item.get("published", "")).strip()
        meta = source + (f" / {published}" if published else "")
        if link:
            lines.append(f"- [{title}]({link}) - {meta}")
        else:
            lines.append(f"- {title} - {meta}")
        watch_labels = format_watch_match_labels(item.get("watch_matches", []))
        if watch_labels:
            lines.append(f"  - Priority: {', '.join(watch_labels)}")
        reasons = [str(reason) for reason in item.get("category_reasons", []) if str(reason).strip()]
        if reasons:
            lines.append(f"  - Reason: {'; '.join(reasons[:2])}")
        summary = get_display_summary(item)
        if summary:
            lines.append(f"  - Summary: {summary[:240]}")
    return lines


def render_report(
    path: Path,
    categorized: Mapping[str, Sequence[Mapping[str, Any]]],
    counts_before_limit: Mapping[str, int],
    counts_displayed: Mapping[str, int],
    total_new_before_limit: int,
    total_new_displayed: int,
    errors: Sequence[str],
    source_log: Mapping[str, Mapping[str, Any]],
    *,
    priority_items: Sequence[Mapping[str, Any]] = (),
) -> None:
    failed_sources = sum(1 for stats in source_log.values() if stats.get("status") == "failed")
    fallback_attempted = sum(1 for stats in source_log.values() if stats.get("fallback_attempted"))
    fallback_succeeded = sum(1 for stats in source_log.values() if stats.get("used_fallback"))
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    displayed_suffix = f" (displayed: {total_new_displayed})" if total_new_displayed != total_new_before_limit else ""
    counts_suffix = (
        f" (displayed: {counts_displayed.get('A', 0)} / {counts_displayed.get('B', 0)} / {counts_displayed.get('C', 0)})"
        if dict(counts_before_limit) != dict(counts_displayed)
        else ""
    )

    lines = [
        "# Network Tech Watch Report",
        "",
        f"- Generated at: **{now}**",
        f"- New items: **{total_new_before_limit}**{displayed_suffix}",
        f"- A / B / C: **{counts_before_limit.get('A', 0)} / {counts_before_limit.get('B', 0)} / {counts_before_limit.get('C', 0)}**{counts_suffix}",
        f"- Priority hits: **{len(priority_items)}**",
        f"- Sources failed: **{failed_sources}**",
        f"- HTML fallback: **attempted {fallback_attempted}, succeeded {fallback_succeeded}**",
        "",
    ]

    lines.append("## Priority Hits")
    lines.append("")
    if priority_items:
        lines.extend(render_item_lines(priority_items))
    else:
        lines.append("- No priority hits this run.")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    if not errors:
        lines.append("- No source errors.")
    else:
        lines.extend(f"- {error}" for error in errors)
    lines.append("")

    lines.append("## Source Breakdown")
    lines.append("")
    lines.extend(render_source_table(source_log))
    lines.append("")

    for category in ("A", "B", "C"):
        items = list(categorized.get(category, []))
        lines.append(f"## {category} ({len(items)})")
        lines.append("")
        if items:
            lines.extend(render_item_lines(items))
        else:
            lines.append("- No new items.")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def render_digest(
    path: Path,
    categorized: Mapping[str, Sequence[Mapping[str, Any]]],
    counts_before_limit: Mapping[str, int],
    total_new_before_limit: int,
    source_log: Mapping[str, Mapping[str, Any]],
    *,
    priority_items: Sequence[Mapping[str, Any]] = (),
) -> None:
    failed_sources = sum(1 for stats in source_log.values() if stats.get("status") == "failed")
    fallback_attempted = sum(1 for stats in source_log.values() if stats.get("fallback_attempted"))
    fallback_succeeded = sum(1 for stats in source_log.values() if stats.get("used_fallback"))
    lines = [
        "# Network Tech Watch Digest",
        "",
        f"- New items: **{total_new_before_limit}**",
        f"- A / B / C: **{counts_before_limit.get('A', 0)} / {counts_before_limit.get('B', 0)} / {counts_before_limit.get('C', 0)}**",
        f"- Priority hits: **{len(priority_items)}**",
        f"- Sources failed: **{failed_sources}**",
        f"- HTML fallback: **attempted {fallback_attempted}, succeeded {fallback_succeeded}**",
        "",
    ]
    if total_new_before_limit == 0 and failed_sources == 0:
        lines.append("- No new items this run.")
        lines.append("")
    if priority_items:
        lines.append("## Priority Hits")
        lines.append("")
        lines.extend(render_item_lines(priority_items[:5]))
        lines.append("")
    for category in ("A", "B", "C"):
        items = list(categorized.get(category, []))
        if not items:
            continue
        lines.append(f"## {category}")
        lines.append("")
        for item in items[:5]:
            title = get_display_title(item)
            link = str(item.get("link", "")).strip()
            source = str(item.get("source", "")).strip() or "-"
            lines.append(f"- [{title}]({link}) - {source}" if link else f"- {title} - {source}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def save_digest_meta(
    path: Path,
    categorized: Mapping[str, Sequence[Mapping[str, Any]]],
    counts_before_limit: Mapping[str, int],
    source_log: Mapping[str, Mapping[str, Any]],
    *,
    priority_items: Sequence[Mapping[str, Any]] = (),
) -> None:
    failed_source_names = [
        name for name, stats in source_log.items()
        if stats.get("status") == "failed"
    ]
    fallback_rescued_names = [
        name for name, stats in source_log.items()
        if stats.get("used_fallback")
    ]
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "new_items": int(sum(counts_before_limit.get(category, 0) for category in ("A", "B", "C"))),
        "counts": {category: int(counts_before_limit.get(category, 0)) for category in ("A", "B", "C")},
        "displayed_counts": {category: len(categorized.get(category, [])) for category in ("A", "B", "C")},
        "failed_sources": len(failed_source_names),
        "failed_source_names": failed_source_names,
        "fallback_attempted": sum(1 for stats in source_log.values() if stats.get("fallback_attempted")),
        "fallback_succeeded": len(fallback_rescued_names),
        "fallback_rescued_names": fallback_rescued_names,
        "priority_hits": len(priority_items),
        "priority_notify_hits": sum(1 for item in priority_items if item.get("watch_notify")),
        "highest_priority_level": max((int(item.get("watch_priority_level", 0) or 0) for item in priority_items), default=0),
        "source_log": dict(source_log),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def enrich_new_item(item: Mapping[str, Any], rules: Sequence[WatchRule]) -> dict[str, Any]:
    decision = build_classification_decision(item)
    category_reasons = decision.reasons.get(decision.bucket, [])
    base = {**dict(item), "category": decision.bucket, "category_reasons": list(category_reasons)}
    watch_evaluation = evaluate_watch_rules(base, rules)
    return {
        **base,
        "watch_hits": list(watch_evaluation.names),
        "watch_matches": serialize_watch_matches(watch_evaluation.matches),
        "watch_notify_hits": list(watch_evaluation.notify_names),
        "watch_priority_level": watch_evaluation.highest_priority_level,
        "watch_notify": watch_evaluation.should_notify,
    }


def run_watch(
    *,
    config: Path = CONFIG_PATH,
    state: Path = STATE_PATH,
    archive_dir: Path = ARCHIVE_DIR,
    report: Path = REPORT_PATH,
    digest: Path = DIGEST_PATH,
    digest_meta: Path = DIGEST_META_PATH,
    limit: int = 5,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    sources = load_sources(config)
    watch_rules = load_watch_rules(config)
    previous_state = load_state(state)
    previous_seen_ids = [str(item_id) for item_id in previous_state.get("seen_ids", [])]
    seen_ids = set(previous_seen_ids)

    fetched_items, errors, stats_by_source = fetch_all_sources(sources, timeout=timeout)
    unique_items = dedupe_items(fetched_items)
    new_items = [item for item in unique_items if item.get("id") not in seen_ids]
    enriched_items = [enrich_new_item(item, watch_rules) for item in new_items]

    categorized_before_limit: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for item in enriched_items:
        category = str(item.get("category", "C")).upper()
        if category not in categorized_before_limit:
            category = "C"
        categorized_before_limit[category].append(item)

    counts_before_limit = {category: len(items) for category, items in categorized_before_limit.items()}
    categorized_displayed = {
        category: items[: max(0, int(limit))]
        for category, items in categorized_before_limit.items()
    }
    counts_displayed = {category: len(items) for category, items in categorized_displayed.items()}
    total_new_before_limit = len(enriched_items)
    total_new_displayed = sum(counts_displayed.values())
    priority_items = [item for item in enriched_items if item.get("watch_hits")]
    source_log = build_source_log(sources, enriched_items, stats_by_source)

    collected_at = datetime.now(UTC)
    archive_path = resolve_archive_path(archive_dir, collected_at)
    archive_records = [build_archive_record(item, collected_at) for item in enriched_items]
    archived_count = append_archive_records(
        archive_path,
        archive_records,
        existing_ids=load_archive_ids(archive_dir),
    )

    render_report(
        report,
        categorized_displayed,
        counts_before_limit,
        counts_displayed,
        total_new_before_limit,
        total_new_displayed,
        errors,
        source_log,
        priority_items=priority_items,
    )
    render_digest(
        digest,
        categorized_displayed,
        counts_before_limit,
        total_new_before_limit,
        source_log,
        priority_items=priority_items,
    )
    save_digest_meta(
        digest_meta,
        categorized_displayed,
        counts_before_limit,
        source_log,
        priority_items=priority_items,
    )

    merged_seen_ids = merge_seen_ids(previous_seen_ids, unique_items)
    save_state(state, merged_seen_ids)

    failed_sources = sum(1 for stats in source_log.values() if stats.get("status") == "failed")
    exit_code = 1 if failed_sources == len(sources) and sources else 0
    run_state = "total_failure_run" if exit_code else ("partial_failure_run" if failed_sources else ("new_items_run" if total_new_before_limit else "quiet_run"))

    return {
        "run_state": run_state,
        "run_state_reason": (
            "all configured sources failed"
            if run_state == "total_failure_run"
            else f"new={total_new_before_limit}, failed_sources={failed_sources}"
        ),
        "new_items_count": total_new_before_limit,
        "failed_sources": failed_sources,
        "fallback_attempted": sum(1 for stats in source_log.values() if stats.get("fallback_attempted")),
        "fallback_succeeded": sum(1 for stats in source_log.values() if stats.get("used_fallback")),
        "priority_hits": len(priority_items),
        "priority_notify_hits": sum(1 for item in priority_items if item.get("watch_notify")),
        "highest_priority_level": max((int(item.get("watch_priority_level", 0) or 0) for item in priority_items), default=0),
        "exit_code": exit_code,
        "error": "",
        "raw_json": {
            "sources": len(sources),
            "fetched": len(unique_items),
            "new": total_new_before_limit,
            "errors": len(errors),
            "archived": archived_count,
            "priority_hits": len(priority_items),
            "per_source": source_log,
            "state": str(state),
            "archive": str(archive_path),
            "report": str(report),
            "digest": str(digest),
            "digest_meta": str(digest_meta),
            "exit_code": exit_code,
        },
    }


def main() -> int:
    args = parse_args()
    digest_meta_path = resolve_digest_meta_path(args.digest, args.digest_meta)
    result = run_watch(
        config=args.config,
        state=args.state,
        archive_dir=args.archive_dir,
        report=args.report,
        digest=args.digest,
        digest_meta=digest_meta_path,
        limit=args.limit,
        timeout=args.timeout,
    )
    print(json.dumps(result["raw_json"], ensure_ascii=False))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
