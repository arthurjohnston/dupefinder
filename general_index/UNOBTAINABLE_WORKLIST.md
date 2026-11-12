# The unobtainable worklist: hand-downloading papers the pipeline couldn't get

The unobtainable list (`gi_western_csv.py unobtainable` -> `data/unobtainable_western_2026-10-30.csv`) is the
General Index copying pairs with a Western academic on the copying side whose full text the automatic
retrieval couldn't fetch (no open-access copy, archive rescue failed). Checking one needs both PDFs, so
this is a by-hand job, and there are far more pairs (~20,000 copying papers) than anyone will download.
Three scripts turn it into a ranked list to work down, and take whatever you download back into the
pipeline with no renaming.

```
data/unobtainable_western_2026-10-30.csv
        |
  [gi_unobtainable_sort.py]   score, filter, sort; blank the DOIs we already hold
        |   (uses gi_lead_score.py's model)
        v
  ..._by_band_prestige[_top30][_scoreS].csv + .xlsx      <- open the .xlsx, click DOIs / titles
        |
  you save PDFs, any filename, into data/unobtainable_downloads/
        |
  [gi_manual_import.py]   match each PDF to a needed paper, import, measure, write pages
        v
  fulltext rows (run manual_s16) + comparison pages in data/fulltext_pages_s16
```

All commands from `general_index/`, with `../.venv/bin/python3` (needs `openpyxl` for the .xlsx and
`scikit-learn` for the score, both in `requirements.txt`).

## 1. Make the worklist: `gi_unobtainable_sort.py`

```bash
../.venv/bin/python3 gi_unobtainable_sort.py                          # everything (~20k copying papers)
../.venv/bin/python3 gi_unobtainable_sort.py --top30                  # only Western top-30 universities
../.venv/bin/python3 gi_unobtainable_sort.py --top30 --min-score 0.5  # the short list (107 papers on 2026-11-11)
```

Each writes a `.csv` and an `.xlsx` with the same table: `data/unobtainable_western_2026-10-30_by_band_prestige.csv`,
with `_top30` and/or `_score0.5` added to the name by the options. **Open the .xlsx**: the links are real
cell hyperlinks there. LibreOffice doesn't evaluate `HYPERLINK()` formulas in an imported CSV, so the CSV
is plain text.

One row per academic (a paper with three Western co-authors has three rows, kept together, the most
prestigious university first). The first columns:

| column | meaning |
|---|---|
| `doi` | the copying (later) paper's DOI, a link to doi.org. **Blank if we already hold that PDF.** `gi:<dkey>` means the index has no DOI for it: find it by title |
| `copied_doi` | the copied (earlier) paper's DOI, same rules |
| `score` | how likely the pair is to turn out a medium/high-severity copying case (below) |
| `abstract_check` | `shared text` / `no shared text` / `not checked`: whether the two abstracts share a run of text |
| `title` | the copying paper's title; a Google search link when we still need it |
| `university` | this row's academic's university (OpenAlex name) |
| `copied_pct` | the copying paper's share of 5-grams the index found in the other paper |
| `title_a`, `title_b` | both titles, linked only when still needed |
| `have_pdf` | `neither` / `copying` / `copied` / `both`: which PDFs we already hold |
| `n_authors` | the copying paper's author count |
| `uni_h_index` | the university's OpenAlex h-index, the prestige measure |

So the non-blank DOI cells are exactly the PDFs still to get. Re-run the script after importing and
fetched papers drop out of them.

**Left out:** copying papers with more than `--max-authors` (default 5) authors (consortium and
multi-centre papers, where shared text is mostly a team reusing its own methods); pairs with a template
placeholder DOI (`10.1039/x0xx00000x`, RSC's article template, filtered upstream in `gi_western_csv.py`).

**`--top30`** keeps a paper if any of its academics is at one of the 27 Western universities in
`affiliation_hunt/top30_find.py`'s list (Tsinghua, Peking and NUS are on that list but never in a Western
list), matched on the OpenAlex name, including medical-school and health-system spellings
(`TOP30_NAMES`). It doesn't change what you can access: that depends on the publisher, not on the
author's university.

**Order:** by score in bands of 0.1, then within a band by the best university's h-index, then by score.

### The score: `gi_lead_score.py`

A gradient-boosted classifier (scikit-learn, depth 3, 300 rounds) trained on every tier 2/4 pair already
checked in full text with both PDFs: 4,143 pairs, of which 739 were real cross-author overlap that ended
up decided `problem` at medium or high severity (your review where there is one, the case agent's
otherwise). Features: the index's larger and smaller 5-gram overlap share, the shared 5-gram count, both
documents' 5-gram totals, the common-phrase share, and the abstract check.

5-fold cross-validated: AUC 0.886; precision 75% in the top 100, 71% top 300, 64% top 500, 54% top 1,000.
What it learned, in short:

- The abstract check is the strongest signal. A pair with shared abstract text is usually real.
- **Without** it, high index overlap mostly means a dud (60%+ overlap with no shared abstract text: 1 real
  in 828), so sorting by `copied_pct` alone puts the worst pairs first. That was this list's order before
  2026-11-11.
- Fewer shared 5-grams is more often real, not less: a huge shared count is usually two versions of one
  document or shared boilerplate.

The training pairs were picked by earlier runs, not at random from this list, so read the score as a
ranking, not a probability. Retrain after many new reviews:

```bash
../.venv/bin/python3 gi_lead_score.py export   # ~7 min: data/lead_score_training.tsv
../.venv/bin/python3 gi_lead_score.py cv       # cross-validated metrics
```

`gi_unobtainable_sort.py` fits the model from that file each time it runs (a second or two).

## 2. Download

Save each PDF you get into `data/unobtainable_downloads/` under whatever name the site gives it. No
renaming, no list to fill in.

## 3. Import: `gi_manual_import.py`

```bash
../.venv/bin/python3 gi_manual_import.py --dry-run   # show what each file would match, change nothing
../.venv/bin/python3 gi_manual_import.py             # import, measure, write pages
```

For each PDF it works out which needed paper it is, by, in order of trust:

1. a DOI in the PDF's metadata;
2. a needed paper's title near the top of page 1, confirmed by its DOI or an author's surname on the page
   (page 2 only if page 1 finds nothing, to get past cover sheets);
3. a needed DOI printed on the page (not inside the reference list).

A matched PDF is moved into `data/verify_manual_s16/papers/` and recorded (`state.sqlite3` there, plus
`local_pdfs` in `gi.sqlite3`). Every pair that now has both PDFs moves into run `manual_s16` (logged in
`data/manual_s16.moved.tsv`), and the script runs extraction, `gi_verify.py load-text` and `measure`, then
`gi_write_pages.py --run manual_s16` into `data/fulltext_pages_s16`. Real overlaps then go through the
usual review. `--no-measure` / `--no-pages` stop earlier.

Files it can't place stay in the folder and are listed in `data/unobtainable_downloads/_unmatched.md`
(scanned PDFs with no text layer, titles that differ from the index's).

Tested on 300 PDFs we already held, against all 38,682 needed papers: 253 matched correctly, 45 left
unmatched, 2 wrong. One was a same-author paper with a near-identical title; the other was a held PDF that
was itself the wrong paper.
