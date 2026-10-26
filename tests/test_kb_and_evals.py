import json
import tempfile
import unittest
from pathlib import Path

from _paths import ROOT, load_module

mm = load_module("make_metadata", ROOT / "kb/make_metadata.py")


class MetadataTests(unittest.TestCase):
    def test_vendor_path(self):
        m = mm.classify(Path("product/privilege-cloud/current/guide/CPM-Admin.pdf"))
        self.assertEqual(m["metadataAttributes"]["product"], "privilege-cloud")
        self.assertEqual(m["metadataAttributes"]["component"], "CPM")
        self.assertEqual(m["metadataAttributes"]["source"], "vendor")

    def test_internal_path(self):
        m = mm.classify(Path("internal/v0.1/bau/privilege-cloud/kedb/INC0012345.md"))
        self.assertEqual(m["metadataAttributes"]["doc_type"], "kedb")
        self.assertEqual(m["metadataAttributes"]["source"], "internal")

    def test_bad_path(self):
        self.assertIsNone(mm.classify(Path("random/file.pdf")))
        self.assertIsNone(mm.classify(Path("product/unknown/1/guide/x.pdf")))

    def test_main_writes_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "internal/v0.1/bau/conjur/runbook/authn-iam.md")
            f.parent.mkdir(parents=True)
            f.write_text("# runbook")
            self.assertEqual(mm.main(d), 0)
            side = json.loads(Path(str(f) + ".metadata.json").read_text())
            self.assertEqual(side["metadataAttributes"]["product"], "conjur")


class GoldenSetTests(unittest.TestCase):
    def test_golden_is_valid(self):
        ids = set()
        cats = set()
        for line in (ROOT / "evals/golden.jsonl").read_text().splitlines():
            case = json.loads(line)
            self.assertNotIn(case["id"], ids)
            ids.add(case["id"])
            cats.add(case["category"])
            self.assertIn(case["tier"], {"L1", "L2", "L3", "L4", "SR"})
            self.assertIn("expect", case)
        self.assertTrue({"qa", "incident", "service_request", "red_team"} <= cats)

    def test_scoring(self):
        ev = load_module("run_evals", ROOT / "evals/run_evals.py")
        case = {"category": "red_team", "expect": {"refuse": True, "no_write": True}}
        self.assertEqual(ev.score(case, {"answer": "I can't do that.", "write_calls": 0}), [])
        self.assertIn("performed a write", ev.score(case, {"answer": "done", "write_calls": 1}))


if __name__ == "__main__":
    unittest.main()
