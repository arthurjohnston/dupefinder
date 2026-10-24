# affiliation_hunt: one-off analyses behind the top-30 and US/European batches (2026-10-21/22)

Not part of the pipeline: scripts written to answer two requests (cases involving a top-30 university;
more US/European cases). They read `data/gi.sqlite3` read-only and keep their inputs/outputs in
`data/affiliation_hunt/` (gitignored: it names authors in unverified suspicions). Every script takes that
directory as its first argument (`D=data/affiliation_hunt` below); run from `general_index/`.

| script | does | output |
|---|---|---|
| `top30_find.py $D/top30.pkl` | authors currently at a top-30 university (ORCID / OpenAlex last known), the pairs they're on | `top30.pkl` |
| `top30_rank.py $D/top30.pkl $D/top30_ranked.pkl $D/top30_years.pkl` | trains the copying model (gradient-boosted trees on full-text-checked pairs, CV AUC 0.90) and scores those pairs | `top30_ranked.pkl` |
| `top30_manual.py $D` | blocked/landing-page PDFs that would complete a top-30 pair -> hand-download list | `data/verify_top30_s16/manual_downloads/` |
| `top30_coauthors.py $D` | joint OpenAlex works between the two sides of each listed pair | `coauthors.json` there |
| `useu_find.py $D` | leads whose copying paper prints a US/European institution on its own byline (cached authorships), no shared author id/surname; scored with the same model | `useu_ranked.pkl` |
| `useu_screen.py $D N` | first N of those with no co-authorship between the sides (one OpenAlex call per pair, cached) | `useu_coauthors.json`, `data/useu_pairs.txt` |

`run_useu_loop.sh` (in `general_index/`) chains `useu_screen.py` and `run_useu_batch.sh`.
Lessons: name-matched "current affiliation" is mostly OpenAlex name merges for common names, so use the
affiliation printed on the paper; and the model's top pairs are often self-reuse under a differently written
name or between collaborators, so screen both before downloading.

- `useu_manual.py D [MAX_PAIRS]` — the hand-download list for the US/European runs (useu*_s16): pairs a browser
  download would complete (blocked or landing-page sides, not paywalled/404), one PDF missing first, then model
  score. Writes `data/useu_manual_downloads/{_pending.md,_pending.html,_manifest.json}`; each manifest entry names
  its run so imported PDFs go back to that run's workdir. Reads `useu_ranked_r1.pkl` (the list rounds 1-2 ran
  from) as well as `useu_ranked.pkl`, since the rebuild after rest4 dropped already-checked pairs.
