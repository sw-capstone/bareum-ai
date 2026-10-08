import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_harness.checks import CHECK_NAMES
from project_harness.cli import execution_identity, main


def valid_policy():
    return {
        "version": "1.4.0",
        "required_paths": ["README.md"],
        "checks": {name: False for name in CHECK_NAMES},
    }


class ReportIdentityTest(unittest.TestCase):
    def run_cli(self, root, policy):
        (root / "harness").mkdir(exist_ok=True)
        (root / "harness/policy.json").write_text(json.dumps(policy), encoding="utf-8")
        with patch("sys.argv", ["harness", "check", "--root", str(root)]):
            with contextlib.redirect_stdout(io.StringIO()):
                code = main()
        return code, json.loads((root / "harness/reports/report.json").read_text(encoding="utf-8"))

    def test_no_git_metadata_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            code, report = self.run_cli(Path(directory), valid_policy())
            self.assertEqual(code, 0)
            execution = report["execution"]
            self.assertEqual(execution["environment"], "local")
            self.assertIsNone(execution["repository"])
            self.assertIsNone(execution["git_sha"])
            self.assertIsNone(execution["working_tree_dirty"])
            self.assertIsNone(execution["ci_run"])
            self.assertIn("repository", execution["unavailable"])
            self.assertIn("git_sha", execution["unavailable"])

    def test_github_actions_records_source_and_checkout_sha_separately(self):
        environment = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "sw-capstone/bareum-ai",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_WORKFLOW": "Harness",
            "GITHUB_JOB": "check",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/pull/20/merge",
            "GITHUB_SERVER_URL": "https://github.com",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, environment, clear=True):
            _, report = self.run_cli(Path(directory), valid_policy())
            execution = report["execution"]
            self.assertEqual(execution["environment"], "github_actions")
            self.assertEqual(execution["repository"], "sw-capstone/bareum-ai")
            self.assertIsNone(execution["git_sha"])
            self.assertEqual(execution["ci_run"]["event_sha"], "a" * 40)
            self.assertEqual(execution["ci_run"]["run_attempt"], "2")
            self.assertEqual(execution["ci_run"]["url"], "https://github.com/sw-capstone/bareum-ai/actions/runs/123/attempts/2")

    def test_partial_ci_and_invalid_policy_still_write_report(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CI": "true"}, clear=True):
            code, report = self.run_cli(Path(directory), {**valid_policy(), "version": ""})
            self.assertEqual(code, 1)
            self.assertEqual(report["execution"]["environment"], "ci")
            self.assertEqual(report["checks"][0]["name"], "policy")
            self.assertEqual(report["summary"]["failed"], 1)

    def test_missing_git_executable_does_not_abort_reporting(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with patch("project_harness.cli.subprocess.run", side_effect=FileNotFoundError):
                code, report = self.run_cli(Path(directory), valid_policy())
            self.assertEqual(code, 0)
            self.assertIsNone(report["execution"]["git_sha"])

    def test_stale_github_variables_do_not_mark_a_local_run_as_ci(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "GITHUB_REPOSITORY": "wrong/repo"}, clear=True):
                identity = execution_identity(Path(directory))
            self.assertEqual(identity["environment"], "local")
            self.assertIsNone(identity["repository"])
            self.assertIsNone(identity["git_sha"])

    def test_incomplete_github_identity_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True):
                _, report = self.run_cli(Path(directory), valid_policy())
            identity = report["execution"]
            self.assertIsNone(identity["ci_run"]["url"])
            self.assertIn("ci_run.run_id", identity["unavailable"])

    def test_malformed_json_policy_still_produces_an_identified_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / "harness").mkdir()
            (root / "harness/policy.json").write_text("{", encoding="utf-8")
            with patch("sys.argv", ["harness", "--root", str(root)]), contextlib.redirect_stdout(io.StringIO()):
                code = main()
            report = json.loads((root / "harness/reports/report.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(report["policy_version"], "unavailable")
            self.assertEqual(report["execution"]["environment"], "local")

    def test_local_git_identity_and_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            def git(*arguments):
                return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            (root / "README.md").write_text("initial", encoding="utf-8")
            git("add", "README.md")
            git("-c", "user.name=Harness Test", "-c", "user.email=harness@example.invalid", "commit", "-qm", "fixture")
            git("remote", "add", "origin", "git@github.com:sw-capstone/bareum-ai.git")
            expected_sha = git("rev-parse", "HEAD")
            _, report = self.run_cli(root, valid_policy())
            self.assertEqual(report["execution"]["repository"], "sw-capstone/bareum-ai")
            self.assertEqual(report["execution"]["git_sha"], expected_sha)
            self.assertTrue(report["execution"]["working_tree_dirty"])

            git("add", "harness")
            git("-c", "user.name=Harness Test", "-c", "user.email=harness@example.invalid", "commit", "-qm", "policy fixture")
            clean_sha = git("rev-parse", "HEAD")
            identity = execution_identity(root)
            self.assertFalse(identity["working_tree_dirty"])
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_SHA": "b" * 40}, clear=True):
                identity = execution_identity(root)
            self.assertEqual(identity["git_sha"], clean_sha)
            self.assertEqual(identity["ci_run"]["event_sha"], "b" * 40)

            for remote in ("https://user:example@github.com/sw-capstone/bareum-ai.git", "../local-repo", "https://[broken"):
                git("remote", "set-url", "origin", remote)
                identity = execution_identity(root)
                if remote.startswith("https://user"):
                    self.assertEqual(identity["repository"], "sw-capstone/bareum-ai")
                    self.assertNotIn("user:example", json.dumps(identity))
                else:
                    self.assertIsNone(identity["repository"])

if __name__ == "__main__":
    unittest.main()
