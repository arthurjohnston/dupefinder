#!/usr/bin/env python3
"""More accurate (and more expensive) textual-overlap checks than the cosine
similarity on sentence embeddings the rest of the pipeline runs on.

Cosine similarity between two paragraph embeddings is what makes candidate
generation affordable at corpus scale (find_duplicates.py / lsh_index.py) --
but it's a *semantic* measure, not a textual one: it can be fooled by two
paragraphs that are just about the same narrow topic without sharing much
actual wording (a false positive), and a bare similarity score doesn't say
*what* overlaps or how much of it is verbatim -- which matters a lot for
judging "copied outright" vs. "reworded." Computing genuine textual overlap
is expensive enough per pair (effectively O(len_a * len_b) for a longest
common run) that it isn't affordable across a whole corpus's O(n^2) pairs --
exactly why it doesn't run until after LSH + cosine has already narrowed
things down to a few thousand candidates (build_dupe_candidates.py), where
it's cheap.

Both metrics are word-level (tokenized on whitespace, lowercased) rather
than character-level -- character-level comparison on paragraph-length text
is both slower and more sensitive to the kind of noise pdftotext/PyMuPDF
extraction already introduces (a stray space, a dehyphenation edge case)
than word-level is -- and both are normalized to [0, 1] so they're
comparable across paragraph lengths.
"""

import difflib
import re

WORD_RE = re.compile(r"\S+")


def words(text):
    return WORD_RE.findall(text.lower())


def longest_common_word_run(text_a, text_b):
    """Length of the longest run of *consecutive* words shared verbatim
    between the two paragraphs, as a fraction of the shorter paragraph's
    word count -- the strongest single signal for "this is copied, not just
    related": one long contiguous match is much harder to explain away as
    coincidence or shared topic than a high but diffuse embedding
    similarity is. Uses difflib.SequenceMatcher.find_longest_match() (same
    stdlib tool review_dupes.py's word-diff and retrieve_papers.py's title
    matching already use) rather than a hand-rolled DP -- same result,
    reuses a well-tested implementation.
    """
    a, b = words(text_a), words(text_b)
    if not a or not b:
        return 0.0
    match = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return match.size / min(len(a), len(b))


def ngram_jaccard(text_a, text_b, n=5):
    """Jaccard similarity of word n-grams ("shingles") -- catches copying
    that's distributed across the paragraph (a handful of words changed
    here and there) rather than concentrated in one run, which
    longest_common_word_run() alone would under-score. n=5 is a common
    default for near-duplicate text detection: short enough to survive
    minor edits, long enough that a shared 5-gram is a real signal rather
    than coincidental phrasing.
    """
    a, b = words(text_a), words(text_b)
    if len(a) < n or len(b) < n:
        # Too short for a stable n-gram signal -- fall back to whole-word overlap
        # (equivalent to n=1) rather than reporting a meaningless 0 for short captions.
        shingles_a, shingles_b = set(a), set(b)
    else:
        shingles_a = {tuple(a[i:i + n]) for i in range(len(a) - n + 1)}
        shingles_b = {tuple(b[i:i + n]) for i in range(len(b) - n + 1)}
    if not shingles_a or not shingles_b:
        return 0.0
    return len(shingles_a & shingles_b) / len(shingles_a | shingles_b)
