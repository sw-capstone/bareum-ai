from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote

from .schema import SchemaValidationError, validate

SKIP_PARTS = {".git", ".venv", "node_modules", "reports", "__pycache__"}
TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
ID_PATTERN = re.compile(r"^[A-Z]+-[A-Z0-9]+-[0-9]{3}$")
ALLOWED_ID_TYPES = {"FEAT", "API", "RULE", "DATA", "EVAL", "TEST", "HAR", "DEC"}
ID_RELATION_FIELDS = ("implements", "depends", "verified_by")
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
        if not isinstance(value, str):
            findings.append(
                Finding(
                    "HAR-STRUCT-001",
                    "error",
                    f"harness/policy.json#required_paths[{index}]",
                    "필수 경로는 문자열이어야 합니다.",
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


def check_schema_headers(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(root):
        if not path.name.endswith(".schema.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not all(key in data for key in ("$schema", "$id", "type")):
            findings.append(
                Finding("HAR-SCHEMA-001", "error", str(path.relative_to(root)), "Schema 필수 헤더가 없습니다.")
            )
    return findings


def check_schema_examples(root: Path, policy: dict[str, object]) -> list[Finding]:
    """Validate declared fixtures against their JSON Schemas without external packages."""
    findings: list[Finding] = []
    config = policy.get("schema_examples", {})
    if not isinstance(config, dict):
        return [
            Finding(
                "HAR-SCHEMA-002",
                "error",
                "harness/policy.json",
                "schema_examples는 Schema 경로와 예시 경로의 객체여야 합니다.",
            )
        ]
    for schema_value, example_value in config.items():
        schema_path = root / str(schema_value)
        example_path = root / str(example_value)
        missing_paths = [path for path in (schema_path, example_path) if not path.is_file()]
        if missing_paths:
            findings.extend(
                Finding(
                    "HAR-SCHEMA-002",
                    "error",
                    str(path.relative_to(root)),
                    "정책에 등록된 Schema 또는 예시 파일이 없습니다.",
                )
                for path in missing_paths
            )
            continue
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            example = json.loads(example_path.read_text(encoding="utf-8"))
            validate(example, schema)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaValidationError) as error:
            findings.append(
                Finding(
                    "HAR-SCHEMA-002",
                    "error",
                    str(example_path.relative_to(root)),
                    f"Schema 예시 검증 실패 ({schema_value}): {error}",
                )
            )
    return findings


def check_id_registry(root: Path) -> list[Finding]:
    registry_path = root / "packages/contracts/id-registry.json"
    if not registry_path.exists():
        return []
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # HAR-JSON-001 reports the parse failure. This check must not abort the run.
        return []
    findings: list[Finding] = []
    if not isinstance(data, dict) or "items" not in data or not isinstance(data.get("items"), list):
        return [
            Finding(
                "HAR-ID-003",
                "error",
                "packages/contracts/id-registry.json",
                "ID 레지스트리는 items 배열을 가진 객체여야 합니다.",
            )
        ]
    seen: set[str] = set()
    items = data.get("items", [])
    for index, item in enumerate(items):
        location = f"packages/contracts/id-registry.json#items[{index}]"
        if not isinstance(item, dict):
            findings.append(Finding("HAR-ID-003", "error", location, "ID 항목은 객체여야 합니다."))
            continue
        item_id = item.get("id", "")
        if not isinstance(item_id, str) or not ID_PATTERN.fullmatch(item_id):
            findings.append(Finding("HAR-ID-001", "error", location, f"잘못된 ID 형식: {item_id}"))
        if isinstance(item_id, str) and item_id in seen:
            findings.append(Finding("HAR-ID-002", "error", location, f"중복 ID: {item_id}"))
        if isinstance(item_id, str):
            seen.add(item_id)

        required_fields = {"id", "type", "status", "owner", "description", "path"}
        missing_fields = sorted(required_fields.difference(item))
        item_type = item.get("type", "")
        valid_type = isinstance(item_type, str) and item_type in ALLOWED_ID_TYPES
        type_matches = isinstance(item_id, str) and valid_type and item_id.startswith(f"{item_type}-")
        if missing_fields or not valid_type or not type_matches:
            detail = f"누락 필드: {', '.join(missing_fields)}" if missing_fields else "ID 접두사와 type이 일치하지 않습니다."
            findings.append(Finding("HAR-ID-003", "error", location, detail))

        registered_path = item.get("path")
        if registered_path:
            if not isinstance(registered_path, str):
                findings.append(Finding("HAR-ID-003", "error", location, "path는 문자열이어야 합니다."))
                continue
            asset_path = (root / registered_path).resolve()
            try:
                asset_path.relative_to(root)
            except ValueError:
                path_exists = False
            else:
                path_exists = asset_path.exists()
            if not path_exists:
                findings.append(Finding("HAR-ID-004", "error", location, f"등록 경로가 없습니다: {registered_path}"))

    for index, item in enumerate(items):
        location = f"packages/contracts/id-registry.json#items[{index}]"
        if not isinstance(item, dict):
            continue
        references: list[str] = []
        for field in ID_RELATION_FIELDS:
            values = item.get(field, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                findings.append(Finding("HAR-ID-003", "error", location, f"{field}는 ID 문자열 배열이어야 합니다."))
                continue
            references.extend(values)
        replaced_by = item.get("replaced_by")
        if replaced_by:
            if not isinstance(replaced_by, str):
                findings.append(Finding("HAR-ID-003", "error", location, "replaced_by는 ID 문자열이어야 합니다."))
            else:
                references.append(replaced_by)
        for reference in references:
            if reference not in seen:
                findings.append(Finding("HAR-ID-005", "error", location, f"등록되지 않은 연결 ID: {reference}"))
    return findings


def check_markdown_links(root: Path) -> list[Finding]:
    findings: list[Finding] = []
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
                target_path.relative_to(root)
            except ValueError:
                findings.append(Finding("HAR-DOC-001", "error", str(path.relative_to(root)), "링크가 저장소 밖을 가리킵니다."))
                continue
            if not target_path.exists():
                findings.append(Finding("HAR-DOC-002", "error", str(path.relative_to(root)), f"없는 문서 링크: {target}"))
    return findings


def check_secret_patterns(root: Path) -> list[Finding]:
    patterns = (
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"(?:API_KEY|SECRET|TOKEN)\s*=\s*[^\s$<{][^\s]{7,}"),
    )
    findings: list[Finding] = []
    for path in iter_files(root):
        is_env_file = path.name == ".env" or path.name.startswith(".env.")
        is_docker_file = path.name == "Dockerfile" or path.name.startswith("Dockerfile.")
        if not is_env_file and not is_docker_file and path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in patterns):
            findings.append(Finding("HAR-SEC-001", "error", str(path.relative_to(root)), "비밀정보로 보이는 값이 있습니다."))
    return findings


def check_product_spec(root: Path, policy: dict[str, object]) -> list[Finding]:
    config = policy.get("product_spec", {})
    if not isinstance(config, dict):
        return [Finding("HAR-PRODUCT-001", "error", "harness/policy.json", "product_spec 설정이 객체가 아닙니다.")]
    path_value = config.get("path", "docs/product/service-spec.md")
    if not isinstance(path_value, str):
        return [Finding("HAR-PRODUCT-001", "error", "harness/policy.json", "product_spec.path는 문자열이어야 합니다.")]
    path = root / path_value
    if not path.exists():
        return [Finding("HAR-PRODUCT-001", "error", str(path_value), "서버 구현 요약 문서가 없습니다.")]
    content = path.read_text(encoding="utf-8")
    required_markers = config.get("required_markers", [])
    if not isinstance(required_markers, list) or not all(isinstance(marker, str) for marker in required_markers):
        return [
            Finding(
                "HAR-PRODUCT-001",
                "error",
                "harness/policy.json",
                "product_spec.required_markers는 문자열 배열이어야 합니다.",
            )
        ]
    return [
        Finding("HAR-PRODUCT-001", "error", str(path_value), f"구현 요약 문서 필수 표식이 없습니다: {marker}")
        for marker in required_markers
        if marker not in content
    ]


def evaluate_checks(root: Path, policy: dict[str, object]) -> tuple[list[Finding], list[CheckExecution]]:
    enabled = policy.get("checks", {})
    if not isinstance(enabled, dict) or any(not isinstance(value, bool) for value in enabled.values()):
        finding = Finding(
            "HAR-POLICY-001",
            "error",
            "harness/policy.json",
            "checks는 검사 이름과 true 또는 false 값으로 구성된 객체여야 합니다.",
        )
        return [finding], [CheckExecution("policy", "failed", 1)]
    findings: list[Finding] = []
    executions: list[CheckExecution] = []
    schema_examples = policy.get("schema_examples", {})
    checks = (
        ("required_paths", lambda: check_required_paths(root, policy), True),
        ("json_syntax", lambda: check_json_files(root), True),
        ("schema_headers", lambda: check_schema_headers(root), any(path.name.endswith(".schema.json") for path in iter_files(root))),
        ("schema_examples", lambda: check_schema_examples(root, policy), not isinstance(schema_examples, dict) or bool(schema_examples)),
        ("id_registry", lambda: check_id_registry(root), (root / "packages/contracts/id-registry.json").is_file()),
        ("markdown_links", lambda: check_markdown_links(root), True),
        ("secret_patterns", lambda: check_secret_patterns(root), True),
        ("product_spec", lambda: check_product_spec(root, policy), True),
    )
    for name, check, applicable in checks:
        if not enabled.get(name, True):
            executions.append(CheckExecution(name, "disabled", 0))
            continue
        if not applicable:
            executions.append(CheckExecution(name, "not_applicable", 0))
            continue
        check_findings = check()
        findings.extend(check_findings)
        status = "failed" if any(item.level == "error" for item in check_findings) else "passed"
        executions.append(CheckExecution(name, status, len(check_findings)))
    return findings, executions


def run_checks(root: Path, policy: dict[str, object]) -> list[Finding]:
    findings, _ = evaluate_checks(root, policy)
    return findings
