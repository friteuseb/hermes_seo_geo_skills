---
name: seo-semantic-geo
description: "Editorial SEO audit: internal links, semantics, GEO."
version: 1.0.0
author: Cyril Wolfangel (cyril.wolfangel@gmail.com, @friteuseb)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [seo, geo, internal-linking, semantics, content, audit]
    related_skills: [french-seo-writing]
---

# Editorial SEO audit: internal linking, semantics and GEO

## When to Use

- "audit the SEO of <site>", "is my internal linking any good?", "are these two pages
  cannibalising each other?", "why does ChatGPT never cite my site?"
- A redesign, a new site, a site inherited from someone else: you need to know what the
  site actually *says* and how its pages hold together.
- Before writing new content: find out which pages already cover the topic.

**Do not use this skill** for technical performance (TTFB, page weight, caching, Core Web
Vitals) — it measures no timings at all. The two concerns are complementary and do not
overlap.

**This skill does not measure** rankings, search volumes, backlinks or traffic: it has no
access to Search Console or any third-party data. It analyses what the site publishes.
Never present a similarity score or an internal PageRank as a Google position.

## Doctrine

An audit is a **read**. Never modify the site being audited, even with access: produce
recommendations and, where useful, the patch or the copy ready to paste. The owner decides.

On sites you do not own, stay under 30 pages and keep the default delay. An audit crawl
should not show up in someone else's logs.

## Three things not to confuse

| | What it is | The lever |
|---|---|---|
| **Boilerplate linking** | menu, footer, breadcrumb — on every page | close to none: what is everywhere ranks nothing |
| **Editorial linking** | links placed in the body copy | the real lever: it says which page matters |
| **Semantic proximity** | two pages cover the same subject | tells you *where* to place a link, or *which* pages to merge |

The central trap of any linking audit is counting menu links. On a 50-page site with a
12-item menu, 90% of internal links are boilerplate and carry no information. The scripts
separate the two: the `nav`/`header`/`footer`/`aside` tags, plus a statistical test — an
anchor+target pair appearing on more than 60% of pages is boilerplate whatever its markup.

## Workflow

Standard-library Python 3 plus `requests`. No BeautifulSoup, no lxml, no jq — deliberately,
so it runs anywhere, including a Raspberry Pi with nothing installed. Run the commands below
from the skill's own directory, so that `scripts/crawl.py`, `scripts/linking.py`,
`scripts/semantics.py` and `scripts/geo.py` resolve.

### 1. Crawl once, analyse three times

```bash
python3 scripts/crawl.py https://example.com --max-pages 150
```

Writes `/tmp/seo-example-com.json` and prints progress per page. It starts from the sitemap
declared in `robots.txt` **and** from a breadth-first walk of the home page: the gap between
the two is already a result (pages in the sitemap that nothing links to, pages linked but
never declared).

Options: `--max-pages` (default 150), `--delay` (pause between batches, default 0.2 s),
`--workers` (default 4), `--ignore-robots` (only on a site you are responsible for, and say
so in the report).

### 2. Internal linking

```bash
python3 scripts/linking.py /tmp/seo-example-com.json
```

Reports: click depth from the home page, orphan pages, pages with **no** inbound editorial
link, editorial dead ends, internal PageRank, non-descriptive anchors, identical anchors
pointing at different targets, noindex pages receiving editorial links, broken links, links
to a redirect, sitemap discrepancies, and a table per section (first URL segment).

### 3. Semantics

```bash
python3 scripts/semantics.py /tmp/seo-example-com.json
```

TF-IDF over unigrams and bigrams, cosine similarity. In order of usefulness:

- **Linking opportunities**: pairs of close pages (0.30 to 0.70) *not* linked editorially,
  with the most discriminating shared terms — those are the anchors to use. This is the
  most actionable output of the skill.
- **Near-exact duplication** (≥ 0.95): grouped by cluster of URLs, not listed pair by pair.
  Almost always a URL parameter or pagination with no `canonical`.
- **Cannibalisation** (≥ 0.70): two pages chasing the same intent.
- Pages isolated from the lexical field, thin content, broken heading hierarchy, titles out
  of step with the copy, duplicate `title`/H1/`description`.

Tunable: `--linking-threshold 0.30`, `--cannibalisation-threshold 0.70`, `--thin-words 250`.
On a very homogeneous site (one offer in several flavours), raise the cannibalisation
threshold to 0.80 before concluding, or the whole site looks like itself.

Stop-word lists for English and French are both applied, so mixed corpora work.

### 4. GEO — readability for generative engines

```bash
python3 scripts/geo.py /tmp/seo-example-com.json
```

Four separate axes, never merged into one score:

- **Access**: crawlers that *answer* (OAI-SearchBot, ChatGPT-User, PerplexityBot,
  Claude-SearchBot, Googlebot, Bingbot…) versus *training* crawlers (GPTBot, ClaudeBot,
  Google-Extended, CCBot…). Blocking the latter is a legitimate editorial choice; blocking
  the former removes any chance of being cited. Also checks for `/llms.txt`.
- **Rendering**: these crawlers do not execute JavaScript. A page with 40 words of text in
  300 KB of HTML is empty to them.
- **Citability**: every heading section is scored out of 6 (self-contained length 60–220
  words, heading phrased as a question, answer in the first sentence, no carry-over from the
  previous paragraph, figures worth quoting). The weakest passages are listed with their
  exact shortcoming.
- **Attribution**: JSON-LD types found, `sameAs`, identifiable author, detectable date,
  external sources cited.

`--offline` skips the `robots.txt` / `llms.txt` probes.

## Reading the results

**Depth.** Past 3 clicks from the home page a page barely exists. A page marked
"unreachable" is reachable through the sitemap but through no link at all: the worst case —
indexable and recommended by nothing.

**No inbound editorial link.** The most common symptom and the cheapest to fix: the page is
in the menu, therefore "accessible", but no copy anywhere recommends it. Search engines and
generative engines read the opposite of an importance signal.

**Internal PageRank.** Read the spread, not the absolute value: the question is "are the top
pages the ones that make money?". Legal notices in the top three means the footer outweighs
the editorial.

**Similarity.** 0.30–0.50 = same universe, two complementary pages → link them. 0.50–0.70 =
overlap worth watching. > 0.70 = pick a primary page and point the other at it (or merge).
> 0.95 = not cannibalisation at all, the same page under several URLs: a `canonical` fixes it.

**Citability.** A mean of 4/6 is good. Below 3 the copy runs as continuous prose: it reads
well, but no block can be lifted and quoted without its context. The fix is not to write
more, it is to segment and answer first.

## Pitfalls

- **A truncated crawl lies about depth.** If `crawl.py` stops at `--max-pages` before seeing
  everything, `linking.py` says so at the top of its report. Depth, PageRank and orphan pages
  then only hold inside the fetched scope: re-run higher before telling an owner a page is
  orphaned. Links whose target was never fetched are counted separately, under "unverified" —
  they are not broken links.
- **A flat internal PageRank is not a bug.** When every page is worth the same, it is because
  each links to almost every other: a mega-menu flattens the structure. The script says so
  explicitly; that is the finding, not a failure.
- **An 8-page brochure site has no linking to optimise.** Say so instead of producing 15
  recommendations: on that format the real levers are topic coverage and citability, not the
  graph.
- **The corpus only holds what is served as HTML.** A client-rendered site (React/Vue with no
  SSR) yields a corpus with no text — which is itself the single most important finding of the
  audit, not a script failure. Check the "Rendering" axis of `geo.py` before concluding
  anything about semantics.
- **URL parameters inflate everything.** The crawler keeps non-advertising parameters
  (`?variant=x`) because their duplication is a genuine finding. Do not count those URLs as
  distinct pages in the figure you quote to the owner.
- **Semantic proximity is not search intent.** Two pages at 0.75 on a blog may be two
  legitimate angles. Look at the titles and H1s before recommending a merge, and propose
  rather than decide.
- **Never recommend "keyword density".** It is not a ranking signal, the scripts do not
  compute it, and writing it discredits the whole report.

## The report to hand over

The owner is not a developer. Fixed structure:

1. **What the site says** — the measured lexical field, in one sentence. If it does not match
   the actual business, that is the first finding of the report.
2. **Three numbers**: pages returning 200, editorial share of internal links, pages with no
   inbound editorial link.
3. **What is working**, with the measurement behind it. A healthy site deserves to be told so.
4. **Three to five fixes ranked by effort against effect**, each with the exact URL, the exact
   action, and for links: source page, target page and proposed anchor. An internal link
   recommended without its anchor is not a recommendation.
5. **GEO at the end, kept separate**: crawler access, rendering, citability. Do not mix "fix a
   broken link" with "rewrite this so ChatGPT quotes it" — different effort, different horizon.

Thresholds, vocabulary and edge cases: `references/thresholds.md`.
