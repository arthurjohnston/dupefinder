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
pip install -r requirements.txt   # requests, sentence-transformers (pulls in torch), numpy, pymupdf, scikit-learn
```

Every subsequent command in this doc (`python3 retrieve_papers.py ...`, `python3 run_pipeline.py ...`,
etc.) assumes `.venv` is activated in that shell — run `source .venv/bin/activate` first if it's a fresh
shell, or invoke `.venv/bin/python3 <script>.py` directly instead if you'd rather not activate.

`extract_papers.py` reads PDFs with PyMuPDF (pip-installed above) — no system packages are needed to run
the pipeline itself. `pdftotext` (poppler-utils, `apt install poppler-utils`) is only needed for the
by-hand byline verification `REVIEWING.md` asks for.

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
python3 retrieve_papers.py --email you@your-institution.edu          # starting.json -> papers/, state.sqlite3
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

All of these scripts are independently re-runnable and resumable/idempotent — each keeps enough state (in
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
extracts text with PyMuPDF's block-level layout analysis (`pdf_to_text()` — replaced `pdftotext`, which
either merged whole pages into one "paragraph" or, with `-layout`, garbled two-column text), takes title/authors/year/doi from `state.sqlite3` (Crossref-verified — more
reliable than re-parsing noisy PDF header text), locates the References/Bibliography section by heading
match (searched from the end of the document, to avoid a false hit on an in-body mention) and splits it
into raw citation strings, and splits the remaining body into paragraphs (dehyphenates wrapped words,
merges fragments split across page/column breaks, filters out headers/short fragments).
`--recompute` re-extracts every downloaded paper from scratch and rewrites `paragraphs.jsonl` — use it
after changing the extraction logic; `embed_paragraphs.py` then re-embeds whatever text changed.

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
- Some two-column/justified PDFs lose inter-word spaces (`humanbiasesfrom`) in the PDF's own text layer —
  not patched.

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

### `compare_two_papers.py` — the exact word-shingle matcher and its three extension modes

`find_shingle_matches()` is the verbatim-overlap engine everything textual in this project runs on
(`write_dupe_reports_html.py`, `batch_compare_papers.py`, `rank_paper_pairs.py`,
`write_case_reports_md.py`, `mill_prefix_audit.py`, `find_title_bucket_dupes.py`). It indexes every
`--shingle-size`-word shingle of document A, looks each of B's up, and grows every hit into a maximal
run. How far a run is allowed to grow past its exact core is the part with three settings:

| mode | flags | bridges |
|---|---|---|
| exact only (default) | — | nothing; one differing word ends the run |
| lockstep X-drop | `--x-drop N` | position-for-position **substitutions** |
| gapped | `--x-drop N --gap-open M` | substitutions **and insertions/deletions** |

**Lockstep X-drop** (`_extend_xdrop()`) walks both documents in step, scoring +1 per match and
`--mismatch-penalty` per mismatch, and stops once the score falls more than `--x-drop` below its own
running maximum — then trims back to where that maximum was. It bridges a swapped or
differently-spelled word. It cannot bridge an inserted or deleted one: that desyncs every comparison
after it, the mismatches pile up, and the extension stops. Raising `--x-drop` does not help, because
the trim-back lands on the same word however far the walk looked — confirmed on a real pair
(85490 ↔ 85538), where x-drop 3 and x-drop 30 give byte-identical output.

**Gapped** (`_extend_gapped()`, `--gap-open`) is banded affine-gap dynamic programming with the same
X-drop termination — BLAST's gapped extension. It tracks three scores per cell (this pair aligned,
words consumed from A against a gap in B, the reverse), charging `--gap-open` to open a gap and
`--gap-extend` per further word of it, so one long gap costs much less than several short ones. That
is what real reuse looks like: a copied passage with a citation marker dropped, a clause spliced in,
a sentence trimmed. `--max-gap` (default 50) is the band half-width and the honest limit — an indel
longer than that stays two runs. The default was 10 at first, which was an untested guess and too
tight: measured across this project's confirmed pairs, 10 -> 50 consolidates the same evidence without
adding any (run counts fall — vigilante 29 -> 24, CE-32 14 -> 9, CE-01 137 -> 110 — while coverage
stays within a point or two), 50 -> 200 changes nothing on any of them, and six unrelated control
pairs stay at 0 runs even at 200, so a wider band manufactures nothing. Cost is the band, so 2-4x per
pair, all still under a second. If a page shows the same passage broken into several runs, raise it
further before concluding the extension is failing.
A gap also has to be *earned*: the affine cost must be repaid by matches beyond it or the extension
trims back, which is what stops it stitching unrelated passages together.

On 85490 ↔ 85538, `--x-drop 8 --gap-open 2` turned 56 runs into 29, the longest from 219 to 392
words, and coverage from 36%/34% to 39%/37% — the same text, correctly recognized as fewer, longer
passages. `--gap-open`/`--gap-extend`/`--max-gap` are on every script that runs the matcher:
`compare_two_papers.py`, `write_dupe_reports_html.py` (both views), `batch_compare_papers.py`,
`rank_paper_pairs.py` and `mill_prefix_audit.py`. `--gap-open` needs `--x-drop` (that supplies the
threshold); the two scripts whose `--x-drop` defaults to None refuse without it, and the three that
default it to 3 always have one.

**Cost, and the guard on it.** Gapped extension pays a banded DP plus an alignment *per seed*, where
the lockstep walk is nearly free per seed — so its cost tracks the exact-seed count, not document
length. Real flagged cases in either corpus sit between 1,000 and 14,000 seed hits and finish in well
under a second. Two long repetitive documents (a thesis against a proceedings volume, a masthead
reprinted on every page) reach **10–25 million** seed hits and over 2 million reported runs: lockstep
finishes those in under a minute, gapped does not finish at all. Found the hard way — a 400-pair
backlog sweep sat at 100% CPU for 20 minutes on one pair having done 400 in under three minutes
lockstep. So `find_shingle_matches()` counts seed hits before doing any gapped work and raises
`DegenerateGappedPair` past `MAX_GAPPED_SEED_HITS` (250,000 — three orders of magnitude clear of any
real case), in milliseconds. `rank_paper_pairs.py` and `mill_prefix_audit.py` catch it and re-measure
that pair lockstep, marking it `[LOCKSTEP]` in the report so its figures aren't read as comparable;
the single-pair tools print the message and the remedy (drop `--gap-open`, or raise `--shingle-size`,
which cuts the seed count fast on a repetitive pair) and write nothing.

Two interactions to know when sweeping with it. `--min-shingle-matches` and `rank_paper_pairs.py`'s
own ranking both count *runs*, and gapped scanning reports FEWER, longer runs for the same text, so a
threshold tuned against lockstep counts filters harder than it used to — judge on coverage, not run
count. And a backsweep of the 143-paper GIFT set (see FLAGGED_CASES_INDEX.txt's CE-38) found gapped
scanning promotes pairs lockstep ranked as ordinary noise: two more pairs cleared "50% of both
documents" (45%/40% -> 61%/57% and 31%/34% -> 53%/55%), none dropped out, and the AI-Doctor/
Real-Estate pair went from 63%/61% to 88%/92%. Those pairs are the template-with-nouns-swapped shape,
where substitutions are dense AND indels are frequent -- exactly what lockstep extension keeps
breaking on.

**What every caller has to know:** with an indel bridged the two sides of a run are *different
lengths*. `find_shingle_matches()` returns `ShingleRun` named tuples whose first ten fields are
positionally what the plain tuples were (so `r[0]`/`r[3]`/`r[5:9]` indexing is unaffected) plus an
eleventh, `indels`, and `length` is the A side only. Anything pairing the two sides
position-by-position must check for equal length and fall back to a `difflib` alignment — both
renderers in `write_dupe_reports_html.py` do, so the reports show which words were inserted rather
than silently mispairing everything past the first gap. Substitution and indel counts come from that
same `difflib` alignment (`_alignment_counts()`), not from a DP traceback, so the numbers always
match the alignment the reports draw.

### Wrong-PDF records: a retrieval failure that tops every ranking

A paper record can contain **a different paper entirely**: the publisher's DOI resolves to the wrong
file, retrieval stores it under the requested title, and `extract_papers.py` faithfully extracts
someone else's document into that record. The pair then measures as 100%/100% with a multi-thousand-word
run and outranks every real finding — while being one paper against itself. Five such pairs sat at the
top of a 400-pair gapped sweep on 2026-09-26, including "A Comparison of Popular Home Security Systems"
against "Drug Prediction System Using Data Mining Techniques" at 100%/100%, and "The Change In The
Concentration Of Phospholipids" against "Socio-Pedagogical Bases Of Ideological Preventive Work".

**Detection is pairwise, not per-paper.** The tempting test — does a paper's own title appear in its own
extracted text — is useless in bulk: **27% of a 1,500-paper sample fails it** for entirely benign
reasons (catalogue-added `Review of:`/`Retraction Note:` prefixes, `pdftotext` losing inter-word spaces
in two-column layouts, a title rendered as an image, metadata titles that differ from the printed one).
What is decisive is the pair signature: *this record is missing its own title AND carries the other
paper's*. That is only knowable once a candidate pair exists, so the check lives in triage —
`rank_paper_pairs.wrong_pdf_side()`, which marks the pair `[WRONG-PDF]` in its report with the side at
fault, plus a NOTE at the top. Validated at 5/5 on the real cases and 0/43 false positives on pairs
screened clean. `tests/unit/test_rank_paper_pairs.py` pins both halves down — an earlier version of the
condition compared the wrong pair of facts and would have missed every real case.

**The repair** follows `filter_low_relevance_papers.py`'s convention: move the PDF to
`papers_excluded/wrongpdf/`, set `state.sqlite3` status to `excluded_wrongpdf` with the reason in
`error` (so `extract_papers.py`, which only reads `status='downloaded'`, never picks it up again), and
delete the bogus content from `library.sqlite3` — paragraphs, `lsh_buckets`/`lsh_scanned` rows,
`potential_dupes` + `potential_dupe_authors`, `paper_authors`, `citations`, and the `papers` row.
Leaving the extracted text in place keeps regenerating the same false pair on every sweep. The five
found on 2026-09-26 were repaired this way (141,252 -> 141,247 downloaded).

Not automated as a bulk pass, and deliberately: the only reliable signal is pairwise, so there is no
corpus-wide sweep to run that wouldn't either miss most of them or delete real papers.

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

**`--whole-document`**: renders both papers end to end as ONE continuous word-level diff instead of a
list of per-run exhibits — shared verbatim text highlighted, everything that differs marked inline
where it falls. For a pair that is substantially the same document (the paper-mill republication
shape: same body, swapped title noun, new byline), the exhibit view is the wrong shape — "Shingle
match 1 of 82" invites assessing 82 separate findings when the finding is that there is *one*
document here, and the diff shows that at a glance: an almost entirely highlighted page with a few
red/green spots at the masthead, the title and the author names. Built from the same
`find_shingle_matches()` runs (so it needs `--shingle-size`), and it replaces the exhibits rather than
adding to them — drop the flag to get the per-run page back. Both views write the *same* filename, so
to keep both for one pair (worth it where the reordering caveat below fires) send one to a different
`--out-dir` or append a suffix to `--case-number`; CE-38's three reorder-affected pairs use
`--case-number <NN>-<a>-<b>-per-run` for exactly this. `--max-gap-words` (default 120, 0
disables) collapses a long one-sided stretch into a click-to-expand block so one paper's genuinely
original section can't bury the diff around it; nothing is omitted. Pages run roughly a fifth the
size of the exhibit version, since no passage is rendered twice with context.

**`--neutral`**: for pages shown outside a review (this repo's `example_output/`). Heading
"Text-overlap comparison" instead of "Duplicate-text finding", a two-way arrow between the papers
instead of an earlier→later one, paper cards labelled "Paper A"/"Paper B" rather than "Earlier /
source" and "Later / flagged", no "this was copied" phrasing, and no `--classification` badge or
`review_dupes.py` command block. Measurements and the diff itself are unchanged. (Before 2026-10-04 the
card labels leaked through `--neutral` on every pair with different years; printed years are not
reliable enough to label a side anyway -- CE-36's "2023" paper cites 2025 work.)

**Labels follow the extension mode**, in three tiers, because the mode changes what a "run" is and a
page that will be sent to a publisher must not claim more than the scan established: no `--x-drop` is
"exact" (N consecutive *identical* words); `--x-drop` alone is "near-exact" (substituted words allowed
within a run, no indels); `--gap-open` is "near-verbatim" (substituted *and* inserted/deleted words).
The heading, the summary bar, the legend and the footer all say which, and the footer names the exact
flag values so the page is reproducible. This corrected a pre-existing looseness too — the old blanket
"Exact word-shingle matches" heading was already wrong under `--x-drop`.

A gapped page also reports **substituted and inserted/deleted words separately** (the first version
halved their sum, which reports an indel as half a substitution), and states what the coverage
percentage actually counts: every word inside a matched run, *including* the edited ones. So it prints
the share that is not identical — 1% on the Food Delivery pair (genuinely one document word for word),
26% on CE-32 and 21% on Smart Inventory/BusBee (the same passages, reworded at word level). Without
that line "shared 100%" reads as "word-for-word identical", which under gapped extension it is not.

**Ordering affects this view only, not detection.** `find_shingle_matches()` looks each shingle of B
up in a hash index of A, so relocated material is found wherever it moved to — on a real corpus pair,
reversing or shuffling one document's paragraphs leaves scan coverage at 100% (128 runs unchanged vs
253 runs reversed, both 100%), while the ordered spine drops from 100% to 8%. Every consumer that
reports a coverage figure — `rank_paper_pairs.py`, `mill_prefix_audit.py`, `write_case_reports_md.py`,
and this module's own header stats — sums over ALL runs; `_wdd_spine()` is called in exactly one
place, for layout. This matters for a case in this corpus: one paper's conclusion (word 6702)
reappears in the other's abstract (word 210), which the per-run exhibit view shows and the ordered
view cannot place. `tests/unit/test_compare_two_papers.py`'s `TestOrderIndependence` and
`test_rank_paper_pairs.py`'s `TestSpineIsTheOnlyOrderedStep` pin the asymmetry.

The one thing this view cannot do is show reordered material: the walk is monotone, so a run that
sits at a different *position* in each paper can't be placed, and `_wdd_spine()` picks the
heaviest forward-moving chain. The page's headline overlap figure is always the **scan's** coverage
(the number `FLAGGED_CASES_INDEX.txt`, the case WRITEUPs and `rank_paper_pairs.py` all report), and
where the ordered diff places materially less than that — 100+ matched words, `WDD_UNPLACED_NOTE_WORDS`
— a caveat says so, gives both figures, and points at the exhibit view. Note that a run falling
outside the spine is *not* the same as lost coverage: `find_shingle_matches()` reports one run per
occurrence pair, so 72 of 80 runs sit outside the spine on this project's own 99%/99% pair while
coverage stays at 99%. That is why the caveat keys on covered words, not on dropped runs.

### `run_pipeline.py` — chain the deterministic stages

Runs `extract_papers.py` → `embed_paragraphs.py` → `build_dupe_candidates.py` → `classify_dupes.py` →
`write_dupe_reports.py` → `copy_dupe_pdfs.py` in sequence (each stage via its own `main()` with `sys.argv` patched, same
`run_with_argv()` pattern `tests/run_tests.py` uses), stopping at the first stage that raises. Retrieval is off
by default (`--retrieve-input FILE --retrieve-email ADDR` runs `retrieve_papers.py` first; the
`bulk_retrieve_*.py` scripts have different argument shapes and are run deliberately, not chained) — this is specifically the "a new batch
of papers just got retrieved, now turn that into updated reports" half, runnable as one command instead
of five. `--skip-extract`/`--skip-embed`/`--skip-build-dupe`/`--skip-classify`/`--skip-reports`/`--skip-copy-pdfs`
skip individual stages.

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
`process_paper`'s branching), `text_overlap.py` (`lcs_ratio`/`ngram_jaccard`), `lsh_index.py`
(`bucket_keys`/`_pairs_for_group`, plus an integration pass through `sync_index()`/
`scan_candidate_pairs()` against a real temp-file SQLite DB), `compare_two_papers.py`
(`find_shingle_matches()`'s exact/X-drop/gapped extension paths, `_extend_gapped()`'s boundaries and
`_alignment_counts()`), and `rank_paper_pairs.py` (`wrong_pdf_side()`, both that it fires on the pair
signature and that it stays silent on the benign title-mismatch shapes). No network, finishes in well under a
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
python3 tests/run_tests.py --email you@your-institution.edu
```

## Logs

`retrieve_papers.py` logs to both console and `retrieve_papers.log` (appended across runs, not rotated).
The other pipeline scripts log to console only.
