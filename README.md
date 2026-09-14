# dupefinder

**THIS PROJECT HAS A FALSE-POSITIVE RATE ABOVE 80% DUE TO THE MESSINESS OF REAL ACADEMIC
PUBLISHING (SAME PAPER RETRIEVED TWICE UNDER DIFFERENT DOIs, SHARED CITATIONS, REUSED
BOILERPLATE, DATABASE METADATA BUGS, AND MORE). YOU CANNOT SIMPLY RUN IT AND ACCUSE PEOPLE.
EVERY CANDIDATE IT SURFACES IS A LEAD, NOT A FINDING — YOU MUST LOOK AT THE ACTUAL SOURCE
DOCUMENTS AND VERIFY EACH ONE BY HAND (SEE `REVIEWING.md`) BEFORE TREATING IT AS REAL, LET
ALONE PUBLISHING OR ACTING ON IT.**

For a demonstration that it's not *all* noise: back-tested against Nicholas Carlini's
independently-documented, already-public 2022 finding that the 99-author survey "A Roadmap for
Big Model" plagiarized his own paper — the survey was withdrawn within two weeks, citing the
exact section Carlini flagged. This pipeline, using nothing but its own paragraph embeddings,
independently recovers that same overlap. See "Ground-truth plagiarism cases" below and
`thankyou.md` for this and three more real, externally-documented cases (not ones this project
found itself) it's validated against.

Finds near-duplicate and plagiarized text across a large library of academic papers: it downloads
open-access PDFs, extracts and paragraph-splits their text, embeds every paragraph, and searches for
paragraphs across different papers (or the same paper, or the same author) that are suspiciously
similar — then filters out the mundane explanations (shared citations, boilerplate legal text,
an author reusing their own prior work) automatically, so a human only has to look at what's left.

This document is the "what is this and how does it work" overview. `CLAUDE.md` has the full
script-by-script reference (every flag, every table column, every known gap) for anyone actually
working on the code; `todo.md` has the design notes and incident post-mortems behind the choices
below.

## What this is

A set of standalone Python scripts chained by file/database handoffs, not a packaged app — there's no
server, no build step, nothing to deploy. You run scripts in sequence; each one reads what the last one
wrote and adds to it. Everything is designed to be safely re-run: interrupt any stage and restart it
later, and it picks up exactly where it left off rather than redoing work or duplicating results.

The scale this has actually been run at: corpora from a few hundred papers up to on the order of
100,000, across several academic fields (computer ethics/AI fairness, nursing, hindawi-published
journals, cultural anthropology), with real, documented plagiarism cases (see below) used as ground
truth throughout — the goal from day one was finding *real* copying, not scoring well on synthetic
test data.

## How it works

```
starting.json (titles/authors/years to find)
        |
        v
  [retrieve papers]   -- resolve a DOI, find an open-access copy, download the PDF
        |
        v
  [extract text]      -- PDF -> plain text -> split into paragraphs + citation list
        |
        v
  [embed paragraphs]  -- each paragraph -> a 384-number vector capturing its meaning
        |                  (a fast regex + ML filter skips boilerplate here, see below)
        v
  [find candidates]   -- which paragraph-pairs are suspiciously similar? (LSH, not brute force)
        |
        v
  [classify]          -- auto-clear the mundane explanations (citations, self-reuse, disclaimers)
        |
        v
  [human review]       -- what's left, one pair at a time, git-diff style
```

Six stages, six scripts (`retrieve_papers.py`, `extract_papers.py`, `embed_paragraphs.py`,
`build_dupe_candidates.py`, `classify_dupes.py`, `review_dupes.py`), each independently re-runnable.
`run_pipeline.py` chains the middle four together as one command for "a new batch of papers just got
retrieved, now turn that into updated reports."

### Retrieval

Given a paper's title/authors/year, `retrieve_papers.py` resolves it to a DOI via Crossref, looks that
DOI up in Unpaywall for a legally-open copy, and downloads it. This is the stage where most of the real
engineering pain lives, for a mundane reason: open-access coverage is uneven and every publisher's site
behaves slightly differently. A family of `bulk_retrieve_*.py` scripts grew around this for scaling up
beyond hand-curated title lists — searching Crossref/OpenAlex by keyword or field classification,
walking a specific publisher's whole catalog, pulling an author's full bibliography, chasing
theses/dissertations through DataCite specifically (they're not registered through Crossref, so a
Crossref-only search structurally can't find them), and falling back to a PDF's `citation_pdf_url` meta
tag when the direct link on file turns out to be an HTML landing page instead of the PDF itself.

Two separate SQLite databases track state, on purpose: `state.sqlite3` is retrieval bookkeeping ("did
we already try to fetch this, and what happened") and `library.sqlite3` is everything downstream of a
successful download (extracted text, embeddings, candidate pairs). They don't share a schema or a key
scheme because they answer genuinely different questions and have different lifecycles.

#### Grey literature: four different ways in

Plagiarism doesn't only hide in mainstream, DOI-registered journal articles — a documented real case in
this project (see below) was a journal paper copying an obscure PhD dissertation, exactly the kind of
document that's easy to miss. "Grey literature" here means anything that isn't a normal DOI-registered
publisher article: theses, working papers, preprints, conference materials, institutional-repository
deposits. Four sources cover different, deliberately non-redundant slices of it — each one was checked
against the real live API before being built, not assumed to work from documentation alone:

| source | script | what it actually is | how a candidate's OA copy is found |
|---|---|---|---|
| DataCite | `bulk_retrieve_theses.py` | University repositories register dissertation DOIs through DataCite, not Crossref — a Crossref-only search structurally can't find them | Unpaywall/OpenAlex lookup on the resolved DOI, same as any other DOI-based source |
| CORE | `bulk_retrieve_core.py` | A dedicated aggregator that indexes repository content directly (institutional repositories, preprint servers, journals) rather than searching a DOI registry — plenty of real results carry `doi: null` while still having a genuine download URL | CORE's own search response already resolves a `downloadUrl` |
| Zenodo | `bulk_retrieve_zenodo.py` | CERN-operated general-purpose deposit storage — individual researchers upload working papers/conference materials directly and get a DOI auto-minted, not through an institutional repository or publisher | Search response includes a `files[]` list with a ready download link |
| SocArXiv | `bulk_retrieve_socarxiv.py` | A genuine preprint server for the social sciences (hosted on OSF), the arXiv-shaped source this project didn't have for anthropology the way `bulk_retrieve_arxiv.py` already has for computer science | Two hops: search returns a file *reference*, a follow-up request resolves that to the actual download link |

Each one needed real, live investigation before being trustworthy enough to build against — API behavior
that documentation alone didn't reveal:

- **Rate limits vary from "documented and generous" to "undocumented and best guessed."** Zenodo exposes
  real `x-ratelimit-*` response headers (confirmed: 30 requests per ~60-second window) — the safe pacing
  is a measured number, not a guess. CORE and OSF/SocArXiv expose no such header; their scripts default
  to a conservative pace instead (matching this project's normal "polite pool" convention elsewhere) and
  lean on the shared `http_get()` retry/backoff (which *does* respect a server's `Retry-After` header,
  capped at 60s) as the actual safety net. Every one of these scripts stops harvesting cleanly on a
  persistent rate-limit response rather than retrying forever — "keeping whatever was already found this
  run," the same graceful-degradation shape `retrieve_papers.py`'s OpenAlex handling already used.
  **Before running any of these against a real API key or a fresh source, actually check the response
  headers first** (as this project did for CORE and Zenodo) rather than trust a vague "raises the limit"
  claim in a script's own docstring — CORE's real quota turned out to be a **500-requests-per-day** total,
  not a per-minute pace, which changes how you budget a run entirely.
- **An API key isn't always the same kind of thing.** CORE and Zenodo both support an optional key (env
  var only — `CORE_API_KEY`/`ZENODO_API_KEY`/`OPENALEX_API_KEY`, deliberately never a CLI flag, so it
  never sits in plain sight in `ps aux` for the life of the process) but for different, concretely
  confirmed reasons: Zenodo's key raises the max page size from 25 results to 100 (fewer requests needed
  for the same depth); CORE's raises a request-rate ceiling. SocArXiv/OSF needed no key at all — every
  request there is against already-public preprint metadata.
- **A working search endpoint doesn't guarantee a working download endpoint.** CORE's search API is
  healthy, but its actual file-download service returned `HTTP 400: "No repository ID for id X"` for
  *every* result during a real run — confirmed as a genuine CORE-side outage (reproduced with a bare
  `curl`, no project code involved) rather than anything fixable here. When a whole batch fails
  identically, checking whether the failure reproduces completely outside this project's own code is
  the right first move, not re-running the same request against a service that's already told you no.
- **Some "download" links need an extra hop, and some don't.** CORE and Zenodo hand back a ready file URL
  directly in their search response. SocArXiv only gives a *reference* to the file; the actual download
  link needs a second request. Combining that second request into the *same* API call as the first
  (`embed=primary_file` alongside `embed=contributors`) reliably returned `HTTP 502` from OSF's own
  servers — a real server-side bug, not a client mistake, found only by actually trying it.
- **Not every source only hosts PDFs.** SocArXiv accepts Word-doc uploads as a preprint's primary file,
  not just PDF. Rather than inspect a filename extension by hand, `bulk_retrieve_socarxiv.py` just hands
  the resolved URL to the same shared `download_pdf()` every source here uses — its magic-byte sniffing
  (checking for a literal `%PDF` header, not trusting a server's `Content-Type`) already rejects a
  non-PDF file correctly, the same `oa_url_not_pdf` outcome a landing-page-instead-of-a-PDF case gets
  from any other source.

### Extraction

Each downloaded PDF gets its text pulled out and split into paragraphs and a separate citation list
(the references section, found by heading match). This is regex/heuristic-based, not a real
layout-aware parser, which is a deliberate scope choice — see `CLAUDE.md`'s "Known heuristic gaps" for
where that shows (footnote-style law-review citations, two-column PDFs losing inter-word spaces, that
kind of thing).

### Embedding

Every paragraph gets converted into a 384-dimensional vector using a local `sentence-transformers`
model (`all-MiniLM-L6-v2`) — no API calls, runs entirely on this machine. Two paragraphs that say
roughly the same thing end up as nearby vectors regardless of exact wording, which is what makes
*semantic* similarity search possible (catching a paraphrase, not just an exact quote).

Before a paragraph is embedded at all, two cheap filters get a chance to skip it entirely (never
embedded, never compared against anything, never shown to a human):

- **Non-English text** — the embedding model is English-tuned and produces meaningless high-similarity
  noise on other languages. `english_score()` estimates this from function-word overlap (no
  language-detection library involved, kept dependency-free).
- **Known boilerplate** — see the ML section below.

### Finding candidates: why not just compare everything to everything

The obvious approach — compare every paragraph's vector to every other paragraph's vector — is O(n²).
At 10 million paragraphs that's on the order of 10^14 comparisons, which doesn't finish. Instead,
`lsh_index.py` uses **locality-sensitive hashing** (specifically, random-hyperplane hashing / SimHash):
each paragraph's vector gets hashed into a short code such that similar vectors are likely to land in
the same "bucket," using several independent hash tables to keep that "likely" honest. Two paragraphs
sharing a bucket in *any* table become a **candidate** — worth actually computing exact cosine
similarity for — while paragraphs that never share a bucket are never compared at all. This turns an
intractable N² problem into "for each of a few million buckets, compare only the handful of paragraphs
inside it," and it's incremental: a newly-embedded paragraph gets hashed and checked against the
existing index immediately, without waiting for the whole corpus to finish.

The one real recall gap this creates: buckets containing more than `max_bucket_size` paragraphs (default
30) are skipped entirely, because a small number of extremely common paragraph types — boilerplate
disclaimers, standard methodology templates — hash into buckets with thousands of members, and
comparing every pair inside a 50,000-member bucket is its own O(n²) problem in miniature. This means an
exact-duplicate cluster larger than the cap can be missed by the fast path (`find_duplicates.py`'s
exhaustive brute-force comparison, or `build_dupe_candidates.py --brute-force`, is the fallback when
that matters).

### Classification: clearing the obvious non-plagiarism automatically

A candidate pair — two paragraphs similar enough to flag — is *not* the same thing as plagiarism. Most
candidates turn out to be one of a handful of mundane, recognizable patterns:

- Both papers cite the same source, so their bibliography entries look nearly identical
- Standard journal/publisher boilerplate (copyright notices, funding-disclosure templates, ethics
  disclaimers) that every paper from that venue carries verbatim
- An author reusing their own prior methodology section in a follow-up paper
- Two papers that happen to be about the same narrow topic and use similar vocabulary, without any
  actual text being copied

`classify_dupes.py` runs a library of over 100 hand-validated regex patterns (each one added after
reading a real example, not guessed) plus a same-author default rule, and marks whatever it recognizes
`ai_check='no'` (with a specific reason) or `'yes'` (self-reuse) — leaving only the genuinely ambiguous,
cross-author, unrecognized cases for a human. The ML boilerplate classifier (below) extends this same
idea to catch reworded variants the exact-match regexes don't.

### Human review

`review_dupes.py` is a terminal tool that shows one candidate pair at a time as a `git diff
--word-diff`-style rendering (red/green word-level highlighting) and takes a single keypress verdict —
confirmed duplicate, false positive, boilerplate, citation, "these are actually the same paper," unsure,
skip. Each decision commits immediately, so quitting partway through never loses progress. A `b`
(boilerplate) or `c` (citation) verdict also fans out to every other candidate sharing the same LSH
bucket, since recognizing one instance of a recurring pattern usually means recognizing all of them at
once, without re-deciding each one by hand.

## The ML boilerplate classifier

`train_boilerplate_family_classifier.py` adds a second, generalizing layer on top of the regex pattern
library described above.

**The gap it closes:** a regex pattern needs the *exact* wording it was written for. A publisher's
"Ethics Approval" paragraph phrased slightly differently than the one pattern already covers sails
right past every existing rule and has to be caught by a human, one instance at a time, forever.

**How it works:** each paragraph is converted into a **TF-IDF vector** — a weighted count of its words
and word-pairs, where common words ("the", "of") are down-weighted and distinctive ones ("jurisdictional
claims", "Creative Commons") are up-weighted — and fed to a **logistic regression** classifier trained
to predict one of several boilerplate *families* (citation/bibliography text, copyright/license
notices, funding acknowledgments, ethics declarations, journal masthead furniture, and a few more) or
"not boilerplate." Because it's working from the overall statistical shape of the text rather than a
literal substring, a paragraph that says roughly the same thing as a known boilerplate pattern in
different words still gets recognized — while staying cheap enough (no GPU, microseconds per paragraph)
to run as a pre-embedding filter, the same role the regex patterns already play.

**Training data** comes from the regex patterns themselves: every paragraph in the corpus that a regex
already recognizes becomes a labeled positive example for that pattern's family; a large random sample
of paragraphs *nothing* recognizes becomes the negative ("real content") class. This means the model
generalizes *within* families the regex list has already found real examples of — it cannot invent a
brand-new boilerplate category nobody has ever pattern-matched. Finding genuinely new categories still
needs a human reading a fresh sample, same as every previous round of growing the regex list.

**Why the safety tuning matters more than the accuracy:** a paragraph this filter skips is gone for
good — unlike a wrongly-classified *candidate pair*, which a human can still catch later in
`review_dupes.py`, a paragraph filtered out before embedding never gets compared against anything at
all. So the one number that actually matters is: *of paragraphs that are genuinely real content, what
fraction does the filter wrongly throw away?* The model only trusts a "this is boilerplate" prediction
above a **90% confidence threshold** — below that, it defaults to keeping the paragraph — and is trained
*without* class-rebalancing, specifically because the more "sensitive" balanced version measurably
traded away that safety margin (8.08% of real content wrongly flagged) for better minority-class recall.
The tuned version brings that down to **0.10%**, at the cost of missing more of the rarer boilerplate
variants — an intentional trade: a missed reworded disclaimer just falls through to the existing
regex/human-review path; a wrongly-discarded real paragraph is unrecoverable.

**Validated, not just trusted:** before being wired into the real pipeline, the trained model was
checked in shadow mode (predictions logged, nothing actually changed) against two independent
ground-truth sources — every paragraph involved in a human-`confirmed` real duplicate anywhere in the
corpus, and the full documented back-test suite below (real plagiarism cases plus a negative control) —
with **zero real-content paragraphs ever misclassified** in either check.

**Where it runs:** `embed_paragraphs.py`'s pre-embedding boilerplate skip calls the regex patterns
first, then this classifier as a second pass on whatever survives — auto-enabled whenever a trained
model file is present, silently falling back to regex-only if it isn't (a fresh checkout has no trained
model until someone runs `train_boilerplate_family_classifier.py`). `--no-ml-boilerplate-filter` opts
out explicitly. The same second pass is available for `--reclassify-existing-boilerplate`, the
retroactive mode that finds already-embedded paragraphs that should have been skipped.

## Ground-truth plagiarism cases

The pipeline is validated against real, documented plagiarism — not just synthetic test data — because
the actual goal is finding this in the wild, so it has to be proven against cases where the answer is
already known.

- **`tests/cases/` + `tests/fixtures/`** — the subset wired into the automated back-test suite
  (`python3 tests/run_tests.py`, see `tests/README.md`): every case here is asserted to score above a
  measured similarity threshold, so a regression in the matching logic fails a real test, not just a
  vibe check. Currently 4 positive cases (Carlini/"Roadmap for Big Model", Saxby/"Taro Roots", and two
  arXiv papers removed for plagiarism by arXiv's own administrators — one cross-author, one
  same-author) plus 1 negative control.
- **`manual_examples/`** — the broader collection, including cases found but not (yet, or ever) usable
  as an automated test: real plagiarism findings against Ward Churchill and Francesca Gino (the latter
  naming Dan Ariely as a co-author on the specific plagiarizing chapter) are documented there with full
  sourcing, even though none of their actual texts turned out to be legitimately obtainable
  (commercially published books/chapters, or — for Churchill's case — an undigitized 1972 pamphlet).
  Kept for tracking rather than silently dropped; see `manual_examples/README.md` for the full writeup
  of what was found, what's missing, and why.

Every case (working or blocked) is attributed to whoever actually found and documented the plagiarism —
the working ones are also credited in `thankyou.md`.

## Setup

```bash
sudo apt install -y python3-venv python3-pip   # one-time, needs a real terminal (interactive sudo)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

See CLAUDE.md's "Setup" section for what to do if `pip install` fails with
`externally-managed-environment`, and for the `pdftotext` system dependency `extract_papers.py` needs.

## Runbook: getting a manually-downloaded paper into the pipeline

Automated retrieval (`retrieve_papers.py`/`bulk_retrieve_arxiv.py`/`bulk_retrieve_crossref.py`) doesn't
get everything -- a real DOI gets resolved (so the paper is genuinely identified), but Unpaywall doesn't
know an OA copy, or the publisher blocks automated downloads (HTTP 403 is common), or the OA link turns
out to be a landing page rather than a PDF. CLAUDE.md's retrieve_papers.py "Known gap" documents this and
says these get "re-fetched by hand" -- `list_manual_downloads.py`/`import_manual_downloads.py` are that
by-hand process turned into a repeatable tool instead of ad hoc key-matching each time.

### 1. Generate the list

```bash
python3 list_manual_downloads.py
```

Writes `manual_downloads/_pending.md` (a human-readable list -- title, year, DOI link, why the automated
attempt failed, and the exact filename to save the PDF as) and `manual_downloads/_manifest.json` (the
same data as JSON; `import_manual_downloads.py` reads this, not the filename itself, to know which paper
a file belongs to -- so nothing depends on parsing a slug back apart).

### 2. Fetch PDFs by hand

Work through `manual_downloads/_pending.md` -- via your institution's access, the publisher site, the
author's homepage, whatever's legitimately available to you -- and save each PDF into `manual_downloads/`
using the **exact filename** the list gives for it. Skip any you can't get; nothing is lost by leaving a
paper unfetched, it just stays in the list for next time.

### 3. Import them

```bash
python3 import_manual_downloads.py
```

For each file in `manual_downloads/` that matches a manifest entry: verifies it's actually a PDF (checks
the `%PDF` magic bytes -- a rejected file is left in place, not silently dropped, so you can tell what
still needs a real download), moves it into `papers/`, and records it in `state.sqlite3` with
`status='downloaded'`, `oa_status='manual'`, and the real `file_path` -- the same shape
`retrieve_papers.py`'s own successful downloads take, so `extract_papers.py` picks it up with zero special
handling. Safe to run repeatedly as more files trickle into `manual_downloads/` over time -- already-done
entries are skipped (`--overwrite` to force), and the manifest is rewritten to only what's still missing
each time.

Then run the normal pipeline (`python3 run_pipeline.py`) to fold the newly-imported papers into
extraction/embedding/candidate-generation, same as any other batch.

## Concurrency: all DB connections go through `db.py`

Every script connects to `library.sqlite3`/`state.sqlite3` via `db.connect(path)` rather than calling
`sqlite3.connect()` directly. That helper opens in **WAL journal mode**, not SQLite's default rollback
journal — under the default mode, a plain *read* from one connection can block another connection's
*write* from ever reaching commit (not just writer-vs-writer contention), which is a real problem here
since several scripts are designed to connect concurrently (see CLAUDE.md's "may be writing
concurrently" comments throughout). This bit for real: a read-only diagnostic `SELECT COUNT(*)` run
during a live `embed_paragraphs.py` run collided with its commit and crashed it with "database is
locked" — see `todo.md`'s LSH post-mortem. WAL mode removes that class of bug: readers never block a
writer's commit and vice versa. It's retrofitted onto whichever `.sqlite3` file a script next opens with
nothing else holding it open (switching *to* WAL needs exclusive access — see `db.py`'s docstring), so it
takes effect automatically, no manual migration needed.

Practical upshot: the checks below are safe to run even while another script is actively writing.

## Runbook: verifying a clean state after a crash / before restarting a script

If a script was killed, crashed, or hit a "database is locked" error, run through this before
restarting anything.

### 1. No stray journal file

```bash
ls -la *.sqlite3-journal
```

Should print nothing (no matches). A `library.sqlite3-journal` or `state.sqlite3-journal` file next to
the database means a transaction was interrupted mid-write. SQLite auto-recovers this itself on the next
connection (rolls the incomplete transaction back — it does **not** get half-applied), so its mere
presence isn't corruption, but it shouldn't still be there after everything's actually settled. If one
persists across multiple checks a few seconds apart, something still has the file open (see step 4).

### 2. Nothing still running from the last attempt

```bash
ps aux | grep -E "embed_paragraphs|build_dupe_candidates|find_duplicates|extract_papers|retrieve_papers" | grep -v grep
```

Should print nothing. A killed foreground process is usually gone immediately, but check for stragglers
(e.g. a child process, or a `kill` that only signaled the shell wrapper) before assuming it's dead.

### 3. The database actually opens and reads

Cheap sanity check — table counts, not a full check, so it's fast even on a large `library.sqlite3`.
Uses `db.connect()` (WAL mode) like every script does, so — unlike the very first draft of this
runbook — this is genuinely safe to run even while another script has the file open for writing:

```bash
python3 -c "
import db
conn = db.connect('library.sqlite3', timeout=5)
for t in ('papers', 'paragraphs', 'lsh_buckets', 'potential_dupes'):
    print(t, conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
"
```

If this hangs instead of returning quickly, either the file is still on the pre-WAL rollback journal
(rare after a fresh checkout — check by running the query, then check `PRAGMA journal_mode;` on a second
connection; should report `wal`) or the disk itself is under pressure (step 5). If you want a real,
thorough integrity check rather than just "it opens", `sqlite3 library.sqlite3 "PRAGMA integrity_check;"`
— but it reads the *entire* file including all indexes, so budget real time for it on a large DB (it took
over 10 minutes once on a 676MB library.sqlite3 with a cold page cache after a memory crunch — see step
5). The table-count check above is enough for routine "is it safe to restart" checks; save
`integrity_check` for when you actually suspect corruption, not as a matter of course.

### 4. Nothing still has the file open

```bash
fuser library.sqlite3 state.sqlite3 2>/dev/null
# or: lsof library.sqlite3 state.sqlite3
```

Should print nothing. If a PID shows up and it's not a process you expect to be running, that's your
lock holder — `ps -p <pid> -o pid,etime,stat,cmd` to see what it actually is before deciding to kill it.

### 5. Headroom to restart

```bash
free -h
```

Check `available` isn't near zero and `Swap used` isn't already high before kicking off another
embedding/candidate-generation run. This isn't paranoia: the LSH candidate index's `max_bucket_size`
default was shipped without being measured at real scale once, and a scan against the real corpus drove
this machine to 9.7GB RSS + 13GB swap before it had to be killed by hand — see `todo.md`'s "Candidate
index design (LSH)" post-mortem for the full story and the fix. If `available` is low and nothing here
explains why, don't start a new heavy run until it clears — check for the memory hog directly with
`ps aux --sort=-%mem | head`.

## Runbook: is a silent, running script actually stuck?

Several of these scripts print nothing until they're well into a run (or, for `find_duplicates.py`,
until they're completely done) — "no output" is not by itself evidence of a hang. Before killing
anything, work through these in order; each one narrows down which of the real failure modes we've
actually hit (below) you're looking at.

### 1. Is it using CPU, or waiting on something?

```bash
ps -p <pid> -o pid,etime,stat,%cpu,%mem
```

The `STAT` column is the first signal:
- **`R`** (running) with real `%CPU` — it's genuinely computing. `find_duplicates.py`'s brute-force
  compare showed 966% CPU (multi-threaded BLAS) while actively working through a real O(n²) matmul —
  slow and silent is not the same as stuck.
- **`D`** (uninterruptible sleep) — blocked in the kernel, almost always on disk I/O. Not necessarily
  wrong, but needs the next two checks to know which case below it is.
- **`S`** (interruptible sleep) — waiting on something like a timer or signal, not disk; unusual for
  these scripts mid-work.

### 2. If it's in `D` state, what is it actually blocked on?

```bash
cat /proc/<pid>/wchan; echo
```

- **`submit_bio_wait`** — a real block I/O operation is in flight (an actual disk read/write submitted
  to the kernel). This means the process is making genuine, if possibly slow, progress — not deadlocked.
- **`folio_wait_bit_common`** — waiting on a page-cache lock, not a fresh I/O submission. This is what we
  saw when a large, ballooning WAL file made every read compete for cache pages already in flux from
  concurrent writes (see "I/O contention" below) — the actual bottleneck was a design problem (re-reading
  far more data than necessary), not disk hardware.

### 3. Is it making forward progress, or truly flat?

```bash
cat /proc/<pid>/io   # note rchar/wchar
# wait ~10-30s
cat /proc/<pid>/io   # compare
```

Growing `rchar`/`wchar` between samples (even slowly) means it's working, however slowly. Completely
flat across multiple samples, combined with `D` state, is the closer-to-actually-stuck signal.

### 4. Context: WAL size, memory, sync mode, and who else is touching the file

```bash
ls -la library.sqlite3-wal      # size + growth over repeated checks
free -h                          # available memory, swap used
ps aux | grep -E "embed_paragraphs|build_dupe_candidates|find_duplicates" | grep -v grep   # anyone else?
python3 -c "import db; print(db.connect('library.sqlite3').execute('PRAGMA synchronous').fetchone())"  # should be (1,) i.e. NORMAL
```

`PRAGMA synchronous` is per-*connection*, not stored in the file like `journal_mode` is, so the command
above only confirms `db.py` itself sets it correctly for the *next* connection -- it can't inspect what
setting an already-running, possibly-stuck process is using. If a script is showing failure mode 3 below
(near-zero CPU, huge wall time, no other writer, nothing persisted) and it was started before `db.py` set
`synchronous=NORMAL`, that mismatch is your answer -- kill and restart it, don't wait it out.

### Putting it together: the three real failure modes we hit building this

- **I/O contention between two processes sharing the same growing file** (`D` / `folio_wait_bit_common` /
  I/O counters creeping up slowly / another script actively writing concurrently / WAL growing into the
  hundreds of MB–GB range): `build_dupe_candidates.py` re-reading the *entire* `paragraphs` and
  `lsh_buckets` tables while `embed_paragraphs.py` continuously wrote to the same file stalled for over an
  hour this way — nothing was deadlocked (confirmed via this exact checklist), it was just losing a fight
  for disk bandwidth it didn't need to be in. Fix was architectural (`lsh_index.py`'s incremental
  scanning, `todo.md`'s post-mortem 2), not "wait longer" — kill it and either let the other writer finish
  first, or fix what's reading more than it needs to.
- **Genuine runaway memory** (`%MEM`/`free -h`'s swap used climbing steadily, independent of I/O state):
  `scan_candidate_pairs()`'s original `max_bucket_size=300` default building a 263M-pair array drove this
  machine to 9.7GB RSS + 13GB swap. This one *won't* resolve itself — I/O counters may even look normal
  while RAM is what's actually being exhausted. Kill it; see `todo.md`'s LSH post-mortem for the fix that
  made this specific case safe (a pre-flight pair-count projection that now refuses to proceed instead).
- **Fsync-bound writes, no other process involved at all** (`D` state, `%CPU` near zero, huge wall clock
  vs. tiny cumulative CPU time in `ps`'s `TIME`/`etime` columns, `fuser` shows only this one process has
  the file open, memory is fine): a first-ever backfill against the full corpus sat at ~0.3% CPU for 3
  hours with `lsh_scanned` still at 0 rows the whole time — real progress (`lsh_buckets` was still slowly
  growing between checks), just extremely slow. Cause: `synchronous=FULL` (SQLite's default) forces an
  fsync on every commit even in WAL mode, and several write loops here deliberately commit every ~2000
  rows (to keep lock-hold-time short, per failure mode 1's fix) — thousands of small commits became
  thousands of full fsyncs. Fixed in `db.py` (`PRAGMA synchronous=NORMAL`, WAL mode's documented pairing);
  see `todo.md`'s post-mortem 3. A process started before that fix won't benefit retroactively — kill and
  restart it rather than waiting.

Either way: killing a script here is safe (WAL mode means no reader/writer lock corruption, and an
in-flight transaction just rolls back — see the crash-recovery runbook above), so when in doubt, gather
the evidence above first, but don't hesitate to kill and re-diagnose rather than let something run
unobserved for an hour on a guess.

## FAQ: why does `embed_paragraphs.py` make network requests to huggingface.co?

It shouldn't, once the model's cached — and now doesn't. `sentence-transformers` (via `huggingface_hub`)
defaults to a HEAD request per model file on *every* load, even fully cached ones, just to check the
cache is current — not a re-download, but a real network dependency and latency on what's meant to be a
purely local step. `embed_paragraphs.py`'s `load_model()` now passes `local_files_only=True` to
`SentenceTransformer(...)`, so a cached model loads with zero network calls; it only falls back to a
normal networked load (`local_files_only=False`) the first time a given model hasn't been downloaded yet
(the documented "first run downloads it, ~90MB" case).

(An earlier version of this fix set the `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` env vars instead — don't
reintroduce that: those are read into module-level constants the first time `huggingface_hub` is
imported and never re-read afterward, so popping them mid-process to "fall back online" silently doesn't
work. Confirmed for real, the first time this ran against a completely cold model cache.)

### Then: restart

All six pipeline scripts are idempotent/resumable (state lives in `state.sqlite3`/`library.sqlite3`),
so once the above is clean, just re-run the script that was interrupted with its normal arguments — no
special "resume" flag or manual cleanup needed. It'll skip whatever it already finished and pick up
where it left off.

## License

[Unlicense](LICENSE) — public domain. Do whatever you want with this code, no attribution required.

This does **not** cover the third-party academic papers/PDFs this project retrieves or that a handful of
`flagged_cases/` write-ups include as evidence — those remain under whatever license/copyright their own
publishers or authors hold; check before redistributing any of them yourself.
