"""라벨러 응답 검증. 형식과 원문 근거의 실재만 확인하며 의미상 정답 여부는 판단하지 않는다.

오류 코드는 docs/specs/rma-labeling-pipeline.md 6절과 같다. 재요청에 붙일 수 있도록
첫 오류에서 멈추지 않고 모두 모은다.
"""

from dataclasses import dataclass
import re

from bareum_ai import specs
from bareum_ai.rma.request import BLOCK_REQUEST, DOC_REQUEST

DOC_RESPONSE = "rma-doc-response-1"
BLOCK_RESPONSE = "rma-block-response-1"
DOCUMENT_TYPES = ("기획", "상황", "결과", "검토", "기타")
DOCUMENT_TOPICS = ("행사", "교육")
TABLE_ROLES = ("header", "body", "empty")
LABELER_TABLE_STATUS = ("labeled", "unreadable")
ANNOTATION_KEYS = (
    "annotation_id",
    "move",
    "evidence_span",
    "context_block_ids",
    "steps",
    "step_status",
    "step_reason",
    "undetermined_reason",
    "spec_items",
)
_BLOCK_ID = re.compile(r"^a[1-9]\d*$")
_ROW_ID = re.compile(r"^r(\d+)\.a[1-9]\d*$")


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str


def locate(text: str, quote, occurrence) -> tuple[int, int] | str:
    """quote의 위치(start, end) 또는 실패 사유."""
    if not isinstance(quote, str) or not quote:
        return "quote가 비어 있습니다"
    starts = []
    i = text.find(quote)
    while i >= 0:
        starts.append(i)
        i = text.find(quote, i + 1)
    if not starts:
        return "원문에서 quote를 찾지 못했습니다"
    if occurrence is None:
        if len(starts) > 1:
            return f"quote가 {len(starts)}번 나옵니다. occurrence가 필요합니다"
        return starts[0], starts[0] + len(quote)
    if type(occurrence) is not int or not 1 <= occurrence <= len(starts):
        return f"occurrence는 1~{len(starts)} 사이여야 합니다"
    start = starts[occurrence - 1]
    return start, start + len(quote)


def _is_reason(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


class _Checker:
    def __init__(self):
        self.issues: list[Issue] = []

    def add(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue(code, path, message))


def validate_block_response(request: dict, response: dict) -> list[Issue]:
    moves = specs.load("moves")["moves"]
    check = _Checker()
    if request.get("schema_version") != BLOCK_REQUEST:
        check.add("V01", "request", "블록 요청이 아닙니다")
        return check.issues
    if not isinstance(response, dict) or response.get("schema_version") != BLOCK_RESPONSE:
        check.add("V01", "schema_version", f"{BLOCK_RESPONSE}이어야 합니다")
        return check.issues
    if response.get("request_id") != request["request_id"]:
        check.add("V01", "request_id", "요청의 request_id와 다릅니다")

    targets = {t["id"]: t for t in request["targets"]}
    document_ids = set(request["document_block_ids"])
    blocks = response.get("blocks")
    if not isinstance(blocks, list):
        check.add("V02", "blocks", "blocks는 목록이어야 합니다")
        return check.issues
    seen = [b.get("block_id") if isinstance(b, dict) else None for b in blocks]
    missing = set(targets) - set(seen)
    extra = [i for i in seen if i not in targets]
    dupes = {i for i in seen if seen.count(i) > 1}
    if missing:
        check.add("V02", "blocks", f"응답에 없는 대상: {sorted(missing)}")
    if extra:
        check.add("V02", "blocks", f"요청하지 않은 식별값: {extra}")
    if dupes:
        check.add("V02", "blocks", f"중복된 대상: {sorted(dupes)}")

    for b, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("block_id") not in targets:
            continue
        target = targets[block["block_id"]]
        path = f"blocks[{b}]"
        if target["kind"] == "table":
            _check_table(check, path, block, target, moves, document_ids)
        else:
            _check_text_block(check, path, block, target, moves, document_ids)
    return check.issues


def _check_text_block(check, path, block, target, moves, document_ids):
    if block.get("table") is not None:
        check.add("V11", f"{path}.table", "표가 아닌 블록의 table은 null이어야 합니다")
    annotations = block.get("annotations")
    if not isinstance(annotations, list):
        check.add("V02", f"{path}.annotations", "annotations는 목록이어야 합니다")
        return
    if not annotations and not target.get("title_like", target["kind"] == "heading"):
        check.add("V12", f"{path}.annotations", "빈 annotations는 구간 이름만 있는 제목(title_like)에만 허용합니다")
    ids = _check_annotations(
        check, f"{path}.annotations", annotations, moves, document_ids,
        id_pattern=_BLOCK_ID, row=None,
        span_text=lambda span: target["text"], table=None,
    )
    _check_primary(check, f"{path}.primary_annotation_id", block.get("primary_annotation_id"), ids)


def _check_primary(check, path, value, ids):
    if value is not None and value not in ids:
        check.add("V04", path, "null이거나 같은 단위의 annotation_id여야 합니다")


def _check_annotations(check, path, annotations, moves, document_ids, id_pattern, row, span_text, table):
    ids = []
    for a, ann in enumerate(annotations):
        p = f"{path}[{a}]"
        if not isinstance(ann, dict) or any(k not in ann for k in ANNOTATION_KEYS):
            check.add("V02", p, f"필드가 빠졌습니다: {[k for k in ANNOTATION_KEYS if not isinstance(ann, dict) or k not in ann]}")
            continue
        ann_id = ann["annotation_id"]
        match = id_pattern.match(ann_id) if isinstance(ann_id, str) else None
        if not match or (row is not None and int(match.group(1)) != row):
            check.add("V03", f"{p}.annotation_id", "형식이 맞지 않습니다")
        if ann_id in ids:
            check.add("V03", f"{p}.annotation_id", "블록 안에서 중복됩니다")
        ids.append(ann_id)

        move = ann["move"]
        if move not in moves:
            check.add("V05", f"{p}.move", f"정의되지 않은 Move: {move}")
            continue
        defined_steps = moves[move].get("steps") or {}
        _check_steps(check, p, ann, move, defined_steps, span_text, table, row)
        _check_undetermined(check, p, ann, move, moves)
        _check_spec(check, p, ann, move, moves, span_text, table, row)
        _check_span(check, f"{p}.evidence_span", ann["evidence_span"], span_text, table, row)
        context = ann["context_block_ids"]
        if not isinstance(context, list) or any(i not in document_ids for i in context):
            check.add("V13", f"{p}.context_block_ids", "같은 문서의 블록 식별값 목록이어야 합니다")
    return ids


def _check_steps(check, p, ann, move, defined_steps, span_text, table, row):
    steps, status, reason = ann["steps"], ann["step_status"], ann["step_reason"]
    if not isinstance(steps, list):
        check.add("V06", f"{p}.steps", "steps는 목록이어야 합니다")
        return
    names = [s.get("step") if isinstance(s, dict) else None for s in steps]
    for s, name in enumerate(names):
        if name not in defined_steps:
            check.add("V06", f"{p}.steps[{s}]", f"{move}에 정의되지 않은 Step: {name}")
        else:
            _check_span(check, f"{p}.steps[{s}].evidence_span", steps[s].get("evidence_span"), span_text, table, row)
    if len(set(names)) != len(names):
        check.add("V06", f"{p}.steps", "같은 Step을 두 번 적었습니다")
    if not defined_steps:
        ok = status == "not_applicable" and not steps and reason is None
        rule = "Step이 없는 Move는 not_applicable, 빈 steps, step_reason null"
    elif status == "determined":
        ok, rule = bool(steps) and reason is None, "determined는 steps 1개 이상, step_reason null"
    elif status == "partial":
        ok, rule = bool(steps) and _is_reason(reason), "partial은 steps 1개 이상과 step_reason"
    elif status == "undetermined":
        ok, rule = not steps and _is_reason(reason), "undetermined는 빈 steps와 step_reason"
    else:
        ok, rule = False, "Step이 있는 Move의 step_status는 determined·partial·undetermined"
    if move == "UNDETERMINED":
        return  # UNDETERMINED의 steps는 V07에서 본다
    if not ok:
        check.add("V06", f"{p}.step_status", rule)


def _check_undetermined(check, p, ann, move, moves):
    reason = ann["undetermined_reason"]
    if move == "UNDETERMINED":
        if reason not in moves["UNDETERMINED"]["reasons"]:
            check.add("V07", f"{p}.undetermined_reason", "INPUT_BROKEN·CONTEXT_MISSING·AMBIGUOUS 중 하나여야 합니다")
        if ann["steps"] or ann["spec_items"]:
            check.add("V07", p, "UNDETERMINED의 steps·spec_items는 빈 목록이어야 합니다")
    elif reason is not None:
        check.add("V07", f"{p}.undetermined_reason", "UNDETERMINED가 아니면 null이어야 합니다")


def _check_spec(check, p, ann, move, moves, span_text, table, row):
    items = ann["spec_items"]
    if not isinstance(items, list):
        check.add("V08", f"{p}.spec_items", "spec_items는 목록이어야 합니다")
        return
    if move != "SPEC":
        if items:
            check.add("V08", f"{p}.spec_items", "SPEC이 아니면 빈 목록이어야 합니다")
        return
    if not items:
        check.add("V08", f"{p}.spec_items", "SPEC은 항목을 1개 이상 가져야 합니다")
    allowed = moves["SPEC"]["field"]["enum"]
    for i, item in enumerate(items):
        q = f"{p}.spec_items[{i}]"
        if not isinstance(item, dict):
            check.add("V08", q, "항목은 객체여야 합니다")
            continue
        field = item.get("spec_field")
        if field is None:
            if not _is_reason(item.get("reason")):
                check.add("V08", f"{q}.reason", "spec_field가 null이면 사유가 필요합니다")
        elif field not in allowed:
            check.add("V08", f"{q}.spec_field", f"허용되지 않은 항목: {field}")
        for key in ("raw_label", "value"):
            if not isinstance(item.get(key), str):
                check.add("V08", f"{q}.{key}", "문자열이어야 합니다")
        _check_span(check, f"{q}.evidence_span", item.get("evidence_span"), span_text, table, row)
        label_span = item.get("label_span")
        if table is None and label_span is not None:
            check.add("V08", f"{q}.label_span", "label_span은 표에서만 씁니다")
        if table is not None and label_span is not None:
            _check_span(check, f"{q}.label_span", label_span, span_text, table, row)


def _check_span(check, path, span, span_text, table, row):
    if not isinstance(span, dict):
        check.add("V09", path, "근거(span)가 필요합니다")
        return
    if table is not None:
        cell = _table_cell(check, path, span, table, row)
        if cell is None:
            return
        text = cell["text"]
    else:
        text = span_text(span)
    found = locate(text, span.get("quote"), span.get("occurrence"))
    if isinstance(found, str):
        check.add("V09", path, found)


def _table_cell(check, path, span, table, row):
    """V10: 같은 행, 열 제목 행, 그 행을 덮는 병합 시작 셀만 근거가 될 수 있다."""
    r, c = span.get("row"), span.get("col")
    rows = table["rows"]
    if type(r) is not int or type(c) is not int or not 0 <= r < len(rows) or not 0 <= c < len(rows[r]):
        check.add("V10", path, "row·col이 표 범위를 벗어났습니다")
        return None
    cell = rows[r][c]
    if "inherited_from" in cell:
        check.add("V10", path, f"병합으로 덮인 셀입니다. 시작 셀 {cell['inherited_from']}을 가리켜야 합니다")
        return None
    origins = {tuple(x["inherited_from"]) for x in rows[row] if "inherited_from" in x}
    if r != row and r not in table["header_rows"] and (r, c) not in origins:
        check.add("V10", path, "같은 행, 열 제목 행, 병합 시작 셀만 가리킬 수 있습니다")
        return None
    return cell


def _check_table(check, path, block, target, moves, document_ids):
    if block.get("annotations") != []:
        check.add("V11", f"{path}.annotations", "표 블록의 annotations는 빈 목록이어야 합니다")
    if block.get("primary_annotation_id") is not None:
        check.add("V04", f"{path}.primary_annotation_id", "표 블록 수준은 null이어야 합니다")
    table = block.get("table")
    if not isinstance(table, dict):
        check.add("V11", f"{path}.table", "표 블록은 table 객체가 필요합니다")
        return
    rows = target["rows"]
    status = table.get("table_status")
    if status not in LABELER_TABLE_STATUS:
        check.add("V11", f"{path}.table.table_status", "labeled 또는 unreadable이어야 합니다")
        return
    if status == "unreadable":
        if not _is_reason(table.get("table_reason")) or table.get("rows") != []:
            check.add("V11", f"{path}.table", "unreadable은 table_reason과 빈 rows가 필요합니다")
        return
    if table.get("table_reason") is not None:
        check.add("V11", f"{path}.table.table_reason", "labeled이면 null이어야 합니다")
    header_rows, row_header_cols = table.get("header_rows"), table.get("row_header_cols")
    width = max(len(r) for r in rows)
    if not isinstance(header_rows, list) or any(type(r) is not int or not 0 <= r < len(rows) for r in header_rows):
        check.add("V11", f"{path}.table.header_rows", "표 범위 안의 행 번호 목록이어야 합니다")
        header_rows = []
    if not isinstance(row_header_cols, list) or any(type(c) is not int or not 0 <= c < width for c in row_header_cols):
        check.add("V11", f"{path}.table.row_header_cols", "표 범위 안의 열 번호 목록이어야 합니다")
    out_rows = table.get("rows")
    if not isinstance(out_rows, list):
        check.add("V11", f"{path}.table.rows", "rows는 목록이어야 합니다")
        return
    numbers = [r.get("row") if isinstance(r, dict) else None for r in out_rows]
    if sorted(n for n in numbers if type(n) is int) != list(range(len(rows))) or len(numbers) != len(rows):
        check.add("V11", f"{path}.table.rows", f"모든 행(0~{len(rows) - 1})을 한 번씩 가져야 합니다")
    context = {"rows": rows, "header_rows": header_rows}
    for i, out in enumerate(out_rows):
        if not isinstance(out, dict) or type(out.get("row")) is not int or not 0 <= out["row"] < len(rows):
            continue
        r = out["row"]
        p = f"{path}.table.rows[{i}]"
        role, annotations = out.get("role"), out.get("annotations")
        if role not in TABLE_ROLES:
            check.add("V11", f"{p}.role", "header·body·empty 중 하나여야 합니다")
            continue
        if not isinstance(annotations, list):
            check.add("V11", f"{p}.annotations", "annotations는 목록이어야 합니다")
            continue
        if role == "header" and r not in header_rows:
            check.add("V11", f"{p}.role", "header 행은 header_rows에 있어야 합니다")
        if role == "empty" and any(c["text"] and "inherited_from" not in c for c in rows[r]):
            check.add("V11", f"{p}.role", "empty는 모든 셀이 빈 행에만 씁니다")
        if role in ("header", "empty") and annotations:
            check.add("V11", f"{p}.annotations", f"{role} 행은 annotations가 없어야 합니다")
        if role == "body" and not annotations:
            check.add("V11", f"{p}.annotations", "body 행은 annotation이 1개 이상 필요합니다")
        ids = _check_annotations(
            check, f"{p}.annotations", annotations, moves, document_ids,
            id_pattern=_ROW_ID, row=r, span_text=None, table=context,
        )
        primary = out.get("primary_annotation_id")
        if role != "body" and primary is not None:
            check.add("V04", f"{p}.primary_annotation_id", "body가 아닌 행은 null이어야 합니다")
        else:
            _check_primary(check, f"{p}.primary_annotation_id", primary, ids)


def validate_document_response(request: dict, response: dict, block_texts: dict[str, str]) -> list[Issue]:
    """block_texts는 request.block_texts()의 결과(block_id → 정규화 원문)다."""
    check = _Checker()
    if request.get("schema_version") != DOC_REQUEST:
        check.add("V01", "request", "문서 요청이 아닙니다")
        return check.issues
    if not isinstance(response, dict) or response.get("schema_version") != DOC_RESPONSE:
        check.add("V01", "schema_version", f"{DOC_RESPONSE}이어야 합니다")
        return check.issues
    if response.get("request_id") != request["request_id"]:
        check.add("V01", "request_id", "요청의 request_id와 다릅니다")

    def span_ok(path, span):
        if not isinstance(span, dict) or span.get("block_id") not in block_texts:
            check.add("D01", path, "근거는 문서의 block_id와 quote가 필요합니다")
            return
        found = locate(block_texts[span["block_id"]], span.get("quote"), span.get("occurrence"))
        if isinstance(found, str):
            check.add("D01", path, found)

    doc_type = response.get("document_type")
    if doc_type is None:
        if not _is_reason(response.get("document_type_reason")):
            check.add("D01", "document_type_reason", "document_type이 null이면 사유가 필요합니다")
    elif doc_type not in DOCUMENT_TYPES:
        check.add("D01", "document_type", f"{DOCUMENT_TYPES} 중 하나여야 합니다")
    else:
        span_ok("document_type_evidence", response.get("document_type_evidence"))

    topics = response.get("document_topics")
    if topics is None:
        if not _is_reason(response.get("document_topics_reason")):
            check.add("D01", "document_topics_reason", "document_topics가 null이면 사유가 필요합니다")
    elif not isinstance(topics, list) or any(t not in DOCUMENT_TOPICS for t in topics) or len(set(topics)) != len(topics):
        check.add("D01", "document_topics", f"{DOCUMENT_TOPICS}의 부분 목록이어야 합니다")
    else:
        evidence = response.get("document_topics_evidence")
        if not isinstance(evidence, list) or sorted(e.get("topic") for e in evidence if isinstance(e, dict)) != sorted(topics):
            check.add("D01", "document_topics_evidence", "주제마다 근거가 하나씩 필요합니다")
        else:
            for i, e in enumerate(evidence):
                span_ok(f"document_topics_evidence[{i}].evidence_span", e.get("evidence_span"))

    outline = response.get("outline_id")
    if outline is not None and outline not in request["outline_ids"]:
        check.add("D01", "outline_id", f"{request['outline_ids']} 중 하나이거나 null이어야 합니다")
    if not _is_reason(response.get("outline_reason")):
        check.add("D01", "outline_reason", "목차 선택 근거 또는 선택 불가 사유가 필요합니다")
    return check.issues
