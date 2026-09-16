# Reviewing candidate pairs — a runbook for whoever (human or agent) is doing the manual pass

This is the practical companion to CLAUDE.md's pipeline description: once `potential_dupes` has rows
in it, something has to look at each candidate pair and decide what it actually is. This doc is that
"what to check, and which button/table to touch" guide. It assumes you've read CLAUDE.md's
`review_dupes.py` / `classify_dupes.py` / `text_overlap.py` sections already — this doesn't repeat the
mechanism descriptions there, just the judgment calls and the exact place each verdict gets written.

## Where to find candidates worth looking at

Two tools, different purposes:

- **`review_dupes.py`** — the interactive one-at-a-time reviewer. Use its filters
  (`--min-lcs-ratio`, `--cross-paper-only`, `--same-author-only`/`--different-author-only`,
  `--min-similarity`, `--author`/`--paper`) to narrow to a slice worth your time rather than paging
  through everything. `--summary` first, to see what's even in there.
- **`find_review_candidates.py`** — not interactive, prints a ranked list of still-unclassified
  (`ai_check IS NULL`, `same_paper=0`, `same_author=0`) rows for bulk triage, specifically to find
  **new boilerplate patterns** worth adding to `classify_dupes.py` (see below) rather than to review
  pairs one by one. `--sort lcs` surfaces contiguous-copying-shaped rows first; `--sort gap` surfaces
  the opposite profile (scattered matches, no one long run) once `lcs` mode's yield drops off. Read a
  sample (its docstring suggests batches of ~100), and look for a *recurring* shape across several rows
  — one paragraph that happens to overlap isn't a pattern, the same disclaimer/template/citation format
  showing up across many unrelated paper pairs is.

## What to actually check, per candidate

`review_dupes.py` shows you a word-diffed pair plus its stats. In order of what to look at:

1. **`lcs_ratio`** (longest verbatim run, as a fraction of the shorter paragraph) — the strongest single
   "this was copied" signal. High lcs_ratio + genuinely original-sounding prose (not a template) is the
   real-plagiarism profile.
2. **`ngram_jaccard`** (5-gram overlap) — catches copying spread across a paragraph without one long run
   (light paraphrasing, reordered sentences). High ngram_jaccard with *low* lcs_ratio is worth a second
   look even when lcs_ratio alone looks unremarkable.
3. **`similarity`** (cosine) alone is the weakest signal — it's semantic, not textual, and will happily
   score two paragraphs on the same narrow topic as 0.90+ with zero actual shared wording. Never confirm
   a dupe on similarity alone; always check the diff.
4. **Read the text.** Ask: is this prose someone wrote about their own findings, or is it a template/
   disclaimer/citation/legal-quote that many unrelated papers would independently contain verbatim?
   That single question is what separates a real dupe from boilerplate — see the pattern list in
   `classify_dupes.py` (`TEXT_PATTERNS`) for ~90 examples of the boilerplate side of that line, spanning
   copyright notices, competing-interest disclosures, PRISMA/SUS/Datasheets-for-Datasets templates,
   repository deposit-policy stamps, funding-acknowledgment boilerplate, and bibliography entries.
5. **`same_author`** — if 1, this is very likely self-reuse (a author quoting their own earlier work,
   a thesis chapter reused in a journal paper by the same person), which is a different, lower-severity
   finding than cross-author copying even when it's real overlap.
6. **Verify authorship against the source PDF directly — never trust `same_author=0` from the database
   alone.** `same_author`/`paper_authors` are derived from Crossref-resolved metadata (see CLAUDE.md), not
   from re-parsing either PDF, and a DOI's Crossref registration can simply omit a real co-author. This is
   not a hypothetical: a candidate in this corpus with `same_author=0` and an otherwise-compelling
   cross-author match turned out to share an author once the actual PDF bylines were read — the database's
   author list for one paper was just missing her (see a corpus's `flagged_cases/README.md`
   cross-cutting-lessons section for the full writeup of what that looked like, how it was caught, and
   why the case itself isn't listed as a finding — it was same-author self-reuse, not real duplication).
   So: **before treating any `same_author=0` candidate as confirmed cross-author** — not for every
   candidate, but for any one you're about to call `d`/`confirmed` or escalate — run `pdftotext <path> -`
   (or open the PDF) on *both* source PDFs and read the actual byline yourself. Check every name on both
   sides against every name on the other (including initials-only forms, e.g. "Smith A." vs "Smith.a" vs
   "A. S."), not just an exact string match — a name that looks different at a glance can still be the
   same person. Only once you've read both bylines directly does a "no shared name" conclusion actually
   mean no shared author. If the bylines do show a shared name `same_author` missed, press `a` (see the
   decision table below) rather than just noting it in a write-up — it corrects the database for every
   `potential_dupes` row between that pair, not just the one you're looking at.
7. **`later_cites_earlier`** — if 1, the later paper's reference list already names the earlier one, which
   changes "did they copy this" to "did they copy this *without* attributing the specific passage" —
   still worth flagging if it's a large uncited block, less clear-cut for a paraphrased sentence near a
   citation.
8. **Paper titles/years in the header** — if the two titles look like the *same paper* rather than two
   different papers, stop treating this as a content question — see "Same paper, cataloged twice" below.
9. **Once a candidate is narrowed down to exactly two specific papers worth a closer look** — i.e. you're
   about to call `d`/`confirmed`, escalate, or write it up — run `compare_two_papers.py` (see
   "Writing up a confirmed case" below) rather than relying only on the one paragraph pair
   `build_dupe_candidates.py` originally surfaced. It runs two independent, corpus-scale-infeasible checks
   (exact word-shingle matching and full sentence-by-sentence comparison) across the two complete
   documents, and has repeatedly found the real extent of overlap is larger — sometimes much larger —
   than the single flagged paragraph suggested (see any write-up in `anthropology/flagged_cases/` for
   real examples: one candidate flagged on a single matched abstract turned out to have 94.8% of the
   paper's words verbatim-identical once the whole document was checked).
10. **Verify each paper's `papers.title` actually matches its own extracted content — never assume the
    database's title field describes what's actually in that paper_id's paragraphs.** Found for real in
    `computer-ethics/flagged_cases/`'s now-retracted case 20: two papers shared the identical title
    "Financial Skills Among Faculty Members in Academia..." (the whole basis for the candidate), but one
    paper_id's *actual* extracted text turned out to be a completely unrelated ACCOUNTING paper — the
    "match" was never content duplication, just the two unrelated papers sharing IAEME's own journal-
    masthead boilerplate. A second confirmed instance (paper 78564, DB title "Co-Designing Ethical AI with
    Faith Communities...", real content a watermelon-disease paper) shows this isn't a one-off — it recurs
    specifically on identical-title pairs from low-scrutiny/fast-turnaround venues (IAEME, AMJSAI), the
    same venue family most paper-mill cases in this project come from, which is exactly where you're most
    likely to be reviewing this kind of candidate. Before writing up a case: read a couple of paragraphs
    from *each* paper_id's own extracted text (not just the matched passage) and confirm the subject
    matter is plausibly consistent with that paper_id's own title — a title/content mismatch here means
    the "duplicate" is a database bug, not a finding, regardless of how compelling the matched text looks.
11. **Before using a matched passage as evidence, check whether it's actually both papers quoting the
    same third party — not each other.** A literature-review or theoretical-framework section routinely
    quotes named scholars directly and at length ("Denzin (2008) summarizes the framework as follows:
    ...", "Goffman states, '...'"), and two papers covering the same theory will independently quote the
    *same* canonical passage from that same third source — real, expected, unremarkable overlap that
    proves nothing about one paper copying the other. Read the paragraph(s) immediately *before* the
    matched text, in both papers, for a named-author "states/argues/describes/defines/summarizes ... as
    follows:"-style lead-in, or literal quotation marks wrapping the matched span. If either paper frames
    it that way, that specific passage is weak evidence and shouldn't be used to support a `d`/`confirmed`
    verdict or a write-up, even if the shingle/lcs_ratio numbers on it look strong — go find the *next*
    matching passage that's each paper's own synthesis/commentary prose instead. Confirmed for real
    (2026-09-14, anthropology `flagged_cases/01`): a hand-picked "headline" example (the single longest
    exact match in the whole pair, 82 words) turned out to be both documents quoting Denzin (2008)
    verbatim, each with its own "(2008)... summarizes/summarises the interactionist framework as follows:"
    lead-in — and five more examples in the same write-up had the identical problem, all in that
    document's theory-heavy chapter. The overall finding still held (a shorter, less flashy synthesis
    sentence a few paragraphs later replaced each one, and the case's whole-document match-count/word-count
    totals were never affected since those come from the raw scan, not from which examples get quoted) —
    but every individually-quoted example had to be re-vetted one at a time before the write-up was
    trustworthy again. This check applies most to literature-review/theory prose; a matched passage
    reporting a paper's *own* empirical results (a Results section, a data table) is inherently not a
    third-party quotation and doesn't need this check.

## Decision table — verdict → keypress → what gets written where

All of this is in `library.sqlite3`'s `potential_dupes` table unless noted. Every action below is one
`review_dupes.py` keypress at the interactive prompt.

| What you found | Key | `status` set to | Extra effect |
|---|---|---|---|
| Real duplication — copied/lightly-edited passage, not boilerplate, not a shared citation | `d` | `confirmed` | None automatic — see "Writing up a confirmed case" below for the optional follow-up |
| Similar-looking but not actually overlapping text (same topic, different content/wording) | `f` | `false_positive` | None |
| Genuine reused boilerplate (disclaimer, template, license text, masthead, etc.) — see below for the "should this become a pattern" question first | `b` | `boilerplate` | **Cascades**: also marks every other `potential_dupes` row whose both paragraphs share this pair's LSH bucket cluster — see `mark_bucket_status()`. One press can resolve thousands of rows if the bucket is large. Overwrites any prior status on those rows. |
| Two papers independently citing the same source, rendered as near-identical formatted citation text | `c` | `citation` | Same cascade as `b`, via the same `mark_bucket_status()` |
| `paper_id_1`/`paper_id_2` are actually the *same underlying paper*, cataloged twice | `p` | *(unchanged)* | Sets `same_paper=1` and nulls `same_author`/`earlier_paper_id`/`later_paper_id`/`later_cites_earlier` on **every** `potential_dupes` row between those two paper ids (`mark_same_paper()`). Deliberately doesn't touch `status`/`reviewed_at` — it's a paper-identity correction, not a text verdict. |
| Two separate papers genuinely share an author `same_author`/`paper_authors` metadata missed (point 6 above) | `a` | *(unchanged)* | Sets `same_author=1` on **every** `potential_dupes` row between those two paper ids (`mark_same_author()`). Leaves `same_paper`/chronology/`status`/`reviewed_at` alone — it's an authorship-metadata correction, not a text verdict. |
| Not sure, want to decide later with fresh eyes | `u` | `unsure` | None |
| Don't want to decide right now, come back to it | `s` | *(left `unreviewed`)* | Row reappears next session |

## "Not a dupe, just boilerplate" — the two places this can go

This is the distinction the keypress table above doesn't fully capture, and it's the one worth getting
right: **pressing `b` only fixes the rows currently in `potential_dupes`.** It does nothing about the
same boilerplate text showing up in the *next* retrieval batch, because `potential_dupes` rows are
generated from embeddings that already exist — `b` never touches `embed_paragraphs.py`'s ingestion path
at all.

**Two questions, in order:**

**1. Is this a one-off, or a recognizable recurring pattern?**

If it's genuinely one pair of papers that happen to share a paragraph you don't expect to see again
(rare — most boilerplate is templated and recurs by construction), `b`/`c` in `review_dupes.py` is the
complete fix. Stop here.

If it's a *pattern* — a publisher's standard disclaimer, a citation-manager output format, a repository's
deposit-policy stamp, a survey instrument's fixed wording — it will keep generating new candidate rows
every time a new paper containing it gets retrieved and embedded. That's worth fixing at the source.

**2. Add it to `classify_dupes.py`'s `TEXT_PATTERNS` list.**

This is "add it to the ingestion" — and it's a single shared list that feeds *two* different mechanisms,
which is why editing it here is worth more than a one-off cascade:

- **`classify_dupes.py`** itself reads it to set `ai_check='no'` on any existing `potential_dupes` row
  whose text matches (via `classify_text()`), so `write_dupe_reports.py`'s `ai_check='yes'` filter never
  surfaces it.
- **`embed_paragraphs.py`** reads the *same* list (via `classify_dupes.classify_text_patterns_only()`,
  the same-file/same-regexes-only subset, no fallback heuristics) as its pre-embedding boilerplate skip
  filter (`skip_boilerplate_paragraphs()`). A paragraph matching a `TEXT_PATTERNS` entry is stored with
  `model='skipped-boilerplate'`, embedding left `NULL`, and **never becomes a `potential_dupes` candidate
  at all** — for every future retrieval batch, not just the current one.

Concretely: pick a distinctive, low-false-positive substring or short regex from the boilerplate text you
just saw (follow the style of the existing ~90 entries — most match on one distinctive phrase, not the
whole passage), add a `(pattern, label)` tuple to `TEXT_PATTERNS` near the top of `classify_dupes.py`,
and leave a one-line comment on how/when you found it (the existing entries do this consistently — it's
what makes the list auditable later). Before committing, spot-check the regex isn't so broad it'd catch
real prose — grep a few known-good paragraphs from the corpus against it if you're unsure.

**3. Apply it retroactively.**

Adding the pattern doesn't reach back and fix anything by itself — two follow-up runs, in order:

```bash
python3 classify_dupes.py --library-db <corpus>/library.sqlite3
```
Sets `ai_check='no'` on existing `potential_dupes` rows now matching the new pattern (only touches
`ai_check IS NULL` rows — never overwrites a prior human `status` or a prior `ai_check` verdict).

```bash
python3 embed_paragraphs.py --library-db <corpus>/library.sqlite3 \
    --paragraphs-file <corpus>/paragraphs.jsonl --reclassify-existing-boilerplate
```
One-off maintenance mode: finds already-embedded paragraphs that now match the new pattern, clears their
embedding and LSH bucket rows, and converts them to the `skipped-boilerplate` sentinel — so they stop
generating new candidate pairs against anything else in the corpus going forward, and existing
`potential_dupes` rows built from a now-orphaned embedding get cleaned up the next time
`build_dupe_candidates.py` runs (it deletes rows referencing a paragraph that no longer has an
embedding). Doesn't also run a normal embedding pass in the same invocation — this is worth doing once
after a batch of new patterns, not after every single one.

**When it's not a clean regex match** — a boilerplate *family* that recurs in reworded/paraphrased form
(no single fixed string to match on) rather than verbatim — that's what the ML layer
(`train_boilerplate_family_classifier.py`, see README's "The ML boilerplate classifier" section) is for,
not a job for `TEXT_PATTERNS`. That's a retrain, not a one-line edit; flag it rather than trying to force
a regex to cover it.

## Same paper, cataloged twice

If the two paper titles/years in the candidate header are clearly the same work (a preprint vs. its
published version, a duplicate retrieval from two sources), press `p` — see the table above for exactly
what it changes. If you expect this exact pair to keep recurring in future pipeline runs (e.g. the corpus
periodically re-retrieves from a source that always double-catalogs something), also consider whether
`find_duplicate_papers.py` — a standalone, safe-to-rerun-anytime maintenance script — would catch it
automatically going forward (it registers exact-DOI or exact-normalized-title matches in a
`duplicate_papers` table, and `build_dupe_candidates.py` consults that table on every future run). It's
deliberately conservative (unambiguous DOI/title matches only, capped at 3-member groups) — most
same-paper cases still need `p`'s manual judgment, this is only for the subset where the data alone
already proves it.

## Agent review mode

Everything above describes the judgment; this section is the mechanism for an agent (as
opposed to a human at a terminal) applying it at volume — e.g. a batch of subagents each
working through an assigned slice of the backlog.

**Agent verdicts get their own status vocabulary**, never the human one directly:

| Human status (`review_dupes.py` keypress) | Agent equivalent |
|---|---|
| `confirmed` (`d`) | `agent_decided_dupe` |
| `false_positive` (`f`) | `agent_decided_false_positive` |
| `boilerplate` (`b`) | `agent_decided_boilerplate` |
| `citation` (`c`) | `agent_decided_citation` |
| `unsure` (`u`) | `agent_decided_unsure` |
| *(`p`, papers-are-the-same)* | same as human — `mark_same_paper()` never sets `status` for either, nothing to distinguish |
| *(`a`, authors-are-the-same)* | same as human — `mark_same_author()` never sets `status` for either, nothing to distinguish |

This is deliberate, not cosmetic: an agent's verdict is a different trust level than a
human's, and writing directly into `confirmed`/`boilerplate`/etc. would make it impossible
to later tell which candidates still need a person to actually look at them. A human
filtering `--status confirmed` for a "what's been properly reviewed" list never sees an
unaudited agent call mixed in.

**Applying a verdict** (non-interactive, no tty needed):

```bash
python3 review_dupes.py --library-db <corpus>/library.sqlite3 --agent-verdict <potential_dupes id> <key>
```

`<key>` is one of `d`/`f`/`b`/`c`/`p`/`u`, same letters and same semantics as the interactive
prompt (including the `b`/`c` LSH-bucket cascade and the `p` same-paper correction — both
reuse the exact same underlying functions a human's keypress calls, just parameterized to
the agent status strings). Prints the candidate's diff and a confirmation line, then exits.

**Working an assigned batch**: pull your slice's candidate ids directly from
`potential_dupes` (e.g. `same_paper=0 AND same_author=0 AND ai_check IS NULL AND status='unreviewed' AND id % 10 = <your partition>`,
matching `find_review_candidates.py`'s own filter for "genuinely needs a look"), read each
one's paragraph text and stats (`sqlite3` CLI, or a short Python script against
`library.sqlite3` — `load_candidate_by_id()` in `review_dupes.py` gives the same row shape
`--agent-verdict` uses internally if you want to reuse it), apply the judgment framework
above, then call `--agent-verdict` once per id. Report back (don't apply yourself): any
recurring boilerplate/citation pattern you noticed that isn't already in `classify_dupes.py`'s
`TEXT_PATTERNS` — see "Add it to `classify_dupes.py`'s `TEXT_PATTERNS` list" above for why
that's a single shared list better consolidated in one pass than edited concurrently by
several agents.

**Three steps that are not optional before an agent calls `d`/`agent_decided_dupe` on a candidate**
(mistakes an agent made on exactly these three points, unnoticed until a human caught them by hand, are
why these are spelled out here rather than left implicit in "apply the judgment framework above"):

1. **Check the source PDF, not just the database, for authorship.** `same_author`/`paper_authors` come
   from Crossref metadata and can be wrong — see checklist item 6 above. Before calling a candidate
   cross-author, `pdftotext` both source PDFs and read the actual bylines yourself.
2. **Check names against each other explicitly, not just for an exact string match.** Compare every
   name on one byline against every name on the other, including initials-only and differently-punctuated
   forms of the same name (e.g. "Smith A." / "Smith.a" / "A. S." are the same person) — a same_author
   verdict should be based on having actually checked this, not on the absence of an identical string in
   two database rows.
3. **Run `compare_two_papers.py` on the two `paper_id`s before finalizing the verdict**, once a candidate
   is narrowed down to one specific pair of papers worth a real look (i.e. before `d`, not for every
   candidate in a batch) — see "Writing up a confirmed case" below for the exact command. Base the
   verdict, and any writeup, on what the whole-document comparison shows, not only the single paragraph
   pair that originally surfaced the candidate.

**Auditing agent verdicts** (human side): `review_dupes.py --agent-reviewed` browses exactly
the `agent_decided_*` rows instead of the default `unreviewed` ones — combine with the usual
filters (`--paper`, `--min-similarity`, etc.) same as any other session. Each one is shown
with a clear banner (`🤖 AGENT VERDICT (...)`) so it's obvious you're auditing, not reviewing
fresh; pressing any real verdict key overwrites it with your own (human) status, same as
overwriting any other prior status. `--summary` reports a separate "agent-reviewed (not yet
human-audited)" count so the backlog is visible at a glance.

## Writing up a confirmed case

A `d` verdict is enough by itself — `status='confirmed'` is durable and won't be touched by a re-run.
If the case is significant enough to want a shareable writeup (the way the Saxby/Taro case has one), two
steps come before writing prose, in order:

1. **Run `compare_two_papers.py` against the two papers' `paper_id`s, appending straight into the writeup
   file.** This is not optional for a writeup — the single paragraph pair that got a candidate flagged in
   the first place is only ever a sample of the real overlap, and this is the tool that checks the rest of
   both complete documents:

   ```bash
   python3 compare_two_papers.py --library-db <corpus>/library.sqlite3 \
       --paper-a <paper_id> --paper-b <paper_id> --append-to-writeup <case_dir>/WRITEUP.md
   ```

   It runs two independent methods and reports both together: exact 6-word word-shingle matching
   (`--shingle-size` to change the window) and full sentence-by-sentence embedding comparison
   (`--min-similarity` to adjust the first-pass filter). Base the writeup's claims about *scale* ("X% of
   the paper," "spans N of M paragraphs") on this output, not on the one originally-flagged paragraph.
   **Always use `--append-to-writeup`, not `--out`, for a writeup you intend to keep**: it appends the
   complete report — a DOI link for each paper plus every match either method found — directly onto the
   end of the `WRITEUP.md` file itself, under a "Full comparison output" heading, rather than leaving the
   evidence sitting in a separate file the writeup only references. That's deliberate, not a formatting
   preference: a separate `--out` file is easy to lose track of, easy to regenerate out of sync with what
   the writeup's prose claims, and easy for someone reading only the writeup to never see at all. Appending
   means the full findings travel with the document making claims about them, always. (`--out` still exists
   for a quick one-off look you don't intend to keep.)

2. **Verify both papers' actual bylines from the PDFs**, per the authorship-verification step above —
   confirm (or rule out) a shared author directly from the source PDFs before the writeup asserts either
   "same author" or "no author overlap," rather than repeating whatever `same_author` says.

Then run `write_dupe_reports_html.py` with `--ids`/`--title`/`--source-note` for that specific paper pair;
it produces a self-contained HTML fragment meant to be published as a Claude Artifact.
