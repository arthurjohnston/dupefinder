# How a General Index pair becomes a lead

Written down 2026-10-11, after the user asked "we are flagging a pair when how many indices are shared by the
pair?". There is no single count: a pair has to pass four thresholds in turn, then the filters, then it is
given a tier.

## The thresholds

1. **Sampling** (`gi_sample_5grams.py`). Not every 5-gram is kept. Each one's lowercased text is hashed
   (blake2b) and kept only when `hash % SAMPLE == 0`, so the same 5-gram is kept or dropped in every
   document. 1 in 8 for the 16-shard run (1 in 4 for the first four slices). A sampled 5-gram that
   occurs in more than 20 documents (`gi_pairs.py --max-group 20`) is ignored as stock phrasing.
2. **At least 10 shared sampled 5-grams** (`gi_pairs.py --min-shared 10`). This is what makes two
   documents a pair at all. At 1 in 8 that is roughly 80 shared 5-grams in the full text.
3. **Both papers at least 25 sampled 5-grams long** (`gi_report.py --min-samples 25` at 1 in 8; 200 / rate
   in `run_gi.sh`), about 200 5-grams in full. Shorter documents get reason "too short": a handful of shared
   phrases would be a large share of almost nothing.
4. **The shared samples are at least 15% of one paper's sampled 5-grams** (`gi_rank_leads.py --min-pct 15`).
   This is the real cut-off for a lead, and the source of the 15-30%, 30-50%, ... bands. Pairs with an
   unidentified side (tier 5) need 50% (`--unidentified-min-pct 50`).

A pair that passes still goes through `gi_filters.py` (same work, shared authors, catalogue artifacts such
as one PDF under two DOIs or a conference supplement) before it is ranked.

So a typical lead shares hundreds of 5-grams; a short paper sharing about 80 still counts if that is 15% of
it. The index's 5-grams are lemmatized and skip windows that start or end on a stopword, so these counts
are not comparable with the full-text check (exact 10-word runs, `compare_two_papers.py`).

**What this misses.** Anything under 10 shared samples or under 15% never becomes a lead: lighter copying,
such as a page or two lifted into a long paper, mostly sits below 15%. Every estimate built on the leads
is a lower bound for that reason. The 15% floor was chosen on the slice 1 pilot (PILOT_FINDINGS.md,
"Verification"): 0 of 72 pairs under 15% shared any abstract text. That is an abstract-level test only;
body reuse under a different abstract would not show up in it. FLOOR_EXPERIMENT.md (when written) tests
the floor on full text.

## The tiers (`gi_rank_leads.py`)

| tier | meaning | DOI pairs (2026-10-11) |
|---|---|---|
| 1 | a Retraction Watch retraction for plagiarism on one side | 1,493; test set only |
| 2 | the two abstracts share a 20+ word verbatim run (and aren't identical) | 21,903; all full-text checked |
| 3 | a Retraction Watch retraction for another reason on one side | 2,350; skipped (already retracted) |
| 4 | everything else the filters kept: index evidence only | 1,870,267 |
| 5 | one side unidentified (1.93M), or a near-identical copy, usually one PDF held twice (35k) | 1.97 million |

## Estimates (2026-10-11)

Copying of someone else's work only (cross-author or data copying), pairs not papers.

| tier | confirmable from the PDFs: low / best / high | behind paywalls: low / best / high |
|---|---|---|
| 2 | 300 / 410 / 490 | 2,800 / 7,500 / 9,300 |
| 4 | 200 / 1,000 / 6,000 | 3,000 / 30,000 / 170,000 |
| 5 | ~0 | can't estimate |

Tier 2: 1,137 of 21,903 pairs had both PDFs (5.2%); 665 of those are real overlap with no shared author on
the bylines (58%); 77% of the AI-reviewed ones are copying of others; the range allows for the AI being
wrong 5-40% of the time (no human check yet). Tier 4 rests on 2 cases in 49 checked pairs; the strat4_s16
sample (5,000 pairs) should give 150-200 checked pairs.

## Counts at each step (2026-10-11, 16 shards at 1 in 8)

| step | document pairs |
|---|---|
| pairs at all (>= 10 shared sampled 5-grams) | 117,217,664 |
| >= 15% on one side | 75,609,653 |
| ... and both sides >= 25 sampled 5-grams | 54,004,538 |
| ranked into tiers 1-5 after the filters | 4,888,077 (3,862,332 DOI pairs) |

So the 15% floor removes about a third of the pairs; the filters (shared authors, same work, catalogue
artifacts, neither side identified) remove most of the rest. Document pairs exceed DOI pairs because the
index holds some PDFs twice under one DOI; the tier table and the estimates count DOI pairs
(`primary_pair = 1`).
