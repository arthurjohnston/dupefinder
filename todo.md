TODO
claude --resume a261980e-2e11-4826-82c5-4f35a7abe94a

The purpose of this project is to find plagiarisms as they are unethical and make science worse

# data sources
1. start pulling more docs. I want a few thousand papers in computer ethics and adjacent fields
	a. Look for a torrent source for arxiv
	b. Look for other free sources if need be such as their website. Ask me before downloading from a website though
2. I also need the publication data and hopefully the preprint date when I get it

3. I also need the list of citations.

4. Manually chase down papers retrieve_papers.py couldn't get automatically (no OA copy found,
   DOI unknown to Unpaywall, blocked download, etc.) via Google Scholar / publisher sites by hand.
   Now handled by list_manual_downloads.py / import_manual_downloads.py (README's "Runbook: getting a
   manually-downloaded paper into the pipeline"); the earlier export_missing_papers.py CSV dump was
   removed 2026-09-27 as superseded.

5. [2026-08-21] Pull directly from RetractionWatch's retraction database instead of relying on the
   general keyword-driven corpus to intersect with a known plagiarism case by chance -- see the
   "Full-corpus plagiarism audit" section below for why: 70,041 organically-retrieved papers found
   zero new real cross-author cases, while the one confirmed case in the whole project (Saxby/Taro)
   came from a RetractionWatch article, not the corpus. RetractionWatch publishes a retraction
   database with reason codes (plagiarism is one) and, where documented, a link to the original work
   -- pulling retraction records reason-filtered to plagiarism and feeding both the retracted paper
   and (when identifiable) its source through the existing retrieve_papers.py download path would
   give confirmed pairs at a much higher hit rate than hoping keyword search stumbles onto one.
   Not yet built -- todo.md itself, not a script.

   [DONE 2026-08-20] Theses/dissertations specifically were found to succeed at less than half the
   corpus-wide download rate (20% vs ~47%, checked directly against the 2,123 dissertation-type
   candidates OpenAlex found) -- mostly because OpenAlex/Unpaywall only have a repository landing
   page on file for these, not a direct PDF link (`doi_unknown_to_unpaywall` was ~49% of thesis
   failures, vs ~11% corpus-wide). Fixed generally, not as a one-off: retrieve_papers.py now looks
   for a `<meta name="citation_pdf_url">` tag (the standard convention most DSpace/EPrints/Bepress
   institutional repositories emit for exactly this) whenever a candidate URL turns out to be a
   landing page, before giving up on it. Confirmed against real previously-failed attempts already
   in state.sqlite3 (rescued real PDFs from McGill's and ANU's repositories that weren't reachable
   before). See CLAUDE.md's retrieve_papers.py section.

6. [DONE 2026-08-24] CORE (core.ac.uk) added as a new bulk-retrieval source specifically for
   no-DOI content -- only 2 of 70,041 papers in the corpus had no DOI before this, because both
   existing bulk scripts (Crossref, DataCite) can only find what a registrar knows about. CORE is a
   genuinely DOI-independent repository aggregator (keyless access works, real but restrictive rate
   limit -- `CORE_API_KEY` env var, mirroring `OPENALEX_API_KEY`, raises it substantially). New
   `bulk_retrieve_core.py`, resumable via a direct `state.sqlite3` lookup per candidate rather than
   the other bulk scripts' harvest-time known-DOI set (most CORE candidates have nothing to dedup
   on). First real run (with a key): 200 candidates harvested for keyword='computer ethics' (harvest
   then stopped early on a transient CORE timeout, not a real rate limit -- the harvest loop's
   stop-on-RetrievalError doesn't yet distinguish "actually rate limited" from "one slow request",
   so it's leaving most of the other ~24 keywords unharvested; worth a real per-keyword-retry fix
   before the next CORE run), 70 new papers downloaded.

   Same day: a real (non-dry-run) `bulk_retrieve_theses.py` (DataCite) run, 2,740 harvested
   candidates across the same keyword list, 278 new dissertations downloaded.

   Both batches turned out dirtier than expected, checked by hand before trusting either:
   - **Off-topic**: DataCite/CORE keyword search matches a query term anywhere in a record's
     metadata (abstract, subject tags, funder info), not just the title, so a nursing/food-security/
     Greek-tragedy/Buddhist-soteriology dissertation that happens to mention "ethics" in IRB
     boilerplate matches a "digital ethics" search with nothing to do with computers or AI. Only
     ~39% of the 265 downloaded thesis titles even loosely matched an on-topic term.
   - **Crank self-publishing**: DataCite's `resource-type-id=dissertation` filter is self-declared by
     the depositor, not verified by any institution -- Zenodo (DOI prefix `10.5281`) lets anyone tag
     an upload "dissertation". One depositor (pseudonymous authors "AIan, CIoud" / "Yamamoto, Takeo")
     has been re-uploading the same handful of amateur "AI consciousness"/"unified theory of
     everything" manuscripts under a fresh DOI every few days -- 7 separate DOIs for one title alone,
     6 for another, 4 for two more -- inflating search-result counts with non-academic content.

   Fixed with a new, reusable `filter_low_relevance_papers.py`, run *before* `extract_papers.py` ever
   sees a batch (moves the PDF to `papers_excluded/<reason>/` and relabels the `state.sqlite3` row
   `excluded_crank`/`excluded_offtopic` instead of `downloaded`, so nothing is deleted and
   `extract_papers.py`'s `WHERE status='downloaded'` query just skips it -- same non-destructive,
   auditable discipline as `find_duplicate_papers.py`/`review_dupes.py`'s corrections elsewhere).
   Crank detected generically (any normalized-title + DOI-prefix pair appearing 3+ times), not by
   hardcoding the specific titles/authors found this session. Off-topic detected by a deliberately
   loose (stem-level, not phrase-exact) title keyword match -- false negatives (keeping something
   borderline) are the cheaper failure than false positives here.

   Important gotcha that cost a wasted first pass: this script's natural scope ("every
   downloaded-but-not-yet-extracted row") collided with `--whole-prefix`'s 1,684 pending IAEME
   (`10.34218`) rows already sitting in `state.sqlite3` -- that batch was *deliberately*
   topic-agnostic (the whole point of a low-scrutiny-publisher catalog crawl), so the off-topic
   filter would have wrongly stripped most of it. Fixed with a `--since <ISO timestamp>` flag to
   scope a run to one specific batch by `updated_at`, rather than the whole backlog. Applied scoped
   to just this session's CORE+theses batch: 32 crank + 139 off-topic excluded, 175 on-topic papers
   left `status='downloaded'` and ready for `extract_papers.py`.

   Still open: the `--whole-prefix` flag itself (`bulk_retrieve_crossref.py`, crawls a publisher's
   entire catalog under `--doi-prefix` with no keyword restriction -- raised IAEME coverage from 56%
   to ~91%) and the pre-embedding boilerplate skip (`classify_dupes.classify_text_patterns_only()`
   wired into `embed_paragraphs.py` so curated-pattern boilerplate is never embedded at all, not just
   flagged after the fact) both shipped and are unit-tested but hadn't gotten a todo.md writeup of
   their own yet before this entry -- see CLAUDE.md's `bulk_retrieve_crossref.py`/`embed_paragraphs.py`
   sections for the mechanics.

7. [DONE 2026-08-25] Author-publication-graph expansion -- a structurally different way to grow the
   corpus from every keyword-search-based script above: an author who already has 3+ papers in this
   corpus is someone the project already has independent reason to care about, and their OTHER
   papers are exactly where self-citation/self-reuse (and their close collaborators' work) would
   surface. Two new scripts:
   - `resolve_author_openalex_ids.py`: resolves a disambiguated OpenAlex author ID per corpus author
     from DOIs already on file (never a bare name search -- common names collide across real
     different people, the same off-topic-pollution risk as item 6's crank/off-topic problem, just
     applied to identity instead of topic). 8,092/10,369 authors (min 2 papers) resolved this way;
     the rest hit an unresolvable tie (expected, left unresolved rather than guessed).
   - `bulk_retrieve_author_works.py`: walks each resolved author's OpenAlex work list. Live dry-run
     against all 8,092 (min 2 papers, no cap) projected 500K-800K candidates -- almost entirely an
     author's unrelated career, not the ethics-adjacent slice this corpus is about. Scoped down to
     `--min-papers 3` (3,148 authors) plus harvest-time title filtering (reusing item 6's
     `filter_low_relevance_papers.is_on_topic_title()`, exported public specifically for this) before
     a single download is spent. Two real batches: 1,042 then 532 new papers.

   Found and fixed two real, generalizable bugs along the way, not just this feature's own code:
   - `bulk_retrieve_crossref.py`'s shared `fetch_candidate()` (used by this script and
     `bulk_retrieve_crossref.py` itself) was calling plain `download_pdf()`, not
     `download_with_landing_page_fallback()` -- found because 721 of ~3,700 first-batch candidates
     landed in `oa_url_not_pdf` (a real OA URL that wasn't a direct PDF, the exact case that fallback
     exists for). Fixed in the shared function, so both callers benefit.
   - `load_known_dois()` (shared by every `bulk_retrieve_*.py` script) and `bulk_retrieve_core.py`'s
     own resumability check both only recognized literal `status='downloaded'` as "already handled" --
     a row `filter_low_relevance_papers.py` had excluded (`status='excluded_crank'`/`'excluded_offtopic'`,
     `file_path` still set to the relocated file) would have been silently re-downloaded right back
     into the pipeline on the next run, undoing that exclusion. Fixed to key off "has a file_path at
     all" instead of the exact status string, in both places.

   Also found and fixed `find_duplicate_papers.py` running completely silently (zero log lines until
   one final summary) -- harmless at the corpus size it was written against, but at the current
   scale (96,900 papers) a tier-3 content-overlap scan over the grown `potential_dupes` table now
   legitimately takes several minutes, and a background task with no progress output got killed
   twice in a row by something (never conclusively identified what, but zero OOM/dmesg evidence) that
   reads "no output for a while" as "this is stuck" -- worth remembering as a general lesson, not
   just this script's problem. Added incremental logging (tier 1/2 group count, tier 3 progress every
   500 pairs); re-run afterward cleanly found 1,759 pairs (154 new).

# back testing 

1. Look for a known case of plagiarism. Figure out what the duplicate paper and the original were. Let us test this approach to see if it works before doing anything else. Also find me the names and internet handles of people who found those plagiarism and output them with the plagiarism they found into thankyou.md
	[DONE] found the Carlini/"Roadmap for Big Model" case, credited in thankyou.md, automated as a rerunnable test in tests/run_tests.py (tests/cases/carlini-roadmap-2022.json). Also added a negative-control case (two unrelated papers that must NOT be flagged) alongside it.

2. Two more known cases found but not yet added as tests -- both originals are genuinely open access but blocked by publisher bot-walls (ScienceDirect 403, MDPI/PMC's JS proof-of-work challenge) that need a real browser to get past. Revisit with claude-in-chrome (or fetch by hand) if it becomes available:
	a. Ramasamy, Sisay & Bahiru, "A Data Science Framework for Data Quality Assessment and Inconsistency Detection" (IJACSA 2021, retracted) copied Enamul Haque's McMaster thesis / Haque & Chiang's paper (DOI 10.1016/j.procs.2019.09.277). IJACSA confirmed "Level 1 plagiarism -- uncredited verbatim copying of a full paper" after Haque reported it. [UPDATE 2026-08-20] Re-checked: IJACSA's hosted copy no longer even has the retracted text, just a 1-page retraction-notice stub now -- both sides are blocked. See manual_examples/ramasamy-ijacsa-data-quality-2021/case.json.
	b. Ferdaus, Chukwu-Munsen, Foguel & Claro da Silva, "Taro Roots: An Underexploited Root Crop" (MDPI Nutrients 2023) copied Solange Saxby's PhD dissertation (U. Hawaii at Manoa, 2020, ScholarSpace). MDPI acknowledged the overlap but as of Retraction Watch's report hadn't corrected it. Thesis downloads fine from ScholarSpace; the MDPI paper and its PMC mirror don't.
	See tests/README.md for full details on both.

	[DONE 2026-08-20] Found and fully downloaded two more real cases (not yet promoted into
	tests/cases/ -- see manual_examples/README.md for the full writeup):
	c. Aygun, Aygun & Tarhan's "Energy Momentum Localization in Marder Space Time" (arXiv
	   gr-qc/0607102, 2006) -- arXiv administrators' own note: "removed ... because it plagiarizes
	   hep-th/0308070, gr-qc/9910015, and others" (both by S.S. Xulu, no author overlap --
	   genuine cross-author plagiarism). All 3 PDFs obtained.
	d. Yilmaz, Aygun & Aygun's "Topological defect solutions..." (arXiv gr-qc/0607104, Gen. Rel.
	   Grav. 37 (2005)) -- "excessive overlap with ... papers also written by the authors or
	   their collaborators" (hep-th/0505013, same author Yilmaz -- self/collaborator reuse, not
	   cross-author theft, labeled as such). Both PDFs obtained.
	Both surfaced from the same 2006-2008 arXiv/Gen.Rel.Grav. plagiarism sweep Peter Woit covered
	(https://www.math.columbia.edu/~woit/wordpress/?p=638). Several other Retraction Watch-era
	leads (COVID-medical retractions, an engineering-journal cluster, a proteomics case, a
	promising "WithdrarXiv" dataset of categorized arXiv withdrawals) didn't pan out -- either the
	original wasn't precisely identifiable, or (WithdrarXiv) needed Hugging Face auth this
	environment doesn't have.

3. make it so that the file download for this only happens once

# don't double store for same doc
1. duplicate text in the same doc. If 2 paragraphs in the same doc, such as a label or chuck of text are duplicates I don't want that counted, or even stored


# paragraphs.jsonl needs splitting

[2026-08-20] paragraphs.jsonl is one giant append-only file -- every paragraph ever extracted,
forever (extract_papers.py never removes a line, only adds; mode="a" unless --recompute). It hit
7,596,632 lines / 4.96GB this session, which was making `git add` on it time out outright and had
bloated .git to 1.9GB before it got un-tracked and purged from history (git-filter-repo, .git back
down to 19MB -- see git log). Untracking it fixes the git-bloat half of the problem, but not the
underlying one: embed_paragraphs.py still reads the *entire* file into memory on every single run
just to filter down to what's actually new (already_embedded() check happens after the full load,
not before it), and reconcile_stale_paragraphs() needs to compare the full current set against
library.sqlite3 to find stale rows -- both get slower and heavier as the file keeps growing,
forever, with no bound.

Split it somehow. Two candidate approaches, from the user, each with a real problem:

1. **One file per extract_papers.py run** (paragraphs-<timestamp>.jsonl or similar) -- fewer files
   than per-paper, but embed_paragraphs.py then needs to know which files still matter and doesn't
   solve the "must scan everything to reconcile staleness" problem unless combined with pruning
   older run-files once their content is confirmed durably embedded in library.sqlite3 -- and
   pruning safely means being sure nothing still depends on that specific file (a real but
   solvable bookkeeping problem, not automatic).
2. **One file per paper** -- avoids ever rewriting/appending to a shared file at all, and maps
   naturally onto "just regenerate this one paper's file if extraction logic changes". But this is
   ~70K+ files today and growing -- real filesystem overhead at that count (directory listing
   perf, inode consumption, backup/sync tools that choke on huge flat directories of small files),
   and embed_paragraphs.py/reconcile logic would trade "read one big file" for "N small file-open
   syscalls", which isn't obviously a win despite reading less total data.

   If going this route, group by DOI rather than raw file_path -- extract_papers.py's own
   upsert_paper() docstring already documents that the same DOI can legitimately show up under a
   second, different file_path (a paper re-fetched after the original file went missing from a
   prior session/computer -- this happened for real, see the DOI-collision incident elsewhere in
   this file's history/git log), so "one file per paper" keyed on file_path would silently split
   what's actually the same underlying work across two paragraph files. DOI is the more stable,
   semantically meaningful key here; fall back to the file_path-based slug (make_key()'s own
   scheme) only for the DOI-less case, same as retrieve_papers.py already does.

A third option worth weighing that isn't either of the above: since library.sqlite3's `paragraphs`
table already stores paragraph text once a paragraph is actually embedded (see CLAUDE.md), the
jsonl file's job could be treated as a *transient staging area* rather than a permanent archive --
once embed_paragraphs.py durably embeds a paper's paragraphs, that paper's lines could be pruned
from paragraphs.jsonl (or the file rebuilt fresh from only pending/not-yet-embedded paragraphs each
run) instead of kept forever. This bounds the file's size by "how much is currently unembedded",
not "everything ever extracted", and sidesteps the per-run vs per-paper file-count tradeoff
entirely -- but changes extract_papers.py/embed_paragraphs.py's handoff contract, so it's a real
design decision, not a drop-in fix. Whichever approach: needs picking, not just noting.

[DONE 2026-08-21] Picked option 3 (transient staging area). embed_paragraphs.py's
`prune_embedded_paragraphs()` rewrites paragraphs.jsonl after every run to drop any paper whose
paragraphs are now all durably embedded (default on, `--no-prune` to opt out) -- steady-state size
is now "extracted since the last embed_paragraphs.py run", not "everything ever extracted". First
real run: **4.96GB / 7,596,632 lines -> 262KB / 328 lines.**

Two real bugs found and fixed getting there, both worth remembering as a pattern:
1. **Prune must be all-or-nothing per paper, never partial.** reconcile_stale_paragraphs() treats
   "this paper is entirely absent from the current file" as "nothing to reconcile" (safe) but "this
   paper appears with fewer indices than before" as "the missing ones are stale, delete them"
   (correct after a genuine extract_papers.py --recompute, wrong -- real data loss -- if the file
   just got *partially* pruned, e.g. by an embed_paragraphs.py run that got interrupted partway
   through one paper's batch, a real scenario per this file's own "System/hardware reliability"
   section). Fixed by only ever pruning a paper's lines as a complete group, verified with a test
   that simulates exactly that interrupted-mid-paper state.
2. **A line load_paragraph_records() can't resolve to a paper_id must still be preserved, not
   silently dropped, on rewrite.** Found for real on this corpus's own file, not hypothetically: 328
   lines across 6 papers whose file_path no longer matched any current `papers` row (stale
   references left behind by an earlier retrieval-path rename -- e.g.
   `...how-influencer-doctors...-fenome-...pdf` in the jsonl vs. `...how-influencer-doctors...pdf`
   [no "-fenome-"] now in `papers`) got silently discarded the first time this ran for real, because
   a line load_paragraph_records() never resolves was never entering the raw_lines map prune reads
   from in the first place -- prune wasn't "dropping" them, it just never knew they existed. Caught
   immediately (0-byte file after a run that should have left ~101 residual lines), recovered from a
   pre-run backup, and load_paragraph_records()/prune_embedded_paragraphs() now track and
   unconditionally preserve unresolvable lines by design. Lesson: "rewrite a file keeping only rows
   you positively identified as safe to drop" and "rewrite a file dropping only rows you positively
   identified as unsafe to keep" sound like the same rule and are not -- the first one silently
   loses anything your identification logic doesn't cover, which is exactly what happened here.

--min-english-score (embed_paragraphs.py, default 0.03) also landed alongside this, same session --
see "Full-corpus plagiarism audit" below for why.


# Looking for dupes 
1. [DONE] I need to run this on hundreds of thousands of paper. So I need a database or format where I don't need to do pairwise comparison. I do not know what type of database would be best for this, my guess is an embedding. Assume that this will become a multistep process that finds candidates then winnows them down and that there will be a table of "potential dupes". For now I want to run this locally but be aware I may want to do it in the cloud in the future, so I would prefer something that could be done in a distributed way
	-> hand-rolled locality-sensitive hashing (LSH) index in library.sqlite3, replacing the brute-force
	compare in build_dupe_candidates.py. Full design below ("Candidate index design (LSH)"); implemented
	in lsh_index.py.

2. In a later step if the author of the older paper is also the author of the newer paper it should be treated as a dupe but there should be a set of flags so that it can be easily filtered

3. Check the citations for the plagiarising paper. This should be a flag to filter out by

4. Check which paper came out first so that the ux is clear 

5. Have a flag to manually mark a paper as checked which will 

6. ideally the table of potential dupes makes it easy to go through all the dupes in a single paper 1 by 1 in the CLI described 
below

7. There should also be another table which has authors linked to these dupes so I can look up potential dupes by authors

8. don't embed citations

## Candidate index design (LSH)

Problem this solves: `find_duplicates.py`/`build_dupe_candidates.py` both require every paragraph to be
embedded first, then compare every embedded paragraph against every other one (`matrix @ matrix.T`,
blocked to fit in memory). That's the brute-force pairwise comparison item 1 above asks to avoid — it's
O(n^2), and it can't produce anything until the whole embedding pass is done. On the real corpus
(~330K paragraphs today) a full sweep at --threshold 0.90 takes ~8 minutes and finds ~32K pairs.

The fix: a persisted **locality-sensitive hashing (LSH) index** — literally the "hashtable where the key
is the vector (or anything close enough to it) and the value is a list of paragraph ids" idea. Two
paragraphs only ever need an exact cosine-similarity check if they land in the same bucket first; nothing
is compared against everything else.

**Mechanism (random-hyperplane / SimHash, the standard LSH scheme for cosine similarity):**
- Draw `L` independent sets of `k` random hyperplanes (Gaussian vectors, `numpy.random.default_rng(seed)`
  so it's reproducible) through the origin of the 384-dim embedding space.
- A paragraph's hash in table `t` is a `k`-bit code: bit `i` is 1 iff its embedding is on the positive
  side of hyperplane `i`. Two vectors are more likely to get the same code the closer their angle (i.e.
  the higher their cosine similarity) — `P[bit agrees] = 1 - theta/pi`.
- `L` independent tables (each its own random `k` hyperplanes) cover the case where two near-duplicates
  don't land in the same bucket in every table: they're candidates if they share a bucket in **any** one
  of the `L` tables ("banding", standard LSH practice).
- A shared bucket is a *candidate*, not a verdict — real cosine similarity is still computed exactly for
  every candidate pair before it's kept, same as today.

**Schema** (library.sqlite3, alongside `paragraphs`):
- `lsh_config` (singleton row): the persisted `model`, `embedding_dim`, `num_tables`, `bits_per_table`,
  `seed`, and the actual hyperplane matrix (`planes` BLOB, float32). Every script hashes against the same
  planes read from this row — if any of those parameters change, the old planes AND every existing bucket
  row are invalid (a bucket key only means something relative to the hyperplanes that produced it) and are
  rebuilt from scratch, logged loudly since it's a full-index rebuild.
- `lsh_buckets(table_num, bucket_key, paragraph_id)`, `PRIMARY KEY(table_num, bucket_key, paragraph_id)
  WITHOUT ROWID` (an index-organized table -- exactly the hashtable: key = (table_num, bucket_key), value
  = the paragraph_ids that hash there) + a secondary index on `paragraph_id` alone (for "does this
  paragraph have any bucket rows yet" / orphan cleanup / invalidation lookups).

**Where it plugs into the pipeline:**
- `embed_paragraphs.py` calls `lsh_index.sync_index()` right after storing new/changed embeddings, so
  every paragraph is bucketed the moment it's embedded — no separate step, no waiting for the rest of the
  corpus. A paragraph whose text (and therefore embedding) changed at an existing `(paper_id, para_index)`
  slot has its stale bucket rows deleted first (`invalidate_paragraphs`) so it doesn't linger under a hash
  computed from the old text.
- `build_dupe_candidates.py` no longer loads every embedding into one matrix. It calls
  `lsh_index.sync_index()` too (a cheap no-op backfill safety net, in case paragraphs were embedded by an
  older version of the code or a different run), then `lsh_index.scan_candidate_pairs()` — a single
  vectorized sweep of `lsh_buckets` (group by `(table_num, bucket_key)`, emit every within-group pair,
  dedupe) instead of a SQL self-join or a per-pair Python loop, either of which turned out too slow at
  this corpus's real bucket sizes (see below). Exact cosine similarity is then computed only for that
  candidate set, same enrichment logic (`same_author`/`later_cites_earlier`/chronology) as before.
  `--brute-force` keeps the old exhaustive `find_duplicates.find_pairs` path available for a full,
  non-approximate sweep (e.g. to spot-check recall). Row data (title/text/embedding) for whatever ends
  up in the candidate set is fetched by paragraph id (`find_duplicates.load_paragraphs_by_ids`), not by
  loading the whole `paragraphs` table, for the same reason.
- `scan_candidate_pairs()` is **incremental by default** (added after the first version turned out to
  always re-derive the *entire* corpus's candidate set on every run, regardless of how little was
  actually new -- see "Incremental scanning" below): it tracks which paragraphs have already been scanned
  (`lsh_scanned`) and only generates pairs touching at least one that hasn't. `--full-rescan` opts back
  into the old whole-table behavior.
- `find_duplicates.py` is deliberately left untouched — brute force, exhaustive, no approximation. It's
  the ad hoc/reference tool (per its own docstring: "console report, nothing saved"); `build_dupe_candidates.py`
  is the scalable persisted one. Use `find_duplicates.py` to sanity-check the LSH path isn't missing
  something important.

**Tuning and validated behavior** (`lsh_index.py`'s `DEFAULT_*` constants: `num_tables=16`,
`bits_per_table=12`, `max_bucket_size=300`, `seed=0`):
- Recall (chance a true pair at a given cosine similarity shares a bucket in at least one of the 16
  tables), measured by Monte Carlo simulation against the formula above: **~81% @ 0.85 sim, ~95% @ 0.90,
  ~99.5% @ 0.95**. It's approximate by construction — lower `--threshold` runs lose more recall than
  higher ones; raise `num_tables`/`bits_per_table` (at the cost of more storage/lookup work) if a
  low-threshold sweep matters, or just use `find_duplicates.py`/`--brute-force` for that case.
- Real bucket occupancy on this corpus is skewed, not uniform (measured on ~330K paragraphs,
  bits_per_table=12): mean 81, median 40, p99 623, max 2206. A handful of buckets are huge because some
  content is reused verbatim across many unrelated papers (shared funding-acknowledgment boilerplate,
  common dataset-description paragraphs) — enumerating every pair inside one of those is what would make
  candidate generation blow back up toward O(n^2). `max_bucket_size` skips buckets above that size (per
  table only — the other 15 tables' independent hyperplanes still get a shot at the same paragraphs).
  The one real gap this leaves: an *exact*-duplicate cluster bigger than `max_bucket_size` hashes into the
  identical bucket key in *every* table (hashing is deterministic), so it can fall through every table's
  cap at once and be missed entirely by the LSH path — accepted as a known limitation, not silently
  ignored; `find_duplicates.py --threshold 0.99` or so catches those precisely because it's exhaustive.
- First validation attempt used a handful of pairs already sitting in `potential_dupes` as "known good"
  ground truth and got near-zero recall — turned out those rows were stale (embeddings had changed since
  the last `build_dupe_candidates.py` run; their current cosine similarity wasn't what the stored row
  said, per that table's own documented idempotency caveat), not an LSH bug. Real validation was Monte
  Carlo against the closed-form recall formula (confirmed the implementation matches theory) plus
  measuring actual bucket occupancy on the corpus (confirmed the skew above and sized `max_bucket_size`
  against it).

**Post-mortem (2026-08-12): shipped `max_bucket_size=300` without finishing the real-scale check, and it
took down a live run.** The bucket-occupancy analysis above was run once against a partial (~1/3) real
corpus scan that got killed accidentally before it printed capped-pair-count numbers for the actual
default; `max_bucket_size=300` was shipped anyway on the assumption a cap that close to the measured p99
(623) would be safe. It wasn't: measured for real afterward, `max_bucket_size=300` on this corpus projects
**263,116,379** candidate pairs before dedup (vs. 3,373,179 at 30) — `scan_candidate_pairs()`'s
`np.unique`/`np.concatenate` over an array that size drove the machine to 9.7GB RSS + 13GB swap and had to
be killed. That same run's `build_dupe_candidates.py` invocation also held one uncommitted transaction for
the whole `lsh_index.sync_index()` backfill (committing once at the end instead of per batch, unlike every
other write loop in this codebase) — while it thrashed, it starved a concurrently-running
`embed_paragraphs.py` of the write lock past its 30s timeout and crashed it with "database is locked".

Fixed: `DEFAULT_MAX_BUCKET_SIZE` lowered to 30 (measured safe); `sync_index()` now commits every 2000
paragraphs instead of once at the end; `build_dupe_candidates.py`'s `build_candidates()` now commits every
2000 pairs for the same reason; `scan_candidate_pairs()` now does a cheap SQL `GROUP BY` to project the
pre-dedup pair count *before* doing any of the expensive numpy work, and raises immediately if it's above
`MAX_CANDIDATE_PAIRS` (20M) instead of silently building the array. Re-measured after the fix: 9.94s wall,
1.77GB peak RSS, zero swap, against the same real bucket data. Lesson: an "I killed my own validation job,
here's what theory predicts" estimate is not the same as actually measuring the real-scale number before
setting a default that runs unattended against a live, concurrently-written database.

**Post-mortem 2 (2026-08-12): fixing the memory blowup didn't fix everything -- every run still re-read
the WHOLE corpus, every time.** With the above fixed, `build_dupe_candidates.py` was safe from crashing
or thrashing, but still consistently stalled for an hour+ (state `D`, near-zero CPU, WAL ballooning to
~2GB) when run alongside a live `embed_paragraphs.py`. Root cause: `scan_candidate_pairs()` re-read and
re-derived the candidate set from the *entire* `lsh_buckets` table on every single call, and
`pairs_from_lsh()` loaded the *entire* `paragraphs` table for its id -> row lookup -- regardless of how
many paragraphs were actually new since the last run. At this corpus's real size (600K+ paragraphs,
millions of bucket rows), those two full-table reads, competing for disk I/O against
`embed_paragraphs.py`'s continuous small commits, were enough to make almost no forward progress for over
an hour, even though nothing was actually deadlocked (confirmed via `/proc/<pid>/wchan` ==
`folio_wait_bit_common`, genuine kernel I/O wait, not a SQLite busy-retry loop).

Fixed: `scan_candidate_pairs()` is now incremental by default -- a new `lsh_scanned` table tracks which
paragraphs have already had their candidate pairs derived, and a scan only touches buckets containing at
least one paragraph that hasn't (`--full-rescan` opts back into the old whole-table behavior, needed if
you lower `--threshold` and want to catch a pair that was already co-bucketed but rejected under a higher
one before -- incremental scanning won't re-examine an already-seen pair regardless of threshold changes).
Row lookups also switched from loading the whole `paragraphs` table to fetching just the candidate set's
ids (`find_duplicates.load_paragraphs_by_ids`). Verified against a scratch DB: a second scan with nothing
new returns the empty set immediately; adding one new paragraph only produces pairs involving it, not a
re-derivation of everything that existed before; `--full-rescan` still reproduces the complete set;
invalidating a re-embedded paragraph correctly makes it eligible for re-scanning again. Lesson (same
shape as post-mortem 1, different bug): "idempotent" and "incremental" are not the same thing -- a stage
can safely re-run without corrupting anything (idempotent) while still redoing all the same expensive
work every time (not incremental), and only the latter actually scales.

**Post-mortem 3 (2026-08-12): WAL mode alone wasn't the whole fix -- `synchronous=FULL` (SQLite's
default) meant every commit still forced a disk fsync.** With post-mortems 1 and 2 fixed, a first-ever
backfill against the full ~916K-paragraph corpus still sat at ~0.3% CPU for 3 hours without completing a
single scan (`lsh_scanned` stayed at 0 rows the whole time). Diagnosed via the README's "is a silent,
running script actually stuck?" runbook: `/proc/<pid>/io` showed almost no CPU time relative to wall
clock, `fuser` confirmed no other process had the file open (so this time it genuinely wasn't
writer-vs-writer contention), and `lsh_buckets` was still slowly growing between checks -- real but
extremely slow progress, not a hang. Root cause: `sync_index()`/`build_candidates()` deliberately commit
every ~2000 rows to keep lock-hold-time short (post-mortem 1's fix) -- with the default `synchronous=FULL`,
WAL mode still fsyncs on every commit, so that turned into thousands of full fsyncs, and this machine's
disk apparently can't do that quickly. Fixed: `db.py`'s `connect()` also sets `PRAGMA synchronous=NORMAL`
-- SQLite's own documented pairing for WAL mode, which only syncs at checkpoint boundaries instead of
every commit. Consistency after an application crash is unaffected; the only tradeoff is a small window
of already-committed transactions that could theoretically be lost on an actual OS/power crash before the
next checkpoint, an accepted tradeoff here. Lesson (third variation on the same theme): getting the
journal mode right isn't the same as getting the sync mode right -- WAL and `synchronous` are two
different knobs, and a design that leans on frequent small commits (for good reasons!) needs both tuned
together, not just the one that fixes the specific symptom seen first.

**Post-mortem 4 (2026-08-23): the corpus outgrew `bits_per_table=12` and candidate generation quietly
went to ~0 recall -- not a crash, a silent detection gap.** `num_tables=16 x bits_per_table=12` gives
exactly `2^12 x 16 = 65,536` distinct `(table_num, bucket_key)` slots total -- fine when this was tuned
against a ~330K-916K paragraph corpus (mean occupancy 81, see above), but the corpus has since grown to
**10.35M embedded paragraphs**. Confirmed directly: `SELECT COUNT(DISTINCT table_num*100000+bucket_key)
FROM lsh_buckets` returns exactly 65,536 -- every slot in the entire hash space is now occupied, average
occupancy ~158/bucket, and a live `build_dupe_candidates.py` run logged `65535 oversized/skipped` out of
`65536 bucket(s) with >=2 member(s)` -- i.e. essentially every bucket now exceeds `max_bucket_size=30` and
gets skipped in every table. A full pipeline pass that newly LSH-scanned ~6M previously-unscanned
paragraphs (a batch that included ~4,000 new papers from that day's retraction-watch-source retrieval)
added **zero** new rows to `potential_dupes` (8,539 before and after).

Caught by direct verification against real ground truth, not just noticing the count: cross-referencing
`retraction_watch_sources_found.json`'s plagiarizing-paper/source-paper DOI pairs against `papers.doi`
found **62 pairs where both the confirmed-plagiarizing paper and its actual source are in this library** --
and **none of the 62 have any `potential_dupes` row at all**, confirmed real matches the LSH candidate
path is currently structurally incapable of surfacing at this bucket saturation, not just missed on a
recall coin-flip.

Not yet fixed. `bits_per_table` needs raising before the next `build_dupe_candidates.py` run does anything
useful -- `2^15 x 16 = 524,288` slots (avg occupancy ~20, comfortably under `max_bucket_size=30`) or
`2^16 x 16 = 1,048,576` slots (avg ~10) both restore real headroom. Changing it invalidates every existing
bucket row (`lsh_config`'s own documented behavior -- a bucket key only means something relative to the
hyperplanes that produced it) and requires re-hashing the full 10.35M-paragraph corpus via `sync_index()`
-- safe now that post-mortem-4-adjacent fix (`sync_index()`'s unbounded `fetchall()`, see the OOM incident
entry in "System / hardware reliability" below) batches that read, but still a real multi-minute-plus job,
and `scan_candidate_pairs()` needs a `--full-rescan` afterward (an incremental scan won't revisit pairs
that were already bucketed-and-rejected under the old, saturated config). `max_bucket_size` itself is a
secondary lever (raising it also restores headroom, at the cost of reintroducing the O(bucket-size^2) risk
post-mortem 1 exists to prevent) but the primary fix is more buckets, not bigger ones.

**Post-mortem 5 (2026-08-24): measured LSH candidate-generation recall against real, external ground
truth after fixing post-mortem 4 -- even correctly tuned, it structurally cannot find a majority of
threshold-eligible true positives, and the reason is a fundamental tension in the technique, not a bug in
this implementation. Worth documenting for anyone else building LSH-based plagiarism/near-duplicate
detection, not just as a note to future work on this repo.**

After fixing the bucket saturation (raised `bits_per_table` 12->16, `max_bucket_size` dropped to 15 to
stay under `MAX_CANDIDATE_PAIRS`), re-checked the same 62-pair Retraction-Watch ground-truth set from
post-mortem 4. LSH candidate generation found 9/62 on the first pass. Rather than accept that number,
every one of the other 53 pairs' actual paragraph-level cosine similarity was computed directly (brute
force, cheap at 62 pairs) to find out what LSH was actually missing and why:

- **23/62**: no real paragraph-level match >=0.85 anywhere between the two papers. Not an LSH problem --
  these pairs' confirmed plagiarism isn't captured as body-paragraph textual overlap by this pipeline at
  all (could be data/image plagiarism, non-text sections, or wording distant enough to fall under
  threshold -- not investigated further here).
- **27/62**: DID have a real >=0.85 paragraph match, invisible to the LSH candidate path anyway. Manually
  inspecting all 27's actual text (not just their similarity score) split them cleanly:
  - **~19/27**: correctly-suppressed boilerplate -- shared CC-BY license blocks, "competing interests"/
    "author contributions" sections, publisher branding headers, citation self-references ("please cite
    this paper as..."), and standard experimental-methodology templates (e.g. a formalin nociceptive-assay
    protocol description reused near-verbatim across thousands of unrelated pain-research papers).
    Confirmed directly: several of these paragraphs sit in `lsh_buckets` groups with 200-2,900+ *other*
    paragraphs -- `max_bucket_size` correctly identified these as corpus-wide noise and skipped them, in
    every one of the 16 tables (deterministic: identical/near-identical embeddings hash identically).
  - **8/27**: real, substantive plagiarism content (mathematical proofs with systematic variable-renaming,
    near-identical clinical conclusions, near-verbatim methods text) missed by pure LSH randomness, not the
    size cap -- one pair shared **zero** buckets across all 16 independent hash tables despite 0.88 cosine
    similarity, an accepted outcome at that similarity per the project's own Monte Carlo recall curve
    (~81% recall per table-set at 0.85 sim is not 100%, and this pair simply landed on the wrong side of
    that dice roll in all 16 tables at once).

**The generalizable finding:** of the 39 pairs (9 + 27 + 3 borderline within the 23) that had *any*
threshold-eligible signal at all, LSH alone surfaced 9 -- roughly a quarter. The other three-quarters
split almost evenly between "the size cap correctly filtered it as boilerplate" and "pure hash-collision
bad luck." **The same mechanism (bucket-size capping) that is essential for suppressing genuine
corpus-wide boilerplate noise is *exactly* the mechanism that also suppresses genuinely templated
plagiarism** (paper-mill content reused across dozens of papers, not just the two under comparison) --
there is no value of `max_bucket_size` that cleanly separates the two cases, because both produce the
*same* bucket-occupancy signature (a short, generic-looking passage shared by many papers at once). Six of
the math-paper-mill matches recovered by hand here are themselves proof this isn't hypothetical: real,
confirmed (via Retraction Watch) plagiarism that looks, to a pure occupancy-count heuristic, identical to
a shared license paragraph.

**What would actually fix this** (not implemented -- scope, and needs its own validation before trusting
it unattended): a maintained *content-based* boilerplate exclusion list -- fingerprint/hash known common
phrases, license blocks, and standard methodology sentences *once*, store them separately, and exclude
paragraphs matching that list from the size cap's "is this bucket noise" judgment entirely, rather than
inferring "boilerplate" purely from how many other paragraphs happen to share a bucket. That would let
`max_bucket_size` (or removing the cap concept entirely, once boilerplate is excluded another way) stop
being the thing standing between real recall and a real templated-plagiarism cluster.

**Practical takeaway for measuring any LSH-based candidate generator's real recall:** a self-consistency
check (does the system re-find pairs it already knows about) cannot catch this class of blind spot at
all, because the blind spot is specifically about pairs the system has *never* surfaced. Only checking
against small, external, independently-verified ground truth (here: Retraction Watch's own confirmed
plagiarism list, not this project's own prior findings) revealed it. Anyone relying on LSH/SimHash
candidate generation for plagiarism or near-duplicate detection at corpus scale should expect a similar
blind spot and validate against real known-positive pairs periodically, not just trust the Monte Carlo
recall formula in isolation -- the formula describes per-pair collision probability correctly, but says
nothing about the bucket-size cap's separate, content-blind, all-or-nothing effect on templated/reused
content.

**Post-mortem 6 (2026-08-24): actually looked inside the biggest buckets (built `inspect_lsh_buckets.py`
for this) instead of assuming post-mortem 5's boilerplate story explained all of them -- it doesn't. The
single biggest LSH buckets in this corpus are NOT boilerplate at all; they're a distinct phenomenon worth
knowing about separately, with a different (and harder) fix.**

Full-corpus numbers (bits_per_table=16, 10.35M embedded paragraphs): 1,047,996 of the theoretical
1,048,576 `(table_num, bucket_key)` slots are occupied (near-total saturation again, just at 16x the
resolution of post-mortem 4's 12-bit config); mean occupancy 158, median 69, p99 1,392, **max 51,537** --
one single bucket holding *half a percent of the entire corpus's paragraphs*. Sampling the 40 biggest
buckets' actual content (not just their similarity scores) found **zero** matched the boilerplate-marker
heuristic from post-mortem 5 -- these aren't license blocks or disclosure statements. What they actually
are: paragraphs overwhelmingly drawn from a narrow set of the corpus's *earliest, most heavily-cited
foundational papers* in its subject area (paper_ids 1-55ish -- "A Survey on Bias and Fairness in Machine
Learning", "Big Data's Disparate Impact", "The Measure and Mismeasure of Fairness", "War-Algorithm
Accountability", "Bots as Virtual Confederates" -- canonical, widely-cited works in the AI-fairness/ethics
space this corpus was deliberately built around), landing in the same bucket alongside many *different*
papers' paragraphs on the same narrow topic, and even alongside *each other* (one seminal paper's own
different paragraphs frequently share a bucket with one another).

**Why this happens, and why it's a different mechanism than post-mortem 5's boilerplate story:** this
corpus is thematically narrow by design (`bulk_retrieve_arxiv.py`/`bulk_retrieve_crossref.py`'s
`DEFAULT_KEYWORDS` are all AI-ethics/fairness/algorithmic-bias adjacent). At 10M+ paragraphs almost all
discussing a handful of closely related core concepts (algorithmic fairness definitions, discrimination,
bias mitigation), a huge fraction of the corpus's vocabulary and phrasing genuinely converges -- not
because anyone copied anything, but because "same topic, different content" (CLAUDE.md's own documented
~0.60-0.85 similarity band) is happening at a scale where even a 16-bit-per-table SimHash code isn't fine
enough to keep genuinely-different-but-topically-close paragraphs in separate buckets. This is the
corpus's own thematic concentration expressing itself as a hashing artifact, not reused text -- a
fundamentally different cause from post-mortem 5's literal-boilerplate-reuse story, even though both
manifest identically as "an oversized bucket that gets skipped."

**Why this matters for the fix:** post-mortem 5's proposed fix (a content-based boilerplate exclusion
list -- fingerprint known license/methodology text, exclude it from the size cap's judgment) would do
**nothing** for this category, because there's no fixed, enumerable "known phrase" to exclude here --
it's organic topical convergence across millions of independently-written paragraphs, not a finite set of
templates. A corpus this thematically concentrated may need either much finer hashing still (more
bits/tables, with the same re-hash-everything cost each time), a fundamentally different indexing
approach for the "extremely common topic" tail (e.g. clustering-aware bucketing, or accepting that dense
topical regions of the embedding space need brute-force/ANN search rather than LSH), or simply accepting
that LSH-based candidate generation has a structural ceiling on a narrow-topic corpus that a
broader-topic corpus of the same size wouldn't hit as hard. Not solved here -- recorded because it's the
kind of thing that would otherwise look identical to "just more boilerplate" from the outside, and isn't.

**Post-mortem 7 (2026-08-25/26): post-mortem 6's own "~10/bucket average" sizing was itself wrong (an
arithmetic error, not just an underestimate) -- corrected, re-hashed at bits=21, hit a real OOM
fixing it, rewrote the candidate pipeline to stream through bounded batches, and the resulting
410,702-new-candidate haul still found zero new independent cross-author plagiarism after ~130
candidates read by hand across three different sampling strategies.**

The corrected math: mean bucket occupancy = `(paragraphs x num_tables) / (num_tables x 2^bits)` =
`paragraphs / 2^bits` -- `num_tables` cancels out of the AVERAGE entirely (it only affects how many
independent chances a given pair gets to collide, not how full a bucket gets). Post-mortem 6's "~10/bucket"
comment computed `paragraphs / total_slots` (dividing by `num_tables x 2^bits`, missing a factor of
`num_tables`) -- undershooting the real mean by 16x from the moment it shipped. Verified directly against
the real corpus at that config: mean 165, median 72 (bits=16, 96,900 papers/~11M paragraphs) -- 75.9% of
all buckets over the `max_bucket_size=30` cap, 97.5% of all paragraph-bucket memberships sitting in one.

Two fixes applied together, not separately, before re-hashing:
1. **Retroactive boilerplate reclassification** (`embed_paragraphs.py --reclassify-existing-boilerplate`,
   new `reclassify_embedded_boilerplate()`): the pre-embedding filter from item 6's writeup only ever
   affected paragraphs embedded going forward -- most of the corpus predates it. Ran it retroactively:
   621,811 of 10,818,380 already-embedded paragraphs (5.75%) matched a known `classify_dupes.py` pattern,
   got their embedding cleared (`SKIPPED_MODEL_SENTINEL_BOILERPLATE`) and their LSH bucket/scanned rows
   removed via `lsh_index.invalidate_paragraphs()`. Existing `potential_dupes` rows referencing a
   reclassified paragraph are left alone (same non-clobbering precedent as everywhere else) -- only future
   candidate generation stops considering it.
2. **`bits_per_table` 16 -> 21** (`lsh_index.DEFAULT_BITS_PER_TABLE`): 2^21 x 16 = 33.5M slots, mean
   ~5.2/bucket at post-reclassification corpus size. Real-world result after re-hashing (~40 min, far
   faster than a several-hour worst-case estimate -- this ran on a different, newer machine, NVMe SSD not
   the older machine's documented spinning-disk distress): mean 7.06, median 3.0, buckets-over-cap down to
   3.6% (from 75.9%), memberships-in-saturated-buckets down to 34.6% (from 97.5%) -- big, but not total:
   matches post-mortem 6's own prediction that some of this is organic topical convergence in a
   narrow-topic corpus, not fixable by hashing resolution alone.

**The OOM, and why it wasn't a tuning problem.** First `--full-rescan` attempt (`--max-bucket-size 8`,
sized from a real measured ~155 bytes/pair cost of the final candidate set -- see
`lsh_index.MAX_CANDIDATE_PAIRS`'s own comment) was killed by the kernel OOM-killer at 30.6GB RSS (61GB
total-vm; system has 30GB RAM + 31GB swap). Root cause wasn't the pair-count ceiling at all: even at the
most conservative possible cap (`--max-bucket-size 2`), **6.6M of 10.2M paragraphs (65%) still get
touched** -- the finer hashing that fixed bucket sizes also means almost every paragraph now sits in
*some* small kept bucket, so a full rescan's candidate set is close to the whole corpus regardless of the
cap. `find_duplicates.load_paragraphs_by_ids()`'s own docstring assumed "a few thousand paragraphs at
most"; loading real embeddings+text for millions at once was always going to be tens of GB.

**The actual fix: stream everything through bounded batches, end to end**, not a bigger machine or a
smaller cap. New `lsh_index.scan_candidate_pairs_to_table()` (Pass 1 shared with the original
`scan_candidate_pairs()` via extracted `_discover_candidate_groups()`; Pass 2 writes each
`group_batch_size` chunk's pairs straight to a caller-provided SQLite table instead of holding one Python
list of numpy arrays per kept group -- 16.6M of them at this corpus's current full-rescan scale, itself a
huge allocation before any pair data existed at all) + `build_dupe_candidates.process_lsh_candidates_streaming()`
(paginates that table `pair_batch_size` pairs at a time via new `read_pairs_in_batches()`, loading and
verifying only one batch's worth of paragraph data at a time, persisting via the existing
`build_candidates()` per batch). New `--max-candidate-pairs`/`--group-batch-size`/`--pair-batch-size` CLI
flags on `build_dupe_candidates.py`. Unit-tested (consistency against the original in-memory path,
chunk-boundary correctness, small-batch-size stress). Peak memory is now bounded by batch size, not corpus
size, at any scale.

**Real re-run result** (`--max-bucket-size 6 --max-candidate-pairs 60000000`, batched): 254 batches, 3-4
sec each, finished in ~34 minutes wall-clock (18:08-18:42 that day) with no memory pressure. 410,702 new
candidate pairs -- `potential_dupes` went from 46,759 to 426,631 (~9.1x). Of those, 352,536 are
`same_paper=1` (internal near-duplicates, not plagiarism candidates); 74,095 are genuine cross-paper.
`classify_dupes.py` run afterward: 39,877 `no` (known pattern), 3,541 `yes`, 30,677 left `NULL`
(`same_author=0`, no pattern matched -- the genuinely uncertain set).

**Manually read ~130 of the highest-signal candidates across three sampling strategies** (highest
`lcs_ratio`, pairs backed by a 2-member LSH bucket specifically to avoid the "common topic" false-positive
shape, and high-cosine-but-low-`lcs_ratio` pairs to catch paraphrase-shaped overlap instead of one long
verbatim run) -- **zero new independent cross-author plagiarism.** Every real text match resolved to one
of: publisher/journal boilerplate not yet in `classify_dupes.py`'s pattern list (funding-acknowledgment
statements, a journal's own submission guidelines, `academia.edu`'s Terms of Service scraped in as a page
footer, MDPI's singular/plural copyright line), both papers quoting the same well-known external source
verbatim (actual EU AI Act statute text, a famous Facebook ad-discrimination study's figure caption, a
canonical AI-safety principle, a philosophical personhood syllogism), standard field-definitional/
methodological language common across a whole subfield (a fairness-metric formula, a tumor-detection
background section), a shared bibliography entry, or -- the one *real*, recurring pattern -- same-author
self-reuse hidden behind `same_author=0` by this project's own known author-extraction gap (empty
`authors` list on one or both sides). That last category alone accounts for the large majority of every
genuinely cross-paper `ai_check='yes'` verdict found this session (Coeckelbergh, Silva-Atencio, the "Zoo
of Fairness Metrics" cluster, White's Kant/Aristotle papers, Mittos/Blackburn/De Cristofaro's 23andMe
Twitter study, and a game-theory anti-coordination pair -- 6+ confirmed instances now).

One real structural bug found along the way, distinct from text-overlap false positives: candidate 17109
matched because one "paper" is literally a journal issue's own table of contents, listing the other
paper's title as one of its entries -- a multi-article-PDF-cataloged-as-one-paper retrieval artifact
(same family as `find_duplicate_papers.py`'s own tier-3 content-overlap detector was built for), not a
text-similarity false positive.

**Not yet done, and now the single highest-value remaining fix given how often it explains a real
candidate**: a name-similarity fallback for `same_author` when a paper's `authors` list is empty --
comparing the OTHER paper's actual author names against affiliation-footer/CRedIT-statement text already
present in the paragraph, rather than relying solely on the (sometimes-empty) `paper_authors` join. Would
have auto-resolved every one of the 6+ instances found by hand this session without a human/AI review
pass needed at all.

**Post-mortem 8 (2026-09-08): same null result reproduced at a much larger corpus scale, via two
independent code paths this time, and a new dominant false-positive pattern identified.** Corpus grew
to 118,754 papers / 12.6M embedded paragraphs (grey-lit retrieval rounds specifically targeting CORE,
Zenodo, SocArXiv, and thesis sources). Re-ran the full chain (`build_dupe_candidates.py
--max-bucket-size 6 --max-candidate-pairs 60000000`, the same proven-safe flags from post-mortem 7 --
defaults still hit the ~20M safety ceiling, this time projecting ~230.9M pairs) + `classify_dupes.py`:
212,375 new candidate pairs, `potential_dupes` up to 675,400; classify_dupes left 72,638 for manual
review. Checked two independent samples against this new backlog:
- **Highest-`lcs_ratio` candidates (top 3000), both-sides-authored, excluding the 24 already-known
  cases**: only 9 pairs survived the filter. All 9 resolved to non-findings -- bundled-PDF/table-of-
  contents containment artifacts (a whole edited-book or journal-issue PDF matching its own contained
  chapters, same family as the candidate-17109 finding in post-mortem 7), shared publisher backmatter
  boilerplate, or two unrelated papers both quoting the same well-known external fact/source verbatim.
  Marked accordingly (`agent_decided_false_positive`/`boilerplate`/`citation`) via `--agent-verdict`.
- **`find_title_bucket_dupes.py` round-2 "survivors" list** (1,405 groups, the identical-title
  candidates the embedding pipeline structurally can't find -- this project's actual productive
  mechanism): filtering out same-DOI-base version pairs (42) left 1,363; of those, **906 (66%) turned
  out to be a single dominant pattern -- an arXiv/OSF preprint DOI paired with that same paper's
  published-venue DOI** (e.g. `10.48550/arxiv.1910.10045` vs `10.1016/j.inffus.2019.12.012`, confirmed
  directly on one instance: same title, same 19-author byline, the classic Arrieta et al. XAI survey --
  the preprint side had a full extracted author list, the published side had none, another instance of
  the empty-`paper_authors` gap above). Filtering those out too, and a further 68 DOE/OSTI
  `10.2172`-prefix "Environmental Regulatory Update Table" recurring-template pairs, left 435 -- spot-
  checking the top 25 of *those* by `longest_run` found still more of the same shape: BioMed Central
  `preaccept-*` DOIs (a paper's pre-acceptance manuscript vs. its own final version), Research Square
  preprint `/v1` vs `/v2` pairs my own version-pair filter's regex didn't catch (only strips a trailing
  `.N`, not a `/vN` path segment -- a real gap in the ad hoc filter used for this check, not in any
  shipped code), a literal doubled-prefix DOI formatting bug (`10.30966/10.30966/2018.riga.8.6` vs
  `10.30966/2018.riga.8.6.`), and the already-documented ECIAIR/ICAIR conference-DOI-variant pattern
  (28915/35009, 27008/27635 -- same pair ids as the entry above, unprompted re-discovery, good cross-
  check that this identification method is sound).

**Net result: zero new independent cross-author plagiarism found, via either code path, after three
layers of filtering converged on the same "same underlying work under two DOI registrations" story
each time.** This replicates and extends post-mortem 7's own null result onto a corpus that's grown
substantially since. Not investigated further this pass (would mean hand-checking hundreds more
individually-explainable-looking pairs for a rate that's now been zero twice); logged instead so a
future pass doesn't re-spend the same effort rediscovering it. The recurring theme across both
post-mortems remains the same: this corpus's real signal lives in identical-title, cross-author,
low-scrutiny-venue candidates specifically (the IAEME/Pearl Blue/AMJSAI families) -- of the 13 such
candidates surviving every filter in this pass, 12 were already-known cases and the 13th (a generic
"hybrid quantum-classical neural networks" title collision, zero exact-shingle overlap) was a genuine
false positive, not a coverage gap.

**Concrete follow-up worth doing, not attempted this pass** (data hygiene, not plagiarism-finding):
`find_duplicate_papers.py` doesn't currently recognize any of the four DOI-registration patterns found
above (arXiv/OSF-preprint-vs-published, BMC preaccept-vs-final, Research-Square-versioned, doubled/
malformed DOI prefixes) as the same underlying paper -- a targeted extension covering these would clean
up a meaningful fraction of `potential_dupes`' `unreviewed` backlog (906+ rows from the preprint
pattern alone, in this one title-bucket survivors file) without touching plagiarism-finding logic at
all.

# System / hardware reliability -- open, not a software bug (2026-08-13/14)

**This machine's disk showed real, recurring distress under today's sustained multi-hour write load, up
to and including one actual unplanned reboot -- this is a hardware/system-health concern, not something
fixable from within this repo, and it's still unresolved.** Writing it down here rather than letting it
stay only in chat history, since it needs the user's own direct action (disk health check, thermal check,
possibly just giving the machine a rest), not a code change.

Timeline:
- **2026-08-13 ~20:35-20:47**: the machine crashed and rebooted, unplanned (confirmed via
  `journalctl --list-boots` -- the previous boot's log just stops, no clean shutdown recorded; `who -b`/
  `last reboot` showed a genuine ~12-minute gap). This landed in the middle of several hours of continuous
  heavy retrieval + embedding + LSH-sync activity (three chained batches running back to back). All
  in-flight work was lost (background jobs, not the data) but every database reopened cleanly afterward --
  WAL mode did its job, no corruption, `PRAGMA quick_check` (once it finally completed -- itself very slow,
  see below) and targeted row-count queries both came back clean.
- **2026-08-14, hours later, during another long `build_dupe_candidates.py` run**: the run appeared to
  silently stall for 3-4+ hours (state `D`, `wchan=folio_wait_bit_common`, the same "real I/O wait, not a
  hang" signature documented in the LSH post-mortems above). Given the file sizes/mtimes still looked like
  they were changing between early checks, this initially looked like the known "just slow on this disk at
  this scale" pattern. A rigorous check (compare `/proc/<pid>/io`'s `rchar` before/after a real,
  timer-enforced 90-second wait, not just two quick manual checks seconds apart) showed **zero bytes read
  in that window** -- genuinely no progress, not just slow.
- Investigating *that* (rather than assuming "the process is broken, kill it") surfaced the real finding:
  `journalctl -k -b 0` showed `systemd-journald.service` repeatedly failing with `result 'watchdog'` and
  `result 'timeout'` -- **9+ occurrences between 17:02 and 20:17 that same day**, i.e. an ongoing, recurring
  pattern, not a one-off. `ps -eo stat` at the same moment showed `dmcrypt_write` and `jbd2/dm-1-8` (the
  LUKS/dm-crypt write-back worker and the ext4 journal-commit thread) both sitting in `D` state -- i.e. the
  actual disk-encryption/filesystem write path itself was stuck, not just this one Python process. A canary
  write to an unrelated file on the same filesystem eventually succeeded but took unusually long. Load
  average was 8.66 on a 12-core machine -- elevated, consistent with several D-state kernel threads, not
  by itself alarming, but corroborating.
- This is very likely the same underlying condition that caused the 20:35 crash -- not proven (no root
  access here to run `smartctl`/check dmesg for hardware-level ATA/SCSI errors, so "failing drive" vs.
  "thermal throttling" vs. "just severely overtaxed after ~14 continuous hours of heavy write activity in
  one day" can't be distinguished from inside this session), but the pattern (write-path kernel threads
  stuck, journald itself timing out, right in the middle of this project's heaviest sustained I/O day so
  far) is consistent enough to treat as one ongoing story, not two coincidences.

What was done in response: the specific process that looked stalled had in fact just finished naturally
(a timing race between the diagnostic check and its actual completion -- confirmed via the pipeline's own
log reaching `write_dupe_reports.py` and `CHAIN_COMPLETE` right after, data verified intact). But rather
than treat that as "false alarm, all clear," a queued continuation (another multi-hour retrieval +
download batch, about to start immediately after) was deliberately killed and nothing further was
launched -- piling more sustained heavy write load onto a disk that had *just* shown this pattern seemed
like the wrong call regardless of whether this specific process turned out fine.

**Not yet resolved / needs the user, not a script:**
1. Check actual disk/hardware health directly (`smartctl -a` on the physical device -- needs `sudo`,
   not available in this session) before resuming multi-hour unattended background retrieval/pipeline runs.
2. Consider whether today's pattern (~14 hours of near-continuous heavy write activity across many
   chained retrieval + embedding + LSH batches, run back-to-back with little rest) is itself a contributing
   factor independent of any hardware fault -- i.e. whether future batches should be deliberately paced/
   spaced out rather than chained immediately one after another, regardless of what the hardware check
   finds.
3. Until 1 (and maybe 2) happens, don't assume it's safe to resume large chained background jobs the way
   they were being run today.

**2026-08-23: a second, different reliability incident -- this one a real, fixed software bug, not
hardware.** Felt to the user like a power-cycle (screen/session reset while asleep), but `uptime`/
`journalctl --list-boots` confirmed the kernel never rebooted (continuous since 2026-08-15). What actually
happened, from the kernel/systemd journals:
1. `run_pipeline.py`, mid-way through `embed_paragraphs.py`'s post-embedding LSH sync (`lsh_index.sync_index()`,
   called right after the corpus's biggest-ever single embedding batch: 2,556,990 paragraphs in one run),
   grew to 24.6GB RSS and was killed outright by the kernel OOM-killer.
2. That alone didn't relieve memory pressure fast enough -- `systemd-oomd` (the userspace early-OOM daemon)
   had independently already flagged the whole user session as over its pressure threshold and killed
   `org.gnome.Shell@wayland.service` -- i.e. the desktop compositor itself.
3. Losing GNOME Shell tore down the entire graphical session: systemd SIGKILLed everything else in the
   user slice with it, including the terminal running the retrieval-chain waiter script and the second
   `run_pipeline.py` pass it had just auto-launched (dead one line into `extract_papers.py`, before it
   could write anything). This cascading teardown is what read as "the computer power-cycled."

**Root cause, found and fixed:** `lsh_index.sync_index()` fetched every not-yet-bucketed paragraph's
`(id, embedding)` in a single unbounded `.fetchall()` before hashing any of it -- fine at the corpus sizes
this was written and tested against, but at 2.56M pending paragraphs in one run (10.15M total embedded
paragraphs in the corpus as of this incident) the materialized list of `(int, bytes)` tuples plus the
`np.stack(...)`'d matrix was enough on its own to be the dominant contributor to the 24.6GB RSS peak.
Fixed to page on both sides: pending paragraph *ids* are still fetched in one shot (cheap even at 10M+ --
plain ints, tens of MB), but each batch's embeddings are now pulled with their own small `WHERE id IN (...)`
query, sized to the same `batch_size=2000` the write side already committed in. Regression test:
`tests/unit/test_lsh_index.py::test_sync_index_indexes_every_paragraph_across_multiple_batches` (4,500
paragraphs, crosses two batch boundaries, asserts nothing lost/double-counted).

**Not fully closed:**
- `embed_paragraphs.py`'s `main()` also holds `all_records` (every paragraph's text, parsed from
  `paragraphs.jsonl`) *and* a second copy in `text_by_key` (built for `skip_non_english_paragraphs()`)
  simultaneously in scope through to the `sync_index()` call -- a secondary, smaller contributor (a couple
  GB at this corpus's paragraph-text volume) not addressed here.
- `lsh_index.scan_candidate_pairs()` (called by `build_dupe_candidates.py`, not reached in this incident)
  has the same one-shot-`fetchall()` shape for its "buckets touched by new paragraphs" query. It wasn't
  what OOM'd this time, and the incremental scoping (only buckets touched by a not-yet-scanned paragraph)
  bounds it better than `sync_index()`'s old unfiltered fetch already did -- but with `lsh_scanned` now
  ~6M paragraphs behind `paragraphs.embedding IS NOT NULL` after this incident, the first post-fix
  `build_dupe_candidates.py` run has a much bigger "new" set than any run before it. Worth watching memory
  on directly before assuming it's fine at this scale, not yet proven safe.
- No swap configured on this machine (30GB RAM, 0 swap) -- with swap, this whole cascade (OOM-killer ->
  systemd-oomd -> GNOME Shell -> full session teardown) would very likely have degraded to "slow" instead
  of "kill the desktop out from under a sleeping user." Worth doing regardless of the code fix above; needs
  `sudo`, which needs the user's own terminal (see this repo's CLAUDE.md on `sudo` not being available
  interactively in this session).

# AI pre-filter pass: false-positive patterns to catch programmatically

2026-08-13: hand-reviewed (with AI assistance) all 1,294 cross-paper `potential_dupes` candidates and
marked each `ai_check='yes'` (plausible, worth a human look) or `'no'` (obviously not a real duplicate),
with `ai_check_reason` recording why -- see `build_dupe_candidates.py`'s `ensure_columns()` docstring for
the schema. Result: **1,093/1,294 (84%) were obviously not real duplicates.** Of the 201 marked `yes`,
198 are `same_author` (self-reuse across two of the same person's own papers -- real text duplication,
but a different ethical question than a stranger copying them, per the existing `same_author` flag) and
only 3 are genuinely unrelated authors with substantive, unexplained overlap.

**Correction (2026-08-13, same day):** 2 of those 3 cross-author "yes" verdicts didn't hold up on a
second look, prompted by the user checking `dupe_reports/5350-...txt` and saying "that doesn't look like
a dupe." Both are now `no`:
- The 5350 case: the "earlier" paragraph looked suspicious partly because it was cut off mid-sentence
  ("...prevent adverse consequences. To" / next paragraph: "address the risks posed by..." --
  `extract_papers.py`'s paragraph splitter broke one sentence across two paragraph records, a real
  extraction artifact, not just a report-display issue -- see `write_dupe_reports.py`'s `expand_paragraph()`
  fix below). Read whole, it's unremarkable generic domain phrasing. Worse, the review had missed that
  `later_cites_earlier=1` for this pair -- the later paper properly cites the earlier one, so this was
  never uncredited anything.
- The 147 case (found independently while re-checking the other two after the first correction): both
  sides are chart axis/legend label text (ACC, DP, TPR, FPR, PPV, FOR -- standard fairness-metric
  abbreviations), not prose -- almost certainly two papers independently using the same bias-audit
  toolkit to generate a similar chart, not duplicated writing.

Only **1 of 201** survives as a genuine cross-author signal: potential_dupes id=4135, MAFT (2024) vs.
Automatic Fairness Testing of Neural Classifiers (2021) -- a near-identical three-condition formal
definition of "individual discriminatory instance," `later_cites_earlier=0`, that held up and if
anything looked *more* convincing once `expand_paragraph()` pulled in the full itemized definition
instead of a truncated fragment.

Lesson: `later_cites_earlier` should have been checked as a matter of course during the original
hand-review, not as an afterthought -- a citation is direct, structured evidence the text overlap is
attributed, and skipping it on a "worth a second look" verdict is exactly how a false positive survives
a review that was otherwise being careful. Fixed process: check it every time from here on.

This was a manual (AI-assisted) pass, not automated -- todo.md's original "Looking for dupes" ask
already anticipated needing this kind of winnowing step ("assume this will become a multistep process
that finds candidates then winnows them down"). The patterns found are specific and clear enough that
most of them SHOULD become programmatic filters (regex/heuristics applied before or during
`build_dupe_candidates.py`, or as an extra `ai_check`-style pre-computed flag), so future candidate
batches don't need another full manual pass. In descending order of how much they accounted for:

1. **Publisher/venue boilerplate** (biggest single category, ~734 of the 1,093 caught in the first
   pass alone): CC license text, ACM copyright/permission-to-copy/manuscript-submission headers, CEUR
   proceedings notices, bare arXiv identifier headers, competing-interest/conflict-of-interest
   disclaimers (many phrasings), company/employer disclaimers (e.g. "the views and opinions expressed are
   those of the authors and do not necessarily reflect..."), funding-acknowledgment templates (grant
   numbers vary, phrasing doesn't), ACM CCS taxonomy tags, journal running-page-headers (e.g. JMIR's
   "page number not for citation purposes" line). All of this is publisher/venue front-or-back-matter
   that every paper from that venue includes verbatim by template -- never content one paper copied
   from another. Detectable via a fixed regex library (see `lsh_index`/`build_dupe_candidates` session
   history for the actual patterns used, not yet extracted into a reusable module).
2. **Shared bibliography/citation entries**: two different papers citing the same third-party source
   (a DOI, a numbered `[N]` reference, an `et al. (YYYY)` entry near the start of the paragraph, or
   simply 2+ distinct years mentioned in one paragraph) produces high embedding similarity between the
   two papers' *reference lists*, not their own writing. This is a real gap in `extract_papers.py`'s
   citation splitting (already documented in CLAUDE.md's "Known heuristic gaps" -- hanging-indent/
   author-year reference lists that don't split cleanly can leak into body paragraphs instead of the
   `citations` table) compounding with the dupe-finder having no way to distinguish "cites the same
   source" from "copied from each other." The real fix belongs upstream: better citation-list
   extraction so these never become body paragraphs at all, not just downstream filtering.
3. **Standard methodology/reporting templates reused verbatim by design**: "Datasheets for Datasets"
   (Gebru et al. 2018) documentation questions, System Usability Scale (SUS) survey items, PRISMA
   systematic-review reporting checklist items, the NeurIPS paper-submission Code-of-Ethics checklist,
   even a canonical WinoBias-style gender-coreference example reused across the gender-bias-in-NLP
   literature. These are *supposed* to be copied verbatim -- that's the point of a standardized
   instrument/checklist -- so any two papers using the same one will always look like duplicates to a
   pure embedding/text-overlap check. Worth a small library of known-template fingerprints (a handful of
   distinctive phrases per template is enough, per what worked here).
4. **Quoted external legal/rights/reference text**: two papers both quoting the same ECHR or GDPR
   article, a rights declaration (e.g. Pachamama/Sumak kawsay, African Commission resolutions), or a
   shared formal definition traceable to a common source -- the two *papers* didn't duplicate each
   other, they both quoted the same *third thing*. Harder to catch with a fixed phrase list (unlike
   boilerplate, the actual quoted passage varies); a more general heuristic might be "does this paragraph
   read as a direct quotation of a named external authority" (quotation marks, a citation marker
   immediately before/after, a proper-noun-heavy structure).
5. **Author affiliation/contact-info blocks**: paragraphs with 2+ email addresses, or ORCID-heavy
   metadata, that happen to repeat because it's the literal same people's names/affiliations appearing
   near-identically formatted on two of their own papers. Not content at all; cheap to detect (email
   count, ORCID pattern) and exclude before candidate generation even runs.
6. **Sequential editions of the same recurring report/dataset series**: e.g. "The State of AI Ethics
   Report (June 2020)" vs "(October 2020)", "AGILE Index 2024" vs "2025", "NELA-GT-2020" vs "NELA-GT-2021"
   -- a later edition of an ongoing report/dataset naturally carries forward large amounts of the
   previous edition's text, which is expected, not noteworthy. Detectable by title similarity after
   stripping a trailing year/date token.
7. **Genuine embedding false-positives on numeric/tabular content** (a real, distinct finding, not a
   corpus quirk): two completely *different* numeric result tables (different actual percentages,
   different chart categories) still scored 0.92-0.97 cosine similarity, because the sentence embedding
   model can't meaningfully distinguish "a table of percentages" from "a different table of percentages"
   -- it's out of distribution for a model trained on prose. This is exactly the failure mode
   `text_overlap.py`'s `lcs_ratio`/`ngram_jaccard` checks were added to catch (CLAUDE.md's "cosine
   similarity is a semantic measure and can be fooled..."), and in every case found here, both were near
   zero once actually computed -- confirming the design works, but also that a bare cosine-similarity
   candidate (no text-overlap check yet run on it) needs to be treated with real skepticism for
   numeric-heavy paragraphs specifically. A cheap pre-filter (alphabetic-character fraction of the
   paragraph text) could flag "numeric/tabular, verify with text_overlap before trusting this one" at
   candidate-generation time rather than waiting for a human/AI pass to notice.

[DONE] Patterns 1-6 above (everything except the harder-to-fix-upstream bibliography-extraction gap) are
now `classify_dupes.py` -- a real, reusable script, not just this write-up. It's automatically re-applied
to new candidates by `run_pipeline.py` (see below), so this pass doesn't need repeating by hand after
every future retrieval batch. It intentionally does NOT replicate the ~150 individual one-off judgment
calls from the original hand-review (those were genuine per-pair reading, not reproducible rules) -- the
one durable rule it *does* encode: a candidate that survives every "no" pattern and is `same_author=1`
gets marked `yes` automatically (self-reuse), since that was 198 of the 201 original `yes` verdicts.
`same_author=0` survivors are left `ai_check IS NULL` on purpose -- those are the rare, high-value ones
(3 of 201 in the original pass) that deserve an actual look, not a guess.

`write_dupe_reports.py` (per-paper text reports, ranked by count) and `run_pipeline.py` (chains
extract_papers.py -> embed_paragraphs.py -> build_dupe_candidates.py -> classify_dupes.py ->
write_dupe_reports.py, each stage independently skippable) turn "found some candidates" into "here are
updated reports" as one command, so a fresh retrieval batch doesn't require redoing any of this by hand.

# Full-corpus plagiarism audit (2026-08-21): what we actually found, and why scale didn't help

Went through every `potential_dupes` row the user had personally marked (`status='confirmed'`, 24 rows),
then every remaining cross-paper `status='unreviewed'` row in the whole corpus (819 rows spanning 347
distinct paper-pairs) -- not a sample, the full remaining backlog. Corpus at the time: **70,041 papers,
7,596,575 paragraphs**. Bottom line: **the corpus's organic, keyword-driven retrieval has found zero new
real cross-author plagiarism cases.** The only genuine one in the whole database (Saxby/Taro,
`tests/cases/saxby-taro-2023.json`) is a hand-seeded test fixture sourced from a RetractionWatch article,
pinned to local PDFs specifically so the test suite never has to rediscover it -- not something
`bulk_retrieve_arxiv.py`/`bulk_retrieve_crossref.py` turned up on their own at any point.

**Reviewing the 24 `confirmed` rows** (the ones the user had hand-marked while using `review_dupes.py`)
found only 4 rows / 1 case actually holds up -- Saxby/Taro. Everything else fell into one of:
- **Same document, retrieved/cataloged twice** (12 of the 24 rows): a book's DOI matched against its own
  chapter's DOI (`10.1007/...` vs `..._2`/`..._6`), a journal-issue/table-of-contents PDF matched against
  individual articles it contains, a preprint matched against its own later-published version, a straight
  retrieval mixup (one title's PDF was actually a different paper's text entirely).
- **Self-reuse mistaken for cross-author copying** (2 rows, both corrected this session): one was the
  user's own call ("that was a mistake"); the other looked cross-author only because `same_author` was
  wrongly `0` -- the two "different" authors were literally the same person with their name's word order
  reversed between two journals' metadata conventions.
- **Templated/paper-mill content, not real plagiarism** (2 rows, the "AI-based Cloud Governance" /
  "AI-driven Governance" IJCC pair): flagged as legit at first (100% identical text, different authors),
  but a second look -- prompted by the user saying "I don't think it is a dupe" -- found the exact matched
  phrases (including a suspiciously specific fabricated benchmark, "S3 buckets detected in under 1.2
  seconds") appear nowhere else in the corpus, yet cover 7 of ~155 paragraphs in each paper: two
  differently-"authored" papers from a low-scrutiny publisher family sharing an entire fabricated-sounding
  technical section reads as shared-template/paper-mill output, not one author copying another's real work.
  **Lesson: 100% textual identity plus different author names is not sufficient evidence of cross-author
  theft on its own** -- check whether the shared text is *specific and real* (unique to just this pair,
  like Saxby/Taro's shared numbers) vs. *specific but fabricated-sounding* (a mill's template filler);
  both can hit lcs_ratio=1.0.
- **Weak/no real textual overlap despite high cosine similarity** (8 rows): topically-similar prose the
  embedding model scored as near-duplicate despite `lcs_ratio` under 0.1 -- properly-cited reuse of a
  known illustrative example, generic "intelligent tutoring systems" boilerplate, one weak paragraph-pair
  within the otherwise-real Saxby/Taro case that itself didn't hold up on inspection.

**Sweeping the remaining 819 cross-paper `unreviewed` rows** (every one of them, not a sample) found the
same handful of patterns accounting for all of it, confirmed via `title_similarity()`, `english_score()`,
and LSH-bucket-membership checks rather than reading each of the 819 individually where a mechanical check
could settle it:
- **705 `potential_dupes` rows total (across this session) turned out to be "same paper retrieved/cataloged
  twice"** -- title-case variants, one typo ("Optimiazation"), an HTML-entity-corrupted title (`&amp;`
  defeated the similarity check), a `Correction:` notice vs. the original, more book/chapter and
  journal-issue/article pairs of the kind found in the 24. This is a real gap in `retrieve_papers.py`'s
  `make_key()` slugify-based dedup, not a dupe-finding problem -- it just showed up as one because every
  duplicate-retrieved pair generates spurious "cross-paper" candidates.
- **249 rows were pure embedding noise on non-English text** (Russian, Ukrainian, Korean) -- `lcs_ratio`
  and `ngram_jaccard` both ~0.00, `english_score()` (already built for exactly this, see `review_dupes.py`)
  confirmed neither side was meaningfully English. `all-MiniLM-L6-v2` is English-tuned; cosine similarity
  on non-English prose it wasn't trained for is not a signal.
- **173 rows were genuine same-author self-reuse** across two real, distinct papers (a shared
  definition/methodology/example passage) -- real duplicated text, same ethical category as the existing
  `same_author=1` flag already distinguishes, not cross-author plagiarism.
- **The rest (~130 rows) were shared external material**: both papers independently quoting the same
  external source -- the canonical NIST Mell & Grance (2011) cloud-computing definitions, an AI-reporting
  guideline checklist item shared between two different reporting-standard papers, funding/CC-license/
  affiliation boilerplate, dataset-description boilerplate (Adult Census Income, WinoGender), VIF/
  multicollinearity statistical-methods boilerplate. One more templated/paper-mill pair surfaced here too
  (different DOI-prefix family, `10.63282`, only 1 candidate row -- an isolated instance, not (yet) a
  pattern at this corpus's size).
- **Net new genuinely undiscovered cross-author case count: 0.**

## Why scale didn't find more, and what would actually help

70,041 papers turned out to be the wrong lever. Corpus composition: arXiv is 12,539 papers (~18%, no
longer the 99.94%-arXiv skew an earlier corpus-composition check found), the rest spread across
legitimate, high-scrutiny publishers (Springer 7,659, BMC 4,112, Frontiers 2,840, IEEE 2,837, Nature
1,661, JMIR 1,495, PLOS 1,492) -- broad and diverse, but structurally excluding exactly the venue types
where real plagiarism concentrates. Only **2 of 70,041 papers have no DOI** (i.e. close to zero
organically-retrieved theses/dissertations made it in at all -- `retrieve_papers.py`'s Crossref-based
resolution structurally can't find them, a gap already documented in CLAUDE.md). The one real, documented
case in this whole project (Saxby/Taro) is exactly that missing category: an obscure PhD dissertation,
sitting in a university repository, never indexed anywhere that would cross-reference it against the
paper that copied it -- it took a whistleblower and RetractionWatch to surface it, not a corpus scan.
The AI-ethics/fairness keyword niche this project retrieves from is also a small, tightly-networked
research community (arXiv-heavy, high mutual visibility) -- close to the opposite of where undetected
copying survives.

Concretely, for whoever picks this back up:
1. **Retrieve theses/dissertations in bulk, on purpose** -- OATD, NDLTD, CORE, or BASE (all harvestable),
   not incidental Crossref hits. Highest-leverage single change: it's the actual source category of the
   one confirmed case in the corpus.
2. **Pull directly from RetractionWatch's retraction database** (reason codes include plagiarism, often
   already links the original) instead of hoping the general corpus intersects with a known case by
   chance -- confirmed pairs at high yield instead of a guess.
3. **Target known lower-scrutiny publisher families on purpose** -- this session's IAEME/`ijcc` and
   `10.63282` finds (both templated/paper-mill-flavored, not confirmed cross-author theft, but the right
   neighborhood) suggest walking Crossref by publisher/DOI-prefix would surface more of exactly this than
   keyword search does incidentally.
4. **Fix the duplicate-retrieval dedup gap** -- 705 pairs this session were pure "same paper indexed
   twice," not a plagiarism-finding matter at all; a fuzzier "have we already got this" check at retrieval
   time (title-similarity/DOI, the same check used to clean this up after the fact) would shrink every
   future review queue for free.
5. **Decide on non-English content deliberately** -- either filter it out before embedding (cheaper than
   discovering the noise post-hoc, and 249 of 819 rows this session were exactly this) or, if
   cross-language plagiarism is an actual goal, switch to a multilingual embedding model -- `all-MiniLM-
   L6-v2` currently produces pure cost with no signal on non-English text.

[DONE 2026-08-21] Built 1, 3, 4, 5 same session (2 not built -- todo.md item under "data sources" is
the record of it, RetractionWatch needs a real harvesting design of its own):

- **1: `bulk_retrieve_theses.py`** -- searches DataCite's public `/dois` API
  (`resource-type-id=dissertation`), reusing bulk_retrieve_crossref.py's own keyword list (theses on
  the same topics are exactly what's wanted) and retrieve_papers.py's Unpaywall/download pipeline.
  One real fix beyond a straight port: switched to
  `download_with_landing_page_fallback()` (the citation_pdf_url rescue retrieve_papers.py's own
  docstring already documents as the single biggest lever for thesis download rate) instead of the
  plain `download_pdf()` bulk_retrieve_crossref.py uses -- without it, a live 15-dissertation test
  batch downloaded 0/15 (KAUST/Alberta/Cambridge/EPFL/UT Austin all handed back a landing page, not
  a PDF, from Unpaywall); with it, 5/15, all verified real multi-page PDFs (one a genuine 98-page
  dissertation).
- **3: `--doi-prefix`** on bulk_retrieve_crossref.py -- restricts a keyword search to one publisher's
  DOI-prefix block via Crossref's `filter=prefix:...`, combinable with the existing keyword loop.
  Backward compatible (omitted = old unrestricted behavior).
- **4: `find_duplicate_papers.py`** -- detects papers retrieved/cataloged twice (exact DOI match, or
  exact title match after HTML-unescape/lowercase/punctuation-strip normalization) and registers them
  in a new `duplicate_papers` table; `build_dupe_candidates.py` now consults it so a known-duplicate
  pair is treated as same_paper from candidate-generation time forward, not just corrected after a
  human notices. **A serious false-positive bug found and fixed on the very first real run**, worth
  its own paragraph:

  The first run against the real 70,041-paper corpus produced 3,563 "duplicate" pairs -- title
  normalization alone found 3,545 of them. Almost all wrong: generic recurring publisher furniture
  collapsed into giant single-title groups that are actually hundreds of genuinely different
  documents -- "Front Cover" (561 papers), "Masthead" (528), "Reliability Society" (435), "Abstract"
  (231), "Table of Contents" (66), "Introduction" (45), "Editorial" (28) -- plus a structurally
  generated pattern at longer title lengths that a length floor alone wouldn't have caught: 171
  papers all titled "Review of <the same submission title>" (an OpenReview-style per-reviewer report
  title -- one genuinely different document per reviewer, all sharing an auto-generated title).
  Root cause: title-normalization equality with no bound on how many papers can share one normalized
  title. Fixed with a group-size cap (>3 members means "recurring pattern", not "one paper retrieved
  twice") plus a title-length floor as defense in depth; re-run found 1,255 pairs, spot-checked and
  legitimate (distinctive multi-word titles, case/typo/HTML-entity variants of each other). Repaired
  the 61 `potential_dupes` rows the buggy run had actually touched by recomputing same_author/
  chronology/later_cites_earlier from scratch for whichever pairs didn't survive into the corrected
  set -- confirmed `status`/`reviewed_at` were never touched (mark_same_paper()'s own convention), so
  no human review was ever at risk, only the automated enrichment fields. Lesson, same shape as the
  paragraphs.jsonl bug above: an aggregation rule ("these share a key, so they're duplicates") needs
  an explicit bound on group size before it's safe to trust, not just a plausible-sounding key.
- **5: `--min-english-score`** (embed_paragraphs.py, default 0.03) -- skips embedding (and durably
  marks as skipped, so it isn't reconsidered every run -- see paragraphs.jsonl section above) any
  paragraph english_score() scores below threshold. Chose "filter it out" over "switch to a
  multilingual model": far cheaper, and this project's stated goal never specified cross-language
  scope. Only affects paragraphs not yet embedded -- doesn't retroactively strip the ~249 non-English
  embeddings already in the corpus from before this existed (those already got manually resolved to
  `false_positive` this session; a full retroactive re-scan is a possible follow-up, not done here).

**[DONE 2026-08-21, later same day] OpenAlex batch OA-location lookup, after kicking off 1/3/1
above for real.** The broad general-Crossref job (67,720 candidates, no `--doi-prefix`) projected
~19 hours just for its Unpaywall phase -- 1 req/sec, no batch endpoint, tens of thousands of DOIs.
Investigated buying a paid Unpaywall tier first: doesn't exist for this (the only paid product is
the Data Feed, a subscription for a *self-hosted continuously-synced copy* of the whole database,
not a higher API rate; the free REST API's real ceiling is already ~100k requests/day, so paying
buys nothing here). OpenAlex, which ingests Unpaywall's own OA data plus more, turned out to have
the actual lever: `filter=doi:a|b|c` resolves up to 50 DOIs in one request
(https://blog.openalex.org/fetch-multiple-dois-in-one-openalex-api-request/). Added
`retrieve_papers.query_openalex_batch()`; both bulk scripts now batch-resolve every harvested
candidate through it first and only fall back to the original per-DOI `query_unpaywall()` for
whatever it doesn't find (coverage-neutral, not a tradeoff -- OpenAlex and Unpaywall draw from
overlapping but not identical sources). Live-tested against 20 real candidates before touching the
already-running jobs (16/20 resolved in one batched call, 8/8 subsequent downloads were real PDFs),
covered by 10 new unit tests (`TestQueryOpenalexBatch`/`TestChunked`), then the three running jobs
were killed and restarted with it -- safe/lossless, since `state.sqlite3` already durably tracked
every paper downloaded before the restart and skips them automatically. Real resolution rates on
this corpus's actual candidate pool: low-tier publisher batch 91% resolved directly, theses batch
56% (thinner OpenAlex/Unpaywall coverage of DataCite-registered dissertation DOIs, consistent with
the existing landing-page-fallback note above).

`run_after_retrieval.sh` (new): waits for all three retrieval jobs to fully exit, then runs
`run_pipeline.py` (extract -> embed -> build_dupe_candidates -> classify -> reports) once, rather
than starting it as soon as any one job finishes -- running the heavy extract/embed stages
concurrently with still-active retrieval is exactly the "heavy sustained multi-hour write load"
pattern that caused the real crash documented in this file's "System / hardware reliability"
section. One clean handoff after retrieval is fully done, not overlapping heavy stages.

**Post-mortem 7 (2026-08-24): the boilerplate-marker heuristic from post-mortem 5/6 (`inspect_lsh_buckets.py`)
badly under-detects real boilerplate once applied broadly -- built `find_review_candidates.py` to surface
the top 100 unclassified (`ai_check IS NULL`), cross-author (`same_author=0`) `potential_dupes` candidates
scoring < 0.99 on similarity/lcs_ratio/ngram_jaccard (i.e. real-but-not-exact overlap, the profile genuine
paraphrased copying should have), and manually read all 100. Result: the keyword-marker list flagged only
17/100 as boilerplate; a full read found the true boilerplate fraction was far higher -- categories the
marker list had no coverage for at all:**
- journal running headers/footers and masthead text repeated across every article in an issue (e.g.
  "Scholars Journal of Engineering and Technology... Journal homepage:...", "Frontiers in Medicine ...
  frontiersin.org Publisher's note...")
- standardized systematic-review/reporting checklists (PRISMA, ARRIVE) -- the *exact same* checklist-item
  wording is reproduced verbatim across every paper that follows that reporting standard, by design
- PDF-scraped website UI chrome, not article content at all -- e.g. Copernicus/EGU discussion-paper
  pages' navigation text ("Title Page Abstract Introduction Conclusions References Tables Figures ◀ ▶
  ... Printer-friendly Version") landing in `pdftotext`'s output and getting treated as a real paragraph
- old government/NTIS report distribution stamps ("U.S. DEPARTMENT OF COMMERCE National Technical
  Information Service...")
- a shared *externally-cited* source (the same textbook, standard, or table reproduced/cited by multiple,
  otherwise-unrelated papers -- e.g. the same NIST SP 800-82 passage, or the same Russian-language legal
  textbook citation appearing at different footnote numbers in three unrelated papers) -- real overlap,
  but the right call is `(c)itation`, not plagiarism between the two candidate papers themselves

**A second, distinct thing this same scan surfaced: several clear "these are the same paper retrieved
under two different `paper_id`s" cases that `find_duplicate_papers.py` did NOT catch** -- near-identical
titles differing only in capitalization or a single word ("Reporting animal research: Explanation and
Elaboration..." vs "...explanation and elaboration...", "Think About the Stakeholders First!" vs "Think
about the stakeholders first!", "Interventions for long-term software security" vs "Interventions for
Software Security"), with matching near-verbatim body text confirming it's the same underlying document,
not two authors independently writing similarly. `find_duplicate_papers.py`'s own docstring already
documents this as an accepted gap (deliberately no fuzzy/typo-tolerant title matching, by design -- that
tier needs human judgment) -- this is a real, current instance of exactly that documented gap, not a new
bug, but worth flagging as a concrete backlog item for whoever next reviews `review_dupes.py`'s `(p)` key
against this batch.

**Practical upshot:** any keyword-list-based boilerplate heuristic (this project's or anyone else's) needs
to be understood as a *rough triage aid*, not a real classifier -- it will systematically undercount
boilerplate whose actual form isn't in the list yet, and the list can never be complete (this project
alone found 4+ new categories in one 100-row sample it hadn't seen before). A full manual read of a
*ranked-by-signal* sample (highest lcs_ratio first, here) surfaces categories a keyword list simply cannot
anticipate -- there is no substitute for that at some cadence, however small the sample.

**The exact query** (`find_review_candidates.py`'s `find_candidates()`), for reproducing this sample again
later or re-running it after more `ai_check IS NULL` rows accumulate:

```sql
SELECT pd.id, pd.similarity, pd.lcs_ratio, pd.ngram_jaccard,
       pd.paper_id_1, pd.paper_id_2, pp1.title, pp2.title,
       p1.text, p2.text
FROM potential_dupes pd
JOIN papers pp1 ON pp1.id = pd.paper_id_1
JOIN papers pp2 ON pp2.id = pd.paper_id_2
JOIN paragraphs p1 ON p1.id = pd.paragraph_id_1
JOIN paragraphs p2 ON p2.id = pd.paragraph_id_2
WHERE pd.ai_check IS NULL
  AND pd.same_paper = 0
  AND pd.same_author = 0
  AND pd.similarity < 0.99
  AND pd.lcs_ratio < 0.99
  AND pd.ngram_jaccard < 0.99
ORDER BY pd.lcs_ratio DESC, pd.similarity DESC
LIMIT 100
```

`same_paper = 0` was added explicitly after the fact (the original version of this query only had
`same_author = 0`, which happens to imply `same_paper = 0` today -- `build_dupe_candidates.py` only ever
sets `same_author` for `same_paper = 0` rows, `same_paper = 1` rows always have `same_author IS NULL` --
but stating it directly means this query's intent doesn't quietly depend on remembering that convention
holds).

**Follow-up (2026-08-24, same day): turned the 7 new boilerplate categories above into real
`classify_dupes.py` patterns** (journal masthead/homepage links, Springer/Frontiers "Publisher's note",
NTIS report stamps, Copernicus/EGU website navigation chrome, "please cite this paper as", "electronic
supplementary material", numbered bibliography entries) rather than leaving them as a one-off reading
exercise -- see `classify_dupes.py`'s `TEXT_PATTERNS` list and `tests/unit/test_classify_dupes.py`.
Re-running `classify_dupes.py` against the live DB (safe, only touches `ai_check IS NULL` rows) picked up
51 more candidates as `no` out of its real working pool of 6,262 (`same_paper=0 AND ai_check IS NULL` --
the "22,034" figure quoted earlier in conversation was every `ai_check IS NULL` row including
`same_paper=1` ones, which `classify_dupes.py` never touches; the real cross-paper backlog was smaller).
The PRISMA/ARRIVE-checklist-quoting category and the "same paper retrieved twice" cases were deliberately
NOT turned into patterns here -- the former is too example-content-specific to generalize without risking
false positives on real methods prose, the latter is `find_duplicate_papers.py`'s documented human-judgment
tier, not a text-boilerplate problem.

**Second batch (2026-08-24, same day): read rank 101-250 of the same query** (`find_review_candidates.py
--offset 100 --limit 150`) rather than stopping at one 100-row sample -- found more categories the first
pass of new patterns didn't cover, and one structural fix, not just more patterns:
- A **huge** Springer Nature "Publisher's Note...remains neutral with regard to jurisdictional claims"
  cluster -- a *different* Publisher's Note wording than the Frontiers one already covered (same concept,
  different publisher's boilerplate text), overwhelmingly on `Correction:`/`Publisher Correction:` articles.
- University of Glasgow "Enlighten" institutional-repository deposit stamps (`eprints.gla.ac.uk`), DOE
  national-lab funding acknowledgments ("performed under the auspices of the U.S. Department of Energy"),
  DOE/OSTI report-distribution boilerplate, U.S. Government Printing Office distribution boilerplate, and
  Italian/Spanish national open-access funding agreement boilerplate (CRUI-CARE / CRUE-CSIC).
- **The structural fix, not just a new pattern**: a whole cluster of DOE "Environmental regulatory update
  table, `<Month>` `<Year>`" report-series pairs was falling through every check -- not because the pairs
  weren't recognized as the same recurring series, but because `classify_row()`'s existing
  "sequential editions of the same recurring report/index series" logic (`strip_trailing_date()`) only
  stripped a *parenthesized* `(Month Year)` or a *bare trailing year* -- this series spells its date as
  `, Month Year` (comma, no parens) and sometimes `Month/Month Year. Revision N`, a format that regex
  simply never matched, so two editions of the literal same report always looked like two different
  titles. Added `BARE_MONTH_YEAR_RE` to catch that format, and made the title-equality check
  case-insensitive (some editions differ only in `Update Table` vs `update table` capitalization).
- Applying just the second batch (`classify_dupes.py` re-run): **410 more candidates `no`** (out of the
  6,262-row same_paper=0 pool minus the 51 the first batch already resolved) -- 104 Springer Nature notes,
  190 from the DOE report-series date fix, 24 DOE/OSTI, 10 Glasgow Enlighten, 9 Italian/Spanish funding
  agreements, plus the date-stripping fix generalizing for free to several *other* recurring-series title
  pairs that happened to only differ by date-format or capitalization (e.g. "IEEE Computer Society
  information" vs "...Information").
- Total across both batches: 461 of 6,262 unclassified cross-author candidates resolved by pattern/title
  fixes alone, no human judgment needed for any of them. Confirms post-mortem 7's original point again --
  reading a *second* ranked slice past the first 100 still surfaced genuinely new categories, not
  diminishing returns yet.

**Third batch (2026-08-24, same day): re-ran `find_review_candidates.py` from the top (`--limit 150`, no
offset) after the first two batches resolved 461 rows -- since the query only ever selects
`ai_check IS NULL` rows, re-running naturally surfaces a fresh top-150 slice (previously-classified rows
drop out, new ones rise to rank 1-150) without needing to track offsets across runs.** Found:
`declaration of interest` / BioMed Central "Declarations" blocks (ethics approval/consent/competing
interests combined), Dutch "Taverne"/Article 25fa copyright-license boilerplate, Frontiers "Supplementary
material...can be found online" notices, medRxiv preprint license boilerplate, UK HMSO monograph copyright
boilerplate, a French journal-platform copyright footer (Cairn/OpenEdition-style, "Tous droits réservés...
sauf mention contraire"), and a single-journal citation-block template (Bioinformation, "Edited by P.
Kangueane..." on every issue). Also broadened the existing "journal homepage:" pattern to drop the
required colon after finding a real miss ("Journal homepage http://..." with no colon).

Result: **39 more resolved** (7 Dutch 25fa, 6 French footer, 6 declaration-of-interest, 5 Frontiers supp.
material, 4 medRxiv, 4 BioMed declarations block, 3 HMSO, 3 Bioinformation, 1 journal-homepage-no-colon).
**Running total across all three batches: 500 of 6,262 resolved, 5,762 remaining.** The per-batch yield
dropped sharply (461 -> 39) -- a real signal, not noise: the first two batches were dominated by a few
very large, very common categories (Springer Nature's two Publisher's Note variants, the DOE report
series alone accounted for ~190), and those get exhausted. What's left after three rounds increasingly
reads as content that actually needs semantic/human judgment (paraphrased citations of the same source,
genuine same-topic prose, shared quoted material) rather than more boilerplate categories waiting to be
found -- consistent with post-mortem 7's own point that a keyword list can never be complete, but also a
sign that continuing to mine *this specific slice* for more patterns is starting to hit diminishing
returns. A future pass would likely do better sampling a different slice of the ranking (e.g. lower
lcs_ratio, or filtering to specific `same_author=0` high-`ngram_jaccard`-low-`lcs_ratio` pairs, the profile
distributed-rather-than-contiguous copying has) than reading further down this same one.

**Fourth batch (2026-08-24, same day): switched to that different slice.** Added a `--sort gap` mode to
`find_review_candidates.py` (`ORDER BY (ngram_jaccard - lcs_ratio) DESC` instead of `lcs_ratio DESC`) --
surfaces *reworded* copying (n-grams matching throughout a paragraph without one long contiguous run:
clauses reordered, synonyms swapped, sentences split/merged) rather than verbatim-with-minor-edits, which
`lcs` mode already covers well. Ran it (`--sort gap --limit 150`) and read the result.

This slice behaved differently than the first three, as expected going in: it surfaced far LESS pure
boilerplate (one new category found, see below) and far MORE genuinely-interesting content -- which makes
sense, since "ngram overlap without one contiguous run" is closer to the actual profile of real paraphrased
plagiarism than to the profile of verbatim-reused boilerplate. Two concrete outcomes from reading it:

1. **One real new boilerplate pattern**: a university "thesis by publication" policy declaration
   ("Publications can be used in \[their/the candidate's\] thesis in lieu of a Chapter if...") reused
   verbatim across multiple *different* theses at the same institution -- a real, if narrow, boilerplate
   category the first three batches' slice never surfaced (added to `TEXT_PATTERNS`, resolved 6 rows).
2. **A real data-hygiene gap, not a text pattern**: this slice surfaced two papers ("Indoor 3D Video
   Monitoring Using Multiple Kinect Depth-Cameras", paper_ids 65626/62515) with the *exact same title* and
   near-identical DOIs (`10.5121/ijma.2013.6105` vs `10.5121/ijma.2014.6105` -- same journal/volume, only
   the year differs) that were still showing up as unreviewed cross-paper "plagiarism" candidates --
   `find_duplicate_papers.py` should have caught this (identical titles is exactly its second detection
   tier) but simply hadn't been *re-run* since the large retrieval batches earlier this session added the
   papers in question. Re-ran it (safe/idempotent, per its own docs): **355 new duplicate-paper pairs
   found, 12,776 existing `potential_dupes` rows corrected to `same_paper=1`** in one pass. Confirmed the
   Kinect pair specifically is now `same_paper=1`. Lesson: `find_duplicate_papers.py` needs to be re-run
   periodically as new papers are retrieved, not just once -- it was easy to forget precisely because nothing
   *fails* when it's stale, candidates just keep silently surfacing as if they were real cross-paper matches.

Combined with the new pattern: **506 of ~6,250 originally-unclassified cross-author candidates now
resolved across four batches, 5,744 remaining.** (The exact denominator shifts run to run as
`find_duplicate_papers.py`/`classify_dupes.py` both correct and reclassify rows, so treat these running
totals as approximate snapshots, not a precise ledger.) The bigger lesson from this batch specifically:
switching ranking slices didn't just find different boilerplate -- it surfaced a completely different
*kind* of gap (stale paper-identity bookkeeping) that the `lcs`-ranked slice would never have surfaced,
because `lcs` mode's top rows were dominated by verbatim near-duplicate text where the "same paper twice"
explanation was already obvious from the title alone; `gap` mode's more scattered/reworded matches made a
same-title pair with lower textual overlap visible in a spot worth actually looking at closely.

**Follow-up (2026-08-24): applied tier 3 to the full corpus, then read a fresh random 200 to check the
fix actually worked** (as opposed to just trusting the spot-checked validation sample). Real numbers:
**447 new duplicate-paper pairs found, 17,481 existing `potential_dupes` rows corrected to `same_paper=1`
in one run** -- roughly half the entire table. The unclassified cross-author pool dropped from ~5,744 to
2,808. Verified against the original random-200 sample's specific flagged pairs: 9 of 10 now correctly
`same_paper=1`; the tenth (`11567`<->`11696`, "Security and Privacy for Edge AI" vs "Reliability Society")
correctly stayed `same_paper=0` -- it only shares 1 of 11 paragraphs (a journal masthead/admin-committee
list reused across different issues, genuine boilerplate, not a duplicated document) -- confirming the
50% threshold discriminates real cases from partial-boilerplate-reuse rather than just flagging everything.

**A fresh random-200 sample from the cleaned-up pool told a genuinely different story than the first
one** -- read all 200 by hand, found **zero new plagiarism candidates**. What's left after the tier-3 fix
is dominated by: shared external citations (same source cited by unrelated papers), boilerplate patterns
(funding acknowledgments, VIF/multicollinearity statistical boilerplate phrasing, HTA-monograph
"Paying by credit card" back-matter, PRISMA methodology boilerplate), and coincidental non-English/
cross-topic cosine-similarity noise (high embedding similarity, near-zero lcs_ratio/ngram_jaccard --
`all-MiniLM-L6-v2` producing noise on non-English pairs, the same documented gap `embed_paragraphs.py`'s
`--min-english-score` filter targets, evidently not catching every case).

**One more real sub-category surfaced, deliberately NOT automated**: a handful of "same paper, different
PDF extraction" pairs -- a preprint vs. its own published-journal version, or two revisions of the same
NIST guide -- where formatting/numbering differs enough (different theorem/citation numbering, reworded
sentences) that byte-exact-overlap fraction stays low (0-15%) even though the papers are clearly the same
or a near-identical work. Tier 3 correctly does NOT flag these (the threshold would need to drop low
enough to risk false-collapsing genuinely different-but-related editions, e.g. two different NIST SP
800-82 revisions really can have substantively different content in places). One clear instance
(paper_ids 30448/33037, "Self-Stabilizing and Private Distributed Shared Atomic Memory in Seldom-Fairness
Environments" -- recurred 6+ times across both 200-row samples with consistent theorem/citation-number
shifts, the exact fingerprint of a preprint-vs-journal-version pair) was resolved by hand via
`mark_same_paper()`, matching `review_dupes.py`'s own `(p)` key semantics -- this is real judgment-call
territory, not a new pattern to generalize.

**Final numbers after all of today's fixes**: `potential_dupes` total 36,799; `same_paper` 29,150/7,649;
unclassified cross-author pool 2,797 (down from ~6,262 at the start of today's pattern-mining, and further
down from a corpus-scale-driven high before the LSH bucket-saturation fix even started). The remaining
pool is genuinely harder from here -- no more single systemic bugs found in two full random-sample passes,
just organic noise and edge cases needing real per-case judgment (`review_dupes.py`), not more automation.

**Fifth batch (2026-08-24, same day): a fundamentally different kind of rule -- not a text pattern, a
metric-threshold rule.** Checked the 2,797-row pool's `lcs_ratio`/`ngram_jaccard` distribution directly:
**1,146 rows (41%) had both metrics below 0.05** despite clearing the >=0.85 cosine-similarity bar
`build_dupe_candidates.py` requires to generate a candidate at all -- i.e. embeddings call these
"similar," but almost no actual words are shared. Hand-read 30 random examples at this threshold before
trusting it (same discipline as every other rule added today): all 30 were either coincidental
same-topic-different-content pairs, or non-English pairs where `all-MiniLM-L6-v2` (English-tuned)
produces cosine-similarity noise rather than signal -- exactly the documented gap
`embed_paragraphs.py --min-english-score` targets, evidently still slipping candidates through at the
pairwise-comparison stage even with that filter active on individual paragraphs.

Added as a new rule in `classify_dupes.py`'s `classify_row()` (not `TEXT_PATTERNS`, since it's a numeric
threshold on the already-computed overlap metrics, not a text pattern) -- deliberately a much stricter
threshold (0.05) than the existing numeric/tabular-content check just below it (0.3), and deliberately
validated separately rather than assumed to generalize from that existing check's threshold. Also
refactored `classify_row()`/`run()` to accept precomputed `lcs_ratio`/`ngram_jaccard` from
`potential_dupes` (already persisted by `build_dupe_candidates.py`) instead of recomputing them from text
every time -- cheap either way at this scale, but avoids doing the same O(paragraph-length) work twice for
every row `run()` processes going forward.

Applied to the real pool: **1,146 rows resolved** (matched the projection exactly), pool now **1,651**.

**Deliberately did NOT push the threshold higher.** Spot-checked the 0.05-0.15 band (25 more random
examples) before deciding: it's a *qualitatively different* population, not just "the same pattern with
more noise." Several are papers independently describing the *same standard technical concept* (the PSO
algorithm, LIME, AES, Blowfish, secure multi-party computation, perplexity) in similarly-phrased academic
language -- real but weak textual similarity from both papers drawing on the same standard
textbook/Wikipedia-style explanation of a well-known technique, not evidence of copying between *these
two specific papers*. That's a plausible future pattern (a "convergent standard-technique description"
category, similar in spirit to the existing PRISMA/ARRIVE-checklist and Datasheets-for-Datasets template
rules already in `TEXT_PATTERNS`) but it needs its own validation the same way everything else today did,
not an assumption that 0.05's clean result extends to 0.15. Left as unreviewed rather than guessed.

**Running total, all five batches today: 1,796 of the original ~6,262-row backlog resolved by
pattern/threshold/title fixes with zero human judgment spent, plus 17,481 `potential_dupes` rows
corrected via the tier-3 content-overlap fix's `same_paper` correction (a separate, larger number --
that fix corrected *existing* candidate rows across the whole corpus, not just today's unclassified
pool). Final unclassified cross-author pool: 1,651**, down from ~6,262 at the start of today and from
2,797 after the systemic same-document-artifact fix alone. All changes tested (204 unit tests passing
throughout today's work) and documented here rather than only in commit history, per this file's own
purpose.

**Sixth batch (2026-08-24, same day): targeted review of the strongest-verbatim-overlap slice
(`lcs_ratio>=0.999` or `ngram_jaccard>=0.999`) specifically, at the user's request.** The five batches
above worked from ranked (`lcs`, `gap`) or random samples of the whole unclassified pool; this pass
instead isolated the subset carrying the single strongest "this text is identical" signal available
(a perfect longest-common-run or perfect 5-gram-Jaccard score) and read through it directly, on the
theory that real plagiarism -- as opposed to boilerplate reuse -- would be most likely to concentrate
there. Read several hundred rows across three successive fresh queries (the query was re-run after each
pattern batch was applied, since resolved rows drop out and the top of the ranking shifts each time).

Findings, in the order they came up:

- **No new real cross-author plagiarism found** in this slice beyond what earlier batches had already
  surfaced. Every row read was one of: (a) more institutional/publisher boilerplate (below), (b) a
  same-paper duplicate not yet registered in `duplicate_papers` (below), (c) an author-extraction gap
  producing a false `same_author=0` on a genuine self-citation (below), or (d) genuine independent-paper
  overlap that's either a shared results table/baseline (e.g. two IBM fairness papers both reporting the
  same COMPAS accuracy numbers -- plausibly common co-authorship or a shared benchmark, not investigated
  further) or two clearly-different, unrelated papers whose *only* shared text is 1-2 sentences of
  incidental phrasing -- neither worth an automated rule.
- **15 new boilerplate patterns** added to `TEXT_PATTERNS`, each validated with a unit test before being
  applied to the real DB, same discipline as every batch above:
  - NCCHTA/HTA monograph series ordering-information boilerplate (credit-card payment methods, cheque
    payment instructions) and its Gray Publishing, Tunbridge Wells publisher-imprint line -- a UK Health
    Technology Assessment report series where every monograph carries the same back-matter ordering page.
  - NIST institute-locations address boilerplate (Gaithersburg MD / Boulder CO) and NTIS's own mailing-
    address/ordering block (5285 Port Royal Rd, Springfield VA) -- distinct from the existing "national
    technical information service" pattern, which only matches the spelled-out name, not this address-only
    variant.
  - Sandia National Laboratories' standard DOE-contract funding/imprint line (`DE-AC04-94AL85000`) --
    recurs across every Sandia-authored report regardless of topic.
  - A Springer Nature standard CC-BY rights-and-permissions paragraph ("...not permitted by statutory
    regulation or exceeds the permitted use, you will need to obtain permission directly from the
    copyright holder...") -- a *different* paragraph of the same standard Springer front/back matter as
    the existing "jurisdictional claims" Publisher's Note rule, appearing across 8+ unrelated Springer
    CC-BY article pairs in this slice alone.
  - **Fixed a real miss in the existing "jurisdictional claims" pattern**: some PDFs' `pdftotext` output
    dehyphenates "jurisdictional" into "juris dictional" (with a space), which the plain-spelling regex
    didn't match. Widened to `juris\s*dictional`. This is exactly the kind of extraction-artifact gap
    `extract_papers.py`'s own docstring warns about generically -- worth remembering as a pattern to check
    for on *any* future TEXT_PATTERNS regex that spans a naturally-hyphenatable compound word.
  - IJCSMC journal masthead boilerplate and IEEE Computer Society Jobs Board advertising-filler pages
    (proceedings volumes routinely embed a full-page job-board ad between papers; `extract_papers.py`
    extracts it as ordinary body text with no way to know it isn't).
  - A Turkish journal's standard funding-disclosure/author-contributions clause (`herhangi bir destek
    alınmamıştır` / `Yazar Katkıları`) -- found via a cluster of Turkish nursing/health-ethics journal
    papers, the first non-English/non-German boilerplate family found in this pass.
  - A German academic-repository (SSOAR/peDOCS-style) standard non-commercial-use licence clause ("Sie
    dürfen die Dokumente nicht für öffentliche oder kommerzielle Zwecke...").
- **One same-paper pair not yet caught by `find_duplicate_papers.py`'s three tiers**: paper ids 12185 and
  17529, both titled "The Importance of Modeling Data Missingness in Algorithmic Fairness: A Causal
  Perspective" -- one copy has a literal `\n` embedded mid-title from a PDF-extraction line break, which
  defeated tier 2's normalized-title exact match. Corrected by hand via `review_dupes.py`'s
  `mark_same_paper()` (4 `potential_dupes` rows corrected). Not generalized into a code fix (a single
  observed case isn't enough to justify a fuzzy-title-matching change to `find_duplicate_papers.py`,
  which deliberately stays exact-match-only -- see that script's docstring); logged here in case a second
  instance of this exact failure mode (an embedded newline defeating normalization) shows up later.
- **One author-extraction-gap self-citation cluster resolved by hand**: 4 candidate rows between papers by
  Mark Coeckelbergh (a recurring solo-author affiliation/email signature footer -- "M. Coeckelbergh ...
  University of Twente..." -- appearing verbatim across 4 of his own papers), where 2 of the 4 papers have
  an empty `authors` list (extraction failure) causing `same_author` to compute as `0` instead of `1`.
  Same root cause and same manual `ai_check='yes'` treatment as the Zoo of Fairness Metrics / Gabriel
  Silva-Atencio clusters found in the fifth-batch review -- confirms this is a recurring, not one-off,
  failure mode of paragraph-level author matching. **Not yet worth a code fix**: doing this properly would
  mean either re-running author extraction on the affected papers or adding a name-similarity fallback to
  `same_author` computation, both bigger changes than this pass's scope; three confirmed instances so far
  (Coeckelbergh, Zoo of Fairness Metrics, Silva-Atencio) is a real pattern worth eventually fixing at the
  root, not just papering over case-by-case forever.

**Running total, all six batches today: pool went from ~6,262 at the start of the day to 1,367** after
this batch (1,651 -> 1,367, a reduction of 284 via the 15 new patterns plus manual resolution of the two
individual cases above), plus the separate 17,481-row `same_paper` correction from the tier-3 content-
overlap fix. All changes tested (229 unit tests passing after this batch) before being applied to the
real DB, consistent with every batch above.

**Seventh batch (2026-08-24, same day): back to a plain random-200 sample of the whole pool (not the
lcs=1/ngram=1 slice), at the user's request** -- same method as the second-batch random-200 review earlier
today, re-run against the pool as it stood after the sixth batch (1,367). Read all 200 rows.

Findings:

- **Again, no new real cross-author plagiarism** -- every row was boilerplate, a same-paper duplicate, an
  author-extraction-gap self-citation, or genuine-but-uninteresting independent overlap (shared results
  tables, convergent standard-technique descriptions, thesis-vs-published-paper pairs), the same four
  buckets as every batch today.
- **11 more boilerplate patterns** added and validated with unit tests before touching the real DB:
  LSHTM institutional-repository licence notice; UNSW thesis "Candidate's Declaration"; a broader
  NIST/NBS address-only variant (`located at Boulder, CO 80303` without requiring the Gaithersburg ZIP the
  existing pattern needed); NIST/NBS's "Standard Reference Materials...calibration services" mission-
  statement boilerplate; an EPA pesticide risk-assessment fact-sheet's standard study-category list
  (`fate in animals (rats), fate in plants...`); a JMIR journal `XSL•FO RenderX` XML-pipeline artifact;
  SDSS-III's standard funding acknowledgment; and journal masthead lines for two more venues (IJET/
  SciencePubCo, and the Hill Publishing "Journal of Humanities, Arts and Social Science") that each
  recurred 3 times in this 200-row sample alone -- likely to recur far more across the full corpus given
  every issue of both journals carries the same masthead text.
- **Also widened two existing patterns** that this sample showed real misses in: the OECD/Springer
  jurisdictional-claims-note pattern now matches on the clause itself (`jurisdictional claims in
  published maps`) rather than requiring the `remains neutral with regard to` lead-in, since some
  paragraph splits cut that lead-in off before the match region; the Jobs Board pattern now matches
  `computer society jobs board` without requiring a leading `IEEE`, since some issues' masthead text
  omits it.
- **Two more same-paper pairs caught by the same title-embedded-newline failure mode** as the Data
  Missingness pair in the sixth batch: paper ids 69417/69630, "A Provenance-Policy Based Access Control
  Model For/for Data Usage Validation in Cloud" (capitalization of "For" is the only visible title
  difference, but the underlying stored title has a `\n` mid-string same as before). Corrected via
  `mark_same_paper()`. **Three confirmed instances of this exact failure mode now** (this pair, the
  Data Missingness pair, and -- checking the pattern -- worth actually fixing at the root next time this
  file is touched for `find_duplicate_papers.py`: normalizing away embedded newlines before the tier-2
  title comparison would be a small, well-justified change at this point, unlike the fifth-batch
  "convergent standard technique" idea which stayed deliberately unautomated for being a different kind
  of judgment call.**
- **One more author-extraction-gap self-citation resolved by hand**: papers 65333/65341 (Auditing and
  Decision-Making in the Context of Industry 5.0 / Digital Banking as a Catalyst for Sustainable
  Development), tied together by an identical CRediT Author Contribution Statement for "Gabriel Silva
  Atencio" -- both papers have an empty `authors` list. Same treatment as the earlier Silva-Atencio,
  Coeckelbergh, and Zoo of Fairness Metrics clusters (manual `ai_check='yes'`, reasoning logged on the
  row). **Four confirmed instances of the author-extraction-gap pattern now**, reinforcing the sixth
  batch's note that this is worth a real fix at the root eventually.

**Running total, all seven batches today: pool now 1,231** (down from 1,367 after batch six, from ~6,262
at the start of the day), plus the separate 17,481-row same-paper correction from the tier-3 fix. 245 unit
tests passing after this batch.

**Eighth batch (2026-08-24, same day): another plain random-200, at the user's explicit prompt ("do
another pass of the 200. You seem to keep finding stuff")** -- same method as batch seven, re-run against
the pool as it stood after batch seven (1,231). Read all 200 rows.

Findings -- same four buckets as every batch today, still no new real cross-author plagiarism:

- **19 more boilerplate patterns**, each validated with a unit test before touching the real DB:
  - A broadened numbered-bibliography-entry pattern: the existing rule required a comma between surname
    and initials (`Lastname, Initial.`); a citation-padding cluster in this sample (`Odeniran OM.
    Exploring the Potential of Bambara Groundnut Flour...`, reused as a throwaway citation across at least
    two otherwise-unrelated AI papers) used the no-comma `Lastname Initial.` form instead and slipped
    through. Widened to make the comma optional, keeping the requirement that initials are immediately
    followed by a period so an ordinary numbered section heading (`3. Results We found...`) still doesn't
    misfire (regression test added for exactly that negative case).
  - IEEE's standard retraction-notice wording (`found to be in violation of IEEE's Publication
    Principles`) -- reprinted verbatim on every IEEE-retracted paper regardless of topic, so two
    *unrelated* retracted papers will always share it as boilerplate, not evidence of copying between
    each other.
  - Four more institutional-repository/technical-report-series footers, each recurring across their own
    venue: BRICS (Aarhus) technical reports, Bond University's ePublications@bond law repository, the
    Journal of Healthcare Ethics & Administration/Saint Joseph's University repository, and Université
    d'Ottawa's civil-law-section repository (French-language).
  - Three more DOE/national-laboratory boilerplate families beyond the Sandia one added in the sixth
    batch: Los Alamos National Laboratory's own DOE-contract line (different lab, different contract
    number), NREL's standard address/imprint block, and -- the highest-value one of this batch -- the
    generic DOE/national-laboratory report liability disclaimer (`prepared as an account of work
    sponsored by an agency of the United States Government...neither the Regents...nor any agency
    thereof...make any warranty`), which isn't tied to one specific lab and is likely to recur across
    every DOE-funded report in the corpus, not just the LANL/Sandia ones spotted so far.
  - Three more journal mastheads (JCMCC/CombinatorialPress, plus the AAAI Symposium Committee roster text
    that recurs across different AAAI News issues) and a generic publisher CC-BY-NC-ND-style permission
    paraphrase (`download articles and share them with others as long as they credit the authors and the
    publisher...`) seen across three unrelated pairs.
- **No same-paper duplicates or author-extraction-gap self-citations found this batch** -- the first batch
  today without either, which is itself a useful data point: today's earlier same-paper/self-citation
  finds weren't an artifact of oversampling one corner of the pool, but they also aren't inexhaustible --
  this batch's 200 rows were entirely accounted for by boilerplate and genuine-but-uninteresting overlap
  (shared results tables, thesis-vs-published-paper pairs, convergent standard-technique descriptions).

**Running total, all eight batches today: pool now 1,204** (down from 1,231 after batch seven, from
~6,262 at the start of the day), plus the separate 17,481-row same-paper correction from the tier-3 fix.
258 unit tests passing after this batch. Diminishing returns are visible batch-over-batch (80 -> 49 ->
25 -> 78 -> 30 -> 13 -> 11 -> 6+8 rows resolved by new patterns in each successive random-200/lcs-slice
batch since the sixth), consistent with the boilerplate-pattern well running dry rather than the corpus
containing more to find -- worth remembering before spending another full batch on plain random sampling
without a more targeted slice (e.g. author-name clustering for the extraction-gap self-citation pattern,
which has 4 confirmed instances and no dedicated search yet).

**Ninth batch (2026-08-24, same day): a full manual read-through of the entire pool, at the user's
explicit request ("Can you manually check the last 1200")** -- not another sample: every one of the
1,204 rows in the unclassified cross-author pool as it stood at the start of this pass, read start to
finish in ~150-row chunks, in `id` order (deterministic, so no row was skipped or re-shown). Same
per-row judgment as every batch above, applied exhaustively rather than to a subsample.

Findings, consistent with the eighth batch's observation that the boilerplate well was running dry:

- **No new real cross-author plagiarism found anywhere in the full pool.** Every one of the 1,204 rows
  was accounted for by boilerplate, a same-paper duplicate, an author-extraction-gap self-citation, or
  genuine-but-uninteresting overlap (shared results tables/citations, thesis-vs-published-paper pairs,
  convergent standard-technique descriptions, revision-vs-published-version pairs). This is the strongest
  evidence yet that this pool -- as opposed to the corpus overall -- has been exhausted of findable
  plagiarism by this method; the six batches before it were not undersampling a larger population of real
  cases still hiding in the pool.
- **~35 more boilerplate patterns** added across six sub-batches during the read-through, each validated
  with a unit test before being applied to the real DB (same discipline as every batch today). Notable
  ones beyond routine journal-masthead/institutional-repository/funding-boilerplate additions:
  - Three more DOE/national-laboratory funding-imprint variants (Argonne, a broader Sandia wording) and
    the government-report liability-disclaimer/trade-name-disclaimer patterns predicted as likely to
    recur in the eighth batch's writeup -- confirmed correct, they did recur.
  - A **Hindawi-style mass-retraction notice** ("submitted to be part of a guest-edited issue... an
    investigation by the publisher found a number of articles... with concerns including but not limited
    to compromised editorial process") -- identical wording reused across multiple unrelated retracted
    articles from the same publisher-side peer-review-fraud investigation, the same non-plagiarism
    signature as the IEEE retraction-notice pattern from the eighth batch, different publisher.
  - Two more recurring predatory/junk-citation patterns: `Pasupuleti, MK 2023...Topological and
    Software-Driven Advances in Robotics`, cited as a throwaway reference across many unrelated AI
    survey/book-chapter papers by different authors, and the broadened numbered-bibliography regex
    (comma made optional) that this pattern's exact wording had been slipping past.
  - Several journal mastheads recurring heavily enough in this corpus slice to be worth a dedicated
    pattern each: Sinergi International Journal of Islamic Studies (9+ occurrences in one read-through
    alone), IJSAT, EDUZONE, LIPIcs/Schloss Dagstuhl, APSDPR, GJETR, Stratford, ITSI Transactions,
    BioExcel/Drugs in Context.
- **Two more same-paper duplicates found and corrected via `mark_same_paper()`**:
  - Paper ids 55436/55437, "The impact of large-scale deployment of Wolbachia mosquitoes on arboviral
    disease incidence..." vs "...on dengue and other Aedes-borne diseases...", two versions of the same
    study protocol (F1000/Gates-Open-Research-style, with visible `v1`/`v2` peer-review revision markers
    in the text) with substantially revised titles -- different enough wording that tier-2's normalized-
    title match couldn't catch it, and only 42/103 = 40.8% exact-paragraph overlap, just under tier-3's
    50% content-overlap threshold. 19 `potential_dupes` rows corrected in one call -- the single largest
    per-pair correction found by hand this session. Confirmed genuinely the same protocol by hand (matching
    subtitle, matching author-empty-list extraction gap, near-identical structure) before applying, same
    as every other manual same-paper call this session; not folded into an automated threshold change,
    since one example isn't enough to justify lowering tier 3's 0.5 cutoff (the fifth batch's own
    reasoning for why thresholds need independent validation, not extrapolation).
  - Paper ids 69417/69630, "A Provenance-Policy Based Access Control Model For/for Data Usage Validation
    in Cloud" -- the third confirmed instance of the embedded-newline-defeats-tier-2-title-matching
    failure mode flagged as worth a real code fix in the eighth batch's writeup. Now backed by three
    real cases; still not fixed at the root this session (scope discipline: flagged, not yet acted on).
- **One more author-extraction-gap self-citation resolved by hand**: papers 28156/32060, "Autonomous
  Reboot: Kant, the categorical imperative..." / "Autonomous reboot: Aristotle, autonomy and the ends of
  machine ethics" -- a two-part paper series by the same author (Jeffrey White, matching affiliation
  footer: University of Twente / Okinawa Institute of Science and Technology), both papers with an empty
  `authors` list. Fifth confirmed instance of this failure mode.

**Running total, nine batches today: pool now 1,116** (down from 1,204 at the start of this batch, from
~6,262 at the start of the day), plus the separate 17,481-row same-paper correction from the tier-3 fix
found earlier today, plus a handful more same-paper pairs and 5 manual author-extraction-gap
self-citations found by hand across today's batches combined. 292 unit tests passing after this batch. Given a full,
non-sampled read-through of the entire pool turned up zero new real plagiarism, this is a reasonable
stopping point for today's boilerplate-mining work on this corpus slice -- further gains would need
either a new corpus batch, or the root-cause fixes flagged above (embedded-newline title normalization
in `find_duplicate_papers.py`; a name-similarity fallback for `same_author` when a paper's `authors` list
is empty) rather than more manual review of this same pool.

# UX 

There will need to be 2 different UX's 

1. [DONE] A cli that easily lets me see a potential dupes and lets me flag it as a duplicate or not. It should also let me look at the potential matches by author.
	I would like it to look like git diff
	I would like the confirmation to be dupe(d) for confirmed, (f)false positive (u) for unsure
	-> review_dupes.py. git --word-diff style rendering (red/green, [-...-]/{+...+} fallback without
	color), single-keypress d/f/u/s/q prompts (no Enter needed), --author/--paper filters,
	--order paper to go through one paper's candidates at a time. Reviews commit one at a time so
	quitting mid-session never loses progress. `--summary` for a non-interactive count-by-status view.

2. The output format I have not deicded on yet
 
2. plain webpage output that shows the duplicate and comparison by paper and has appropriate links. Ask questions
# Double check

Double check each part of this. Check for places where assumptions in one place cause issues in another

## Cross-process retrieval dedup race (flagged 2026-08-30, not yet fixed)

Real exposure: up to 5 `bulk_retrieve_*.py` processes ran concurrently against the same
`anthropology/state.sqlite3` this session (two `bulk_retrieve_openalex_concept.py` invocations, a wide
`bulk_retrieve_crossref.py`, `bulk_retrieve_zenodo.py`, `bulk_retrieve_core.py`/`bulk_retrieve_socarxiv.py`
at various points) -- not a hypothetical scenario, something this project's own retrieval scaling this
session actually did.

**The race, confirmed by code inspection:** every `fetch_candidate()` across these scripts is
check-then-act -- `PaperStore.get(key)` (a plain SELECT) to see if a key's already downloaded, then (if
not) the actual download (real network I/O, can take seconds), then `store.upsert(...)` to record the
result. Nothing claims the row atomically between the check and the download starting.
`ThreadLocalPaperStore`'s write lock (`retrieve_papers.py`) only serializes threads *within one process*
(`threading.Lock()` is per-process memory) -- it does nothing across the separate OS processes multiple
concurrent `bulk_retrieve_*.py` invocations actually are. Two processes can both see "not yet
downloaded" for the same key and both proceed.

There's a second, related issue on top of the timing race: `make_key()` is DOI-based when a DOI is
known (`doi:<doi>`), title-based otherwise (`title:<slug>`) -- confirmed in code, this part is *not* a
race, it's deterministic. But a source that resolves no DOI for a paper another source resolves a real
DOI for (CORE frequently returns `doi: null` even for papers with a real DOI known elsewhere) computes a
*different* key for the same logical paper, so even a clean, race-free run across multiple sources can
still produce two rows for one paper -- just not from a timing bug.

**What actually checking anthropology/state.sqlite3 found, after this session's real 5-process run:**
not what the race theory alone would predict.
- 261 downloaded rows share a normalized title with another row. But 255 of those 261 are same-source
  duplicates, not cross-process races -- overwhelmingly `bulk_retrieve_zenodo.py` harvesting **two
  different DOIs Zenodo itself minted for two versions of the same deposit** (confirmed: the paired DOI
  suffixes differ by exactly 1, e.g. `10.5281/zenodo.6424074` / `.6424073` -- Zenodo's own search index
  returns each version as a separate record, and `parse_zenodo_item()`/the harvest-time known-DOI dedup
  has no way to know two different real DOIs are the same underlying work). This is a **real, confirmed,
  already-happened bug** -- ~255 wasted duplicate downloads/extractions/embeddings -- but it's a Zenodo
  API data-modeling gap, not the cross-process race. Proper fix: dedup Zenodo candidates by
  `conceptrecid` (Zenodo's own field grouping every version of one deposit together), not just `doi`.
- Only 6 titles are duplicated *across* different sources. All 6 have genuinely different, both-real
  DOIs (e.g. a journal DOI `10.5121/ijbes.2016.3403` alongside an independent Zenodo self-archive mirror
  `10.5281/zenodo.1209543` of the same paper) -- legitimate multi-registry mirroring, not a race
  artifact, and exactly the shape `find_duplicate_papers.py` already exists to catch downstream
  regardless of how the two rows came to exist.
- Checked for the specific fingerprint an actual race would leave (a file on disk with no `.part`
  suffix that no current `file_path` row references -- i.e. two processes both finished downloading the
  same key, and the loser's completed file got orphaned when the winner's upsert overwrote the row): zero
  found. The 18 unreferenced files present were all legitimate in-flight `.part` files from downloads
  still running at the moment of the check.

**Conclusion: the race is real and unfixed, but did not provably cause damage in this session's actual
run** -- likely because the different processes were mostly working sufficiently different
keyword/source candidate spaces that same-key collisions inside the narrow check-to-download window were
rare, not because anything actually prevents one. This is "got lucky," not "safe" -- a future run with
more source overlap (e.g. two processes both searching similar keyword sets against the same field) has
a real, unquantified chance of hitting it, and nothing here would catch or report it if it did (the
losing process's file simply goes orphaned and silently wastes disk, or -- worse, if a THIRD write lands
between two racing upserts -- a `file_path` could end up pointing at a file that was never fully
written).

**Not fixed in this session** (flagged per the user's request, scope discipline: this needs a real fix,
not a workaround under time pressure). The concrete direction, not yet implemented: `PaperStore` needs an
atomic claim step before the expensive work starts -- `INSERT INTO papers (key, status) VALUES (?,
'in_progress') ON CONFLICT(key) DO NOTHING`, then checking whether the insert actually happened (SQLite's
own row-level atomicity under WAL mode holds across separate processes, not just threads, unlike
`ThreadLocalPaperStore`'s in-process lock) -- a process that loses the claim skips the candidate instead
of downloading it. Every `bulk_retrieve_*.py` script's `fetch_candidate()` would need this same change;
`retrieve_papers.py`'s own single-paper `process_paper()` likely needs it too if it's ever run
concurrently with a bulk script against the same `state.sqlite3` (not confirmed either way -- not
checked this session).

## UPDATE (2026-09-04): the race actually happened for real, and hit real damage this time

`PaperStore.claim()` was built and wired into `core`/`zenodo`/`socarxiv` later the same day (commit
`8b66d67`), but that commit's own message explicitly flagged `bulk_retrieve_theses.py` (plus
`bulk_retrieve_crossref.py`/`bulk_retrieve_openalex_concept.py`/`retrieve_papers.py`'s own
`process_paper()`) as **remaining work, not done** -- and it stayed undone for five days.

Today, running a real grey-lit harvest for the `computer-ethics` corpus, `bulk_retrieve_theses.py` ran
concurrently with the three already-fixed scripts (`core`/`zenodo`/`socarxiv`) against the same
`computer-ethics/state.sqlite3` -- exactly the scenario the original entry above predicted "a future run
with more source overlap... has a real, unquantified chance of hitting it." It hit it: **315 rows** were
found with a real `file_path` on disk (proof of a prior successful download) but a status other than
`downloaded` -- `bulk_retrieve_theses.py`'s `fetch_candidate()` had no live pre-check *or* claim at all
(worse than the plain check-then-act race the original entry described: its `known_dois` dedup is a
one-time snapshot taken at process startup, never re-checked per candidate across a 40+-minute run, and
its own final `upsert()` unconditionally overwrote whatever another process had already written for that
key in the meantime).

**Caught by noticing `state.sqlite3`'s `downloaded` count go DOWN between two checks minutes apart**
(97,461 -> 97,399) while four retrieval processes were running -- a real download count should only ever
be monotonically non-decreasing during a retrieval run; a decrease is the tell. Stopped all three
still-running processes immediately (`TaskStop`) to stop further damage before investigating further.

**Recovery**: of the 315 clobbered rows, 171 still had their PDF file present on disk (confirmed via
`Path(file_path).exists()`) -- restored those to `status='downloaded'` directly (safe: the file
genuinely exists, the row was just bookkeeping-corrupted, not actually lost). The other 144 had no file
on disk either way -- left as-is; restoring `status='downloaded'` for those would have created a false
record pointing at a nonexistent file, which is worse than an honest `error`/`no_oa` status. (Separately:
those 144 substantially overlap with the still-unexplained fully-emptied `computer-ethics/papers/`
directory found earlier the same session -- see the "moved the root corpus" work earlier -- not
something this race caused, but the two problems compounded to make the clobbered rows unrecoverable.)

**Fixed properly this time**, not worked around: added the identical pre-check + `store.claim()` pattern
`core.py` already had to `bulk_retrieve_theses.py`'s `fetch_candidate()` (which had no pre-check of any
kind before this) and to `bulk_retrieve_crossref.py`'s `fetch_candidate()` (shared by
`bulk_retrieve_author_works.py` and `bulk_retrieve_openalex_concept.py` too, so one edit covers all
three). All three edited files re-verified against the full unit test suite (510 tests) after the change.

**`retrieve_papers.py`'s own `process_paper()` remains unfixed** -- attempted the same pattern, reverted
after it broke `test_no_oa_previously_known_is_skipped_without_recheck`: unlike the bulk scripts'
`fetch_candidate()`, `process_paper()`'s skip-without-recheck branches (`doi_not_found`, then a real
`known_urls` download block, then `no_oa`) are scattered through the function rather than consolidated in
one pre-check block up front, so a single `claim()` insertion point isn't safe to add without
restructuring and re-verifying every branch against the full skip-check ordering. Real exposure is lower
here than the bulk scripts (this is the single-paper/`starting.json` flow, not normally run concurrently
with another process against the same `state.sqlite3`) but it's still a real, confirmed-by-code-reading
gap if that ever happens. Left as future work rather than rushed under time pressure a second time.

## sync_index()'s read side wasn't actually bounded -- a second, larger OOM (2026-09-03)

**What happened, attempt 1:** a `run_pipeline.py` run against the anthropology corpus (84,073 downloaded
papers, ~9.57M paragraphs newly embedded over a ~52-hour `embed_paragraphs.py` run) was silently killed by
the kernel OOM-killer partway into the very next stage, `build_dupe_candidates.py`, inside
`lsh_index.sync_index()` -- confirmed via `journalctl -k`: `Out of memory: Killed process ... (python3)
total-vm:64319836kB, anon-rss:29727724kB` (machine has 30GB RAM + 31GB swap). No traceback --
a `SIGKILL` gives the process no chance to log anything, so the only symptom was the log going silent and
the PID disappearing. First hypothesis: `run_pipeline.py` runs every stage in one long-lived Python
process (`run_with_argv()`, not a subprocess per stage), so 52 hours of `embed_paragraphs.py`
accumulation (torch/numpy allocator fragmentation, etc.) was still resident when the next stage's own
working set stacked on top. Restarted `build_dupe_candidates.py` as a fresh, standalone subprocess on that
theory.

**What happened, attempt 2 (this disproved attempt 1's theory):** the fresh subprocess hit the *exact
same step* -- `lsh_index.sync_index()` -- and climbed to 29.7GB RSS / 91.5% memory in under 22 minutes,
starting from a clean ~35MB baseline. A genuinely fresh process cannot inherit another process's memory,
so cross-stage accumulation was not the (or at least not the whole) cause. Killed it manually
(`SIGTERM`, memory recovered immediately) before a second kernel OOM-kill, rather than let it happen again.

**Actual root cause, found by checking the query, not the process:** `sync_index()`'s *write* side was
already batched (`batch_size=2000`, commit per batch -- this exact function's docstring already documents
fixing an earlier OOM this way at ~2.5M-paragraph scale). But its *read* side -- the query that decides
which paragraphs still need indexing -- was not:
```sql
SELECT p.id FROM paragraphs p
LEFT JOIN lsh_buckets b ON b.paragraph_id = p.id AND b.table_num = 0
WHERE p.embedding IS NOT NULL AND p.model = ? AND p.embedding_dim = ? AND b.paragraph_id IS NULL
```
run once, unchunked, over the whole corpus. `EXPLAIN QUERY PLAN` confirmed this *is* a good plan (`SCAN p`
+ `SEARCH b USING COVERING INDEX idx_lsh_buckets_paragraph ... LEFT-JOIN` -- no full table scan, no temp
b-tree) -- the bug isn't a bad plan, it's that at this corpus's scale (13,967,471 embedded paragraphs;
`lsh_buckets` itself over 200M rows after the first attempt's partial progress) an indexed lookup done
~14M times, plus materializing the full pending-id result into one Python list, is enormous work that the
"IDs alone are cheap even at 10M+" assumption behind the original write-side fix didn't anticipate scaling
this far past. (Diagnostic aside: plain `SELECT COUNT(*) FROM lsh_buckets` against this same DB also timed
out at 60s post-kill -- this table is genuinely large enough now that even trivial reads against it are
slow; not itself a bug, just context for why the join felt disproportionately expensive.)

**The actual fix**: chunk the read side the same way the write side already is, by paragraph id range
(`id_chunk_size`, default 200,000) -- every query in `sync_index()` now touches only one bounded slice of
`paragraphs`/`lsh_buckets` at a time, so peak memory is bounded by chunk size regardless of corpus size,
matching the discipline the write side already had. `id_chunk_size` is a parameter (not a hardcoded
constant) specifically so a unit test could exercise real multi-chunk behavior at a tiny scale
(`id_chunk_size=2` against deliberately gapped/non-contiguous ids) without needing a multi-million-row
fixture -- see `test_sync_index_id_chunking_covers_every_paragraph`. Full unit suite (508 tests) green
after the change. Confirmed safe to resume without an integrity check either time: `library.sqlite3`'s
`-wal` file was empty at both kill points (0 bytes) -- WAL mode's crash-safety guarantee (`db.py`'s own
docstring) held, nothing lost beyond the last commit.

**Lesson for next time a "this is already batched" assumption gets checked**: batching the *write* side of
a backfill and leaving the *read* side (the query that decides what's pending) unchunked is the same class
of bug in a different spot -- both need to scale with corpus size, not just the side that was obviously
doing the writing. Also: don't trust "still running" from a `pgrep -f <pattern>` check without confirming
by exact PID -- a later diagnostic command that happens to contain the same search pattern in its own argv
can make a dead process look alive.

**Update: the id-range-chunked LEFT JOIN fix above wasn't actually enough either -- confirmed by two more
real attempts.** Restarted with the chunked fix in place; RSS still climbed the same way (15.3GB at 11
minutes, same shape as the original crash), killed it proactively (`SIGTERM`, not another OOM) before it
repeated. A third, fresh subprocess (same fix) also climbed fast (8GB in 6 minutes, accelerating) and was
also killed proactively. **Root cause of the read side still being expensive even chunked by id range**:
chunking bounds how many *paragraph ids* one query considers, but the query still has to probe
`lsh_buckets` -- a table with up to `num_tables` (16) rows per paragraph -- for every single one of them.
At this corpus's scale (~14M embedded paragraphs, `lsh_buckets` over 200M rows), that's still enormous
real work per chunk regardless of how narrow the id range is; chunking made each individual query bounded
in *result size*, not in how much of the large table it had to touch to produce that result.

**The actual, confirmed-working fix**: stop checking "is this paragraph missing from `lsh_buckets`" at
all. New dedicated `lsh_indexed(paragraph_id)` table -- one row per paragraph, not per
paragraph-per-table, so structurally ~16x smaller than `lsh_buckets` regardless of corpus size. Every
pending/read check now queries `lsh_indexed` instead; every successful hash+insert batch adds to it
alongside the existing `lsh_buckets` insert. A **one-time, also-chunked migration** backfills
`lsh_indexed` from `lsh_buckets`' existing contents for a database that already had bucket rows from
before this table existed (so it doesn't waste time re-hashing ~14M already-done paragraphs).
`invalidate_paragraphs()`, `delete_orphaned_buckets()`, and `load_or_create_planes()`'s config-change
rebuild path all updated to clear/rebuild `lsh_indexed` alongside `lsh_buckets`/`lsh_scanned` -- each is a
place a paragraph's "already indexed" status needs to become false again, and missing any of them would
silently reintroduce a version of the bug (a paragraph that needs re-hashing getting skipped forever).

**Verified for real, not just by code review**: an isolated probe script (bypassing
`build_dupe_candidates.py` entirely, calling `sync_index()` directly with per-chunk logging) against the
actual anthropology corpus -- migration backfilled 13,967,471 paragraphs into `lsh_indexed` in ~330s at a
flat, unmoving **30MB RSS** the entire time (watched via a filtered background monitor polling
`/proc/<pid>/status` every 10-15s, not just spot-checked), then the main pending-check loop (now querying
the small table) found 0 pending and finished in the remaining ~56s, same flat 30MB. Total 376.5s, peak
RSS 30MB -- down from the 29.7GB kernel OOM-kill that started this investigation. Two new unit tests
(`test_sync_index_migrates_preexisting_lsh_buckets_into_lsh_indexed`,
`test_invalidate_paragraphs_clears_lsh_indexed_too`) cover the migration path and the invalidation-clears-
lsh_indexed-too requirement specifically; full suite (510 tests) green.

**Broader lesson, sharpened by getting the first fix wrong too**: "chunk the query by id range" and "make
the table you're checking against structurally smaller" are different fixes for different failure modes,
and it's possible to ship the first one, watch it still fail, and only then realize the second one was
the actual answer. The tell, in hindsight: `EXPLAIN QUERY PLAN` said the join was using a covering index
(true, and irrelevant) -- the real question that mattered was never "is this query plan good" but "how
much total data does a good plan for this query still have to touch," which scales with the *target*
table's size, not the query's own cleverness. Also worth naming plainly: two proactive kills based on "the
memory trajectory looks like the same shape as the crash that already happened" turned out to be
premature both times relative to what that specific run would have done (the second killed run, per the
follow-up probe, had likely already finished or nearly finished the real work and was killed for nothing)
-- reading a trajectory as "this matches a known bad pattern" without also checking "is meaningful new
work actually completing" is itself worth being skeptical of before pulling the trigger a third time.

**Update: the `lsh_indexed` fix was real and necessary, but not sufficient -- found the actual final
culprit by diffing an isolated reproduction against the real invocation.** A fourth real attempt (with
the `lsh_indexed` fix in place) climbed to 10.5GB in 9 minutes and was killed; a follow-up probe that
replicated `build_dupe_candidates.py`'s exact enrichment-then-`sync_index()` sequence on the same
connection stayed flat at 1.4GB for the full 371s run. That's conclusive: something *else* in the real
script -- not `sync_index()`, not the enrichment queries -- was responsible. A fifth attempt (patient
this time, no premature kill) climbed past 23GB before hitting a deliberately-set 24GB safety threshold
and was killed there.

Comparing the probe's code path against `main()`'s line-by-line found the one call the probe never
made: `delete_orphaned_candidates()`, which runs *before* enrichment, at the very top of `main()`.
`EXPLAIN QUERY PLAN` on its actual query --
```sql
DELETE FROM potential_dupes
WHERE paragraph_id_1 NOT IN (SELECT id FROM paragraphs WHERE embedding IS NOT NULL)
   OR paragraph_id_2 NOT IN (SELECT id FROM paragraphs WHERE embedding IS NOT NULL)
```
-- showed `LIST SUBQUERY 1` / `LIST SUBQUERY 2`, each a full `SCAN paragraphs`: SQLite was
materializing the *entire* `embedding IS NOT NULL` result set (millions of ids, at real corpus scale)
as an ephemeral in-memory structure, twice -- once per `NOT IN` clause -- to check membership for a
`potential_dupes` table that only had 72,930 rows to begin with. Classic `NOT IN (large subquery)`
anti-pattern. This function had always run first, on every invocation, since before this whole
investigation started; it was never touched by any of the four failed attempts, and its own cost had
been completely invisible until directly diffing against something that provably didn't have the
problem.

**Fix**: rewrote as `NOT EXISTS` with a correlated subquery per side instead of `NOT IN`. New
`EXPLAIN QUERY PLAN`: `SEARCH p USING INTEGER PRIMARY KEY (rowid=?)` -- one indexed point-lookup per
`potential_dupes` row (bounded by the candidate table's size, not the corpus's) instead of materializing
millions of ids twice. Same semantics, confirmed by the existing `delete_orphaned_candidates()` unit
tests (`test_build_dupe_candidates.py`) passing unchanged; full suite (510 tests) green.

**Why this one was so hard to find**: everything about the investigation's own instincts pointed at
`sync_index()` (that's where the log was sitting when memory was climbing, on every single attempt),
and `EXPLAIN QUERY PLAN` on *that* function's queries kept coming back clean, which was actually
correct -- `sync_index()` really was fine, on every attempt. The actual culprit ran to completion in
under a second (72,930 rows, indexed lookups even in the bad plan) and produced no log line when it
found nothing to delete (every single attempt found 0 orphaned rows), so it was invisible in every
log-based read of "what's happening right now" -- the process was always already past it and "inside"
`sync_index()`'s log line by the time memory looked concerning. Finding it required stopping trying to
read tea leaves from a climbing RSS number during a live run and instead building a clean-room
reproduction that isolated exactly which code path was and wasn't present -- worth remembering before
the next time a real bug hides behind a slow, unrelated-looking step that happens to log its "before"
message right as the actual problem's memory footprint is peaking.

**Fourth and final piece: `_discover_candidate_groups()`'s own new-paragraph query had the identical
anti-pattern one level deeper.** After the `lsh_indexed`/`NOT EXISTS` fixes above, growth persisted --
traced (via the same technique: read the actual code path between the last printed log line and the
next one, not just watch RSS) to `_discover_candidate_groups()`'s incremental-mode query:
```sql
SELECT DISTINCT b.paragraph_id FROM lsh_buckets b
LEFT JOIN lsh_scanned s ON s.paragraph_id = b.paragraph_id WHERE s.paragraph_id IS NULL
```
-- same `lsh_buckets`-not-`lsh_indexed` mistake as `sync_index()`'s original pending check, fixed the
same way (query `lsh_indexed` instead, no `DISTINCT` needed since it's already unique by primary key).

That fix alone wasn't enough either, for a genuinely different, structural reason: the *next* query in
the same function --
```sql
SELECT b.table_num, b.bucket_key, b.paragraph_id, (n.paragraph_id IS NOT NULL) AS is_new
FROM lsh_buckets b
JOIN (SELECT DISTINCT b2.table_num, b2.bucket_key FROM lsh_buckets b2
      JOIN _lsh_scan_new n2 ON n2.paragraph_id = b2.paragraph_id) touched
  ON touched.table_num = b.table_num AND touched.bucket_key = b.bucket_key
LEFT JOIN _lsh_scan_new n ON n.paragraph_id = b.paragraph_id
ORDER BY b.table_num, b.bucket_key
```
-- fetches every member of every bucket touched by *any* new paragraph, unchunked, in one `.fetchall()`.
Correctly cheap in the design's intended scenario (a small trickle of new paragraphs against an
already-large, mostly-old corpus -- few buckets touched). Tonight's actual scenario is the opposite:
~13.9-14M paragraphs all newly embedded, none yet scanned -- essentially every bucket is "touched," so
this one `fetchall()` pulls close to the whole multi-hundred-million-row `lsh_buckets` table. Confirmed
directly: one real attempt's log showed `2,183,737 buckets (1,874,798 oversized/skipped), ~626,778
projected pairs` -- Pass 1 legitimately needs to fetch and then discard ~1.87M oversized/shared
(boilerplate-shaped) buckets' full membership before Pass 2 ever starts, because the size filter is
applied in Python *after* the fetch, not in the SQL. This is a real structural gap in the incremental
algorithm at this scale, not something `--max-bucket-size` or either `lsh_indexed` fix could reach on
their own (a smaller `--max-bucket-size` reduces what Pass 2 *keeps*, not what Pass 1 *fetches* in the
first place). The proper fix (moving the size filter into SQL, e.g. a `HAVING COUNT(*) <= ?` on the
touched-bucket subquery before the membership fetch) was deliberately **not** attempted tonight --
identified, but core candidate-matching SQL is exactly the wrong place to rush a change under
time/fatigue pressure, and the workaround below is safe and already validated instead.

**The workaround actually used, tonight, successfully: `batched_first_scan.py`.** Since the
real problem is "too many paragraphs are simultaneously 'new' in one call," not a bug in the algorithm's
new/old pairing logic itself (which is correct and already handles cross-batch old/new pairs fine), the
fix doesn't need to touch `lsh_index.py` at all: mark every pending paragraph `lsh_scanned` up front
(hides all of them), then for each `BATCH_SIZE`-sized (200,000) slice, un-mark just that slice (making
only it "new," everything else "old") and call the ordinary, unmodified
`build_dupe_candidates.process_lsh_candidates_streaming()` -- which re-marks the slice `lsh_scanned` for
real when it finishes. Net marking work across the whole run is O(corpus size), not O(batches x corpus
size), since each paragraph is marked/unmarked/re-marked exactly once.

**Validated for real, end to end, on the actual anthropology corpus**: batch 1/70 (of ~13.97M pending
paragraphs) completed in ~4.3 minutes -- Pass 1 peaked at ~12.8GB RSS and *dropped back to ~4.2GB the
moment it finished* (confirmed live: the raw fetched rows are genuinely transient, freed once reduced to
the much smaller `kept_groups`, not a leak), then Pass 2 raced through in seconds (3,016 real candidates
persisted). Batch 2 started cleanly afterward. This is the first time any candidate-generation attempt
on this corpus reached real, correct output -- everything before this in tonight's investigation (five
`build_dupe_candidates.py` attempts, the two direct `sync_index()`-only probes) either OOM-crashed or was
killed first.

**One real safety gap found and fixed *in this workaround*, not the core code**: the "mark everything
up front" step means an interrupted run leaves a large majority of the corpus incorrectly marked
`lsh_scanned` without ever having been genuinely processed -- confirmed live (a kill mid-batch-1 left
9,372,714 paragraphs stuck marked-but-unprocessed; a second attempt reported only 200,000 "pending,"
which would have silently skipped the other ~9.37M). Recovery is simple and lossless (`DELETE FROM
lsh_scanned` -- `potential_dupes` inserts are `INSERT OR IGNORE` on a unique pair constraint, so
already-real candidates from completed batches are never duplicated or lost, just safely re-derived on
the next attempt) but **is required after any interruption of this specific script**, every time, for as
long as it's mid-run -- there is no safe partial-state resume, only "let it finish" or "kill, clear
`lsh_scanned`, restart from batch 1." Anyone running this again should budget for that: don't kill it
without also clearing `lsh_scanned` before the next attempt.

**Not yet done**: the real, structural fix (SQL-level bucket-size filtering in
`_discover_candidate_groups()`, so Pass 1 never fetches oversized/boilerplate bucket membership it's
just going to discard) would remove the need for this external batching workaround entirely and is the
right thing to do eventually -- properly designed and tested, not rushed. Until then, any corpus-scale
LSH candidate scan where most of the corpus is simultaneously "new" (a first-ever run, or a very large
one-time embedding batch, rather than the small-trickle-of-new-paragraphs scenario the algorithm was
originally tuned for) needs `batched_first_scan.py` (`--library-db <corpus>/library.sqlite3`), not a
direct `build_dupe_candidates.py` invocation. (Originally written and run as a hardcoded
`anthropology/batched_first_scan.py`; generalized to flags and moved to the repo root 2026-09-16,
unchanged in logic.)

## Same-year candidates are silently dropped from write_dupe_reports.py -- needs a real look

Checked why only 201 reports came out of 8,553 `ai_check='yes'` anthropology candidates. The dominant
reason isn't the `--min-count`/grouping logic (that only drops the count from 488 to 201) -- it's
upstream: **6,617 of 8,553 (77%)** have `later_paper_id IS NULL` and never reach `write_dupe_reports.py`'s
grouping at all. Checked precisely: all 6,617 have *both* years present and *equal* -- zero are missing
years. `build_dupe_candidates.py` only sets `earlier_paper_id`/`later_paper_id` when it can resolve
chronological order by year (deliberately NULL rather than guessed when ambiguous, per its own
convention) -- year-only granularity means any two papers published in the same calendar year are
structurally unresolvable this way, which turns out to be the common case, not an edge case, at this
corpus's scale.

**Not yet done**: go through a sample of the same-year-dropped 6,617 by hand (`write_dupe_reports.py
--min-count 1` surfaces the 488 distinct papers this filter is currently hiding, or query
`potential_dupes` directly for `same_paper=0 AND ai_check='yes' AND later_paper_id IS NULL`) and figure
out whether this is worth fixing, and how. Real options, none implemented: (a) if source metadata
sometimes carries a finer date (month/day) that CLAUDE.md's current schema doesn't capture, extracting
it would let same-year pairs resolve properly instead of a structural dead end; (b) a same-year pair
could still get a report using a different disambiguator (retrieval order, DOI registration date, arXiv
version number) as a fallback tiebreaker when publication year truly can't distinguish them, flagged as
lower-confidence; (c) accept the gap and treat `--min-count 1` runs as the way to see this slice by hand
periodically, rather than changing the default pipeline.

## Two real gaps found by hand-spot-checking 10 of the 201 anthropology reports (2026-09-03)

Read 10 reports spanning the full count range (n=197 down to n=2) end to end, not just skimmed. All 10
had a genuinely true `same_author=1` -- zero cross-author misattributions in the sample -- but two
distinct, real weaknesses turned up, both concentrated in the `n=2` tier (the lowest-signal end, exactly
where a closer look matters most):

1. **Author-affiliation/byline boilerplate isn't in `classify_dupes.py`'s `TEXT_PATTERNS` yet, for
   generic phrasing.** Two of the ten reports (Turin Shroud papers by Giulio Fanti; "Apathy"/"Gaius
   Musonius Rufus"/"Epictetus" by Alexander Stoliarov) matched almost entirely on the author's own
   recurring "Corresponding author: [Name], Department of..." / "ENCYCLOPEDIC SEARCH [Name] DSc in
   Philosophy, Leading Research Fellow..." byline block reused across their own paper series -- not real
   content overlap. `TEXT_PATTERNS` already has an ORCID-block entry for exactly this class of false
   positive, but not a *generic* corresponding-author/byline pattern, so these fell through to the
   same_author fallback rule instead of being cleanly filtered pre-classification. Two independent
   examples in a 10-report sample suggests this is common, not a one-off -- worth a real pattern (or a
   family in the ML classifier) for "paragraph is mostly author-name + institutional-affiliation +
   contact-info", not tied to one journal's specific template wording.
2. **The `same_author=1` auto-`yes` fallback in `classify_dupes.py` doesn't check `lcs_ratio`/
   `ngram_jaccard` before firing.** The "How humans adapt to hot climates" report (n=2) is same-author,
   cites the earlier paper (`later_cites_earlier=1`), and clears the 0.85 cosine-similarity bar -- but
   `lcs_ratio` is 0.05-0.08 and `ngram_jaccard` is 0.005-0.02, meaning almost no actual shared wording.
   This reads as a 2022 review article summarizing/citing its own group's 2017 primary study in its own
   words, not self-reuse of text, and shouldn't really be in a "suspected duplication" report at all.
   Since 198/201 of the original hand-reviewed same_author `yes` verdicts really were genuine self-reuse
   (per `classify_dupes.py`'s own docstring), the fallback rule itself is right on balance -- but it could
   plausibly be tightened with a minimum `lcs_ratio` (or `ngram_jaccard`) floor before auto-firing,
   catching cases like this one without touching the pattern-matched majority.

Not fixed -- both are two-line-shaped changes to `classify_dupes.py` (`TEXT_PATTERNS` addition;
augment the same_author fallback condition) but deliberately left for a dedicated pass with proper
before/after verification against the existing 201-report baseline, not bundled into an already very
long session's tail end.

The other three `n=2` reports checked (Platyhelminthes catalogue, Vivekananda metaphor analysis,
"Differentiation of Early Christianity from Judaism") were genuine, well-classified same-author findings
-- the last two in particular (uncited data-table reuse; uncited substantive-prose reuse across two
differently-titled papers) are exactly the kind of real signal this whole pipeline exists to surface, and
worth an actual human look rather than dismissal.

## Full-backlog review pass on computer-ethics, 2026-09-04 -- 6-subagent findings

A bigger-than-usual review pass: `computer-ethics`'s entire 31,214-item `same_author=0`/`ai_check IS
NULL` backlog was partitioned `id % 6` across 6 subagents (see `LEAD_AGENT_PLAYBOOK.md`'s Phase 3),
each applying `REVIEWING.md`'s full framework including the three non-negotiable checks. Backlog went
from 31,214 to ~23,000ish remaining (not fully cleared -- each subagent worked its partition in
`lcs_ratio`-descending order and stopped at a reasonable checkpoint, not full completion). The most
consolidation-worthy findings, several independently confirmed by 2+ subagents working different
partitions:

### Confirmed cross-author plagiarism -- likely paper-mill / fabricated-authorship ring

**5 distinct `d`-verdict pairs, all sharing one signature**: near-total verbatim text republished under
completely different (often fabricated-sounding) author names, concentrated in the `10.34218` (IAEME --
already flagged elsewhere in this project as a deliberately lower-scrutiny publisher) and `10.63282`
("Pearl Blue Research Group"-branded) DOI-prefix families:
- 72885 ("...Non-Volatile Memory...", byline "John McCarthy," a nonexistent EPFL affiliation) vs 73078
  (same content, "Muthukumaran Vaithianathan," Samsung) -- independently reached and confirmed by 3
  different subagents.
- 71045 vs 72458 (cloud-security threat-intelligence papers, Chinnam vs Pandipati) -- reached by 2
  subagents.
- 10058 vs 10198 (multi-cloud governance papers, Gaddam vs Polu) -- reached by 3 subagents.
- 24761 vs 72404 (interpretable-ML-in-healthcare -- one side a legitimate arXiv paper by established
  researchers, the other an IAEME paper credited to unrelated "M.Tech Students").
- 13890 vs 24015 (religious-diversity-in-digital-economy papers, two different obscure new journals).

**A 6th, different-shaped finding**: 16406 vs 28586 (AI-in-education survey papers, different real-
looking named authors, no citation link) share **byte-identical survey statistics** -- same frequency
tables, percentages, ANOVA table, t-test table, both claiming N=74 respondents to "the same" instrument.
This looks like fabricated/copied *data*, not just copied prose -- a different and arguably more serious
mechanism than the paper-mill text-copying cases above.

**Recommended next step** (not done this session): systematically walk the `10.34218` DOI prefix the way
`bulk_retrieve_crossref.py --doi-prefix` was designed for, rather than treating each hit as isolated --
multiple subagents independently flagged this exact prefix as a recurring source, suggesting there are
more undiscovered pairs in the same family still sitting in the backlog below the `lcs_ratio` cutoff each
subagent reached.

### Confirmed data-integrity bugs -- wrong PDF content under the wrong paper_id

Found independently by **4 different subagents**, all pointing at the same row: **paper_id 27759**
("Tuning EU equality law to algorithmic discrimination," DOI `10.1177/1023263x20982173`) -- its actual
extracted content (176 paragraphs, title through appendix) is 100% paper 481's content (Ali et al.,
"Discrimination through optimization," arXiv:1904.02095). Wrong PDF got associated with this DOI at some
point during retrieval/extraction. `p` was applied to stop the noise, but the underlying wrong-content
association in `library.sqlite3` is still wrong and needs a human decision (re-fetch the real PDF, or
drop the row).

**More of the same bug class, found once each**: paper 79114 (claims to be an "IEEE IT Professional Call
For Articles" notice, actually Steven Bellovin's column continuation from paper 11542); paper 39640
(claims "Subgroup fairness in two-sided markets," actually 100% NeurIPS 2020's "Fairness with Overlapping
Groups," paper 30970 -- confirmed via `state.sqlite3`'s `pdf_url` pointing at the wrong
`proceedings.neurips.cc` file); paper 79012 (titled "IEEE Transactions on Sustainable Computing," actually
a continuation of paper 78991's "Robot Hacking Games" column); papers 11676/11653 (both bogus-titled
"Reliability Society," actually two different real IEEE Security & Privacy magazine columns). **Pattern
within the pattern**: IEEE Security & Privacy magazine department-page DOIs seem specifically prone to
generic/wrong title metadata in this corpus (multiple subagents hit different instances independently) --
worth a targeted check of every `papers` row whose title is a generic magazine-section name ("Reliability
Society," "IEEE Computer Society," "IEEE Intelligent Systems Magazine," or literally `"0"`) rather than
waiting to find them one at a time via candidate review.

### Systemic gap: `same_author=0` is wrong far more often than "occasionally"

All 6 subagents independently confirmed this at real volume (dozens of instances total, not a handful).
Two distinct sub-causes, both needing the PDF/paragraph-text check `REVIEWING.md` already mandates,
neither fixable by the check alone since the real fix is upstream in ingestion:
1. **`paper_authors` is simply empty for one side** -- arXiv preprints and theses especially. Dominant
   shape: a PhD thesis reusing the candidate's own earlier (often co-authored) paper as a chapter.
   Dozens of named instances across both corpora this session (Jake Fawkes, Jeremy Vollen, Rishi
   Bommasani, Dan Hendrycks, Shalaleh Rismani, Elliot Creager, and many more).
2. **Name-format mismatches** -- "Firstname Lastname" vs "Lastname, Firstname," diacritic differences,
   or a name simply missing from one paper's author list while present on the other's PDF (Bommasani,
   Jan Fillies, Jakob Mökander, Anubrata Das, Bogdan Kulynych, Bahar Taskesen, Agostina Calabrese --
   partition 0 alone found 9+ of these).
3. A **third, distinct sub-pattern** worth separating from the above: books/edited-volumes cataloged as
   one `papers` row alongside their own component chapters, each with their own DOI -- Crossref's
   book-level metadata usually lists only editors, so a chapter author republishing their own chapter
   legitimately looks like `same_author=0`. 10+ named instances (Cambridge Handbook of Responsible AI +
   3 of its chapters, several SpringerBriefs volumes + their own chapters, a Frontiers Research Topic
   ebook + component articles, IRIE journal volumes, ACL Ethics-in-NLP workshop proceedings). Distinct
   from sub-causes 1/2 because the fix isn't "match names better" -- it's "recognize a book-DOI-plus-
   chapter-suffix relationship," structurally closer to what `find_duplicate_papers.py` already does for
   exact-duplicate detection than to author-matching.

**Recommendation, not done this session**: `same_author=0` should not be trusted as a hard automated
filter anywhere in this pipeline without a text-based fallback check -- the volume found here (in a
single review pass, on top of everything already caught) suggests this is costing real reviewer time
across every session that touches this backlog, not a one-off.

### Two "unsure" cases specifically flagged for a human's judgment call, not resolved by any subagent

1. **70116 vs 71455** -- two TU Darmstadt PhD dissertations (Bartsch, Schmidt), same advisor, both
   include what's almost certainly a paper the two co-authored together within a joint DFG project
   (Bartsch's own acknowledgements name Schmidt as "my former colleague, co-author, and collaborator").
   1,750 exact shingle matches, an entire empirical study section (same n=132, same PLS-SEM
   coefficients) reproduced verbatim in both. Not really a `same_author=0` gap in spirit -- it's two
   legitimate collaborators each including their joint work in separate solo dissertations -- but
   distinct enough from ordinary self-reuse that no subagent felt confident calling a verdict alone.
2. **4667 vs 23795** (McIntosh et al.'s Gemini/Q* survey vs. Zarif Bin Akhtar's "From Bard to Gemini") --
   share an extensive, distinctively-formatted "AI capability taxonomy" table verbatim across 5+
   paragraph pairs, zero author overlap, zero citation either direction. Genuinely unsure whether one
   copied the other or both lifted a common uncredited source (a blog/listicle seems plausible given the
   table's format) -- flagged rather than guessed.

### New `TEXT_PATTERNS` entries added and applied (2026-09-04)

10 patterns added to `classify_dupes.py` from this pass's findings (Academia.edu ToS, an Internet Policy
Review-style special-issue credit line, an unfilled ACM template placeholder, Elsevier's accepted-
manuscript disclaimer, a BMC/Springer retraction-notice fragment, the Fair Information Practice
Principles canonical text, F1000Research's peer-review boilerplate, two distinct ProQuest/UMI dissertation
notices, and a German AI-training opt-out clause) -- each had concrete verbatim example text reported by
a subagent. Verified against the unit suite and real example text before applying.

### Pattern candidates NOT added -- described only generically, no exact wording captured

Worth a follow-up pass specifically to capture verbatim text and turn these into real `TEXT_PATTERNS`
entries (or, for the first one, ML-classifier training data) rather than adding untested guesses:
- **The IAEME/"Pearl Blue" predatory-journal filler-paragraph family** -- not one fixed string but a
  recurring family of AI-generated-sounding filler paragraphs (a "bioenergy/biomass" paragraph, a
  "controlled experiments across three cloud platforms" paragraph, EV-charging commentary, a "desktop
  research methodology" paragraph) reused across unrelated-topic papers in the same publisher families.
  This is exactly the `train_boilerplate_family_classifier.py` use case, not a regex job -- confirmed by
  at least one concrete instance of real cross-contamination (an IAEME "Plastic Detection" paper
  containing verbatim marketing copy about an unrelated "VoxMark AI" product).
- NIST SP-series shared glossary/definition entries (individually concrete but too numerous/varied to
  turn into one pattern -- would need either a curated term list or the ML classifier).
- Frontiers journal "Generative AI statement" boilerplate; Frontiers ebook copyright statement (two
  distinct Frontiers-specific boilerplates).
- IEEE Robotics & Automation Magazine "Ethical, Legal, and Societal Issues" column closing (a recurring
  Mark Twain quote) and "Curmudgeon Corner" (*AI & Society*) column intro.
- CDC MMWR editorial board member list (recurs per-issue).
- Two distinct NIHR Journals Library boilerplates (HS&DR programme description; HTA pricing block).
- AAAI News annual financial-statement footnote.
- IEEE's generic "accepted for publication...this is the author's version" early-access notice.
- A cluster of predatory/paper-mill journal mastheads worth treating as one family for detection
  purposes even though each has different exact wording: IAEME, zesterapublications.com, Gyanshauryam/
  GISRRJ, jrps.in, Wisdom Leaf Press/ICAPSR, Inderscience's "International Journal of Generative AI in
  Business," "The USA Journals," SHISRRJ, LawFoyer/LIJDLR, Global International Journal.
- A half-dozen distinct institutional-repository notices (Sheffield Hallam SHURA, Aberystwyth, RaY/York
  St John, CentAUR/Reading, WRAP/Warwick, NRC Canada, Edinburgh Research Explorer, plus a Canadian ETD
  bilingual rights notice and an Aarhus "coversheet" template).
- Apress disclaimer + separate bulk-sales notice; InTech/IntechOpen edited-book chapter back-matter;
  JISE "Statement of Peer Review Integrity"; Deutsche Nationalbibliothek cataloging notice; ALUNA
  Publishing masthead; CAUL open-access funding line; Palgrave Macmillan trademark boilerplate; a CIFAR-
  100-style superclass label list (dataset boilerplate, not prose); STOA/EU deliverable consortium
  boilerplate; OECD territorial-dispute (Cyprus/Türkiye) footnotes; German TU Darmstadt doctoral
  declaration text; Oxford thesis "Statement of Authorship for joint/multi-authored papers" template;
  shared LLM-benchmark-example quotes (a fixed externally-sourced string, not a citation).
- **SAU Press's "Digital Ethics ..." book series** -- 8 titles in-corpus, 343 candidate pairs among just
  those 8 books -- a single-publisher templated-content issue worth a dedicated look rather than a
  generic pattern.
- **Structural, not textual**: several `papers` rows are entire books/journal issues/proceedings volumes
  stored as one record that *also* has its own constituent chapters/articles cataloged separately
  elsewhere (the same book/chapter shape as the `same_author=0` sub-cause above, but here causing
  false candidates even for same-author pairs). Suggested fix, not implemented: a `find_duplicate_papers.py`-
  style automated check for "paper A's paragraphs are a superset of paper B's," which would catch this
  independent of author metadata entirely.


# Whole-prefix IAEME/Pearl Blue sweep + 7th paper-mill case, 2026-09-05

Retrieved the remaining unretrieved works under the two known predatory-family DOI prefixes
(`10.34218` IAEME, `10.63282` Pearl Blue) via `bulk_retrieve_crossref.py --doi-prefix ... --whole-prefix`
against `computer-ethics` -- 652 new candidates found (439 IAEME + 213 Pearl Blue), 222 downloaded (rest
404/403'd -- dead links, not a retrieval-script problem). Note for next time: `--whole-prefix`'s default
`--from-date` window (10 years back) is too narrow for IAEME specifically -- its earliest registered work
is from 2010, not ~2016, so the first attempt silently missed everything 2010-2015 until caught by
checking Crossref's own earliest-work date before trusting the default window. Ran the full
`run_pipeline.py` afterward (`--skip-extract` restart needed once, after first invoking it with plain
`python3` instead of `.venv/bin/python3` -- `sentence_transformers` only lives in the venv, extract_papers.py
doesn't need it so that stage's failure wasn't visible until embed_paragraphs.py's import). Result: 2,432
new candidate pairs, 194 new `ai_check='yes'`.

## 7th confirmed paper-mill case found by manually triaging the new candidates

`build_dupe_candidates.py`'s + `classify_dupes.py`'s automated pass do NOT surface genuinely new
paper-mill pairs on their own -- `ai_check='yes'` only ever means self-reuse (`same_author=1`) or a
matched *not-a-real-match* pattern; a real cross-author duplicate is deliberately left `NULL` for a human
(see `classify_dupes.py`'s own docstring). Finding this one took manually ranking the new same_author=0
candidates by similarity and eyeballing titles -- paper_id 74264 ("Advanced Deep Learning Architectures
for Scalable and Explainable Artificial Intelligence", Lena Chandrika, 2020,
`10.63282/3050-9262.ijaidsml-v1i4p101`) vs 98326 (character-for-character identical title, Srikanth Reddy
Katta, 2025, `10.63282/3050-9262.ijaidsml-v6i1p105`) -- confirmed via `compare_two_papers.py --shingle-size
10`: 36 exact runs, longest 553 words (the entire abstract, verbatim). Written up at
`computer-ethics/flagged_cases/07-deep-learning-architectures-fabricated-authorship/`.

## Real bug found: `classify_dupes.py`'s "sequential editions" check has no floor

This exact pair was auto-classified `ai_check='no'` ("sequential editions of the same recurring
report/index series") by `classify_row()`'s `strip_trailing_date()`-based check (`classify_dupes.py:517-519`)
-- wrongly. The check only compares titles *after* attempting to strip a trailing date/edition marker,
but never verifies a date/edition marker was actually found and stripped: two titles that are identical
with *no* dating information at all (as here -- no volume/date substring anywhere in the title) pass the
same `t1_stripped.lower() == t2_stripped.lower()` test as two genuine dated editions of a real recurring
series, even though an identical title with zero edition markers on a same_author=0, high-lcs-ratio pair
is exactly the paper-mill signature this project is looking for, not a benign report series. Not fixed
yet -- the fix is straightforward (require `t1_stripped != title1` and `t2_stripped != title2`, i.e. a
date/edition token was actually present and stripped, before trusting title equality alone) but hasn't
been applied or tested against the existing 461-candidate baseline this check already correctly resolves
(todo.md's "second batch" entry above), so a re-run to confirm nothing there regresses is needed before
shipping it. Until fixed, any exact-duplicate-title-no-date-marker pair in the `ai_check='no'` bucket is
worth a manual second look -- there may be more of these than just the one found here.

# Idea: title-bucket + cross-author + expanding-n-gram-shingle as a THIRD candidate-generation method (2026-09-05)

User's proposal, prompted while waiting on the `find_duplicate_papers.py` re-run above: bucket papers
by title (not by paragraph embedding), run the exact word-shingle check (`compare_two_papers.py`'s
`find_shingle_matches()`) across every same-title-bucket pair that does NOT share an author, with some
threshold to further filter. Assessment: **yes, genuinely reasonable, and not hypothetical** -- while
writing this entry up, checking whether `find_duplicate_papers.py`'s existing title-matching tier could
be trusted surfaced a real bug (below) whose fix required doing almost exactly this proposed method by
hand, and it immediately found **14 new confirmed paper-mill duplicates** that the existing paragraph-
embedding-LSH pipeline had either missed outright or actively mislabeled as harmless. Concretely:
title-bucketing is the right complementary axis because it doesn't depend on paragraph-level cosine
similarity clearing the `build_dupe_candidates.py` threshold at all -- two of the 14 new confirmed pairs
(95235/95356, 95591/95635) had **zero** rows in `potential_dupes` before this: the embedding pipeline
never generated a candidate for them at all, at any threshold, because whatever made their embeddings
diverge (paraphrasing severity, non-adjacent paragraph reordering, whatever) was enough to miss every
LSH bucket collision, even though the actual text overlap (7 and 123 exact word-shingle runs
respectively) is completely unambiguous once you know to compare the two full documents directly.
Title-bucketing sidesteps that miss entirely since it never looks at paragraph embeddings in the first
place.

Suggested implementation shape: cheap O(n log n) grouping by `normalize_title()` (already exists in
`find_duplicate_papers.py`) or a fuzzy title-similarity bucketing (looser than exact-after-normalization
-- would also catch punctuation-only variants like case 01's "Impact on" vs "Impacton", which happen to
still normalize the same today but wouldn't necessarily under every normalization scheme); for each
bucket, pairs without a shared author (reusing `build_dupe_candidates.py`'s own same-author check, not
reinventing one) go to `find_shingle_matches()` at whatever `--shingle-size` threshold, same as
`compare_two_papers.py` already does for a manually-specified pair -- this whole idea is really "run
`compare_two_papers.py`'s existing method automatically across a title-bucketed candidate set" rather
than new matching logic. Not implemented as a standalone script yet -- the immediate need (below) was
handled by hand against a much narrower, already-known-suspicious slice (identical title AND a known
predatory DOI prefix), not a full corpus-wide title bucketing pass. A real implementation should also
decide the "threshold to further check" the user flagged as an open question -- some minimum shingle run
length and/or total-matched-word-count floor, calibrated the same way `DEFAULT_THRESHOLD` was for cosine
similarity (see `build_dupe_candidates.py`'s own threshold-history comment) rather than guessed.

## Real bug found in the process: `find_duplicate_papers.py`'s title tier blindly merges as `same_paper`

`find_pairs()`'s tier 2 (`by_norm_title`) adds every group of 2-3 papers sharing a normalized title to
the same unconditional `mark_same_paper()` path as tier 1's exact-DOI matches -- with no check that the
two papers are actually the same work, just that the title string matches. This conflates three very
different real situations under one identical-looking DB row:
1. **Genuinely the same paper, retrieved twice** (the tier's actual intended purpose) -- correct to merge.
2. **Coincidentally identical generic titles, genuinely different documents** -- e.g. three different
   people's "Review of Zuboff's *The Age of Surveillance Capitalism*" in the same journal issue (paper
   ids 11205/11232/11269, `Surveillance & Society` v17i1/2) -- correctly NOT flagged as plagiarism, but
   wrongly merged as `same_paper=1` anyway, which is at least harmless (no real content to hide) but is
   still a mislabel.
2b. Similarly: 72412/72425 (0 shingle matches, very different lengths -- 5254 vs 635 words) is almost
    certainly two unrelated short/long pieces at two IAEME sub-journals sharing a generic device-review
    title, not a real duplicate.
3. **Identical title, different named authors, substantially the same text** -- exactly this project's
   own confirmed paper-mill signature (see the 6-case, then 7-case, `flagged_cases` write-ups) -- and
   this is the dangerous one: `mark_same_paper()` nulls `same_author`/`earlier_paper_id`/etc. on every
   `potential_dupes` row for the pair and `build_dupe_candidates.py` treats a `duplicate_papers`-
   registered pair as settled "from candidate-generation time forward" per its own docstring -- so a
   real plagiarism finding gets silently and permanently removed from the human/AI review pool, with
   nothing in the data to indicate anything suspicious happened. This is exactly how case 07 (paper_id
   74264/98326, already written up at `computer-ethics/flagged_cases/07-.../WRITEUP.md`) got its own
   `potential_dupes` rows quietly re-merged as `same_paper=1` by *today's own* `find_duplicate_papers.py`
   re-run, immediately after being confirmed as a real cross-author duplicate -- caught only because the
   write-up already existed as an independent record to notice the contradiction against.

**How this was found and how big it is**: cross-referenced every `duplicate_papers` row whose reason is
`'same title after normalization...'` against the two papers' actual author lists (exact
lowercased/trimmed name-set intersection -- a crude check, see caveat below). computer-ethics: 1,885
title-tier rows total, 41 with zero shared author string; anthropology: 613 total, 131 with zero shared
author string. **Caveat, important**: most of those "mismatched" pairs are false alarms from the crude
check, not real cases -- same person written in a different format (`"Deepak P"` vs `"Deepak P."`,
`"Rebecca Salganik"` vs `"salganik, rebecca"`, `"e. n. anderson"` vs `"eugene n. anderson"`) is the
majority, consistent with the already-documented systemic same_author name-format gap elsewhere in this
file. Restricting to the subset that ALSO carries a known predatory-family DOI (`10.34218`/`10.63282`) on
at least one side narrowed this to 18 genuinely-worth-checking pairs; `compare_two_papers.py --shingle-
size 10` against all 18 confirmed **16 real** (2 weak/negative: 72412/72425 at 0 matches, 95587/95594 at
only 2 matches on ~4,500-word papers each -- both left merged as-is, not reverted). The 16 real ones
(counts = exact 10-word shingle runs found) were reverted out of `duplicate_papers`/un-merged back to
`same_paper=0, same_author=0` with `ai_check` reset to `NULL` for a fresh look under the now-fixed
`classify_dupes.py` logic: 9644/9706 (193), 72953/72991 (102), 95591/95635 (123), 74428/74489 (68),
73144/73662 (51), 73395/74447 (51), 73701/74413 (51), 74316/74391 (51), 74483/74695 (50), 72962/72967
(34), 73392/73870 (34), 73250/73741 (35), 73063/74139 (30), 95306/95316 (33), 74264/98326 (36, case 07,
re-reverted), 95235/95356 (7 -- weakest of the confirmed set, worth a second look before writing up).

**Not fixed yet** (flagged, not applied under time pressure while a dedup/reclassify pass was already
mid-flight): the right fix is almost certainly the user's proposed method above, run as a gate *before*
`mark_same_paper()` fires for the title tier specifically (tier 1 exact-DOI-match doesn't need this gate,
a real DOI collision is unambiguous) -- require either a shared author (properly name-normalized, not
exact string match -- the false-alarm rate above shows why) OR a real content-overlap check
(`find_content_overlap_pairs()`'s own exact-paragraph-match logic already in this file, or a shingle
check) before trusting title equality alone. Whichever check is added, re-run the full audit above (not
just the DOI-prefix-filtered slice) across both corpora afterward -- the 41/131 mismatched-author count
was only narrowed to 18 by restricting to a known-suspicious DOI prefix; the same conflation almost
certainly exists outside that prefix slice too, just without as strong a prior to spot-check against.

**Still open**: whether to write up all 14-15 solid new confirmed pairs as individual `flagged_cases`
entries (matching cases 01-07's convention) or a consolidated write-up given the volume and the fact that
all of them share the exact same signature already extensively documented -- asked the user rather than
assumed, given the effort difference.

## Anthropology's version of the same title-tier bug: much lower yield, sampled not exhaustively checked

Ran the same `find_duplicate_papers.py` title-tier + author-mismatch check against anthropology: 613
title-tier pairs, 131 with no shared author string. Unlike computer-ethics, anthropology has no equivalent
single dominant predatory-publisher DOI prefix to narrow the list against, so a 5-pair sample was checked
instead of a targeted subset: 34190/34193 ("Tuberculosis Acquired through Ritual Circumcision"),
59420/59421 ("Eliminating Use of the Linear No-Threshold Assumption in Medical Imaging"), 53388/55824/60978
("The future cost of cancer in South Africa..."), 38016/38017 ("Astronomia Cultural Cultural Astronomy"),
64815/64817 ("Technology of Lifelong Linguistic Education..."). All 5 came back with 1-10 shingle runs on
documents of hundreds-to-thousands of words each -- i.e. real, substantively different documents that
happen to share an exact title. Most look like genuine journal conventions (companion Point/Counterpoint
pieces, paired commentary, a themed issue's bilingual title) rather than plagiarism. Conclusion: the same
`mark_same_paper()` bug exists here too, but its practical yield looks much lower than computer-ethics's
predatory-family slice (5/5 negative here vs. 16/18 positive there) -- a full audit of anthropology's
131 pairs is not done and not obviously worth doing without a better prior (e.g. a known lower-scrutiny
publisher/DOI-prefix in this corpus, which hasn't been identified the way IAEME/Pearl Blue was for
computer-ethics). Left as-is (not reverted) since the sample found no real duplicates to un-merge.

# 6-subagent review pass on computer-ethics's cleaned-up backlog, 2026-09-05

Following the classify_dupes.py fix and find_duplicate_papers.py dedup/revert work above, dispatched 6
subagents (`id % 6` partitions) against the cleaned-up 17,560-row filtered backlog, per
`LEAD_AGENT_PLAYBOOK.md`'s Phase 3. 4 of 6 independently hit a transient "Server error mid-response" API
failure at different points in their own progress and were resumed with no data loss (each re-checked its
own DB state before continuing, since `--agent-verdict` writes commit immediately per row).

**Combined verdicts this pass**: several thousand rows resolved via `b`/`c`/`p` cascades (corpus-wide
`agent_decided_boilerplate` rose from ~18.6k to ~28.8k, `agent_decided_citation` from ~3.7k to ~6.3k) plus
~1,300 rows given a direct verdict across the 6 partitions. **6 new confirmed cases** written up (23, 29,
30 survived; 26, 27, 28 were duplicates of already-existing cases 10/17/19, caught and removed -- see
`flagged_cases/README.md`'s cross-cutting-lessons section). Backlog remains large (~3,200-3,900 unreviewed
rows per partition) -- this was a checkpoint pass, not completion, consistent with the 2026-09-04 pass.

## Findings worth acting on later

- **`same_author=0` false-negative rate looks much higher than previously documented.** Every partition
  independently found this, and partition 5 in particular reported that *every* promising-looking
  candidate it dug into (~20+) turned out to be same-author self-reuse the database's metadata had
  simply missed (PhD theses reusing the student's own earlier paper, arXiv preprint vs. published
  version, companion papers by the same research team) -- never genuine cross-author duplication. This
  isn't new (documented earlier as a "systemic gap"), but the hit rate this pass suggests it may be worth
  a structural fix (e.g. fuzzy author-name matching across a pair before ever presenting it as
  same_author=0) rather than continuing to catch it one candidate at a time by hand.
- **Only 939 of 98,542 papers' PDFs are reachable on disk in this environment** (`computer-ethics/papers/`
  is a symlink to `/home/arthur/papers`, which apparently doesn't hold the full download set every
  session runs in). Where a PDF was missing, extracted `paragraphs` text was used as a fallback
  authorship-verification source (email addresses, dedications, publication lists sometimes appear in
  the extracted text); where that also wasn't enough, candidates were left `u` rather than guessed at.
  Worth checking why the on-disk PDF set is so much smaller than the DB's paper count before the next
  review pass, since it materially limits the "verify from the actual PDF" non-negotiable check.
- **Recurring boilerplate patterns spotted but not yet added to `classify_dupes.py`'s `TEXT_PATTERNS`**
  (reported by subagents, not applied -- no exact wording captured to validate a regex against): journal
  masthead/editorial-board/"Content"-stats front matter; "About the Guest Editors" bios; author bio
  blocks reused across an author's own papers at one venue; ProQuest/UMI dissertation-scan disclaimer;
  NIST SP-series glossary/definitions appendix; OECD competition-report boilerplate intro; USGS/DOE
  government report-series boilerplate; Frontiers/Springer CC-BY and "topical collection" notices; IEEE
  accepted-manuscript disclaimer; standard AI-policy-paper author-contribution disclaimer; IGI Global
  address block; BMC "Supplementary Material"/"Acknowledgements Not applicable" placeholder block.
- **Data-quality artifact**: paper_id 11721 (DOI `10.71146/jbdpm43`, "Journal of Big Data Privacy
  Management") has 86 extracted paragraphs whose first paragraph's byline ("Muhammad Sohail Asghar")
  doesn't match the `papers` table's own title/author ("Dr. Zainab Khalid") -- the underlying PDF for
  this DOI appears to actually be a whole journal issue rather than one article, so shared back-matter
  (author bios across the issue's different papers) generates false "duplicate" candidates against
  everything else in that issue. Not fixed (a full re-extraction/re-split of this one PDF is needed).
- **Unresolved lead, not yet checked**: a cluster of candidates titled "Leveraging Artificial
  Intelligence to enhance the Quality of Life for patients with Autism Spectrum Disorder" /
  "Artificial Intelligence in Autism Spectrum Disorder..." (paper ids 30739/31675) -- identical topic, no
  extracted authors on file, different DOI prefixes than the usual IAEME/Pearl Blue family. Marked `u`
  pending byline verification (PDFs weren't available in the reviewing environment).

# 6-subagent review pass on anthropology's cleaned-up backlog, 2026-09-05

Same dispatch as computer-ethics above (`id % 6` partitions, ~4-27 candidates directly reviewed per
partition after cross-agent cascades resolved most of the backlog before each agent reached it). Result:
**zero new confirmed cases** -- consistent with the earlier prediction that anthropology's title-tier
bug mostly hits legitimate journal conventions, not a paper-mill pattern. Every candidate across all 6
partitions resolved to boilerplate, shared citations, or coincidental-term/embedding-similarity false
positives; nothing met the `d` (confirmed dupe) bar. `anthropology/flagged_cases/` remains at cases 01/02.

One good catch during this pass: an agent overrode an over-aggressive automatic citation cascade back to
`false_positive` (id 172148) after checking the actual text -- a reminder that `b`/`c` cascades apply a
verdict to every row sharing an LSH bucket, which is usually right but not guaranteed for every row in a
large bucket; worth a second look if a cascade ever looks too good to be true.

## Recurring boilerplate patterns spotted, not yet added to `classify_dupes.py`'s `TEXT_PATTERNS`

Reported by multiple subagents independently (not applied -- shared file, avoided editing it concurrently
per REVIEWING.md):
- University of Huddersfield eprints repository deposit-policy text (`eprints.hud.ac.uk`,
  "Repository Team... E.mailbox@hud.ac.uk", plus its reuse-conditions bullet list and "This version is
  available at..." stamp) -- large bucket, hit in 3 of 6 partitions.
- Queensland University of Technology (QUT) eprints repository copyright disclaimer ("This file was
  downloaded from: https://eprints.qut.edu.au/... © Consult author(s) regarding copyright matters...")
  -- the single largest recurring cluster this pass, hit in 4 of 6 partitions, hundreds of sharing
  paragraphs.
- A generic Creative Commons repository deposit-policy stamp ("...authors, title and full bibliographic
  details is credited in any copy; A hyperlink and/or URL is included for the original metadata page; and
  The content is not changed in any way.") -- single-handedly accounted for most of one partition's
  boilerplate cascade.
- Agricultural-journal "Author for correspondence : ... Email :" byline-template block, recurring across
  unrelated papers in at least one plant-pathology journal.
- Assorted per-issue journal masthead/editorial-board/"Contents" listings recurring verbatim across
  different issues of the same journal: *Cultural Intertexts*, *South African Medical Journal* (also its
  CPD-questionnaire administrative notice), and an 1850s periodical's "TRANSACTIONS OF THE COUNTY AND
  CITY OF CORK MEDICAL AND SURGICAL SOCIETY (Continued from vol. ...)" section header.

All of the above follow the same shape as the already-listed Glasgow Enlighten/White Rose entries in the
existing `TEXT_PATTERNS` history -- likely worth adding as a batch the same way, once someone has time to
extract exact wording from each and validate against a real sample before committing a regex.

# find_title_bucket_dupes.py: implemented the title-bucket idea as a real tool, 2026-09-05

Built `find_title_bucket_dupes.py`, a standalone script implementing the "title-bucket + cross-
author + expanding-n-gram-shingle" idea from the earlier entry above as reusable code, not a
one-off hand check. Reuses `find_duplicate_papers.py`'s `normalize_title()`/group-size guards,
`build_dupe_candidates.py`'s `load_paper_authors()` (author_id-based same-author check, same known
name-normalization limitation as the rest of this project), and `compare_two_papers.py`'s
`find_shingle_matches()`/`load_paper_words()`.

## Two more real bugs found while building and validating it

1. **Don't trust `find_duplicate_papers.py`'s own title-tier merges when re-checking its own
   output.** An early version consulted `build_dupe_candidates.py`'s `load_duplicate_paper_pairs()`
   (all reasons pooled) to skip already-known-duplicate pairs -- which meant it skipped every
   single one of anthropology's 613 title-tier pairs (never reverted there, unlike computer-
   ethics's 16) and came back with **zero** candidates, a false "nothing here" result. Fixed by
   only honoring the exact-DOI-match tier's skip-list (`load_doi_matched_pairs()`, filtering
   `duplicate_papers` on `reason LIKE 'same DOI%'`) -- the title tier is precisely the thing this
   script exists to re-check independently, so trusting its own prior verdict defeats the purpose.
2. **`find_shingle_matches()` produces a nonsensical count on large, highly-self-similar document
   pairs.** Running unrestricted (not human-pre-selected) surfaced a pair -- an arXiv paper vs. its
   own author's ~105K-word EPFL PhD thesis (paper_id 5256 "Vinitra Swamy" vs 75198 "swamy, vinitra"
   -- same person, name-format gap, not a real cross-author candidate at all) -- that came back
   reporting **21.8 million matched words**, ~207x either document's actual length. Every prior use
   of this function in the project was a human manually choosing two specific, moderately-sized
   papers already believed to be a real match, so this scaling/degeneracy edge case never surfaced
   before. Not fixed in `find_shingle_matches()` itself (would need investigating why the maximal-
   run extraction blows up on this input) -- `find_title_bucket_dupes.py` instead sanity-checks the
   result (total matched words landing more than 5x above the shorter paper's own length is treated
   as untrustworthy, not a real count -- calibrated against known-good pairs which land as high as
   ~1.14x legitimately, e.g. a phrase repeated inside one paper matching two spots in the other) and
   reports such pairs separately as "needs a manual look" rather than printing a bogus number.

## What running it exhaustively (no DOI-prefix restriction) actually found

**computer-ethics**: reproduces all 16 previously-hand-found IAEME/Pearl Blue cases exactly (same
run counts), confirming the earlier DOI-prefix-restricted manual investigation had already found
everything this method would find in that predatory-publisher slice. Lifting the restriction
surfaces ~1,200 more raw candidates -- but every one manually spot-checked (the top of the list by
total words, a 50-5,000-word "moderate overlap" band, and specifically the pairs with the most
unrelated-looking DOI registrars) resolved to one of a few already-well-documented benign
categories, not a new fabricated-authorship finding: arXiv-preprint-vs-published-venue pairs
(overwhelmingly the largest category), F1000Research/NIST/DOE multi-version document revisions,
old-DOI-vs-reissued-DOI journal reissues, and same-author self-archiving where the `authors` table
extraction simply failed on one side (confirmed by finding the real byline sitting in the
extracted paragraph text itself when the `authors` table came back empty -- e.g. paper_id 76870,
"RIFAT CAN ISHAKOGLU" plainly visible in paragraph 1's text despite zero rows in `paper_authors`).
**No new confirmed cases from the unrestricted computer-ethics run.**

**anthropology**: only 11 total candidates survive the >=20-matched-word floor at all (down from
613 raw title-tier pairs, most excluded by the author-overlap check or by the >=4-significant-word
title-length floor), and the strongest of the 11 has only 70 matched words -- far below even this
project's weakest confirmed real case (case 20, 90 words with unambiguous content overlap). This
exhaustively confirms the earlier 5-pair sample's conclusion: anthropology's title-tier bug mostly
surfaces legitimate journal conventions (Point/Counterpoint companion pieces, "C Naidu"'s recurring
reply articles in one journal, generic academic phrasing), not a paper-mill pattern.

**Conclusion**: for surfacing *more fabricated-authorship* cases specifically, restricting to a
known/suspected predatory-publisher DOI-prefix family (as the original hand-check did) remains the
higher-signal approach -- the fully unrestricted version is presently a better *general* "same
document under two DOIs" detector (overlapping with `find_duplicate_papers.py`'s job) than a
paper-mill detector, because the dominant noise category (legitimate multi-version/multi-repository
duplication) swamps the much rarer real signal once the predatory-prefix prior is removed. A
sharper filter -- e.g. checking whether the two documents' actual extracted bylines differ, not
just their `authors`-table rows, given how often that table is simply empty or unlinked -- would
likely improve this; not built yet.

# X-drop re-check of the weak/rejected title-tier candidates, both corpora (2026-09-06)

Following the X-drop seed-and-extend fix to `find_shingle_matches()` (fragmented runs from an
isolated word substitution now merge instead of undercounting), re-ran `compare_two_papers.py
--x-drop 3` on every specific pair this file had previously logged as "not enough shingle matches
to believe it was a dupe": computer-ethics's 2 weak/negative pairs from the 18-pair DOI-prefix-
restricted check (72412/72425, 95587/95594), and anthropology's 5-pair title-tier sample
(34190/34193, 59420/59421, 53388/55824/60978, 38016/38017, 64815/64817).

**No new confirmed plagiarism cases** -- every pair's underlying conclusion is unchanged. But X-drop's
merging did make two pre-existing data-quality issues far more visible than the old exact-only
fragment counts ever did:

- **59420/59421 (anthropology) is a PDF-bundling artifact, not a duplicate.** Under exact-only
  matching this showed as "a few short runs on short documents," easy to read as two similar-length
  independent letters. Under X-drop it collapses to one clean **669-word exact match = the entirety
  of paper 59420** (Carol Marcus's "Letters to the Editor" reply, DOI ...189860) sitting verbatim
  inside paper 59421's extracted text (nominally Siegel & Sacks's reply, DOI ...189928). Checked the
  actual PDFs (`pdftotext`, distinct files, distinct MD5s, 43KB vs 57KB): DOI ...189928's PDF is the
  journal's full "Letters to the Editor" page, containing Marcus's complete letter *followed by*
  Siegel & Sacks's own letter -- i.e. the publisher bundled both DOIs' letters onto one page/PDF, and
  whichever retrieval resolved ...189928 downloaded the bundled page rather than a per-letter
  offprint. Same failure shape as the already-documented paper_id 11721 case (CLAUDE.md/todo.md:
  a DOI resolving to a whole issue/page rather than one article) -- not fixed here, just newly
  confirmed as the same category via a different symptom (X-drop's merged 100%-of-shorter-paper
  match made it undeniable instead of merely suggestive).
- **55824/60978 (anthropology) are the same 106-word erratum notice under two DOI-formatting
  variants of the same registration** (`10.7196/samj.2016.v106.i12.12182` vs
  `10.7196/samj.2017.v106i12.12182` -- differing only in a stray `.` before `i12` and one DOI's year
  digit), confirmed via a clean 106/106-word exact match (100% both sides). This is a genuine
  `find_duplicate_papers.py` miss (same underlying document, cataloged twice) rather than a title-tier
  false-merge or a plagiarism finding -- `normalize_title()`'s exact-DOI tier never fires here because
  the DOIs aren't byte-identical, and the two-source-vs-one-erratum situation (53388, the original
  guest editorial being corrected, is legitimately a different document from either) means a
  same-paper merge needs a DOI-normalization step (case-fold, strip stray punctuation) rather than
  exact string match. Not fixed; a small, contained thing to add to `find_duplicate_papers.py`'s
  DOI-match tier if anyone wants a quick win.
- **The other 3 anthropology pairs and both computer-ethics pairs are unaffected in substance**:
  34190/34193 still resolves to one 77-word shared historical quotation (two different 1913 case
  reports citing the same "Professor Maas" line) on ~2,700/~4,700-word documents; 38016/38017 and
  64815/64817 still resolve entirely to shared journal running-header/masthead boilerplate; 72412/72425
  still finds **zero** matches of any kind even with X-drop's tolerance; 95587/95594's two former
  fragment matches merge into one 44-word run that's still pure IAEME journal-masthead metadata, not
  content. All five remain correctly un-flagged.

Lesson: X-drop is a strictly-more-informative lens on a weak candidate, not just a formatting nicety
for already-confirmed cases -- it turned two "shrug, probably nothing" pairs into clearly-identified
data-quality bugs (a PDF-bundling retrieval artifact and a duplicate-DOI cataloging gap) worth fixing
on their own terms, even though neither is the fabricated-authorship pattern this file was originally
screening for.

# Title-bucket subagent review pass, both corpora (2026-09-07)

Dispatched 8 subagents (6 on computer-ethics's top 200 title-bucket candidates by longest verbatim run,
2 on anthropology's full 74-candidate survivor list) against the fresh `find_title_bucket_dupes.py`
output generated after the 20k-paper computer-ethics retrieval round. Each subagent worked read-only
(no `library.sqlite3` writes, no `flagged_cases`/`dupe_reports_html` touches) and reported verdicts back
for centralized case-numbering, specifically to avoid the cross-subagent case-numbering collision this
file already documents happening once for real (see `LEAD_AGENT_PLAYBOOK.md`'s "Case-numbering
collisions" section).

**Results**: 274 candidates reviewed total (200 computer-ethics, 74 anthropology). **2 confirmed new
cases, both anthropology** — written up as `anthropology/flagged_cases/03-low-birth-weight-fabricated-study/`
and `04-cross-cultural-competence-patchwriting/` (see that corpus's own README for details). **Zero new
computer-ethics cases** despite reviewing its 200 strongest-signal candidates (sorted by longest exact
verbatim run) — every one turned out to be the same underlying paper under two DOI registrations (arXiv
preprint vs. published venue overwhelmingly, plus JAIR/arXiv historical mirrors, DOE report
double-registrations, conference-proceedings double-DOI issues, BMC pre-acceptance vs. final DOI), not
paper-mill fraud. This reproduces, at much larger scale, what an earlier pass already found (see this
file's "Anthropology's version of the same title-tier bug" and the unrestricted `find_title_bucket_dupes.py`
run further down): without restricting to a known predatory-publisher DOI prefix, high-overlap
identical-title candidates are dominated by legitimate multi-venue republication, not fabricated
authorship. **Lesson for next time**: sorting this candidate pool by `longest_run` descending was the
wrong prioritization — the very top of that distribution is specifically where "same document extracted
twice" concentrates (a whole-document match structurally produces the longest possible run), which is
the opposite of what's likely to be a real finding. A sharper filter for future passes: require BOTH
sides of a candidate to already have a populated `paper_authors` list (this review found the "no shared
author" signal was overwhelmingly a Crossref metadata gap on one side, not real differing authorship —
mechanically excluding empty-author-list pairs would have cut this batch's review cost by a large
fraction for near-zero lost recall) — not yet implemented, just newly understood as the actual problem
with the false-positive rate here.

## Data-quality findings surfaced along the way (not new plagiarism cases, logged for later action)

- **PARJ (app.parj.africa) boilerplate template, anthropology**: a predatory/abstract-only venue whose
  fixed marketing template ("ABSTRACT-ONLY PUBLICATION... REQUEST FULL PAPER...") alone accounted for
  24 of anthropology's 74 title-bucket candidates. Good `classify_dupes.py` `TEXT_PATTERNS` candidate
  (match on `"ABSTRACT-ONLY PUBLICATION"` or `"app.parj.africa"`) if this venue recurs in future
  retrieval batches -- not added yet.
- **Two wrong-PDF-for-DOI retrieval bugs, anthropology**: (a) paper_id 59421's DOI (nominally a Siegel &
  Sacks "Letters to the Editor" reply) actually has Carol Marcus's letter stored as its PDF/text -- not
  the bundled-multi-letter-page theory this file's earlier entry proposed, a more specific
  wrong-document-for-this-DOI bug (needs a targeted re-retrieval, not attempted). (b) paper_id 73914 (a
  DOI that resolves to a short book review per its own author metadata) actually has the entire 314-page
  book stored as its PDF/text, matching 28202 (the book's own record) at 120,957/131,081 words -- same
  category, different mechanism (OA lookup for the review resolved to the open book file instead).
- **`find_duplicate_papers.py` coverage gap, computer-ethics**: paper_ids 17761/100723 are an exact-title,
  exact-author, exact-content match (a CORE-hosted mirror of an arXiv paper) that neither the DOI tier
  (100723 has `doi=NULL`) nor the title tier caught -- worth investigating why the title tier missed it
  specifically, since title-normalization + author-overlap should have applied regardless of DOI. Not
  investigated further this pass.
- **Recurring double-DOI publisher/venue patterns, computer-ethics** (same document, same authors, two
  DOIs -- not new findings, just noted structural patterns): the ECIAIR/ICAIR conference-proceedings
  pair registers papers under two DOIs at least twice (28915/35009, 27008/27635); Zibeline International
  (`10.26480` prefix) cross-registered at least one article under two of its own journal series
  (63355/63357); IJITE and a Poznań economics journal (`10.29119`) each independently showed the same
  same-article-two-DOIs pattern once. None require action, just recorded in case the pattern recurs at
  volume.
- **Self-archiving author pattern, anthropology**: one author (Abraham Kuol Nyuon) accounts for at least
  6 of anthropology's title-bucket pairs (35344/35380, 35380/35788, 35344/35788, 28696/28725,
  28730/28744, 28709/28744) by repeatedly depositing overlapping/restructured manuscript variants to
  Zenodo under placeholder DB author fields ("Office, Editorial", "review, Author details pending") that
  only resolve to his real name via the extracted paragraph text. Same-author self-reuse, not a new
  finding, but will keep generating title-bucket candidate noise against itself.

## Computer-ethics corpus is heavily off-topic for its own name (2026-09-12)

**Diagnosis, prompted by the user noticing the confirmed cases didn't read as computer-ethics content:**
a random 40-title sample of the 118,754-paper corpus found genuinely unrelated papers (malaria-risk
mapping, tea-quality-by-hyperspectral-imaging, blood pressure response to vasodilators, archaeal virus
recognition) with zero ethics/AI/tech content at all -- even `filter_low_relevance_papers.py`'s own
deliberately generous `ON_TOPIC_TITLE_TERMS` regex (built for exactly this problem, see its own module
docstring) flags 43.6% of all titles as failing it (though that number overstates the problem somewhat,
since real fairness/ethics classics like "Gender Shades" and "Equality of Opportunity in Supervised
Learning" also fail on title vocabulary alone). Root cause, in two parts:

1. **`filter_low_relevance_papers.py` was never actually run against the recent grey-lit retrieval
   rounds** (CORE round 2, Zenodo round 3, SocArXiv round 3, theses round 3, all this past week) --
   `papers_excluded/` has exactly 1 file total, no log of the script ever running for computer-ethics.
   The 9 malaria papers found in the sample all came through these channels (mainstream DOIs --
   `10.1186`/PLOS/Nature-family -- not the paper-mill prefix families), i.e. exactly the population this
   script exists to catch, just never invoked.
2. **Most of the 24 confirmed duplicate-text cases are generic CS/systems papers with zero ethics
   content** (NVM memory tech, cloud threat intelligence, HPC architecture, hardware-software co-design,
   IoT, reinforcement learning, even a pure-math harmonic-functions paper) -- 17-18 of 24, checked
   directly by paper_id/DOI, all came from the IAEME (`10.34218`)/Pearl Blue (`10.63282`) `--whole-prefix`
   sweeps. This is NOT a bug: `--whole-prefix` was built specifically to find paper-mill duplication
   *regardless of topic* at known low-scrutiny venues (see its own help text and the "Full-corpus
   plagiarism audit" item above) -- the sweep is doing exactly what it was designed to do. But it means
   the *confirmed-case set*, read as a whole, reads as "generic-CS paper-mill fraud detection" rather
   than "computer ethics," which is a real mismatch with what the corpus/directory is named and
   presented as, even though nothing here is factually wrong.

**Fix for (1), not yet applied**: `filter_low_relevance_papers.py --since <round's start timestamp>`
should be run retroactively-as-possible against anything still `status='downloaded'`-and-unextracted from
these rounds; anything already extracted/embedded would need the "real deletion/re-extraction pass" the
script's own docstring declines to do automatically -- a bigger, more consequential decision left to the
user rather than done unilaterally.

**Fix for (2)/going forward, applied**: added `bulk_retrieve_crossref.py --issn` (exact `filter=issn:...`
match, unlike `--container-title`'s relevance-ranked free-text query which can drift onto a same-named
journal at a different publisher) and used it to target five verified precise-remit computer/AI/tech-
ethics journals end to end (whole back-catalog, no keyword restriction needed since publication in a
dedicated ethics journal is on-topic by construction): *Ethics and Information Technology* (1388-1957),
*Philosophy & Technology* (2210-5433), *Science and Engineering Ethics* (1353-3452), *AI and Ethics*
(2730-5953), *AI & SOCIETY* (0951-5666) -- ISSNs identified via real scholars' CVs/publication lists
(Floridi, Vallor, Coeckelbergh, Nissenbaum, van Wynsberghe, Wallach, Dignum, Bryson, Friedman, Deborah
G. Johnson) and independently verified against Crossref's own `/journals/<issn>` endpoint before use.
6,989 new candidate DOIs found net of what's already in the corpus (retrieval running as of this
writing). This is a precision-over-recall approach deliberately different from `--whole-prefix`'s
recall-over-precision one -- worth extending with more journals from the same scholars' CVs if this
batch's on-topic hit rate holds up once extracted.

## Idea: bulk-precompute shingle-match counts as a review triage signal (2026-09-15, not implemented)

Raised while dispatching a review pass over a large (~7,156-pair) `ai_check='yes'` backlog: would
restricting review to pairs with a minimum exact word-shingle match count (`compare_two_papers.py`'s
`find_shingle_matches()`, run across each pair's *complete* documents, not just the one paragraph that
originally surfaced the candidate) be a useful optimization?

**Partial yes, with an important limit.** A pair with zero shingle overlap despite a high embedding-
similarity paragraph match is a cheap, mechanical way to catch a real failure mode this project's own
docs already name -- cosine similarity "fooled by two paragraphs that are merely on the same narrow
topic" -- so pre-filtering those out (or just deprioritizing them) would save real review time, and it's
effectively automating the exact verification step REVIEWING.md's "Writing up a confirmed case" section
already says to run before calling anything confirmed, just moved earlier and applied in bulk instead of
only on already-promising finalists. It'd also work as a ranking signal: every real finding so far has
had a substantial run (9 to 254 shingle-match runs, hundreds to thousands of matched words), so sorting
a backlog by shingle-match count/total words and reviewing the top of that list first should front-load
the highest-yield candidates.

**What it does NOT solve**: separating genuine misconduct from boilerplate/citation. Boilerplate *is*
verbatim reused text (a repeated disclaimer, a standard methodology paragraph, a shared citation string)
so it produces plenty of exact shingle matches too -- a `>=N shingles` threshold would let nearly all of
it through just as easily as a real case. The last hand-reviewed batch was ~97% boilerplate/citation
(624 of 640 pairs) despite already being pre-filtered through `classify_dupes.py`'s `ai_check='yes'`
pattern-matching; a shingle-count filter on top wouldn't have shrunk that bulk. `classify_dupes.py`'s
`TEXT_PATTERNS` coverage (or the reviewer's own judgment) is still the actual lever for that half of the
problem.

**Not yet built**: a script that runs the shingle scan across every `potential_dupes` pair in a review
batch upfront, stores the count/total-words alongside the row (or in a side table), and lets a review
pass sort/filter on it before touching REVIEWING.md's judgment questions at all. Would need to size the
real cost of running `find_shingle_matches()` (loads and scans both complete documents) across
thousands of pairs at once before deciding whether it's cheap enough to run unconditionally versus only
on a pre-narrowed set.

## Author homepage/CV retrieval pilot (2026-09-18)

`bulk_retrieve_author_homepages.py`, run on the top 100 authors (by paper count) of *Ethics and
Information Technology*, *Philosophy & Technology*, *AI and Ethics* and *AI & Society* (Science and
Engineering Ethics deliberately left out -- its top authors are research-ethics, not computer ethics). No
flagged-case authors were in that top 100.

Funnel: 100 -> 78 with an ORCID -> 37 with a homepage on ORCID/Wikidata (Wikidata added 4) -> 28 with a
missing work matched on their site -> 9 with a verified download. 91 verified PDFs total; 64 of them
OpenAlex had no OA location for (i.e. unreachable by every other retrieval path here). Every one
hand-checked for its title at the top of page 1 -- all correct. The title-on-first-2-pages check rejected
225 wrong files (a neighbouring list entry's PDF, slides under the same title).

Concentrated: Takayuki Kanda 51 (lab page with direct links; HRI robotics, off-topic -- held back in
`computer-ethics/homepage_downloads/held_offtopic/`, not imported), Coeckelbergh 18, Marin 6, Capurro 5,
Lütge 3, Steen 3, Veluwenkamp 2, Gordon 2, Kempt 1. The other 40 were imported into computer-ethics.

Why the yield is low: 63/100 have no findable homepage (many senior specialists have no ORCID at all);
many "homepages" are Pure/university profile pages without PDF links, or JavaScript-rendered (the crawler
sees nothing -- van den Hoven, Pagallo, Shin); most matched links point to publishers (403 / HTML, 1,324
"not a PDF"). Floridi: 745 missing works, 0 got. Roughly one new paper per author tried -- a modest
supplement, carried by the few authors who self-archive on their own sites.

Bug hit and fixed during import: the manifest wrote `authors` as a JSON list, but `state.sqlite3` stores
it as a JSON *string* (`import_manual_downloads.py` crashed binding a list).


**Review of the pilot's candidates (2026-09-19):** all 121 open candidates (cross-paper, cross-author,
`ai_check IS NULL`) involving the 40 imported papers were reviewed. No real duplication. 86 were
publisher/repository boilerplate (Emerald, Taylor & Francis, Open University ORO, MDPI, AOSIS, Wiley,
CRediT) -- applied row-by-row, NOT via the `b` bucket cascade, since an agent cascade overwrites prior
human verdicts in the same bucket; 7 false positives (same topic, different wording); 27 were "Digital
Slot Machines" vs. its own published Correction, which reprints the whole article (`p`, 54 rows between
that pair corrected); 1 pair was Lavinia Marin on both sides, missed by `same_author` because one record
has "Marin, Lavinia" and the other "Lavinia Marin" (`a`). That name-order miss is a real, general
`same_author` gap worth fixing at the source. The 7 boilerplate families were added to
`classify_dupes.py`'s `TEXT_PATTERNS` (none were covered before, not even MDPI's license footer);
re-running `classify_dupes.py` marked 232 more corpus-wide rows `ai_check='no'`.
`embed_paragraphs.py --reclassify-existing-boilerplate` has NOT been run for them yet.

**`same_author` name-order fix (2026-09-19):** `build_dupe_candidates.py` now computes `same_author`
from normalized author names (`load_paper_author_keys()`, reusing `resolve_author_openalex_ids.py`'s
`normalize_author_name()`) instead of `author_id`, and `backfill_same_author()` flips existing rows
0 -> 1 on every run (never 1 -> 0, never touches status). Also fixed `normalize_author_name()` dropping
letters NFKD doesn't decompose ("Søren" -> "sren"). Exact match only after normalization -- no initials
or fuzzy matching, since a wrong `same_author=1` hides a real cross-author case. Applied: 584 rows on
computer-ethics (276 paper pairs; 15-pair random sample all genuinely the same person; none were
`confirmed`/`agent_decided_dupe`), 97 on anthropology; `classify_dupes.py` then marked 441 / 78 of them
`ai_check='yes'` (self-reuse). Known remaining gap: `build_dupe_candidates.py`'s upsert still overwrites
`same_author` on a re-scanned pair, so a manual `(a)` correction for a variant this normalization can't
catch (initials, transliterations) could be reverted by a `--full-rescan`.
