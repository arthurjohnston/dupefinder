# The 15% floor: what lowering or removing it would do

Run 2026-10-11, after the user asked "what would happen if we lowered or even removed the 15%" overlap
floor for leads (`gi_rank_leads.py --min-pct 15`). Nothing in the production pipeline was changed: every
step read `data/gi.sqlite3` read-only and wrote to `data/floor_experiment/` (its own `floor.sqlite3`, its
own verification library). Scripts: `floor_common.py`, `floor_volume.py`, `floor_rw_recall.py`,
`floor_select.py`, `floor_rescue.py`, `floor_measure.py`, `floor_verify.sh`, `floor_compare.py`,
`floor_inspect.py` (all new; no production script was edited).

## Summary

- **Volume.** 41.6M of the 117.2M document pairs sit below 15%. Removing the floor would add **5.97M new
  tier-4 DOI pairs** to today's 1.87M; a 10% floor adds 1.18M, a 5% floor 3.62M. Most of the sub-15% pool
  exists only because `--min-shared` is 5 at 1-in-8 (84% of it shares 5-9 samples).
- **Abstracts** say almost nothing: 1,152 of the 5.97M have an abstract check, a biased leftover from the
  pilot. At 10-15% they share a 20+ word run as often as 15-30% pairs do (4-6% vs 5%).
- **Full text, random cross-author sample** (4,800 pairs, 1,200 per band, no retractions): 227 had both
  PDFs. The pipeline's own verdict ("real overlap" with no byline overlap) fires on 25 of them, but on
  reading the shared passages only about **6 are plausible copying of someone else's text**. The rest are
  publisher back matter, repository rights blocks, templated report series, stock maths preliminaries and
  same-group papers the byline check missed. Per both-PDF pair, plausible copying is **0 of 92 at 0-5%, 2 of
  63 at 5-10% and 5 of 90 at 10-15%** (these counts include the earlier strat_s16a sub-15% pairs), against
  **7 of 44 in the 15-30% band** of the existing tier-4 samples.
- **Shape.** A real copy below 15% looks just like one at 15-30%: 300-800 matched words, the longest run
  47-189 words, introduction, background or method paragraphs lifted into a later paper (6-26% of the
  shorter one). It is not a long section dropped into a long paper. A low index share mostly means the
  *other* paper is long.
- **Calibration** (Retraction Watch plagiarism papers that appear only below 15%; test set, not in the
  sample): with both PDFs, **16 of 21 pairs at 5-10% and 6 of 11 at 10-15% are real overlap**, mostly
  copied methods sections in paper-mill-style cancer biology. When one side is already known to be bad,
  the sub-15% partners are worth checking.
- **Recommendation: keep 15% as the floor for the general queue; don't remove it.** Lower it only for
  targeted expansion: the other partners of a pair already confirmed as copying, down to 5%. A general 10%
  floor would add an estimated ~2,300 confirmable cases (range ~700-5,100) but costs about 3x as many
  retrievals per case as the untouched 15-30% tier-4 pool (estimated ~6,000). Work that pool first.

## 1. Volume below the floor

`floor_volume.py`: one pass over `leads` joined to `pairs` (117,217,664 document pairs, 17 min). `pairs`
is sorted by containment, so each band is a pair_id range (`floor_common.band_ranges()`). The script
re-applies `gi_rank_leads.py`'s tier rules at any share; above 15% its tiers agree with the stored ones
on every one of the 75.6M rows (0 mismatches), so the counts below 15% are what `--min-pct` would give.
Bands are the larger side's share (`max(pct_a, pct_b)`), as everywhere else. `gi_report.py` ran with
`--min-containment 0`, so `leads` does hold every sub-15% pair with its filter reason (checked: none of
them has a tier).

### Document pairs, and what the filters keep

| band | document pairs | kept by the filters | kept, no shared author | kept, shared author | kept, authors unknown | no metadata |
|---|---|---|---|---|---|---|
| 0-2.5% | 4,535,134 | 2,777,101 | 1,010,539 | 573,661 | 1,192,901 | 1,187,980 |
| 2.5-5% | 9,692,955 | 6,812,067 | 2,190,220 | 3,449,676 | 1,172,171 | 1,520,080 |
| 5-7.5% | 10,246,197 | 7,551,994 | 2,052,240 | 4,591,242 | 908,512 | 1,252,584 |
| 7.5-10% | 7,628,349 | 5,605,115 | 1,393,942 | 3,547,213 | 663,960 | 900,479 |
| 10-12.5% | 5,418,430 | 3,909,988 | 924,627 | 2,481,735 | 503,626 | 655,109 |
| 12.5-15% | 4,086,946 | 2,849,817 | 647,123 | 1,789,389 | 413,305 | 524,517 |
| **all < 15%** | **41,608,011** | **29,506,082** | **8,218,691** | 16,432,916 | 4,854,475 | 6,040,749 |
| 15-30% | 12,676,190 | 7,153,127 | 1,436,264 | 4,454,633 | 1,262,230 | 1,685,918 |
| 30-50% | 9,945,334 | 2,974,456 | 466,906 | 1,874,081 | 633,469 | 1,098,645 |
| 50-70% | 10,354,467 | 1,664,280 | 262,880 | 989,510 | 411,890 | 878,793 |
| 70-90% | 28,713,216 | 1,555,161 | 347,621 | 793,380 | 414,160 | 2,140,727 |
| 90%+ | 13,920,446 | 339,675 | 65,012 | 161,513 | 113,150 | 1,145,774 |

35% of all document pairs sit below 15%, and they are the ones the filters keep most: 71% kept below 15%
against 4% at 70%+ (the filters' "same work" and "too short" reasons are mostly high-share). Below 15%,
shared-author pairs outnumber cross-author ones 2:1 (a group's own papers sharing methods text).

### What each floor would add (DOI pairs, each counted once, at its highest-share document pair)

| band | tier 4 (index evidence only) | tier 2 (abstracts share a 20+ word run) | tier 1 (plagiarism retraction) | tier 3 (other retraction) | partner unidentified, under tier 5's 50% |
|---|---|---|---|---|---|
| 12.5-15% | 490,475 | 32 | 365 | 750 | 197,440 |
| 10-12.5% | 684,949 | 16 | 572 | 1,138 | 255,999 |
| 7.5-10% | 1,008,007 | 2 | 742 | 1,576 | 363,657 |
| 5-7.5% | 1,434,276 | 0 | 874 | 1,844 | 510,739 |
| 2.5-5% | 1,536,285 | 0 | 497 | 1,170 | 536,657 |
| 0-2.5% | 818,522 | 0 | 71 | 156 | 234,365 |
| *today, >= 15%* | *1,865,479* | *21,879* | *1,493* | *2,349* | |

Cumulative, new tier-4 DOI pairs: floor 12.5% **+0.49M**, 10% **+1.18M** (+63% on today's 1.87M), 7.5%
+2.18M, 5% **+3.62M** (+194%), 2.5% +5.15M, 0% **+5.97M** (+320%). Tier 2 barely moves (+48 at 10%, none
below 7.5%) because almost no sub-15% pair has had its abstracts checked (section 2): it is not evidence
that their abstracts don't overlap. Partner-unidentified pairs below 50% never become leads (tier 5 needs
50%), so they don't count; a lower floor changes nothing for them.

### How much of this is the other thresholds

- **Min shared samples.** Production `pairs` is the 1-in-8 run with `--min-shared` **5** (gi_blocks.py
  `MIN_SHARED_BY_RATE = {4: 10, 8: 5}`, ~40 shared 5-grams), not 10 as HOW_LEADS_ARE_FLAGGED.md says. Below
  15% that threshold is what bounds the volume: 6.93M of the 8.22M kept cross-author document pairs there
  share only 5-9 samples (91% at 0-2.5%, 65% at 12.5-15%). With 10 shared samples required, the sub-15%
  cross-author pool would be 1.29M document pairs, not 8.22M. Above 15% the same 5-9 share is 44% (15-30%).

  | band | kept cross-author doc pairs | 5-9 shared | 10-19 | 20-49 | 50-99 | 100+ |
  |---|---|---|---|---|---|---|
  | 0-2.5% | 1,010,539 | 916,998 | 85,297 | 8,116 | 124 | 4 |
  | 2.5-5% | 2,190,220 | 2,000,099 | 161,353 | 27,317 | 1,413 | 38 |
  | 5-7.5% | 2,052,240 | 1,804,378 | 216,410 | 28,489 | 2,726 | 237 |
  | 7.5-10% | 1,393,942 | 1,117,687 | 240,445 | 31,295 | 4,010 | 505 |
  | 10-12.5% | 924,627 | 669,435 | 214,184 | 37,218 | 3,108 | 682 |
  | 12.5-15% | 647,123 | 419,347 | 183,623 | 40,183 | 3,249 | 721 |
  | 15-30% | 1,436,264 | 631,945 | 529,621 | 243,572 | 25,673 | 5,453 |

- **Min samples (25, "too short").** Never applies below 15%: 5 shared samples under 15% of the smaller side
  means that side has at least 34 samples. In turn the low bands are long documents only: a pair under 5%
  needs both sides at 100+ samples (~800 5-grams), under 2.5% at 200+.

## 2. Abstract evidence that already exists

`abstract_checks` holds 385,043 checked pairs, but `run_gi.sh` checks only the >= 15% bands, so almost no
sub-15% pair has one: 1,152 of the 5.97M new cross-author DOI pairs. Those 1,152 are not a random sample:
every sub-15% row in `abstract_checks` comes from the pilot-era TSV runs at 1-in-4 sampling
(`leads_s1`, `leads_s01`, `leads_s01_alt`, `leads_s0123_alt`; 1,343 rows of any author relation), none from
the 16-shard check of 2026-10-08 (367,750 rows, all >= 15%). Most were at or above 15% in the run they were
checked in and fell below it at 1-in-8, so they lean towards the top of the range.

| band | checked cross-author DOI pairs | abstracts share a 20+ word run | longest run 15-19 | identical (artifact) |
|---|---|---|---|---|
| 0-7.5% | 8 | 0 | 0 | 0 |
| 7.5-10% | 45 | 2 (4%) | 0 | 0 |
| 10-12.5% | 272 | 16 (6%) | 10 | 0 |
| 12.5-15% | 827 | 32 (4%) | 32 | 1 |
| 15-30% (comparison) | 194,073 | 9,675 (5.0%) | 6,424 | 78 |
| 30-50% (comparison) | 59,032 | 6,173 (10.5%) | 2,763 | 29 |

So at 10-15% the abstract hit rate is about that of 15-30% (4-6% vs 5%), on a biased sample. That is not
PILOT_FINDINGS.md's 0 of 72 at < 15% (slice 1, 1-in-4); that test had 72 pairs over the whole 0-15%
range, and the 1,152 here say nothing about 0-7.5%. Checking abstracts for a random sub-15% sample would
need OpenAlex (not run: see "Not done").

## 3. Full text below the floor

### The sample

`floor_select.py --run floor_s1 --per-band 1200 --seed 7`: random pair_ids drawn inside each band's range,
kept when the pair passes the same eligibility as `gi_verify.py select --per-band` (filters kept, no shared
author, both sides titled, not an identical-abstract artifact), and also: **neither side is in Retraction
Watch for any reason**; the DOI pair has no document pair at >= 15% (else it is already above the floor);
not already in `fulltext`; one per DOI pair. 4 bands x 1,200 = 4,800 pairs, 9,552 papers (2,700 without a
DOI, retrieved by title through Crossref, as production does). Excluded while drawing: 11 for a retraction,
85 for a document pair already >= 15%, 2 already measured.

Retrieval and measurement (`floor_verify.sh floor_s1 arthur.j.johnston@gmail.com`, 4 h 25 min):
`retrieve_papers.py` (Crossref + Unpaywall, 2 workers) -> `floor_rescue.py` (every Unpaywall
`oa_locations` entry for papers whose first URL failed; 212 of 1,620 rescued) -> `extract_papers.py` ->
`gi_verify.load_text` -> `floor_measure.py`, which uses production's settings exactly (10-word exact
shingles, lockstep X-drop 3, documents of 300+ words, `wrong_pdf_side()`, "real overlap" at >= 300 matched
words, the byline check on the first paragraphs plus PDF page 1) but writes to `floor.sqlite3`. 1,365 of
9,540 retrieval entries downloaded (14.3%).

### Per band (the pipeline's own verdicts)

| band | pairs | both PDFs | real overlap | ...no byline overlap | wrong PDF | no real overlap |
|---|---|---|---|---|---|---|
| 0-5% | 1,200 | 92 (7.7%) | 10 | 7 | 2 | 80 |
| 5-10% | 1,200 | 52 (4.3%) | 9 | 3 | 0 | 43 |
| 10-12.5% | 1,200 | 55 (4.6%) | 20 | 10 | 2 | 33 |
| 12.5-15% | 1,200 | 28 (2.3%) | 6 | 5 | 0 | 22 |
| *strat_s16a 5-10% (earlier run)* | 162 | 11 | 0 | 0 | 0 | 11 |
| *strat_s16a 10-15% (earlier run)* | 131 | 7 | 3 | 2 | 0 | 4 |
| *15-30%: strat4_s16 + strat_s16a (tier 4)* | 1,282 | 44 (3.4%) | 21 | 10 | 1 | 22 |

The low bands have the higher both-PDF rate because they hold only long documents (section 1), which are
more often open access: physics, maths and open-access book chapters.

### What the "real overlap" pairs actually are

I read the longest shared passages of every real-overlap pair (`floor_inspect.py`; the full listings are in
`data/floor_experiment/inspect_floor_s1.txt` and `inspect_compare.txt`). This is a reading of the shared
text, not a REVIEWING.md review. Bylines were not checked beyond the automatic check, and nothing has gone
into `reviews`.

Below 15% (floor_s1): of the 45 real-overlap pairs, 20 have byline overlap (the same authors reusing their
own text). The 25 without:

| what the shared text is | pairs |
|---|---|
| IntechOpen book back matter (publisher address block, "Available from" citation) | 4 |
| other publisher/repository boilerplate (a journal's advertising and disclaimer pages; Lund University's "General rights" block) | 2 |
| templated report series (USGS water-resources programme reports; DSDP explanatory notes) | 2 |
| one collaboration's figure text (ALICE) | 1 |
| stock maths preliminaries (standard definitions and lemmas, licence line) | 3 |
| probably the same group, or unclear (equations reused across papers, "in our group", likely co-authors the byline check missed) | 7 |
| **plausible copying of another group's prose** | **6** |

So the 300-word threshold doesn't separate copying from boilerplate at this level of overlap: a publisher
back-matter block alone is 300+ words, and at 15%+ the real overlap usually dwarfs it.

In the 15-30% comparison band, the same reading of its 21 real-overlap pairs gives 11 with byline overlap,
1 collaboration, 2 unclear and **7 plausible copies**. Plausible copying per both-PDF pair:

| band | plausible copies / both-PDF pairs | 95% interval | with the unclear ones |
|---|---|---|---|
| 0-5% | 0 / 92 | 0-3.9% | 1 / 92 |
| 5-10% | 2 / 63 | 0.4-11% | 2 / 63 |
| 10-15% | 5 / 90 | 1.8-12.5% | 9 / 90 |
| 15-30% | 7 / 44 | 6.6-30% | 9 / 44 |

(The 5-10% and 10-15% rows include strat_s16a's sub-15% pairs: 1 plausible copy, a wavelet-analysis passage,
in its 7 at 10-15%.) Both counts are small, so the intervals overlap. The point estimate falls by about
3x from 15-30% to 10-15%, and to nothing at 0-5%.

### Examples (plausible copies below 15%; DOIs only, no names)

| band | index share | pair (earlier -> later) | full text | longest run | what |
|---|---|---|---|---|---|
| 12.5-15 | 14.6% / 4.8% | 10.5772/31435 (2012 book chapter) -> 10.20546/ijcrbp.2016.307.017 (2016) | 736 words, 17.6% of the later paper | 74 | background on vacuum impregnation, five 49-74 word passages |
| 12.5-15 | 13.5% / 9.7% | gi:9176d183... (no DOI, routing-protocol comparison) / 10.7763/ijiet.2012.v2.171 | 688 words, 25.7% of the shorter | 189 | DSDV/AODV/DSR descriptions, 110-189 word runs (could be a shared third source, e.g. RFC text) |
| 10-12.5 | 12.2% / 2.0% | 10.1126/science.291.5501.79 (2001) -> 10.1088/1367-2630/9/1/017 (2007, 33k-word review) | 555 words, 12.4% of the Science paper, 1.7% of the review | 47 | sentences of a short paper scattered through a long review |
| 10-12.5 | 11.9% / 3.6% | 10.4236/health.2013.58a4005 (2013) -> 10.5455/ijmsph.2014.020820142 | 334 words, 11.4% of the shorter | 56 | introduction sentences |
| 5-10 | 9.6% / 6.8% | 10.5772/38007 / 10.5430/wje.v2n2p95 | 789 words, 12.4% | 93 | background and quoted passages on immigrant youth |
| 5-10 | 4.2% / 5.5% | 10.1186/1742-4755-10-S1-S2 (2013) -> 10.18203/2320-1770.ijrcog20164500 | 323 words, 5.8% | 47 | opening epidemiology sentences |

### Does a real copy below 15% look like "a section lifted into a long paper"?

Mostly not. The plausible copies below 15% look like the ones at 15-30%. Median matched words are 555 (range
323-789) against 533 (312-1,220) at 15-30%; median longest run 56 (47-189) against 76 (49-141). The largest
contiguous block (runs within 30 words merged) is 70-690 words. The copied text is introduction, background
or method description, spread over a few paragraphs, and it is 6-26% of the shorter paper in full text.
What puts these pairs below 15% is mostly length: the *other* paper is long (a 13,600-word book chapter, a
33,000-word review), or the 1-in-8 sample caught only 5-12 of the shared 5-grams. All seven share 5-12
samples, so a stricter `--min-shared 10` below 15% would have lost four of them.

## 4. Calibration: Retraction Watch plagiarism papers below the floor (test set only)

`floor_rw_recall.py`: of rw_recall's 697 plagiarism retractions (year <= 2020, in the slice 0-3 metadata),
217 are a side of a ranked lead today. A lower floor would make 133 more a side of a would-be lead: 60 at
10-15%, 57 at 5-10%, 16 at 0-5%. That is a pair existing, not the right source found. Every identified,
not-yet-found pair (any `is_plagiarism` record) went into a separate test run,
`floor_select.py --run floor_rw --rw-calibration` + `floor_verify.sh floor_rw ...`: 1,204 DOI pairs, 38
with both PDFs.

| band | both PDFs | real overlap | ...no byline overlap |
|---|---|---|---|
| 0-5% | 6 | 0 | 0 |
| 5-7.5% | 12 | 8 | 8 |
| 7.5-10% | 9 | 8 | 7 |
| 10-12.5% | 5 | 2 | 1 |
| 12.5-15% | 6 | 4 | 3 |

17 distinct retracted papers have a real-overlap partner, 7 of them in rw_recall's definition (of 10 with
a both-PDF partner). The shared text is almost all copied methods paragraphs (Western blot, MTT, Transwell,
luciferase protocols, 400-900 words, runs 40-220) in cancer-biology papers of the paper-mill kind, plus
review passages and a few licence lines. These retractions are often for data or image plagiarism, so the
text partner is not necessarily what got the paper retracted. But the reuse is real, at 5-10% index
overlap, and in nearly every pair checked. With a high prior (one side known to be bad), the sub-15% band
is high-yield. With the random prior it is not (section 3).

## 5. Recommendation

**Keep `--min-pct 15` for the general tier-4 queue. Don't remove it.**

Estimates below are extrapolations: band pool x this run's both-PDF rate x the plausible-copy rate of
section 3. They rest on 5-7 events per band and on my reading of the shared text, not on reviews.

| floor | extra tier-4 DOI pairs | est. confirmable cross-author copies (low / best / high) | random pairs to retrieve per case found |
|---|---|---|---|
| 15% (today; 15-30% band only, for comparison) | — (1.09M in 15-30%) | 2,500 / 6,000 / 11,000 | ~180 |
| 10% | +1.18M | +740 / +2,300 / +5,100 | ~520 |
| 5% | +3.62M | adds 5-10%: +410 / +3,400 / +11,600 | ~730 |
| 0% | +5.97M | adds 0-5%: 0 / 0 / +7,100 (0 seen in 92) | none found |

- **0-5%: no value seen.** 0 plausible copies in 92 measured pairs, and the band would add 2.35M DOI pairs.
- **10-15% and 5-10%: real but thin.** Per retrieved pair they yield about a third (10-15%) to a fifth
  (5-10%) of the 15-30% band. And 1.87M tier-4 pairs at >= 15% are still unworked, at the better rate. A
  lower floor only makes sense once that pool is exhausted, and then 10% before 5%.
- **Lower it for targeted expansion now.** For a pair a human has confirmed as copying, list that paper's
  other partners down to 5%. Section 4 shows sub-15% partners of known-bad papers are real reuse in most
  pairs checked. This is a small set (a confirmed paper has a handful of sub-15% partners), not a batch.
  Retraction Watch pairs themselves stay test-only.
- **Any sub-15% batch needs a stricter full-text verdict first.** Below 15%, the boilerplate a pair shares
  (IntechOpen back matter, repository rights blocks, licence lines, publisher advertising) is the same size
  as the real overlap, and 300 matched words is reached on boilerplate alone: 7 of the 25 no-byline "real
  overlap" pairs were that. Something like `compare_two_papers.drop_furniture_runs()` plus a back-matter
  pattern list would need to be in `measure` before such pairs enter `to_review`.
- **Don't tighten min-shared below 15% to cut volume.** It would drop four of the seven plausible copies found.

## 6. Not done, and caveats

- **No abstract check for a random sub-15% sample.** It needs OpenAlex (`gi_abstract_check.py`), which this
  experiment was not allowed to call. For the 4,800 sampled pairs it would be about 6,850 DOIs / 50 = ~140
  filter calls, inside the free daily budget ($0), plus whatever share already sits in the `abstracts`
  cache. Ask before running it.
- **No OpenAlex rescue.** `gi_rescue_pdfs.py`'s title search and OpenAlex locations were skipped
  (`floor_rescue.py` is Unpaywall only). In strat_s16a the rescue added 123 of its 577 downloads (about a fifth), so
  both-PDF rates here are somewhat below production's. The 15-30% comparison runs had it. Precision per
  measured pair shouldn't depend on it.
- **No year correction** (`--fix-years` is OpenAlex), so the earlier -> later direction in the examples comes
  from the index metadata and the DOIs. It is not checked.
- **Plausible copying is my reading of the shared passages**, with the automatic byline check. It is not a
  REVIEWING.md review: no PDF bylines read by hand, no citation check, no third-source search. The same
  reading was applied to the 15-30% comparison pairs, so the comparison is like for like. The absolute
  numbers are not confirmed cases.
- **HOW_LEADS_ARE_FLAGGED.md says `--min-shared 10`**. Production `pairs` (`r8_c100_m5`) used 5, gi_blocks.py's
  default at 1-in-8. Worth correcting there.

## Files and commands

All under `general_index/` (`data/` is gitignored; the inspection listings name authors and stay there).

```bash
cd general_index
nice -n 10 ionice -c3 ../.venv/bin/python3 floor_volume.py          # section 1-2 counters -> data/floor_experiment/volume.json (17 min)
nice -n 10 ionice -c3 ../.venv/bin/python3 floor_rw_recall.py       # section 4 recall, rw_pairs table (2 min)
nice -n 10 ../.venv/bin/python3 floor_select.py --run floor_s1 --per-band 1200 --seed 7
./floor_verify.sh floor_s1 arthur.j.johnston@gmail.com              # retrieve -> rescue -> extract -> load-text -> measure (4.5 h)
nice -n 10 ../.venv/bin/python3 floor_select.py --run floor_rw --rw-calibration
./floor_verify.sh floor_rw arthur.j.johnston@gmail.com              # test set (50 min)
../.venv/bin/python3 floor_compare.py                               # 15-30% comparison from strat_s16a/strat4_s16 (read-only)
../.venv/bin/python3 floor_inspect.py --run floor_s1 > data/floor_experiment/inspect_floor_s1.txt
```

`data/floor_experiment/floor.sqlite3`: `sample` (the drawn pairs, index figures), `draws` (exclusions per
band), `results` (full-text measurements per pair, run floor_s1 / floor_rw), `rw_pairs` (every sub-15%
pair with a plagiarism retraction on one side), `compare` (the 15-30% comparison). PDFs and libraries are
in `data/floor_experiment/verify_floor_s1/` and `verify_floor_rw/`. Nothing was written to `gi.sqlite3`
(`fulltext`, `local_pdfs`, `reviews` untouched).
