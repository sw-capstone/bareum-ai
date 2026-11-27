"""평가 분할 기준 파일(manifest)의 무결성 검사.

프롬프트·규칙을 고칠 때 쓴 문서가 평가 문서로 새면 측정 점수를 믿을 수 없다. 이 검사는
원본 PDF 없이 manifest만 보고 항목별 위반을 모두 돌려준다. 파일명은 NFD일 수 있어 모든
이름은 NFC로 맞춰 비교한다.

- SP-01 구조: 필수 키, split 값, sha256 형식
- SP-02 A/B 구성: 그룹마다 A·B 파일이 정확히 하나씩
- SP-03 그룹 중복: 같은 group_id가 두 번 이상
- SP-04 파일 중복: 같은 sha256 또는 path가 두 번 이상
- SP-05 이중 등록: 분류가 같고 제목이 같은 그룹이 서로 다른 split에 있음
- SP-06 경로·분류: path가 <source>/<group_id>_<variant>.pdf와 다르거나 category가 group_id와 다름
- SP-07 제외 그룹: test_excluded_groups의 그룹이 없거나 test에 있음

SP-03으로 잡힌 중복 그룹은 SP-04·SP-05에서 다시 세지 않는다. 형식이 깨진 항목(SP-01)은 다른
검사에서 다시 보고하지 않는다. Violation.group은 항상 NFC 이름이다. 분류는 group_id의 첫
'/' 앞이고, 나머지가 제목이다.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

SPLITS = {"dev", "test", "cbr"}

_SHA256 = re.compile(r"[0-9a-f]{64}")
_BRACKET_PREFIX = re.compile(r"^\[[^\]]*\]")
_NON_WORD = re.compile(r"[\W_]+")
_GROUP_KEYS = ("group_id", "category", "files", "split")
_FILE_KEYS = ("variant", "path", "sha256")


@dataclass(frozen=True)
class Violation:
    code: str
    group: str | None
    reason: str


def check_manifest(manifest: object) -> list[Violation]:
    """위반을 모두 돌려준다. 위반이 없으면 빈 목록이다. 객체가 아닌 입력은 SP-01 하나를 돌려준다."""
    if not isinstance(manifest, dict):
        return [Violation("SP-01", None, "manifest가 객체가 아닙니다")]
    groups = _groups(manifest)
    unique = _unique_groups(groups)
    return [
        *_check_structure(manifest),
        *_check_ab_files(groups),
        *_check_duplicate_groups(groups),
        *_check_duplicate_files(unique),
        *_check_double_registration(unique),
        *_check_consistency(manifest, groups),
        *_check_excluded(manifest, groups),
    ]


def _nfc(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def _groups(manifest: dict) -> list[dict]:
    """형식이 깨진 항목은 SP-01이 보고하므로 나머지 검사에서는 제외한다."""
    groups = manifest.get("groups")
    if not isinstance(groups, list):
        return []
    return [
        g for g in groups if isinstance(g, dict) and isinstance(g.get("group_id"), str)
    ]


def _name(group: dict) -> str:
    return _nfc(group["group_id"])


def _split_name(group_id: str) -> tuple[str, str]:
    """(분류, 제목). 분류는 첫 '/' 앞이고, '/'가 없으면 분류는 빈 문자열이다."""
    category, sep, title = _nfc(group_id).partition("/")
    return (category, title) if sep else ("", category)


def _files(group: dict) -> list[dict]:
    files = group.get("files")
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, dict)]


def _files_well_formed(group: dict) -> bool:
    """files가 목록이고 모든 항목이 variant 문자열을 가진 객체인가. 아니면 SP-01이 보고한다."""
    files = group.get("files")
    return isinstance(files, list) and all(
        isinstance(f, dict) and isinstance(f.get("variant"), str) for f in files
    )


def _unique_groups(groups: list[dict]) -> list[dict]:
    first = {}
    for g in groups:
        first.setdefault(_nfc(g["group_id"]), g)
    return list(first.values())


def _check_structure(manifest: dict) -> list[Violation]:
    out = []
    if not isinstance(manifest.get("source"), str):
        out.append(Violation("SP-01", None, "source가 문자열이 아닙니다"))
    if not isinstance(manifest.get("test_excluded_groups"), dict):
        out.append(Violation("SP-01", None, "test_excluded_groups가 객체가 아닙니다"))
    groups = manifest.get("groups")
    if not isinstance(groups, list):
        out.append(Violation("SP-01", None, "groups가 목록이 아닙니다"))
        return out
    for g in groups:
        if not isinstance(g, dict):
            out.append(Violation("SP-01", None, f"그룹이 객체가 아닙니다: {g!r}"))
            continue
        out.extend(_check_group_structure(g))
    return out


def _check_group_structure(group: dict) -> list[Violation]:
    name = group.get("group_id")
    label = _nfc(name) if isinstance(name, str) else None
    out = [
        Violation("SP-01", label, f"필수 키가 없습니다: {key}")
        for key in _GROUP_KEYS
        if key not in group
    ]
    if "group_id" in group and label is None:
        out.append(Violation("SP-01", None, f"group_id가 문자열이 아닙니다: {name!r}"))
    split = group.get("split")
    if "split" in group and not (isinstance(split, str) and split in SPLITS):
        out.append(Violation("SP-01", label, f"알 수 없는 split: {split!r}"))
    if "category" in group and not isinstance(group["category"], str):
        out.append(Violation("SP-01", label, "category가 문자열이 아닙니다"))
    if "files" in group and not isinstance(group["files"], list):
        out.append(Violation("SP-01", label, "files가 목록이 아닙니다"))
        return out
    for f in group.get("files", []):
        if not isinstance(f, dict):
            out.append(Violation("SP-01", label, f"파일 항목이 객체가 아닙니다: {f!r}"))
            continue
        out.extend(
            Violation("SP-01", label, f"파일의 필수 키가 없습니다: {key}")
            for key in _FILE_KEYS
            if key not in f
        )
        if "variant" in f and not isinstance(f["variant"], str):
            out.append(Violation("SP-01", label, f"variant가 문자열이 아닙니다: {f['variant']!r}"))
        if "path" in f and not isinstance(f["path"], str):
            out.append(Violation("SP-01", label, f"path가 문자열이 아닙니다: {f['path']!r}"))
        sha = f.get("sha256")
        if "sha256" in f and not (isinstance(sha, str) and _SHA256.fullmatch(sha)):
            out.append(Violation("SP-01", label, f"sha256 형식이 다릅니다: {sha!r}"))
    return out


def _check_ab_files(groups: list[dict]) -> list[Violation]:
    out = []
    for g in groups:
        if not _files_well_formed(g):
            continue  # SP-01이 보고한다
        variants = sorted(f["variant"] for f in g["files"])
        if variants != ["A", "B"]:
            out.append(
                Violation("SP-02", _name(g), f"파일 구성이 A·B 각 1개가 아닙니다: {variants}")
            )
    return out


def _check_duplicate_groups(groups: list[dict]) -> list[Violation]:
    splits = defaultdict(list)
    for g in groups:
        splits[_nfc(g["group_id"])].append(str(g.get("split")))
    return [
        Violation("SP-03", name, f"같은 그룹이 {len(found)}번 있습니다: {sorted(found)}")
        for name, found in splits.items()
        if len(found) > 1
    ]


def _check_duplicate_files(groups: list[dict]) -> list[Violation]:
    by_sha = defaultdict(list)
    by_path = defaultdict(list)
    for g in groups:
        paths = set()  # 한 그룹 안의 같은 path는 SP-06이 보고하므로 그룹당 한 번만 센다
        for f in _files(g):
            if isinstance(f.get("sha256"), str):
                by_sha[f["sha256"]].append(g)
            if isinstance(f.get("path"), str):
                paths.add(_nfc(f["path"]))
        for path in paths:
            by_path[path].append(g)
    return [
        *_duplicates(by_sha, "같은 sha256"),
        *_duplicates(by_path, "같은 path"),
    ]


def _duplicates(owners: dict[str, list[dict]], label: str) -> list[Violation]:
    out = []
    for value, groups in owners.items():
        if len(groups) < 2:
            continue
        if len({_name(g) for g in groups}) == 1:
            reason = f"한 그룹 안에서 {label} {value}가 {len(groups)}번 있습니다"
        else:
            where = sorted(f"{_name(g)}({g.get('split')})" for g in groups)
            reason = f"{label} {value}: {where}"
        out.append(Violation("SP-04", _name(groups[0]), reason))
    return out


def _report_key(group_id: str) -> tuple[str, str]:
    """분류와 정규화한 제목. 계획·결과보고서는 제목이 같아도 다른 보고서다."""
    category, title = _split_name(group_id)
    normalized = _NON_WORD.sub("", _BRACKET_PREFIX.sub("", title)).casefold()
    # 제목이 [...]뿐이면 정규화하면 비므로 원래 제목으로 구분한다.
    return category, normalized or title.casefold()


def _check_double_registration(groups: list[dict]) -> list[Violation]:
    reports = defaultdict(list)
    for g in groups:
        reports[_report_key(g["group_id"])].append(g)
    out = []
    for found in reports.values():
        if len({str(g.get("split")) for g in found}) > 1:
            where = sorted(f"{_name(g)}({g.get('split')})" for g in found)
            out.append(
                Violation("SP-05", _name(found[0]), f"같은 보고서가 split에 갈라져 있습니다: {where}")
            )
    return out


def _check_consistency(manifest: dict, groups: list[dict]) -> list[Violation]:
    source = manifest.get("source")
    out = []
    for g in groups:
        name = _name(g)
        category = g.get("category")
        expected, _ = _split_name(name)
        if isinstance(category, str) and _nfc(category) != expected:
            out.append(Violation("SP-06", name, f"category가 group_id와 다릅니다: {category}"))
        if not isinstance(source, str):
            continue
        for f in _files(g):
            path, variant = f.get("path"), f.get("variant")
            if not isinstance(path, str) or not isinstance(variant, str):
                continue
            want = _nfc(f"{source}/{name}_{variant}.pdf")
            if _nfc(path) != want:
                out.append(Violation("SP-06", name, f"path가 규칙과 다릅니다: {path}"))
    return out


def _check_excluded(manifest: dict, groups: list[dict]) -> list[Violation]:
    excluded = manifest.get("test_excluded_groups")
    if not isinstance(excluded, dict) or not isinstance(manifest.get("groups"), list):
        return []
    by_name = defaultdict(list)
    for g in groups:
        by_name[_nfc(g["group_id"])].append(g)
    out = []
    for name in excluded:
        label = _nfc(name)
        found = by_name.get(label)
        if not found:
            out.append(Violation("SP-07", label, "제외 그룹이 groups에 없습니다"))
        elif any(g.get("split") == "test" for g in found):
            out.append(Violation("SP-07", label, "제외 그룹이 test에 있습니다"))
    return out
