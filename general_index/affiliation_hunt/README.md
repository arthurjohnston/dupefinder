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

## The unobtainable pool: what OpenAlex alone can rule out (2026-10-27)

Pairs the full-text runs tried but can't complete (a missing side is no_oa / 404 / DOI not found, not a 403 or a
landing page). Which of them are self-reuse or the same paper, without the PDFs?

| script | does | output |
|---|---|---|
| `unobtainable_fetch.py $D [MAX_CALLS]` | builds the pool (~10 min join), fetches OpenAlex authorships for its DOIs (cached in gi.sqlite3) | `unobtainable_pool.pkl` |
| `unobtainable_screen.py $D` | calibrates each signal on the checked real-overlap pairs, scores the pool with top30_rank.py's model, flags each pair | `data/unobtainable_openalex_screen.tsv` |
| `unobtainable_coauthors.py $D [MAX_CALLS] [MIN_P]` | joint works between the two sides' authors, one OpenAlex call per surviving pair, best score first; rerun the screen after | `unobtainable_coauthors.json` |

Eliminated = shared author id, a name in any word order ("Jia Li Wei" = "Liwei Jia", which OpenAlex splits into two
ids), surname + first initial, 2+ joint works, or a JPT synopsis of an SPE paper. On 1,406 checked real-overlap pairs
the author signals caught 417 of 613 self-reuse/same-paper pairs and none of 793 cross-author cases. NOT eliminated:
same title with different authors (it also holds whole papers republished under other names: worth reading as leads)
and shared institution alone (would lose ~3% of real cases).
