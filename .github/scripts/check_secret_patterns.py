"""저장소 파일에서 비밀정보로 보이는 값을 찾는다. 발견한 값은 로그에 남기지 않는다."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKIP_PARTS = {".git", ".venv", "build", "dist", "node_modules", "reports", "__pycache__"}
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
TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:[A-Z0-9]+[_-])?(?:API[_-]?KEY|CLIENT[_-]?SECRET|SECRET|TOKEN|PASSWORD|PASSWD)\b"
    r"\s*[:=]\s*[\"']?([^\s\"'${}<]{8,})",
    re.IGNORECASE,
)
PLACEHOLDER_PATTERN = re.compile(
    r"^(?:change[-_ ]?me|replace[-_ ]?me|your[-_ ].*|example|placeholder|dummy|sample|test|redacted|not[-_ ]?set|bareum[-_ ]password)$",
    re.IGNORECASE,
)


def is_target(path: Path) -> bool:
    is_env_file = path.name == ".env" or path.name.startswith(".env.")
    is_docker_file = path.name == "Dockerfile" or path.name.startswith("Dockerfile.")
    return is_env_file or is_docker_file or path.suffix in TEXT_SUFFIXES


def has_secret_value(text: str) -> bool:
    return bool(TOKEN_PATTERN.search(text)) or any(
        not PLACEHOLDER_PATTERN.fullmatch(match.group(1).rstrip(";,)"))
        for match in ASSIGNMENT_PATTERN.finditer(text)
    )


def find_secret_files(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or SKIP_PARTS.intersection(relative.parts) or not is_target(path):
            continue
        if has_secret_value(path.read_text(encoding="utf-8", errors="ignore")):
            found.append(relative.as_posix())
    return found


def main() -> int:
    found = find_secret_files(Path.cwd())
    for path in found:
        print(f"ERROR {path}: 비밀정보로 보이는 값이 있습니다.", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
