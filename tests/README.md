# Tests

Two independent suites, covering different things:

- **`unit/`** -- true unit tests (pure logic + mocked network) for `retrieve_papers.py`,
  `text_overlap.py`, and `lsh_index.py`. No network, no `--email`, finishes in well under a
  second. Run with `python3 tests/run_unit_tests.py` (or `-v`). Plain stdlib `unittest`, no
  pytest dependency.
- **This directory's back-tests** (below) -- run the real pipeline end-to-end against
  known-outcome plagiarism cases, over the real network.

Run the unit suite on every change to the modules it covers; run the back-tests before
trusting a change to the pipeline's actual duplicate-finding behavior.

## Back-tests: known-outcome cases for find_duplicates.py

Validates the pipeline against ground truth before scaling up retrieval, per todo.md's
"back testing" item. Each case in `cases/*.json` is either a **positive** case (a real,
documented plagiarism pair -- find_duplicates.py must find it) or a **negative control**
(two unrelated papers it must *not* flag).

## Running

```bash
python3 tests/run_tests.py --email you@example.com                    # all cases
python3 tests/run_tests.py --email you@example.com --case carlini-roadmap-2022
python3 tests/run_tests.py --email you@example.com --keep-work        # inspect the DBs after
```

Each case gets its own isolated working directory (`tests/work/<case-name>/`, gitignored,
deleted after the run unless `--keep-work`) with its own `papers/`, `state.sqlite3`,
`library.sqlite3` -- cases never share state or touch the main library.

## Case format

```json
{
  "name": "...",
  "description": "...",
  "papers": [
    {"title": "...", "authors": [...], "year": ...},
    {"title": "...", "authors": [...], "year": ...,
     "manual": {"pdf_url": "...", "doi": "...", "oa_status": "...", "reason": "..."}},
    {"title": "...", "authors": [...], "year": ...,
     "manual": {"local_path": "tests/fixtures/...", "doi": "...", "oa_status": "...", "reason": "..."}}
  ],
  "assertions": [
    {"type": "min_similarity", "pair": ["title A", "title B"], "threshold": 0.80},
    {"type": "max_similarity", "pair": ["title C", "title D"], "threshold": 0.70}
  ]
}
```

A `"manual"` block is only needed when `retrieve_papers.py` can't resolve the paper itself
(the same situation as CLAUDE.md's retrieve_papers.py "Known gap" -- Crossref missing an
arXiv/PMLR preprint, or the publisher blocking automated downloads outright). It takes one of
two forms:

- `"pdf_url"`: `run_tests.py` fetches it directly over HTTP and records it in `state.sqlite3`
  exactly as a human would after manually verifying the PDF, via `retrieve_papers.py`'s own
  `PaperStore`/`download_pdf`.
- `"local_path"` (repo-root-relative, e.g. `tests/fixtures/foo.pdf`): copied in with no
  network call at all. Supported for a PDF that had to be fetched by hand outside the
  pipeline (e.g. blocked behind a bot-check, or -- like a dissertation -- has no DOI/Crossref
  record to even attempt), but **not currently used by any case in this repo**: every case's
  PDFs are third-party copyrighted works this project doesn't hold redistribution rights to,
  so all of them now use `"pdf_url"` instead and are fetched fresh over the network each run
  rather than committed to `tests/fixtures/`.

## Current cases

- **carlini-roadmap-2022** (positive): "A Roadmap for Big Model" (arXiv:2203.14101) lifted
  paragraphs from "Deduplicating Training Data Makes Language Models Better"
  (arXiv:2107.06499) in Section 2.3.1. Discovered by Nicholas Carlini (a coauthor of the
  original); the survey was withdrawn 12 days after his write-up, citing that exact section.
  Credited in `thankyou.md`. find_duplicates.py recovers it at 0.80-0.90 cosine similarity.

- **negative-control-unrelated-fairness-papers** (negative): two legitimate, unrelated
  papers from the main corpus ("Equality of Opportunity in Supervised Learning" vs.
  "Gender Shades") -- the empirically lowest cross-paper similarity in the whole 11-paper
  corpus (0.540), asserted to stay under 0.70. Guards against false positives, the way the
  positive case guards against false negatives.

- **saxby-taro-2023** (positive): "Taro Roots: An Underexploited Root Crop" (Ferdaus et al.,
  MDPI *Nutrients* 2023) copied passages near-verbatim, uncited, from Solange Saxby's 2020
  PhD dissertation (U. Hawai'i at Manoa). Saxby discovered it in 2024 and pushed for
  retraction/correction; see the `source_url` in the case file (Retraction Watch). Both PDFs
  are bot-walled from automated fetch (MDPI/PMC serve a JS proof-of-work challenge;
  ScholarSpace was fetched by hand instead), so this case pins each one's exact `"pdf_url"`
  by hand rather than relying on DOI resolution -- fetched fresh over the network each run,
  not checked into the repo, since neither is this project's to redistribute.
  find_duplicates.py recovers it at 0.877 cosine similarity.

- **aygun-tarhan-marder-spacetime-2006** (positive): "Energy Momentum Localization in Marder
  Space Time" (Aygun, Aygun & Tarhan, arXiv gr-qc/0607102, 2006) was removed by arXiv
  administrators, who stated directly it "plagiarizes hep-th/0308070, gr-qc/9910015, and
  others" -- both by S.S. Xulu, with no author overlap with Aygun/Aygun/Tarhan (genuine
  cross-author plagiarism). All three arXiv PDFs are pre-withdrawal versions, pinned by hand
  via `"pdf_url"` (the actual withdrawn version 404s) and fetched fresh over the network each
  run rather than committed to the repo. find_duplicates.py recovers it at 0.845-0.888 cosine
  similarity. See `manual_examples/README.md` for the fuller writeup and how this was found.

- **yilmaz-aygun-topological-defect-2005** (positive): "Topological defect solutions in the
  spherically symmetric space-time admitting conformal motion" (Yilmaz, Aygun & Aygun, arXiv
  gr-qc/0607104, *Gen. Rel. Grav.* 37 (2005)) was withdrawn by arXiv for "excessive overlap
  with ... papers also written by the authors or their collaborators" -- same-author
  (Ihsan Yilmaz) reuse across a different coauthor lineup, not cross-author theft, unlike the
  case above. find_duplicates.py recovers it at 0.997 cosine similarity (a near-exact
  duplicate paragraph). See `manual_examples/README.md`.

## Candidates that didn't make it in (bot-walled, not fetchable without a real browser)

Found but couldn't add -- worth revisiting if browser automation (e.g. claude-in-chrome)
becomes available, or if fetched by hand:

- **  ** vs. **Haque & Chiang's McMaster thesis /
  Procedia Computer Science paper** (DOI 10.1016/j.procs.2019.09.277) -- IJACSA confirmed
  "Level 1 plagiarism – uncredited verbatim copying of a full paper" after PhD student
  Enamul Haque reported it; near-100% overlap expected (would be a much cleaner >0.95
  positive case than Carlini's ~0.90). The retracted IJACSA paper downloads fine
  (https://thesai.org/Downloads/Volume12No4/Paper_76-A_Data_Science_Framework_for_Data_Quality_Assessment.pdf),
  but ScienceDirect returns HTTP 403 to every automated fetch attempt despite Unpaywall
  confirming it's genuinely gold OA (CC-BY-NC-ND). 
  *** Human edit. The retracted version just says retracted
