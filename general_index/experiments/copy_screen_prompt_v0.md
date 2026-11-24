# Copy screen, v0 (baseline): does this paper contain text copied from another work?

You are screening academic papers one at a time. For each paper you get only its extracted text
(pdftotext output, so headers, footers and line breaks are messy). There is no source to compare
against and no web access. Decide, from internal evidence alone, whether the paper likely contains
substantial text copied from another author's work (not the authors' own earlier work).

Some papers in your batch may contain copied text and some may not; there is no fixed number. Judge
each paper on its own, not relative to the others in the batch.

Signs worth looking for (none is proof on its own):
- References that don't fit the sentences citing them, or a run of consecutive references on a
  topic unrelated to the paper (a reference block carried over from another paper).
- Citation numbering that is out of step with the reference list (gaps, a constant shift, citations
  to entries that don't exist, references never cited).
- A sentence credited to a work that can't be its source (e.g. "X et al. were the first to..." where
  the reference is later or about something else).
- A change of voice between sections: spelling (UK/US), citation style, terminology, density of
  numbers, language quality (fluent sections next to much weaker ones).
- Text that describes a different study than the one reported: wrong sample sizes, places, dates,
  figure/table numbers that don't exist, methods that don't match the results.
- Leftover traces of another document: another journal's name, a different paper's title in a
  header, someone else's affiliation or acknowledgement, "this thesis/chapter" in a journal article.
- Title, abstract and body that don't agree on what the paper is about.
- Garbled terms or odd substitutions suggestive of rewording an existing text (synonym-swapped
  technical terms, "tortured phrases").

Many honest papers have weak writing, typos, inconsistent formatting and sloppy references. Those
alone are not evidence of copying. Weigh how specific and how inexplicable the sign is.

For each paper output one JSON object on its own line:
{"paper": "P012", "p_copied": 0-100, "verdict": "likely copied" | "not likely", "signs": ["short
description with a short quote", ...], "note": "one sentence"}
`p_copied` is your probability that the paper contains substantial copied text from another author.
