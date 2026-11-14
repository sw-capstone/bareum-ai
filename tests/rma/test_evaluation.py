"""평가 CLI를 기존 파일럿 저장물과 라벨러 실행 결과로 검증한다."""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from bareum_ai.dataset.rma import review, run
from bareum_ai.dataset.rma.evaluation.__main__ import evaluate
from bareum_ai.dataset.rma.evaluation.models import (
    Annotation, DocumentFields, EvidenceReview, Judgment, Labels, Pilot, Report, SpecItem, Unit, UnitKey,
)
from bareum_ai.dataset.rma.evaluation.scoring import score
from bareum_ai.rma.labelers import ReplayLabeler
from fixtures import ann
from test_review import labels_for, make_work
from test_runner import correct


@pytest.fixture
def workspace(tmp_path: Path) -> review.Workspace:
    ws = review.Workspace(make_work(tmp_path))
    assert review.save(ws, "adjudicated", labels_for(ws))["valid"]
    assert review.finalize(ws, "adjudicated") == (["doc_t"], [])
    return ws


def _model_run(ws: review.Workspace, name: str, responder: Callable = correct) -> Path:
    root = ws.work.parent / name
    frozen = ws.work / "snapshots" / ws.meta("doc_t")["snapshot"]["snapshot_id"] / "parsed.json"
    manifest = run([frozen], ReplayLabeler(responder), root)
    return root / manifest["run_id"]


def _annotations(path: Path) -> Path:
    return path / "documents/doc_t/annotations.json"


def test_cli_scores_the_fixed_pilot_when_one_run_loses_a_heading(workspace: review.Workspace) -> None:
    roots = [_model_run(workspace, name) for name in ("one", "two", "three")]
    artifact = _annotations(roots[1])
    result = json.loads(artifact.read_text())
    result["blocks"] = [b for b in result["blocks"] if b["block_id"] != "b2"]
    artifact.write_text(json.dumps(result))
    out = workspace.work.parent / "evaluation.json"

    command = [sys.executable, "-m", "bareum_ai.dataset.rma.evaluation",
               "--work", str(workspace.work), "--gold", "adjudicated", "--out", str(out)]
    for root in roots:
        command.extend(["--run", str(root)])
    subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(out.read_text())

    assert data["pilot"]["selected_units"] == 9
    assert data["repeatability"]["metrics"]["moves"] == {"matched": 7, "total": 8, "rate": 7 / 8}
    assert data["repeatability"]["variation_cases"][0]["block_id"] == "b2"
    scores = data["runs"][1]["scores"]
    assert scores["overall"]["move_exact"]["matched"] == 7
    assert scores["overall"]["move_exact_nonempty"]["rate"] == 1
    assert scores["overall"]["normal_empty"]["matched"] == 2
    assert scores["overall"]["step_scored_units"] == 7
    assert scores["processing"]["response_failure"]["statuses"] == {"missing": 1}
    assert scores["processing"]["input_unreadable"]["matched"] == 1
    assert scores["evidence"]["structural_validity"]["rate"] == 1
    assert data["summary"]["overall.move_exact.rate"]["min"] == 7 / 8


def test_human_agreement_retains_units_without_a_list(workspace: review.Workspace) -> None:
    alice = labels_for(workspace)
    bob = labels_for(workspace)
    for response in bob["block_responses"].values():
        for block in response["blocks"]:
            if block["block_id"] == "b3":
                block["annotations"] = [a for a in block["annotations"] if a["move"] == "ACTION"]
        response["blocks"] = [b for b in response["blocks"] if b["block_id"] != "b2"]
    review.save(workspace, "alice", alice)
    review.save(workspace, "bob", bob)

    report = evaluate(workspace.work, annotators=("alice", "bob"))

    human = report["human_agreement"]
    assert human["move_exact"] == {"matched": 6, "total": 8, "rate": 0.75}
    assert human["missing_list_units"] == 1
    kappa = next(row for row in human["kappa"] if row["move"] == "GROUNDS")
    assert kappa == {"move": "GROUNDS", "left_positive": 2, "right_positive": 1, "total": 7, "kappa": 10 / 17}
    assert next(row for row in human["kappa"] if row["move"] == "OTHER")["kappa"] is None
    assert {row["block_id"] for row in human["differences"]} == {"b2", "b3"}


def test_repeatability_rejects_different_settings_without_losing_run_scores(workspace: review.Workspace) -> None:
    roots = tuple(_model_run(workspace, name) for name in ("one", "two", "three"))
    path = roots[2] / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["prompt_sha256"] = "different"
    path.write_text(json.dumps(manifest))

    report = evaluate(workspace.work, gold_name="adjudicated", run_paths=roots)

    assert report["repeatability"]["reasons"] == ["different_prompt_sha256"]
    assert report["repeatability"]["metrics"]["moves"] == {"matched": None, "total": 8, "rate": None}
    assert all(result["scores"] is not None for result in report["runs"])
    assert report["summary"] is None


def test_human_agreement_counts_unedited_body_rows_as_missing(workspace: review.Workspace) -> None:
    alice = labels_for(workspace)
    bob = labels_for(workspace)
    table = next(b["table"] for r in bob["block_responses"].values() for b in r["blocks"] if b["block_id"] == "b6")
    table["rows"][1].update(role=None, annotations=[], primary_annotation_id=None)
    table["rows"][2].update(annotations=[], primary_annotation_id=None)
    review.save(workspace, "alice", alice)
    review.save(workspace, "bob", bob)

    report = evaluate(workspace.work, annotators=("alice", "bob"))

    assert report["human_agreement"]["missing_list_units"] == 2
    assert report["human_agreement"]["move_exact"]["matched"] == 6


def test_repeatability_preserves_move_multiplicity_while_scoring_deduplicates(workspace: review.Workspace) -> None:
    roots = tuple(_model_run(workspace, name) for name in ("one", "two", "three"))
    path = _annotations(roots[2])
    result = json.loads(path.read_text())
    block = next(b for b in result["blocks"] if b["block_id"] == "b3")
    block["annotations"].append({**block["annotations"][1], "annotation_id": "a3"})
    path.write_text(json.dumps(result))

    report = evaluate(workspace.work, gold_name="adjudicated", run_paths=roots)

    assert report["runs"][2]["scores"]["overall"]["move_exact"]["rate"] == 1
    repeated = report["repeatability"]
    assert repeated["metrics"]["moves"]["matched"] == 7
    assert repeated["metrics"]["annotation_move_steps"]["matched"] == 7
    assert repeated["metrics"]["primary_move"]["rate"] == 1


def _unreadable(request: Report, attempt: int) -> Report:
    response = correct(request, attempt)
    for block in response.get("blocks", []):
        if block["block_id"] == "b6":
            block["table"] = {"table_status": "unreadable", "table_reason": "열 구조를 읽지 못함",
                              "rows": [], "header_rows": [], "row_header_cols": []}
    return response


def test_model_unreadable_is_a_miss_even_when_all_runs_return_it(workspace: review.Workspace) -> None:
    roots = tuple(_model_run(workspace, name, _unreadable) for name in ("one", "two", "three"))

    report = evaluate(workspace.work, gold_name="adjudicated", run_paths=roots)

    scores = report["runs"][0]["scores"]
    assert scores["processing"]["model_unreadable"]["matched"] == 3
    assert scores["processing"]["model_unreadable"]["tables"] == 1
    assert scores["processing"]["response_failure"]["matched"] == 0
    assert scores["table_rows"]["move"]["micro"]["missing"] == 2
    assert scores["table_rows"]["normal_empty"]["matched"] == 0
    assert report["repeatability"]["metrics"]["annotation_move_steps"]["matched"] == 5
    assert len(report["repeatability"]["repeated_errors"]) == 3


def _fails_selected_request(request: Report, attempt: int) -> Report:
    response = correct(request, attempt)
    if "b3" in request.get("target_block_ids", []):
        response["blocks"] = []
    return response


def test_exhausted_retries_count_as_failures_in_completed_runs(workspace: review.Workspace) -> None:
    roots = tuple(_model_run(workspace, name, _fails_selected_request) for name in ("one", "two", "three"))

    report = evaluate(workspace.work, gold_name="adjudicated", run_paths=roots)

    scores = report["runs"][0]["scores"]
    failures = scores["processing"]["response_failure"]
    assert failures["statuses"] == {"failed": failures["matched"]}
    assert failures["reasons"] == {"V02": failures["matched"]}
    assert scores["overall"]["move"]["micro"]["missing"] >= 2
    assert report["repeatability"]["comparable"]
    assert report["repeatability"]["metrics"]["moves"]["matched"] == 8 - failures["matched"]


def test_manual_evidence_review_is_independent_of_position_validity(workspace: review.Workspace) -> None:
    root = _model_run(workspace, "one")
    path = _annotations(root)
    artifact = json.loads(path.read_text())
    target = next(b for b in artifact["blocks"] if b["block_id"] == "b3")
    target["annotations"][0]["evidence_span"]["start"] += 1
    path.write_text(json.dumps(artifact))
    review_path = workspace.work / "evidence-reviews.json"
    review_path.write_text(json.dumps([EvidenceReview(
        run_id=artifact["run_id"], evidence_id="doc_t/blocks/b3/annotations/a1/evidence_span",
        verdict="undetermined", reason="검수에 필요한 문맥 부족",
    ).model_dump()]))

    report = evaluate(workspace.work, gold_name="adjudicated", run_paths=(root,), evidence_reviews=review_path)

    evidence = report["runs"][0]["scores"]["evidence"]
    assert evidence["quote_validity"]["rate"] == 1
    assert evidence["position_validity"]["matched"] == evidence["position_validity"]["total"] - 1
    assert evidence["manual_validity"] == {"matched": 0, "total": 1, "rate": 0}
    assert evidence["manual_undetermined"] == 1


def test_spec_matches_each_gold_item_once_with_unicode_and_whitespace_normalized() -> None:
    key = UnitKey("doc", "block")
    document = DocumentFields(document_type=None, document_topics=None, outline_id=None)
    gold_item = SpecItem(spec_field=None, raw_label="기간", value="가  1")
    prediction = SpecItem(spec_field=None, raw_label="기간", value="가\t1")
    a = Annotation.model_validate(ann("a1", "SPEC", "가"))
    gold = Labels({key: Judgment("ok", (a.model_copy(update={"spec_items": [gold_item]}),))}, {"doc": document})
    predictions = Labels({key: Judgment("ok", (a.model_copy(update={"spec_items": [prediction, prediction]}),))},
                         {"doc": document})
    pilot = Pilot((Unit(key, "block", "가  1"),), {"doc": "hash"}, {}, {})

    report = score(pilot, gold, predictions, ["SPEC"], [])

    assert report["overall"]["spec"] == {
        "matched": 1, "missing": 0, "added": 1, "support": 1,
        "precision": 0.5, "recall": 1, "f1": 2 / 3,
    }


def test_partial_gold_steps_are_kept_separately_from_full_step_scoring() -> None:
    document = DocumentFields(document_type=None, document_topics=None, outline_id=None)
    partial_key = UnitKey("doc", "partial")
    confirmed_key = UnitKey("doc", "confirmed")
    label = ann("a1", "GROUNDS", "조례", steps=[{"step": "ORDINANCE"}], status="determined")
    confirmed = Annotation.model_validate(label)
    partial = Annotation.model_validate({**label, "step_status": "partial", "step_reason": "종류 일부 확인"})
    wrong_move = Annotation.model_validate(ann("a1", "ACTION", "조례"))
    gold = Labels({partial_key: Judgment("ok", (partial,)), confirmed_key: Judgment("ok", (confirmed,))},
                  {"doc": document})
    predictions = Labels({partial_key: Judgment("ok", (confirmed,)), confirmed_key: Judgment("ok", (wrong_move,))},
                         {"doc": document})
    units = (Unit(partial_key, "block", "조례"), Unit(confirmed_key, "block", "조례"))

    report = score(Pilot(units, {"doc": "hash"}, {}, {}), gold, predictions,
                   ["ACTION", "GROUNDS"], [("GROUNDS", "ORDINANCE")])

    overall = report["overall"]
    assert overall["move_scored_units"] == 2
    assert overall["step_scored_units"] == 1
    assert overall["step"]["micro"]["missing"] == 1
    assert overall["known_steps_in_unconfirmed_units"] == {"matched": 1, "missing": 0}
