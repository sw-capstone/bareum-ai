"""모델 요청 설정을 실행 기록에 보존하고 반복 비교에 적용한다."""

import json
from types import ModuleType

import pytest

from bareum_ai.dataset.rma import review
from bareum_ai.dataset.rma.evaluation.__main__ import evaluate
from bareum_ai.rma import request, targets
from test_evaluation import _model_run, workspace as workspace


@pytest.mark.parametrize("setting", [
    pytest.param((targets, "CHUNK_LIMIT", 5), id="chunk_limit"),
    pytest.param((request, "FULL_TEXT_LIMIT", 10), id="full_text_limit"),
])
def test_repeatability_rejects_change_to_a_request_limit(
    workspace: review.Workspace, monkeypatch: pytest.MonkeyPatch, setting: tuple[ModuleType, str, int],
) -> None:
    first, second = (_model_run(workspace, name) for name in ("one", "two"))
    module, constant, value = setting
    with monkeypatch.context() as settings:
        settings.setattr(module, constant, value)
        changed = _model_run(workspace, "three")

    report = evaluate(workspace.work, gold_name="adjudicated", run_paths=(first, second, changed))

    manifest = json.loads((changed / "manifest.json").read_text())
    assert manifest["policy"][constant.lower()] == value
    assert report["repeatability"]["comparable"] is False
    assert report["repeatability"]["reasons"] == ["different_policy"]
    assert [result["scores"]["overall"]["move_exact"]["rate"] for result in report["runs"]] == [1, 1, 1]
    assert report["summary"] is None


def test_repeatability_reports_missing_policy_without_losing_legacy_run_scores(workspace: review.Workspace) -> None:
    roots = tuple(_model_run(workspace, name) for name in ("one", "two", "three"))
    for root in roots:
        path = root / "manifest.json"
        manifest = json.loads(path.read_text())
        for field in ("chunk_limit", "full_text_limit"):
            manifest["policy"].pop(field, None)
        path.write_text(json.dumps(manifest))

    report = evaluate(workspace.work, gold_name="adjudicated", run_paths=roots)

    assert report["repeatability"]["comparable"] is False
    assert report["repeatability"]["reasons"] == [
        f"{root.name}:missing_policy_fields:chunk_limit,full_text_limit" for root in roots
    ]
    assert report["repeatability"]["metrics"]["moves"] == {"matched": None, "total": 8, "rate": None}
    assert [result["scores"]["overall"]["move_exact"]["rate"] for result in report["runs"]] == [1, 1, 1]
    assert report["summary"] is None
