#!/usr/bin/env python3
"""Unit tests for text_overlap.py's pure textual-overlap metrics."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import text_overlap  # noqa: E402


class TestWords(unittest.TestCase):
    def test_splits_on_whitespace_and_lowercases(self):
        self.assertEqual(text_overlap.words("Hello   World\tFoo"), ["hello", "world", "foo"])

    def test_empty_string(self):
        self.assertEqual(text_overlap.words(""), [])


class TestLongestCommonWordRun(unittest.TestCase):
    def test_identical_text_is_full_ratio(self):
        text = "the quick brown fox jumps over the lazy dog"
        self.assertEqual(text_overlap.longest_common_word_run(text, text), 1.0)

    def test_no_overlap_at_all(self):
        self.assertEqual(
            text_overlap.longest_common_word_run("apples bananas cherries", "xylophone zebra yak"), 0.0
        )

    def test_partial_verbatim_run_scored_against_shorter_paragraph(self):
        # "brown fox jumps" (3 words) is the longest shared run; shorter paragraph has 4 words.
        a = "the brown fox jumps over lazy dogs"
        b = "brown fox jumps quickly"
        self.assertAlmostEqual(text_overlap.longest_common_word_run(a, b), 3 / 4)

    def test_empty_paragraph_returns_zero(self):
        self.assertEqual(text_overlap.longest_common_word_run("", "some text here"), 0.0)
        self.assertEqual(text_overlap.longest_common_word_run("some text here", ""), 0.0)
        self.assertEqual(text_overlap.longest_common_word_run("", ""), 0.0)

    def test_shared_words_out_of_order_dont_count_as_a_run(self):
        # Same bag of words, reversed order -- no run longer than 1 word survives.
        a = "alpha beta gamma delta"
        b = "delta gamma beta alpha"
        self.assertAlmostEqual(text_overlap.longest_common_word_run(a, b), 1 / 4)


class TestNgramJaccard(unittest.TestCase):
    def test_identical_text_is_full_overlap(self):
        text = "the quick brown fox jumps over the lazy dog again"
        self.assertEqual(text_overlap.ngram_jaccard(text, text), 1.0)

    def test_no_overlap_at_all(self):
        a = "one two three four five six seven"
        b = "cat dog bird fish tree rock cloud"
        self.assertEqual(text_overlap.ngram_jaccard(a, b), 0.0)

    def test_short_text_falls_back_to_whole_word_overlap(self):
        # Both shorter than n=5 words -- falls back to n=1 (whole-word set) overlap per the docstring.
        a = "red blue green"
        b = "blue green yellow"
        # shingles_a={red,blue,green}, shingles_b={blue,green,yellow}; intersection=2, union=4
        self.assertAlmostEqual(text_overlap.ngram_jaccard(a, b), 2 / 4)

    def test_distributed_partial_overlap_with_default_n(self):
        a = "a b c d e f g h"
        b = "a b c d e f g x"
        # 4 shingles each (len-5+1=4); only the shingles touching the final (differing) word
        # diverge -- (a,b,c,d,e), (b,c,d,e,f), (c,d,e,f,g) are shared, only the last differs -> 3/5
        self.assertAlmostEqual(text_overlap.ngram_jaccard(a, b), 3 / 5)

    def test_custom_ngram_size(self):
        a = "a b c d"
        b = "a b c e"
        # n=2: shingles_a={(a,b),(b,c),(c,d)}, shingles_b={(a,b),(b,c),(c,e)} -> intersection 2, union 4
        self.assertAlmostEqual(text_overlap.ngram_jaccard(a, b, n=2), 2 / 4)

    def test_empty_paragraph_returns_zero(self):
        self.assertEqual(text_overlap.ngram_jaccard("", "some real text here"), 0.0)
        self.assertEqual(text_overlap.ngram_jaccard("", ""), 0.0)


if __name__ == "__main__":
    unittest.main()
