"""라벨링 테스트용 가상 파서 출력과, 그에 맞는 정상 응답."""

import copy
from unittest import mock

from bareum_ai.rma import targets


def small_chunks(test):
    """묶음 상한을 5로 낮춰 가상 문서가 구간별 묶음 3개(b2~b4 / b5·b6·b12 / b9)로 나뉘게 한다."""
    patcher = mock.patch.object(targets, "CHUNK_LIMIT", 5)
    patcher.start()
    test.addCleanup(patcher.stop)


def _block(id, kind, text="", section=None, parent=None, **extra):
    return {"id": id, "page": 1, "kind": kind, "text": text, "source_text": text, "bbox": [0, 0, 10, 10],
            "section_id": section, "parent_id": parent, **extra}


def parsed_doc() -> dict:
    s1, s2, s3 = "doc_t_s001", "doc_t_s002", "doc_t_s003"
    return copy.deepcopy({
        "schema_version": "1.0.0",
        "document": {"document_id": "doc_t", "filename": "t.pdf", "sha256": "0", "page_count": 1},
        "sections": [
            {"section_id": s1, "head_block_id": "b2", "head_text": "1. 추진배경"},
            {"section_id": s2, "head_block_id": "b5", "head_text": "2. 추진결과"},
            {"section_id": s3, "head_block_id": "b9", "head_text": "붙임 1. 세부 내역"},
        ],
        "blocks": [
            _block("b1", "document_title", "2026년 민원 처리 개선 결과"),
            _block("b2", "heading", "1. 추진배경", s1),
            _block("b3", "item", "관련 조례에 따라 시스템을 도입할 계획임.", s1, "b2"),
            _block("b4", "item", "처리 시간이 20% 감소하고 주민 이용 편의가 향상될 것으로 예상됨.", s1, "b2"),
            _block("b5", "heading", "2. 추진결과", s2),
            _block(
                "b6", "table", "", s2,
                rows=[["구분", "추진 실적"], ["6월", "담당자 교육 3회"], ["", "점검 2회"]],
                cells=[
                    {"row": 0, "col": 0, "rowspan": 1, "colspan": 1},
                    {"row": 0, "col": 1, "rowspan": 1, "colspan": 1},
                    {"row": 1, "col": 0, "rowspan": 2, "colspan": 1},
                    {"row": 1, "col": 1, "rowspan": 1, "colspan": 1},
                    {"row": 2, "col": 1, "rowspan": 1, "colspan": 1},
                ],
            ),
            _block("b12", "para", "관련 규정\x01에 따라 추진함.", s2),
            _block("b7", "approval", "담당 과장 국장", excluded_from_retrieval=True),
            _block("b8", "figure", "", s2, excluded_from_retrieval=True, requires_vision=True),
            _block("b9", "attachment", "붙임 1. 세부 내역 1부. 끝.", s3),
            _block("b10", "table", "", s3, rows=[["항목", "금액"]], cells=[]),
            _block("b11", "table", "", s2, rows=[["도식"]], cells=[], requires_vision=True),
        ],
        "layout_regions": [],
        "warnings": [],
    })


def span(quote, **position):
    """구조화 출력처럼 모든 필드를 채운 근거."""
    return {"quote": quote, "occurrence": None, "row": None, "col": None, **position}


def ann(id, move, quote, steps=(), status=None, reason=None, spec_items=(), undetermined=None, **position):
    return {
        "annotation_id": id,
        "move": move,
        "evidence_span": span(quote, **position),
        "context_block_ids": [],
        "steps": [dict(s) for s in steps],
        "step_status": status or "not_applicable",
        "step_reason": reason,
        "undetermined_reason": undetermined,
        "spec_items": [dict(i) for i in spec_items],
    }


LABELS = {
    "b2": {"primary_annotation_id": None, "annotations": []},
    "b3": {
        "primary_annotation_id": "a2",
        "annotations": [
            ann("a1", "GROUNDS", "관련 조례에 따라",
                steps=[{"step": "ORDINANCE", "evidence_span": span("관련 조례")}], status="determined"),
            ann("a2", "ACTION", "시스템을 도입할 계획"),
        ],
    },
    "b4": {
        "primary_annotation_id": "a1",
        "annotations": [
            ann("a1", "EFFECT", "처리 시간이 20% 감소하고 주민 이용 편의가 향상될 것으로 예상됨",
                steps=[
                    {"step": "QUANTITATIVE", "evidence_span": span("처리 시간이 20% 감소")},
                    {"step": "QUALITATIVE", "evidence_span": span("주민 이용 편의가 향상")},
                ], status="determined"),
        ],
    },
    "b5": {"primary_annotation_id": None, "annotations": []},
    "b9": {"primary_annotation_id": "a1", "annotations": [ann("a1", "ANNEX_REF", "붙임 1. 세부 내역 1부")]},
    "b12": {
        "primary_annotation_id": "a1",
        "annotations": [ann("a1", "GROUNDS", "관련 규정에 따라", status="undetermined",
                            reason="규정 종류를 확인할 문맥 부족")],
    },
}

TABLE_LABELS = {
    "b6": {
        "table_status": "labeled",
        "table_reason": None,
        "header_rows": [0],
        "row_header_cols": [0],
        "rows": [
            {"row": 0, "role": "header", "primary_annotation_id": None, "annotations": []},
            {"row": 1, "role": "body", "primary_annotation_id": "r1.a1",
             "annotations": [ann("r1.a1", "EXECUTION", "담당자 교육 3회", row=1, col=1)]},
            {"row": 2, "role": "body", "primary_annotation_id": "r2.a1",
             "annotations": [ann("r2.a1", "EXECUTION", "점검 2회", row=2, col=1)]},
        ],
    },
}


def response_for(request: dict) -> dict:
    """요청의 대상에 맞춘 정상 블록 응답."""
    blocks = []
    for target in request["targets"]:
        if target["kind"] == "table":
            blocks.append({"block_id": target["id"], "primary_annotation_id": None,
                           "annotations": [], "table": copy.deepcopy(TABLE_LABELS[target["id"]])})
        else:
            blocks.append({"block_id": target["id"], **copy.deepcopy(LABELS[target["id"]]), "table": None})
    return {"schema_version": "rma-block-response-1", "request_id": request["request_id"], "blocks": blocks}


def document_response(request: dict) -> dict:
    return {
        "schema_version": "rma-doc-response-1",
        "request_id": request["request_id"],
        "document_type": "결과",
        "document_type_evidence": {"block_id": "b1", "quote": "개선 결과", "occurrence": None},
        "document_type_reason": None,
        "document_topics": [],
        "document_topics_evidence": [],
        "document_topics_reason": None,
        "outline_id": "결과",
        "outline_reason": "결과보고서 목차를 따른다",
    }
