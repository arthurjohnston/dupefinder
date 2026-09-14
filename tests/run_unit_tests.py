#!/usr/bin/env python3
"""Run the unit test suite (tests/unit/*.py) -- true unit tests for pure
logic (text_overlap.py, lsh_index.py's hashing/pairing) and retrieve_papers.py
(slugify/normalize_doi/make_key, RateLimiter, retry/backoff, concurrent_fetch,
PaperStore/ThreadLocalPaperStore, process_paper's branching), all with the
network mocked out or, where a real SQLite round-trip matters more than a
mock would prove, run against a real temp-file DB.

Distinct from tests/run_tests.py's back-tests: those run the real pipeline
end-to-end against known-outcome plagiarism cases, need the real network and
--email, and take real (if modest) wall-clock time. This suite needs neither
and finishes in well under a second -- run it on every change to the modules
it covers; run run_tests.py before trusting a change to the pipeline's actual
duplicate-finding behavior.

Plain stdlib unittest, no pytest dependency -- consistent with this project
having no build step or extra tooling beyond requirements.txt.

Usage:
    python3 tests/run_unit_tests.py
    python3 tests/run_unit_tests.py -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    verbosity = 2 if "-v" in sys.argv else 1
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(Path(__file__).parent / "unit"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
