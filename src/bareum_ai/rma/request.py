"""문서 요청과 블록 요청을 만든다."""

import hashlib
import json

from bareum_ai import specs
from bareum_ai.dataset.parsing.snapshot import content_hash
from bareum_ai.rma import targets as T

DOC_REQUEST = "rma-doc-request-1"
BLOCK_REQUEST = "rma-block-request-1"
FULL_TEXT_LIMIT = 15_000  # 이 길이 이하이면 문서 전문을 문맥으로 넣는다
NEIGHBORS = 3


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def spec_hashes() -> dict[str, str]:
    return {f"{name}_sha256": sha256(specs.load(name)) for name in ("moves", "sections")}


def validate_spec_hashes(*records: dict) -> None:
    """고정 요청·파일럿과 현재 명세가 다르면 기존 라벨을 재해석하지 않는다."""
    current = spec_hashes()
    if any(any(record.get(key) != value for key, value in current.items()) for record in records):
        raise ValueError("명세가 파일럿·요청 생성 시점과 다릅니다. 생성 당시 코드·명세 버전을 사용하거나 새 파일럿을 만드세요.")


def with_request_id(request: dict) -> dict:
    request["request_id"] = sha256(request)
    return request


def block_text(block: dict) -> str:
    """요청 문맥에 쓰는 한 줄 표현. 표는 행마다 셀을 ' | '로 잇는다."""
    if block["kind"] == "table":
        return "\n".join(" | ".join(c["text"] for c in row) for row in T.table_rows(block))
    return T.normalize(block.get("text", ""))


def document_text(doc: dict, classes: dict[str, dict]) -> str:
    """제외 블록을 뺀 문서 전문. 각 블록 앞에 [block_id]를 붙인다."""
    return "\n".join(
        f"[{b['id']}] {block_text(b)}"
        for b in doc["blocks"]
        if classes[b["id"]]["class"] != T.EXCLUDED
    )


def block_texts(doc: dict, section_tags: dict[str, str]) -> dict[str, str]:
    """block_id → 요청에 담긴 정규화 원문. 문서 응답의 근거 검증에 쓴다."""
    classes = T.classify(doc, section_tags)
    return {b["id"]: block_text(b) for b in doc["blocks"] if classes[b["id"]]["class"] != T.EXCLUDED}


def _sections(doc: dict, section_tags: dict[str, str]) -> list[dict]:
    return [
        {
            "section_id": s["section_id"],
            "head_text": T.normalize(s["head_text"]),
            "tag": section_tags.get(s["section_id"], "SEC_UNKNOWN"),
        }
        for s in doc.get("sections") or []
    ]


def _title(doc: dict) -> str | None:
    return next(
        (T.normalize(b["text"]) for b in doc["blocks"] if b["kind"] == "document_title"),
        None,
    )


def document_request(doc: dict, section_tags: dict[str, str]) -> dict:
    classes = T.classify(doc, section_tags)
    section_spec = specs.load("sections")
    conditions = section_spec["outline_selection"]["applicability_conditions"]
    return with_request_id(
        {
            "schema_version": DOC_REQUEST,
            "document_id": doc["document"]["document_id"],
            "parsed_sha256": content_hash(doc),
            **spec_hashes(),
            "title": _title(doc),
            "sections": _sections(doc, section_tags),
            "outline_ids": list(section_spec["outlines"]),
            "outline_candidates": [
                {"outline_id": name, "required": outline["required"], "order": outline["order"],
                 "applicability_conditions": conditions.get(name)}
                for name, outline in section_spec["outlines"].items()
            ],
            "section_tag_names": {name: tag["name"] for name, tag in section_spec["tags"].items()},
            "text": document_text(doc, classes),
        }
    )


def _target_payload(block: dict, parents: set[str]) -> dict:
    if block["kind"] == "table":
        return {
            "id": block["id"],
            "kind": "table",
            "section_id": block.get("section_id"),
            "rows": T.table_rows(block),
            **({"heading_hint": block["heading_hint"]} if block.get("heading_hint") else {}),
        }
    return {
        "id": block["id"],
        "kind": block["kind"],
        "text": T.normalize(block.get("text", "")),
        "parent_id": block.get("parent_id"),
        "section_id": block.get("section_id"),
        "title_like": T.title_like(block, block["id"] in parents),
    }


def _local_context(doc: dict, classes: dict[str, dict], chunk: list[str]) -> list[dict]:
    """긴 문서의 문맥: 묶음이 속한 섹션 전체, 상위 블록 사슬, 앞뒤 블록."""
    blocks = [b for b in doc["blocks"] if classes[b["id"]]["class"] != T.EXCLUDED]
    index = {b["id"]: i for i, b in enumerate(blocks)}
    by_id = {b["id"]: b for b in blocks}
    keep: set[str] = set()
    sections = {by_id[i].get("section_id") for i in chunk}
    keep.update(b["id"] for b in blocks if b.get("section_id") in sections - {None})
    for block_id in chunk:
        parent, seen = by_id[block_id].get("parent_id"), set()
        while parent in by_id and parent not in seen:
            seen.add(parent)
            keep.add(parent)
            parent = by_id[parent].get("parent_id")
    first, last = index[chunk[0]], index[chunk[-1]]
    keep.update(b["id"] for b in blocks[max(0, first - NEIGHBORS) : last + NEIGHBORS + 1])
    keep.difference_update(chunk)
    return [{"id": b["id"], "text": block_text(b)} for b in blocks if b["id"] in keep]


def block_requests(
    doc: dict, section_tags: dict[str, str], document_labels: dict, include=None
) -> list[dict]:
    """묶음마다 블록 요청 하나. document_labels는 검증을 통과한 문서 응답이다.

    include(block) → bool을 주면 그 블록만 판정 대상으로 삼고 나머지 대상은 문맥으로 돌린다
    (파일럿처럼 일부 섹션만 라벨링할 때).
    """
    classes = T.classify(doc, section_tags)
    if include is not None:
        for block in doc["blocks"]:
            cls = classes[block["id"]]
            if cls["class"] in (T.TARGET, T.TABLE) and not include(block):
                classes[block["id"]] = {"class": T.CONTEXT, "reason": "not_selected"}
    by_id = {b["id"]: b for b in doc["blocks"]}
    parents = {b["parent_id"] for b in doc["blocks"] if b.get("parent_id")}
    full = document_text(doc, classes)
    common = {
        "schema_version": BLOCK_REQUEST,
        "document_id": doc["document"]["document_id"],
        "parsed_sha256": content_hash(doc),
        **spec_hashes(),
        "document": {
            "title": _title(doc),
            "document_type": document_labels.get("document_type"),
            "document_topics": document_labels.get("document_topics"),
            "sections": _sections(doc, section_tags),
        },
    }
    out = []
    for chunk in T.chunks(doc, classes):
        context = (
            {"full_text": full}
            if len(full) <= FULL_TEXT_LIMIT
            else {"blocks": _local_context(doc, classes, chunk)}
        )
        out.append(
            with_request_id(
                {
                    **common,
                    "context": context,
                    "targets": [_target_payload(by_id[i], parents) for i in chunk],
                    "target_block_ids": [i for i in chunk if by_id[i]["kind"] != "table"],
                    "target_table_ids": [i for i in chunk if by_id[i]["kind"] == "table"],
                    "document_block_ids": [
                        b["id"] for b in doc["blocks"] if classes[b["id"]]["class"] != T.EXCLUDED
                    ],
                }
            )
        )
    return out


def widened(request: dict, doc: dict, section_tags: dict[str, str]) -> dict:
    """문맥을 문서 전문으로 넓힌 같은 대상의 요청. CONTEXT_MISSING이 많을 때 다시 묻는 데 쓴다."""
    out = {k: v for k, v in request.items() if k != "request_id"}
    out["context"] = {"full_text": document_text(doc, T.classify(doc, section_tags))}
    return with_request_id(out)
