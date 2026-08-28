#!/usr/bin/env python3
"""Analyse the internal link graph of a corpus produced by crawl.py.

    python3 linking.py /tmp/seo-example.json [--json report.json]

Separates **boilerplate** linking (menu, footer: on every page, therefore
incapable of ranking anything) from **editorial** linking (links inside the body
copy), which is the only lever anyone can actually pull. A link counts as
boilerplate if it sits inside <nav>/<header>/<footer>/<aside>, or if the
(target, anchor) pair repeats on more than 60% of pages.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict, deque
from urllib.parse import urlparse

WEAK_ANCHORS = {
    "click here", "here", "read more", "learn more", "more", "see more", "view",
    "details", "find out more", "continue", "link", "this link", "page", "our site",
    "discover", "go", "next", ">", ">>", "→", "read", "see",
}
BOILERPLATE_THRESHOLD = 0.60  # share of pages the (target, anchor) pair must appear on


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build(corpus: dict) -> dict:
    """Resolve internal links and split boilerplate from editorial."""
    pages = {p["url"]: p for p in corpus["pages"]}
    complete = corpus.get("complete", True)
    # A URL can be reached through its original or its final form.
    alias = {}
    for p in corpus["pages"]:
        alias[p["url"]] = p["url"]
        if p.get("final_url"):
            alias.setdefault(p["final_url"], p["url"])
        for r in p.get("redirects", []):
            alias.setdefault(r, p["url"])

    indexable = {
        u
        for u, p in pages.items()
        if p["status"] == 200
        and "noindex" not in p.get("robots_meta", "")
        and "noindex" not in p.get("x_robots_tag", "")
    }

    # Count (target, anchor) pairs for the statistical boilerplate detection.
    occurrences: Counter = Counter()
    for p in pages.values():
        seen = {(l["target"], l["anchor"].lower()) for l in p.get("links", []) if l["internal"]}
        occurrences.update(seen)
    total_pages = max(1, len([p for p in pages.values() if p["status"] == 200]))

    edges = []
    broken = []
    unverified: Counter = Counter()
    redirected = []
    external: Counter = Counter()

    for src, p in pages.items():
        if p["status"] != 200:
            continue
        for l in p.get("links", []):
            if not l["internal"]:
                external[(urlparse(l["target"]).hostname or "").lower()] += 1
                continue
            raw = l["target"]
            target = alias.get(raw)
            if target is None:
                # Never fetched: that is a broken link only if the crawl went all
                # the way. Otherwise it is merely out of scope — conflating the two
                # produces thousands of phantom "broken links".
                if complete:
                    broken.append(
                        {"source": src, "target": raw, "anchor": l["anchor"], "reason": "target missing"}
                    )
                else:
                    unverified[raw] += 1
                continue
            dest = pages[target]
            if dest["status"] == 0 or dest["status"] >= 400:
                broken.append(
                    {"source": src, "target": raw, "anchor": l["anchor"], "reason": f"HTTP {dest['status']}"}
                )
                continue
            if dest.get("redirects"):
                redirected.append(
                    {"source": src, "target": raw, "to": dest["final_url"], "anchor": l["anchor"]}
                )

            frequent = occurrences[(raw, l["anchor"].lower())] / total_pages >= BOILERPLATE_THRESHOLD
            edges.append(
                {
                    "source": src,
                    "target": target,
                    "anchor": l["anchor"],
                    "boilerplate": bool(l["boilerplate"] or frequent),
                    "nofollow": l["nofollow"],
                }
            )

    return {
        "pages": pages,
        "indexable": indexable,
        "edges": edges,
        "broken": broken,
        "unverified": unverified,
        "complete": complete,
        "redirected": redirected,
        "external": external,
    }


def depths(start: str, edges: list[dict]) -> dict[str, int]:
    """Clicks from the home page, following every followable link."""
    out = defaultdict(list)
    for e in edges:
        if not e["nofollow"]:
            out[e["source"]].append(e["target"])
    d = {start: 0}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for v in out.get(u, []):
            if v not in d:
                d[v] = d[u] + 1
                queue.append(v)
    return d


def pagerank(pages: dict, edges: list[dict], damping: float = 0.85, rounds: int = 40) -> dict[str, float]:
    nodes = [u for u, p in pages.items() if p["status"] == 200]
    if not nodes:
        return {}
    index = set(nodes)
    out = defaultdict(set)
    for e in edges:
        if not e["nofollow"] and e["source"] in index and e["target"] in index and e["source"] != e["target"]:
            out[e["source"]].add(e["target"])
    n = len(nodes)
    rank = {u: 1.0 / n for u in nodes}
    for _ in range(rounds):
        nxt = {u: (1 - damping) / n for u in nodes}
        leak = 0.0
        for u in nodes:
            targets = out.get(u)
            if not targets:
                leak += rank[u]
                continue
            share = damping * rank[u] / len(targets)
            for v in targets:
                nxt[v] += share
        if leak:
            share = damping * leak / n
            for u in nodes:
                nxt[u] += share
        rank = nxt
    return rank


def section(url: str) -> str:
    parts = [s for s in urlparse(url).path.split("/") if s]
    return "/" + parts[0] if parts else "/(root)"


def analyse(corpus: dict) -> dict:
    g = build(corpus)
    pages, edges = g["pages"], g["edges"]
    home = corpus["site"]
    if home not in pages:
        home = min(pages, key=lambda u: len(u))

    editorial = [e for e in edges if not e["boilerplate"]]
    inbound = Counter(e["target"] for e in edges if not e["nofollow"])
    inbound_edit = Counter(e["target"] for e in editorial if not e["nofollow"])
    outbound_edit = Counter(e["source"] for e in editorial)

    depth = depths(home, edges)
    rank = pagerank(pages, edges)

    ok = [u for u, p in pages.items() if p["status"] == 200]
    orphans = [u for u in ok if inbound.get(u, 0) == 0 and u != home]
    no_editorial_in = [u for u in ok if inbound_edit.get(u, 0) == 0 and u != home]
    dead_ends = [u for u in ok if outbound_edit.get(u, 0) == 0 and pages[u].get("words", 0) >= 250]
    deep = sorted(((depth.get(u, 99), u) for u in ok if depth.get(u, 99) >= 4), reverse=True)

    anchors_by_target = defaultdict(Counter)
    weak_anchors = []
    ambiguous = defaultdict(set)
    for e in editorial:
        anchor = e["anchor"].strip()
        anchors_by_target[e["target"]][anchor] += 1
        key = anchor.lower().strip(" .:!?\"'")
        if not anchor:
            weak_anchors.append({"source": e["source"], "target": e["target"], "anchor": "(empty / image with no alt)"})
        elif key in WEAK_ANCHORS:
            weak_anchors.append({"source": e["source"], "target": e["target"], "anchor": anchor})
        elif len(key) > 2:
            ambiguous[key].add(e["target"])
    ambiguous = {k: sorted(v) for k, v in ambiguous.items() if len(v) > 1}

    sections = defaultdict(lambda: {"pages": 0, "within": 0, "out": 0, "in": 0})
    for u in ok:
        sections[section(u)]["pages"] += 1
    for e in editorial:
        s, t = section(e["source"]), section(e["target"])
        if s == t:
            sections[s]["within"] += 1
        else:
            sections[s]["out"] += 1
            sections[t]["in"] += 1

    sitemap = set(corpus.get("sitemap", []))
    missing_from_sitemap = [u for u in ok if sitemap and u not in sitemap and not pages[u].get("in_sitemap")]
    sitemap_not_crawled = [u for u in sitemap if u not in pages]

    return {
        "home": home,
        "pages_200": len(ok),
        "total_links": len(edges),
        "editorial_links": len(editorial),
        "editorial_share": len(editorial) / max(1, len(edges)),
        "avg_editorial_out": sum(outbound_edit.values()) / max(1, len(ok)),
        "noindex_linked": [u for u in ok if u not in g["indexable"] and inbound_edit.get(u, 0) > 0],
        "orphans": orphans,
        "no_editorial_in": no_editorial_in,
        "dead_ends": dead_ends,
        "deep": deep,
        "depth": depth,
        "inbound": inbound,
        "inbound_editorial": inbound_edit,
        "outbound_editorial": outbound_edit,
        "pagerank": rank,
        "anchors_by_target": anchors_by_target,
        "weak_anchors": weak_anchors,
        "ambiguous_anchors": ambiguous,
        "sections": dict(sections),
        "broken": g["broken"],
        "unverified": g["unverified"],
        "complete": g["complete"],
        "urls_discovered": corpus.get("urls_discovered", 0),
        "redirected": g["redirected"],
        "external": g["external"],
        "missing_from_sitemap": missing_from_sitemap,
        "sitemap_not_crawled": sitemap_not_crawled,
        "pages": pages,
    }


def report(a: dict, limit: int = 12) -> str:
    L = []
    add = L.append
    add(f"# Internal linking — {a['pages_200']} pages returning 200\n")
    if not a["complete"]:
        add(
            f"⚠ Truncated crawl: {a['pages_200']} pages analysed out of {a['urls_discovered']} internal "
            f"URLs discovered. Depth, PageRank and orphan pages only hold within that scope — re-run "
            f"with a higher --max-pages before drawing conclusions.\n"
        )
    add(
        f"{a['total_links']} internal links, {a['editorial_links']} of them editorial "
        f"({a['editorial_share']:.0%}) — {a['avg_editorial_out']:.1f} editorial outbound link(s) per page.\n"
    )

    dist = Counter(a["depth"].get(u, 99) for u in a["pages"] if a["pages"][u]["status"] == 200)
    detail = ", ".join(f"{'4+' if d == 99 else d} click(s): {n}" for d, n in sorted(dist.items()))
    add(f"## Depth from the home page\n{detail}\n")
    if a["deep"]:
        add("Pages at 4 clicks or more (or unreachable):")
        for d, u in a["deep"][:limit]:
            add(f"  - {'unreachable' if d == 99 else str(d) + ' clicks'}: {u}")
        add("")

    if a["orphans"]:
        add(f"## Orphans — no inbound internal link at all ({len(a['orphans'])})")
        for u in a["orphans"][:limit]:
            add(f"  - {u}")
        add("")
    if a["no_editorial_in"]:
        add(
            f"## No inbound editorial link ({len(a['no_editorial_in'])}) — reachable only through the "
            f"menu, therefore never ranked by the structure"
        )
        for u in a["no_editorial_in"][:limit]:
            add(f"  - {u} ({a['pages'][u].get('words', 0)} words)")
        add("")
    if a["noindex_linked"]:
        add(
            f"## noindex pages receiving editorial links ({len(a['noindex_linked'])}) — linking spent "
            f"for nothing"
        )
        for u in a["noindex_linked"][:limit]:
            add(f"  - {a['inbound_editorial'].get(u, 0)} link(s): {u}")
        add("")
    if a["dead_ends"]:
        add(f"## Editorial dead ends ({len(a['dead_ends'])}) — 250+ words, no outbound link in the copy")
        for u in a["dead_ends"][:limit]:
            add(f"  - {u} ({a['pages'][u].get('words', 0)} words)")
        add("")

    add("## Best-served pages (internal PageRank)")
    values = sorted(a["pagerank"].values())
    if values and values[0] > 0 and values[-1] / values[0] < 1.2:
        add(
            "  Undifferentiated graph: every page is worth the same, because each one links to almost "
            "every other (mega-menu). That is the finding itself — the structure promotes nothing."
        )
    for u, r in sorted(a["pagerank"].items(), key=lambda kv: -kv[1])[:limit]:
        add(f"  - {r * 100:5.2f} %  {a['inbound_editorial'].get(u, 0):>3} editorial inbound  {u}")
    add("")

    if a["weak_anchors"]:
        add(f"## Non-descriptive anchors ({len(a['weak_anchors'])})")
        for x in a["weak_anchors"][:limit]:
            add(f"  - \"{x['anchor']}\": {x['source']} → {x['target']}")
        add("")
    if a["ambiguous_anchors"]:
        add(f"## Identical anchors pointing at several pages ({len(a['ambiguous_anchors'])})")
        for anchor, targets in list(a["ambiguous_anchors"].items())[:limit]:
            add(f"  - \"{anchor}\" → {len(targets)} targets: {', '.join(targets[:3])}")
        add("")

    single = [
        (u, list(c.keys())[0]) for u, c in a["anchors_by_target"].items()
        if len(c) == 1 and sum(c.values()) >= 3
    ]
    if single:
        add(f"## One anchor repeated (over-optimisation risk) ({len(single)})")
        for u, anchor in single[:limit]:
            add(f"  - \"{anchor}\" ×{sum(a['anchors_by_target'][u].values())} → {u}")
        add("")

    add("## Sections (first URL segment) — editorial links")
    add(f"  {'section':<24} {'pages':>5} {'within':>7} {'out':>5} {'in':>5}")
    for s, v in sorted(a["sections"].items(), key=lambda kv: -kv[1]["pages"])[:limit]:
        label = s if len(s) <= 24 else s[:21] + "…"
        add(f"  {label:<24} {v['pages']:>5} {v['within']:>7} {v['out']:>5} {v['in']:>5}")
    add("")

    if a["broken"]:
        add(f"## Broken internal links ({len(a['broken'])})")
        for x in a["broken"][:limit]:
            add(f"  - {x['reason']}: {x['source']} → {x['target']}")
        add("")
    if a["unverified"]:
        add(
            f"## Unverified links — targets outside the crawled scope "
            f"({sum(a['unverified'].values())} links to {len(a['unverified'])} URLs)"
        )
        add("  These are NOT broken links: those pages were simply never fetched.")
        for u, n in a["unverified"].most_common(limit):
            add(f"  - ×{n} {u}")
        add("")
    if a["redirected"]:
        add(f"## Internal links pointing at a redirect ({len(a['redirected'])})")
        for x in a["redirected"][:limit]:
            add(f"  - {x['source']} → {x['target']} ⇒ {x['to']}")
        add("")
    if a["sitemap_not_crawled"]:
        add(f"## In the sitemap but not crawled ({len(a['sitemap_not_crawled'])})")
        for u in a["sitemap_not_crawled"][:limit]:
            add(f"  - {u}")
        add("")
    if a["missing_from_sitemap"]:
        add(f"## Crawled but missing from the sitemap ({len(a['missing_from_sitemap'])})")
        for u in a["missing_from_sitemap"][:limit]:
            add(f"  - {u}")
        add("")

    if a["external"]:
        top = ", ".join(f"{h} ({n})" for h, n in a["external"].most_common(8) if h)
        add(f"## Most-cited outbound domains\n  {top}\n")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Internal link graph of a crawl.py corpus.")
    ap.add_argument("corpus")
    ap.add_argument("--limit", type=int, default=12, help="rows per section")
    ap.add_argument("--json", default="", help="also write the raw metrics")
    args = ap.parse_args()

    a = analyse(load(args.corpus))
    print(report(a, args.limit))
    if args.json:
        raw = {
            k: (dict(v) if isinstance(v, (Counter, defaultdict)) else v)
            for k, v in a.items()
            if k not in ("pages", "anchors_by_target", "external")
        }
        raw["anchors_by_target"] = {u: dict(c) for u, c in a["anchors_by_target"].items()}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
