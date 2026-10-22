"""Download works listed in works.yaml from marxists.org (or any static HTML site).

Multi-page works start from an index page. The fetcher follows links to pages in
the same directory, in the order the index lists them, and joins them into one
UTF-8 HTML file in texts/.
"""
import os
import posixpath
import time
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, UnicodeDammit

USER_AGENT = "comrade-bot/0.1 (+https://github.com/commie-bot/commie-bot)"
DELAY = 1.0  # seconds between requests, to be polite to volunteer-run archives
# Archive-written pages linked from indexes that aren't the author's text
DEFAULT_SKIP = ("guide", "study", "glossary", "notes.htm", "intro-ed")

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def get_html(url: str) -> str:
    resp = _session.get(url, timeout=60)
    resp.raise_for_status()
    # Many marxists.org pages are Latin-1 or Windows-1252 with no charset header
    return UnicodeDammit(resp.content, is_html=True).unicode_markup


def should_crawl(work: dict) -> bool:
    if "crawl" in work:
        return bool(work["crawl"])
    path = urlparse(work["url"]).path
    return path.endswith("/") or posixpath.basename(path) in ("index.htm", "index.html")


def chapter_links(index_url: str, soup: BeautifulSoup, skip=()) -> list[str]:
    """Links from the index to other HTML pages in the same directory tree, in order."""
    base_dir = index_url.rsplit("/", 1)[0] + "/"
    index = urldefrag(index_url)[0]
    seen, links = {index}, []
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(index_url, a["href"]))[0]
        if url in seen or not url.startswith(base_dir):
            continue
        if not url.endswith((".htm", ".html")):
            continue
        name = url[len(base_dir):].lower()
        if any(s in name for s in (*DEFAULT_SKIP, *skip)):
            continue
        seen.add(url)
        links.append(url)
    return links


def body_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    return body.decode_contents()


def fetch_work(work: dict, texts_dir: str, log=print) -> str:
    """Download one work and return the path it was saved to."""
    url = work["url"]
    html = get_html(url)
    pages = [html]
    if should_crawl(work):
        links = chapter_links(url, BeautifulSoup(html, "lxml"), work.get("skip", ()))
        if links:
            pages = []  # the index is a table of contents, not text
            for i, link in enumerate(links, 1):
                time.sleep(DELAY)
                log(f"  [{i}/{len(links)}] {link}")
                try:
                    pages.append(get_html(link))
                except requests.HTTPError as e:
                    log(f"  skipped: {e}")

    sections = "\n".join(f"<section>\n{body_html(p)}\n</section>" for p in pages)
    out = os.path.join(texts_dir, work["file"])
    with open(out, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html>\n<html><head><meta charset="utf-8"><title>{work["title"]}</title></head>\n'
                f"<body>\n{sections}\n</body></html>\n")
    return out
