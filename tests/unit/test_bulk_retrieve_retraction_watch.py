#!/usr/bin/env python3
"""Unit tests for bulk_retrieve_retraction_watch.py's CSV parsing --
load_plagiarism_cases()'s date-parsing bug (found for real against the
actual live dataset, see that function's own comment) gets a real
regression test here rather than relying on having re-downloaded the CSV."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bulk_retrieve_retraction_watch as brw  # noqa: E402

FIELDNAMES = [
    "Record ID", "Title", "Subject", "Institution", "Journal", "Publisher", "Country",
    "Author", "URLS", "ArticleType", "RetractionDate", "RetractionDOI", "RetractionPubMedID",
    "OriginalPaperDate", "OriginalPaperDOI", "OriginalPaperPubMedID", "RetractionNature",
    "Reason", "Paywalled", "Notes", "",
]


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            full = {k: "" for k in FIELDNAMES}
            full.update(row)
            writer.writerow(full)


class TestLoadPlagiarismCases(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.tmpdir.name) / "retraction_watch.csv"

    def test_filters_by_reason_substring(self):
        write_csv(self.csv_path, [
            {"Title": "A", "OriginalPaperDOI": "10.1/a", "Reason": "Plagiarism of/in Article;"},
            {"Title": "B", "OriginalPaperDOI": "10.1/b", "Reason": "Error in Text;"},
        ])
        cases = list(brw.load_plagiarism_cases(self.csv_path, "Plagiarism of/in Article"))
        self.assertEqual([c["title"] for c in cases], ["A"])

    def test_skips_rows_with_no_doi(self):
        write_csv(self.csv_path, [
            {"Title": "No DOI", "OriginalPaperDOI": "", "Reason": "Plagiarism of/in Article;"},
        ])
        cases = list(brw.load_plagiarism_cases(self.csv_path, "Plagiarism of/in Article"))
        self.assertEqual(cases, [])

    def test_date_with_time_component_parses_correctly(self):
        """Regression test: the real dataset's OriginalPaperDate is formatted
        like "5/14/2025 0:00" -- a naive split-on-"/" leaves the year fused
        with the time ("2025 0:00"), silently dropping the year. Confirmed
        against the real live dataset before this fix, not hypothetical."""
        write_csv(self.csv_path, [
            {"Title": "A", "OriginalPaperDOI": "10.1/a", "Reason": "Plagiarism of/in Article;",
             "OriginalPaperDate": "5/14/2025 0:00"},
        ])
        cases = list(brw.load_plagiarism_cases(self.csv_path, "Plagiarism of/in Article"))
        self.assertEqual(cases[0]["year"], 2025)

    def test_missing_date_yields_none_year(self):
        write_csv(self.csv_path, [
            {"Title": "A", "OriginalPaperDOI": "10.1/a", "Reason": "Plagiarism of/in Article;",
             "OriginalPaperDate": ""},
        ])
        cases = list(brw.load_plagiarism_cases(self.csv_path, "Plagiarism of/in Article"))
        self.assertIsNone(cases[0]["year"])

    def test_semicolon_separated_authors_split_correctly(self):
        write_csv(self.csv_path, [
            {"Title": "A", "OriginalPaperDOI": "10.1/a", "Reason": "Plagiarism of/in Article;",
             "Author": "Jane Doe; John Smith; "},
        ])
        cases = list(brw.load_plagiarism_cases(self.csv_path, "Plagiarism of/in Article"))
        self.assertEqual(cases[0]["authors"], ["Jane Doe", "John Smith"])

    def test_doi_url_prefix_is_normalized(self):
        write_csv(self.csv_path, [
            {"Title": "A", "OriginalPaperDOI": "https://doi.org/10.1/a", "Reason": "Plagiarism of/in Article;"},
        ])
        cases = list(brw.load_plagiarism_cases(self.csv_path, "Plagiarism of/in Article"))
        self.assertEqual(cases[0]["doi"], "10.1/a")


class TestWriteRetractionMetadata(unittest.TestCase):
    def test_writes_keyed_by_lowercased_doi(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "meta.json"
            cases = [{"doi": "10.1/ABC", "title": "A Paper"}]
            brw.write_retraction_metadata(cases, out_path)
            data = json.loads(out_path.read_text())
            self.assertIn("10.1/abc", data)
            self.assertEqual(data["10.1/abc"]["title"], "A Paper")


if __name__ == "__main__":
    unittest.main()
