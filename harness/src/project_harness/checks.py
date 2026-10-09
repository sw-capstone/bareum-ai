from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote

SKIP_PARTS = {".git", ".venv", "build", "dist", "node_modules", "reports", "__pycache__"}
CHECK_NAMES = frozenset({"required_paths", "json_syntax", "markdown_links", "secret_patterns"})
TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".ini",
    ".java",
    ".json",
    ".md",
    ".properties",
    ".py",
    ".sh",
    ".kts",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


@dataclass(frozen=True)
class Finding:
    check_id: str
    level: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class CheckExecution:
    name: str
    status: str
    finding_count: int

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and not SKIP_PARTS.intersection(path.parts):
            yield path


def policy_path_error(root: Path, value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return "비어 있지 않은 경로 문자열이어야 합니다."
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return "저장소 내부의 상대 경로여야 합니다."
    try:
        (root / value).resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        return "경로가 저장소 밖을 가리키거나 해석할 수 없습니다."
    return None


def validate_policy(root: Path, policy: object, *, require_metadata: bool = False) -> list[Finding]:
    findings: list[Finding] = []

    def error(field: str, message: str) -> None:
        findings.append(Finding("HAR-POLICY-001", "error", f"harness/policy.json#{field}", f"{field}: {message}"))

    if not isinstance(policy, dict):
        error("policy", "JSON 객체여야 합니다.")
        return findings
    checks = policy.get("checks", {})
    if not isinstance(checks, dict):
        error("checks", "검사 이름과 true 또는 false 값으로 구성된 객체여야 합니다.")
    else:
        unknown = set(checks) - CHECK_NAMES
        if unknown:
            error("checks", f"알 수 없는 검사 이름: {', '.join(sorted(map(str, unknown)))}")
        for name, value in checks.items():
            if not isinstance(value, bool):
                error(f"checks.{name}", "true 또는 false여야 합니다.")
    if require_metadata:
        for field in ("version", "required_paths", "checks"):
            if field not in policy:
                error(field, "필수 항목이 없습니다.")
    if "version" in policy:
        version = policy["version"]
        if not isinstance(version, str) or not version.strip():
            error("version", "비어 있지 않은 문자열이어야 합니다.")
    allowed = {"version", "required_paths", "checks", "autofix"}
    unknown_fields = set(policy) - allowed
    if unknown_fields:
        error("policy", f"알 수 없는 항목: {', '.join(sorted(map(str, unknown_fields)))}")
    paths = policy.get("required_paths", [])
    if not isinstance(paths, list):
        error("required_paths", "경로 문자열 배열이어야 합니다.")
    else:
        for index, value in enumerate(paths):
            message = policy_path_error(root, value)
            if message:
                error(f"required_paths[{index}]", message)
    if "autofix" in policy:
        autofix = policy["autofix"]
        if not isinstance(autofix, dict):
            error("autofix", "객체여야 합니다.")
        else:
            for field, value in autofix.items():
                if field not in {"enabled", "semantic_changes"}:
                    error("autofix", f"알 수 없는 항목: {field}")
                elif not isinstance(value, bool):
                    error(f"autofix.{field}", "true 또는 false여야 합니다.")
    return findings


def check_required_paths(root: Path, policy: dict[str, object]) -> list[Finding]:
    required_paths = policy.get("required_paths", [])
    if not isinstance(required_paths, list):
        return [
            Finding(
                "HAR-STRUCT-001",
                "error",
                "harness/policy.json",
                "required_paths는 경로 문자열 배열이어야 합니다.",
            )
        ]
    findings: list[Finding] = []
    for index, value in enumerate(required_paths):
        message = policy_path_error(root, value)
        if message:
            findings.append(
                Finding(
                    "HAR-STRUCT-001",
                    "error",
                    f"harness/policy.json#required_paths[{index}]",
                    message,
                )
            )
        elif not (root / value).exists():
            findings.append(Finding("HAR-STRUCT-001", "error", value, "필수 경로가 없습니다."))
    return findings


def check_json_files(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(root):
        if path.suffix != ".json":
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            findings.append(
                Finding("HAR-JSON-001", "error", str(path.relative_to(root)), f"JSON 파싱 실패: {error}")
            )
    return findings


def check_markdown_links(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    resolved_root = root.resolve()
    for path in iter_files(root):
        if path.suffix != ".md":
            continue
        content = path.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(content):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = (path.parent / unquote(target.split("#", 1)[0])).resolve()
            try:
                target_path.relative_to(resolved_root)
            except ValueError:
                findings.append(
                    Finding("HAR-DOC-001", "error", str(path.relative_to(root)), "링크가 저장소 밖을 가리킵니다.")
                )
                continue
            if not target_path.exists():
                findings.append(
                    Finding("HAR-DOC-002", "error", str(path.relative_to(root)), f"없는 문서 링크: {target}")
                )
    return findings


def check_secret_patterns(root: Path) -> list[Finding]:
    token_pattern = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
    assignment_pattern = re.compile(
        r"\b(?:[A-Z0-9]+[_-])?(?:API[_-]?KEY|CLIENT[_-]?SECRET|SECRET|TOKEN|PASSWORD|PASSWD)\b"
        r"\s*[:=]\s*[\"']?([^\s\"'${}<]{8,})",
        re.IGNORECASE,
    )
    placeholder = re.compile(
        r"^(?:change[-_ ]?me|replace[-_ ]?me|your[-_ ].*|example|placeholder|dummy|sample|test|redacted|not[-_ ]?set|bareum[-_ ]password)$",
        re.IGNORECASE,
    )
    findings: list[Finding] = []
    for path in iter_files(root):
        is_env_file = path.name == ".env" or path.name.startswith(".env.")
        is_docker_file = path.name == "Dockerfile" or path.name.startswith("Dockerfile.")
        if not is_env_file and not is_docker_file and path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        has_secret_value = bool(token_pattern.search(text)) or any(
            not placeholder.fullmatch(match.group(1).rstrip(";,)"))
            for match in assignment_pattern.finditer(text)
        )
        if has_secret_value:
            findings.append(
                Finding("HAR-SEC-001", "error", str(path.relative_to(root)), "비밀정보로 보이는 값이 있습니다.")
            )
    return findings


def evaluate_checks(root: Path, policy: dict[str, object]) -> tuple[list[Finding], list[CheckExecution]]:
    policy_findings = validate_policy(root, policy)
    if policy_findings:
        return policy_findings, [CheckExecution("policy", "failed", len(policy_findings))]
    enabled = policy.get("checks", {})

    checks = (
        ("required_paths", lambda: check_required_paths(root, policy)),
        ("json_syntax", lambda: check_json_files(root)),
        ("markdown_links", lambda: check_markdown_links(root)),
        ("secret_patterns", lambda: check_secret_patterns(root)),
    )
    findings: list[Finding] = []
    executions: list[CheckExecution] = []
    for name, check in checks:
        if not enabled.get(name, True):
            executions.append(CheckExecution(name, "disabled", 0))
            continue
        check_findings = check()
        findings.extend(check_findings)
        status = "failed" if any(item.level == "error" for item in check_findings) else "passed"
        executions.append(CheckExecution(name, status, len(check_findings)))
    return findings, executions


def run_checks(root: Path, policy: dict[str, object]) -> list[Finding]:
    findings, _ = evaluate_checks(root, policy)
    return findings
