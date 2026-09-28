# sourcing/ — build a large, open-access starting list from OpenAlex

`starting.json` at the repo root is a hand-written list of a couple dozen papers. To seed the pipeline
with thousands of papers from one field instead, these three scripts search OpenAlex, keep only the works
that have a legally open-access copy, and write a `retrieve_papers.py`-compatible starting list.

Nothing here downloads PDFs. `retrieve_papers.py` does that afterward.

```
queries.txt --[collect_candidates.py collect]--> output/candidates.jsonl
                                                        |
                                          [enrich_open_access.py]  (adds is_oa / oa_url / oa_alt_urls in place)
                                                        |
                                          [build_oa_starting_list.py]
                                                        |
                                                        v
                                       output/starting_oa_candidates.json  --> ../retrieve_papers.py
```

## Requirements

Only `requests` (`pip install -r requirements.txt`, or reuse the repo root's venv, which already has it).
A free OpenAlex API key (openalex.org/settings/api) is strongly recommended. Anonymous traffic gets
rate-limited after a few thousand requests. Set it as an env var:

```bash
export OPENALEX_API_KEY=...
```

`enrich_open_access.py` also reads the key from a file named `openalex_api_key` in this directory. That
file is gitignored.

## 1. Choose the search queries

`queries.txt` holds one OpenAlex full-text search per line. `#` starts a comment. The shipped list covers
computer ethics and nearby topics. Edit it to define your own field. Field boundaries matter more than
any threshold below, so read a sample of the output before scaling up.

## 2. Collect candidates

```bash
cd sourcing
python3 collect_candidates.py collect                          # <=5,000 results per query, relevance score >= 4
python3 collect_candidates.py collect --max-per-query 1000 --min-score 5   # smaller, higher-precision run
python3 collect_candidates.py collect --from-year 2000 --to-year 2009      # scope to one era
```

Writes `output/candidates.jsonl`, one line per DOI-bearing work:

```json
{"doi": "10.1234/example", "openalex_id": "https://openalex.org/W...", "title": "Example title",
 "year": 2024, "cited_by_count": 17, "relevance_score": 8,
 "relevance_reasons": ["strong:ai ethics", "strong-title"], "matched_queries": ["AI ethics"]}
```

**Relevance scoring** is a keyword heuristic you can read in the code: points for explicit phrases
(`computer ethics`, `algorithmic fairness`, ...), for ethics and computing vocabulary, and for a strong
phrase in the title. It subtracts points for unrelated ethics fields (bioethics, business ethics, ...)
when no strong computing phrase is present. Raise `--min-score` (e.g. 6) for precision, lower it (e.g. 3)
for recall. Check `relevance_reasons` on a random sample of rows to see what's getting through.

**Response cache:** every raw OpenAlex page is cached under `output/openalex_cache/`. A re-run replays the
cached pages, including a re-run with a different `--min-score`, and fetches only pages that aren't cached
yet. Raising `--max-per-query` therefore resumes each query where it left off. `--cache-dir PATH`,
`--no-cache` and `--refresh-cache` override this.

## 3. Add open-access status

```bash
python3 enrich_open_access.py
```

Looks each candidate up by OpenAlex ID, 50 per request, and adds `is_oa`, `oa_status`, `oa_url` and
`oa_alt_urls` (every other hosted location that has a PDF link) to `output/candidates.jsonl` in place.
The original file is kept as `candidates.jsonl.pre_oa_enrich.bak`. Lookups are cached in
`output/open_access_cache.jsonl`, so a re-run after collecting more candidates only fetches the new ones.

## 4. Write the starting list

```bash
python3 build_oa_starting_list.py        # --state-db defaults to ../state.sqlite3
```

Writes `output/starting_oa_candidates.json`. The list contains only `is_oa=true` works, minus DOIs that
`retrieve_papers.py` has already downloaded or confirmed have no OA copy. `--exclude-transient-failures`
also drops DOIs that previously errored. Each entry includes the known OA URLs, so
`retrieve_papers.py` tries them before calling Unpaywall.

## 5. Retrieve

```bash
cd ..
python3 retrieve_papers.py sourcing/output/starting_oa_candidates.json --email you@your-institution.edu
```

Then continue with the normal pipeline (see the top-level README).
