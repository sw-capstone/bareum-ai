import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_harness import schema as schema_module
from project_harness.checks import (
    check_id_registry,
    check_json_files,
    check_product_spec,
    check_required_paths,
    check_schema_examples,
    check_schema_headers,
    check_secret_patterns,
    evaluate_checks,
    run_checks,
)
from project_harness.cli import load_policy
from project_harness.schema import SchemaValidationError


class HarnessChecksTest(unittest.TestCase):
    def test_required_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("ok\n", encoding="utf-8")
            self.assertEqual(check_required_paths(root, {"required_paths": ["README.md"]}), [])

    def test_required_paths_reject_non_string_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            findings = check_required_paths(Path(directory), {"required_paths": [42]})
            self.assertEqual([finding.check_id for finding in findings], ["HAR-STRUCT-001"])

    def test_invalid_policy_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "harness"
            harness.mkdir()
            (harness / "policy.json").write_text("{", encoding="utf-8")
            policy, finding = load_policy(root)
            self.assertIsNone(policy)
            self.assertEqual(finding.check_id if finding else None, "HAR-POLICY-001")

    def test_non_boolean_check_setting_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "harness"
            harness.mkdir()
            (harness / "policy.json").write_text(
                '{"version":"1", "checks":{"required_paths":null}}', encoding="utf-8"
            )
            policy, finding = load_policy(root)
            self.assertIsNone(policy)
            self.assertEqual(finding.check_id if finding else None, "HAR-POLICY-001")

            findings, executions = evaluate_checks(root, {"checks": {"required_paths": None}})
            self.assertEqual([item.check_id for item in findings], ["HAR-POLICY-001"])
            self.assertEqual([item.status for item in executions], ["failed"])

    def test_invalid_json_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.json").write_text("{", encoding="utf-8")
            findings = check_json_files(root)
            self.assertEqual([finding.check_id for finding in findings], ["HAR-JSON-001"])

    def test_schema_headers_are_checked_outside_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / "rules"
            rules.mkdir()
            (rules / "rules.schema.json").write_text('{"type":"object"}', encoding="utf-8")
            findings = check_schema_headers(root)
            self.assertEqual([finding.check_id for finding in findings], ["HAR-SCHEMA-001"])

    def test_invalid_schema_json_does_not_crash_header_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.schema.json").write_text("{", encoding="utf-8")
            self.assertEqual(check_schema_headers(root), [])

    def test_schema_header_rejects_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.schema.json").write_text("null", encoding="utf-8")
            findings = check_schema_headers(root)
            self.assertEqual([finding.check_id for finding in findings], ["HAR-SCHEMA-001"])

    def test_fallback_schema_validator_checks_any_of(self) -> None:
        with patch.object(schema_module, "Draft202012Validator", None):
            with self.assertRaises(SchemaValidationError):
                schema_module.validate(123, {"anyOf": [{"type": "string"}, {"type": "boolean"}]})

    def test_schema_example_validation_checks_types_and_enums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.schema.json").write_text(
                '{"type":"object","required":["status"],"properties":{"status":{"enum":["ok"]}}}',
                encoding="utf-8",
            )
            (root / "value.json").write_text('{"status":"broken"}', encoding="utf-8")
            findings = check_schema_examples(
                root,
                {"schema_examples": {"value.schema.json": "value.json"}},
            )
            self.assertEqual([finding.check_id for finding in findings], ["HAR-SCHEMA-002"])

    def test_missing_configured_schema_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            findings = check_schema_examples(
                root,
                {"schema_examples": {"missing.schema.json": "missing.json"}},
            )
            self.assertEqual([finding.check_id for finding in findings], ["HAR-SCHEMA-002", "HAR-SCHEMA-002"])

    def test_secret_scan_includes_env_typescript_and_examples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret_value = "accidentally-real-value"
            (root / ".env.example").write_text(f"API_KEY={secret_value}\n", encoding="utf-8")
            (root / "config.ts").write_text(f"const TOKEN = {secret_value};\n", encoding="utf-8")
            findings = check_secret_patterns(root)
            self.assertEqual(len(findings), 2)
            self.assertEqual({finding.path for finding in findings}, {".env.example", "config.ts"})

    def test_disabled_check_is_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = {"required_paths": ["missing.md"], "checks": {"required_paths": False}}
            findings = run_checks(root, policy)
            self.assertNotIn("HAR-STRUCT-001", [finding.check_id for finding in findings])

    def test_duplicate_id_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            registry = '{"items":[{"id":"RULE-REG-001"},{"id":"RULE-REG-001"}]}'
            (contracts / "id-registry.json").write_text(registry, encoding="utf-8")
            findings = check_id_registry(root)
            self.assertIn("HAR-ID-002", [finding.check_id for finding in findings])

    def test_invalid_id_registry_json_does_not_abort_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            (contracts / "id-registry.json").write_text("{", encoding="utf-8")
            findings = run_checks(root, {"checks": {"required_paths": False}})
            self.assertIn("HAR-JSON-001", [finding.check_id for finding in findings])

    def test_invalid_id_registry_shape_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            (contracts / "id-registry.json").write_text('{"items":"invalid"}', encoding="utf-8")
            findings = check_id_registry(root)
            self.assertEqual([finding.check_id for finding in findings], ["HAR-ID-003"])

    def test_id_registry_requires_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            (contracts / "id-registry.json").write_text("{}", encoding="utf-8")
            findings = check_id_registry(root)
            self.assertEqual([finding.check_id for finding in findings], ["HAR-ID-003"])

    def test_execution_status_distinguishes_not_applicable_and_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, executions = evaluate_checks(
                root,
                {"checks": {"required_paths": False}, "product_spec": {"path": "missing.md"}},
            )
            statuses = {item.name: item.status for item in executions}
            self.assertEqual(statuses["required_paths"], "disabled")
            self.assertEqual(statuses["schema_examples"], "not_applicable")
            self.assertEqual(statuses["id_registry"], "not_applicable")
            self.assertEqual(statuses["product_spec"], "failed")

    def test_missing_registry_fields_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            (contracts / "id-registry.json").write_text('{"items":[{"id":"HAR-ID-001"}]}', encoding="utf-8")
            findings = check_id_registry(root)
            self.assertIn("HAR-ID-003", [finding.check_id for finding in findings])

    def test_missing_registered_path_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            registry = (
                '{"items":[{"id":"HAR-ID-001","type":"HAR","status":"active",'
                '"owner":"harness-maintainers","description":"test","path":"missing.md"}]}'
            )
            (contracts / "id-registry.json").write_text(registry, encoding="utf-8")
            findings = check_id_registry(root)
            self.assertIn("HAR-ID-004", [finding.check_id for finding in findings])

    def test_id_type_mismatch_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            asset = root / "asset.md"
            asset.write_text("ok\n", encoding="utf-8")
            registry = (
                '{"items":[{"id":"HAR-ID-001","type":"DEC","status":"active",'
                '"owner":"harness-maintainers","description":"test","path":"asset.md"}]}'
            )
            (contracts / "id-registry.json").write_text(registry, encoding="utf-8")
            findings = check_id_registry(root)
            self.assertIn("HAR-ID-003", [finding.check_id for finding in findings])

    def test_unknown_related_id_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "packages" / "contracts"
            contracts.mkdir(parents=True)
            asset = root / "asset.md"
            asset.write_text("ok\n", encoding="utf-8")
            registry = (
                '{"items":[{"id":"HAR-ID-001","type":"HAR","status":"active",'
                '"owner":"harness-maintainers","description":"test","path":"asset.md",'
                '"depends":["HAR-ID-999"]}]}'
            )
            (contracts / "id-registry.json").write_text(registry, encoding="utf-8")
            findings = check_id_registry(root)
            self.assertIn("HAR-ID-005", [finding.check_id for finding in findings])

    def test_github_managed_product_spec_requires_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "docs/product"
            path.mkdir(parents=True)
            (path / "planning-final-v1.1.md").write_text("문서 상태: GitHub 관리 기준본\n", encoding="utf-8")
            findings = check_product_spec(
                root,
                {"product_spec": {"path": "docs/product/planning-final-v1.1.md", "required_markers": ["## 0. 한 줄"]}},
            )
            self.assertEqual([finding.check_id for finding in findings], ["HAR-PRODUCT-001"])
