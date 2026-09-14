# Lead agent playbook: retrieval → pipeline → subagent review → feedback

A runbook for whichever agent is orchestrating a full cycle on one corpus (its own
`<corpus>/state.sqlite3` + `<corpus>/library.sqlite3` + `<corpus>/papers/`, e.g. `anthropology/`,
`hindawi/`, `nursing/`, `computer-ethics/`) — kicking off retrieval, running the deterministic
pipeline, dispatching subagents to review what it found, and feeding real findings back into the
pipeline so the next cycle doesn't rediscover the same noise. Assumes you've read `CLAUDE.md` (what
each script does) and `REVIEWING.md` (the per-candidate judgment criteria) already — this is the
sequencing and orchestration layer on top of both, written up after a real session that hit most of
the failure modes described below for real, not hypothetically.

## Phase 0: before kicking anything off

**Check for other running retrieval against the same corpus first.** Two or more `bulk_retrieve_*.py`
processes writing to the same `state.sqlite3` is normal and expected (that's the whole point of Phase
1 below) — but only if every script involved has the atomic cross-process claim fix. As of this
writing, `bulk_retrieve_core.py`, `bulk_retrieve_zenodo.py`, `bulk_retrieve_socarxiv.py`,
`bulk_retrieve_theses.py`, and `bulk_retrieve_crossref.py` (which `bulk_retrieve_author_works.py` and
`bulk_retrieve_openalex_concept.py` also share code with) all have it. **`retrieve_papers.py`'s own
single-paper `process_paper()` does not** — its skip-checks are scattered through the function rather
than consolidated, so it was deliberately left unfixed rather than rushed (see `todo.md`'s
"Cross-process retrieval dedup race" entry for the full incident writeup, including why a quick fix
attempt there broke a real test). **Never run `retrieve_papers.py` concurrently with a bulk script
against the same `state.sqlite3`.** Before adding any new `bulk_retrieve_*.py` script to a concurrent
batch, grep its `fetch_candidate()` for `store.claim(` first — if it's not there, either add it
(pattern below) or run that one alone, not alongside the others.

**The fix pattern**, for any script found missing it — copy this into `fetch_candidate()`, right after
computing `key` and before any real network work (Unpaywall query, download):

```python
record = store.get(key)
if record:
    status = record.get("status")
    if status and status.startswith("excluded_"):
        return "skipped"
    if status == "downloaded" and record.get("file_path") and Path(record["file_path"]).exists():
        return "skipped"

if not store.claim(key):
    return "skipped"
```

If the target function has *other* skip-without-recheck branches after this point (e.g. a
`no_oa`-without-`--recheck` check, or a real-download block that itself can return early) — like
`retrieve_papers.py`'s `process_paper()` does — this simple insertion is **not safe**. Every such branch
needs to run *before* the claim (a claim commits to doing real work; nothing after it should still be a
free skip), which usually means restructuring, not just inserting one call. Don't rush that under time
pressure — flag it in `todo.md` and move on, the way `process_paper()` was left this session.

## Phase 1: expanding a corpus with grey literature (optional, run when a corpus looks skewed)

If a corpus check shows most papers came from one narrow source (a single preprint server, a single
publisher's keyword search), the four grey-literature scripts (`CLAUDE.md`'s "Grey literature: four
different ways in") fill that gap. Run all four with explicit paths into the target corpus directory —
never rely on the default `papers`/`state.sqlite3` relative-path arguments, which resolve against
whatever the current shell's cwd is and will silently write into the wrong corpus (or the repo root) if
you get it wrong:

```bash
env OPENALEX_API_KEY="$(cat 'sourcing/openalex_api_key')" .venv/bin/python3 bulk_retrieve_theses.py \
    --email <email> --outdir <corpus>/papers --db <corpus>/state.sqlite3 \
    --log-file <corpus>/bulk_retrieve_theses.log

env CORE_API_KEY="$(cat 'sourcing/core api key')" .venv/bin/python3 bulk_retrieve_core.py \
    --email <email> --outdir <corpus>/papers --db <corpus>/state.sqlite3 \
    --log-file <corpus>/bulk_retrieve_core.log

.venv/bin/python3 bulk_retrieve_zenodo.py \
    --email <email> --outdir <corpus>/papers --db <corpus>/state.sqlite3 \
    --log-file <corpus>/bulk_retrieve_zenodo.log \
    --no-key-ok   # no stored Zenodo key as of this writing -- fine, just slower pagination (25/page vs 100)

.venv/bin/python3 bulk_retrieve_socarxiv.py \
    --email <email> --outdir <corpus>/papers --db <corpus>/state.sqlite3 \
    --log-file <corpus>/bulk_retrieve_socarxiv.log
```

Stored keys live in `sourcing/openalex_api_key` and `sourcing/core api key` (note the space in that
filename — quote it). All four default to `bulk_retrieve_crossref.py`'s `DEFAULT_KEYWORDS`
("computer ethics and adjacent fields") when `--keyword` is omitted — pass your own `--keyword` (repeatable)
if the corpus's actual topic is different; don't just let the default run against an unrelated corpus.

**The `require_api_keys.py` PreToolUse hook** will block any of the six `SCRIPT_KEY_MAP` scripts
(`core`/`zenodo`/`crossref`/`theses`/`author_works`/`openalex_concept`) if it can't detect the relevant
API key env var anywhere in the command text. Setting the var inline (`env VAR="..." python3 ...`, as
above) satisfies it. `--no-key-ok` is a real, accepted flag on all six scripts (fixed this session —
it used to be documented but not actually implemented, which broke the hook's own suggested escape
hatch) for a deliberate anonymous/slower run.

**Realistic pace, from real runs this session** (25-keyword `DEFAULT_KEYWORDS`, per source): CORE ~40s/
keyword (~15-20 min total). Zenodo ~3+ min/keyword anonymous, mostly pagination-bound at 25 results/page
(~75-90 min total; a real API key would roughly quarter this). SocArXiv is the long pole — OSF's API is
flaky (frequent read-timeouts needing retries) and a single keyword has taken 10+ minutes; budget hours,
not minutes, for a full 25-keyword run. Don't promise a tight ETA to whoever's waiting on this — give a
range and say you'll know more from the next check-in.

### Monitoring while these run

Run each via `Bash` with `run_in_background: true`, one call per script (they're independent, safe to
run in parallel per the claim-fix above). Poll with `Read` on the task's output file or the log file
directly. **Watch the corpus's `downloaded` count every check**:

```bash
python3 -c "
import db
conn = db.connect('<corpus>/state.sqlite3')
print(conn.execute(\"SELECT COUNT(*) FROM papers WHERE status='downloaded'\").fetchone()[0])
"
```

**A decrease does NOT automatically mean the cross-process race.** There's a second, legitimate reason
the count can drop, confirmed for real this session: `PaperStore.claim()`'s pre-check only skips a
`status='downloaded'` row when its `file_path` **still exists on disk**; if the file is genuinely
missing (this corpus's own `papers/` directory was found completely empty once already, cause still
unexplained — see `todo.md`), the pre-check correctly falls through to a "legitimate recovery" re-download
attempt, which can legitimately fail (dead link, source now 403s, etc.) and correctly records
`status='error'` — dropping the count without any bug involved. **The two cases look identical from the
count alone; they are not identical, and treating the second as the first wastes a stop-and-investigate
cycle for nothing.** Distinguish them by checking whether the *newly non-downloaded* rows still have a
file on disk:

```python
import db
from pathlib import Path
conn = db.connect('<corpus>/state.sqlite3')
# narrow to rows touched since your last check, not all-time -- e.g. updated_at > '<last check's ISO timestamp>'
rows = conn.execute("SELECT key, file_path FROM papers WHERE status != 'downloaded' "
                     "AND file_path IS NOT NULL AND file_path != '' AND updated_at > ?", (last_check_iso,)).fetchall()
exists = sum(1 for k, fp in rows if Path(fp).exists())
print(f"{exists}/{len(rows)} newly-affected row(s) still have their file on disk")
```

- **Any of them still have a file on disk** → this is the real race. Stop every running retrieval
  process immediately (`TaskStop` on each) before investigating further, don't let it keep compounding.
  Recovery:
  ```python
  to_restore = [k for k, fp in rows if Path(fp).exists()]
  conn.executemany("UPDATE papers SET status='downloaded', error=NULL WHERE key=?", [(k,) for k in to_restore])
  conn.commit()
  ```
  Only restore rows whose file is confirmed still on disk — restoring `status='downloaded'` for a row
  whose file is actually gone just replaces an honest failure status with a false record.
- **None of them have a file on disk** → this is the missing-file recovery-attempt case, not a bug.
  Nothing to restore (there's no file to point `status='downloaded'` back at — `error` is the honest
  status here), no need to stop anything. Just note the count drop and keep going. Worth flagging to
  whoever's tracking this that the underlying missing-files mystery is still live and apparently
  affecting more than a handful of rows, but that's a separate, already-known open question, not a new
  incident.

### Long-unattended monitoring: local cron, not a cloud routine

If this needs checking over a longer stretch than you'll stay actively engaged (an hour or more), a
**local session-scoped `CronCreate` recurring job** is the right tool, not a cloud `/schedule` routine —
a cloud routine runs in Anthropic's cloud on a fresh checkout with no access to this machine, so it
literally cannot see a locally-running background task or read a local-only log file. It would silently
fail to find anything to check. If a user asks for hourly-or-longer check-ins, that distinction is
worth surfacing before setting anything up (see `/loop`'s own cloud-offer step) — don't build a cloud
routine for a task that structurally can't work that way.

A local `CronCreate` job is session-only (dies if the session closes, auto-expires after 7 days) — say
so up front. Prompt it with the exact task IDs to check, the log-file paths, and the same
downloaded-count-regression guard described above, so an unattended firing doesn't just report numbers
but actually catches the race if it recurs:

```
Check on background job(s) <task_id(s)> via their log files in <corpus>/*.log. Report progress since
last check. Sanity-check the downloaded count hasn't decreased (query above) -- if it has, TaskStop
everything and tell the user before anything else. Once all jobs finish, report final totals and stop
recommending further checks.
```

When you kill and later resume jobs (new task IDs each time), delete the old cron job and recreate it
with the current IDs rather than letting it reference dead tasks.

### Known external failure modes — recognize these, don't chase them as your own bugs

- **AWS WAF bot-challenge** (figshare and figshare-powered institutional repositories — e.g. Purdue's
  "hammer", `<university>.figshare.com`): downloads fail with `HTTP 202` and an empty body. Confirmed via
  `curl -sI <url>` showing `x-amzn-waf-action: challenge` — a bot challenge, not a broken link, not
  fixable by adjusting retry logic (would need real JS execution to pass). Expect this whenever theses
  candidates route through figshare.
- **CORE outages**: a healthy search API but a broken download endpoint (`HTTP 400: "No repository ID
  for id X"`) has happened before and was confirmed as CORE-side via a bare `curl`, no project code
  involved. A *different* CORE incident (2026-09-04) confirmed the same `core.ac.uk` download domain can
  independently pick up its own Cloudflare bot-challenge (`cf-mitigated: challenge` header, `HTTP 403`)
  after a high-volume burst — same underlying WAF-challenge phenomenon as the figshare case above, just a
  different vendor (Cloudflare vs. AWS WAF) and a different source.
- **A bot-challenge doesn't necessarily respond to slowing down.** Tested for real: after CORE and
  figshare-hosted theses both hit their respective challenges from a high-volume run, restarting *much*
  slower (10x the interval, `--max-workers 1`) did not help either — re-testing the exact same failing
  URLs live with `curl` 15+ minutes into the slow restart showed the identical challenge response on both.
  That result means the block is IP-level, not a request-rate throttle a gentler pace would satisfy — at
  that point, stop the retrieval process entirely rather than let it keep grinding through guaranteed
  failures; there's no pace slow enough to fix an IP flag, and time (an unknown cooldown, possibly hours)
  is the only lever left, not code. Re-verify with one live `curl` against a currently-failing URL before
  concluding a slowdown didn't work — don't infer it from the error log alone, confirm the actual response
  hasn't changed.
- **General rule**: when many *different*, unrelated candidates fail identically, reproduce the failure
  with a bare `curl` outside any project code before assuming it's something here to fix. If it
  reproduces identically outside the project, it's an external dependency issue — log it, move on, don't
  spend the session's effort budget on it.

## Phase 2: the deterministic pipeline

Once retrieval is done (or far enough along to be worth processing), run `run_pipeline.py` (or its
stages individually if you want to stop and inspect between them — see `CLAUDE.md`):

```bash
python3 run_pipeline.py --state-db <corpus>/state.sqlite3 --library-db <corpus>/library.sqlite3 \
    --paragraphs-file <corpus>/paragraphs.jsonl --reports-out-dir <corpus>/dupe_reports \
    --dupe-pdfs-out-dir <corpus>/flagged_dupe_pdfs
```

(`extract_papers.py` reads each PDF's path from `state.sqlite3`'s own `file_path` column — there's no
separate papers-directory flag to pass. Every path argument here defaults to a root-relative name, so
get in the habit of passing all of them explicitly for a non-default corpus; a forgotten one silently
reads/writes at the repo root instead of erroring.)

This chains `extract_papers.py` → `embed_paragraphs.py` → `build_dupe_candidates.py` →
`classify_dupes.py` → `write_dupe_reports.py`. It's resumable/idempotent — safe to re-run after adding
more retrieved papers, it only processes what's new. Sanity-check afterward with
`review_dupes.py --library-db <corpus>/library.sqlite3 --summary`.

## Phase 2.5: wide-net paper-pair triage (find whole-document overlap the per-candidate view can miss)

A single `potential_dupes` row is one matched paragraph — real whole-document overlap (the shape both the
refugee-fathers and fusarium `flagged_cases` turned out to have) shows up as *many* rows between the same
two papers, not necessarily as any one row with an unusually high `similarity`. Ranking paper pairs by
how many candidate rows they share surfaces that pattern directly, instead of hoping a reviewer notices
it one row at a time.

**Use `potential_dupes`, not raw `lsh_buckets`, for the ranking.** Both were considered; only one is
actually cheap. `potential_dupes` is already the output of LSH bucketing + a real similarity threshold
(≥0.85 cosine by default) — a paper-pair count over it is a *stronger* signal than raw bucket
co-occurrence (which includes every incidental hash collision, not just real matches), and it's
dramatically cheaper: this query ran in ~1.1s against anthropology's 170k-row `potential_dupes` table,
versus a bare `COUNT(*)` on the same corpus's `lsh_buckets` table not finishing in 3+ minutes (killed
rather than let it keep running — that table is roughly two orders of magnitude bigger, and a full scan
over it is the same class of operation `todo.md` already documents causing multi-hour stalls at this
corpus's scale). Don't reach for `lsh_buckets` for this unless the `potential_dupes`-based ranking
genuinely doesn't have what you need.

```sql
SELECT paper_id_1, paper_id_2, COUNT(*) as n
FROM potential_dupes
WHERE same_paper=0 AND same_author=0
GROUP BY paper_id_1, paper_id_2
ORDER BY n DESC LIMIT 100
```

(Drop `same_author=0` to include self-reuse pairs too — usually not worth it, they're already the
lower-severity, well-understood category, and including them just pushes real cross-author pairs further
down a fixed-size top-N.)

**For each pair in the ranking, run the exact-shingle method alone** (`compare_two_papers.py`'s other
method, sentence-embedding comparison, needs a loaded model per pair and is the expensive part per-pair;
skip it for a first-pass triage across a whole ranking and let a human/later step decide which specific
pairs are worth the full combined treatment):

```bash
python3 compare_two_papers.py --library-db <corpus>/library.sqlite3 \
    --paper-a <id1> --paper-b <id2> --skip-sentences --shingle-size 8 \
    --out <triage_dir>/<id1>-<id2>.txt
```

`--shingle-size 8` (vs. the tool's own default of 6) is deliberately stricter here — at this volume (up
to 100 pairs) a shorter shingle size produces more short, low-value matches to sift through; 8 consecutive
identical words is a stronger floor for "worth a human's attention" when triaging in bulk, vs. 6 being the
right default when a human is already looking closely at one known pair. Real per-pair cost: well under a
minute even for large documents (the refugee-fathers case's shingle-only pass ran in well under a minute
on ~50k+73k-word documents) — the whole top-100 batch is a 15-50 minute job, not an OOM/multi-hour one,
safe to run as one background task or split across a few parallel ones.

**"Generate the reports" means:** one triage file per pair (`compare_two_papers.py`'s own `--out` output,
as above) plus one consolidated summary ranking every pair by total shingle-matched words (sum of all
`length` values across a pair's runs) and longest single run — the two numbers that mattered most in
distinguishing the flagged cases (Case-1-caliber: 94.8% of one paper's words, one 342-word run; ordinary
noise: a handful of short runs under a few hundred words total). The summary is what a human or a later
Phase-3 pass actually triages against — nobody should need to open all 100 raw files to find the two or
three worth a closer look. A minimal summary line per pair:

```
paper_id_1  paper_id_2  n_runs  total_shingled_words  longest_run  potential_dupes_count
```

sorted by `total_shingled_words` descending. Anything near the top with `longest_run` in the hundreds is
worth escalating to the full `compare_two_papers.py` treatment (both methods, `--append-to-writeup`) and
a `REVIEWING.md`-framework review per Phase 3 above — this triage pass is explicitly a filter, not a
verdict; nothing here should be written up as a finding on the strength of the triage numbers alone.

## Phase 3: dispatching subagents to review candidates

Once `potential_dupes` has fresh rows, split the actionable backlog and hand slices to subagents rather
than reviewing serially. The actionable slice (matching `find_review_candidates.py`'s own filter) is:

```sql
same_paper=0 AND same_author=0 AND ai_check IS NULL AND status='unreviewed'
```

Partition it across N subagents with `id % N = <partition>` so no two agents touch the same rows. Each
subagent applies `REVIEWING.md`'s judgment framework and writes verdicts non-interactively:

```bash
python3 review_dupes.py --library-db <corpus>/library.sqlite3 --agent-verdict <potential_dupes id> <key>
```

**Don't dispatch a batch of these while a write-heavy maintenance script is still running against the
same `library.sqlite3`** (`embed_paragraphs.py --reclassify-existing-boilerplate`, `build_dupe_candidates.py`,
`classify_dupes.py`, `find_duplicate_papers.py` applying its `same_paper` correction) — confirmed for
real: dispatching 6 review subagents while a reclassify pass was still doing its own
`DELETE FROM lsh_buckets` calls produced a `sqlite3.OperationalError: database is locked` that killed the
maintenance script outright. `db.py`'s WAL mode removes reader-vs-writer blocking, but concurrent
*writers* still serialize against each other, and enough of them queuing at once can exceed even the
120s connection timeout. Either wait for a write-heavy maintenance script to finish before dispatching a
review batch, or accept that the maintenance script may need a retry afterward (it's idempotent, so a
retry is safe/cheap) — but don't be surprised when it fails if both run at once, and don't treat that
failure as a sign of a deeper bug when it's really just this.

`<key>` is `d`/`f`/`b`/`c`/`p`/`u` — same semantics as the interactive keys, written to the
`agent_decided_*` status vocabulary (never the human one directly, so a later human audit pass can tell
them apart). **Non-negotiable steps before a subagent calls `d` (confirmed) on anything**, per
`REVIEWING.md`'s checklist — these are there because getting them wrong produced real, embarrassing
errors this session:

1. **Verify authorship from the actual PDF, not the database.** `same_author`/`paper_authors` come from
   Crossref metadata and can simply omit a real co-author. Before treating `same_author=0` as genuine
   cross-author, `pdftotext` both source PDFs and read the real bylines. Check every name on one byline
   against every name on the other, including differently-punctuated/initials-only forms of the same
   name (`"Smith A."` / `"Smith.a"` / `"A. S."` are the same person) — a real case this session looked
   like cross-author duplication for hours until this check caught a shared author the database had
   simply lost.
2. **Run `compare_two_papers.py` on the pair before finalizing**, once it's narrowed to two specific
   papers worth a real look:
   ```bash
   python3 compare_two_papers.py --library-db <corpus>/library.sqlite3 \
       --paper-a <id> --paper-b <id> --append-to-writeup <writeup path>.md
   ```
   This runs two independent methods together — exact 6-word word-shingle matching (catches verbatim
   copying regardless of sentence boundaries) and sentence-embedding + word-overlap comparison (catches
   paraphrase) — and appends the complete findings (with DOI links) directly onto the end of the writeup
   file, not into a separate file that's easy to lose track of. **Always use `--append-to-writeup`, not
   `--out`, for anything meant to be kept.** A real case this session looked like "just one reused
   abstract" until this tool found 94.8% of the paper's words were verbatim-identical across nearly the
   entire document.
3. **Check whether the later paper actually cites the earlier one** — search *both* the extracted
   `citations` table (`SELECT raw_text FROM citations WHERE paper_id=?`) *and* a direct full-text search
   of the later paper's own PDF (citation-list extraction has known gaps — hanging-indent/author-year
   styles and footnote-style law-review references both fail to split per-entry, see `CLAUDE.md`'s
   `extract_papers.py` section). A paper's absence from a reference list that otherwise cites *other*
   work by the same source author is a stronger signal than absence from a sparse/poorly-extracted list
   — note which case you're in.
4. **Confirm each paper_id's `papers.title` actually matches its own extracted content** — read a couple
   of its own paragraphs (not just the matched passage) and check the subject matter is plausibly the
   same paper the title describes. Computer-ethics case 20 (retracted) rested on two papers sharing an
   identical title where one paper_id's real content turned out to be a totally unrelated paper — the
   "match" was never content duplication, just shared journal-masthead boilerplate. This recurs
   specifically on identical-title candidates from low-scrutiny venues (IAEME, AMJSAI) — exactly the
   venue family most paper-mill candidates come from — so it's worth this one extra check before writing
   up any identical-title case from that population, not just an occasional spot-check.

Batch-apply a large set of pre-decided verdicts (e.g. after a subagent's analysis pass, or when
converting your own findings into database state) with `apply_agent_verdicts_batch.py`, which takes a
JSON `{potential_dupes_id: key}` mapping and calls `apply_agent_verdict()` for each.

**Writing up a significant finding**: give it its own numbered directory under `<topic>/flagged_cases/`
(e.g. `anthropology/flagged_cases/01-<slug>/`) containing `WRITEUP.md`, both source PDFs, and
`compare_two_papers.py`'s output already appended into the writeup per above. Keep each `WRITEUP.md`
self-contained — no references to other cases, to the review process, or to corpus-internal concepts
(LSH, `potential_dupes`, `ai_check`) a reader outside this project wouldn't recognize; state findings
and their DOI links directly. Update the directory's `README.md` index (severity table) as cases are
added, corrected, or removed. **If a case turns out to be same-author self-reuse rather than genuine
cross-author duplication** (a real instance this session), pull it out of the numbered case list
entirely and fold the lesson into the README's cross-cutting-lessons section instead — a corrected case
still worth remembering, but not one that belongs alongside real cross-author findings.

**Case-numbering collisions across concurrent subagents**: multiple subagents working different
partitions of the same backlog *will*, some fraction of the time, independently confirm the same
paper pair and each give it a fresh case number — "check the highest existing number" isn't a real
guard against this, since two agents can both read the directory before either has written its own
new case into it. Confirmed for real: a 6-subagent computer-ethics pass produced three duplicate
write-ups this way (cases 26/27/28 each turned out to be an already-existing case, 10/17/19, just
with the two papers swapped). The only real check is comparing DOIs, not case numbers or titles —
after any concurrent pass, diff every new case's DOI pair against every existing case's before
trusting the count, and remove/merge any duplicate rather than leaving a numbering gap unexplained
(a gap is fine and expected once you've done this — note it in the README so a later pass doesn't
"helpfully" try to fill it back in).

**A subagent that fails with "Server error mid-response" mid-task** is a transient platform issue,
not a sign the task itself is broken — confirmed for real: 4 of 6 concurrently-dispatched subagents
in one pass hit this independently, each at a different point in its own progress, and 0 of 6 did in
a separate same-session dispatch. Resume it (`SendMessage` to the same agent id, asking it to check
its own already-committed DB state before continuing) rather than relaunching a fresh agent for that
partition — `--agent-verdict` writes commit immediately per row, so a resumed agent that re-checks
the database first loses no completed work and doesn't redo it either.

## Phase 4: feeding real findings back into the pipeline

This is the step that keeps the *next* retrieval batch from regenerating the same noise a review pass
just spent time triaging. Two different feedback targets depending on what was found:

**A recurring boilerplate/citation pattern** (a publisher disclaimer, a citation-manager output format,
a survey-instrument's fixed wording — anything that will keep showing up in new papers, not a one-off):

1. Add a `(pattern, label)` tuple to `TEXT_PATTERNS` near the top of `classify_dupes.py`. Follow the
   existing ~90 entries' style — one distinctive phrase or a tight regex, not the whole passage — and
   leave a one-line comment on how/when you found it. Spot-check the regex against a few known-good
   paragraphs before committing so it isn't so broad it'd catch real prose.
2. Apply it retroactively, in order:
   ```bash
   python3 classify_dupes.py --library-db <corpus>/library.sqlite3
   python3 embed_paragraphs.py --library-db <corpus>/library.sqlite3 \
       --paragraphs-file <corpus>/paragraphs.jsonl --reclassify-existing-boilerplate
   ```
   The first sets `ai_check='no'` on existing matching rows (only touches `ai_check IS NULL`, never
   overwrites a prior verdict). The second finds already-embedded paragraphs newly matching the pattern,
   clears their embedding/LSH-bucket rows, and converts them to the `skipped-boilerplate` sentinel — so
   they stop generating candidate pairs against anything else in the corpus **going forward**, for every
   future retrieval batch, not just this one. This is the actual point: a one-off `review_dupes.py`
   `b`/`c` press only fixes rows that already exist; this is what stops them from being generated again.

One shared list feeds both `classify_dupes.py` (existing-row filtering) and `embed_paragraphs.py`
(pre-embedding skip filter via `classify_dupes.classify_text_patterns_only()`) — editing it once in
`classify_dupes.py` is enough, both mechanisms read the same source.

**A false positive that isn't a boilerplate pattern** (a genuinely one-off coincidental match, or a
same-paper-cataloged-twice situation): doesn't need a `TEXT_PATTERNS` entry. `f` (false_positive) or `p`
(papers-are-the-same, see `REVIEWING.md`) in `review_dupes.py` is the complete fix — no retroactive step
needed since there's no recurring pattern to prevent.

**A boilerplate family that recurs in reworded/paraphrased form** (no single fixed string to regex on):
not a job for `TEXT_PATTERNS` — that's what the ML boilerplate-family classifier
(`train_boilerplate_family_classifier.py`) exists for. Flag it for a retrain rather than trying to force
a regex to cover something it structurally can't.

## Session hygiene, for whoever's actually writing code during any of this

Before considering a batch of script edits done: run `pyflakes` across whatever you touched (unused
imports and dead locals are cheap to introduce mid-edit and cheap to catch), and run
`python3 tests/run_unit_tests.py` (fast, no network, should stay green). A real example from this
session: a "let me also fix the other flagged script" edit went in *before* its unused variable was
double-checked, and it turned out to be genuinely dead (safe to remove) only after tracing where the
value it used to hold now gets set instead — don't assume "assigned but never used" always means "safe
to delete blind," trace it first.

If you're maintaining a project-local Claude Code hook (`.claude/hooks/`), remember it's just a Python
script subject to the same bugs as anything else — this session's `require_api_keys.py` had a real
false-positive bug (plain substring match against the whole Bash command text, so a `grep` that merely
mentioned a gated script's filename got blocked as if it were running it) that went unfixed for a while
because the workaround (use `Read` instead of `Bash` for those specific inspections) was easy enough not
to force the issue. Worth periodically re-testing a hook against real command examples, not just
trusting it once it stops visibly misfiring.
