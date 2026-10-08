import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_harness.checks import CHECK_NAMES, evaluate_checks
from project_harness.cli import load_policy, main


def valid_policy():
    return {
        "version": "1.4.0",
        "required_paths": ["README.md"],
        "checks": {name: False for name in CHECK_NAMES},
    }


class PolicyValidationTest(unittest.TestCase):
    def load(self, root, policy):
        (root / "harness").mkdir(exist_ok=True)
        (root / "harness/policy.json").write_text(json.dumps(policy), encoding="utf-8")
        return load_policy(root)

    def test_policy_fields_and_types(self):
        invalid = [
            (None, "JSON 객체"),
            ({**valid_policy(), "version": " "}, "version"),
            ({**valid_policy(), "version": 1}, "version"),
            *[
                ({key: value for key, value in valid_policy().items() if key != field}, field)
                for field in ("version", "required_paths", "checks")
            ],
            ({**valid_policy(), "required_paths": "README.md"}, "required_paths"),
            ({**valid_policy(), "required_paths": [None]}, "required_paths[0]"),
            ({**valid_policy(), "required_paths": [""]}, "required_paths[0]"),
            ({**valid_policy(), "checks": []}, "checks"),
            ({**valid_policy(), "checks": {"json_syntax": 1}}, "json_syntax"),
            ({**valid_policy(), "checks": {"typo": False}}, "typo"),
            ({**valid_policy(), "required_path": []}, "required_path"),
            ({**valid_policy(), "autofix": {"enabled": "false"}}, "autofix.enabled"),
            ({**valid_policy(), "autofix": []}, "autofix"),
            ({**valid_policy(), "autofix": {"unknown": False}}, "autofix"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for policy, message in invalid:
                with self.subTest(policy=policy):
                    loaded, finding = self.load(root, policy)
                    self.assertIsNone(loaded)
                    self.assertEqual(finding.check_id, "HAR-POLICY-001")
                    self.assertIn(message, finding.message)

    def test_paths_stay_inside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            outside = Path(directory) / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            (root / "escaped.md").symlink_to(outside)
            for value in ("../outside.md", str(outside), "escaped.md", "C:\\outside.md", "bad\x00path"):
                with self.subTest(path=value):
                    policy = {**valid_policy(), "required_paths": [value]}
                    loaded, finding = self.load(root, policy)
                    self.assertIsNone(loaded)
                    self.assertEqual(finding.check_id, "HAR-POLICY-001")
                    self.assertIn("required_paths[0]", finding.message)

    def test_valid_policy_and_explicit_disabled_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = valid_policy()
            loaded, finding = self.load(root, policy)
            self.assertEqual(loaded, policy)
            self.assertIsNone(finding)
            findings, executions = evaluate_checks(root, loaded)
            self.assertEqual(findings, [])
            self.assertTrue(all(item.status == "disabled" for item in executions))

    def test_invalid_paths_fail_even_when_check_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            findings, executions = evaluate_checks(
                Path(directory), {**valid_policy(), "required_paths": ["../outside.md"]}
            )
            self.assertEqual([item.check_id for item in findings], ["HAR-POLICY-001"])
            self.assertEqual([item.status for item in executions], ["failed"])

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            (root / "harness/policy.json").write_text(
                '{"version":"1.4.0","required_paths":[],"checks":{"json_syntax":true,"json_syntax":false}}',
                encoding="utf-8",
            )
            loaded, finding = load_policy(root)
            self.assertIsNone(loaded)
            self.assertIn("중복된 정책 항목: json_syntax", finding.message)

    def test_invalid_unicode_policy_still_writes_readable_failure(self):
        invalid = [
            json.dumps({**valid_policy(), "version": "\ud800"}),
            json.dumps({**valid_policy(), "required_paths": ["\ud800"]}),
            json.dumps({**valid_policy(), "\ud800": False}),
            json.dumps({**valid_policy(), "checks": {"\ud800": True}}),
            '{"version":"1.4.0","required_paths":[],"checks":{},"\\ud800":1,"\\ud800":2}',
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            for raw in invalid:
                with self.subTest(raw=raw):
                    (root / "harness/policy.json").write_text(raw, encoding="utf-8")
                    output = io.StringIO()
                    with patch("sys.argv", ["harness", "--root", str(root)]), contextlib.redirect_stdout(output):
                        code = main()
                    self.assertEqual(code, 1)
                    output.getvalue().encode("utf-8")
                    report = json.loads((root / "harness/reports/report.json").read_text(encoding="utf-8"))
                    self.assertEqual(report["policy_version"], "unavailable")
                    self.assertEqual(report["summary"]["failed"], 1)
                    self.assertEqual(report["findings"][0]["check_id"], "HAR-POLICY-001")

    def test_valid_unicode_strings_remain_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = {**valid_policy(), "required_paths": ["안내😀.md"]}
            loaded, finding = self.load(Path(directory), policy)
            self.assertEqual(loaded, policy)
            self.assertIsNone(finding)

    def test_omitted_check_still_defaults_to_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.json").write_text("{", encoding="utf-8")
            findings, executions = evaluate_checks(root, {"checks": {"required_paths": False}})
            self.assertIn("HAR-JSON-001", [item.check_id for item in findings])
            self.assertEqual(next(item.status for item in executions if item.name == "json_syntax"), "failed")

if __name__ == "__main__":
    unittest.main()
