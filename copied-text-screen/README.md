# Screening papers for copied text with an AI agent

Can an AI agent read a single academic paper and tell whether it contains text copied from other
authors, **without being given a suspected source**? And can it then find that source?

We tested this blind on 250 papers. The answer is yes, as a two-stage process:

1. **Read.** The agent reads the paper with no web access and scores how likely it is to contain
   copied text. It looks for the seams copying leaves behind.
2. **Search.** For papers it flags, it searches the web for the source, fetches the full text and
   measures the overlap with a small script.

Stage 1 alone flags too many papers to act on. Stage 2 is what establishes copying: in every test it
called a paper copied only when it had a measured full-text source.

This folder is self-contained. It needs an agent that can read files and search the web (we used
Claude Code), Python 3 with no extra packages, and `pdftotext` (poppler-utils).

| Path | What it is |
|---|---|
| `screen-for-copied-text/SKILL.md` | The method, written as an agent skill. Read it as a human too. |
| `screen-for-copied-text/scripts/shared_runs.py` | Measures the text two documents share (standard library only) |
| `prompts/` | The exact prompts used in the blind tests, for reproducing them |

**To use it with Claude Code,** copy `screen-for-copied-text/` into `~/.claude/skills/` (or a
project's `.claude/skills/`). Then ask, for example, "screen this paper for copied text". With
another agent, give it `SKILL.md` as instructions.

## What the agent looks for

Copying leaves seams where someone else's text meets the author's, or where the source's facts no
longer fit the new paper. These were the signs behind almost every correct flag:

- **Another study's facts left in.** An Indian trial approved by a Glasgow ethics board. A "2008-2010"
  outlook in a 2013 paper. A case series from one country
  republished as another's, with one regional phrase changed and the rest left in.
- **First-person claims of someone else's work.** "Our previous publications [23]", where [23] is a
  different group. A paper describing a government lab's research as "we developed".
- **Leftovers from another document.** Another journal's footer in a reference. "This thesis". A
  MATLAB tutorial's variable names. Keywords belonging to a different paper.
- **The paper contradicts itself where a source was pasted.** The Discussion explains recurrences the
  paper never had. The text's numbers disagree with its own table. A spinal case report at T12-L1
  ends with an L3-L4 operation.
- **A reference list out of step with the text, systematically.** Citations shifted by a constant. A
  block of references on another topic. (Scattered single mismatches are ordinary sloppiness.)

A change of voice, weak English, typos and generic background are not evidence on their own. Neither
is text the paper credits where it uses it, such as quotation marks or "as defined by X".

## Results

Positives were papers that people had already confirmed, by comparing them with an earlier article,
as copying another author's text or data. Controls came from the same kinds of venues: many
low-scrutiny journals, with mainstream physics, maths, biology and medicine mixed in. Their text
contained no retraction or plagiarism notice that would give the answer away.

Some "controls" kept turning out to be copied from somewhere else, so every test was corrected
afterwards by running the web search, blind, on all flagged controls and a random sample of
unflagged ones.

| Test | Papers | Stage 1 version | Copied after correction | Caught at p ≥ 50 | Falsely flagged | Precision |
|---|---|---|---|---|---|---|
| Development | 100 (20 known copies) | baseline prompt | 27 | 23 | 5 of 73 | 82% |
| Held-out 1 | 50 (5 known copies) | the skill | 7 | 7 | 4 of 43 | 58-67% |
| Held-out 2 | 100 (5 known copies) | the skill, revised* | 10 | 10 | 2 of 90 | 83% |

\* Revised after held-out 1, with the "minor" grade and the credited-text rule (see below).

- **Stage 1 ranking:** AUC was 0.92-0.99 across the tests.
- **Finding sources:** for the 10 known copies in the held-out tests, Stage 2 found the source of
  every one. For most it took 1-4 searches. The sources covered 9-83% of the paper.
- **Uncited copying nobody had recorded:**
  - Held-out 2 was meant to be 5 copied papers to 95 clean ones. In fact 5 of the "clean" papers
    were copied as well, from uncited reports, reviews and Wikipedia.
  - Stage 1 flagged all 5 of them, and Stage 2 found and measured their sources.

**Partial copies.** Held-out 2 included positives where only 9%, 16% and 35% of the paper was
copied. All were caught. The 9% case, sentences from one case series scattered through another,
scored 55, just over the line.

**False flags.** Stage 2 sorted these out:
- copying that was quoted and credited;
- self-reuse of the authors' own earlier work;
- a paper-mill patchwork with no single prose source;
- a maths paper flagged on suspicion rather than any text seam;
- leads whose source couldn't be fetched (paywalled).

### Revisions made from the held-out results

- **A "minor" grade.** One flagged paper did contain a real uncited leftover from another paper, but
  only two sentences (0.6%). Stage 2 now grades results:
  - **copied:** about 100+ uncredited words in one place, most of a section, or 5%+ of the body;
  - **minor:** smaller amounts;
  - **lead, not verified**, **self-reuse only** or **not found**.
- **Credited text doesn't count.** Passages in quotation marks, definitions introduced as a cited
  source's, and tables marked "reproduced from" are left out at both stages. A citation somewhere
  else in the paper is not credit for a near-verbatim passage. One held-out paper cited a review
  only at a couple of sentence ends while reproducing a third of the paper from it, and it counts
  as copied.

## Limits

- **Base rate.** About 10-25% of these test sets turned out to be copied. In a mainstream journal's
  output the rate is much lower, so most Stage 1 flags there would be false. Never report a Stage 1
  flag on its own.
- **Seamless copies are invisible to Stage 1.** A whole paper copied and rewritten in one consistent
  voice (for example, translated) has no internal seams; one such case scored 20. Copying inside
  quotation marks with a citation also reads as honest.
- **Recall is checked unevenly.** Only 14 of the 194 unflagged controls across the three tests were
  checked on the web (all clean or self-reuse only). Copying hidden among the rest would lower the
  true catch rate.
- **Small numbers.** The held-out tests contain 17 copied papers in all, so the error bars are wide.
- **Same model throughout.** One model family (Claude) did both the screening and the verification.
  The positives were confirmed by people.
- **Overlap figures are a floor.** `shared_runs.py` counts near-verbatim runs of 10+ words, so
  reworded copying isn't measured. Unrelated papers on the same topic score 0-1%.

## Measuring overlap yourself

```bash
python3 -I screen-for-copied-text/scripts/shared_runs.py paper.pdf candidate_source.pdf
```

It prints the share of each document's words inside shared runs, the run count, and the longest runs
with their position in the paper. It accepts PDF (via `pdftotext`), HTML/XML or plain text. Use `-I`
and keep downloaded files in their own folder, since they are untrusted input.

---

*Run in November 2026. Paper titles and DOIs are left out of this write-up on purpose: a public
document shouldn't name papers as copied without the full evidence beside each one.*
