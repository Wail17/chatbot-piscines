"""
Scrape Beniferro.eu + Zwembad.eu and write a curated markdown knowledge file
to app/data/company_knowledge.md.

The output is read by app/rag.py at chatbot startup and prepended to the
cached FAQ context sent to Claude, so the LLM knows the two companies as
deeply as it knows the FAQ.

Run manually whenever the websites change:
    python scripts/scrape_company_sites.py
"""
from __future__ import annotations

import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Iterable, List, Set, Tuple

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "app" / "data" / "company_knowledge.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WifipoolKnowledgeScraper/1.0; "
        "+https://chatbot-piscines.onrender.com)"
    )
}

REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 0.8

SITES: List[dict] = [
    {
        "name": "Beniferro",
        "base_url": "https://beniferro.eu",
        "max_pages": 25,
        "skip_patterns": [
            "/wp-login", "/wp-admin", "/wp-json", "/cart", "/checkout",
            "/my-account", "/?add-to-cart=", "feed", "?orderby=",
            "?filter_", ".jpg", ".jpeg", ".png", ".webp", ".pdf",
            ".gif", ".zip", "/page/",
        ],
    },
    {
        "name": "Zwembad.eu",
        "base_url": "https://www.zwembad.eu",
        "max_pages": 25,
        "skip_patterns": [
            "/wp-login", "/wp-admin", "/wp-json", "/cart", "/checkout",
            "/my-account", "/?add-to-cart=", "feed", "?orderby=",
            "?filter_", ".jpg", ".jpeg", ".png", ".webp", ".pdf",
            ".gif", ".zip", "/page/",
        ],
    },
]


def _should_skip(url: str, patterns: List[str]) -> bool:
    low = url.lower()
    return any(p in low for p in patterns)


def _same_host(url: str, base_host: str) -> bool:
    try:
        return urllib.parse.urlparse(url).netloc.endswith(base_host)
    except Exception:
        return False


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.split("#", 1)[0]
    return url.rstrip("/")


def _fetch(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return ""
        ct = r.headers.get("Content-Type", "").lower()
        if "html" not in ct and "xml" not in ct:
            return ""
        return r.text
    except requests.RequestException:
        return ""


def _extract_links(html: str, base_url: str, host: str, skip_patterns: List[str]) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[str] = []
    seen: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        abs_url = _normalize_url(urllib.parse.urljoin(base_url, href))
        if not _same_host(abs_url, host):
            continue
        if _should_skip(abs_url, skip_patterns):
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append(abs_url)
    return out


def _extract_clean_text(html: str) -> Tuple[str, str]:
    """Return (title, body_text) cleaned from nav/footer/script noise."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_tag = soup.find("title")
    title = (title_tag.get_text(strip=True) if title_tag else "").strip()

    # Strip noise
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Prefer <main> or <article>
    main = soup.find("main") or soup.find("article") or soup.body or soup

    # Collect text, preserving headings (with intra-page dedup)
    parts: List[str] = []
    seen_local: Set[str] = set()
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = el.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) < 3:
            continue
        # Skip duplicate paragraphs within the SAME page (sidebars, repeated CTAs)
        key = text.lower()[:200]
        if key in seen_local:
            continue
        seen_local.add(key)
        if el.name in {"h1", "h2"}:
            parts.append(f"\n### {text}\n")
        elif el.name in {"h3", "h4"}:
            parts.append(f"\n**{text}**\n")
        elif el.name == "li":
            parts.append(f"- {text}")
        else:
            parts.append(text)

    body = "\n".join(parts).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return title, body


def _try_sitemap(base_url: str) -> List[str]:
    """Attempt to read sitemap.xml and return discovered URLs."""
    candidates = [
        f"{base_url}/sitemap.xml",
        f"{base_url}/sitemap_index.xml",
        f"{base_url}/wp-sitemap.xml",
    ]
    for sm in candidates:
        text = _fetch(sm)
        if not text or "<urlset" not in text and "<sitemapindex" not in text:
            continue
        soup = BeautifulSoup(text, "xml")
        urls = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
        urls = [u for u in urls if u]
        # If sitemap index, recurse one level
        nested: List[str] = []
        if "<sitemapindex" in text:
            for u in urls[:6]:
                nested_text = _fetch(u)
                if not nested_text:
                    continue
                nested_soup = BeautifulSoup(nested_text, "xml")
                for loc in nested_soup.find_all("loc"):
                    val = loc.get_text(strip=True)
                    if val:
                        nested.append(val)
                time.sleep(DELAY_BETWEEN_REQUESTS)
            return nested
        return urls
    return []


def _crawl_site(site: dict) -> str:
    name = site["name"]
    base_url = site["base_url"].rstrip("/")
    host = urllib.parse.urlparse(base_url).netloc
    max_pages = site["max_pages"]
    skip = site["skip_patterns"]

    print(f"\n[{name}] starting at {base_url}")

    visited: Set[str] = set()
    queue: List[str] = [base_url]

    sitemap_urls = _try_sitemap(base_url)
    if sitemap_urls:
        print(f"[{name}] sitemap found, {len(sitemap_urls)} URLs")
        for u in sitemap_urls:
            if not _should_skip(u, skip):
                queue.append(_normalize_url(u))

    pages_md: List[str] = []
    seen_global: Set[str] = set()
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        url = _normalize_url(url)
        if not url or url in visited or _should_skip(url, skip):
            continue
        visited.add(url)

        html = _fetch(url)
        if not html:
            continue
        title, body = _extract_clean_text(html)
        if not body or len(body) < 80:
            continue

        # Cross-page dedup: drop paragraphs we've already seen on a previous
        # page of this site (boilerplate banners, repeated product callouts).
        kept_lines: List[str] = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("###") or stripped.startswith("**"):
                kept_lines.append(line)
                continue
            key = stripped.lower()[:200]
            if key in seen_global:
                continue
            seen_global.add(key)
            kept_lines.append(line)
        body = "\n".join(kept_lines).strip()
        body = re.sub(r"\n{3,}", "\n\n", body)
        if len(body) < 80:
            continue

        rel = url.replace(base_url, "") or "/"
        pages_md.append(f"\n#### Page: {rel}\n")
        if title:
            pages_md.append(f"*Title:* {title}")
        pages_md.append(body)
        print(f"[{name}] [{len(visited):>2}] {rel}  ({len(body)} chars)")

        # Enqueue more
        for link in _extract_links(html, url, host, skip):
            if link not in visited and link not in queue:
                queue.append(link)

        time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"[{name}] done. {len(visited)} pages visited, {len(pages_md)} kept.")
    return "\n\n".join(pages_md).strip()


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    blocks: List[str] = []
    blocks.append("# Company knowledge")
    blocks.append("")
    blocks.append(
        "This file is auto-generated by `scripts/scrape_company_sites.py`. "
        "It contains a curated extract of the public content from the two "
        "companies behind the Wifipool chatbot:\n"
        "- Beniferro (https://beniferro.eu)\n"
        "- Zwembad.eu (https://www.zwembad.eu)\n\n"
        "The chatbot LLM uses this content (cached) alongside the FAQ "
        "knowledge base so it can answer customer questions about the "
        "companies, their products and services."
    )

    for site in SITES:
        content = _crawl_site(site)
        if not content:
            print(f"[!] No content extracted for {site['name']}")
            continue
        blocks.append("")
        blocks.append("---")
        blocks.append("")
        blocks.append(f"# {site['name']} ({site['base_url']})")
        blocks.append(content)

    OUT_PATH.write_text("\n".join(blocks), encoding="utf-8")
    size_kb = OUT_PATH.stat().st_size // 1024
    print(f"\n[ok] Wrote {OUT_PATH.relative_to(ROOT)}  ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
