# Manually-found plagiarism cases

Real, documented academic plagiarism cases collected by hand (source-hunting/downloading, not
dupefinder's own pipeline) -- distinct from `tests/cases/` + `tests/fixtures/`, which are the
subset of these already wired into the automated back-test suite (`tests/run_tests.py`). This
directory is the broader collection; a case here is worth promoting into `tests/cases/` once it's
confirmed dupefinder actually flags it.

Each subdirectory is one case: a `case.json` (title/authors/year/arXiv-or-DOI/source URL/quoted
evidence for every paper involved) plus the actual PDFs, named `plagiarizing_*`/`overlapping_*`
vs. `source_*`.

## Cases

### aygun-tarhan-marder-spacetime-2006 -- confirmed, cross-author, all PDFs obtained

S. Aygun, M. Aygun & I. Tarhan's "Energy Momentum Localization in Marder Space Time" (arXiv
gr-qc/0607102, 2006) was removed by arXiv administrators with the explicit note: *"This paper has
been removed by arXiv administrators because it plagiarizes hep-th/0308070, gr-qc/9910015, and
others."* Both cited sources are S.S. Xulu's work (University of Zululand) -- his PhD thesis "The
Energy-Momentum Problem in General Relativity" (hep-th/0308070, 106pp) and his short paper "Total
Energy of the Bianchi Type I Universes" (gr-qc/9910015, 8pp) -- and Xulu has no author overlap
with Aygun/Aygun/Tarhan, so this is genuine cross-author plagiarism, not self-reuse. All three
PDFs downloaded successfully (arXiv keeps pre-withdrawal versions fetchable even though the
withdrawn v3 itself 404s). Source: https://arxiv.org/abs/gr-qc/0607102

### yilmaz-aygun-topological-defect-2005 -- confirmed, same-author/collaborator overlap

I. Yilmaz, M. Aygun & S. Aygun's "Topological defect solutions in the spherically symmetric
space-time admitting conformal motion" (arXiv gr-qc/0607104, published Gen. Rel. Grav. 37 (2005))
was withdrawn for *"excessive overlap with the following papers also written by the authors or
their collaborators: hep-th/0505013 and 0705.2930."* hep-th/0505013 shares author Ihsan Yilmaz
with the withdrawn paper -- this is same-author reuse across a different coauthor lineup, not
theft from a stranger, and is labeled that way in `case.json`. Included anyway as a real,
arXiv-confirmed near-duplicate useful for exercising dupefinder's `same_author` flag against an
actual case rather than only synthetic ones. The second cited source, 0705.2930, 404s (likely
withdrawn with no earlier version left accessible) and isn't included. Source:
https://arxiv.org/abs/gr-qc/0607104

Both of the above surfaced from the same 2006-2008 arXiv/Gen.Rel.Grav. plagiarism-detection sweep
(a graduate student, Mustafa Salti, and collaborators including Yilmaz/Aygun/Tarhan/Xulu-adjacent
authors, had dozens of papers flagged); see Peter Woit's contemporaneous coverage for the wider
context: https://www.math.columbia.edu/~woit/wordpress/?p=638

### ramasamy-ijacsa-data-quality-2021 -- BLOCKED, not usable yet

Already identified in `todo.md`/`tests/README.md` as a strong candidate (IJACSA confirmed "Level 1
plagiarism -- uncredited verbatim copying of a full paper"), but still not obtainable end-to-end:
the original (Procedia Computer Science, DOI 10.1016/j.procs.2019.09.277) is blocked by
ScienceDirect (HTTP 403 to automated fetches despite being genuinely gold OA), an alternate source
(the McMaster thesis this drew from) is now behind MacSphere's bot-checker, and -- newly discovered
this pass -- IJACSA's own hosted copy of the retracted paper no longer contains the actual text at
all, just a one-page retraction-notice stub. See `case.json` for the full detail. Kept for tracking,
not as a working example; revisit if a real-browser fetch path (e.g. claude-in-chrome) becomes
available.

### gino-hbs-2024 -- BLOCKED, not usable (copyrighted books)

Francesca Gino (Harvard Business School) is mainly known for a data-*fabrication* scandal (Data
Colada, several retractions, tenure revoked 2025) -- a different kind of misconduct than
dupefinder targets. Separately, in April 2024, Science/Erinn Acland identified real *text*
plagiarism: a co-authored book chapter and passages in two of Gino's trade books ("Rebel Talent",
"Sidetracked") borrowed without attribution from ~10 other works. Every instance is a commercially
published, copyrighted book -- no legitimate free route to the text on either side, unlike the
arXiv-based cases above. See `case.json`.

**Dan Ariely** is a co-author on the specific plagiarizing chapter ("Dishonesty Explained: What
Leads Moral People to Act Immorally," 2016, *The Social Psychology of Good and Evil* handbook,
with Gino) -- its opening sentence is reported to be word-for-word identical to Umphress, Bingham
& Mitchell (2010, *J. Applied Psychology* 95(4)), and other sections matched three student theses,
one supervised by Ariely himself (his spokesperson says he only gave direction/feedback, didn't
write it). A self-hosted copy of the chapter was found at `francescagino.com/s/Gino-and-Ariely-2015.pdf`
but 404s now, with no Wayback Machine snapshot -- so this is the same underlying blocker as the
rest of this case, not a separate one. Ariely's other well-known scandal (a 2012 honesty-pledge
field study, co-authored with Gino, later found to rest on data an insurance company never
actually collected) is data fabrication like Gino's main scandal, not plagiarism -- out of scope
for the same reason. Full detail in `case.json`'s `ariely_detail` field.

### churchill-cu-boulder-2006 -- BLOCKED for full texts

University of Colorado at Boulder's 2006 Investigative Committee found Ward Churchill committed
plagiarism in multiple instances, leading to his 2007 firing. Two well-documented instances (full
bibliographic detail in `case.json`): reuse of a 1972 environmental pamphlet ("The Water Plot",
Dam the Dams Campaign) across four of his own 1989-2002 publications, and near-verbatim reuse of
Fay G. Cohen's 1991 essay under a pseudonymous institute credit in a 1992 volume. All the actual
source/plagiarizing texts are chapters in commercially published academic books or an obscure,
undigitized pamphlet -- not freely obtainable. The 125-page committee report itself (a legitimate
public university document, not copyrighted trade material, quoting the opening paragraphs of both
the pamphlet and its 1989 reuse side-by-side on pp. 83-85 as primary evidence -- the one piece of
directly comparable text available for this case) is no longer checked in as
`investigative-committee-report.pdf`: this project settled on not committing any PDF, public-record
or not, rather than judgment-call exceptions per file. See `case.json` for where to find it.

## Why only two working cases

Several other leads were chased and didn't pan out within reasonable effort: general Retraction
Watch coverage of plagiarism retractions (COVID-era medical papers, an engineering-journal cluster,
a proteomics case) either didn't name the specific original paper with enough precision to locate
it, or the field/venue made both sides hard to verify as freely downloadable; a promising
comprehensive dataset of arXiv withdrawals categorized by reason (WithdrarXiv, arXiv:2412.03775)
turned out to require a Hugging Face login this environment doesn't have. The two included cases
were chosen specifically because arXiv's own admin notes name the exact plagiarized source
paper(s) directly and authoritatively -- no secondary reconstruction needed -- and every paper
involved turned out to still be fetchable.
