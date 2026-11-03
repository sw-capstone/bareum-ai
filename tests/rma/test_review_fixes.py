"""리뷰 결함의 대상 선정·검수 화면·최종 조립 회귀 검증. 외부 모델을 호출하지 않는다."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import unicodedata
from unittest.mock import patch

import pytest

from bareum_ai import sections
from bareum_ai.dataset.rma import pilot, review
from bareum_ai.rma import request as R, targets as T
from bareum_ai.rma.assemble import assemble
from bareum_ai.rma.validate import validate_block_response, validate_document_response
from fixtures import ann, document_response, parsed_doc, response_for, span

RUN = {"run_id": "review-regression", "labeler": {"type": "model", "id": "fixture"}}


def warning_doc():
    doc = parsed_doc()
    doc["warnings"] = [{"code": "table_grid_sparse", "page": 1, "bbox": [0, 0, 10, 10]}]
    return doc


@pytest.mark.parametrize("code", ["table_grid_sparse", "table_geometry_ambiguous"])
def test_structure_warning_excludes_only_matching_table(code):
    doc = warning_doc()
    doc["warnings"][0]["code"] = code
    table = next(b for b in doc["blocks"] if b["id"] == "b6")
    for bid, page, box in [("neighbor", 1, [5, 0, 15, 10]), ("next_page", 2, [0, 0, 10, 10])]:
        doc["blocks"].append({**copy.deepcopy(table), "id": bid, "page": page, "bbox": box})
    doc["warnings"].append({**doc["warnings"][0], "bbox": [0.2, 0, 10, 10]})
    classes = T.classify(doc, sections.tag_document(doc))
    assert classes["b6"] == {"class": T.UNREADABLE, "reason": "table_structure_warning:" + code}
    assert classes["neighbor"]["class"] == classes["next_page"]["class"] == T.TABLE
    assert classes["b3"]["class"] == T.TARGET
    assert classes["b10"] == {"class": T.NOT_TARGETED, "reason": "annex_table"}
    assert classes["b11"]["reason"] == "requires_vision"
    table["excluded_from_retrieval"] = True
    assert T.classify(doc, sections.tag_document(doc))["b6"]["class"] == T.EXCLUDED


def test_split_grid_requires_containment_and_recovery_history():
    doc = warning_doc()
    doc["warnings"][0].update(code="table_background_grid_split", bbox=[-5, -5, 20, 20])
    table = next(b for b in doc["blocks"] if b["id"] == "b6")
    assert T.table_structure_warnings(doc, table) == []
    table["transformations"] = ["background_grid_split"]
    assert T.table_structure_warnings(doc, table) == ["table_background_grid_split"]
    table["bbox"] = [15, 15, 25, 25]
    assert T.table_structure_warnings(doc, table) == []


def test_multiple_warning_reasons_are_deduplicated_and_stable():
    doc = warning_doc()
    doc["warnings"] += [{**doc["warnings"][0], "code": "table_geometry_ambiguous"}, doc["warnings"][0]]
    tags = sections.tag_document(doc)
    first = T.classify(doc, tags)["b6"]
    doc["warnings"].reverse()
    assert T.classify(doc, tags)["b6"] == first
    assert first["reason"] == "table_structure_warning:table_geometry_ambiguous,table_grid_sparse"


@pytest.mark.parametrize("warning", [
    {"code": code, "page": 1, "bbox": [0, 0, 10, 10]}
    for code in ["table_heading_ambiguous", "table_cell_linebreak_review", "approval_like_table", "other"]
] + [{"code": "table_grid_sparse", "page": 1},
     {"code": "table_grid_sparse", "page": 1, "bbox": [0, 0, 0, 0]},
     {"code": "table_grid_sparse", "bbox": [0, 0, 10, 10]}])
def test_unrelated_or_unlocatable_warning_does_not_exclude(warning):
    doc = parsed_doc()
    doc["warnings"] = [warning]
    assert T.classify(doc, sections.tag_document(doc))["b6"]["class"] == T.TABLE


def workspace(tmp_path, doc, selected=None):
    source = tmp_path / "결과보고서" / "doc.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps(doc), encoding="utf-8")
    selection = pilot.whole_documents([source])
    selection["documents"][0]["sections"] = selected
    work = tmp_path / "work"
    work.mkdir()
    pilot.freeze(selection, work)
    return review.Workspace(work)


def assembled(doc):
    tags = sections.tag_document(doc)
    dr = R.document_request(doc, tags)
    response = document_response(dr)
    pairs = [(r, response_for(r)) for r in R.block_requests(doc, tags, response)]
    return dr, response, pairs, assemble(doc, tags, dr, response, pairs, [], RUN)


def test_warning_table_is_omitted_from_requests_and_prefilled():
    doc = warning_doc()
    _, _, pairs, result = assembled(doc)
    assert "b6" not in [t["id"] for r, _ in pairs for t in r["targets"]]
    table = next(b for b in result["blocks"] if b["block_id"] == "b6")["table"]
    assert table["table_status"] == "unreadable"
    assert table["table_reason"] == "table_structure_warning:table_grid_sparse"
    assert table["rows"] == []


def test_old_requests_cannot_bypass_classification_or_overwrite_labels(tmp_path):
    doc = warning_doc()
    with patch.object(T, "STRUCTURE_WARNINGS", frozenset()):
        ws = workspace(tmp_path, doc)
        reqs = ws.requests("doc_t")
        body = {"document_id": "doc_t", "snapshot_id": ws.meta("doc_t")["snapshot"]["snapshot_id"],
                "document_response": document_response(reqs["document_request"]),
                "block_responses": {r["request_id"]: response_for(r) for r in reqs["block_requests"]}}
        assert review.save(ws, "alice", body)["valid"]
        dr, response, pairs, original = assembled(doc)
        run_dir = tmp_path / "run"
        source = run_dir / "documents/doc_t/annotations.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps(original), encoding="utf-8")
        review.import_run(ws, run_dir, "model")
    saved = ws.labels_path("alice", "doc_t")
    before = saved.read_bytes()
    reference = ws.labels_path("model", "doc_t").read_bytes()
    for action in [lambda: review.state(ws, "alice", []), lambda: review.save(ws, "alice", body),
                   lambda: review.finalize(ws, "alice"),
                   lambda: review.import_run(ws, run_dir, "model"),
                   lambda: assemble(doc, sections.tag_document(doc), dr, response, pairs, [], RUN)]:
        with pytest.raises(ValueError, match="대상 선정"):
            action()
    assert saved.read_bytes() == before
    assert ws.labels_path("model", "doc_t").read_bytes() == reference
    assert not (ws.work / "final").exists()


def test_unselected_changed_table_does_not_invalidate_selected_section(tmp_path):
    with patch.object(T, "STRUCTURE_WARNINGS", frozenset()):
        ws = workspace(tmp_path, warning_doc(), ["doc_t_s001"])
    assert {t["id"] for r in ws.requests("doc_t")["block_requests"] for t in r["targets"]} == {"b2", "b3", "b4"}


@pytest.fixture
def repeated_run(tmp_path):
    doc = parsed_doc()
    by_id = {b["id"]: b for b in doc["blocks"]}
    for bid in ["b1", "b3", "b12"]:
        text = by_id[bid]["text"]
        by_id[bid]["text"] = unicodedata.normalize("NFD", "😀 " + text + "\r\n" + text)
    by_id["b9"]["text"] = "가가가"  # 겹치는 두 번째 구절
    for row in by_id["b6"]["rows"]:
        for c, value in enumerate(row):
            if value:
                row[c] = unicodedata.normalize("NFD", value + "\x01 " + value)
    ws = workspace(tmp_path, doc)
    tags = sections.tag_document(doc)
    dr = R.document_request(doc, tags)
    document = document_response(dr)
    document["document_type_evidence"]["occurrence"] = 2
    document["document_topics"] = ["교육"]
    document["document_topics_evidence"] = [{"topic": "교육", "evidence_span": {
        "block_id": "b6", "quote": "담당자 교육 3회", "occurrence": 2}}]
    pairs = [(r, response_for(r)) for r in R.block_requests(doc, tags, document)]
    for request, response in pairs:
        for block in response["blocks"]:
            if block["block_id"] in ("b3", "b12"):
                for a in block["annotations"]:
                    a["evidence_span"]["occurrence"] = 2
                    for step in a["steps"]:
                        step["evidence_span"]["occurrence"] = 2
            if block["block_id"] == "b9":
                block["annotations"][0]["evidence_span"] = span("가가", occurrence=2)
            if block["block_id"] == "b6":
                rows = block["table"]["rows"]
                rows[1]["annotations"] = [ann("r1.a1", "GROUNDS", "담당자 교육 3회", row=1, col=1, occurrence=2,
                    status="determined", steps=[{"step": "ORDINANCE", "evidence_span": span("담당자 교육 3회", row=1, col=1, occurrence=2)}])]
                rows[2]["annotations"] = [ann("r2.a1", "SPEC", "점검 2회", row=2, col=1, occurrence=2, spec_items=[{
                    "spec_field": "일정", "raw_label": "구분", "value": "6월",
                    "evidence_span": span("6월", row=1, col=0, occurrence=2),  # 병합 시작 셀
                    "label_span": span("구분", row=0, col=0, occurrence=1), "reason": None}])]
        assert validate_block_response(request, response) == []
    assert validate_document_response(dr, document, R.block_texts(doc, tags)) == []
    result = assemble(doc, tags, dr, document, pairs, [], RUN)
    run_dir = tmp_path / "run"
    source = run_dir / "documents/doc_t/annotations.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(result), encoding="utf-8")
    return ws, doc, result, run_dir, source


def test_repeated_spans_survive_import_ui_copy_save_reload_and_finalize(repeated_run):
    ws, doc, original, run_dir, _ = repeated_run
    assert review.import_run(ws, run_dir, "model") == ["doc_t"]
    node = shutil.which("node")
    if node is None:
        pytest.skip("실제 검수 화면의 복사·저장 변환에는 Node.js가 필요합니다")
    state = review.state(ws, "alice", ["model"])
    output = subprocess.run([node, str(Path(__file__).with_name("review_labels.cjs")), "--copy"],
                            input=json.dumps(state), text=True, capture_output=True, check=True)
    body = json.loads(output.stdout)
    result = review.save(ws, "alice", body)
    assert result["valid"], result
    reloaded = review.Workspace(ws.work)
    assert review.check(reloaded, "doc_t", reloaded.labels("alice", "doc_t"))["valid"]
    assert review.finalize(reloaded, "alice") == (["doc_t"], [])
    final = json.loads((ws.work / "final/alice/doc_t.json").read_text())
    assert final["document"] == original["document"]
    assert final["blocks"] == original["blocks"]
    # 정상 단일 구절은 null, 반복 구절은 첫 출현부터 순번을 갖는다.
    blocks = {b["block_id"]: b for r in body["block_responses"].values() for b in r["blocks"]}
    assert blocks["b4"]["annotations"][0]["evidence_span"]["occurrence"] is None
    assert blocks["b6"]["table"]["rows"][2]["annotations"][0]["spec_items"][0]["label_span"]["occurrence"] == 1
    assert blocks["b9"]["annotations"][0]["evidence_span"]["occurrence"] == 2


@pytest.mark.parametrize("change", ["start", "end", "quote", "missing", "cell", "inherited", "document"])
def test_bad_import_preserves_existing_reference(repeated_run, change):
    ws, _, result, run_dir, source = repeated_run
    review.import_run(ws, run_dir, "model")
    path = ws.labels_path("model", "doc_t")
    before = path.read_bytes()
    blocks = {b["block_id"]: b for b in result["blocks"]}
    evidence = blocks["b3"]["annotations"][0]["evidence_span"]
    if change in ("start", "end"):
        evidence[change] += 1
    elif change == "quote":
        evidence["quote"] = "다른 구절"
    elif change == "missing":
        blocks["b3"]["annotations"][0]["evidence_span"] = None
    elif change in ("cell", "inherited"):
        item = blocks["b6"]["table"]["rows"][2]["annotations"][0]["spec_items"][0]
        item["evidence_span"]["row"] = 99 if change == "cell" else 2
    else:
        result["document"]["document_type_evidence"]["block_id"] = "missing"
    source.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError):
        review.import_run(ws, run_dir, "model")
    assert path.read_bytes() == before


def test_import_accepts_document_abstention_and_empty_annotation_lists():
    doc = parsed_doc()
    dr = R.document_request(doc, sections.tag_document(doc))
    document = document_response(dr)
    document.update(document_type=None, document_type_evidence=None, document_type_reason="판정 불가",
                    document_topics=None, document_topics_evidence=[], document_topics_reason="판정 불가")
    result = assemble(doc, sections.tag_document(doc), dr, document, [], [], RUN)
    restored, blocks = review._import_spans(doc, result)
    assert restored["document_type_evidence"] is None
    assert restored["document_topics_evidence"] == []
    assert all(b["annotations"] == [] and b["table"]["rows"] == [] for b in blocks)
