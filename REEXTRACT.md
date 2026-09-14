# Re-running extraction on the existing corpus

`extract_papers.py` switched from `pdftotext` to PyMuPDF for pulling text out
of PDFs (see its module docstring / CLAUDE.md for why: `pdftotext` was
merging whole pages into oversized "paragraphs", and `pdftotext -layout`,
tried as a fix, garbled two-column papers instead). The ~7,810 papers already
downloaded into `papers/` were extracted with the *old* logic, so
`library.sqlite3`/`paragraphs.jsonl` still hold the old, oversized paragraphs
until extraction is re-run against them.

This is all local recomputation -- no network access, nothing gets
re-downloaded.

## Steps

Run these in order from the project root:

```bash
# 1. Re-extract every paper's paragraphs/citations with the new PyMuPDF-based logic.
#    --recompute forces a full re-extract of every downloaded paper (not just new
#    ones) and rewrites paragraphs.jsonl from scratch, since virtually every
#    paragraph's text/boundaries are changing, not just a handful.
python3 extract_papers.py --recompute

# 2. Re-embed. No flag needed: embed_paragraphs.py already detects that a
#    paragraph's text changed at a given (paper_id, para_index) and re-embeds
#    it automatically -- since paragraph boundaries changed almost everywhere,
#    this will end up re-embedding almost everything anyway, just without you
#    having to force it.
python3 embed_paragraphs.py

# 3. Rebuild the persisted candidate-duplicate table from the new embeddings.
python3 build_dupe_candidates.py

# 4. Optional: an ad-hoc console report instead of / in addition to step 3.
python3 find_duplicates.py --cross-paper-only
```

## If it gets interrupted

- Step 1 (`--recompute`) is **not** incremental -- it always processes every
  downloaded paper and truncates `paragraphs.jsonl` at the start. If it's
  killed partway through, it's safe to just run the same command again (paper
  rows already written to `library.sqlite3` get overwritten, not duplicated),
  but it starts over rather than resuming where it left off.
- Steps 2-4 are all incremental/idempotent as normal -- safe to kill and
  re-run with no flags, they'll just pick up whatever's left.

## Sanity check first (optional but recommended)

```bash
python3 tests/run_tests.py --email you@example.com
```

Confirms the known plagiarism case still gets flagged and the negative
control still doesn't, before spending time on the full corpus. Downloaded
test-case PDFs are cached under `tests/work/<case>/` and reused across runs,
so this doesn't hit the network either after the first time.
