"""문서 하나를 라벨링한다: 문서 요청 → 블록 요청 → 검증·재요청 → 조립.

검증에 실패하면 오류를 붙여 최대 MAX_RETRIES번 다시 묻는다. 그래도 실패한 묶음은
failed_units에 남기고 다음 묶음으로 넘어간다. 라벨러 호출 자체의 실패(네트워크·인증)는
LabelerError로 그대로 올려 실행을 멈춘다.
"""

from collections.abc import Callable
import json

from bareum_ai import sections
from bareum_ai.rma import prompt as P
from bareum_ai.rma import request as R
from bareum_ai.rma.assemble import assemble
from bareum_ai.rma.validate import Issue, validate_block_response, validate_document_response

MAX_RETRIES = 2
CONTEXT_MISSING_RATIO = 0.3  # 이 비율 이상이면 문서 전문을 문맥으로 넣어 한 번 더 묻는다


def _ask(labeler, system: str, schema: dict, request: dict, validate, log: Callable[[dict], None]):
    """(검증을 통과한 응답 또는 None, 마지막 오류 목록)."""
    issues: list[Issue] = []
    previous = None
    for attempt in range(1, MAX_RETRIES + 2):
        raw = labeler(
            system=system,
            user=P.user_message(request, issues, previous),
            schema=schema,
            request=request,
        )
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as error:
            issues = [Issue("V00", "response", f"JSON이 아닙니다: {error}")]
        else:
            issues = validate(response)
        log(
            {
                "request_id": request["request_id"],
                "attempt": attempt,
                "raw": raw,
                "issues": [i.__dict__ for i in issues],
                "usage": getattr(labeler, "last_usage", None),
            }
        )
        if not issues:
            return response, []
        previous = raw
    return None, issues


def context_missing_ratio(response: dict) -> float:
    """판정 단위(일반 블록, 표 body 행) 중 CONTEXT_MISSING이 붙은 비율."""
    units = []
    for block in response["blocks"]:
        if block["table"] is None:
            units.append(block["annotations"])
        else:
            units.extend(r["annotations"] for r in block["table"]["rows"] if r["role"] == "body")
    if not units:
        return 0.0
    missing = sum(any(a["undetermined_reason"] == "CONTEXT_MISSING" for a in anns) for anns in units)
    return missing / len(units)


def _failed(request: dict, issues: list[Issue], unit: str) -> dict:
    return {
        "unit": unit,
        "request_id": request["request_id"],
        "block_ids": [t["id"] for t in request.get("targets", [])],
        "issue_codes": sorted({i.code for i in issues}),
    }


def _unlabeled_document(request: dict) -> dict:
    reason = "문서 단위 라벨링이 검증을 통과하지 못했다"
    return {
        "schema_version": "rma-doc-response-1",
        "request_id": request["request_id"],
        "document_type": None,
        "document_type_evidence": None,
        "document_type_reason": reason,
        "document_topics": None,
        "document_topics_evidence": [],
        "document_topics_reason": reason,
        "outline_id": None,
        "outline_reason": reason,
    }


def label_document(doc: dict, labeler, run: dict, log: Callable[[dict], None] = lambda _: None) -> dict:
    """run은 {"run_id", "labeler"}. log는 호출마다 기록 한 줄을 받는다."""
    tags = sections.tag_document(doc)
    failed: list[dict] = []

    doc_request = R.document_request(doc, tags)
    texts = R.block_texts(doc, tags)
    doc_response, issues = _ask(
        labeler,
        P.document_system_prompt(),
        P.document_response_schema(),
        doc_request,
        lambda r: validate_document_response(doc_request, r, texts),
        log,
    )
    if doc_response is None:
        failed.append(_failed(doc_request, issues, "document"))
        doc_response = _unlabeled_document(doc_request)

    system, schema = P.block_system_prompt(), P.block_response_schema()
    results = []
    for request in R.block_requests(doc, tags, doc_response):
        response, issues = _ask(
            labeler, system, schema, request, lambda r, q=request: validate_block_response(q, r), log
        )
        if (
            response is not None
            and "blocks" in request["context"]
            and context_missing_ratio(response) >= CONTEXT_MISSING_RATIO
        ):
            wide = R.widened(request, doc, tags)
            wide_response, _ = _ask(
                labeler, system, schema, wide, lambda r, q=wide: validate_block_response(q, r), log
            )
            if wide_response is not None:
                request, response = wide, wide_response
        if response is None:
            failed.append(_failed(request, issues, "blocks"))
        else:
            results.append((request, response))

    return assemble(doc, tags, doc_request, doc_response, results, failed, run)
