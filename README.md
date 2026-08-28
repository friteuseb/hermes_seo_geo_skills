# hermes_seo_geo_skills

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill tap for editorial SEO:
internal linking, semantics, and readability for generative engines (GEO).

## Install

```bash
hermes skills tap add friteuseb/hermes_seo_geo_skills
hermes skills search seo
hermes skills install seo-semantic-geo
```

## What is in here

### `seo-semantic-geo`

Audits what a site *says* and how its pages hold together. It measures no page-load timings:
this is the editorial half of SEO, not the technical one.

| Script | What it produces |
|---|---|
| `crawl.py` | JSON corpus of the site (editorial text, headings, links with anchors, JSON-LD, canonical) |
| `linking.py` | click depth, orphans, internal PageRank, anchors, broken links, sections |
| `semantics.py` | linking opportunities, cannibalisation, duplication, thin content |
| `geo.py` | generative-engine crawler access, rendering without JS, passage citability, attribution |

The site is crawled once; the three analyses read the same corpus.

**The idea the skill is built on:** separate *boilerplate* linking (menu, footer — present on
every page, therefore incapable of ranking anything) from *editorial* linking, which is the
only lever anyone can pull. Detection is twofold: the `nav`/`header`/`footer`/`aside` tags,
plus a statistical test — an anchor+target pair on more than 60% of pages is boilerplate
whatever its markup.

The most actionable output is `semantics.py`'s **linking opportunities**: pairs of pages that
are semantically close but not linked editorially, each with the shared terms that make the
best anchor text.

### Requirements

Python 3.9+ and `requests`. Nothing else — no BeautifulSoup, no lxml, no jq — so it runs on a
Raspberry Pi with a bare Python install. English and French stop-word lists are both applied,
so mixed-language corpora work.

### Standalone use

The scripts have no dependency on Hermes and can be run directly:

```bash
python3 skills/seo-semantic-geo/scripts/crawl.py https://example.com --max-pages 150
python3 skills/seo-semantic-geo/scripts/linking.py /tmp/seo-example-com.json
python3 skills/seo-semantic-geo/scripts/semantics.py /tmp/seo-example-com.json
python3 skills/seo-semantic-geo/scripts/geo.py /tmp/seo-example-com.json
```

Be a good citizen on sites you do not own: keep `--max-pages` low and leave the default delay.

## Licence

MIT.
