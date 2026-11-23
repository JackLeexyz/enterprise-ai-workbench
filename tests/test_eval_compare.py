import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evaluate_retrieval import compare, evaluate


def report(cases, dataset="a", fixture="b"):
    return {"dataset_sha256": dataset, "fixture_sha256": fixture, "cases": cases}


def case(name, passed, violations=None):
    return {"id": name, "contract_passed": passed, "security_violations": violations or []}


class EvaluationComparisonTests(unittest.TestCase):
    def test_reports_regressions_improvements_and_security(self):
        old = report([case("a", True), case("b", False)])
        new = report([case("a", False, ["restricted"]), case("b", True)])
        result = compare(old, new)
        self.assertEqual(result["regressions"], ["a"])
        self.assertEqual(result["improvements"], ["b"])
        self.assertEqual(result["security_regressions"], ["a"])

    def test_rejects_mismatched_or_duplicate_cases(self):
        old = report([case("a", True)])
        with self.assertRaises(ValueError):
            compare(old, report([case("a", True)], dataset="different"))
        with self.assertRaises(ValueError):
            compare(old, report([case("a", True), case("a", False)]))
        with self.assertRaises(ValueError):
            compare(old, report([case("b", True)]))

    def test_duplicate_dataset_ids_fail_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.json"
            path.write_text(json.dumps({"cases": [{"id": "duplicate"}, {"id": "duplicate"}]}))
            with self.assertRaisesRegex(ValueError, "unique"):
                evaluate(path, directory)

    def test_cli_cannot_overwrite_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            baseline.write_text('{"sentinel": true}')
            result = subprocess.run([sys.executable, "evaluate_retrieval.py", "--baseline", str(baseline),
                                     "--out", str(baseline)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not overwrite", result.stderr)
            self.assertEqual(json.loads(baseline.read_text()), {"sentinel": True})


if __name__ == "__main__":
    unittest.main()
