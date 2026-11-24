# Finding copied papers across a field from abstracts alone

Most ways of finding plagiarism start from a suspect paper, or need a full-text index of everything. This
starts from neither. It uses the abstracts that OpenAlex publishes for most of the literature:

1. **Harvest** every abstract OpenAlex has for a field (a set of topics or concepts).
2. **Pair** the works whose abstracts share a run of 20+ consecutive identical words.
3. **Filter** out the innocent explanations, which are about 98% of the matches: the same work recorded twice,
   translations, preprints, shared authors, OpenAlex errors, a book's blurb on every chapter.
4. **Measure** the full text of what's left: download both PDFs and count the words they share.
5. **Read** the survivors, which is the agent's (or your) job, and decide which are copying.

A whole field costs about $0.15 of OpenAlex credit and a few minutes of compute.

| Path | What it is |
|---|---|
| `find-copying-from-abstracts/SKILL.md` | The method, written as an agent skill: running the scan, reading the leads, reporting. Readable by people too. |
| `find-copying-from-abstracts/scripts/abstract_scan.py` | The scan: `harvest`, `pairs`, `fulltext`, `report` |
| `find-copying-from-abstracts/scripts/shared_runs.py` | Measures the text two documents share, for checking a lead by hand (standard library only) |
| `find-copying-from-abstracts/scripts/preprint_doi_prefixes.tsv` | DOI prefixes of preprint servers, so a preprint and its published version aren't counted as copying |

**Requirements:** Python 3 with numpy, `pdftotext` (poppler-utils), and ideally a free OpenAlex API key in
`OPENALEX_API_KEY`. **To use it with Claude Code,** copy `find-copying-from-abstracts/` into
`~/.claude/skills/` and ask, for example, "sweep bioethics for copied papers". With another agent, give it
`SKILL.md` as instructions. Or run the script yourself:

```bash
S=find-copying-from-abstracts/scripts/abstract_scan.py
python3 -I $S harvest  --dir scans/bioethics --filter "topics.id:T13147|T14307" --filter "concepts.id:C188084074"
python3 -I $S pairs    --dir scans/bioethics
python3 -I $S fulltext --dir scans/bioethics --limit 100
python3 -I $S report   --dir scans/bioethics      # -> scans/bioethics/leads.csv
```

## Results from two fields

| | Bioethics | Computer ethics |
|---|---|---|
| Works with an abstract | 279k | 343k |
| Pairs sharing a 20+ word abstract run | 46,946 | 56,156 |
| Leads left after filtering | 699 | 921 |
| Leads where both PDFs downloaded and were measured | 47 | 127 |
| Real full-text overlap (300+ shared words) | 14 (30%) | 56 (44%) |

- **Precision.** About a third of measured leads share real body text. For comparison, pairs found by a
  full-text 5-gram index and then filtered on their abstracts reached 53% in the same project.
  - Some real overlaps are still innocent on reading: self-reuse under a differently written name, or an
    agency's draft and final report.
- **New cases.** Only 9 of 463 bioethics leads had been found by the full-text index.
- **Where the copying is.** Mostly near-total copies (60-95% of both papers) in predatory and regional
  journals. Western-authored copying was rare: 5 confirmed pairs across 620k works. They were:
  - an editorial that was three-quarters an uncited NEJM review, later republished with a co-author added;
  - another group's survey study republished under a new title;
  - a law article taking prose from an uncited master's thesis;
  - an abstract and consent passages from an uncited regional paper.
- **The bottleneck is downloading.** Only about a quarter to two-fifths of leads with open-access links on
  both sides could be fetched automatically.

## What the filter misses, and what reading catches

The automatic classes remove most of the noise. Of the leads that remain, the innocent ones were mostly:
- OpenAlex showing one work's abstract on another where both titles fit the topic;
- both papers quoting the same source text (the Declaration of Helsinki, the Nuremberg Code, a statute);
- self-reuse under a reordered or changed name, or a byline missing from OpenAlex;
- one project's two outputs (a thesis and its article, a draft and final report);
- book introductions and chapters;
- a single shared stock sentence.

`SKILL.md` lists these and the checks that settle a lead: both full texts, direction and dates from the
PDFs themselves, bylines from the PDFs, and whether the earlier work is credited.

## Limits

- **Copiers who write their own abstract are invisible.** The thesis case above was found only because a
  stretch of its abstract matched too. To target a particular group of authors, start from their papers'
  full text instead.
- **Coverage.** About half of a field's works have no abstract in OpenAlex.
- **Metadata errors.** OpenAlex years, bylines, affiliations and abstracts are sometimes wrong. Every finding
  has to be checked against the PDFs.
- **Floors, not totals.** The full-text figure counts near-verbatim runs only, so reworded copying isn't
  measured.

## Changes from the original run

Differences from the run whose results are above:
- Full text is extracted with `pdftotext` and measured with 10-word runs, merging gaps of up to 3 words.
  The original used PyMuPDF and a slightly different run extension. On the 52 bioethics pairs both
  measured, the two versions agree on all 14 real overlaps and on 30 of the 33 that weren't. The 3 that
  changed were near the 300-word line, or had text that PyMuPDF had failed to extract.
- Preprint DOI rules now respect each server's suffix pattern. The original treated every Wiley, Nature,
  SciELO and JMIR DOI as a preprint, which hid about 510 of the 46,946 bioethics pairs (28 of them leads)
  as "same work".

---

*Run in November 2026. Paper titles, authors and DOIs are left out of this write-up on purpose: a public
document shouldn't name papers as copied without the full evidence beside each one.*
