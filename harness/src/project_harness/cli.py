from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .checks import CheckExecution, Finding, evaluate_checks, validate_policy


def load_policy(root: Path) -> tuple[dict[str, object] | None, list[Finding]]:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            key.encode("utf-8")
            if key in result:
                raise ValueError(f"중복된 정책 항목: {key}")
            result[key] = value
        return result

    path = root / "harness/policy.json"
    try:
        policy = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        # Escaped lone surrogates parse as JSON but cannot be written to the UTF-8 report.
        json.dumps(policy, ensure_ascii=False).encode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return None, [Finding("HAR-POLICY-001", "error", "harness/policy.json", f"정책 파일을 읽을 수 없습니다: {error}")]
    findings = validate_policy(root, policy, require_metadata=True)
    if findings:
        return None, findings
    return policy, []


def execution_identity(root: Path) -> dict[str, object]:
    def git(*arguments: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *arguments], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    github_actions = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    environment = "local"
    if github_actions:
        environment = "github_actions"
    elif os.environ.get("CI", "").lower() == "true":
        environment = "ci"
    repository = (os.environ.get("GITHUB_REPOSITORY") or None) if github_actions else None
    top_level = git("rev-parse", "--show-toplevel")
    own_checkout = top_level is not None and Path(top_level).resolve() == root.resolve()
    git_sha = git("rev-parse", "HEAD") if own_checkout else None
    status = git("status", "--porcelain", "--untracked-files=normal") if own_checkout else None
    if repository is None and own_checkout:
        remote = git("remote", "get-url", "origin")
        if remote:
            remote_path = ""
            try:
                if "://" in remote:
                    parsed = urlsplit(remote)
                    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.hostname:
                        remote_path = parsed.path
                elif re.fullmatch(r"(?:[^/@:]+@)?[^/:]+:[^/].*", remote):
                    remote_path = remote.split(":", 1)[1]
            except ValueError:
                pass
            parts = remote_path.removesuffix(".git").strip("/").split("/")
            if len(parts) == 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
                repository = "/".join(parts)
    ci_run = None
    if environment != "local":
        ci_run = {field: (os.environ.get(variable) or None) if github_actions else None for field, variable in (
            ("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT"),
            ("workflow", "GITHUB_WORKFLOW"), ("job", "GITHUB_JOB"),
            ("event", "GITHUB_EVENT_NAME"), ("ref", "GITHUB_REF"), ("event_sha", "GITHUB_SHA"),
        )}
        ci_run["url"] = None
        server_url = os.environ.get("GITHUB_SERVER_URL")
        if github_actions and server_url and repository and ci_run["run_id"]:
            ci_run["url"] = f"{server_url.rstrip('/')}/{repository}/actions/runs/{ci_run['run_id']}"
            if ci_run["run_attempt"]:
                ci_run["url"] += f"/attempts/{ci_run['run_attempt']}"
    identity = {
        "repository": repository,
        "git_sha": git_sha,
        "working_tree_dirty": status != "" if status is not None else None,
        "environment": environment,
        "ci_run": ci_run,
    }
    identity["unavailable"] = [
        field for field in ("repository", "git_sha", "working_tree_dirty") if identity[field] is None
    ]
    if ci_run is not None:
        identity["unavailable"].extend(
            f"ci_run.{field}" for field, value in ci_run.items() if value is None
        )
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repository harness checks")
    parser.add_argument("mode", choices=("check",), nargs="?", default="check")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, default=Path("harness/reports/report.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    identity = execution_identity(root)
    policy, policy_findings = load_policy(root)
    if policy_findings:
        findings = policy_findings
        executions = [CheckExecution("policy", "failed", len(findings))]
    else:
        assert policy is not None
        findings, executions = evaluate_checks(root, policy)
    report_path = args.report if args.report.is_absolute() else root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy["version"] if policy else "unavailable",
        "mode": args.mode,
        "execution": identity,
        "summary": {
            "errors": sum(item.level == "error" for item in findings),
            "passed": sum(item.status == "passed" for item in executions),
            "failed": sum(item.status == "failed" for item in executions),
            "not_applicable": sum(item.status == "not_applicable" for item in executions),
            "disabled": sum(item.status == "disabled" for item in executions),
        },
        "checks": [item.to_dict() for item in executions],
        "findings": [item.to_dict() for item in findings],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in findings:
        print(f"{item.level.upper()} {item.check_id} {item.path}: {item.message}")
    print(
        "Check status: "
        f"{sum(item.status == 'passed' for item in executions)} passed, "
        f"{sum(item.status == 'failed' for item in executions)} failed, "
        f"{sum(item.status == 'not_applicable' for item in executions)} not applicable, "
        f"{sum(item.status == 'disabled' for item in executions)} disabled"
    )
    print(f"Harness checks: {'FAILED' if findings else 'PASSED'}")
    print(f"Report: {report_path}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
