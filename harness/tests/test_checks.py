import tempfile
import unittest
from pathlib import Path

from project_harness.checks import (
    check_json_files,
    check_markdown_links,
    check_required_paths,
    check_secret_patterns,
    evaluate_checks,
    run_checks,
)
from project_harness.cli import load_policy


class HarnessChecksTest(unittest.TestCase):
    def test_unknown_check_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            (root / "harness/policy.json").write_text(
                '{"version":"1.4.0","required_paths":[],"checks":{"unknown":false}}', encoding="utf-8"
            )
            policy, findings = load_policy(root)
            self.assertIsNone(policy)
            self.assertEqual([item.check_id for item in findings], ["HAR-POLICY-001"])

    def test_required_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("ok\n", encoding="utf-8")
            self.assertEqual(check_required_paths(root, {"required_paths": ["README.md"]}), [])

    def test_missing_required_path_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            findings = check_required_paths(Path(directory), {"required_paths": ["README.md"]})
            self.assertEqual([item.check_id for item in findings], ["HAR-STRUCT-001"])

    def test_required_paths_reject_non_string_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            findings = check_required_paths(Path(directory), {"required_paths": [42]})
            self.assertEqual([item.check_id for item in findings], ["HAR-STRUCT-001"])

    def test_invalid_policy_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            (root / "harness/policy.json").write_text("{", encoding="utf-8")
            policy, findings = load_policy(root)
            self.assertIsNone(policy)
            self.assertEqual([item.check_id for item in findings], ["HAR-POLICY-001"])

    def test_non_boolean_check_setting_is_rejected(self) -> None:
        findings, executions = evaluate_checks(Path.cwd(), {"checks": {"required_paths": None}})
        self.assertEqual([item.check_id for item in findings], ["HAR-POLICY-001"])
        self.assertEqual([item.status for item in executions], ["failed"])

    def test_invalid_json_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.json").write_text("{", encoding="utf-8")
            findings = check_json_files(root)
            self.assertEqual([item.check_id for item in findings], ["HAR-JSON-001"])

    def test_missing_markdown_link_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("[missing](missing.md)\n", encoding="utf-8")
            findings = check_markdown_links(root)
            self.assertEqual([item.check_id for item in findings], ["HAR-DOC-002"])

    def test_external_markdown_link_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[server](https://github.com/sw-capstone/bareum-server)\n", encoding="utf-8"
            )
            self.assertEqual(check_markdown_links(root), [])

    def test_secret_scan_includes_env_source_and_configuration_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = "accidentally-real-value"
            (root / ".env.example").write_text(f"API_KEY={value}\n", encoding="utf-8")
            (root / "config.ts").write_text(f"const TOKEN = {value};\n", encoding="utf-8")
            key = "API" + "_KEY"
            password = "PASS" + "WORD"
            (root / "Config.java").write_text(f'String {key} = "{value}";\n', encoding="utf-8")
            (root / "application.properties").write_text(f"{key}={value}\n", encoding="utf-8")
            (root / ".env.secrets").write_text(f"DB_{password}={value}\n", encoding="utf-8")
            findings = check_secret_patterns(root)
            self.assertEqual(
                {item.path for item in findings},
                {".env.example", "config.ts", "Config.java", "application.properties", ".env.secrets"},
            )

    def test_secret_scan_ignores_explicit_example_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            placeholder = "replace" + "-me"
            (root / ".env.example").write_text(f"DB_PASSWORD={placeholder}\n", encoding="utf-8")
            self.assertEqual(check_secret_patterns(root), [])

    def test_disabled_check_is_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = {"required_paths": ["missing.md"], "checks": {"required_paths": False}}
            findings = run_checks(root, policy)
            self.assertNotIn("HAR-STRUCT-001", [item.check_id for item in findings])

    def test_all_current_checks_execute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = {
                "required_paths": [],
                "checks": {
                    "required_paths": True,
                    "json_syntax": True,
                    "markdown_links": True,
                    "secret_patterns": True,
                },
            }
            findings, executions = evaluate_checks(root, policy)
            self.assertEqual(findings, [])
            self.assertEqual([item.status for item in executions], ["passed"] * 4)


if __name__ == "__main__":
    unittest.main()
