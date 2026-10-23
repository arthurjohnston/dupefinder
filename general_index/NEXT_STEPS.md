# General Index pilot: state and next steps

Handoff for the next session, written 2026-10-06 ~05:30 after the overnight run. The full record
(every measurement, each filter rule and why it exists, dead ends) is in `PILOT_FINDINGS.md`; this
file is the short version.

## Rules carried over from the user

- **Do not start any more n-gram slice downloads** until the user says so. Slices 0-3 are the working
  set. (Metadata/keyword downloads serving slices 0-3 are fine.)
- **OpenAlex API key** is in `~/.config/dupefinder/openalex_api_key` (outside the repo, mode 600),
  exported as `OPENALEX_API_KEY` by `~/.bashrc`. Never copy it into the repo, a command line, a log or
  a commit. Free budget $1/day (10,000 filter calls, resets midnight UTC); the account also holds a
  prepaid balance (~$81) drawn only past the free $1. Keep a day's runs under the free budget, and ask
  before any bulk *search* calls ($1 per 1,000).
- **Contact email** for Crossref/Unpaywall/OpenAlex in this pilot: arthur.j.johnston@gmail.com
  (approved by the user).
- **Files under `general_index/data/` name authors in unverified suspicions.** Same rule as
  `private/`: never copy them anywhere committed. `general_index/` as a whole is gitignored by the
  root `/*/` rule; nothing in it is committed. To commit the scripts later, add `!/general_index/`
  and `/general_index/data/` to `.gitignore` (it has the user's uncommitted edits; ask first).

## Where things stand (slices 0-3)

| | |
|---|---|
| Documents with 5-gram data | 26,268,878 (about 1/4 of the index's 107M) |
| ...with a DOI | 20,028,283 (76%): 17.8M slice metadata + 2.2M 2022 update |
| ...title only / nothing | 3,728,901 (14%) / 2,511,694 (10%) |
| Candidate pairs (>= 10 shared samples) | 6,734,753 |
| Cross-author leads at >= 15% (tiers 1-5) | 259,080, mostly unverified and noisy |
| Tier 1 (a Retraction Watch plagiarism retraction on one side) | 124 |
| Tier 2 (both abstracts share a 20+ word verbatim run) | 1,823 |
| Tier 1-2 pairs with both PDFs retrievable | 85 (456 of 3,367 papers downloaded; most are paywalled) |
| **...real shared text in the full text** | **68 distinct pairs** (+ 5 wrong-PDF, 6 no real overlap) |
| **...of which no author overlap in either PDF's opening text** | **44 distinct pairs** |
| Retraction Watch recall | 80 of 697 known plagiarism papers in slices 0-3 (~45% of cases whose source is in the index at all; crude) |

(Earlier messages said 74 and 47. Those counted a few pairs twice: the index holds some PDFs twice
under one DOI, so one DOI pair appears as two document pairs. 68 and 44 are distinct DOI pairs.)

**Review status:** 1 of the 44 reviewed so far. GI-01 is the same paper (AIRCC conference and journal
versions; the journal record's "title" is the journal name), so its page is removed and it is
recorded in the `reviews` table. **43 left, none read by hand.** They are leads, not cases.

## Known data caveat: dates from the 2022 update are PDF dates

A 2022-update (meta_alt) pub_date is the PDF's download/creation date, not the publication date: a 2006
PLoS Medicine paper came through as 2017-10-14. **277 of the 456 papers in the slice 0-3 verification
library had a wrong year** (corrected from OpenAlex on 2026-10-06). gi_report.py now prefers the
publisher record for any DOI the slice metadata also has, and leaves a 2022-update date blank
otherwise. Anything computed before that (earlier/later direction in old leads files) may be wrong.

## The workflow: one database, a table per step (since 2026-10-06)

Everything after streaming lives in **`data/gi.sqlite3`** (~12 GB for slices 0-3). Each script reads
the previous step's table and rebuilds only its own; `reviews` is never rebuilt.

| step | script | reads | writes |
|---|---|---|---|
| 1 stream + sample a slice | `gi_sample_5grams.py` | the archive.org zip | `slice*/part_XX.bin` + `docs.tsv` (files: ~1B rows/slice, read sequentially) |
| 1b metadata | `gi_db.py import-documents` | `slice*/docs.tsv`, `gi_meta.sqlite3` (meta + meta_alt) | `documents` (26,268,878), `update_dois` |
| 2 count pairs | `gi_blocks.py run / common / clusters / finalize` (see "Step 2 at 16 shards") | `slice*/part_XX.bin` | `pairs` (6,734,753 for 0-3 at 1-in-4, cap 20; + `shared_common`), `pair_blocks`, `common_phrases`, `phrase_clusters`, `phrase_cluster_summary` |
| 3a metadata join + filters | `gi_report.py` | `pairs`, `documents`, `reviews` | `leads` (reason, authors, band; one row per pair, 6,734,753) |
| 3b abstracts | `gi_abstract_check.py --email ...` | `leads`, `abstracts` cache | `abstracts` (per DOI), `abstract_checks` (17,293 pairs) |
| 3c ranking | `gi_rank_leads.py` | `leads`, `pairs`, `abstract_checks`, `retraction_watch` | `leads` tier/shape/lead_rank/primary_pair |
| 4 full text | `gi_verify.sh RUN EMAIL [--tiers 1 2 --limit N]` (= `gi_verify.py select` -> retrieve -> `gi_verify.py paths` -> extract -> embed -> `gi_verify.py measure`) | `lead_details`, `fulltext` | `fulltext`, `local_pdfs` |

PDFs (from run leads12_s16a, 2026-10-09) go to the external drive, `/mnt/papers/general_index/<run>/`
(`udisksctl mount -b /dev/sda1` if it isn't mounted; `PDF_ROOT=...` to put them elsewhere). Every
downloaded PDF's path is in `local_pdfs`, keyed by the index's document key (dkey), so a document with
no DOI has one too; `select` now includes no-DOI sides that have a title (retrieved by title through
Crossref), and `measure` finds each side's paper through `local_pdfs`. The 4-shard run's 456 PDFs are
backfilled there (they stay in data/verify_leads12_s0123/papers).
| 4b pages | `gi_write_pages.py [OUT_DIR]` | `fulltext`, `leads`, `pairs`, `reviews` | HTML pages, `fulltext.page`, `_index.tsv` |
| 5 your review | by hand: `insert into reviews (doi_a, doi_b, pair_lo, pair_hi, verdict, note, reviewed_at) ...` | | **`reviews`** (editable) |
| 6 Retraction Watch | `gi_db.py import-rw`, `gi_recall.py [--list]` | `../retraction_watch.csv`, `leads` | `retraction_watch`, view `rw_recall` |

`run_gi.sh [SHARDS] [RATE] [CAP] [DB]` (defaults `0-15 8 100 gi.sqlite3`) chains steps 2-3 and the recall
check. Full text (step 4) is run deliberately, on a chosen batch.

## Step 2 at 16 shards (gi_blocks.py, 2026-10-06)

`gi_pairs.py` paired all shards in one run: RAM and spill grow with the square of the corpus, so 16
shards don't fit. `gi_blocks.py` replaces it (gi_pairs.py is kept, unused):

- **Shard-pair blocks**: one combination of shards at a time (i <= j; 136 for 16), each reading only its
  two shards. A pair belongs to exactly one block, so the threshold is exact per block. Results go to
  `blocks_r{rate}_c{cap}[_m{min}]/pairs/block_i_j.npz` and `pair_blocks` (what's done, timings).
  Resumable and incremental: a shard still streaming is skipped, and the next `run` adds only the new
  blocks. Blocks made under an older frequency pass (a different shard set) are redone.
- **Global frequency pass** first: per hash partition, how many documents each sampled 5-gram is in,
  across every shard in the run, so a group-size rule means the same in every block.
- **Nothing is thrown away for being common** (the user's rule): phrases in 2..100 documents (was 20)
  are expanded into pairs; phrases in more than 100 go to the common-phrase store
  (`common/part_XX_*.npy`: hash, count, document list up to 1000 documents, a 100-document sample above),
  summarised in `common_phrases`. `common` adds `shared_common` per pair; `clusters` finds template
  families (common phrases whose document sets are >= 80% the same, MinHash-proposed, exactly checked)
  -> `phrase_clusters` / `phrase_cluster_summary`. Both stream one partition at a time.
- **Numpy document tables** (`docs_dkey.npy`, `docs_sampled.npy` per shard) instead of Python dicts.
- **Thresholds follow the sampling rate**: min shared samples 10 at 1-in-4, 5 at 1-in-8; `gi_report.py
  --min-samples` 50 at 1-in-4, 25 at 1-in-8 (run_gi.sh passes 200/rate). With the 1-in-4 thresholds,
  1-in-8 lost a third of the confirmed pairs (36 of 68) and recall fell to 75.

Measured on shards 0-3 (production `pairs`/`leads` in gi.sqlite3 are still the 1-in-4 / cap 20 run):

| run | pairs | ranked tiers 1/2/3 | RW recall | confirmed pairs ranked | time |
|---|---|---|---|---|---|
| 1-in-4, cap 20 (= old gi_pairs, **identical**: 6,734,753 pairs, same counts) | 6.73M | 110 / 1,639 / 129 | 80 / 697 | 65 / 68 | 4.0 h before the sort fix |
| 1-in-4, cap 100 | 10.22M | 137 / 1,656 / 205 | 92 (none of the 80 lost) | 65 / 68 | 49 min |
| 1-in-8, cap 100, thresholds 5 / 25 | 11.37M | 146 / 1,548 / 222 | 94 (18 gained, 4 lost vs cap 20) | 61 / 68 | 22 min |

Tier 2 for the new pairs is understated: the abstract check wasn't rerun on them (OpenAlex). The 4
Retraction Watch papers lost at 1-in-8 each sit on a threshold (a 14.3% share against the 15% floor,
24 samples against 25, a partner pair that no longer pairs): sampling noise, net +14. Experiment DBs:
`data/gi_exp_r4c100.sqlite3`, `data/gi_exp_r8c100m5.sqlite3` (slim copies: pairs/leads + small tables).

Cost per block, 1-in-8: self 3 min, cross 6.5 min, ~1 GB RAM, spill <= 4.6 GB (deleted after). The
frequency pass merges every shard per partition (~2.6 GB per worker at 4 shards, ~10.5 GB at 16, so it
runs 1 worker there, ~50 min). **All 16 shards: ~5 h of blocks (136 blocks, 3 workers)**, plus
`common`/`clusters`/`finalize`. Step 3 is parallel now (2026-10-06): `gi_report.py` splits pair_id
ranges across `--workers` processes (default cores - 4), 33 min -> 4 min 40 s on shards 0-3 with
identical output, so ~150M pairs should take ~1.5 h. `gi_rank_leads.py` takes ~30 s at 4 shards.

**Disk** for 16 shards at 1-in-8: samples ~6.4 GB per shard (~103 GB), blocks' spill ~14 GB at a time,
plus pairs/leads in gi.sqlite3. The 1-in-4 originals of shards 0-3 (`slice0..3`, ~52 GB) are now
redundant with `slice0_s8..slice3_s8` but kept until the user agrees to delete them; without that the
remaining downloads leave almost no free space.

**Views:** `lead_details` (leads + pairs + both documents' metadata), `to_review` (real overlap, no
byline overlap, no review yet, most overlap first: 43 rows now), `rw_recall`.

**Keys:** `pairs`/`leads` are per *document* pair (`pair_id`); the index holds some PDFs twice, so one
DOI pair can be several. DOI-level tables (`abstract_checks`, `fulltext`, `reviews`) carry `pair_lo`,
`pair_hi` = the two lowercased DOIs sorted. Count DOI pairs with `leads.primary_pair = 1`.

**Reviewing:** a `reviews` row with verdict `same paper` makes `gi_report.py` give that pair reason
`same work (reviewed)`; any review takes the pair out of `to_review`. Verdict vocabulary: `same paper`,
`copying`, `not copying`, `legitimate reuse`, `unsure`. Example:

```sql
insert into reviews (doi_a, doi_b, pair_lo, pair_hi, verdict, note, reviewed_at)
values ('10.x/a', '10.y/b', min(lower('10.x/a'), lower('10.y/b')), max(lower('10.x/a'), lower('10.y/b')),
        'copying', 'methods and results copied verbatim; bylines checked on both PDFs', date('now'));

select title_a, year_a, title_b, year_b, ft_pct_a, ft_pct_b, ft_matched_words   -- what's left to review
from to_review join lead_details l using (pair_lo, pair_hi) where l.primary_pair = 1;
```

The old TSVs (`report_s0123_alt.tsv`, `leads_ranked_s0123_alt.tsv`, `*.abstracts.tsv`, ...) are kept;
`gi_report.py --tsv` / `gi_rank_leads.py --tsv` still write that format. `reviewed_pairs.tsv` and
`gi_results.sqlite3` are retired (imported into gi.sqlite3; `gi_results_db.py`, which pointed here, removed 2026-10-22).

## Step 4.5: agent review (before the human)

`gi_agent_review.py` reads every full-text pair with real shared text that has no human verdict yet,
builds an evidence packet (metadata, both papers' opening text with the bylines, full-text and index
overlap figures, the 6 longest shared passages) and asks Claude (Opus 5.5, effort high, structured
output) for a triage verdict: likely_copying / same_authors_or_group / legitimate_reuse /
shared_third_source / artifact / unsure, plus confidence, which paper looks earlier, reasons, red
flags, and checks for the human. Results go to `agent_reviews`, keyed by prompt version.
**It never writes `reviews`**; `to_review_agent` shows pairs still awaiting a human with the agent's
latest verdict beside them, and a pair leaves it as soon as a human reviews it.

Follows REVIEWING.md: verdicts carry their REVIEWING.md agent equivalent (agent_decided_*), bylines
are read from page 1 of both actual PDFs (pdftotext) with every name compared, each title checked
against its own content, the 60 words before each shared passage shown for the third-party-quotation
check, each passage run through classify_dupes.classify_text, later-cites-earlier checked, and new
boilerplate shapes reported (not added) in possible_new_text_patterns. The non-optional steps are
enforced: a likely_copying verdict that skipped or failed one is stored as unsure, with the reason in
precondition_failures and the model's own answer in raw_verdict. Prompt version v2.

Needs an Anthropic credential: ANTHROPIC_API_KEY, `ant auth login`, or a key in
`~/.config/dupefinder/anthropic_api_key` (mode 600, outside the repo). `--dry-run` builds packets
and estimates cost with no billable call (67 pending pairs: about $5-6 in all with the v2 packets); `--max-cost` (default
$10) stops a run; `--show-packet` prints what the model sees. The SDK (`anthropic` 1.11) is installed
in .venv but not listed in requirements.txt.

## Where the 44 are

**Start at `data/fulltext_pages_s0123/index.html`**: every page ranked by the averaged overlap score
((ft_pct_a + ft_pct_b) / 2), with the type/severity/AI/human decisions from `results`; reviewed
same-paper pairs (GI-01) are listed separately without a page. Each page's evidence block now starts
with relative links to the local PDFs of both papers (the verification library's copies), so both
work opened from disk. Regenerate with `gi_write_pages.py` (`--no-db-writes` for a read-only run,
`--index-only` for just the index).

- **`to_review` view in `data/gi.sqlite3`** — the 43 still unreviewed (GI-01 is reviewed: same
  paper), with full-text % per side, matched words, longest run, page file. `fulltext` holds all 74
  measured pairs, `lead_details` the index evidence for any of them.
- `data/fulltext_pages_s0123/` — **one neutral HTML comparison page per pair** (GI-02 ... GI-47; the
  three duplicates GI-05, GI-14, GI-22 and the reviewed same-paper GI-01 removed), made by
  `gi_write_pages.py` from the database: `write_dupe_reports_html.py --paper-ids A,B --neutral
  --shingle-size 10 --x-drop 3` plus an "Overlap evidence" block (full text, then the index's
  evidence). Years are OpenAlex's, corrected. `_index.tsv` lists them. Not published: they name
  people over unverified suspicions.
- `data/leads_s0123_fulltext_report.txt` — rank_paper_pairs text report with a
  `compare_two_papers.py` rerun command per pair (library: `data/verify_leads12_s0123/library.sqlite3`).

## Queued full-text batches (2026-10-10)

The user doesn't want pairs where one paper is already retracted (tiers 1 and 3), except as test sets.
Three chained scripts, each waiting for the one before:

1. `run_qwen_eval_after_batch.sh`: the 56-pair local-model evaluation on Qwen3-30B, compared with Opus
   (`data/compare_qwen_vs_opus.tsv`). gpt-oss-20b's result: decision agrees 40/56, but it called 11 pairs
   "cross-author copying" that Opus didn't call a problem.
2. `run_next_batch_after_qwen.sh` (run `leads12_s16c`): every remaining tier 1-2 lead by rank. Tier 1
   is exhausted, so this is the 12,356 tier-2 pairs.
3. `run_tier45_sample_after_batch.sh`: random samples, stratified by the larger side's index overlap
   (`gi_verify.py select --per-band N --strat-tiers T --min-band B`):
   - `strat4_s16`: tier 4, 1,000 per band (15-30/30-50/50-70/70-90/90+), 5,000 pairs. Tier 4 holds
     1.87M DOI pairs: 1.56M with no abstract to check, 308k whose abstracts share no 20-word run.
   - `strat5_s16`: tier 5's "near-identical copy" kind (35k pairs, all at 70%+), 300 per band, 600
     pairs. Expected to be mostly the index holding one PDF twice; this measures it. The other kind (1.93M
     pairs) has a side with no metadata at all, so it can't be retrieved.
   - then `gi_check_index_text.py` over both (`data/index_text_check_tier45.tsv`): in the last random
     batch (strat_s16a) 12 of 16 measured 50%+ pairs were the index's wrong-DOI error, not overlap.

What to expect from strat_s16a's rates: about 4% of selected pairs end with both PDFs, so ~200 measured
tier-4 pairs, ~40 per band. Its yield by band (all tiers but 1 and 3): 50%+ 4 real of 16, 15-50% 6 of
11, 5-15% 2 of 12. Use the result to decide whether ranked tier-4 batches are worth it and from which band.

How a pair becomes a lead (the 5-gram thresholds, the tiers) and the current estimates per tier:
HOW_LEADS_ARE_FLAGGED.md.

## Next steps, in priority order

1. **Review the remaining 43 by hand** (REVIEWING.md). Record every verdict in
   the `reviews` table of `data/gi.sqlite3` (see "The workflow" above); a "same paper" verdict makes
   `gi_report.py` report that pair as "same work (reviewed)" on every rerun, so it isn't proposed
   again, and any verdict takes the pair out of the `to_review` view. Check the bylines on the PDFs themselves (the author
   metadata here comes from the index, which is noisy), that each PDF matches its own title (5 wrong
   PDFs already turned up), and whether the shared text is a common third source (a standard
   protocol, a guideline, a template). Known legitimate families to discard: USGS flood-inundation and
   geologic-map series (templated government reports), review/encyclopedia chapters by a paper's own
   authors under a book DOI. A pair that survives goes into `private/FLAGGED_CASES_INDEX.txt` like any
   other case.
2. **Deduplicate leads by DOI pair** in `gi_rank_leads.py`, and in `gi_report.py`'s output. The
   index's duplicate PDFs (same DOI, two dkeys) inflate every count by a few percent and produced
   duplicate pages.
3. **Get more full text for tier 1-2.** Only 13.5% of their papers downloaded. Things to try:
   OpenAlex OA locations (`query_openalex_batch()`, now affordable with the key) before Unpaywall; the
   landing-page `citation_pdf_url` fallback (`download_with_landing_page_fallback()`, which
   `retrieve_papers.py`'s plain path doesn't use); `rescue_via_europepmc.py` for biomedical papers.
   `list_manual_downloads.py` for the paywalled remainder, if the user wants to fetch any by hand.
   **JSTOR experiment (2026-10-19, `data/jstor_experiment/README.md`):** JSTOR Text Analysis Support
   (Constellate's successor; full-text OCR as JSON for an item-ID list, no n-grams any more) for the
   `10.2307/` sides. Tier 2 has 36 such DOI pairs, none full-text checked; JSTOR alone completes only 6
   (the other side is paywalled elsewhere in 29), so the test request is 7 items. Waiting on the user
   to submit it; then find out what the terms allow quoting on the comparison pages before going larger.
4. **Tier 4 (104,948, index evidence only)** is the biggest unexplored pool. Its top (sorted two-sided
   first) holds real-looking republications among artifacts. Options: an abstract check where
   abstracts exist (already done for all tier 4 pairs with DOIs; the rest lack abstracts), or
   full-text retrieval of a stratified sample to measure its precision before going further.
5. **Tier 5 (152,049, partner unidentified)** and the 424,151 pairs with neither side identified: the
   documents with no metadata at all (10%). Title -> DOI lookup by `gi_resolve_titles.py` (Crossref,
   `retrieve_papers.resolve_doi()`) measured 96% precision / 80% recall on clean titles but **has not
   been run** on the 2022 update's text-extracted titles. Spot-check it on those first. (Keyword ->
   title identification, `gi_identify.py`, failed: about 50% precision at best. Don't reuse it; it and
   `gi_keywords.py` were removed 2026-10-22, see git history.)
6. **Only then decide on more slices.** The user wants slices 0-3 worked through first. Lessons for
   any future streaming: run two or three slices in parallel (one stream is about 10-13 MB/s, two
   together about 24 MB/s); 1-in-8 sampling matches 1-in-4 at half the disk
   (`BUCKETING_IDEAS.md`); add the tortured-phrase lookup to `gi_sample_5grams.py` before streaming
   (todo.md, "Look up commonly known tortured phrases").
7. **Tortured phrases** (todo.md entry): collect the known catalogues. The index's 2-4-gram rows can
   be matched against them while a slice streams.

## How to rerun the pipeline (slices 0-3)

```bash
cd general_index
./run_gi.sh slice0 slice1 slice2 slice3      # pairs (~60 min, 12 GB RAM) -> report (~17 min) -> abstracts -> ranking -> recall
./gi_verify.sh tier12_batch2 arthur.j.johnston@gmail.com --tiers 1 2 --limit 500   # full text for a new batch
./gi_verify.sh tier24_aff_batch arthur.j.johnston@gmail.com --tiers 2 4 --limit 500 --order affiliation   # the same, leads with a DOI and a year on both sides first, then authors still at a US/European university (gi_affiliations.py; picks from the top 5 x --limit, --pool to change)
../.venv/bin/python3 gi_affiliations.py backfill --orcid   # refetch the whole OpenAlex/ORCID records (raw columns: affiliation history, ROR ids, raw affiliation strings, past ORCID jobs) for rows cached before they were kept; resumable, ~52k calls for the 2026-10-18 cache
./run_top30_batch.sh top30_s16   # 2026-10-21: full-text check of the top-30-university shortlist (data/top30_pairs.txt, gitignored: 600 pair_ids with an active top-30 author on the copying side, ranked by a model trained on checked pairs; 509 not yet checked). gi_verify.py select --pairs FILE takes any such list
#   then: 64 of its unchecked pairs need only PDFs a browser can usually get (publisher 403s, landing pages): data/verify_top30_s16/manual_downloads/_pending.md (+ .html, _manifest.json). Save them there, run ../../../.venv/bin/python3 ../../../import_manual_downloads.py in data/verify_top30_s16, then the paths/extract/load-text/measure/pages steps of run_top30_batch.sh (from "cd $W" on)
#   2026-10-21 hand round: 14 pairs downloaded by the user; real top-30-linked copying: GI-809 (#258) and GI-813 (#119, eosinophilic colitis); real copying but name-merged "top-30" authors: GI-810, GI-811; the rest self-reuse/collaborators/no overlap. Every pair on the list with different authors has now been tried (paywalled.txt). Gotcha: PDFs saved from Wiley Online Library (incl. Hindawi) carry a per-page "Downloaded from ... Terms and Conditions" stamp that matches between two such PDFs (GI-812 inflated by it) -- not stripped yet
./run_useu_batch.sh useu_s16   # 2026-10-22: more US/European cases: data/useu_pairs.txt = tier-4 leads whose copying paper prints a US/European university on its own byline (cached OpenAlex authorships, not name matching), no shared author id/surname, no co-authorship between the sides (one free OpenAlex call per pair), best model score first, 3,000. 82,604 such leads exist in all
./run_useu_loop.sh 48   # 2026-10-22: after useu_s16, rounds useu2_s16, useu3_s16, ... for 48 hours or until a round finds no real cross-author overlap (log data/useu_loop.log). Scripts in affiliation_hunt/ (README there), data in data/affiliation_hunt/. Also scoring tier 4's other 823k no-shared-author leads (gi_affiliations.py leads --sets rest4, paid credit, log data/affiliations_rest4.log); the loop rebuilds the list when that finishes
../.venv/bin/python3 gi_write_pages.py      # pages for full-text-confirmed, unreviewed pairs
```

After changing `gi_filters.py`, rerun from `gi_report.py` (then `gi_rank_leads.py`, `gi_recall.py`) so a
new rule can't quietly hide known cases: recall must stay at 80 of 697 for slices 0-3 unless the change
is meant to move it. Twice in the pilot a rule that looked right hid a known plagiarism pair.

## Scripts (all in general_index/)

| script | does |
|---|---|
| `gi_sample_5grams.py` | streams one n-gram slice zip, keeps a 1-in-N hash sample of 5-grams |
| `gi_blocks.py` | step 2 at scale: shard-pair blocks, global frequency pass, common-phrase store, shared_common, phrase clusters, finalize -> `pairs` |
| `gi_pairs.py` | the old single-run step 2 (superseded by gi_blocks.py; kept) |
| `gi_load_meta.py` / `gi_load_meta_alt.py` | slice metadata / 2022-update metadata into `gi_meta.sqlite3` |
| `gi_filters.py` | why a pair is not worth checking (same work, shared pages, one file twice, ...) |
| `gi_report.py` | metadata + filters + bands per pair -> `leads` |
| `gi_abstract_check.py` | OpenAlex abstracts for both sides, shared-run verdict |
| `gi_rank_leads.py` | tiers 1-5, two-sided before one-sided |
| `gi_verify.sh` / `gi_verify.py` | select -> retrieve -> extract -> embed -> measure, into `fulltext` |
| `gi_resolve_titles.py` | title -> DOI via Crossref (not yet run on text-extracted titles) |
| `gi_db.py` | the database: schema, views, `import-documents`, `import-rw`, `migrate` (one-off from the TSV era) |
| `gi_recall.py` | Retraction Watch recall from the `rw_recall` view |
| `gi_write_pages.py` | HTML comparison pages from `fulltext` (overlap-evidence block: full text, then index) |
| `run_gi.sh` | chains steps 2-3 + recall into the database |
| `gi_tortured.py` | tortured phrases: count them in the 5-grams, undo them, look for new pairs (none found: TORTURED_PHRASES.md) |

The one-off launchers of finished batches (`run_full16.sh`, `run_tortured.sh`, the local-model and tier 4/5
sample chains, ...) were removed 2026-10-22; their logs are in `data/`, the scripts in git history.
`run_band4_batch.sh` stays as the template for new batches.

Disk: `general_index/data/` is about 64 GB (the slice samples are 4 x ~13 GB). 145 GB free.
