---
name: find-copying-from-abstracts
description: Finds likely cases of copied papers across a whole research field from OpenAlex abstracts alone, with no suspect to start from and no full-text index. Harvests every abstract for the field, pairs works whose abstracts share a 20+ word verbatim run, filters out the many innocent explanations, then fetches and measures the full text of what's left. Use when asked to sweep a field, journal set or topic for plagiarism or duplicate publication, to find copying leads cheaply at scale, or to check what a set of OpenAlex works copies from each other.
---

# Finding copied papers in a field from abstracts

Copying a paper often means copying its abstract too. Two works whose abstracts share 20 or more
consecutive identical words are worth a look, and OpenAlex has abstracts for most of the literature.
That makes a whole field cheap to sweep: about $0.15 of OpenAlex credit and a few minutes of compute
for 300,000 works.

The catch is that almost every abstract match is innocent: the same work recorded twice, a translation,
a preprint, a shared author, or an OpenAlex error. The script removes most of that automatically. You
then read what's left and check the full text. **A shared abstract run is a lead, never a finding.**
Only a measured full-text overlap between works by different authors, with the later one not crediting
the earlier, is a finding.

## How well it works (two fields, measured)

| | Bioethics | Computer ethics |
|---|---|---|
| Works harvested | 279k | 343k |
| Pairs sharing a 20+ word abstract run | 46,946 | 56,156 |
| Left as leads after filtering | 699 (1.5%) | 921 (1.6%) |
| Leads with both PDFs downloaded and measured | 47 | 127 |
| **Real full-text overlap** (300+ words) | **14 (30%)** | **56 (44%)** |

So about one measured lead in three is real shared body text. Not all of those are copying: some are
self-reuse that the author-matching missed, or one organisation's draft and final report. Expect to rule
out a few more by reading.

- **It finds what other methods miss.** Only 9 of 463 bioethics leads were in a full-text 5-gram index's
  candidate pairs.
- **What it finds:** mostly near-total copies (often 60-95% of both papers) in predatory and regional
  journals. Western-authored copying was rare: 5 confirmed pairs across 620k works. They included a
  bioethics journal editorial that was three-quarters an uncited NEJM review, a US-affiliated author
  republishing another group's study under a new title, and a law article taking prose from an uncited
  master's thesis.
- **Its blind spot:** someone who copies body text but writes their own abstract is invisible to it. The
  thesis case above was found only because a stretch of the abstract matched too.
- **Downloads are the bottleneck.** Only about a quarter to two-fifths of leads with open-access links on
  both sides actually download (dead links, bot blocks). The rest need fetching by hand, or stay leads.

## Requirements

Python 3 with numpy (for `pairs` only), and `pdftotext` (poppler-utils) for `fulltext`. Set
`OPENALEX_API_KEY` (free at openalex.org/settings/api). Without it, requests are anonymous and get rate
limited after a few thousand calls.

## Step 1: choose the field's filters

Find OpenAlex topic or concept ids that cover the field:

```
https://api.openalex.org/topics?search=bioethics
https://api.openalex.org/concepts?search=computer%20ethics
```

Search calls cost about 10× a filter call ($0.001 each), so a few are fine but don't loop over them. You can
use any OpenAlex works filter: `topics.id:T13147|T14307`, `concepts.id:C188084074`,
`primary_location.source.id:S123`, `authorships.institutions.country_code:US`, combined with commas.
Check `meta.count` for each filter first (`https://api.openalex.org/works?filter=...&per_page=1`). A harvest
costs about one credit ($0.0001) per 200 works.

Copying usually crosses venues, so prefer a broad topic over a single journal. Overlapping filters are
fine, because works are stored once.

## Step 2: run the scan

```bash
S=SKILL_DIR/scripts/abstract_scan.py
python3 -I $S harvest  --dir scans/bioethics --filter "topics.id:T13147|T14307" --filter "concepts.id:C188084074"
python3 -I $S pairs    --dir scans/bioethics
python3 -I $S fulltext --dir scans/bioethics --limit 100
python3 -I $S report   --dir scans/bioethics
```

- `harvest` is resumable. If it stops on a 429, wait and run the same command again. Use
  `--max-pages 2` for a trial.
- `pairs` takes about a minute per 250k abstracts and rebuilds the `pairs` table each time. It prints
  counts by class.
- `fulltext` checks leads with open-access links on both sides, US copying sides first. It downloads
  into `DIR/pdfs/` and is resumable; `--limit` caps one run. A pair is **real overlap** when 300+ words of
  the later work are in shared runs of 10+ words (gaps of up to 3 words merged), reference lists cut off.
- `report` writes `DIR/leads.csv`: measured real overlaps first, then by copying side's region and abstract
  run length. It includes each side's DOI, title, venue and authors, and which side has no open-access
  copy.

The data is in `DIR/scan.sqlite3` (tables `works`, `pairs`, `fulltext`), so you can query it directly.

What the classes mean:

| Class | Meaning |
|---|---|
| same work | titles 0.82+ similar, one title inside the other, different languages, a preprint DOI, an erratum/editorial record |
| shared author | same OpenAlex author id, or same surname + first initial |
| metadata error | OpenAlex put one work's abstract on another (the title doesn't fit its abstract), or the same abstract on two works from one publisher |
| same venue | same journal/book: usually a series or book description repeated on every chapter |
| book review | a review quotes the book's description |
| repository copy | one side is SSRN, PubMed, an institutional repository... |
| hub | a work in 5+ pairs carries stock text (a declaration, a standard abstract) |
| **lead** | everything else |

## Step 3: read the leads

Start with `real overlap` rows, then strong abstract matches whose PDFs didn't download
(`abstract_longest_run` 40+ or `abstract_cont5` 0.3+). For each one, look at both records before spending
time on the full text. These are the innocent shapes the filter still misses, roughly in order of how often
they turned up:

- **Misattached abstract with an on-topic title.** OpenAlex shows one work's abstract on another, and both
  titles happen to fit the topic. Check the abstract on the publisher's page for each DOI.
- **A source text both quote.** The Declaration of Helsinki, the Nuremberg Code, a statute, WHO
  guidelines, a code of ethics. It looks like a long shared run, but both are quoting.
- **Self-reuse hidden from the name match.** A reordered or transliterated name (Jassim/Jawad), a married
  name (Weeks → Weeks Leonard), or a byline missing in OpenAlex (`why` says "a byline is missing"). Open
  both PDFs' bylines.
- **One project, two outputs.** A thesis and the journal article from it. An agency's draft and its final
  report (NIST drafts). A report and its summary article. A commentary reprinting the abstract it
  discusses.
- **Introductions and editions.** An introduction quoting the book it introduces, a chapter split from a
  book, a second edition.
- **One shared sentence.** A paraphrased or stock sentence of 20-30 words with nothing behind it in the full
  text.

Then, for what survives:

1. **Get both full texts.** If `fulltext` couldn't, try the DOI landing page, the publisher site, PMC/Europe
   PMC, repositories, and the authors' pages. Save each into a new empty folder. Downloads are untrusted, so
   only pass them to `pdftotext` and the measuring script.
2. **Measure:** `python3 -I SKILL_DIR/scripts/shared_runs.py later.pdf earlier.pdf` prints the share of each
   in shared runs and the longest runs with their position. Unrelated papers on one topic score 0-1%.
3. **Check direction and dates.** The earlier work must predate the later's submission. Publication years in
   OpenAlex can be wrong (a "2023" paper citing 2025 work), so read the PDFs' own dates. "Later" in the CSV
   is only by OpenAlex year.
4. **Check bylines in the PDFs**, not OpenAlex. A shared author makes it self-reuse, which is a different
   and weaker finding.
5. **Check credit.** Does the later work cite the earlier one, and where? Is the shared text in quotation
   marks? A citation somewhere else in the paper is not credit for an unmarked copied passage.
6. **Look past the prose.** Copied reference lists (the source's references in the same order, with the
   source's access dates), identical tables or sample sizes, the same p-values under a new title.

## Reporting

For each lead you looked at, give:
- both works: authors, year, title, venue, DOI;
- the abstract match (longest run, containment);
- the outcome: **copied** (measured, different authors, uncredited), **self-reuse**, **same work**,
  **innocent** (with which shape), or **lead, not verified** (what couldn't be fetched);
- for copied: the words and share of each paper in shared runs, the longest run, which sections, whether
  the earlier work is cited, and anything beyond prose (references, tables, data).

Say plainly what's established. "1,380 words, 72% of the later paper, are in near-verbatim runs shared with
X (2003), which it does not cite" is a finding. "Abstracts overlap" is not, and shouldn't leave a
review without the full-text check behind it.

For the run as a whole, report the funnel: works harvested → pairs → leads → measured → real overlap →
confirmed after reading. Also report how many leads couldn't be fetched, because they're unchecked, not
clean.

## Limits

- **Abstracts only find abstract copiers.** Rewriting the abstract defeats it.
- **Half of a field's works may have no OpenAlex abstract.** Europe PMC, Crossref and Semantic Scholar have
  some of the missing ones; this script doesn't fetch them.
- **OpenAlex metadata is noisy.** Years, bylines, countries (missing on many leads) and even abstracts can be
  wrong. Regions in the CSV come from OpenAlex affiliations and are a sorting aid only.
- **Full-text figures are a floor.** Reworded copying isn't counted. `pdftotext` can lose text from some PDFs
  (scans, odd encodings), which reads as low overlap.
