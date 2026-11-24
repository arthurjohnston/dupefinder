# Stage 1 screening prompt (one agent per batch of about 10 papers)

This is the prompt used for the blind tests. Replace the paths in capitals with your own.

```
Blind screening test. Rules:
- Read SKILL_DIR/SKILL.md and do **Stage 1 only** (no web search or fetch, no downloads, no scripts on
  the papers).
- Read in full, including reference lists, ONLY these files: PAPER_DIR/P001.txt through P010.txt. Use
  Read's offset/limit for long files. Open nothing else (no keys or other results).
- Keep any quote in "signs" to 8 words or fewer; otherwise describe in your own words.
- As soon as you've judged each paper, append one JSON line to RESULTS_DIR/batch_01.jsonl:
  {"paper": "P001", "p_copied": 0-100, "verdict": "likely copied" | "weak signs" | "not likely",
   "signs": [...], "note": "one sentence on what the copied part probably is, or why not"}
- Reply at the end with just the ten p_copied values.
```

Papers were plain text from `pdftotext PAPER.pdf PAPER.txt`, reference list included. Names were
anonymised (P001...), and the answer key was kept outside anything the agent could open.

Practical notes:
- **Write one line per paper as you go.** A safety classifier sometimes stops an agent partway
  through writing a whole batch's results about plagiarism, and per-paper lines lose at most one
  paper's result.
- **Keep quotes short.** Agents that paraphrased the signs were stopped less often than ones that
  quoted at length.
