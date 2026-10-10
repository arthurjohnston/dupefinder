# General Index pilot: slice 1 against itself (2026-10-05)

Inputs: slice 1, 1-in-4 hash sample of 5-grams (6.60M docs, 1.07B samples), gi_pairs.py with
--max-group 20 --min-shared 10. Batched version: 12.7 min, 2.75 GB peak RAM, 3.5 GB of spill.
Output: 604,534 pairs; 321,346 at containment >= 0.3. No PDFs have been downloaded or checked yet;
everything below comes from the index's own metadata.

## What the >= 0.3 pairs are (measured)

| Removed as | Pairs | What it is |
|---|---|---|
| fewer than 50 samples on a side | 111,924 | short items, too little text to judge |
| no DOI metadata | 170,040 | about a third of documents have none |
| neighbouring DOIs | 17,661 | separate short items on one journal page (letters, reviews, abstracts); same scanned page filed under each DOI |
| same title / DOI differs only by a suffix | 4,577 | one work catalogued twice |
| generic title | 4,117 | "Information for authors", "blank", "[untitled]" |
| left over | 13,027 | see below |

Left over, by shape:
- **Equal size (within 2%), unrelated titles: 4,618.** The same file under two DOIs: a whole
  supplement PDF filed under each abstract's DOI, or a publisher landing page captured instead of the
  article (IOP, JJAP). The index's own version of the wrong-PDF problem; never copying.
- **Different size, related titles, at least one shared author: 4,086.** Legitimate reuse:
  preprint against the published version (SSRN), co-published consensus statements, guideline
  updates, successive textbook editions, chapter against whole book.
- **Different size, related titles, no shared author: 993.** The top of this group is still
  legitimate. The same article under a JSTOR DOI and a publisher DOI, with titles differing in
  case or subtitle so the exact title check misses it. New editions with a changed author list. A
  book DOI (editors listed) against one of its chapters. Author strings joined with " and "
  (JSTOR), which the author split misses.
- **Different size, unrelated titles: 2,543.** Mostly pages shared between items: book reviews
  printed together, sections of one guideline, Behavioral and Brain Sciences commentaries printed
  with their target article.

## Conclusion so far

Undirected mining of the index surfaces catalogue structure (shared pages, mirrors, editions,
chapters) far more than copying. Each family can be filtered:
- fuzzy title match (difflib >= 0.82, as retrieve_papers.py uses) and the 10.2307 (JSTOR) mirror
  case, for twins;
- an author split on " and " as well as ";";
- a book-vs-chapter DOI check;
- the equal-size test, for one file under two DOIs.

Even so, a corpus-wide ranking spends most of its effort on legitimate reuse. Two directed uses look
better:
1. Restrict pairs to ones where one side is in a venue of interest: the low-scrutiny DOI prefixes
   this project already sweeps, or the flagged cases' journals.
2. Use the index as a source-finder for this project's own flagged papers, matching their text in
   index form, rather than mining index against index.

## Overnight plan (2026-10-05 night; the user is asleep and asked for this to run unattended)

The user's hypothesis to test: pairs where one side's share (pct_a or pct_b) is over ~90% are mostly
false positives, and under ~15% mostly noise or missed matches. The exact cut-offs may differ, so
measure precision per band rather than assume them.

Steps, in order. [ ] = to do, [x] = done.
- [x] gi_pairs.py writes pct_a / pct_b (each side's share); slice 1's pairs backfilled.
- [x] Slices 2 and 3 streaming (1-in-4 sample, same as 0/1); their metadata loading into gi_meta.sqlite3.
- [x] Filters in gi_report.py (now gi_filters.py, shared by the report and analysis): fuzzy title (difflib >= 0.82), JSTOR mirror, author split on " and ",
      book-vs-chapter DOI, equal-size (one file under two DOIs). Re-measure slice 1's breakdown.
- [x] Verification batch: PDF retrieval does not work for the index (see "Verification" below), so abstracts are used instead: a stratified sample of slice 1 pairs that pass the filters, about 15 per
      band of max(pct) (>=90, 50-90, 15-50, <15), cross-author and shared-author. Retrieve with
      gi_verify.sh (email arthur.j.johnston@gmail.com, which the user approved for this pilot) into
      data/verify_s1/, then measure real overlap with rank_paper_pairs.text_check. Report the real
      copy rate per band.
- [~] When slice 0 finishes: gi_pairs.py over slice0 + slice1 (0x0, 0x1, 1x1). Running: data/pairs_s01.log.
      When done: gi_report.py -> data/report_s01, gi_abstract_check.py over all cross-author >= 15%
      (--per-cell 100000 --relations "no shared author" --bands ">=90%" "50-90%" "15-50%"),
      gi_rank_leads.py, and the Retraction Watch recall check (slices 0+1: data/rw_plagiarism_in_index.json).
- [x] Filter audit: "one file twice" / "same issue" hide no template copies (see "What could we be missing?").
- [x] gi_rank_leads.py: tiers by Retraction Watch / abstract evidence; slice 1 -> data/leads_ranked_s1.tsv.
- [x] (FAILED the precision bar; see "Identifying unnamed documents") Unnamed documents: gi_keywords.py streamed slice 1's keywords (data/keywords_s1.log) for 64,908
      unnamed documents in strong one-sided pairs + 2,000 named controls. When done: FIRST run
      gi_identify.py on the controls only (--only data/keyword_controls_s1.json --controls same
      --meta-db data/gi_meta.sqlite3) and record precision. Only if precision is high (>= 90% of
      identified controls correct) run it on the targets, then re-score those pairs.
- [x] Venue-restricted pass (Hindawi, slice 1; result in "Venue-restricted pass" below). NOTE: the index barely covers the small low-scrutiny prefixes (10.34218, 10.22214 and 10.14569 are absent from slices 0-1); Hindawi 10.1155 is the one big one (27,709 articles in slices 0-1): keep pairs where one side's DOI prefix is a low-scrutiny prefix this
      project already sweeps (see bulk_retrieve_crossref.py / private notes for the prefix list).
- NOTE (user, 2026-10-05 ~20:20): do NOT start any more slice downloads until the user says so; work with slices 0-3 first.
- [x] run_s0123.sh finished 2026-10-06 04:04 (log data/run_s0123.log); results in "Slices 0-3" below.
- [ ] When slices 2/3 finish: gi_pairs.py over slices 0-3 (or 2+3 alone if RAM/disk is tight),
      gi_report.py, gi_rank_leads.py, and the Retraction Watch recall check. OpenAlex is unblocked: the user's API key
      is in ~/.config/dupefinder/openalex_api_key (outside the repo, mode 600), exported as
      OPENALEX_API_KEY by ~/.bashrc. Shells started before 2026-10-05 21:32 don't have it, so prefix
      runs with OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)". Free budget is
      $1/day (10,000 filter calls); the account also holds a prepaid balance (~$81) that is drawn
      only past the free $1, so keep a day's runs well under 10,000 filter calls and use no
      search calls ($1 per 1,000) without asking.
- [ ] Write the results up here.

## Verification: PDFs mostly can't be had, abstracts can (measured 2026-10-05 evening)

**PDF retrieval:** 120 stratified slice 1 pairs, 240 papers, through retrieve_papers.py (Unpaywall):
15 downloaded, 181 no_oa, 17 unknown to Unpaywall, 15 errors, 12 not a PDF. Both sides retrieved for
only 2 of the 120 pairs. The index is mostly older, paywalled literature, so PDF-level verification
covers under 1% of pairs. data/verify_s1/ holds that run.

**Abstracts instead:** gi_abstract_check.py fetches both abstracts from OpenAlex (50 DOIs per
request) and counts a pair as "shared text" when the two abstracts share a verbatim run of 20 or
more words. Both abstracts exist for roughly 25-55% of pairs, depending on the band. This is a
proxy: a copy usually copies the abstract, but body reuse under a rewritten abstract is missed.

Iterating the filters against this check exposed more catalogue artifacts. The filters in
gi_filters.py now also cover:
- the same venue within a year with both sides' shares >= 50%, or with unrelated titles (conference
  abstract supplements, case-report series, letters; venue taken from the DOI stem when the index
  has no journal name);
- unrelated titles with both shares >= 80% (one file under two DOIs, across different journals);
- titles compared without their leading "[IEEE 2016 ...]" proceedings name;
- author parsing: a single "Last, F. M." author, "First Last and First Last" lists, and name order
  ("Chuancheng, Ren" == "Ren, Chuancheng").

**Result after the filters** (report_s1_v3.abstracts.txt; random sample, 100 per cell where
available):

| band (larger side's share) | shared author: abstracts share text | no shared author: abstracts share text |
|---|---|---|
| >= 90% | 21 / 23 | 0 / 5 (75 pairs in the band at all) |
| 50-90% | 38 / 56 | 3 / 14 |
| 15-50% | 11 / 49 | 4 / 24 |
| < 15%  | 0 / 34  | 0 / 38 |

On the user's hypothesis:
- **Over 90%:** for cross-author pairs, yes, essentially all false positives, but catalogue
  artifacts rather than missed copies. For shared-author pairs over 90% is real shared text (21 of
  23), almost always legitimate: versions, mirrors, conference -> journal.
- **Under 15%:** these are not false negatives. 0 of 72 share any abstract text, so they are true
  negatives (shared stock phrasing). The 15% floor is a good cut-off.
- **Where cross-author leads sit:** 15-90%, at roughly 1 in 5 of pairs whose abstracts can be
  checked. Examples from the sample: two Chinese conference papers 2007/2008 (abstracts share a
  38-word run, different authors); a 2013 Nepalese Colles-fracture paper against a 2017 Cureus paper
  by different authors (34-word run); a cloud task-scheduling paper (AMM 2014) against an AJFST 2015
  paper (24-word run). None of these has been read in full.

## Cross-author leads from slice 1 (abstract-confirmed, not yet read in full)

gi_abstract_check.py over EVERY kept cross-author pair at >= 15% (3,391 pairs; both abstracts exist
for 844): 95 shared a 20+ word verbatim abstract run. After the author-parsing fixes (IEEE-style
surname-only lists, "R, Jerome" order, hyphenated names, Jr/III suffixes, "blank" author records
treated as unknown), 72 remain cross-author. 9 of those fall in known-legitimate families (JPT
synopses of SPE papers, EFSA templated opinions, Current Protocols, a Cochrane protocol against its
review, a collaboration paper). That leaves **63 leads** in data/leads_s1_review.tsv, sorted by
abstract run length.

That file names authors in what are, at this stage, unverified suspicions, so it stays under data/
(gitignored) and should not be copied anywhere committed, the same rule as private/.

Scale: slice 1 against itself is about 1/256 of the index's possible pairs, and abstracts exist for
only about a quarter of the candidates, so a full run would surface leads in the thousands. Whether
they are worth that depends on their precision as real copying, which needs full text: retrieval of
the 143 papers behind the 72 leads is running (data/verify_leads_s1.log).

## Venue-restricted pass: Hindawi in slice 1 (measured)

519 kept pairs have a Hindawi (10.1155) DOI on one side. 26 are cross-author at >= 15%. Hindawi is
open access, so abstract coverage is much better here (20 of 25 checked). Only 2 cross-author pairs
share abstract text, both already in the leads file. Restricting to Hindawi raises coverage of the
check, but not the rate of leads: 2 of 20 against 72 of 844 corpus-wide. The 2021 snapshot predates
the 2022-23 Hindawi special-issue retractions, so their mill output is not in the index. Not worth a
dedicated pass.

### Full-text check of the leads (measured)

Of the 143 papers behind the 72 leads, 13 downloaded (107 had no OA copy). Both sides were available
for 3 leads, and **all 3 show substantial verbatim overlap** under rank_paper_pairs' 10-word exact
matching:

| shape | matched words | coverage | longest run |
|---|---|---|---|
| 2007 open-access book chapter vs a 2004 conference paper, different authors | 1,423 | 52% / 30% | 111 |
| 2017 Cureus clinical paper vs a 2013 regional orthopaedic journal paper, different authors | 1,179 | 56% / 37% | 71 |
| 2011 regional pharmacology paper vs a 2004 journal paper, different authors (methods/results text) | 939 | 43% / 25% | 95 |

Report with commands to rerun each: data/leads_s1_fulltext_report.txt (names authors; keep in data/).
Three of three is too small a sample for a precision figure, but it agrees with the abstract
signal, and these are the same shapes as this project's confirmed cases. Bylines and the PDFs
themselves have not been checked by hand, as REVIEWING.md requires before anything is a case.

## What could we be missing? (2026-10-05 evening, after the user asked)

**Ground truth this project already has.** This project's own confirmed and flagged cases are almost
absent from the index: 1 of 84 case DOIs is in slices 0-3, where about 21 would be expected. They are
mostly post-2021 or in small venues. Retraction Watch is the right yardstick instead. Of 3,991
plagiarism retractions published in 2020 or earlier, 697 are in slices 0-3 (a quarter of the
index), so roughly 70% of them are in the index (data/rw_plagiarism_in_index.json).

**Recall check on slice 1** (187 Retraction Watch plagiarism papers): 32 appear in any candidate
pair (53 pairs). Of those 53:
- 30 were dropped as "no metadata", including the six strongest (73-89% containment), each a
  retracted plagiarism paper against an unnamed document sharing most of its text. **The biggest
  leak.** The 2022 metadata update (doc_info.2022.12.update.gz) does not repair it: it covers those
  documents, but 99.9% still have no DOI.
- 19 were kept. Two are cross-author pairs that Retraction Watch confirms as plagiarism: the TQM
  pair (already among the 63 leads, so independent confirmation of a lead), and a 2017 shock-wave
  paper against 2006 and 2010 papers (unverified by the abstract check, no abstracts available).

**Are the "one file twice" / "same issue" filters hiding template copies? No.** Abstract-checked
every cross-author pair they removed at >= 15% (4,829 pairs, 3,339 with both abstracts): 40 share
abstract text, and all 40 are artifacts, items printed together (conference abstract supplements,
BJO/EMJ/Gut case-report pages, a TSQ keywords issue). OpenAlex attaches the same abstract text to
every item on a shared page. That gives a second artifact signature: different DOIs in one venue
with identical abstracts (containment 1.00).

**Remaining gaps, largest first:**
1. Unnamed partners (no metadata): identify them from the keywords table (YAKE top phrases ->
   Crossref title search) for documents in strong pairs only.
2. Verification coverage: abstracts exist for about 25% of cross-author candidates, PDFs for under
   10%. Rank leads on the index's own evidence too, rather than only reporting verified ones.
3. Partner coverage: slice 1 against itself is 1/16 of each document's partners. Five of the six
   documented retraction/source pairs in slices 0-3 sit in different slices. Pairs for 0+1 running.
4. Corpus: the index is a 2021 snapshot weighted to large publishers, so expect classic plagiarism
   (Retraction Watch kind), not this project's post-2021 paper-mill venues.

## Slices 0+1 together (measured)

gi_pairs.py over slice0 + slice1 (0x0, 0x1, 1x1): 28.9 min, 5.4 GB peak RAM, 1,921,526 pairs.
After gi_report.py filters: 174,939 kept, 9,618 cross-author at >= 15% (3x slice 1 alone).

**Retraction Watch recall/precision check.** 350 plagiarism-retracted papers (published 2020 or
earlier) are in slices 0+1; 90 appear in some candidate pair (156 pairs). 76 of those pairs were
lost to "no metadata", still the biggest leak. **12 of the 350 surface as cross-author kept leads at
>= 15%,** and every one has the expected source -> copy shape. Examples: a 2012 TPAMI paper against a
2014 conference paper (87% of the conference paper), a 2008 Computer Graphics Forum paper against a
2009 conference paper (73%), a 2006 Computers & Industrial Engineering paper against a 2009
conference paper (54%), a 2006 earthworm study against the retracted 2010 Bioresource Technology
paper (39%). So the pipeline finds sources Retraction Watch notices often don't name.

Crude recall: a paper's source falls in slices 0+1 about 1/8 of the time, so 12 of 350 (3.4%) is
roughly a quarter of the cases whose source is in the index at all. That is the baseline to measure
improvements against (the no-metadata fix first).

**Abstract check, every cross-author pair at >= 15% in slices 0+1** (9,618 pairs; both abstracts for
2,582): 285 share a 20+ word run (1/7 at >= 90%, 73/215 at 50-90%, 211/2,360 at 15-50%).
gi_rank_leads.py -> data/leads_ranked_s01.tsv: tier 1 (plagiarism retraction on one side) 14, tier 2
(shared abstract text, artifacts excluded) 279, tier 3 (other retraction) 20, tier 4 (index evidence
only) 9,305. Full-text retrieval of the 293 tier 1-2 pairs (575 papers) is running:
data/verify_leads12_s01.log, then data/leads12_s01.verified.txt.

**Full-text check of the slices 0+1 tier 1-2 leads** (293 pairs, 575 papers): 40 papers downloaded
(7%), both sides for 6 pairs. Verdicts after reading the report's bylines:
- 4 genuine cross-author reuse: the three from slice 1 (book chapter vs ICRA paper, Cureus vs a
  regional orthopaedic journal, two antitumor studies), plus two Bangladeshi bottled-water quality
  studies, 2011 and 2013 (934 words, a 135-word run).
- 1 same authors: a 2008 Nature Precedings preprint and its 2009 journal version. The index's author
  string was truncated ("A B, C D…") and read as one name; fixed in gi_filters.author_keys().
- 1 wrong PDF: rank_paper_pairs flagged the retrieved file for one side as holding the other paper's
  text (a retrieval failure, not reuse).
Report: data/leads_s01_fulltext_report.txt (names authors; keep in data/).

## Identifying unnamed documents from their keywords: fails the precision bar (measured)

gi_keywords.py streamed slice 1's keywords table (24.4 GB, 25 min) and kept the top 25 keywords for
66,708 of the 66,908 target documents (64,908 unnamed documents from strong one-sided pairs, plus
2,000 named controls). gi_identify.py sends the best keywords to a search API and accepts a candidate
when its title's words appear among the keywords.

On 200 named controls (Crossref search): it accepts a candidate for 121 and gets 29 right (24%).
Tightening the acceptance rule peaks at about 50% precision (at least 6 overlapping title stems)
while identifying only 13-20% of documents. Failure modes: generic short titles ("The ultrasonic
cleaning bath"), documents on the same narrow topic, and the same article under a second DOI. Far
below the 90% bar set before running it on the targets, so it was NOT run on them.

Usable at most as a hint for a person reviewing the few unnamed pairs that matter most (e.g. the six
paired with retracted plagiarism papers in slice 1). The no-metadata leak stays open. A better
handle may be the index's own n-grams of the opening text (title/byline region), but positions
aren't stored, so that needs more thought.

**OpenAlex is unavailable until 2026-10-06 17:00 PDT.** The keyless per-IP daily budget ran out
during the abstract checks (the first gi_identify run failed against it silently, with no output).
gi_identify.py now uses Crossref by default and stops on a long Retry-After instead of retrying.

## 2022 metadata update: fills most of the no-metadata gap (measured 2026-10-05 ~21:00)

The 64,908 "unnamed" slice 1 documents had no row at all in doc_info_N. The 2022 update
(doc_info.2022.12.update.gz) has one for nearly all of them. gi_load_meta_alt.py streamed it (stopped
after slice 3's table, 7.5 of 24 GB) into meta_alt: 11.8M rows for slices 0-3, of which 4.6M carry a
real DOI and 5.0M more a title (taken from the document text, so noisier than publisher titles).
gi_report.py --use-alt falls back to it; a title-only document gets doi "gi:<dkey>".

Slices 0+1 with --use-alt (data/report_s01_alt.tsv): "no metadata" falls from 1,194,268 to 223,691
pairs; cross-author leads at >= 15% go from 9,618 to about 31,600 (tier 4) plus the new tier 5.
**Retraction Watch recall: 12 -> 24 of 350** (20 with an identified partner, 4 more flagged with an
unidentified one).

No-metadata pairs are no longer thrown away. gi_report writes them with reason "no metadata", and
gi_rank_leads.py ranks them: an unidentified document paired with a plagiarism retraction goes in
tier 1, any other at >= 50% in tier 5 ("partner unidentified", 35,087 in slices 0+1). Pairs with
neither side identified (130,660) are counted, not listed.

Text-extracted metadata needed its own rules (gi_filters.py, applied only when a side comes from
meta_alt): titles matched by contiguous containment after stripping junk prefixes ("Title:",
repository cover pages); a junk title makes the title rules not apply instead of dropping the pair;
garbled bylines -> "authors unknown"; loose surname-token author matching; one article inside an
issue/proceedings PDF -> "inside a compilation". Two over-corrections, found by checking the rules
against Retraction Watch pairs, were reverted: a bag-of-words title match that merged a known
plagiarism pair, and junk titles dropping pairs whose shared text was real evidence.

**Still open:** pairs involving a meta_alt side remain noisy at the top. Remaining artifact
families: report/repository cover pages, proof stamps, a small article inside a big volume whose
metadata is from the slices. The last can't be dropped wholesale, since a journal paper mostly
contained in a long thesis is the Saxby/Taro shape. Better next step than more filters: order
tier 4/5 by two-sided overlap (min of pct_a, pct_b) and by retraction or abstract evidence, and
treat one-sided containment as its own lower-priority list.

Title -> DOI lookup (gi_resolve_titles.py, retrieve_papers.resolve_doi(), Crossref, similarity
>= 0.82): 96% precision / 80% recall on 150 named controls with clean titles. Not yet run on
meta_alt's text-extracted titles, which will do worse; spot-check before trusting it.

### Ordering and the last filter round (2026-10-05 ~21:30)

gi_rank_leads.py now tags each lead two-sided / one-sided (one side under 10% and the other at least
3x it) and sorts two-sided first within a tier, by the smaller share. That put the top of tier 4 on
one artifact: the index holding one PDF twice, one copy with publisher metadata and one with only a
text-extracted title (junk like "RE'" or a cover-page line) or a wrong DOI found inside the PDF (a
target article printed with its commentaries). Pairs with a 2022-update side at >= 85% both ways now
go to tier 5 "partner unidentified (near-identical copy)", not tier 4 (--meta-db tells which DOIs
came from the update). Also: related_titles needs 2+ shared words, German/French stopwords, and
"Author reply"-type titles are generic.

Retraction Watch recall stayed at 24/350 through every change in this round (checked after each).
Slices 0+1 now: tier 1 = 37, tier 2 = 280, tier 3 = 46, tier 4 = 30,836, tier 5 = 35,746
(data/leads_ranked_s01_alt.tsv). The top of tier 4 now holds plausible same-text republications,
e.g. a 2009 and a 2010 Chinese IEEE conference paper at 90%/94%, two AMR/AMM papers 2011/2013 at
90%/92%, alongside the remaining artifacts (unrelated cross-journal pairs at ~89% both ways, which
are one file under two DOIs or a genuine whole-paper copy; only full text can tell).

### Abstract check on the 2022-metadata leads (with the user's OpenAlex key, ~$0.10 of the free $1)

All 31,852 cross-author kept pairs at >= 15% in report_s01_alt (51,819 DOIs incl. gi: placeholders,
which match nothing; both abstracts for 5,297 pairs): 500 share a 20+ word run. Tier 2 grows from 280
to 474 after two more fixes: the same-issue rule ignores the year when a side's date came from the
2022 update (it is the PDF's date, e.g. a 2003 Stroke editorial comment dated 2018), and venues match
on journal name OR DOI stem (one side often has only a DOI). Retraction Watch recall unchanged at 24.

New tier 2 leads that look like real reuse, not artifacts (not read in full): a 2017 IOSR paper vs a
2012 IEEE Sensors Journal paper (85%/81%), a 2014 IJEF paper vs a 2019 IJARCSSE paper (238-word
abstract run), several AMR/AMM papers vs earlier conference papers at 78-93%.

## Slices 0-3 (measured 2026-10-06 overnight)

Streaming: slice 2 (6.60M docs, 1.07B samples) and slice 3 (6.60M docs, 1.07B samples) finished at
01:48 and 01:54, after several reconnects each (read timeouts, and a burst of SSL certificate
failures on one archive.org node), all recovered. Per the user, no further slices are being streamed.

gi_pairs.py over slices 0-3 (256 spill buckets): 60 min, 11.8 GB peak RAM, about 20 GB of spill,
6,734,753 pairs. gi_report.py --use-alt: 2,269,391 kept; cross-author kept at >= 15%: 108,403 (1,757
at >= 90%, 22,236 at 50-90%, 84,410 at 15-50%). Abstract check on all of them (164,676 DOIs incl. gi:
placeholders, about 3,300 filter calls; 14 batches lost to OpenAlex 504s): both abstracts for 17,372
pairs, 1,850 share a 20+ word run.

gi_rank_leads.py -> data/leads_ranked_s0123_alt.tsv: tier 1 = 124, tier 2 = 1,823, tier 3 = 136,
tier 4 = 104,948, tier 5 = 152,049; 424,151 pairs with neither side identified (not listed).

**Retraction Watch recall: 80 of 697** plagiarism-retracted papers in slices 0-3 (67 with an
identified partner, 13 more with an unidentified one). A paper's source falls in slices 0-3 about a
quarter of the time, so 11.5% found is roughly 45% of the cases whose source is in the index at all,
in line with the 0+1 figure (24/350 at 1/8 coverage, about 55%). Crude: it assumes every source is in
the index and Retraction Watch's case is the only copying in each paper.

### Full-text check of the slices 0-3 tier 1-2 leads (measured 2026-10-06 04:10-05:05)

1,928 tier 1-2 pairs (artifacts and partner-unidentified excluded), 3,367 papers through
retrieve_papers.py: 456 downloaded (13.5%; 2,232 no OA copy, 473 errors, 193 not a PDF). Both sides
for 85 pairs. rank_paper_pairs text_check (10-word exact shingles, x-drop 3): **74 real overlap (87%)**,
6 no real overlap, 5 wrong PDF.

Authorship of the 74, checked against the PDFs' own opening text (any author surname of one side in
the first paragraphs of the other): 27 have byline overlap, mostly the same authors (preprint/journal
versions, conference -> journal, a series of the same group's papers). Some hits are weak: places or
single common surnames that the index put in the author field. **47 have no byline overlap: cross-
author pairs with substantial verbatim shared text** (median 934 matched words; in 15 of them at least
half of one paper). Shapes include clinical studies against later papers on the same procedure,
engineering/CS conference papers against later journal papers, and review-type papers against earlier
work. They also include templated government series (USGS flood-inundation and geologic-map
reports), which are legitimate.

Files (all name authors; keep in data/): data/leads12_s0123.real_overlap.tsv (the 74, with a byline
column), data/leads_s0123_fulltext_report.txt (rank_paper_pairs report with rerun commands; it
compares every pair among the 148 papers, so it also lists a few pairs that weren't leads).

None has been read by hand. REVIEWING.md's checks (bylines on the PDFs, each paper's content matching
its own title, shared material being a common third source) come before any of these is a case.

**Correction (2026-10-06 ~05:30):** the 74 / 47 above count document pairs, and a few DOI pairs appear
twice (the index holds some PDFs twice under one DOI). Distinct DOI pairs: **68 with real shared
text, 44 of them with no byline overlap.** Neutral HTML comparison pages for the 44 are in
data/fulltext_pages_s0123/ (index: _index.tsv). Handoff and next steps: NEXT_STEPS.md.

## Pipeline consolidated into one database (2026-10-06)

Everything after streaming now lives in data/gi.sqlite3 (11.8 GB), a table per step: documents
(26,268,878), pairs (6,734,753), leads, abstracts (29,994 DOIs) + abstract_checks (17,293 pairs), fulltext (1,759 selected,
74 measured), reviews (editable, never rebuilt), retraction_watch (71,944 records). Migrated from the
existing files with no network calls and no re-streaming, then checked against them: gi_report.py from
the database reproduces report_s0123_alt.tsv row for row (5,149,093 rows, no field differences);
gi_rank_leads.py reproduces leads_ranked_s0123_alt.tsv exactly (268,017 rows; per DOI pair tiers
110 / 1,639 / 129 / 104,377 / 140,882); recall 80 of 697 (67 + 13); fulltext 74 / 68 / 44; to_review
43; gi_write_pages.py regenerates the 43 pages byte-identical in size with identical evidence blocks.
Workflow table in NEXT_STEPS.md.

## Step 2 rebuilt as shard-pair blocks (measured 2026-10-06 evening)

gi_blocks.py pairs one shard combination at a time after a global phrase-frequency pass. On shards 0-3
at 1-in-4 / cap 20 it reproduces the old pairs table exactly (6,734,753 pairs, identical shared
counts). Structured-array sorts were 95% of the time; replacing them with lexsort / a stable sort on the
hash made blocks 9x faster with identical output. Cap 100 (phrases shared by up to 100 documents
expanded; above that kept in a common-phrase store, with shared_common per pair and template-family
clusters) raised Retraction Watch recall from 80 to 92 of 697, with none of the 80 lost. 1-in-8
sampling loses recall unless the thresholds scale with it (min shared 5, min samples 25); with them it
reaches 94 of 697 (4 boundary losses, 18 gains) and 61 of 68 confirmed pairs. 356 template families on
shards 0-3 at cap 100 (e.g. NSF program-solicitation templates). Details: NEXT_STEPS.md "Step 2 at 16
shards".

### Step 3 parallelised (2026-10-06 ~23:00)

gi_report.py now classifies pair_id ranges in worker processes (default cores - 4 = 10), each reading
gi.sqlite3 read-only while the main process writes leads. On shards 0-3 (6.73M pairs) it went from 33
min to 4 min 40 s, and its TSV output is identical, all 5,432,775 rows in order, to the serial
version's (report_s0123_alt.tsv). gi_rank_leads.py and gi_recall.py rerun afterwards reproduce tiers
110 / 1,639 / 129 / 104,377 / 140,882 per DOI pair, recall 80 of 697, and 43 in to_review. At 16 shards
(~150M pairs) the report should take ~1.5 h instead of most of a day.

### A silent truncation in the streamer (2026-10-07)

Slice 13 recorded `done:` after reading 82.2 of its 282.3 GB (1.80M of ~6.6M documents): archive.org
closed the connection cleanly partway through, and compressed_chunks() treated any empty read as
end-of-file. Every other finished shard was checked against its zip's size from archive.org
metadata: 4-11 read 100.0% (shards 0-3 were complete when streamed). Fix in gi_sample_5grams.py: the
file's total size is taken from the first response (Content-Range / Content-Length), and an empty
read before that many bytes is a dropped connection (reconnect, resume at the same byte); tested
with a simulated mid-file close. Slice 13 was set aside (slice13.truncated.log) and re-streamed with
the fix, slice 15 restarted on it too (it had only 5 GB on the old code); slices 12 and 14 run the old
code and get checked on completion. run_full16.sh now refuses to start unless every shard read its
whole file.
Within the hour slice 14 did the same (35.8 of 282.0 GB). Set aside (slice14.truncated.log) and
re-streamed with the fix. Slice 12, the last stream on the old code (127 GB in), was stopped and
restarted on the fix too (slice12.oldcode_stopped.log): with two of three old-code streams truncated
that afternoon, finishing it risked six more hours for nothing. From 2026-10-07 15:25, slices 12-15
all stream on the fixed code.

### Slow long-lived connections (2026-10-07)

Slices 13 and 14 dropped below 1 MB/s while a fresh 30 MB range request to the same servers (and to
every replica: ia6xxxxx / ia8xxxxx / the dn7xxxxx.ca mirror) ran at 16-25 MB/s, so long-lived
connections were being degraded, not the servers. gi_sample_5grams.py now drops a connection that
averages under MIN_RATE (1.5 MB/s) over SLOW_WINDOW (180 s) and resumes at the same byte. Tested
(simulated slow connection, short read then slow, clean run: output byte-identical each time); the
first version of the check ran between counting a chunk and yielding it and lost that chunk on
reconnect, caught by the same test. Slices 13 and 14 restarted on it (they were 14 GB and 2 GB in).

### GI-13: same-author pairs scored as cross-author (2026-10-07)

The user spotted that GI-13 shares its authors. Both PDFs carry an identical seven-author byline (an
AIRCC conference paper and its journal version). Two checks missed it:
1. **Index metadata:** the proceedings side's 2022-update author string is the volume's editor, from
   the "<editor> et al. (Eds)" running header AIRCC prints on every CS & IT page, not the authors. 110
   documents carry that exact string. gi_filters.author_relation() now treats it as "authors unknown"
   (EDITOR_AUTHORS, exact form only, since a real author elsewhere shares the surname).
2. **Byline check (gi_verify.py):** it searched the first 6 *extracted* paragraphs, and
   extract_papers.py had dropped the title/byline block from both papers. It now also searches page 1
   of the PDF (pdftotext). Multi-word surnames are split ("Le Moal" -> "moal"; joined into "lemoal"
   they could never match).
Rechecked all 44 "no byline overlap" pairs against the PDFs' page 1: 2 are same-author, GI-13 and the
MFCC emotion-recognition pair (the same editor-string cause, AIRCC proceedings vs journal). 13 pairs
have metadata names not found on their own page 1 (junk metadata or authors off page 1, e.g. USGS
reports), and still need a byline read by hand. tests/unit/test_gi_author_checks.py covers all three
fixes. The fulltext.byline values already in gi.sqlite3 were not rewritten: rerun `gi_verify.py
measure` for run leads12_s0123 to refresh them. Name-level detail: data/GI13_author_investigation.md.
Evening of 2026-10-07: the long-lived streams settled at 3-4 MB/s again (above the 1.5 MB/s floor),
while a fresh range request to each file got 8-12 MB/s with all four running. MIN_RATE raised to
5 MB/s; slices 12-14 (129, 93, 84 GB in, with 14-18 h left at that pace) restarted on it, which comes
to ~8 h each at a fresh connection's speed. Slice 15 (137 GB in, ~9.5 h left) kept running.

## 16-shard documents import (measured 2026-10-08)

`gi_db.py import-documents` over all 16 shards: 103,297,019 documents, 08:19 -> 14:23 (6 hours), gi.sqlite3
12.4 -> 40.5 GB. Disk-bound, not CPU-bound: one `insert ... select` that looks each document up in the
40 GB gi_meta.sqlite3 (meta, meta_alt, and meta again by lower(doi)) in dkey order, so the metadata reads
are scattered, and the lower(doi) index on documents is written in random order. Uncommitted, the WAL
reached 36 GB (repeated copies of the same index pages) plus ~21 GB of temp files in /var/tmp, so leave
~60 GB headroom beyond the final size. Progress can be read from the WAL while it runs: rows go in dkey
order, so the leading hex digits of the 40-char keys in the WAL's last few MB give the fraction done.
Faster next time (not tried): create the lower(doi) index after the insert rather than before it.

### Report stopped at 16 shards: WAL checkpoint starvation (2026-10-08)

The parallel gi_report.py wrote leads into gi.sqlite3 while its 10 workers read the same file. With that
many readers one is always on an old snapshot, so SQLite can never restart the WAL: it grew to 130 GB
by chunk 210 of 469 (~1 GB per chunk and rising) and chunks slowed from 13 s to 55 s as the WAL index
grew. Projected to fill the disk before the end, so it was stopped (user's OK) and the WAL checkpointed.
At 4 shards (27 chunks) the same growth was too small to notice. Fix: rows are staged in a side file
(`gi.sqlite3.leads_stage`, journal off) and copied into leads in one pass after the workers finish, with
the two leads indexes dropped and rebuilt after. Checked on the first 600k pairs: 599,999 rows identical
to the stopped run's. Rerun with the abstract check, ranking and recall: run_rest16.sh -> data/run_rest16.log.
General lesson: never write to a SQLite database in WAL mode that many long-lived readers are using.

## All 16 shards at 1-in-8, cap 100 (finished 2026-10-09 07:06)

103,297,019 documents; 117,217,664 pairs (87.4M share a common phrase; 1,082 template families).
Leads: 43,192,781 kept, the rest filtered (same work 25.4M, too short 21.6M, no metadata 13.0M kept
as such, neighbouring DOI 5.7M, generic title 3.8M, ...). Kept with no shared author: 65,012 at >=90%,
610,501 at 50-90%, 1,903,170 at 15-50%.
Abstract check (OpenAlex, ~26k calls): 1,027,944 pairs, 22,742 share abstract text, 345,008 don't,
660,194 lack an abstract; 80 batches rejected with HTTP 400 (mostly the last ~2 minutes of the sorted
DOI list) and 3 network/504 failures, left uncached for a later run.
Ranking per DOI pair: tier 1 1,493; tier 2 21,903; tier 3 2,350; tier 4 1,870,267; tier 5 1,966,319.
Retraction Watch recall: 217 of 697 (31.1%) -- 200 with the partner identified, 17 unidentified only
(94 of 697 at shards 0-3).
Timings: documents import 6 h, frequency pass 33 min, 136 blocks 4 h 25 min (3 workers), common 13 min,
clusters 1 min, finalize 12 min, report 1 h + 37 min copy (after the WAL fix), abstracts 7.5 h,
ranking 28 min, recall 12 min. gi.sqlite3 80 GB.

### Full-text runs: internal disk first, text only (2026-10-09)

The external drive (WD Elements on a laptop USB port, drawing 896 of 900 mA) dropped off USB three times
in two hours, once mid-extraction (583 papers failed to open). gi_verify.sh now does everything on the
internal disk (data/verify_<run>/papers) and only moves the PDFs to /mnt/papers/general_index/<run>/ at
the end (`gi_verify.py move`: copy, size check, update state.sqlite3 + library papers.file_path +
local_pdfs, commit, then delete the original; rerunnable). And it no longer embeds: embed_paragraphs.py
was only there because it is what stores paragraph text in the library; `gi_verify.py load-text` stores
the same text with no vectors (checked on 12 papers: 735 of 735 paragraphs identical, including the 36
the embedder marks non-English). Never edit gi_verify.sh while a run is using it: bash reads a script as
it goes, so an edit mid-run can make it resume at the wrong byte (strat_s16a was stopped and resumed by
data/resume_strat_s16a.sh for this reason).
leads12_s16a (3,700 tier 1-2 pairs, 6,560 papers): 966 PDFs (14.7%); 205 pairs with both papers: 180
real overlap, 14 no real overlap, 11 wrong PDF.

## Index documents filed under the wrong DOI (checked 2026-10-09)

**Finding.** Some General Index documents carry the DOI of a different paper from the one whose text
they hold. Two such documents form a "pair" that is really one paper against itself, so the index
reports high overlap, while the two real papers behind the DOIs share nothing. Among random
cross-author leads it accounts for most of the high-overlap pairs; among the top-ranked leads (tiers
1-2) it is rare.

**How it was checked** (`gi_check_index_text.py`, output `data/index_text_check.tsv`). For every pair
with both PDFs downloaded (244 pairs, 458 index documents), the index's own sampled 5-gram hashes for
each document were read from the shard samples and compared with the same hashes computed from the text
of BOTH downloaded PDFs: `own` = share of the index document's samples found in the PDF for its DOI,
`other` = share found in the partner's PDF. The index lemmatizes and this check doesn't, so even a
correct document only reaches ~13-25% (median `own` 21.6% on the 180 real-overlap pairs); unrelated text
scores 0-2%. A document counts as mislabelled when `own` < 1.5% (it doesn't match its own paper), split
into "holds the partner's text" (`other` >= 3%) and "holds some third document" (`other` < 1.5%).

| Batch | Pairs checked | Index text correct | Mislabelled: partner's text | Mislabelled: other text |
|---|---|---|---|---|
| leads12_s16a (tiers 1-2, top-ranked) | 205 | 201 | 2 | 2 |
| strat_s16a (random cross-author, 6 bands) | 39 | 20 | 15 | 4 |
| **Total** | **244** | **221** | **17** | **6** |

So 23 of 244 checked pairs (9%) involve a mislabelled index document: 4 of 205 (2%) among top-ranked
leads, 19 of 39 (49%) among random ones. (A first tally said 22: a side with `own` 0% but `other` between
1.5% and 3% fell through to "correct". Two top-ranked pairs are like that; the rule is now `own` < 1.5%
whatever `other` is.) In the random batch, by index overlap of the larger side:

| Index overlap | Real full-text overlap | No overlap, index text correct | No overlap, index document mislabelled |
|---|---|---|---|
| 50%+ | 4 | 0 | 12 |
| 15-50% | 6 | 2 | 3 |
| 5-15% | 2 | 6 | 4 |

All 12 high-overlap random pairs with no full-text overlap are this error. Every pair whose full text
does overlap had correct index text, and all 11 "wrong pdf" pairs did too (there the retrieval fetched
the same file twice; the index was right).

**Likely mechanism: the DOI taken from the paper's reference list.** In 9 of the 15 "partner's text"
cases the partner paper cites the paper whose DOI the index document wrongly carries (4 not found in the
references, 2 with no references extracted). Clearest case: DOI 10.1016/0370-2693(93)91553-y (Baur et
al. 1993, a short letter) has 10 index documents; seven are 54-85 sampled 5-grams, three are 343-356,
the size of the 2014 CMS paper 10.1007/JHEP02(2014)013, whose reference [1] is Baur et al. 1993. Those
three hold the CMS paper's text (`own` 0.3%, `other` 16.9%). Not proven for every case: the index
doesn't document how DOIs were assigned.

**Why the top-ranked leads mostly escape it:** tier 2 requires the two papers' OpenAlex abstracts (looked
up by DOI) to share text, which a mislabelled pair can't pass; tier 1 is anchored on a Retraction Watch
DOI. The `same work` filter misses it because it compares catalogue titles, and here the titles belong
to two different real papers.

**Consequences.**
- Index overlap on its own is not a plagiarism signal for untiered/tier-4 cross-author leads: at 50%+ it
  was this error 12 times out of 16.
- **No index-only filter separates it** (tested on the 244 labelled pairs, 23 mislabelled, 221 correct,
  192 of those real overlap):

  | Rule | Mislabelled caught | False alarms (of which real overlap) |
  |---|---|---|
  | size ratio >= 0.9 and overlap >= 70% both ways | 4 / 23 | 26 (23) |
  | size ratio >= 0.9 and overlap >= 50% both ways | 8 / 23 | 68 (62) |
  | a side's size >= 3x or <= 1/3 of the other index documents under its DOI | 8 / 23 | 10 (8) |
  | a side's size >= 2x or <= 1/2 of them | 10 / 23 | 16 (13) |
  | first rule OR the 3x rule | 11 / 23 | 36 (31) |

  The "near-identical sizes, symmetric overlap" signature suggested above is real for some cases
  (343/342, 123/123, 148/149) but a whole-paper copy looks exactly the same in the index, so it flags
  real plagiarism far more often than the error. What does separate them is evidence tied to the DOI from
  outside the index: the abstract check (tier 2) and full-text retrieval. Practical rule: don't treat an
  untiered/tier-4 pair as a lead on index overlap alone.
- Caveat: the check needs both PDFs, and only 39 random pairs had both, so the 49% has a wide margin.

### Agent review needs the PDFs on the internal disk (2026-10-09)

The first agent review of the 16-shard full-text runs (273 pairs, $20.05) ran a minute after the external
drive dropped off USB (fifth time that day, now under read load too: pdftotext reading page 1 was enough).
For the 209 pairs whose PDFs were on the drive, the packets had no pdf_page1, so 34 verdicts were forced
to unsure ("bylines not read from both PDFs") and the rest checked bylines only against extracted text.
The account also ran out of credit after $19.87 (9 pairs unreviewed). Fixes in gi_agent_review.py:
`--runs` (only those fulltext runs), `--pdf-mirror OLD=NEW` (read PDFs from a local copy; the two runs'
1.6 GB are mirrored in data/pdf_cache/), `--workers` (4 calls in flight, ~25 min instead of ~1.5 h), and
a pair whose PDF file isn't found is now skipped, never reviewed blind. Reviews redone with --redo.
