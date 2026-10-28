"""검증을 통과한 응답을 문서별 라벨 파일(rma-annotations-2)로 조립한다.

라벨러가 적은 quote·occurrence로 원문에서 start·end를 찾아 채우고,
미리 정한 표 상태(not_targeted·unreadable)와 실패한 묶음을 함께 기록한다.
"""

from bareum_ai.rma import request as R
from bareum_ai.rma import targets as T
from bareum_ai.rma.validate import locate
from bareum_ai.dataset.parsing import snapshot

ANNOTATIONS = "rma-annotations-2"


def _span(span: dict, text: str) -> dict:
    found = locate(text, span["quote"], span.get("occurrence"))
    if isinstance(found, str):
        raise ValueError(f"검증되지 않은 근거입니다: {found}")
    start, end = found
    out = {k: span[k] for k in ("block_id", "row", "col") if span.get(k) is not None}
    return {**out, "start": start, "end": end, "quote": span["quote"]}


def _annotation(ann: dict, text_of) -> dict:
    return {
        **ann,
        "evidence_span": _span(ann["evidence_span"], text_of(ann["evidence_span"])),
        "steps": [
            {**s, "evidence_span": _span(s["evidence_span"], text_of(s["evidence_span"]))}
            for s in ann["steps"]
        ],
        "spec_items": [
            {
                **item,
                "evidence_span": _span(item["evidence_span"], text_of(item["evidence_span"])),
                "label_span": (
                    _span(item["label_span"], text_of(item["label_span"]))
                    if item.get("label_span")
                    else None
                ),
            }
            for item in ann["spec_items"]
        ],
    }


def _block(response_block: dict, target: dict) -> dict:
    out = {
        "block_id": target["id"],
        "section_id": target.get("section_id"),
        "primary_annotation_id": response_block["primary_annotation_id"],
    }
    if target["kind"] != "table":
        text = target["text"]
        out["annotations"] = [_annotation(a, lambda span: text) for a in response_block["annotations"]]
        out["table"] = None
        return out
    rows = target["rows"]
    table = response_block["table"]
    cell_text = lambda span: rows[span["row"]][span["col"]]["text"]
    out["annotations"] = []
    out["table"] = {
        **table,
        "rows": [
            {**row, "annotations": [_annotation(a, cell_text) for a in row["annotations"]]}
            for row in table["rows"]
        ],
    }
    return out


def _prefilled(block: dict, cls: dict) -> dict:
    return {
        "block_id": block["id"],
        "section_id": block.get("section_id"),
        "primary_annotation_id": None,
        "annotations": [],
        "table": {
            "table_status": cls["class"],
            "table_reason": cls["reason"],
            "header_rows": [],
            "row_header_cols": [],
            "rows": [],
        },
    }


def _document(doc: dict, doc_response: dict, block_texts: dict[str, str]) -> dict:
    def span(value):
        return _span(value, block_texts[value["block_id"]]) if value else None

    return {
        "document_id": doc["document"]["document_id"],
        "document_type": doc_response["document_type"],
        "document_type_evidence": span(doc_response.get("document_type_evidence")),
        "document_type_reason": doc_response.get("document_type_reason"),
        "document_topics": doc_response["document_topics"],
        "document_topics_evidence": [
            {"topic": e["topic"], "evidence_span": span(e["evidence_span"])}
            for e in doc_response.get("document_topics_evidence") or []
        ],
        "document_topics_reason": doc_response.get("document_topics_reason"),
        "outline_id": doc_response["outline_id"],
        "outline_reason": doc_response["outline_reason"],
    }


def assemble(
    doc: dict,
    section_tags: dict[str, str],
    doc_request: dict,
    doc_response: dict,
    block_results: list[tuple[dict, dict]],
    failed: list[dict],
    run: dict,
) -> dict:
    """block_results는 검증을 통과한 (블록 요청, 블록 응답) 목록이다.

    failed는 끝내 통과하지 못한 묶음 {"request_id", "block_ids", "issue_codes"} 목록이며,
    그 블록에는 라벨이 없다. run은 {"run_id", "labeler"}다.
    """
    classes = T.classify(doc, section_tags)
    digest = snapshot.content_hash(doc)
    for req in [doc_request, *(req for req, _ in block_results)]:
        R.validate_spec_hashes(req)
        if (req.get("parsed_sha256") not in {digest, R.sha256(doc)}
            or req.get("document_id") != doc["document"]["document_id"]):
            raise ValueError("다른 파싱 결과에 대한 RMA 요청을 조립할 수 없습니다")
    labeled: dict[str, dict] = {}
    for req, resp in block_results:
        T.require_labelable(classes, (t["id"] for t in req["targets"]))
        targets = {t["id"]: t for t in req["targets"]}
        for block in resp["blocks"]:
            labeled[block["block_id"]] = _block(block, targets[block["block_id"]])

    blocks = []
    for block in doc["blocks"]:
        cls = classes[block["id"]]
        if block["id"] in labeled:
            blocks.append(labeled[block["id"]])
        elif cls["class"] in (T.NOT_TARGETED, T.UNREADABLE):
            blocks.append(_prefilled(block, cls))

    result = {
        "schema_version": ANNOTATIONS,
        "run_id": run["run_id"],
        "labeler": run["labeler"],
        "specs": {**R.spec_hashes(), "parser_schema_version": doc.get("schema_version")},
        "request_ids": [doc_request["request_id"], *(req["request_id"] for req, _ in block_results)],
        "document": _document(doc, doc_response, R.block_texts(doc, section_tags)),
        "blocks": blocks,
        "failed_units": failed,
    }
    result = snapshot.bind(doc, result)
    result["span_basis"] = {
        "block": "bareum_ai.rma.targets.normalize(block.text)",
        "table": "bareum_ai.rma.targets.table_rows(block)[row][col].text",
        "document": "bareum_ai.rma.request.block_text(block)",
    }
    snapshot.validate(doc, result)
    return result
