# Copy screen: label check for a paper

You are checking whether a paper contains substantial text copied from earlier work by OTHER authors.
You get the paper's extracted text (pdftotext output) and its DOI. Unlike the screening step, you
may use web search and fetch, and you may download candidate sources.

1. Read the paper in full.
2. Look for candidate sources: search for distinctive sentences (exact-phrase searches work best with
   WebSearch `mode: "extended"`), for two or three rare references the paper cites together plus a
   topic word, and for leftovers in the text (another journal's name, a URL, an author name that
   doesn't belong). Also check whether the paper itself is the original that others copied (a source
   must PREDATE the paper; a later work sharing the text is a copy of it, not its source).
3. Verify any candidate by getting its full text (curl into a new empty folder under
   /tmp/claude-1000/-home-arthur-Sites-dupefinder-general-index/394ff7aa-aa18-47aa-98f3-73fff751df08/scratchpad/verify/;
   read downloaded files only with `python3 -I` or pdftotext, passing paths as arguments) and comparing
   it with the paper. Count shared word sequences of 10+ words, or compare passage by passage.
   An abstract alone or a search snippet is not verification.
4. Shared text with the paper's OWN authors' earlier work is self-reuse: record it but it does not
   count as copying from other authors. Quotations in quotation marks with a citation don't count.

Do not open anything in the dupefinder repository other than the paper text file you are given (no
databases, no key files, no other results). Spend no more than about 15 searches per paper.

Write one JSON line per paper:
{"paper": "P010", "label": "copied" | "not found" | "self-reuse only" | "unverifiable",
 "sources": [{"title": ..., "year": ..., "doi_or_url": ..., "authors_differ": true/false,
   "verified": "fulltext" | "abstract only" | "snippet only", "share": "rough share of the paper's
   text found in it, e.g. ~40% (intro + discussion)"}],
 "searches_tried": N, "note": "one or two sentences"}
"copied" requires at least one full-text-verified source by other authors covering a substantial part
of the paper (a paragraph or more of running text, or most of a section).
