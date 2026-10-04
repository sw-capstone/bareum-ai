"""파서 산출물의 계약·불변식을 검사하는 테스트 도우미."""

import copy
import json
import math
import os
import re
import unicodedata
import unittest
from collections import defaultdict
from pathlib import Path

KNOWN_KINDS = {
    "item",
    "para",
    "heading",
    "figure",
    "table",
    "note",
    "approval",
    "page_number",
    "document_title",
    "attachment",
    "title_fragment",
    "header",
    "footer",
}

EXCLUDED_KINDS = {
    "header",
    "footer",
    "page_number",
    "figure",
    "approval",
    "title_fragment",
}


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def squash(s):
    return re.sub(r"\s+", "", nfc(s))


class Report:
    """검사 결과. violation 은 고쳐야 할 것, known_gap 은 이미 아는 결손."""

    def __init__(self):
        self.violations = defaultdict(list)
        self.gaps = defaultdict(list)

    def fail(self, code, doc, detail):
        self.violations[code].append((doc, detail))

    def gap(self, code, doc, detail):
        self.gaps[code].append((doc, detail))


def check_schema(doc, name, rep, expected_schema):
    if doc.get("schema_version") != expected_schema:
        rep.fail(
            "C01 schema_version 불일치",
            name,
            f"{doc.get('schema_version')} != {expected_schema}",
        )

    for key in ("document", "parser", "pages", "sections", "blocks", "warnings"):
        if key not in doc:
            rep.fail("C02 최상위 필드 누락", name, key)
    if "document" not in doc or "blocks" not in doc:
        return

    meta = doc["document"]
    for key in ("document_id", "filename", "sha256", "page_count"):
        if not meta.get(key):
            rep.fail("C03 document 필드 누락", name, key)
    if meta.get("page_count") != len(doc.get("pages", [])):
        rep.fail(
            "C03 page_count 불일치",
            name,
            f"{meta.get('page_count')} != {len(doc.get('pages', []))}",
        )

    blocks = doc["blocks"]
    ids = [b.get("id") for b in blocks]
    if any(not isinstance(i, str) or not i for i in ids):
        rep.fail("C04 block id 누락/형식", name, "")
    if len(ids) != len(set(ids)):
        dup = [i for i in set(ids) if ids.count(i) > 1]
        rep.fail("C04 block id 중복", name, f"{len(dup)}건 {dup[:3]}")
    known = set(ids)

    for b in blocks:
        bid = b.get("id")
        if b.get("kind") not in KNOWN_KINDS:
            rep.fail("C13 미지의 kind", name, f"{bid} {b.get('kind')}")
        page = b.get("page")
        if not isinstance(page, int) or not (1 <= page <= meta.get("page_count", 0)):
            rep.fail("C12 page 범위 밖", name, f"{bid} page={page}")
        bbox = b.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            rep.fail("C11 bbox 형식", name, f"{bid} {bbox}")
        elif not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bbox):
            rep.fail("C11 bbox 숫자 오류", name, f"{bid} {bbox}")
        elif bbox[0] > bbox[2] or bbox[1] > bbox[3]:
            rep.fail("C11 bbox 역전", name, f"{bid} {bbox}")
        parent = b.get("parent_id")
        if parent is not None:
            if parent == bid:
                rep.fail("C05 parent 자기참조", name, bid)
            elif parent not in known:
                rep.fail("C05 parent 미실재", name, f"{bid} -> {parent}")
        if b.get("kind") in EXCLUDED_KINDS and not b.get("excluded_from_retrieval"):
            rep.fail("C10 제외 대상 미표시", name, f"{bid} kind={b.get('kind')}")
        if b.get("kind") == "table" and not b.get("rows"):
            rep.fail("C14 표에 rows 없음", name, bid)
        if b.get("rows"):
            rows, sources = b["rows"], b.get("source_rows", [])
            if [len(r) for r in rows] != [len(r) for r in sources]:
                rep.fail("C17 표 원문 격자 불일치", name, bid)
            positions = [(c.get("row"), c.get("col")) for c in b.get("cells", [])]
            if not positions or len(positions) != len(set(positions)):
                rep.fail("C17 표 셀 좌표 누락/중복", name, bid)
        if b.get("marker") and (
            b.get("marker_raw") != b["marker"] or not b.get("marker_normalized")
        ):
            rep.fail("C18 원문 부호 계약 위반", name, bid)
        if (
            b.get("kind") in ("item", "para", "heading")
            and not (b.get("text") or "").strip()
        ):
            rep.fail("C16 본문 블록이 빈 텍스트", name, bid)

    parent_of = {b.get("id"): b.get("parent_id") for b in blocks}
    for bid in parent_of:
        seen, cur = set(), bid
        while cur is not None and cur in parent_of:
            if cur in seen:
                rep.fail("C05 parent 순환", name, bid)
                break
            seen.add(cur)
            cur = parent_of[cur]


def check_sections(doc, name, rep):
    blocks = doc.get("blocks", [])
    sections = doc.get("sections", [])
    known = {b.get("id") for b in blocks}
    page_of = {b.get("id"): b.get("page") for b in blocks}
    sec_ids = {s.get("section_id") for s in sections}
    by_id = {b.get("id"): b for b in blocks}
    if len(sec_ids) != len(sections):
        rep.fail("C06 section_id 중복", name, "")

    for s in sections:
        sid = s.get("section_id")
        if s.get("head_block_id") not in known:
            rep.fail(
                "C07 head_block_id 미실재", name, f"{sid} -> {s.get('head_block_id')}"
            )
        for key in ("block_ids", "child_block_ids"):
            missing = [i for i in s.get(key, []) if i not in known]
            if missing:
                rep.fail(f"C07 {key} 미실재", name, f"{sid} {missing[:3]}")
        if s.get("head_block_id") not in s.get("block_ids", []):
            rep.fail("C07 머리가 block_ids 에 없음", name, sid)
        if not set(s.get("child_block_ids", [])) <= set(s.get("block_ids", [])):
            rep.fail("C07 child ⊄ block_ids", name, sid)
        pr = s.get("page_range") or [0, 0]
        if pr[0] > pr[1]:
            rep.fail("C09 page_range 역전", name, f"{sid} {pr}")
        pages = [page_of[i] for i in s.get("block_ids", []) if i in page_of]
        if pages and (min(pages) != pr[0] or max(pages) != pr[1]):
            rep.fail(
                "C09 page_range 불일치",
                name,
                f"{sid} {pr} vs {min(pages)}~{max(pages)}",
            )
        for child in s.get("child_block_ids", []):
            if child in by_id and by_id[child].get("parent_id") != s.get(
                "head_block_id"
            ):
                rep.fail("C07 직속 자식 parent 불일치", name, child)
        if (
            not (s.get("head_text") or "").strip()
            and by_id.get(s.get("head_block_id"), {}).get("kind") != "attachment"
        ):
            rep.gap("G03 구간 머리 텍스트 없음", name, sid)

    owner = {}
    for s in sections:
        for i in s.get("block_ids", []):
            if i in owner:
                rep.fail(
                    "C08 구간 중첩", name, f"{i} in {owner[i]} & {s.get('section_id')}"
                )
            owner[i] = s.get("section_id")

    for b in blocks:
        sid = b.get("section_id")
        if sid is not None and sid not in sec_ids:
            rep.fail("C06 section_id 미실재", name, f"{b.get('id')} -> {sid}")
        if sid != owner.get(b.get("id")):
            rep.fail(
                "C06 section_id 소유권 불일치",
                name,
                f"{b.get('id')} -> {sid} vs {owner.get(b.get('id'))}",
            )

    if not sections:
        rep.gap("G02 구간 0개", name, "")


MULTILINE_KINDS = {"table", "approval"}


def is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(ch in it for ch in needle)


def check_normalization(doc, name, rep):
    """정규화가 원문에 없던 글자를 만들지 않았는가."""
    for b in doc.get("blocks", []):
        text, src = b.get("text"), b.get("source_text")
        if text is None or src is None:
            continue
        if "\n" in text and b.get("kind") not in MULTILINE_KINDS:
            rep.fail(
                "P03 본문 블록에 줄바꿈 잔존",
                name,
                f"{b.get('id')} kind={b.get('kind')}",
            )
        if not is_subsequence(squash(text), squash(src)):
            rep.fail(
                "P02 원문에 없는 글자 생성",
                name,
                f"{b.get('id')}: {text[:28]!r} ⊄ {src[:28]!r}",
            )


def check_marker_roundtrip(doc, name, rep):
    """marker + text 로 원문을 되짚을 수 있는가."""
    for b in doc.get("blocks", []):
        marker, text, src = b.get("marker"), b.get("text"), b.get("source_text")
        if not marker or text is None or src is None:
            continue
        if not is_subsequence(squash(marker), squash(src)):
            rep.fail("P05 원문 부호 불일치", name, f"{b.get('id')}: {marker!r}")


VOLATILE = (("parser", "stats", "elapsed_seconds"), ("parser", "pymupdf_version"))


def strip_volatile(doc):
    out = json.loads(json.dumps(doc))
    for path in VOLATILE:
        node = out
        for key in path[:-1]:
            node = node.get(key, {})
        node.pop(path[-1], None)
    return out


# 원본 PDF가 필요한 테스트는 BAREUM_PARSER_CORPUS에 검증 기록
# (reports/parser_final_validation/documents.json)이 있는 폴더를 지정했을 때만 실행한다.
CORPUS = os.environ.get("BAREUM_PARSER_CORPUS")
requires_corpus = unittest.skipUnless(
    CORPUS, "원본 PDF 필요: BAREUM_PARSER_CORPUS 지정 시 실행"
)


def source_records():
    path = Path(CORPUS) / "reports/parser_final_validation/documents.json"
    return json.loads(path.read_text(encoding="utf-8"))


def stable_payload(payload):
    result = copy.deepcopy(payload)
    result["parser"]["stats"].pop("elapsed_seconds", None)
    return result


def validate_relations(document):
    errors = []
    pages = {page["page"] for page in document["pages"]}
    if pages != set(range(1, document["document"]["page_count"] + 1)):
        errors.append("page identifiers do not cover document")
    for block in document["blocks"]:
        rows = block.get("rows")
        if not rows:
            continue
        if len({len(row) for row in rows}) != 1:
            errors.append(f"{block['id']}: ragged table")
        for cell in block.get("cells", []):
            row, col = cell["row"], cell["col"]
            if not (0 <= row < len(rows) and 0 <= col < len(rows[row])):
                errors.append(f"{block['id']}: cell index outside grid")
            if cell["rowspan"] < 1 or cell["colspan"] < 1:
                errors.append(f"{block['id']}: invalid cell span")
    blocks = {b["id"]: b for b in document["blocks"]}
    node_ids = set()
    region_ids = set()
    for region in document.get("layout_regions", []):
        if region["id"] in region_ids or region["page"] not in pages:
            errors.append("invalid layout region identity/page")
        region_ids.add(region["id"])
        nodes = {n["id"] for n in region["nodes"]}
        if len(nodes) != len(region["nodes"]) or node_ids & nodes:
            errors.append("duplicate layout node")
        node_ids.update(nodes)
        for node in region["nodes"]:
            for bid in node["block_ids"] + node.get("detail_block_ids", []):
                if bid not in blocks or blocks[bid]["page"] != region["page"]:
                    errors.append("layout reference missing or on another page")
            cell = node.get("cell_ref")
            if cell:
                table = blocks.get(cell["block_id"], {})
                rows = table.get("rows", [])
                if not (
                    0 <= cell["row"] < len(rows)
                    and 0 <= cell["col"] < len(rows[cell["row"]])
                ):
                    errors.append("layout cell reference outside grid")
        for edge in region["edges"]:
            if (
                edge["source"] not in nodes
                or edge["target"] not in nodes
                or edge["source"] == edge["target"]
            ):
                errors.append("invalid layout edge endpoints")
            if edge["type"] == "directed" and not (
                edge.get("evidence_text") and edge.get("evidence_bbox")
            ):
                errors.append("directed edge without visible evidence")
    for block in blocks.values():
        if block.get("layout_node_id") and block["layout_node_id"] not in node_ids:
            errors.append("unknown layout node on block")
    return errors
