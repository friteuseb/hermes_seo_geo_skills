# Thresholds, scales and edge cases

Load this only when a number has to be justified to an owner, or when a result falls outside
the usual range and you need to decide whether it is a defect or just the format.

## Internal linking

| Measure | Comfortable | Worth flagging | Serious |
|---|---|---|---|
| Depth from the home page | ≤ 3 clicks | 4 clicks | ≥ 5, or reachable only through the sitemap |
| Editorial outbound links per content page | 3 to 8 | 1 to 2 | 0 on a page of 250+ words |
| Editorial inbound links on a key page | ≥ 3 | 1 to 2 | 0 |
| Editorial share of internal links | > 25% | 10–25% | < 10% (the site is held together by its menu alone) |
| Distinct anchors to one target | 2 to 5 | a single one repeated 3+ times | one exact anchor sitewide |

**Anchors.** A good anchor describes the destination out of context. "read more", "click
here", "discover" describe none. The same anchor leading to two different pages is worse
than a weak anchor: it contradicts the meaning.

**Sections.** In `linking.py`'s per-section table, a section with many pages and zero
editorial links within it (`within = 0`) is a list, not a silo: its pages do not recommend
one another. A section with `in = 0` is connected to the rest of the site by the menu only.

## Semantics

| Cosine similarity | Reading | Action |
|---|---|---|
| < 0.20 | unrelated subjects | none |
| 0.20–0.30 | distant neighbourhood | none, unless you are building a silo |
| 0.30–0.50 | complementary | place an editorial link each way |
| 0.50–0.70 | overlap | check the intents, sharpen the angles |
| 0.70–0.95 | cannibalisation | primary page plus redirect, or merge |
| ≥ 0.95 | same content, several URLs | `canonical`, no rewriting |

These thresholds are for TF-IDF over a single-site corpus. They are not comparable to the
scores of an embedding model, which has a different scale entirely.

**Thin content.** 250 words is an alert threshold, not a target. A 60-word contact page is
normal; a 120-word service page cannot demonstrate anything. Word count is not a ranking
factor: what a thin page lacks is topic coverage, not volume.

**Title out of step.** The script flags pages whose strongest body terms appear in neither
the `title` nor the H1. That is often an unpersonalised template title — check before
concluding there is an editorial problem.

## GEO

**Two kinds of crawler.** *Answering* crawlers fetch a page to build an answer and cite the
source: blocking them removes all visibility. *Training* crawlers collect for model training:
blocking them is a licensing choice that does not affect citations. Never present a GPTBot or
CCBot block as a mistake — ask about the intent.

| Engine | Answering crawler | Training crawler |
|---|---|---|
| ChatGPT | OAI-SearchBot, ChatGPT-User | GPTBot |
| Claude | Claude-SearchBot, Claude-User | ClaudeBot |
| Perplexity | PerplexityBot, Perplexity-User | — |
| Google (AI Overviews, AI Mode) | Googlebot | Google-Extended |
| Copilot | Bingbot | — |
| Apple | Applebot | Applebot-Extended |

Google's AI Overviews and AI Mode draw on the classic index: there is no extra crawler to
allow, and blocking Googlebot blocks them too.

**`llms.txt`** is a proposed convention, not a standard the engines have adopted. Its absence
is not a defect; its presence guarantees nothing. Offer it as a bonus, never as a priority fix.

**Citability scoring** (`geo.py`, 6 points per section):

| Points | Criterion |
|---|---|
| 2 | length between 60 and 220 words (1 point for 40–60 or 220–320) |
| 1 | section heading phrased as a question |
| 1 | direct answer in the first sentence (a definition, or a sentence of ≤ 35 words) |
| 1 | does not open by carrying over the previous paragraph |
| 1 | at least two quotable figures |

Mean ≥ 4: good. 3 to 4: improvable. < 3: continuous prose, not extractable.

**What cannot be measured here**: brand presence off-site (Wikipedia, Reddit, YouTube,
LinkedIn mentions) weighs more on AI citations than everything above, and no script can
measure it from the site itself. Say so explicitly rather than implying citability is solved
by rewriting paragraphs.

## Edge cases

- **Multilingual sites.** `crawl.py` collects `hreflang`. Two language versions of one page
  often come back highly similar while nothing is wrong: discard pairs whose `lang` differs
  before calling it cannibalisation.
- **Online shops.** Product pages in one range are legitimately close. The useful signal is
  not their similarity but their linking: a product with no inbound link from a category page
  or a guide cannot be sold.
- **Blogs.** The right question is not "do these two posts look alike" but "is there a pillar
  page tying them together". A cluster of posts at 0.4–0.6 with nothing gathering them is a
  missing pillar page.
- **One-page sites.** None of these measures apply. Go straight to citability and attribution.
