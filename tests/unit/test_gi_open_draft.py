import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "general_index"))
import gi_open_draft  # noqa: E402


class TestDraftPath(unittest.TestCase):
    def test_only_drafts_under_the_report_dir(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "dupe_publisher"
            (root / "GI-1").mkdir(parents=True)
            eml = root / "GI-1" / "GI-1-email-institution.eml"
            eml.write_text("x")
            (root / "GI-1" / "notes.md").write_text("x")
            outside = Path(d) / "other.eml"
            outside.write_text("x")
            with mock.patch.object(gi_open_draft.gi_case_findings, "REPORT_DIR", root):
                self.assertEqual(gi_open_draft.draft_path(gi_open_draft.link(eml)), eml.resolve())
                for bad in (gi_open_draft.link(outside), gi_open_draft.link(root / "GI-1" / "notes.md"),
                            gi_open_draft.link(root / "GI-1" / "missing.eml"),
                            gi_open_draft.link(root / "GI-1" / ".." / ".." / "other.eml"),
                            eml.resolve().as_uri()):
                    with self.assertRaises(ValueError):
                        gi_open_draft.draft_path(bad)

    def test_link_survives_spaces(self):
        self.assertIn("%20", gi_open_draft.link("/tmp/a b/GI-1-email-publisher.eml"))


if __name__ == "__main__":
    unittest.main()
