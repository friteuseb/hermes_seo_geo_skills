#!/usr/bin/env python3
"""Semantic analysis of a crawl.py corpus — no external dependency.

    python3 semantics.py /tmp/seo-example.json [--cannibalisation-threshold 0.70]

Three outputs, in order of usefulness:

1. **Linking opportunities** — pairs of semantically close pages that are *not*
   linked editorially, with the shared terms that would make good anchor text.
2. **Cannibalisation** — pairs too close to justify being two separate pages.
3. **Coverage** — the site's lexical field, isolated pages, pages whose title does
   not describe what the page is about.

TF-IDF over unigrams and bigrams, cosine similarity on truncated vectors. English
and French stop-word lists are both applied, so mixed corpora work. No "keyword
density" anywhere: it is not a ranking signal.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict

STOP = set(
    """
the of and to in for you your with is are this that it we our on at as be from by or an will can
have has not was were been being do does did doing but if then than so such no nor only own same
too very just about into over under again further once here there when where why how all any both
each few more most other some what which who whom these those they them their there's i'm it's
""".split()
)
# French list, accent-stripped: these scripts were written for French sites first
# and a French corpus without them is unusable.
STOP |= set(
    """
au aux avec ce ces dans de des du elle en et eux il ils je la le les leur lui ma mais me meme mes
moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un
une vos votre vous c d j l a m n s t y ete etee etees etes etant suis es est sommes sont serai
seras sera serons serez seront serais serait serions seriez seraient etais etait etions etiez
etaient fus fut fumes futes furent sois soit soyons soyez soient fusse fusses fussions fussiez
fussent ayant eu eue eues eus ai as avons avez ont aurai auras aura aurons aurez auront aurais
aurait aurions auriez auraient avais avait avions aviez avaient eumes eutes eurent aie aies ait
ayons ayez aient eusse eusses ceci cela celui celle ceux celles donc dont ici quel quelle quels
quelles sans si sous entre vers chez tout tous toute toutes autre autres meme memes aussi tres
plus moins bien fait faire peut peuvent doit doivent etre avoir cette cet leurs lorsque apres
avant depuis pendant contre selon afin ainsi alors car comme quand encore deja toujours jamais
chaque tel telle non oui
""".split()
)
# Interface vocabulary: present everywhere, carrying no subject matter. Without
# this filter, "read more" and "view details" top the lexical field of any site
# whose every block carries a button.
STOP |= set(
    """
read more view click here learn discover home menu back share print newsletter cookies next
previous page pages download contact subscribe search login signup lire suite voir cliquez
cliquer savoir accueil retour partager imprimer telecharger decouvrir contactez precedente
""".split()
)
NON_WORD = re.compile(r"[^a-zà-öø-ÿ0-9]+", re.I)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokens(text: str) -> list[str]:
    text = text.replace("’", "'").replace("'", " ")
    words = [w for w in NON_WORD.split(text.lower()) if w]
    return [w for w in words if len(w) >= 3 and strip_accents(w) not in STOP and not w.isdigit()]


def terms(text: str) -> Counter:
    """Unigrams plus bigrams; the bigrams carry most of the domain meaning."""
    t = tokens(text)
    c = Counter(t)
    c.update(f"{a} {b}" for a, b in zip(t, t[1:]))
    return c


def vectors(docs: dict[str, str], keep: int = 80) -> dict[str, dict[str, float]]:
    tf = {u: terms(t) for u, t in docs.items()}
    n = max(1, len(docs))
    df: Counter = Counter()
    for c in tf.values():
        df.update(c.keys())
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}

    vecs = {}
    for u, c in tf.items():
        total = max(1, sum(c.values()))
        raw = {t: (f / total) * idf[t] for t, f in c.items()}
        top = dict(sorted(raw.items(), key=lambda kv: -kv[1])[:keep])
        norm = math.sqrt(sum(v * v for v in top.values())) or 1.0
        vecs[u] = {t: v / norm for t, v in top.items()}
    return vecs


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def candidate_pairs(vecs: dict[str, dict[str, float]], min_shared: int = 2) -> set[tuple[str, str]]:
    """Inverted index: only compare pages that share strong terms."""
    index = defaultdict(list)
    for u, v in vecs.items():
        for t in v:
            index[t].append(u)
    count: Counter = Counter()
    for pages in index.values():
        if len(pages) > 60:  # term too common to discriminate anything
            continue
        for i, a in enumerate(pages):
            for b in pages[i + 1 :]:
                count[(a, b) if a < b else (b, a)] += 1
    return {p for p, n in count.items() if n >= min_shared}


def editorial_pairs(corpus: dict) -> set[tuple[str, str]]:
    alias = {}
    for p in corpus["pages"]:
        alias[p["url"]] = p["url"]
        if p.get("final_url"):
            alias.setdefault(p["final_url"], p["url"])
    pairs = set()
    for p in corpus["pages"]:
        for l in p.get("links", []):
            if l["internal"] and not l["boilerplate"]:
                target = alias.get(l["target"])
                if target:
                    pairs.add((p["url"], target))
    return pairs


def heading_issues(headings: list[dict]) -> list[str]:
    issues = []
    levels = [h["level"] for h in headings]
    h1 = levels.count(1)
    if h1 == 0:
        issues.append("no H1")
    elif h1 > 1:
        issues.append(f"{h1} H1s")
    previous = 0
    for n in levels:
        if previous and n > previous + 1:
            issues.append(f"skips H{previous}→H{n}")
            break
        previous = n
    empty = sum(1 for h in headings if not h["text"].strip())
    if empty:
        issues.append(f"{empty} empty heading(s)")
    return issues


def analyse(corpus: dict, low: float, high: float) -> dict:
    pages = {
        p["url"]: p
        for p in corpus["pages"]
        if p["status"] == 200 and p.get("words", 0) >= 40 and "noindex" not in p.get("robots_meta", "")
    }
    if len(pages) < 2:
        return {"pages": pages, "error": "corpus too small for a semantic analysis"}

    docs = {
        u: f"{p.get('title', '')} {' '.join(h['text'] for h in p.get('headings', []))} {p.get('text', '')}"
        for u, p in pages.items()
    }
    vecs = vectors(docs)

    sims = []
    for a, b in candidate_pairs(vecs):
        s = cosine(vecs[a], vecs[b])
        if s >= low:
            sims.append((s, a, b))
    sims.sort(reverse=True)

    # Near-exact duplication: grouped rather than enumerated as N² pairs. It is
    # almost always an unhandled URL parameter or pagination.
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s, a, b in sims:
        if s >= 0.95:
            parent[root(a)] = root(b)
    groups: dict[str, list[str]] = defaultdict(list)
    for u in {u for s, a, b in sims if s >= 0.95 for u in (a, b)}:
        groups[root(u)].append(u)
    duplicates = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=len)
        duplicates.append(
            {
                "urls": members,
                "canonical": [pages[u].get("canonical", "") for u in members],
                "title": pages[members[0]].get("title", ""),
            }
        )
    duplicated = {u for d in duplicates for u in d["urls"]}

    linked = editorial_pairs(corpus)
    cannibalisation, opportunities = [], []
    for s, a, b in sims:
        if s >= 0.95 and a in duplicated and b in duplicated:
            continue
        shared = sorted((t for t in vecs[a] if t in vecs[b]), key=lambda t: -(vecs[a][t] + vecs[b][t]))[:5]
        item = {
            "similarity": round(s, 3),
            "a": a,
            "b": b,
            "title_a": pages[a].get("title", ""),
            "title_b": pages[b].get("title", ""),
            "terms": shared,
            "linked": (a, b) in linked or (b, a) in linked,
        }
        if s >= high:
            cannibalisation.append(item)
        elif not item["linked"]:
            opportunities.append(item)

    neighbours = defaultdict(int)
    for s, a, b in sims:
        neighbours[a] += 1
        neighbours[b] += 1
    isolated = [u for u in pages if neighbours.get(u, 0) == 0]

    by_title, by_h1, by_desc = defaultdict(list), defaultdict(list), defaultdict(list)
    for u, p in pages.items():
        if p.get("title"):
            by_title[p["title"].strip().lower()].append(u)
        h1 = next((h["text"] for h in p.get("headings", []) if h["level"] == 1), "")
        if h1:
            by_h1[h1.strip().lower()].append(u)
        if p.get("description"):
            by_desc[p["description"].strip().lower()].append(u)

    cards = []
    for u, p in pages.items():
        h1 = next((h["text"] for h in p.get("headings", []) if h["level"] == 1), "")
        top = list(vecs[u])[:8]
        named = set(tokens(f"{p.get('title', '')} {h1}"))
        off_title = [t for t in top[:4] if " " not in t and t not in named]
        cards.append(
            {
                "url": u,
                "words": p.get("words", 0),
                "title": p.get("title", ""),
                "h1": h1,
                "terms": top,
                "off_title": off_title,
                "headings": heading_issues(p.get("headings", [])),
                "no_description": not p.get("description"),
            }
        )
    cards.sort(key=lambda c: c["words"])

    lexical: Counter = Counter()
    for u in pages:
        for t, v in vecs[u].items():
            lexical[t] += v

    return {
        "pages": pages,
        "vectors": vecs,
        "duplicates": duplicates,
        "cannibalisation": cannibalisation,
        "opportunities": opportunities,
        "isolated": isolated,
        "cards": cards,
        "duplicate_titles": {k: v for k, v in by_title.items() if len(v) > 1},
        "duplicate_h1": {k: v for k, v in by_h1.items() if len(v) > 1},
        "duplicate_descriptions": {k: v for k, v in by_desc.items() if len(v) > 1},
        "lexical_field": lexical.most_common(25),
    }


def report(a: dict, limit: int = 15, thin: int = 250) -> str:
    if "error" in a:
        return f"# Semantics\n{a['error']}"
    L = []
    add = L.append
    add(f"# Semantics — {len(a['pages'])} pages analysed\n")

    add("## Lexical field as a machine reads it")
    add("  " + ", ".join(t for t, _ in a["lexical_field"][:20]) + "\n")

    if a["opportunities"]:
        add(f"## Linking opportunities ({len(a['opportunities'])}) — close pages, not linked")
        for o in a["opportunities"][:limit]:
            add(f"  - {o['similarity']:.2f}  {o['a']}\n         ↔  {o['b']}")
            add(f"         candidate anchors: {', '.join(o['terms'])}")
        add("")

    if a["duplicates"]:
        add(
            f"## Near-exact duplication ({len(a['duplicates'])} group(s)) — one page served under "
            f"several URLs"
        )
        for d in a["duplicates"]:
            canon = {c for c in d["canonical"] if c}
            state = f"canonical: {', '.join(sorted(canon))}" if canon else "no canonical declared"
            add(f"  - {len(d['urls'])} URLs, \"{d['title'][:60]}\" — {state}")
            for u in d["urls"][:6]:
                add(f"      {u}")
        add("")

    if a["cannibalisation"]:
        add(f"## Likely cannibalisation ({len(a['cannibalisation'])})")
        for c in a["cannibalisation"][:limit]:
            state = "already linked" if c["linked"] else "not linked"
            add(f"  - {c['similarity']:.2f} ({state})\n      {c['a']}\n      {c['b']}")
            add(f"      shared terms: {', '.join(c['terms'])}")
        add("")

    if a["isolated"]:
        add(
            f"## Semantically isolated pages ({len(a['isolated'])}) — no neighbour above the "
            f"proximity threshold"
        )
        for u in a["isolated"][:limit]:
            add(f"  - {u}")
        add("")

    thin_pages = [c for c in a["cards"] if c["words"] < thin]
    if thin_pages:
        add(f"## Thin content (< {thin} words) ({len(thin_pages)})")
        for c in thin_pages[:limit]:
            add(f"  - {c['words']:>4} words: {c['url']}")
        add("")

    issues = [c for c in a["cards"] if c["headings"]]
    if issues:
        add(f"## Heading hierarchy ({len(issues)})")
        for c in issues[:limit]:
            add(f"  - {', '.join(c['headings'])}: {c['url']}")
        add("")

    off = [c for c in a["cards"] if len(c["off_title"]) >= 3]
    if off:
        add(f"## Title out of step with the content ({len(off)})")
        for c in off[:limit]:
            add(f"  - {c['url']}")
            add(f"      title: \"{c['title'] or '(empty)'}\"")
            add(f"      the copy is mostly about: {', '.join(c['off_title'])}")
        add("")

    for key, label in (
        ("duplicate_titles", "Identical title tags"),
        ("duplicate_h1", "Identical H1s"),
        ("duplicate_descriptions", "Identical meta descriptions"),
    ):
        if a[key]:
            add(f"## {label} ({len(a[key])})")
            for text, urls in list(a[key].items())[:limit]:
                add(f"  - \"{text[:70]}\" ×{len(urls)}: {', '.join(urls[:3])}")
            add("")

    no_desc = [c for c in a["cards"] if c["no_description"]]
    if no_desc:
        add(f"## No meta description ({len(no_desc)})")
        for c in no_desc[:limit]:
            add(f"  - {c['url']}")
        add("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Semantic analysis of a crawl.py corpus.")
    ap.add_argument("corpus")
    ap.add_argument("--linking-threshold", type=float, default=0.30)
    ap.add_argument("--cannibalisation-threshold", type=float, default=0.70)
    ap.add_argument("--thin-words", type=int, default=250)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    with open(args.corpus, encoding="utf-8") as f:
        corpus = json.load(f)
    a = analyse(corpus, args.linking_threshold, args.cannibalisation_threshold)
    print(report(a, args.limit, args.thin_words))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in a.items() if k not in ("pages", "vectors")}, f,
                      ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
