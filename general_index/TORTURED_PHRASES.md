# Tortured phrases: does undoing them find new pairs?

Run 2026-10-17 to 2026-10-18. The user asked to find a list of tortured phrases, count how many of the
index's 5-grams use one, and check whether changing each phrase back to the original wording finds any new
matches. Script: `gi_tortured.py` (`list` / `stream` / `match`, documented in its docstring), chained by
`run_tortured.sh`. Outputs are in `data/tortured/` (not committed). It read the index from archive.org
again (all 16 slices, 4,783 GB compressed, about 31 hours four at a time) and wrote only to that folder;
`gi.sqlite3` was read-only. No paid calls.

**Result: no new pairs.** Every document pair the rewritten 5-grams connect at the usual bar (5 shared
samples) is a pair the index pairing had already found.

## The list

A tortured phrase is an established term run through a paraphrasing tool ("profound learning" for "deep
learning", "counterfeit consciousness" for "artificial intelligence"), used to hide copied text from
plagiarism checks. The Problematic Paper Screener (Cabanac, Labbé, Magazinov) publishes its fingerprints as
a CSV, each with the text it replaced: https://dbrech.irit.fr/pls/apex/f?p=9999:5 (copy in
`data/tortured/pps_tortured.csv`).

`gi_tortured.py list` turns it into `data/tortured/phrases.tsv`: 8,356 fingerprints, each lemmatized with
spaCy (`en_core_web_sm`, installed into `.venv`; not in requirements.txt) the way the index stores words
("data" is "datum", "is applied" is "be apply").
- 685 are conditional: they carry AND / NOT / OR conditions in the screener, because the phrase alone is
  often ordinary wording ("differential condition"). The conditions can't be checked from one 5-gram, so
  they are counted separately.
- 351 give no rewrite: the "expected text" is a comment (a link, quotes, "AND 1st order").
- Single-word phrases are counted but never rewritten; alone most are ordinary words.

## How many 5-grams use one

All 16 slices, 66.8 billion 5-gram rows:

| 5-gram rows containing... | rows | per million |
|---|---|---|
| any phrase on the list | 45,016,873 | 670 |
| a multi-word phrase | 5,293,207 | 79 |
| a multi-word phrase with no conditions | 1,660,038 | 25 |

14.2 million documents contain at least one listed phrase; 4,923 of the 8,356 phrases occur at least once.

Most of those hits are not tortured text. The index stores lemmas, so a fingerprint can match ordinary
wording once both are lemmatized, and many fingerprints are normal phrases in some field. The most
widespread unconditioned multi-word phrases, by documents:

| phrase | expected | documents |
|---|---|---|
| boosting converter | voltage buck-boost converter | 45,421 |
| transfer studying | transfer learning | 43,588 |
| cancer grouping | cancer classification | 39,885 |
| data gain | Information Gain | 32,429 |
| selection making | decision making | 30,930 |
| covered layer | hidden layer | 22,399 |
| include choice | feature selection | 17,581 |
| as pursues | as follows | 15,763 |

"transfer studying" lemmatizes to "transfer study", ordinary English; "covered layer" and "data gain" are
ordinary wording in other fields. The textbook examples are rare: "profound learning" 4,980 documents,
"choice tree" 914, "colossal information" 109, "counterfeit consciousness" 71, "irregular woodland" 40,
"flag to commotion" 20. The index's documents mostly predate the tortured-phrase wave the screener tracks
(2019 onwards), which fits.

## Changing them back

For each row with a rewritable phrase, `stream` replaced the phrase with its expected text and hashed every
5-word window that overlaps the replacement, keeping the 1-in-8 the slice samples keep (blake2b-64 % 8),
so the rewritten 5-grams can be looked up in the existing samples. 7,025,139 rewritten 5-grams were kept
(200,514 more had a shorter expected text that can't fill five words from one row; counted only).

`match` looked every one up in all 16 slices' samples, with the pairing's own rules (a hash shared by at
most 100 documents; a pair needs 5 shared):
- 43,873 rewritten 5-grams occur, in their original wording, in some other document;
- they connect 1,209,067 document pairs, 40 rows (about 11 distinct pairs) with 5 or more shared;
- all 40 are already in `pairs`, sharing 166 to 939 samples there: the same document held several times,
  or near-copies the pairing already found. The rewrite adds nothing to them.

`data/tortured/matches.tsv` has the 40 rows (tortured dkey, other dkey, rewritten shared, pair shared,
pair_id).

## Why it finds nothing

A paraphrasing tool rewrites many words, not only the listed phrases. Putting one phrase back restores
only the 5-grams around it, and only if the words on either side were left alone; at 1-in-8 sampling that
is under one kept sample per phrase. A copy needs roughly 40 or more restored phrases, each with untouched
neighbours, to reach 5 shared samples, and text paraphrased that heavily rarely still matches its source
word for word in between. The index's dates make it worse: few of its documents are from the years
tortured-phrase papers appeared in numbers.

## Not done

- Adding the rewritten matches to pairs just below the bar (1-4 shared samples before rewriting). Those
  near-misses were never stored, so it means computing the unrewritten shared count for the 1.2 million
  connected pairs: a few hours locally, no cost. Expected to find little, for the reason above.
- The screener's AND / NOT conditions, which would need each document's full n-gram set, not one row.
- A better use of the list here is likely flagging, not matching: a document with many unconditioned
  multi-word phrases (`hits.tsv` per document) is a tortured-phrase candidate in its own right, whether
  or not its source is in the index.
