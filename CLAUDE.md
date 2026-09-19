# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A pipeline that retrieves open-access academic PDFs, extracts their structured content, embeds their
paragraphs, and finds near-duplicate/overlapping text across the resulting library ("dupefinder"). It's
a set of standalone CLI scripts chained by file/DB handoffs, not a package or service — there is no
build step or app entrypoint. There is a back-test suite (`tests/run_tests.py`) that validates the
pipeline against known-outcome cases, and a unit test suite (`tests/run_unit_tests.py`) covering pure
logic and mocked-network paths in `retrieve_papers.py`, `text_overlap.py`, and `lsh_index.py`.

## Setup

```bash
sudo apt install -y python3-venv python3-pip   # one-time; needs a real terminal, not this harness (no interactive sudo)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # requests, sentence-transformers, numpy (pulls in torch)
```

Every subsequent command in this doc (`python3 retrieve_papers.py ...`, `python3 run_pipeline.py ...`,
etc.) assumes `.venv` is activated in that shell — run `source .venv/bin/activate` first if it's a fresh
shell, or invoke `.venv/bin/python3 <script>.py` directly instead if you'd rather not activate.

`extract_papers.py` also shells out to `pdftotext` (poppler-utils), which must be installed via the
system package manager (already present on this machine at `/usr/bin/pdftotext`) — it is not pip-installable.

**If `pip install` (system-wide, no venv) fails with `externally-managed-environment`:** this is Ubuntu
24.04's PEP 668 guard — apt considers itself the owner of the system Python once `python3-pip` is
installed via apt, and refuses a plain `pip install`/`pip install --user` on top of it. The venv above
sidesteps this entirely (a venv's `pip` was never "externally managed" to begin with) and is the
recommended fix. The alternative, if a venv genuinely isn't an option, is `pip install --user
--break-system-packages -r requirements.txt` — works, but fights the guard rather than avoiding it, so
prefer the venv.

(Earlier revisions of this doc said "there is no `python3-venv` package on this machine" — that was
descriptive of the machine's state at the time (not yet installed), not a real unavailability: `apt
install python3-venv` works fine. If you hit that claim anywhere else, it's stale.)

## The pipeline

Each stage is a separate script, run in order, each reading the previous stage's output:

```
starting.json  --[retrieve_papers.py]-->  papers/*.pdf + state.sqlite3
                                                    |
                                          [extract_papers.py]
                                                    |
                                                    v
                          library.sqlite3 (papers/authors/paper_authors/citations)
                                          + paragraphs.jsonl
                                                    |
                                          [embed_paragraphs.py]
                                                    |
                                                    v
                              library.sqlite3 (paragraphs: text + embedding)
                                                    |
                                          [lsh_index.py: sync_index()]
                                       (every new/changed embedding hashed
                                          into library.sqlite3 lsh_buckets
                                          as soon as it's stored -- no
                                          separate step, no waiting for the
                                          rest of the corpus)
                                                    |
                                    +---------------+---------------+
                                    |                               |
                          [find_duplicates.py]          [build_dupe_candidates.py]
                          (brute-force N x N,             (LSH candidate lookup via
                           exhaustive/reference)            lsh_index.scan_candidate_pairs(),
                                    |                        --brute-force to opt out)
                                    v                               v
                    ranked near-duplicate pairs        library.sqlite3 (potential_dupes +
                    (console report, nothing saved)     potential_dupe_authors, review-ready)
                                                                        |
                                                              [review_dupes.py]
                                                            (interactive CLI: git
                                                             --word-diff-style pairs,
                                                             d/f/u/s/q per candidate)
```

```bash
python3 retrieve_papers.py --email you@example.com          # starting.json -> papers/, state.sqlite3
python3 extract_papers.py                                    # -> library.sqlite3 metadata + paragraphs.jsonl
python3 embed_paragraphs.py                                  # -> library.sqlite3 paragraphs table (text+vector) + lsh_buckets
python3 find_duplicates.py [--cross-paper-only] [--threshold 0.85]     # ad-hoc console report, brute force
python3 build_dupe_candidates.py [--threshold 0.85] [--max-bucket-size N] [--brute-force]   # persists candidates for review
python3 review_dupes.py [--author NAME] [--paper TITLE] [--order paper] [--summary]         # interactive review
```

`find_duplicates.py` and `build_dupe_candidates.py` are two different consumers of the same
`paragraphs` table, not sequential pipeline stages — run whichever fits (or both): the first for a quick
exploratory look (always exhaustive brute force), the second to persist a stateful table `review_dupes.py`
reads from and writes back to (candidates sourced from the LSH index by default; see
`build_dupe_candidates.py`'s docstring and `lsh_index.py`).

All six scripts are independently re-runnable and resumable/idempotent — each keeps enough state (in
`state.sqlite3` or `library.sqlite3`) to skip work already done, so re-running the pipeline after adding
new entries to `starting.json` only processes what's new.

### `retrieve_papers.py` — fetch OA PDFs

Input: `starting.json`, a JSON object or array of `{title, authors, year, doi?}`.

Per paper: resolve a DOI via Crossref (bibliographic title/author search, accepted only above
`--title-match-threshold`, default 0.82) if one isn't already known, look up the DOI in Unpaywall for an
OA location, download the PDF if found. All state (resolved DOI, OA status, file path, per-paper
success/failure) is kept in `state.sqlite3` (table `papers`, keyed by `doi:<doi>` or, when no DOI is
known, `title:<slugified title+year+authors>` — see `make_key()`), so re-runs skip papers already
downloaded or previously confirmed to have no DOI match / no OA copy (`--recheck` forces a re-check).
`--email` is required (Crossref/Unpaywall's polite-pool terms require a contact address).

**`query_openalex_batch()`**: a second, batched way to resolve a DOI's OA location, used by the bulk
retrieval scripts below (not `retrieve_papers.py`'s own single-paper `main()` loop, which has no batch
to build). Unpaywall has no batch endpoint — one HTTP round-trip per DOI at its ~1 req/sec polite-pool
pace, which is fine one paper at a time but is the actual bottleneck at bulk-retrieval scale (tens of
thousands of candidates). OpenAlex ingests Unpaywall's own OA data plus more and supports
`filter=doi:a|b|c` — up to `OPENALEX_BATCH_SIZE` (50) DOIs resolved in one request. Bulk scripts call
this first for the whole candidate batch; any DOI it doesn't resolve (not found, or found but no OA
location) falls back to the original per-DOI `query_unpaywall()`, unchanged — this is a speed change,
not a coverage tradeoff. A failed/erroring OpenAlex batch degrades to "nothing resolved this batch"
(logged, not raised) rather than aborting a run. A 429 is handled distinctly (`RateLimited`, see below)
rather than folded into that generic degrade path.

**`OPENALEX_API_KEY` env var** (not a CLI flag — a flag would sit in plain sight in `ps aux` for the
life of the process): if set, sent as the `api_key` query param OpenAlex's docs specify. Confirmed
necessary for real, not just nice-to-have — anonymous/keyless OpenAlex traffic from this project's own
bulk retrieval hit a rate limit (`Retry-After: 71649`, ~20 hours) after a few thousand cumulative
requests; a free key (openalex.org/settings/api, ~30 seconds to create) raises the effective limit well
past that. Without one set, requests still go through anonymously (unchanged, just rate-limited).

**`RateLimited`** (`RetrievalError` subclass): what a 429 becomes when a caller passes `max_retries=0`
to `http_get()` — raised immediately, no sleep at all, instead of the normal retry-with-backoff path.
`query_openalex_batch()` always calls with `max_retries=0` for exactly this reason (see its own
docstring for the full incident writeup); both bulk retrieval scripts catch `RateLimited` around their
OpenAlex batch loop and stop calling OpenAlex entirely for the rest of that run on the first 429,
falling back to per-DOI Unpaywall for everything remaining. `_retry_after_delay()` also caps whatever a
server's `Retry-After` header asks for at `MAX_RETRY_AFTER_DELAY` (60s) regardless — a single
`http_get()` call blocking for most of a day because a server asked it to is never the right behavior,
for any caller, key or no key.

**Known gap:** Crossref's bibliographic search does not reliably resolve arXiv/PMLR preprint DOIs, so
papers whose canonical open copy lives there often come back `doi_not_found` or get matched to the
paywalled venue-of-record DOI (`no_oa`) even though a free copy exists elsewhere. There is no automatic
fallback for this — misses have so far been re-fetched by hand (verifying the PDF with `pdfinfo`/
`pdftotext` before placing it in `papers/`) and the corresponding `state.sqlite3` row updated to
`status='downloaded'` with the real `file_path`/`pdf_url` set, matching the same key `make_key()` would
compute from `starting.json` (a `doi:` key is dead weight if the input entry has no `"doi"` field, since
`make_key()` never sees the resolved DOI — always check which key form applies before hand-editing state).

**Landing-page PDF discovery:** whenever a candidate URL (a known `oa_url`/`oa_alt_urls` entry, or
Unpaywall's `best_oa_location`) turns out to be an HTML landing page rather than a direct PDF,
`download_with_landing_page_fallback()` looks for a `<meta name="citation_pdf_url" content="...">` tag
before giving up on it — a long-established convention (Highwire Press-derived, emitted by
Google Scholar-indexed sites generally, and specifically by most DSpace/EPrints/Bepress Digital Commons
institutional repositories) that exists exactly to tell an automated fetcher where the real PDF is.
Added after finding theses/dissertations succeed at less than half the corpus-wide download rate (20%
vs. ~47%): OpenAlex/Unpaywall frequently only have a repository landing page on file for these, not a
direct PDF link, even though one exists one click away. Confirmed against real previously-failed
retrieval attempts in this project's own `state.sqlite3` (McGill, ANU repositories) — not a guaranteed
rescue (plenty of landing pages don't emit the tag), just another free shot before falling through to
whatever the normal flow would have done anyway.

### `extract_papers.py` — metadata + paragraph extraction

Reads every `status='downloaded'` row out of `state.sqlite3` (not `starting.json` directly). For each PDF:
runs `pdftotext <path> -`, takes title/authors/year/doi from `state.sqlite3` (Crossref-verified — more
reliable than re-parsing noisy PDF header text), locates the References/Bibliography section by heading
match (searched from the end of the document, to avoid a false hit on an in-body mention) and splits it
into raw citation strings, and splits the remaining body into paragraphs (dehyphenates wrapped words,
merges spurious blank-line fragments from `pdftotext`'s page/column reflow, filters out headers/short
fragments).

Writes:
- **`library.sqlite3`**: normalized tables `papers`, `authors`, `paper_authors` (join table with
  `author_order`), `citations` (raw citation strings only — not structured into author/title/venue).
  Paragraph *text* deliberately does not live here yet.
- **`paragraphs.jsonl`**: one JSON object per paragraph (`file_path`, `para_index`, `text`), matched back
  to a paper via `file_path`. This is the paragraph text hand-off — `embed_paragraphs.py` is what
  actually stores paragraph text into a DB table (paired with its embedding).

Exact-repeat paragraphs within the same document (e.g. a running header or figure caption reprinted on
multiple pages) are dropped before writing `paragraphs.jsonl` at all — they're never stored, embedded, or
flagged as a same-paper "dupe" of themselves (`dedupe_paragraphs()`; only *exact* text matches are
dropped, near-duplicates like paraphrased captions are left for `find_duplicates.py`/
`build_dupe_candidates.py` to catch).

**Known heuristic gaps** (regex-based, not a real bibliographic/layout parser):
- Numbered citation styles (`[1]`, `1.`) split cleanly; hanging-indent/author-year styles without
  numbering (e.g. PMLR reference lists) often fail to split per-entry and fall back to one or two large blobs.
- Footnote-style references (common in law reviews, e.g. *Big Data's Disparate Impact*) have no
  end-of-document References heading at all, so citations end up as 0 and the footnote text is absorbed
  into body paragraphs instead.
- Two-column/justified PDFs can lose inter-word spaces (`humanbiasesfrom`) — an inherent `pdftotext`
  limitation on that layout that isn't patched.

### `embed_paragraphs.py` — embed + persist paragraph text

Reads `paragraphs.jsonl`, maps each paragraph's `file_path` to a `paper_id` via `library.sqlite3`'s
`papers` table, embeds text in batches with a local `sentence-transformers` model (default
`all-MiniLM-L6-v2`, 384-dim, `normalize_embeddings=True` so vectors are unit-length and cosine similarity
reduces to a dot product), and upserts into `library.sqlite3`'s `paragraphs` table
(`paper_id, para_index, text, embedding BLOB, embedding_dim, model`, unique on `(paper_id, para_index)`).
Embeddings are stored as raw `float32` bytes (`ndarray.tobytes()`); reconstruct with
`np.frombuffer(blob, dtype=np.float32)`.

Idempotent: paragraphs that already have a stored embedding are skipped on re-run — matched by
`(paper_id, para_index)` **and** stored text, not index alone, so a paragraph whose text changed at an
existing index (e.g. a later paragraph sliding into an earlier one's slot after `extract_papers.py` drops
a same-document duplicate ahead of it) is detected and re-embedded rather than silently left stale.
Rows whose `(paper_id, para_index)` no longer appears in `paragraphs.jsonl` at all are deleted first
(`reconcile_stale_paragraphs()`). `--recompute` re-embeds everything regardless.

`paragraphs.jsonl` is a transient staging area, not a permanent archive: once a paper's paragraphs are
all durably embedded, this script prunes that paper's lines back out of the file
(`prune_embedded_paragraphs()`, on by default — `--no-prune` to keep the old grows-forever behavior).
Only ever prunes a paper's lines as a complete group, never partially, and always preserves a line it
can't resolve to a known `paper_id` — both are load-bearing, not just cautious (see todo.md's
"paragraphs.jsonl needs splitting" for the two real bugs a partial version of this hit on this
project's own corpus). Before this, the file only ever grew (extract_papers.py appends, nothing ever
removed anything) — 7.6M lines / 4.96GB before the fix, 262KB after pruning the same corpus's backlog.

Paragraphs scoring below `--min-english-score` (default 0.03) on `review_dupes.py`'s `english_score()`
are skipped — stored with `embedding` left `NULL` and `model='skipped-non-english'` rather than a real
vector, so they're durably marked "considered" (not re-scored every run) without ever entering LSH
indexing or candidate generation. `all-MiniLM-L6-v2` is English-tuned and produces high-cosine-
similarity noise, not signal, on other languages — added after a corpus audit found 249 of 341 hard
cross-paper candidates were exactly this (todo.md's "Full-corpus plagiarism audit"). `--min-english-
score 0` disables the filter entirely.

Every paragraph that's newly embedded or re-embedded this run is also hashed into `lsh_index.py`'s
persisted candidate index right after it's stored (`lsh_index.sync_index()`), and any of its stale bucket
rows from a previous embedding are cleared first (`lsh_index.invalidate_paragraphs()`) — see `lsh_index.py`
below.

### `lsh_index.py` — persisted candidate lookup (LSH)

Not a standalone CLI script — a module `embed_paragraphs.py` and `build_dupe_candidates.py` both import.
Maintains a locality-sensitive hashing (LSH) index over `paragraphs.embedding` in `library.sqlite3`: the
"hashtable where the key is the vector (or anything close enough to it) and the value is a list of
paragraph ids" that lets `build_dupe_candidates.py` find near-duplicate candidates without a brute-force
`N x N` compare and without waiting for the whole corpus to be embedded first. Full design, the recall
numbers behind the defaults, and the known gaps are written up in `todo.md`'s "Candidate index design
(LSH)".

Mechanism: random-hyperplane LSH (SimHash). `num_tables` (default 16) independent sets of `bits_per_table`
(default 12) random hyperplanes each hash a paragraph's embedding into a `bits_per_table`-bit code per
table; two paragraphs are candidates if they share a code in *any* table. The hyperplanes themselves
(`lsh_config`, one row, regenerated and everything downstream invalidated if the model/dim/table
config changes) and the resulting buckets (`lsh_buckets(table_num, bucket_key, paragraph_id)`) are
persisted in `library.sqlite3`. A shared bucket is a candidate, not a verdict — callers still compute
exact cosine similarity before treating a pair as a real match.

`sync_index()` bulk-hashes and inserts every embedded-but-not-yet-bucketed paragraph (idempotent, cheap
no-op in the common case). `scan_candidate_pairs()` is **incremental by default**: `lsh_scanned` tracks
which paragraphs have already had their candidates derived, and a scan only touches buckets containing at
least one paragraph that hasn't — re-deriving the whole corpus's candidates on every run turned out to be
the actual mechanism behind repeated hour-long stalls once the corpus got large (todo.md's post-mortem 2).
`--full-rescan` (`build_dupe_candidates.py`'s flag, threaded through to this function) opts back into the
old whole-table behavior — needed after lowering `--threshold`, since incremental scanning won't
re-examine a pair that was already co-bucketed and rejected under a previous, higher threshold. Buckets
bigger than `max_bucket_size` (default 30) are skipped per table (real bucket occupancy is skewed: some
boilerplate paragraphs are reused verbatim across hundreds of unrelated papers), which is the one place
approximate recall can silently drop a match (an exact-duplicate cluster larger than `max_bucket_size`
hashes identically in every table and can be missed entirely — `find_duplicates.py` or
`build_dupe_candidates.py --brute-force` is the exhaustive
fallback).

### `find_duplicates.py` — similarity search

Loads every embedded paragraph, stacks the embeddings into one matrix, and computes the full pairwise
cosine similarity as a single `matrix @ matrix.T` (fine at this corpus's scale — would need an
approximate-nearest-neighbor index instead of brute force well before this stops being cheap). Reports
pairs above `--threshold` (default 0.92), sorted most-similar first, tagged `SAME PAPER` or `CROSS PAPER`.

Rough similarity bands observed on this corpus: **~0.95–1.00** near-verbatim (identical/lightly-edited
text — e.g. repeated figure captions or running headers within one paper); **~0.85–0.95** same
claim/sentence reworded, or heavy boilerplate/abstract overlap between papers; **~0.60–0.85** same
topic, different content. `--cross-paper-only` restricts to pairs from different papers (useful for
spotting shared lineage/reused phrasing between papers rather than a paper's internal repetition).

### `build_dupe_candidates.py` — persisted, review-ready candidates

Same similarity search as `find_duplicates.py` (imports and reuses its `load_paragraphs`/`to_matrix`/
`find_pairs`), but instead of printing, persists every pair above `--threshold` (default 0.85 -- lowered
from 0.90 after the project's own best-documented ground-truth case, Saxby/Taro, turned out to sit at
0.877 and was missed entirely; see build_dupe_candidates.py's `DEFAULT_THRESHOLD` comment) into
`library.sqlite3`'s `potential_dupes` table, enriched with filterable flags:
- `same_author` — the two papers share an author (NULL, not 0, for same-paper pairs, where it would be
  vacuously true and therefore meaningless). Compared on normalized author *names* (`load_paper_author_keys()`:
  "Last, First" flipped, accents/case/punctuation stripped), not `author_id` -- one person routinely has
  two `authors` rows under different name orders; `backfill_same_author()` corrects older rows every run.
- `earlier_paper_id`/`later_paper_id` — resolved by `year`; both NULL when years are equal/missing rather
  than guessed. NULL for same-paper pairs too.
- `later_cites_earlier` — heuristic (significant-word overlap, `CITATION_WORD_OVERLAP = 0.7`, similar in
  spirit to `retrieve_papers.py`'s Crossref title matching) for whether the later paper's `citations` rows
  reference the earlier paper. NULL whenever chronology itself is NULL.
- `status` — `unreviewed` (default) / `confirmed` / `false_positive` / `unsure` / `boilerplate` / `citation`.
  `review_dupes.py` is the CLI that reads from and writes back to this column (todo.md's "UX" section;
  the second UX it asks for, a webpage, doesn't exist yet).

Before scoring, consults `find_duplicate_papers.py`'s `duplicate_papers` table
(`load_duplicate_paper_pairs()`) — a pair already registered there (the same paper retrieved/cataloged
twice, not a real cross-paper match at all) is treated as `same_paper=1` from candidate-generation time
forward, the same correction `review_dupes.py`'s `(p)` key applies by hand after the fact, just applied
automatically before a human ever sees the pair.

### `find_duplicate_papers.py` — dedup gap: same paper retrieved twice

Standalone maintenance script (not part of the deterministic `run_pipeline.py` chain — run it whenever,
safe/idempotent). Finds `papers` rows that are the same underlying work cataloged under two different
ids — exact DOI match, or exact title match after HTML-unescaping/lowercasing/punctuation-stripping —
and registers each pair in a `duplicate_papers` table (`paper_id_1 < paper_id_2, reason, detected_at`),
then applies the same `same_paper=1` correction `review_dupes.py`'s `mark_same_paper()` applies by hand
to every existing `potential_dupes` row between that pair (status/reviewed_at left untouched, same
convention). `build_dupe_candidates.py` consults the resulting table going forward (see above) so a
known duplicate pair doesn't keep generating fresh "cross-paper" noise every time new papers are added.

Deliberately does NOT do fuzzy/semantic title matching (typo tolerance, book-vs-chapter DOI-suffix
detection, reworded preprint-vs-published titles) — that needs real per-case judgment and is what
`review_dupes.py`'s `(p)` key is for; this script only automates the tier where "same paper" is
unambiguous from the data alone. Both detection tiers cap the matching group at 3 members and require
the normalized title to be at least 4 significant words — necessary, not just cautious: an unbounded
first version found 3,563 "duplicate" pairs on this project's real 70,041-paper corpus, almost all
wrong (generic recurring publisher furniture like "Front Cover"/"Masthead" collapsing hundreds of
genuinely different magazine issues into one group; see todo.md's "Full-corpus plagiarism audit" for
the full post-mortem). The capped version found 1,255, spot-checked legitimate.

### `review_dupes.py` — interactive CLI review

Reads `potential_dupes` (whatever's there — no pipeline stage needs to have finished) and walks through
candidates one at a time: a `git diff --word-diff`-styled rendering of the two paragraph texts (red/green
word-level diff, `[-...-]`/`{+...+}` fallback without color), single-keypress `(d)upe /(f)alse-positive
/(b)oilerplate /(c)itation /(p)apers-are-the-same /(u)nsure /(s)kip /(q)uit` — no Enter needed. Each
decision commits immediately (not batched), so quitting mid-session never loses progress; whatever wasn't
reached is simply still `unreviewed`. The screen clears between candidates by default (tty only; never
when output is piped/redirected) so each one starts on a blank screen — `--no-clear` turns this off.

`(b)oilerplate` and `(c)itation` share one cascade (`mark_bucket_status()`), and `(p)apers-are-the-same`
has its own; all three resolve other rows in one press instead of the reviewer clicking through each
repeat individually:

- `(b)oilerplate` / `(c)itation`: reused boilerplate text (disclaimers, standard methodology templates,
  etc.) and independently-cited references to the same source (which often render as near-identical
  formatted citation text) both tend to hash into the same LSH bucket across many unrelated papers (see
  `lsh_index.py`'s note on skewed bucket occupancy), so pressing `b`/`c` marks not just the current pair
  but every other `potential_dupes` row whose *both* paragraphs fall in the same LSH bucket cluster as
  `boilerplate`/`citation` too — overwriting any prior status on those rows, since recognizing the pattern
  is meant to win. Falls back to marking just the current pair when the two paragraphs don't actually
  share an LSH bucket (candidates built via `build_dupe_candidates.py --brute-force` never populate
  `lsh_buckets`). Scans `potential_dupes` once and filters in Python against the bucket's paragraph-id set
  rather than inlining it into a giant SQL `IN (...)` — real boilerplate buckets on this corpus run to
  ~50,000 paragraphs, and a `paragraph_id_1 IN (...) AND paragraph_id_2 IN (...)` query at that size took
  100+ seconds; the Python scan is milliseconds regardless of bucket size.
- `(p)apers-are-the-same`: for the different false-positive case where `paper_id_1`/`paper_id_2` are two
  separate `papers` rows (e.g. retrieved twice from different sources) that are actually the same
  underlying paper — recognizable from the title+year shown in each candidate's header. Every candidate
  between that pair is then not a real cross-paper match, so `p` corrects `same_paper` to `1` (and nulls
  the now-vacuous `same_author`/`earlier_paper_id`/`later_paper_id`/`later_cites_earlier`, matching
  `build_dupe_candidates.py`'s own convention for genuine same-paper pairs) on every `potential_dupes` row
  between those two papers (`mark_same_paper()`). Deliberately leaves `status`/`reviewed_at` alone — it's
  a paper-identity correction, not a verdict on the text — so a future `--cross-paper-only` session won't
  show these again, but a plain rerun still can.

Rows any sweep already resolved are skipped for the rest of that review session instead of being
re-presented.

Filters: `--author`/`--paper` (substring match, via `potential_dupe_authors`/`papers.title`),
`--same-author-only`/`--different-author-only`, `--cited-only`/`--uncited-only`, `--min-similarity`, `--cross-paper-only`,
`--min-lcs-ratio`/`--min-ngram-jaccard` (the `text_overlap.py` checks below), `--min-title-similarity`/
`--max-title-similarity` (isolate vs. filter out the `(p)apers-are-the-same` case -- two `papers` rows whose
titles are near-identical, via a `title_similarity` SQL function registered from `difflib.SequenceMatcher`, the
same check and default 0.82 acceptance threshold `retrieve_papers.py` uses for a Crossref title match),
`--min-english-score` (filters out non-English paragraph pairs -- an `english_score` SQL function scores each
paragraph by the fraction of its words that are common English function words; no language-detection library
involved, kept dependency-free like `title_similarity`; both paragraphs must clear the bar), `--order paper` (go
through one paper's candidates at a time vs. the default most-similar-first). `--summary` prints
counts by status/flag (including how many candidates still lack an `lcs_ratio`/`ngram_jaccard` --
i.e. predate a `build_dupe_candidates.py` run since that check was added) without entering the
review loop.

### `text_overlap.py` — accurate-but-unscalable textual overlap checks

Cosine similarity on embeddings is semantic, not textual, and can be fooled by two paragraphs that
are merely on the same narrow topic; it also doesn't say what actually overlaps. `text_overlap.py`
computes two real textual-overlap metrics -- `lcs_ratio` (longest run of verbatim shared words, as a
fraction of the shorter paragraph -- the strongest single "this was copied" signal) and
`ngram_jaccard` (word 5-gram overlap by default, `--ngram-size` on `build_dupe_candidates.py`, catches
copying distributed across a paragraph rather than one long run). Both are O(paragraph length²)-ish
per pair -- too expensive across a whole corpus's O(n²) pairs, which is exactly why cosine similarity
is what candidate generation runs on, but cheap once LSH + cosine have already narrowed things down
to a small candidate set. Computed and persisted as new `potential_dupes` columns by
`build_dupe_candidates.py` (migrated onto an existing table via `ALTER TABLE`, since this was added
after the table already had live data); `review_dupes.py` displays and can filter on both.

`potential_dupe_authors` (`potential_dupe_id`, `author_id`) links every candidate to both papers' authors,
for "show me potential dupes involving author X" lookups without a multi-way join.

Idempotent, but asymmetrically: candidate rows are always recomputed from the current embeddings on
re-run (`ON CONFLICT ... DO UPDATE`), *except* `status`/`reviewed_at`, which are only ever set on first
insert and never overwritten — so re-running after adding new papers can't reset a human's prior review.
Rows referencing a paragraph that no longer exists (e.g. after `extract_papers.py`'s same-document dedup
removes one) are deleted first; a pair that still exists but has dropped below `--threshold` is
deliberately left alone rather than discarded, since it may already carry a review.

This is where an approximate-nearest-neighbor index would replace the brute-force matrix at real scale
(todo.md wants this to eventually run on hundreds of thousands of papers, possibly distributed) — the
schema and everything downstream of it wouldn't need to change, only how candidate pairs get generated.

### `classify_dupes.py` — automated `ai_check` pre-filter

Applies the fixed pattern library todo.md's "AI pre-filter pass" section documents (publisher/venue
boilerplate, shared bibliography entries, standard methodology templates, quoted external legal/rights
text, author-metadata blocks, sequential report/dataset editions, numeric/tabular embedding
false-positives) to every `same_paper=0` candidate with `ai_check IS NULL`, so re-running
`build_dupe_candidates.py` after a new retrieval batch doesn't mean redoing that whole hand-review pass.
Only ever touches `NULL` rows (same non-clobbering discipline as `status`/`reviewed_at`). One durable
rule beyond the fixed patterns: a candidate that matches nothing and is `same_author=1` gets `ai_check='yes'`
automatically (self-reuse — 198 of 201 hand-reviewed `yes` verdicts in the original pass were exactly
this); `same_author=0` survivors are deliberately left `NULL` for `review_dupes.py`/a human/another AI
pass, since those are the rare cases (3 of 201) actually worth a real look, not a guess.

### `write_dupe_reports.py` — per-paper text reports

For every paper that's the chronologically later side of `--min-count` (default 2) or more
`ai_check='yes'` candidates, writes `dupe_reports/<id>-<slug>.txt`: every match against it, with the
earlier/source paper's DOI link, `similarity`/`lcs_ratio`/`ngram_jaccard`, `same_author`,
`later_cites_earlier`, the `ai_check_reason`, and both paragraphs' text. A paper below `--min-count`
is still included if any of its candidates are cross-author (`same_author=0`) — that signal shouldn't be
dropped just for having a low count. `dupe_reports/_index.txt` lists everything ranked by count.

Each displayed paragraph goes through `expand_paragraph()` first: `extract_papers.py`'s paragraph
splitter sometimes breaks one continuous sentence across two paragraph records (a PyMuPDF
block/page-boundary artifact), and a report showing only the fragment on one side of that break can
misread as weaker or stronger evidence than the complete text actually is — this happened for real (see
todo.md's "AI pre-filter pass" correction). `expand_paragraph()` stitches in neighboring paragraphs (same
`paper_id`, adjacent `para_index`, capped at `MAX_STITCH_EXTRA` per direction) when the text looks cut
off (doesn't end in terminal punctuation, or starts lowercase) — a heuristic, not a guarantee.

### `write_dupe_reports_html.py` — HTML evidence pages for hand-reviewed cases

Sibling to `write_dupe_reports.py`, aimed at `status='confirmed'` candidates (a human's own verdict)
rather than the `ai_check='yes'` pre-filter pass. Groups by paper pair and writes one self-contained
HTML fragment per pair (`--out-dir dupe_reports_html`, no `<!DOCTYPE>`/`<html>`/`<head>`/`<body>` —
written to be published as a Claude Artifact, which wraps a page in that skeleton itself) with a
word-diffed side-by-side rendering of every matched passage, reusing `write_dupe_reports.py`'s
`expand_paragraph()`/`paper_link()`/`slugify()` and `review_dupes.py`'s tokenizer rather than
duplicating either. `--ids`/`--status`/`--title`/`--source-note` let a single specific case get a
proper short page name and an external citation (e.g. a RetractionWatch link) — see the Saxby/Taro
case for the intended usage.

### `run_pipeline.py` — chain the deterministic stages

Runs `extract_papers.py` → `embed_paragraphs.py` → `build_dupe_candidates.py` → `classify_dupes.py` →
`write_dupe_reports.py` in sequence (each stage via its own `main()` with `sys.argv` patched, same
`run_with_argv()` pattern `tests/run_tests.py` uses), stopping at the first stage that raises. Doesn't
include retrieval itself (`retrieve_papers.py`/`bulk_retrieve_arxiv.py`/`bulk_retrieve_crossref.py` have
different argument shapes and are run deliberately, not chained) — this is specifically the "a new batch
of papers just got retrieved, now turn that into updated reports" half, runnable as one command instead
of five. `--skip-extract`/`--skip-embed`/`--skip-build-dupe`/`--skip-classify`/`--skip-reports` stop (or
skip) individual stages.

### `bulk_retrieve_crossref.py` — bulk retrieval across all Crossref publishers

Like `bulk_retrieve_arxiv.py` but source-diverse: searches Crossref's `/works` index by keyword
(`query.bibliographic`, cursor-paginated) across every DOI-registered publisher, not just arXiv, then
resolves each candidate's OA copy via `retrieve_papers.py`'s own `query_unpaywall()`/`download_pdf()`.
Exists because a corpus composition check found 99.94% of this project's papers came from
`bulk_retrieve_arxiv.py`'s single arXiv `cs.CY`/`cs.LG` slice — a real limitation for *finding
duplication*, since this project's own documented ground-truth plagiarism cases are mostly cross-venue
(an MDPI journal paper copying a dissertation; a retracted IJACSA paper copying a Procedia Computer
Science paper) and a single-preprint-server corpus can't contain that pattern at all. `DEFAULT_KEYWORDS`
is a "computer ethics and adjacent fields" list distinct from `bulk_retrieve_arxiv.py`'s
fairness/bias-specific one, per todo.md's original, broader data-sources ask.

`--doi-prefix` (repeatable) restricts a keyword search to one publisher's registered DOI-prefix block
via Crossref's `filter=prefix:...`, combined with each `--keyword`. Added to walk a known
lower-scrutiny publisher's catalog on purpose (e.g. IAEME's `10.34218`) rather than hoping keyword
search surfaces that kind of venue by accident — see todo.md's "Full-corpus plagiarism audit" for why
this matters for *finding duplication* specifically. Omitted (default): no restriction, searches every
publisher, unchanged from before this flag existed.

### `bulk_retrieve_theses.py` — bulk retrieval of theses/dissertations via DataCite

Same shape as `bulk_retrieve_crossref.py` (reuses its `DEFAULT_KEYWORDS`, `load_known_dois()`, and
`retrieve_papers.py`'s Unpaywall/download pipeline) but searches DataCite's public `/dois` API
(`resource-type-id=dissertation`) instead of Crossref — university repositories register dissertation
DOIs through DataCite, not Crossref, so a Crossref-only search structurally can't find them (**only 2
of 70,041 papers in this corpus had no DOI** before this script existed — essentially zero theses made
it in). Exists because the one confirmed real plagiarism case in this project (Saxby/Taro) is exactly
a dissertation a journal paper copied from — an obscure, rarely-cross-referenced document, the
textbook profile for where copying survives undetected; see todo.md's "Full-corpus plagiarism audit"
for the full reasoning.

Uses `retrieve_papers.py`'s `download_with_landing_page_fallback()` (not the plain `download_pdf()`
`bulk_retrieve_crossref.py` uses) since a DataCite dissertation record disproportionately hands back a
repository landing page rather than a direct PDF link, from Unpaywall or from DataCite's own `url`
field as a second fallback if Unpaywall has nothing on file for that DOI at all (thinner coverage of
DataCite-registered DOIs than Crossref-registered ones). Confirmed this fallback is load-bearing, not
optional, on a live test batch: 0/15 downloaded without it, 5/15 with it (KAUST, Alberta, Cambridge,
EPFL, UT Austin repositories all rescued from a landing-page-only Unpaywall response).

### `bulk_retrieve_author_homepages.py` — author-posted PDFs from homepages/CVs

For papers no index has an OA copy of: picks the top authors of dedicated computer-ethics journals
(OpenAlex `group_by` over `DEFAULT_ISSNS`, skipping anyone named in `flagged_cases/`), finds each one's
homepage by identifier only (ORCID `researcher-urls`, then Wikidata "official website" by OpenAlex ID/
ORCID -- never a name search), crawls it plus up to `--max-pages` publication/CV-looking pages (robots.txt
honored; a PDF CV's link annotations read via PyMuPDF), matches links to the author's missing works by the
title in the text around the link, and keeps a download only if the title is on the PDF's first two pages.
Output is staged in `--out-dir` with a `list_manual_downloads.py`-format `_manifest.json` (import with
`import_manual_downloads.py`) plus a per-author `_report.json`. Pilot results (100 authors, 2026-09-18) are
in todo.md's "Author homepage/CV retrieval pilot".

## Two databases, deliberately separate

- **`state.sqlite3`** — `retrieve_papers.py`'s retrieval manifest/bookkeeping (one `papers` table: DOI
  resolution status, OA status, download status, error text). Answers "did we already try to fetch this?".
- **`library.sqlite3`** — the content-mining output (`papers`, `authors`, `paper_authors`, `citations`,
  `paragraphs`, `potential_dupes`, `potential_dupe_authors`). Answers "what's actually in the papers, and
  what overlaps?". Its `papers` table is keyed by `file_path` (not by `state.sqlite3`'s key scheme) and is
  repopulated by `extract_papers.py` from whatever `state.sqlite3` currently reports as downloaded.

They're kept apart because they have different lifecycles and concerns — retrieval bookkeeping vs.
extracted content — not merged into one schema.

## Tests

`tests/run_unit_tests.py` — true unit tests (stdlib `unittest`, no pytest dependency) for pure logic
and mocked-network paths: `retrieve_papers.py` (slugify/normalize_doi/make_key, `RateLimiter`,
`http_get`'s retry/backoff, `concurrent_fetch`'s thread pool, `PaperStore`/`ThreadLocalPaperStore`,
`process_paper`'s branching), `text_overlap.py` (`lcs_ratio`/`ngram_jaccard`), and `lsh_index.py`
(`bucket_keys`/`_pairs_for_group`, plus an integration pass through `sync_index()`/
`scan_candidate_pairs()` against a real temp-file SQLite DB). No network, finishes in well under a
second — run on every change to the modules it covers:

```bash
python3 tests/run_unit_tests.py
```

`tests/run_tests.py` — back-tests against known-outcome cases: runs the full pipeline against
`tests/cases/*.json`, each one either a real, documented plagiarism pair (find_duplicates.py must find
it) or a negative control (two unrelated papers it must not flag), in an isolated per-case working dir,
and asserts a similarity threshold. Needs the real network and `--email`. See `tests/README.md` for the
case format and current cases. Run before trusting a change to the pipeline's actual duplicate-finding
behavior:

```bash
python3 tests/run_tests.py --email you@example.com
```

## Logs

`retrieve_papers.py` logs to both console and `retrieve_papers.log` (appended across runs, not rotated).
The other three scripts log to console only.
