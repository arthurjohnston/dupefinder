---
name: screen-for-copied-text
description: Screens an academic paper for text copied from other authors' work, from the paper's own text alone, then traces and verifies the source on the web. Use when asked whether a paper (PDF or text) is plagiarised or contains copied text, to triage a batch of papers for likely copying, or to find where a suspicious paper's text came from. Does not need a suspected source to start, and needs no code beyond the bundled stdlib script and pdftotext.
---

# Screening a paper for copied text, then finding the source

Two stages. **Stage 1** reads the paper and judges, from internal evidence only, whether it likely
contains copied text. **Stage 2** (only for papers Stage 1 flags) finds the source on the web and
measures the overlap. Only Stage 2 establishes anything. Stage 1 says "worth a search", never
"this paper is plagiarised".

## How well this works (measured, so you can say it honestly)

Tested blind on 100 papers (2026-11-23): 20 later articles from human-confirmed copying cases plus 80
controls from the same kind of venues (many low-scrutiny journals: Indian, Pakistani, Chinese,
Nigerian, Indonesian, IJERT/IOSR-type), with mainstream physics, biology and medicine papers mixed in.
Stage 1 was run with no web access. Seven of the 12 "controls" it flagged turned out, on Stage 2, to
have an uncited source by other authors (full text compared), so the labels were corrected:

| Stage 1 threshold | caught | false flags | precision |
|---|---|---|---|
| p ≥ 50 | 23 of 27 | 5 of 73 | 82% |
| p ≥ 70 | 20 of 27 | 3 of 73 | 87% |

**Held-out test, with this skill as written** (50 papers not used to write it: 5 confirmed copying
cases and 45 controls, run the same way):
- **Stage 1 at p ≥ 50:** flagged all 5 positives and 7 controls. Two of those controls turned out
  copied (35% from a clinical guideline it names but doesn't reference; about 10% from two papers),
  one has an unverified lead, and four were false flags. That's 7 of 7 caught, 4 of 43 falsely
  flagged, precision 58-67%.
- **Stage 2:** found and measured the known source for all 5 positives, each 43-75% of the paper.
  It reported "not found" for all four false flags and for four random unflagged controls (three of
  which only had self-reuse). Across both runs, Stage 2 never called a paper copied without a
  measured full-text source. Stage 2 is the safeguard: Stage 1 alone overflags.
  Under the size grading below, which was added after this test, one of those four would now read
  "minor" instead of "not found": two uncredited sentences that were really from another paper.

**Second held-out test, with the "minor" and credited-text rules** (100 new papers, meant as 5
copied to 95 clean):
- **Stage 1 at p ≥ 50:** flagged all 5 known copies. These included partial ones: 9% scattered
  through the paper scored 55, and 16% scored 80. It also flagged 7 "clean" papers.
- **Stage 2:** 5 of those 7 had uncited sources by other authors (9-34% of the paper). That makes
  10 of 10 copied papers caught and 2 of 90 falsely flagged, 83% precision.
- **Unflagged papers:** the 6 random ones checked were clean. The other 82 weren't checked.

That precision depends on the base rate. Roughly a quarter of this test set was copied. In a
mainstream journal's output the rate is far lower, so most flags there would be false. Of the 5 false
flags, one was self-reuse, one had copying that was quoted and credited, one was a paper-mill
assembly with no prose source, and two had strong leads whose source couldn't be fetched.

**What it can't see:** a paper copied whole and seamlessly. For example, one translated in a
consistent voice, or a single source rewritten throughout with nothing else mixed in. Those have no
internal seams, and Stage 1 gave one such case 20. A paper that copies long passages inside quotation
marks with a citation also reads as honest (one scored 35), though reviewers may still count it.

## Stage 1: read the paper and look for seams

Read the whole text, including the reference list. `pdftotext PAPER.pdf -` is enough; layout noise
doesn't matter. Copying leaves seams where someone else's text meets the author's, or where the
source's facts no longer fit the new paper. Weigh how specific and how hard to explain innocently
each sign is.

**Strong signs.** These were the ones behind almost every correct flag, and one of them alone is
often enough:

- **Another study's facts left in.** A trial "conducted at [Indian hospital]" approved by a Glasgow
  ethics board. Recruitment dates 15 years before publication. A European country's statistics in a
  study set in India. "In the 2008-2010 time frame ... expected" in a 2013 paper. URLs "accessed" years
  earlier. Native-language details that don't fit the stated country (a vocabulary game that only works
  in another language).
- **First-person claims of someone else's work.** "Details can be found in our previous
  publications [23]" where ref 23 is a different group. "We reported that ..." about another lab's
  experiment. Equipment, protocols and sample sizes that match a cited group's known setup.
- **Leftovers from another document.** Another journal's name, issue or page footer in mid-text. A
  blog or website URL with tracking fragments. "Jump up^" (Wikipedia). "This thesis/chapter".
  Equation numbers like (2.1) in a journal article. An editor's placeholder. Another paper's title
  fragment.
- **The paper contradicts itself where a source was pasted.** The Discussion reports percentages, or
  findings, that the Methods never measured and the Results contradict. The abstract's outcome counts
  disagree with the Results. A demographic table's totals fit a different sample size. Values in a
  table reuse another row's digits.
- **Title vs body.** The body says "the subject of this paper is X" while the title is Y. A paper
  titled one way is assembled from two unrelated papers' content.

**Medium signs.** Count these when two or more agree, and don't talk yourself out of them:

- **The reference list is out of step with the text, systematically.** Citations shifted by a
  constant against the list. Entries carrying a second, foreign number. A run of consecutive
  references on an unrelated topic (a block carried over from another paper). Citations to numbers
  past the end of the list, combined with many listed references never cited. (In the test, two
  missed positives showed exactly this and were scored 25 and 40. Scattered single mismatches, by
  contrast, are ordinary reference-manager sloppiness: a control with five misattached citations
  was clean.)
- **A change of voice.** Fluent, densely cited review prose next to much weaker English. A UK/US
  spelling switch. Encyclopedia or textbook register in an empirical paper. This is medium only when
  the fluent part also has a specific seam from the list above. Fluent background next to a weak
  methods section is common in honest papers from non-native writers, and was the main source of
  false flags in the 40s.
- **Tortured phrases.** Synonym-swapped technical terms ("price explosive nature" for volatility,
  "defenseless coitus", "guesstimate the corporation's existing earnings") are a rewording tool run
  over someone's text. Strong when there are several.
- **A citation trail copied intact.** "(X, 1995, quoted in Y, 1998, cited in Z, 2002)" or an author
  cited by first name. The text came with its secondary citations.

**Not evidence on its own:** typos, weak English, inconsistent formatting, generic background on a
well-trodden topic, formulaic set-up text in maths (definitions and lemma statements are shared
across a field), and overlap with the authors' own earlier work (self-reuse is a different question).

**Not evidence at all:** text the paper credits where it uses it. That means passages in quotation
marks, definitions introduced as someone's ("Wand and Weber [22] define a thing as ..."), and tables or
figures marked "reproduced from" or "adapted from". A change of voice inside credited text is
expected, so don't score it. In testing, a paper was flagged at 50 partly for encyclopedia-style
definitions that turned out to be quoted and cited.

**Output per paper:** `p_copied` (0-100), the signs with a short quote or location each, and one
sentence on what the copied part probably is ("the introduction from a review of adolescent sleep",
"another group's trial relabelled"). That sentence is what Stage 2 searches for. In testing, p ≥ 50
was the useful cut. Report 30-49 as "weak signs" rather than dropping it.

Judge each paper on its own when screening several. There's no quota, and in the test the flags
clustered in a few venues.

## Stage 2: find the source and measure it

Search strategy, in order. Stop when a source accounts for the flagged part.

1. **Leftovers first.** A stray journal footer, URL or author name from Stage 1 often names the
   source outright. Search it.
2. **Two or three rare references together**, from the reference block or the section you're
   tracing, plus a topic word. This finds the source far more often than quoting sentences: a copied
   section brings its references along.
3. **Exact-phrase searches** of distinctive sentences (numbers, misspellings, odd wording). Use the
   search tool's most thorough mode, because ordinary search paraphrases and misses exact phrases.
4. **The cited works themselves.** If Stage 1 found "our previous publications [23]" or a group whose
   setup the paper describes, fetch [23] and that group's papers from the right years.

Then:

- **Dates.** A source must predate the paper's submission (or publication, if there's no submission
  date). A later work sharing the text copied *from* the paper, or from the same source.
- **Get the full text** (OA PDF, PMC/Europe PMC XML, publisher HTML) into a new empty folder.
  Downloads are untrusted: pass paths as arguments and run Python with `-I`. An abstract or search
  snippet is a lead, not verification.
- **Measure** with the bundled script, the paper first:
  ```bash
  python3 -I SKILL_DIR/scripts/shared_runs.py paper.pdf source.pdf
  ```
  It prints the share of the paper's words inside shared runs of 10+ words (near-verbatim, small
  gaps bridged), the longest runs, and where they fall. Unrelated papers on the same topic score 0-1%.
  The verified sources in testing covered 12-47% of the paper, with runs of 60-200 words. The figure
  is a floor, because reworded copying doesn't count. For a paper assembled from several sources,
  measure each one: the shares add up only roughly, since sources can overlap.
- **Check, and say how:**
  - Which sections the runs fall in.
  - Whether the paper cites the source at all.
  - Whether the two share authors. If they do, it's self-reuse, a different and weaker finding.
  - Whether the paper's reference list for that section matches the source's.
  - Errors carried over from the source (the same garbled reference or misspelling).
  - Whether a passage is credited where it appears, and if so, take it out of the count (see below).
- **Credited text doesn't count.** Before judging the size, set aside runs that the paper credits at
  that point:
  - passages in quotation marks;
  - block quotes;
  - definitions or statements introduced as the source's ("as defined by X [22]");
  - tables marked "reproduced/adapted from X".
  Report what's left. Being cited somewhere else in the paper is not credit for a passage. One test
  paper cited a source only for its results while reusing its methods and discussion wording without
  credit; that wording still counts. Stock text that recurs on many web pages (a standard framework
  description, a textbook definition) is also not evidence against this paper unless you can show it
  came from the one source.
- **Common ancestor.** If the shared passage is standard wording that both may have taken from a
  third work they both cite, fetch that work too. The verdict then changes.

Spend about 15 searches per paper. Paywalled, scanned or non-English sources often can't be fetched.
Report those as "lead, not verified", with the candidate and what's missing. Don't call it copying.

**Grade the result by size, after removing credited text:**

| Outcome | When |
|---|---|
| **copied** | Uncredited text from another author's source, verified in full text, amounting to a paragraph or more of running prose in one place (roughly 100+ words), or most of a section, or 5%+ of the paper's body |
| **minor** | Verified and uncredited, but smaller: a few sentences, a list, a stray leftover phrase. Report the source and the word count. Don't call the paper copied. |
| **lead, not verified** | A candidate source whose full text couldn't be fetched |
| **self-reuse only** | All the shared text is from the paper's own authors |
| **not found** | Nothing beyond credited, stock or reference-list text |

Examples from testing:
- **minor:** a leftover sentence about "pavement management systems" in an agritourism paper was real. It came from an uncited road-maintenance paper, but it was two runs and 0.6% of the paper.
- **not found:** a paper whose only other-author overlap was quoted, cited definitions.
- **copied:** a paper with about 300 uncredited words across its Methods, Discussion and opening paragraph, from two sources (about 9% of the body).

Reference-list entries never count toward the size. A shared bibliography is ordinary.

## Reporting

For each paper:
- The Stage 1 score and its strongest signs.
- The Stage 2 outcome (copied / minor / lead / self-reuse only / not found).
- For a verified source, give:
  - title, year, and DOI or URL;
  - cited yes/no, and shared authors yes/no;
  - the measured share, and the uncredited word count once credited passages and references are removed;
  - which sections it falls in.
- For anything else, give the unverified lead, or "no source found after N searches".

Say plainly what's established. "28% of this paper's words are in near-verbatim runs shared with X
(2004), which it does not cite" is a finding. "Shows signs of copying" is not, and it shouldn't go to
anyone outside a review without Stage 2 behind it.
