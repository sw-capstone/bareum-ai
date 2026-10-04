from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .checks import CHECK_NAMES, CheckExecution, Finding, evaluate_checks


def load_policy(root: Path) -> tuple[dict[str, object] | None, Finding | None]:
    path = root / "harness/policy.json"
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, Finding("HAR-POLICY-001", "error", "harness/policy.json", f"정책 파일을 읽을 수 없습니다: {error}")
    checks = policy.get("checks") if isinstance(policy, dict) else None
    if (
        not isinstance(policy, dict)
        or not isinstance(policy.get("version"), str)
        or not isinstance(checks, dict)
        or any(not isinstance(value, bool) for value in checks.values())
    ):
        return None, Finding(
            "HAR-POLICY-001",
            "error",
            "harness/policy.json",
            "정책은 version 문자열과 true 또는 false 검사 설정을 가진 JSON 객체여야 합니다.",
        )
    unknown = set(checks) - CHECK_NAMES
    if unknown:
        return None, Finding(
            "HAR-POLICY-001", "error", "harness/policy.json",
            f"알 수 없는 검사 이름: {', '.join(sorted(unknown))}",
        )
    return policy, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repository harness checks")
    parser.add_argument("mode", choices=("check",), nargs="?", default="check")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, default=Path("harness/reports/report.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    policy, policy_finding = load_policy(root)
    if policy_finding:
        findings = [policy_finding]
        executions = [CheckExecution("policy", "failed", 1)]
    else:
        assert policy is not None
        findings, executions = evaluate_checks(root, policy)
    report_path = args.report if args.report.is_absolute() else root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy["version"] if policy else "unavailable",
        "mode": args.mode,
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
