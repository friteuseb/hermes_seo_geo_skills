#!/usr/bin/env python3
"""How readable a site is to generative engines (GEO), from a crawl.py corpus.

    python3 geo.py /tmp/seo-example.json

Answers one question: **can this site be cited by an engine that answers instead
of listing links** (AI Overviews, ChatGPT search, Perplexity)?

Four axes, measured rather than estimated:
  access      — are the answering crawlers allowed, does llms.txt exist
  rendering   — is the text in the served HTML (these crawlers do not run JavaScript)
  citability  — is the copy cut into self-contained, extractable passages
  attribution — entities, author, dates, sources: enough to credit a citation

No invented overall score: each axis is reported separately, because fixing them
falls to different people.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script requires the `requests` module.")

# Crawlers that *answer* (allow them if you want to be cited) versus crawlers that
# collect for training (an editorial choice, not a defect to fix).
ANSWERING = [
    ("OAI-SearchBot", "OpenAI — ChatGPT search index"),
    ("ChatGPT-User", "OpenAI — user-triggered browsing"),
    ("PerplexityBot", "Perplexity — index"),
    ("Perplexity-User", "Perplexity — on-demand browsing"),
    ("Claude-SearchBot", "Anthropic — search index"),
    ("Claude-User", "Anthropic — on-demand browsing"),
    ("Googlebot", "Google — AI Overviews and AI Mode use the classic index"),
    ("Bingbot", "Bing / Copilot"),
    ("Amazonbot", "Amazon — Rufus, Alexa"),
    ("meta-externalagent", "Meta AI"),
]
TRAINING = [
    ("GPTBot", "OpenAI — training"),
    ("ClaudeBot", "Anthropic — training"),
    ("Google-Extended", "Google — Gemini training"),
    ("Applebot-Extended", "Apple Intelligence — training"),
    ("CCBot", "Common Crawl"),
    ("Bytespider", "ByteDance"),
]

QUESTION = re.compile(
    r"^\s*(what|why|how|when|where|who|which|can|should|does|do|is|are|will|"
    r"comment|pourquoi|quand|où|qui|quoi|quel|quelle|quels|quelles|combien|"
    r"est-ce|faut-il|peut-on|doit-on|qu'est)",
    re.I,
)
DEFINITION = re.compile(
    r"\b(is a|is an|is the|are the|refers to|means|consists of|stands for|allows you to|"
    r"est un|est une|est le|est la|sont des|consiste à|désigne|signifie|permet de)\b",
    re.I,
)
DEPENDENT = re.compile(
    r"^\s*(above|below|as seen|as shown|therefore|thus|however|moreover|furthermore|"
    r"this|these|those|it|they|that's why|"
    r"ci-dessus|ci-dessous|comme vu|en effet|donc|ainsi|par ailleurs|de plus|celui-ci|cela)\b",
    re.I,
)
FIGURE = re.compile(r"\b\d+([.,]\d+)?\s?(%|€|\$|£|km|kg|lb|h|min|years?|months?|days?|m²|°[CF])?\b")
BYLINE = re.compile(r"\b(by|written by|author|par|rédigé par|écrit par)\s+[A-ZÉÈÀÂÎÔÛ][\w'’-]+", re.I)
VISIBLE_DATE = re.compile(
    r"\b(\d{1,2}\s+(january|february|march|april|may|june|july|august|september|october|november|"
    r"december|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|"
    r"décembre)\s+\d{4}|\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b",
    re.I,
)
ENTITY_HOSTS = ("wikipedia.org", "wikidata.org", "linkedin.com", "youtube.com", "crunchbase.com",
                "github.com", "companieshouse.gov.uk")


def crawler_access(site: str) -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; SEOSemanticAudit/1.0)"
    res = {"robots_txt": False, "rules": {}, "llms_txt": None, "llms_full": None, "sitemaps": []}
    try:
        r = session.get(urljoin(site, "/robots.txt"), timeout=15)
        if r.ok and "html" not in r.headers.get("content-type", ""):
            res["robots_txt"] = True
            res["sitemaps"] = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
            lines = r.text.splitlines()
            for ua, role in ANSWERING + TRAINING:
                rp = RobotFileParser()
                rp.parse(lines)
                named = re.search(rf"(?im)^\s*user-agent:\s*{re.escape(ua)}\s*$", r.text) is not None
                res["rules"][ua] = {"allowed": rp.can_fetch(ua, site), "named": named, "role": role}
    except requests.RequestException as e:
        res["error"] = str(e)[:150]

    for key, path in (("llms_txt", "/llms.txt"), ("llms_full", "/llms-full.txt")):
        try:
            r = session.get(urljoin(site, path), timeout=15)
            ok = (r.ok and "text/plain" in r.headers.get("content-type", "")) or (
                r.ok and r.text.lstrip().startswith("#")
            )
            res[key] = {"present": bool(ok), "bytes": len(r.content) if r.ok else 0}
        except requests.RequestException:
            res[key] = {"present": False, "bytes": 0}
    return res


def read_jsonld(page: dict) -> list[dict]:
    objects = []
    for block in page.get("jsonld", []):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack += x
            elif isinstance(x, dict):
                if "@graph" in x:
                    stack += x["@graph"] if isinstance(x["@graph"], list) else [x["@graph"]]
                if "@type" in x:
                    objects.append(x)
    return objects


def score_passage(block: dict) -> dict:
    text = block["text"].strip()
    words = text.split()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    first = sentences[0] if sentences else ""
    n = len(words)
    points, gaps = 0, []

    if 60 <= n <= 220:
        points += 2
    elif 40 <= n < 60 or 220 < n <= 320:
        points += 1
        gaps.append("length outside the comfortable window (60–220 words)")
    else:
        gaps.append(
            f"{n} words: {'too short to stand alone' if n < 40 else 'too long to be lifted as-is'}"
        )

    if block["heading"] and QUESTION.match(block["heading"]):
        points += 1
    elif block["heading"]:
        gaps.append("the section heading is not phrased as a question")
    else:
        gaps.append("passage has no section heading")

    if DEFINITION.search(" ".join(sentences[:2])) or (first and len(first.split()) <= 35):
        points += 1
    else:
        gaps.append("no direct answer in the first sentence")

    if DEPENDENT.match(first):
        gaps.append("opens by carrying over the previous paragraph (not self-contained)")
    else:
        points += 1

    if len(FIGURE.findall(text)) >= 2:
        points += 1
    else:
        gaps.append("no figures worth quoting")

    return {"score": points, "out_of": 6, "words": n, "heading": block["heading"], "gaps": gaps}


def analyse(corpus: dict, probe_network: bool = True) -> dict:
    pages = [p for p in corpus["pages"] if p["status"] == 200 and p.get("words", 0) >= 40]
    access = crawler_access(corpus["site"]) if probe_network else {}

    js_only = [
        {"url": p["url"], "words": p.get("words", 0), "bytes": p.get("html_bytes", 0)}
        for p in corpus["pages"]
        if p["status"] == 200 and p.get("words", 0) < 120 and p.get("html_bytes", 0) > 20000
    ]

    jsonld_types: Counter = Counter()
    no_jsonld, sameas, no_date, no_author = [], set(), [], []
    citability, weak_passages = [], []
    q_headings = total_headings = 0
    external_sources: Counter = Counter()
    entity_links: Counter = Counter()

    for p in pages:
        objects = read_jsonld(p)
        if not objects:
            no_jsonld.append(p["url"])
        for o in objects:
            t = o.get("@type")
            for x in t if isinstance(t, list) else [t]:
                jsonld_types[str(x)] += 1
            for s in o.get("sameAs", []) if isinstance(o.get("sameAs"), list) else []:
                sameas.add(s)

        dated = bool(p.get("dates")) or bool(VISIBLE_DATE.search(p.get("text", "")[:2000]))
        if not dated:
            no_date.append(p["url"])
        schema_author = any("author" in o for o in objects)
        if not (schema_author or BYLINE.search(p.get("text", "")[:1500])):
            no_author.append(p["url"])

        for l in p.get("links", []):
            if not l["internal"]:
                host = re.sub(r"^https?://(www\.)?", "", l["target"]).split("/")[0].lower()
                external_sources[host] += 1
                for e in ENTITY_HOSTS:
                    if host.endswith(e):
                        entity_links[e] += 1

        for h in p.get("headings", []):
            if h["level"] >= 2 and h["text"]:
                total_headings += 1
                if QUESTION.match(h["text"]):
                    q_headings += 1

        blocks = [b for b in p.get("blocks", []) if len(b["text"].split()) >= 25]
        scores = [score_passage(b) for b in blocks]
        if scores:
            citability.append(
                {
                    "url": p["url"],
                    "mean": round(sum(s["score"] for s in scores) / len(scores), 2),
                    "passages": len(scores),
                    "strong": sum(1 for s in scores if s["score"] >= 5),
                    "structure": {
                        "tables": p.get("n_tables", 0),
                        "lists": p.get("n_lists", 0),
                        "images_without_alt": sum(1 for i in p.get("images", []) if not i.get("alt")),
                    },
                }
            )
            for s in sorted(scores, key=lambda x: x["score"])[:1]:
                if s["score"] <= 2:
                    weak_passages.append({"url": p["url"], **s})
        else:
            citability.append(
                {"url": p["url"], "mean": 0.0, "passages": 0, "strong": 0,
                 "structure": {"tables": 0, "lists": 0, "images_without_alt": 0}}
            )

    citability.sort(key=lambda c: c["mean"])
    return {
        "site": corpus["site"],
        "pages": len(pages),
        "access": access,
        "js_only": js_only,
        "jsonld_types": jsonld_types,
        "no_jsonld": no_jsonld,
        "sameas": sorted(sameas),
        "no_date": no_date,
        "no_author": no_author,
        "citability": citability,
        "weak_passages": weak_passages,
        "question_headings": (q_headings, total_headings),
        "external_sources": external_sources,
        "entity_links": entity_links,
    }


def report(a: dict, limit: int = 12) -> str:
    L = []
    add = L.append
    add(f"# Readability for generative engines — {a['site']} ({a['pages']} pages)\n")

    access = a.get("access") or {}
    if access:
        add("## Crawler access")
        if not access.get("robots_txt"):
            add("  No robots.txt served: everything is allowed by default (not a defect).")
        else:
            blocked = [(ua, r) for ua, r in access["rules"].items()
                       if not r["allowed"] and ua in dict(ANSWERING)]
            if blocked:
                add("  ⚠ Answering crawlers blocked — the site cannot be cited by them:")
                for ua, r in blocked:
                    add(f"    - {ua} ({r['role']})")
            else:
                add("  Every answering crawler tracked here is allowed.")
            training_blocked = [ua for ua, r in access["rules"].items()
                                if not r["allowed"] and ua in dict(TRAINING)]
            if training_blocked:
                add(f"  Training crawlers blocked (editorial choice): {', '.join(training_blocked)}")
        for key, name in (("llms_txt", "/llms.txt"), ("llms_full", "/llms-full.txt")):
            v = access.get(key) or {}
            add(f"  {name}: {'present (' + str(v.get('bytes', 0)) + ' bytes)' if v.get('present') else 'absent'}")
        add(f"  Sitemap declared in robots.txt: {', '.join(access.get('sitemaps') or ['none'])}")
        add("")

    add("## Rendering without JavaScript")
    if a["js_only"]:
        add(
            f"  ⚠ {len(a['js_only'])} page(s) serve heavy HTML with almost no text — the content is "
            f"injected in the browser, so these crawlers never see it:"
        )
        for p in a["js_only"][:limit]:
            add(f"    - {p['words']} words for {p['bytes'] // 1024} KB of HTML: {p['url']}")
    else:
        add("  Text is present in the served HTML on every page analysed.")
    add("")

    q, t = a["question_headings"]
    add("## Passage citability")
    add(f"  Section headings phrased as questions: {q}/{t}" + (f" ({q / t:.0%})" if t else ""))
    weak = [c for c in a["citability"] if c["mean"] < 3 and c["passages"]]
    add(f"  Pages whose passages are hard to extract (mean < 3/6): {len(weak)}")
    for c in a["citability"][:limit]:
        add(f"    - {c['mean']:.1f}/6  {c['passages']:>2} passage(s), {c['strong']} citable  {c['url']}")
    add("")
    if a["weak_passages"]:
        add("  Passages to rework first:")
        for p in a["weak_passages"][:limit]:
            add(f"    - {p['url']} — section \"{p['heading'][:60] or '(no heading)'}\" ({p['words']} words)")
            for g in p["gaps"][:3]:
                add(f"        · {g}")
        add("")

    add("## Attribution")
    if a["jsonld_types"]:
        add("  JSON-LD types found: " + ", ".join(f"{t} ×{n}" for t, n in a["jsonld_types"].most_common(12)))
    else:
        add("  ⚠ No JSON-LD structured data anywhere on the site.")
    add(f"  Pages without JSON-LD: {len(a['no_jsonld'])}")
    add(f"  Pages with no detectable date: {len(a['no_date'])}")
    add(f"  Pages with no identifiable author: {len(a['no_author'])}")
    if a["sameas"]:
        add("  sameAs declared: " + ", ".join(a["sameas"][:8]))
    else:
        add("  No sameAs: the site's entity is tied to no external profile.")
    if a["entity_links"]:
        add("  Links to entity references: " + ", ".join(f"{k} ×{v}" for k, v in a["entity_links"].items()))
    ext = [f"{h} ({n})" for h, n in a["external_sources"].most_common(8) if h]
    add("  External sources cited: " + (", ".join(ext) if ext else "none"))
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="GEO audit of a crawl.py corpus.")
    ap.add_argument("corpus")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--offline", action="store_true", help="skip the robots.txt / llms.txt probes")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    with open(args.corpus, encoding="utf-8") as f:
        corpus = json.load(f)
    a = analyse(corpus, probe_network=not args.offline)
    print(report(a, args.limit))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({k: (dict(v) if isinstance(v, Counter) else v) for k, v in a.items()},
                      f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
