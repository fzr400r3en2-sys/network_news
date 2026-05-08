from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.dashboard_helpers import (  # noqa: E402
    load_archive_summary,
    load_digest_info,
    load_recent_articles,
    load_state_info,
    select_morning_articles,
)
from app.watch_runner import is_running, run_once  # noqa: E402
from scripts.watch_network_news import (  # noqa: E402
    ARCHIVE_DIR,
    CONFIG_PATH,
    DIGEST_META_PATH,
    DIGEST_PATH,
    REPORT_PATH,
    STATE_PATH,
)


SUMMARY_PATH = ROOT_DIR / "reports" / "archive_summary.json"


def _render_article_list(st, articles, *, limit: int = 10) -> None:
    if not articles:
        st.info("No articles to show yet.")
        return
    for article in articles[:limit]:
        title = str(article.get("title", "") or "(untitled)")
        link = str(article.get("link", "") or "")
        source = str(article.get("source", "") or "-")
        category = str(article.get("category", "") or "-")
        watch_hits = article.get("watch_hits") or []
        heading = f"[{title}]({link})" if link else title
        st.markdown(f"**{heading}**")
        meta = f"`{category}` `{source}`"
        if watch_hits:
            meta += " " + " ".join(f"`{hit}`" for hit in watch_hits)
        st.markdown(meta)
        summary = str(article.get("summary", "") or "")
        if summary:
            st.caption(summary[:260])
        st.divider()


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Network Tech Watch", layout="wide")
    st.title("Network Tech Watch")

    digest = load_digest_info(DIGEST_PATH, DIGEST_META_PATH)
    state = load_state_info(STATE_PATH)
    archive_summary = load_archive_summary(SUMMARY_PATH)
    archive = archive_summary.get("archive", {})
    counts = archive.get("counts", {}) if isinstance(archive, dict) else {}

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("New Items", digest["new_items"])
    col2.metric("Priority Hits", digest["priority_hits"])
    col3.metric("Source Failures", digest["failed_sources"])
    col4.metric("Archive Total", archive.get("total_items", 0) if isinstance(archive, dict) else 0)

    col_a, col_b, col_c, col_state = st.columns(4)
    col_a.metric("A", counts.get("A", 0) if isinstance(counts, dict) else 0)
    col_b.metric("B", counts.get("B", 0) if isinstance(counts, dict) else 0)
    col_c.metric("C", counts.get("C", 0) if isinstance(counts, dict) else 0)
    col_state.metric("Seen IDs", state["seen_count"])

    if is_running():
        st.warning("A collection run is already in progress.")
    elif st.button("Run Collection", type="primary"):
        with st.spinner("Collecting network technology news..."):
            result = run_once(
                config=CONFIG_PATH,
                state=STATE_PATH,
                archive_dir=ARCHIVE_DIR,
                report=REPORT_PATH,
                digest=DIGEST_PATH,
                digest_meta=DIGEST_META_PATH,
            )
        if result.get("status") == "completed" and result.get("exit_code") == 0:
            st.success(f"Collection completed: {result.get('new_items_count', 0)} new item(s).")
        else:
            st.error(f"Collection did not complete cleanly: {result.get('error') or result.get('run_state_reason')}")
        st.rerun()

    tab_overview, tab_recent, tab_priority, tab_digest = st.tabs(
        ["Overview", "Recent", "Priority", "Digest"]
    )

    with tab_overview:
        st.subheader("Morning Picks")
        articles = load_recent_articles(ARCHIVE_DIR, limit=100)
        _render_article_list(st, select_morning_articles(articles, limit=5), limit=5)

    with tab_recent:
        st.subheader("Recent Archive")
        query = st.text_input("Search", key="recent_query")
        category = st.selectbox("Category", ["", "A", "B", "C"], format_func=lambda value: value or "All")
        articles = load_recent_articles(ARCHIVE_DIR, limit=200)
        if category:
            articles = [article for article in articles if str(article.get("category", "")) == category]
        if query.strip():
            q = query.strip().casefold()
            articles = [
                article
                for article in articles
                if q in " ".join(
                    [
                        str(article.get("title", "")),
                        str(article.get("summary", "")),
                        str(article.get("source", "")),
                        " ".join(str(hit) for hit in article.get("watch_hits", [])),
                    ]
                ).casefold()
            ]
        _render_article_list(st, articles, limit=30)

    with tab_priority:
        st.subheader("Priority Hits")
        priority_hits = archive_summary.get("priority_hits", [])
        _render_article_list(st, priority_hits if isinstance(priority_hits, list) else [], limit=30)

    with tab_digest:
        st.subheader("Latest Digest")
        if digest["text"]:
            st.markdown(digest["text"])
        else:
            st.info("No digest has been generated yet.")


if __name__ == "__main__":
    main()
