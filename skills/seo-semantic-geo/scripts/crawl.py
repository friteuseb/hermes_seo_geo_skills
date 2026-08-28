#!/usr/bin/env python3
"""Collect a site's pages into a JSON corpus.

The other three scripts of this skill (linking.py, semantics.py, geo.py) read this
corpus, so the site is crawled once. Standard library plus `requests` only — no
BeautifulSoup, no lxml, no jq, on purpose: this has to run on a Raspberry Pi with
nothing installed.

    python3 crawl.py https://example.com --max-pages 150 --out /tmp/seo-example.json

Per page the corpus keeps: HTTP status, redirect chain, title, meta description,
heading hierarchy, editorial text (excluding nav/header/footer/aside), outgoing
links with their anchor and context, canonical, hreflang, JSON-LD, indexing
directives, images, tables and lists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script requires the `requests` module.")

UA = "Mozilla/5.0 (compatible; SEOSemanticAudit/1.0; internal linking and semantics audit)"

# Extensions we never follow: not HTML.
NON_HTML = re.compile(
    r"\.(jpe?g|png|gif|webp|avif|svg|ico|css|js|mjs|json|xml|pdf|zip|gz|rar|7z"
    r"|mp[34]|m4a|wav|ogg|webm|mov|avi|woff2?|ttf|eot|dmg|exe|apk|csv|xlsx?|docx?)"
    r"(\?|$)",
    re.I,
)
TRACKING = re.compile(r"^(utm_|fbclid|gclid|msclkid|mc_[ce]id|_ga|ref|igshid)", re.I)

# Tags whose contents are never editorial text.
MUTE = {"script", "style", "noscript", "template", "svg", "canvas", "iframe"}
# Template containers: links inside them are structural, not editorial. The real
# filter is statistical — see linking.py.
BOILERPLATE = {"nav", "header", "footer", "aside"}


def normalise(url: str) -> str:
    """Canonical form of a URL, for crawl de-duplication."""
    p = urlparse(url)
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    if p.port and not ((scheme == "https" and p.port == 443) or (scheme == "http" and p.port == 80)):
        host = f"{host}:{p.port}"
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    query = urlencode(sorted(params))
    path = re.sub(r"/{2,}", "/", p.path) or "/"
    return urlunparse((scheme, host, path, "", query, ""))


def same_site(url: str, host: str) -> bool:
    """Same site, ignoring the www prefix and nothing else."""
    h = (urlparse(url).hostname or "").lower()
    return h.removeprefix("www.") == host.removeprefix("www.")


class PageParser(HTMLParser):
    """Extracts everything the analyses need, in a single pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.title = ""
        self.description = ""
        self.robots_meta = ""
        self.canonical = ""
        self.lang = ""
        self.hreflang: list[dict] = []
        self.links: list[dict] = []
        self.headings: list[dict] = []
        self.jsonld: list[str] = []
        self.images: list[dict] = []
        self.og: dict = {}
        self.dates: list[str] = []
        self.n_tables = 0
        self.n_lists = 0
        self.n_paragraphs = 0
        self.blocks: list[dict] = []  # editorial text, split by heading section
        self._section = {"heading": "", "level": 0, "text": []}
        self._link: dict | None = None
        self._heading: dict | None = None
        self._jsonld_buf: list[str] | None = None
        self._title_buf: list[str] | None = None

    # -- context ----------------------------------------------------------
    @property
    def _muted(self) -> bool:
        return any(t in MUTE for t in self.stack)

    @property
    def _boilerplate(self) -> bool:
        return any(t in BOILERPLATE for t in self.stack)

    # -- HTMLParser -------------------------------------------------------
    def handle_starttag(self, tag, attrs):  # noqa: C901 - a dispatch, not a factory
        a = {k.lower(): (v or "") for k, v in attrs}
        self.stack.append(tag)

        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title" and not self.title:
            self._title_buf = []
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            content = a.get("content", "")
            if name == "description":
                self.description = content.strip()
            elif name in ("robots", "googlebot"):
                self.robots_meta = (self.robots_meta + " " + content).strip().lower()
            elif name.startswith("og:") or name.startswith("article:"):
                self.og[name] = content
                if "time" in name or "date" in name:
                    self.dates.append(content)
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "canonical" in rel:
                self.canonical = a.get("href", "")
            elif "alternate" in rel and a.get("hreflang"):
                self.hreflang.append({"hreflang": a["hreflang"], "href": a.get("href", "")})
        elif tag == "script":
            if a.get("type", "").lower().strip() == "application/ld+json":
                self._jsonld_buf = []
        elif tag == "time":
            if a.get("datetime"):
                self.dates.append(a["datetime"])
        elif tag == "a":
            href = a.get("href", "").strip()
            if href and not href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                self._link = {
                    "href": href,
                    "anchor": [],
                    "rel": a.get("rel", "").lower(),
                    "boilerplate": self._boilerplate,
                    "title": a.get("title", ""),
                }
        elif tag == "img":
            self.images.append(
                {
                    "src": a.get("src", "") or a.get("data-src", ""),
                    "alt": a.get("alt", ""),
                    "has_alt": "alt" in a,
                    "loading": a.get("loading", ""),
                }
            )
            if self._link is not None and a.get("alt"):
                self._link["anchor"].append(a["alt"])
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_section()
            self._heading = {"level": int(tag[1]), "text": []}
        elif tag == "table":
            self.n_tables += 1
        elif tag in ("ul", "ol"):
            self.n_lists += 1
        elif tag == "p":
            self.n_paragraphs += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self._title_buf is not None:
            self._title_buf.append(data)
            return
        if self._jsonld_buf is not None:
            self._jsonld_buf.append(data)
            return
        if self._muted:
            return
        if self._heading is not None:
            self._heading["text"].append(data)
        if self._link is not None:
            self._link["anchor"].append(data)
        if not self._boilerplate:
            self._section["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "title" and self._title_buf is not None:
            self.title = " ".join("".join(self._title_buf).split())
            self._title_buf = None
        elif tag == "script" and self._jsonld_buf is not None:
            self.jsonld.append("".join(self._jsonld_buf))
            self._jsonld_buf = None
        elif tag == "a" and self._link is not None:
            self._link["anchor"] = " ".join(" ".join(self._link["anchor"]).split())
            self.links.append(self._link)
            self._link = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading is not None:
            text = " ".join(" ".join(self._heading["text"]).split())
            self.headings.append({"level": self._heading["level"], "text": text})
            self._section = {"heading": text, "level": self._heading["level"], "text": []}
            self._heading = None

        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == tag:
                del self.stack[i:]
                break

    # -- output -----------------------------------------------------------
    def _flush_section(self) -> None:
        text = " ".join(" ".join(self._section["text"]).split())
        if text:
            self.blocks.append(
                {"heading": self._section["heading"], "level": self._section["level"], "text": text}
            )
        self._section = {"heading": self._section["heading"], "level": self._section["level"], "text": []}

    def finish(self) -> None:
        self._flush_section()


def load_sitemaps(base: str, session: requests.Session, seen: set[str] | None = None) -> list[str]:
    """Follow robots.txt then sitemap indexes, with regex (no xmllint available)."""
    seen = seen if seen is not None else set()
    urls: list[str] = []
    candidates: list[str] = []

    try:
        r = session.get(urljoin(base, "/robots.txt"), timeout=15)
        if r.ok:
            candidates += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    except requests.RequestException:
        pass
    candidates += [urljoin(base, "/sitemap.xml"), urljoin(base, "/sitemap_index.xml")]

    while candidates:
        sm = candidates.pop(0)
        if sm in seen or len(seen) > 25:
            continue
        seen.add(sm)
        try:
            r = session.get(sm, timeout=20)
            if not r.ok:
                continue
            body = r.text
        except requests.RequestException:
            continue
        locs = [re.sub(r"\s+", "", m) for m in re.findall(r"<loc>(.*?)</loc>", body, re.S | re.I)]
        if re.search(r"<sitemapindex", body, re.I):
            candidates += locs
        else:
            urls += locs
    return urls


def crawl(start: str, max_pages: int, delay: float, workers: int, obey_robots: bool) -> dict:
    base = normalise(start)
    host = (urlparse(base).hostname or "").lower()
    session = requests.Session()
    session.headers["User-Agent"] = UA

    rp = RobotFileParser()
    if obey_robots:
        try:
            r = session.get(urljoin(base, "/robots.txt"), timeout=15)
            rp.parse(r.text.splitlines() if r.ok else [])
        except requests.RequestException:
            rp.parse([])

    def allowed(u: str) -> bool:
        if not obey_robots:
            return True
        try:
            return rp.can_fetch(UA, u)
        except Exception:
            return True

    sitemap = [normalise(u) for u in load_sitemaps(base, session) if same_site(u, host)]
    in_sitemap = set(sitemap)

    # Breadth-first from the home page. Sitemap URLs are held in reserve and only
    # used once the frontier is exhausted: mixing them in would mean that, on a
    # large site truncated by --max-pages, only sitemap URLs get fetched and every
    # page comes back reported as "unreachable".
    queue = [base]
    reserve = [u for u in sitemap if u != base]
    known = set(queue) | set(reserve)
    pages: dict[str, dict] = {}

    def fetch(url: str) -> dict | None:
        try:
            r = session.get(url, timeout=25, allow_redirects=True)
        except requests.RequestException as e:
            return {"url": url, "status": 0, "error": str(e)[:200], "links": [], "blocks": []}
        final = normalise(r.url)
        ctype = r.headers.get("content-type", "")
        page = {
            "url": url,
            "final_url": final,
            "status": r.status_code,
            "redirects": [normalise(h.url) for h in r.history],
            "content_type": ctype,
            "html_bytes": len(r.content),
            "x_robots_tag": r.headers.get("x-robots-tag", "").lower(),
            "links": [],
            "blocks": [],
        }
        if r.status_code >= 400 or "html" not in ctype.lower():
            return page

        p = PageParser()
        try:
            p.feed(r.text)
            p.finish()
        except Exception as e:  # broken HTML must not stop the audit
            page["parse_error"] = str(e)[:200]
            return page

        links = []
        for l in p.links:
            target = urljoin(final, l["href"])
            if not target.lower().startswith(("http://", "https://")):
                continue
            internal = same_site(target, host)
            links.append(
                {
                    "target": normalise(target) if internal else target,
                    "anchor": l["anchor"],
                    "rel": l["rel"],
                    "boilerplate": l["boilerplate"],
                    "internal": internal,
                    "nofollow": "nofollow" in l["rel"],
                }
            )
        text = " ".join(b["text"] for b in p.blocks)
        page.update(
            {
                "title": p.title,
                "description": p.description,
                "lang": p.lang,
                "canonical": normalise(urljoin(final, p.canonical)) if p.canonical else "",
                "hreflang": p.hreflang,
                "robots_meta": p.robots_meta,
                "headings": p.headings,
                "blocks": p.blocks,
                "text": text,
                "words": len(text.split()),
                "links": links,
                "jsonld": p.jsonld,
                "images": p.images,
                "og": p.og,
                "dates": p.dates,
                "n_tables": p.n_tables,
                "n_lists": p.n_lists,
                "n_paragraphs": p.n_paragraphs,
                "in_sitemap": url in in_sitemap,
            }
        )
        return page

    while (queue or reserve) and len(pages) < max_pages:
        if not queue:
            queue, reserve = reserve, []
        batch = []
        while queue and len(batch) < workers and len(pages) + len(batch) < max_pages:
            u = queue.pop(0)
            if u in pages or NON_HTML.search(u) or not allowed(u):
                continue
            batch.append(u)
        if not batch:
            continue
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for page in ex.map(fetch, batch):
                if not page:
                    continue
                pages[page["url"]] = page
                print(
                    f"  [{len(pages):>4}] {page['status']} {page.get('words', 0):>5} words  {page['url']}",
                    file=sys.stderr,
                )
                for l in page["links"]:
                    t = l["target"]
                    if l["internal"] and t not in known and not NON_HTML.search(t):
                        known.add(t)
                        queue.append(t)
        if delay:
            time.sleep(delay)

    return {
        "site": base,
        "host": host,
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sitemap": sitemap,
        "urls_discovered": len(known),
        "complete": not queue and not reserve,
        "max_pages": max_pages,
        "pages": list(pages.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Crawl a site into a JSON corpus for SEO analysis.")
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--delay", type=float, default=0.2, help="pause between batches, seconds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ignore-robots", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    corpus = crawl(args.url, args.max_pages, args.delay, max(1, args.workers), not args.ignore_robots)
    out = args.out or f"/tmp/seo-{corpus['host'].replace('.', '-')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False)

    ok = [p for p in corpus["pages"] if p["status"] == 200]
    truncated = "" if corpus["complete"] else (
        f"\n⚠ crawl truncated at {args.max_pages} pages: depth, PageRank and broken links "
        f"only hold within that scope."
    )
    print(
        f"\n{len(corpus['pages'])} pages fetched ({len(ok)} with status 200), "
        f"{corpus['urls_discovered']} internal URLs discovered, "
        f"{len(corpus['sitemap'])} URLs in the sitemap.{truncated}\ncorpus: {out}",
        file=sys.stderr,
    )
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
