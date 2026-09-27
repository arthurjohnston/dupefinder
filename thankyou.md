# Thank you

People whose plagiarism-detection work made it possible to validate this project's approach
(see `tests/cases/carlini-roadmap-2022.json` and `tests/run_tests.py` for the rerunnable back-test).

## Nicholas Carlini

- Website: https://nicholas.carlini.com/
- GitHub: [@carlini](https://github.com/carlini)
- Google Scholar: https://scholar.google.com/citations?user=q4qDvAoAAAAJ

Co-author of "Deduplicating Training Data Makes Language Models Better" (Lee, Ippolito,
Nystrom, Zhang, Eck, Callison-Burch, Carlini; arXiv:2107.06499, 2021). A coauthor of his
noticed that text in the 99-author survey "A Roadmap for Big Model" (Sha Yuan et al.,
arXiv:2203.14101, 2022) looked oddly familiar. Carlini ran his own deduplication tooling --
from the very paper being copied -- over both texts, confirmed the overlap, and published
seven side-by-side comparisons publicly:
https://nicholas.carlini.com/writing/2022/a-case-of-plagarism-in-machine-learning.html
(2022-04-08). "A Roadmap for Big Model" was withdrawn by its authors twelve days later,
"due to critical issues in Section 2.3.1 of Article 2" -- the very section Carlini flagged.

This project's `find_duplicates.py` was back-tested against this exact pair and independently
recovered the same overlap (Section 2.3.1's "Duplication" passage against the original's
abstract/methods text, cosine similarity 0.80-0.90) using nothing but the paragraph
embeddings. Run `python3 tests/run_tests.py --email you@your-institution.edu` to reproduce it.

## arXiv administrators

- https://arxiv.org/help/moderation

In 2006-2008, arXiv's plagiarism-detection review (part manual, part -- since June 2011 --
automated text-overlap flagging) caught a cluster of general-relativity/gravitation papers
with unattributed, verbatim-or-near-verbatim reuse of others' work, and removed/withdrew them
with a direct on-record statement of exactly what was copied from where -- no secondary
reconstruction needed to know what happened. Two of those cases are back-tested here:

- **gr-qc/0607102** ("Energy Momentum Localization in Marder Space Time," Aygun, Aygun &
  Tarhan, 2006), removed with the note: *"This paper has been removed by arXiv administrators
  because it plagiarizes hep-th/0308070, gr-qc/9910015, and others"* -- both by S.S. Xulu, an
  unrelated author. `tests/cases/aygun-tarhan-marder-spacetime-2006.json`.
- **gr-qc/0607104** ("Topological defect solutions in the spherically symmetric space-time
  admitting conformal motion," Yilmaz, Aygun & Aygun, *Gen. Rel. Grav.* 37 (2005)), withdrawn
  with the note: *"excessive overlap with the following papers also written by the authors or
  their collaborators: hep-th/0505013 and 0705.2930"* -- same-author reuse, not cross-author
  theft, and labeled as such in the case file. `tests/cases/yilmaz-aygun-topological-defect-2005.json`.

This project's `find_duplicates.py` independently recovers both: 0.845-0.888 cosine similarity
for the Xulu case, 0.997 (a near-exact duplicate paragraph) for the Yilmaz case.

## Peter Woit

- Blog: https://www.math.columbia.edu/~woit/wordpress/

A mathematical physicist at Columbia University whose blog, *Not Even Wrong*, documented the
wider 2006-2008 arXiv/*Gen. Rel. Grav.* plagiarism episode as it unfolded (retraction notices,
the scale of the affected author cluster, follow-up corrections) -- the public trail that made
tracking down the two cases above tractable years later.
https://www.math.columbia.edu/~woit/wordpress/?p=638
