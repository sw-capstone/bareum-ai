import copy
import json
import unicodedata
from pathlib import Path

import pytest

from bareum_ai.harness.split_integrity import check_manifest

MANIFEST_PATH = Path(__file__).parents[2] / "datasets" / "splits" / "manifest.json"


def sha(n: int) -> str:
    return f"{n:064x}"


def group(group_id: str, split: str, seed: int) -> dict:
    return {
        "group_id": group_id,
        "category": group_id.split("/", 1)[0],
        "files": [
            {"variant": "A", "path": f"보고서/{group_id}_A.pdf", "sha256": sha(seed)},
            {"variant": "B", "path": f"보고서/{group_id}_B.pdf", "sha256": sha(seed + 1)},
        ],
        "split": split,
    }


@pytest.fixture
def manifest() -> dict:
    """합성 manifest. 인덱스 순서: 0 dev, 1 test, 2 cbr(제외 그룹)."""
    return {
        "source": "보고서",
        "test_excluded_groups": {"결과보고서/다 사업": "기획 문서에서 인용"},
        "groups": [
            group("결과보고서/가 사업", "dev", 10),
            group("계획보고서/나 사업", "test", 20),
            group("결과보고서/다 사업", "cbr", 30),
        ],
    }


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def codes(manifest: dict) -> set[str]:
    return {v.code for v in check_manifest(manifest)}


def test_valid_manifest_has_no_violation(manifest):
    assert check_manifest(manifest) == []


def test_real_manifest_has_no_violation():
    real = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert check_manifest(real) == []


# SP-01 구조


def test_missing_split_key(manifest):
    del manifest["groups"][0]["split"]
    assert codes(manifest) == {"SP-01"}


def test_unknown_split_value(manifest):
    manifest["groups"][0]["split"] = "valid"
    assert codes(manifest) == {"SP-01"}


@pytest.mark.parametrize("bad", [sha(10)[:63], sha(10).upper().replace("0", "A"), None])
def test_malformed_sha256(manifest, bad):
    manifest["groups"][0]["files"][0]["sha256"] = bad
    assert codes(manifest) == {"SP-01"}


@pytest.mark.parametrize("field", ["split", "category"])
def test_non_string_group_field_does_not_crash(manifest, field):
    manifest["groups"][0][field] = ["dev"]
    assert codes(manifest) == {"SP-01"}


def test_non_string_path_does_not_crash(manifest):
    manifest["groups"][0]["files"][0]["path"] = None
    assert codes(manifest) == {"SP-01"}


@pytest.mark.parametrize("key", ["source", "groups", "test_excluded_groups"])
def test_missing_top_level_key(manifest, key):
    del manifest[key]
    assert codes(manifest) == {"SP-01"}


@pytest.mark.parametrize("bad", [[], "manifest", None])
def test_non_object_manifest_does_not_crash(bad):
    assert codes(bad) == {"SP-01"}


def test_non_object_group_does_not_crash(manifest):
    manifest["groups"].append("결과보고서/문자열")
    assert codes(manifest) == {"SP-01"}


def test_missing_files_is_reported_only_as_structure(manifest):
    del manifest["groups"][0]["files"]
    assert codes(manifest) == {"SP-01"}


def test_non_list_files_is_reported_only_as_structure(manifest):
    manifest["groups"][0]["files"] = "가 사업_A.pdf"
    assert codes(manifest) == {"SP-01"}


def test_missing_variant_is_reported_only_as_structure(manifest):
    del manifest["groups"][0]["files"][0]["variant"]
    assert codes(manifest) == {"SP-01"}


def test_non_string_variant_is_reported_only_as_structure(manifest):
    manifest["groups"][0]["files"][0]["variant"] = 1
    assert codes(manifest) == {"SP-01"}


def test_non_object_file_is_reported_only_as_structure(manifest):
    manifest["groups"][0]["files"].append("가 사업_C.pdf")
    assert codes(manifest) == {"SP-01"}


# SP-02 A/B 구성


def test_missing_b_file(manifest):
    manifest["groups"][0]["files"].pop()
    assert codes(manifest) == {"SP-02"}


def test_two_a_files(manifest):
    files = manifest["groups"][0]["files"]
    files[1] = {**files[0], "sha256": sha(99)}
    assert codes(manifest) == {"SP-02"}


# SP-03 그룹 중복


def test_same_group_registered_in_another_split(manifest):
    manifest["groups"].append(group("결과보고서/가 사업", "test", 40))
    assert codes(manifest) == {"SP-03"}


def test_group_id_differing_only_by_normalization(manifest):
    decomposed = nfd("결과보고서/가 사업")
    assert decomposed != "결과보고서/가 사업"
    manifest["groups"].append(group(decomposed, "test", 40))
    assert codes(manifest) == {"SP-03"}


def test_duplicate_group_is_not_counted_again_as_duplicate_file(manifest):
    manifest["groups"].append(group("결과보고서/가 사업", "test", 10))
    assert codes(manifest) == {"SP-03"}


def test_violation_group_is_nfc_for_every_code():
    broken = {
        "source": "보고서",
        "test_excluded_groups": {nfd("결과보고서/다 사업"): "기획 문서에서 인용"},
        "groups": [
            group(nfd("결과보고서/가 사업"), "dev", 10),
            group(nfd("결과보고서/가사업"), "test", 10),
            group(nfd("결과보고서/다 사업"), "test", 30),
        ],
    }
    broken["groups"][0]["files"].pop()
    broken["groups"][1]["category"] = "계획보고서"
    violations = check_manifest(broken)
    assert {v.code for v in violations} >= {"SP-02", "SP-04", "SP-05", "SP-06", "SP-07"}
    named = [v.group for v in violations if v.group is not None]
    assert named and all(name == unicodedata.normalize("NFC", name) for name in named)


# SP-04 파일 중복


def test_same_sha256_in_different_groups(manifest):
    manifest["groups"][2]["files"][0]["sha256"] = manifest["groups"][0]["files"][1]["sha256"]
    assert codes(manifest) == {"SP-04"}


def test_dev_file_copied_into_test_group(manifest):
    manifest["groups"][1]["files"][0]["sha256"] = manifest["groups"][0]["files"][0]["sha256"]
    violations = check_manifest(manifest)
    assert {v.code for v in violations} == {"SP-04"}
    assert "dev" in violations[0].reason and "test" in violations[0].reason


def test_same_sha256_in_one_group_is_reported_as_one_group(manifest):
    files = manifest["groups"][0]["files"]
    files[1]["sha256"] = files[0]["sha256"]
    violations = check_manifest(manifest)
    assert {v.code for v in violations} == {"SP-04"}
    assert "한 그룹 안" in violations[0].reason
    assert violations[0].reason.count("가 사업") <= 1


def test_same_path_in_different_groups(manifest):
    manifest["groups"][1]["files"][0]["path"] = manifest["groups"][0]["files"][0]["path"]
    assert codes(manifest) == {"SP-04", "SP-06"}


# SP-05 같은 보고서의 이중 등록


def test_same_title_with_spacing_difference_in_another_split(manifest):
    manifest["groups"].append(group("결과보고서/가사업", "test", 40))
    assert codes(manifest) == {"SP-05"}


def test_same_title_with_bracket_prefix_in_another_split(manifest):
    manifest["groups"].append(group("결과보고서/[결과]가 사업", "test", 40))
    assert codes(manifest) == {"SP-05"}


def test_same_title_in_same_split_is_allowed(manifest):
    manifest["groups"].append(group("결과보고서/가사업", "dev", 40))
    assert check_manifest(manifest) == []


def test_plan_and_result_with_same_title_are_different_reports(manifest):
    manifest["groups"].append(group("계획보고서/가 사업", "test", 40))
    assert check_manifest(manifest) == []


def test_groups_with_empty_title_after_normalization_are_different_reports(manifest):
    manifest["groups"].append(group("결과보고서/[결과]", "test", 40))
    manifest["groups"].append(group("결과보고서/[최종]", "dev", 50))
    assert check_manifest(manifest) == []


def test_nested_group_id_uses_first_segment_as_category(manifest):
    manifest["groups"].append(group("결과보고서/2024/가 사업", "dev", 40))
    manifest["groups"].append(group("결과보고서/2024/가사업", "test", 50))
    assert codes(manifest) == {"SP-05"}


def test_nested_group_id_in_another_folder_is_a_different_report(manifest):
    manifest["groups"].append(group("결과보고서/2024/가 사업", "dev", 40))
    manifest["groups"].append(group("결과보고서/2025/가 사업", "test", 50))
    assert check_manifest(manifest) == []


def test_year_difference_is_a_different_report(manifest):
    manifest["groups"].append(group("결과보고서/2024 가 사업", "dev", 40))
    manifest["groups"].append(group("결과보고서/2025 가 사업", "test", 50))
    assert check_manifest(manifest) == []


# SP-06 경로·분류


def test_path_variant_suffix_mismatch(manifest):
    file = manifest["groups"][0]["files"][0]
    file["path"] = file["path"].replace("_A.pdf", "_B.pdf")
    assert codes(manifest) == {"SP-06"}


def test_category_mismatch(manifest):
    manifest["groups"][1]["category"] = "결과보고서"
    assert codes(manifest) == {"SP-06"}


def test_path_compared_after_normalization(manifest):
    file = manifest["groups"][0]["files"][0]
    file["path"] = unicodedata.normalize("NFD", file["path"])
    assert check_manifest(manifest) == []


# SP-07 제외 그룹


def test_excluded_group_in_test_split(manifest):
    manifest["groups"][2]["split"] = "test"
    assert codes(manifest) == {"SP-07"}


def test_excluded_group_not_in_groups(manifest):
    manifest["test_excluded_groups"]["결과보고서/없는 사업"] = "기획 문서에서 인용"
    assert codes(manifest) == {"SP-07"}


def test_excluded_group_matched_after_normalization(manifest):
    reason = manifest["test_excluded_groups"].pop("결과보고서/다 사업")
    manifest["test_excluded_groups"][unicodedata.normalize("NFD", "결과보고서/다 사업")] = reason
    assert check_manifest(manifest) == []


# 위반 수집


def test_all_violations_are_reported_at_once(manifest):
    broken = copy.deepcopy(manifest)
    broken["groups"][0]["split"] = "valid"
    broken["groups"][1]["files"].pop()
    broken["groups"][2]["split"] = "test"
    violations = check_manifest(broken)
    assert {v.code for v in violations} == {"SP-01", "SP-02", "SP-07"}
    assert all(v.reason for v in violations)
