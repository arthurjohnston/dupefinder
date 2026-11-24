# Stage 2 verification prompt (one agent per group of 4-5 papers)

This is the prompt used to check papers on the web in the blind tests. Mix flagged and unflagged
papers in each group, and don't tell the agent which is which.

```
Read SKILL_DIR/SKILL.md and do **Stage 2** (find and verify the source; web search/fetch and downloads
allowed) for each of these papers. The skill's bundled script is at SKILL_DIR/scripts/shared_runs.py.
Read each paper's text in full first; you may also do a quick Stage 1 read of it to know what to
search for.
- PAPER_DIR/P088.txt  DOI ...
- ...
Rules: download into new empty folders under WORK_DIR; open nothing else (no keys or other results);
about 15 searches per paper.
As soon as each paper is done, append one JSON line to RESULTS_DIR/verify_g1.jsonl:
{"paper": "P088", "label": "copied" | "minor" | "lead, not verified" | "self-reuse only" | "not found",
 "sources": [{"title", "year", "doi_or_url", "authors_differ": true/false, "cited": true/false,
   "verified": "fulltext" | "abstract only" | "snippet only",
   "share": "shared_runs.py paper %, uncredited words after removing quoted/credited passages and
             references, sections"}],
 "searches_tried": N, "note": "one or two sentences in your own words"}
Use the skill's size grading for copied vs minor. Keep any quotes to 8 words or fewer; don't paste
passages into the file. Reply with the labels only.
```
