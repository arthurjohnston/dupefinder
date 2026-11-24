# Can an agent tell a paper copied text from the paper alone? (2026-11-23)

Question: without a suspected source, can an agent reading one paper judge whether it contains text
copied from other authors, from internal signs (references that don't fit, a change of voice,
another study's facts left in)? And can it then find the source? Result: yes, well enough to be a
screen, provided a web/full-text check (Stage 2) follows before anything is called copying. The
method is written up as a standalone skill: `.claude/skills/screen-for-copied-text/` (SKILL.md plus
`scripts/shared_runs.py`, stdlib only, no dependency on this repo; `.claude/` is git-ignored, so
copy the folder to share it).

## Set-up

`copy_screen_build.py` builds two blind sets from the GI libraries (`data/copy_screen/`, key in
`key.json`, git-ignored):
- **Positives:** the later article of a case you settled as real cross-author or data copying
  (`case_status.csv`: verdict real, verdict_by you), with different publication years. One per
  article: GI-59/GI-60 are the same article under two DOIs.
- **Controls:** papers from GI pairs whose full-text check found "no real overlap", in no real or
  wrong-PDF pair and no review. Only ~100 of these still have a PDF on disk, so the rest are the
  **earlier** article of other real pairs (original by construction). They are journal-matched to
  the positives where possible (48 of 125).
- Nothing in Retraction Watch, and no text mentioning retraction, plagiarism, withdrawal or an
  expression of concern (which would leak the label).
- **Dev set:** 20 positives / 80 controls. **Held-out set:** 5 / 45, disjoint from dev.

Text is pdftotext output with the reference list. Screening agents saw only their 10 files: no web,
no repo. Prompts: `copy_screen_prompt_v0.md` for the dev baseline; the skill's Stage 1 for the
held-out run. Scoring is `copy_screen_score.py RUN SPLIT`.

## Dev (baseline prompt)

Out of the box: AUC 0.92; at p ≥ 50, caught 16/20 and flagged 12/80 controls.

The 12 flagged controls and 4 random unflagged ones were then checked on the web, blind to which
were which (`copy_screen_verify_prompt.md`, results in `results/v0/verify_g*.jsonl`):
- **7 flagged controls really are copied**, each with an uncited, full-text-verified source by
  other authors covering 12-47% of the paper: P011, P025, P043, P049, P061, P067, P071.
- **2 have strong leads** whose source couldn't be fetched: P005, P010.
- **3 are genuinely false flags:** P008 (quoted and credited), P056 (self-reuse), P048 (paper-mill
  patchwork, no prose source).
- **The 4 unflagged controls were clean.**

So "no real overlap" means only "not copied from its GI partner". Relabelled
(`relabel_dev.json`): AUC 0.95; at p ≥ 50, caught 23/27 with 5/73 false flags (82% precision).

Misses: P003 (a seamless whole-paper copy), P037 (copying in quotation marks with credit), and P092
and P059. The baseline found the right signs on those last two (a constant citation shift, an
off-topic reference block, listed references never cited) and then talked itself down. The skill
tells the agent to count these.

## Held-out (with the skill)

Stage 1 at p ≥ 50 flagged 5/5 positives and 7/45 controls (AUC 0.94 on the original labels).

Stage 2 ran on all 12 flagged papers and 4 random unflagged controls, blind
(`results/skill_v1/verify_g*.jsonl`):
- **Positives:** it found and measured the case's known earlier article for all 5, covering 43-75%
  of the paper. Four match the key's DOI; P013's Atlantis Press 2016 source matches by
  publisher/year and is very likely the same work.
- **Flagged controls:** 2 are copied (P003, about 35% from the 2011 EAU guidelines, named but not
  referenced; P020, about 10% from two cited/uncited papers), 1 is an unverified lead (P018), and 4
  were not found (P004, P007, P012, P025).
- **Unflagged controls:** 3 are self-reuse only, 1 was not found.

Corrected: 7/7 caught, 4/43 false flags, 58-67% precision at Stage 1. Across both runs, Stage 2
never labelled a paper copied without a measured full-text source.

## Changes after the held-out run (2026-11-24)

Two rules were added to the skill. They come from the held-out set, so that set no longer measures
them.
- **Size grading in Stage 2:** copied / **minor** / lead / self-reuse only / not found.
  - "Copied" needs roughly 100+ uncredited words in one place, most of a section, or 5%+ of the body.
  - P025's real but tiny pavement-paper leftover (2 runs, 0.6%) is the "minor" example.
- **Credited text doesn't count**, in either stage.
  - This covers passages in quotation marks, definitions introduced as a cited source's, and tables
    marked "reproduced from". P007 was flagged partly on quoted, cited definitions.
  - A citation elsewhere in the paper is not credit for a passage. P020's uncredited reuse of
    Verdegaal's wording still counts.

## Caveats

- **Base rate.** About a quarter of the dev set turned out copied. On a mainstream journal's output,
  Stage 1's flags would be mostly false.
- **Uneven checking.** Only flagged controls plus 8 random unflagged ones were checked on the web.
  Copying hidden among the other unflagged controls would lower the true recall. The 8 random ones
  were all clean, but that is a small sample.
- **Shape of the positives.** They are mostly whole-paper copies (median 88% shared with the
  earlier article). Partial copying of one section is less represented.
- **Length.** Positives are shorter (median 3,450 words vs 6,290 for controls).
- **Safety stops.** A safety classifier stopped two screening agents mid-write on the same batch
  (dev P095). Writing one line per paper as it went, with paraphrased signs, avoided it.

## Second held-out set, low base rate (skill v2, 2026-11-24)

`copy_screen_build_more.py test2 5 95` built 100 new papers that share no paper with dev or test.
- **Positives:** 5. Three are partial copies: 9%, 16% and 35% of the paper.
- **Controls:** all 95 are "no real overlap" leads.

The script reads PDF paths relative to each library's folder. `copy_screen_build.py` didn't, which is
why its pool was smaller. Stage 1 used the skill after the minor/credited-text changes.

**Stage 1 at p ≥ 50:** 5/5 positives (scored 55-92) and 7/95 controls. AUC 0.97 on the original labels.

**Stage 2** ran on all 12 flagged papers plus 6 random unflagged controls
(`results/skill_v2/verify_g*.jsonl`). It found the known source for all 5 positives, covering 9-83% of
each paper, in 1-3 searches each. The flagged controls came out as follows:
- **5 copied**, each from an uncited source by other authors (`relabel_test2.json`):
  - P055 power tariffs: an NREL report, 9%, plus leftover MATLAB demo text.
  - P035 pesticides: two reviews, about 29%.
  - P064 engine control: a NASA review, 34%, cited only in passing, plus Wikipedia, 8%.
  - P034 ERABS: Awad 2014, 10%.
  - P013 package inserts: about 360 words from two 2015 papers.
- **2 not found**: P006 and P015. For P015 two candidate sources couldn't be fetched.

All 6 unflagged controls came out not found or self-reuse. No paper was graded "minor".

Corrected labels: 10 copied out of 100, AUC 0.99. Stage 1 at p ≥ 50 caught 10/10 and falsely flagged 2/90, so
precision was 83%.

The intended 5% base rate was really about 10%: at least 5 of 95 "clean" controls were copied. Only 6
of the 88 unflagged controls were checked on the web. Recall could be lower if the unchecked ones hide
copying, especially the six scored 30-40 (P045, P049, P067, P075, P079, P097).
