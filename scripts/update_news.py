#!/usr/bin/env python3
"""
Fetch recent publications from Semantic Scholar and update the news section
of index.html. Merges auto-discovered papers with manual news entries from
data/news.json.

Usage:
    python scripts/update_news.py [--dry-run]
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Semantic Scholar author IDs (fragmented profiles)
AUTHOR_IDS = ["2275825472", "1944920685", "2127367730", "2283043684"]
S2_API = "https://api.semanticscholar.org/graph/v1"

ROOT = Path(__file__).resolve().parent.parent
NEWS_JSON = ROOT / "data" / "news.json"
INDEX_HTML = ROOT / "index.html"

# Markers in index.html
MARKER_START = "<!-- NEWS_START -->"
MARKER_END = "<!-- NEWS_END -->"


def fetch_papers() -> list[dict]:
    """Fetch all papers from Semantic Scholar for the known author IDs."""
    papers = {}
    fields = "title,publicationDate,year,externalIds,url,venue"

    for author_id in AUTHOR_IDS:
        url = f"{S2_API}/author/{author_id}/papers?fields={fields}&limit=100"
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "edyoshikun-website-updater/1.0")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                for paper in data.get("data", []):
                    pid = paper.get("paperId")
                    if pid and pid not in papers:
                        papers[pid] = paper
        except urllib.error.URLError as e:
            print(f"Warning: Failed to fetch papers for author {author_id}: {e}")

    return list(papers.values())


def paper_to_news_item(paper: dict) -> dict | None:
    """Convert a Semantic Scholar paper to a news item dict."""
    title = paper.get("title", "").strip()
    if not title:
        return None

    pub_date = paper.get("publicationDate")
    year = paper.get("year")
    if pub_date:
        date_str = pub_date
    elif year:
        date_str = f"{year}-01-01"
    else:
        return None

    # Determine best URL: DOI > ArXiv > S2 URL
    ext_ids = paper.get("externalIds", {}) or {}
    doi = ext_ids.get("DOI")
    arxiv = ext_ids.get("ArXiv")

    if doi:
        url = f"https://doi.org/{doi}"
    elif arxiv:
        url = f"https://arxiv.org/abs/{arxiv}"
    else:
        url = paper.get("url")

    venue = paper.get("venue", "")
    if venue:
        text = f'Our paper "{title}" published in {venue}'
    else:
        text = f'Our paper "{title}" is available'

    return {
        "date": date_str,
        "text": text,
        "url": url,
        "source": "semantic_scholar",
        "paper_id": paper.get("paperId"),
    }


def load_news() -> list[dict]:
    """Load manual news items from data/news.json."""
    if NEWS_JSON.exists():
        return json.loads(NEWS_JSON.read_text())
    return []


def normalize_doi(doi: str) -> str:
    """Normalize a DOI: lowercase, strip version suffixes, trailing slashes."""
    doi = doi.lower().rstrip("/")
    # Strip biorxiv/medrxiv version suffixes (e.g., v1, v2, v3)
    doi = re.sub(r"v\d+$", "", doi)
    return doi


def extract_dois(url: str) -> set[str]:
    """Extract all DOI-like patterns from a URL and return normalized versions."""
    if not url:
        return set()
    dois = set()
    # Find all DOI patterns in the URL
    for m in re.finditer(r"(10\.\d{4,9}/[^\s,;]+)", url):
        raw = m.group(1)
        normalized = normalize_doi(raw)
        dois.add(normalized)
        # Also add progressively shorter versions (strip trailing /segments)
        # This handles cases like .../10.1093/pnasnexus/pgae323/7731083
        # where the DOI is actually 10.1093/pnasnexus/pgae323
        parts = normalized.split("/")
        for i in range(2, len(parts)):
            dois.add("/".join(parts[:i]))

    # Handle Nature URLs: nature.com/articles/XXXXX -> DOI 10.1038/XXXXX
    m = re.search(r"nature\.com/articles/(s[\w.-]+)", url)
    if m:
        doi = normalize_doi(f"10.1038/{m.group(1)}")
        dois.add(doi)

    # Handle arxiv URLs: arxiv.org/abs/XXXXX -> DOI 10.48550/arXiv.XXXXX
    m = re.search(r"arxiv\.org/abs/([\d.]+)", url)
    if m:
        dois.add(f"10.48550/arxiv.{m.group(1)}")

    return dois


def doi_overlap(dois_a: set[str], dois_b: set[str]) -> bool:
    """Check if two sets of DOIs have any overlap."""
    return bool(dois_a & dois_b)


def merge_news(manual: list[dict], auto: list[dict]) -> list[dict]:
    """Merge manual and auto-discovered news, deduplicating by URL and DOI."""
    existing_urls = set()
    existing_dois = set()

    for item in manual:
        url = item.get("url", "")
        if url:
            existing_urls.add(url.rstrip("/"))
            existing_dois.update(extract_dois(url))

    merged = list(manual)
    new_count = 0
    for item in auto:
        url = item.get("url", "")
        if not url:
            continue
        normalized = url.rstrip("/")
        item_dois = extract_dois(url)

        # Skip if URL already exists
        if normalized in existing_urls:
            continue
        # Skip if any DOI overlaps with existing entries
        if doi_overlap(item_dois, existing_dois):
            continue

        merged.append(item)
        existing_urls.add(normalized)
        existing_dois.update(item_dois)
        new_count += 1

    if new_count > 0:
        print(f"Found {new_count} new publication(s) from Semantic Scholar")

    # Sort by date descending
    merged.sort(key=lambda x: x.get("date", ""), reverse=True)
    return merged


def render_news_html(items: list[dict]) -> str:
    """Render news items as HTML for insertion into index.html."""
    lines = []
    lines.append("                <!-- NEWS_START -->")
    lines.append('                <div class="news">')
    lines.append("                    <h2>News</h2>")
    lines.append("                    <ul>")

    for item in items:
        date = item.get("date", "")
        text = item.get("text", "")
        url = item.get("url")
        link_text = item.get("link_text", "here")

        if url:
            link = f' <a href="{url}"> {link_text} </a>.'
            li = f"                        <li>\n                            <strong>{date}</strong> - {text}{link}\n                        </li>"
        else:
            li = f"                        <li>\n                            <strong>{date}</strong> - {text}\n                        </li>"
        lines.append(li)

    lines.append("                    </ul>")
    lines.append("                </div>")
    lines.append("                <!-- NEWS_END -->")
    return "\n".join(lines)


def update_index_html(news_html: str) -> bool:
    """Replace the news section in index.html between markers. Returns True if changed."""
    content = INDEX_HTML.read_text()

    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        re.DOTALL,
    )

    if not pattern.search(content):
        print("Error: Could not find NEWS_START/NEWS_END markers in index.html")
        return False

    new_content = pattern.sub(news_html, content)

    if new_content == content:
        print("No changes to index.html")
        return False

    INDEX_HTML.write_text(new_content)
    print("Updated index.html")
    return True


def main():
    dry_run = "--dry-run" in sys.argv

    print("Loading manual news entries...")
    manual = load_news()
    print(f"  {len(manual)} manual entries")

    print("Fetching publications from Semantic Scholar...")
    papers = fetch_papers()
    print(f"  {len(papers)} papers found")

    auto_items = []
    for paper in papers:
        item = paper_to_news_item(paper)
        if item:
            auto_items.append(item)

    print("Merging news items...")
    merged = merge_news(manual, auto_items)
    print(f"  {len(merged)} total entries")

    # Save merged news back to JSON
    if not dry_run:
        NEWS_JSON.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
        print(f"Saved {NEWS_JSON}")

    # Generate and insert HTML
    news_html = render_news_html(merged)

    if dry_run:
        print("\n--- Generated HTML (dry run) ---")
        print(news_html)
    else:
        changed = update_index_html(news_html)
        if changed:
            print("Done! index.html updated with latest news.")
        else:
            print("Done! No changes needed.")


if __name__ == "__main__":
    main()
