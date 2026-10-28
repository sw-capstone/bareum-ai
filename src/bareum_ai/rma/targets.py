"""라벨링 대상 선정, 텍스트 정규화, 표 행 구성, 요청 묶음 나누기."""

import re
import unicodedata
from math import isfinite

from bareum_ai.sections import ANNEX, UNKNOWN, lookup

TARGET_KINDS = frozenset({"item", "para", "note", "attachment", "heading"})
CONTEXT_KINDS = frozenset({"document_title"})
CHUNK_LIMIT = 40  # 요청 하나의 판정 단위 상한(블록 1개 또는 표 행 1개)

# 블록 분류
TARGET = "target"
TABLE = "table"
NOT_TARGETED = "not_targeted"
UNREADABLE = "unreadable"
CONTEXT = "context"
EXCLUDED = "excluded"

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
STRUCTURE_WARNINGS = frozenset({"table_grid_sparse", "table_geometry_ambiguous", "table_background_grid_split"})
BBOX_TOLERANCE = 1.0  # PDF 좌표. 파서의 표 경계 비교와 같은 허용 오차.


def _valid_bbox(value) -> bool:
    return (
        isinstance(value, (list, tuple)) and len(value) == 4
        and all(type(v) in (int, float) and isfinite(v) for v in value)
        and value[0] < value[2] and value[1] < value[3]
    )


def table_structure_warnings(doc: dict, block: dict) -> list[str]:
    """해당 표에 연결된 구조 경고 코드. 페이지 전체·단순 겹침으로 연결하지 않는다."""
    box = block.get("bbox")
    if block.get("kind") != "table" or block.get("page") is None or not _valid_bbox(box):
        return []
    codes = set()
    for warning in doc.get("warnings") or []:
        code, area = warning.get("code"), warning.get("bbox")
        if code not in STRUCTURE_WARNINGS or warning.get("page") != block["page"] or not _valid_bbox(area):
            continue
        if code == "table_background_grid_split":
            # 경고 bbox는 분리 전 큰 격자다. 실제 복원 이력이 있는 내부 표만 연결한다.
            linked = (
                "background_grid_split" in (block.get("transformations") or [])
                and area[0] - BBOX_TOLERANCE <= box[0]
                and area[1] - BBOX_TOLERANCE <= box[1]
                and box[2] <= area[2] + BBOX_TOLERANCE
                and box[3] <= area[3] + BBOX_TOLERANCE
            )
        else:
            linked = all(abs(a - b) < BBOX_TOLERANCE for a, b in zip(area, box))
        if linked:
            codes.add(code)
    return sorted(codes)


def require_labelable(classes: dict[str, dict], block_ids) -> None:
    """과거 요청으로 현재 판정 대상이 아닌 블록의 라벨을 조립하는 것을 막는다."""
    invalid = sorted({bid for bid in block_ids if classes.get(bid, {}).get("class") not in (TARGET, TABLE)})
    if invalid:
        raise ValueError(f"현재 대상 선정과 요청이 다릅니다: {invalid}. 새 작업 폴더에서 요청을 생성하세요.")


def normalize(text: str) -> str:
    """NFC, 줄바꿈 통일, 줄바꿈·탭을 뺀 제어 문자 제거."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", text)


def classify(doc: dict, section_tags: dict[str, str]) -> dict[str, dict]:
    """block_id → {"class": 분류, "reason": 사유 또는 None}. 문서 순서를 유지한다."""
    out: dict[str, dict] = {}
    for block in doc["blocks"]:
        kind = block["kind"]
        reason = None
        if block.get("excluded_from_retrieval"):
            cls = EXCLUDED
        elif kind == "table":
            if section_tags.get(block.get("section_id")) == ANNEX:
                cls, reason = NOT_TARGETED, "annex_table"
            elif block.get("requires_vision"):
                cls, reason = UNREADABLE, "requires_vision"
            elif not block.get("rows"):
                cls, reason = UNREADABLE, "no_rows"
            elif warnings := table_structure_warnings(doc, block):
                cls, reason = UNREADABLE, "table_structure_warning:" + ",".join(warnings)
            else:
                cls = TABLE
        elif kind in TARGET_KINDS:
            # 표시만 있는 붙임 머리('붙임')처럼 원문이 빈 블록은 판정할 내용이 없다
            cls, reason = (TARGET, None) if normalize(block.get("text", "")).strip() else (CONTEXT, "empty_text")
        elif kind in CONTEXT_KINDS:
            cls = CONTEXT
        else:
            cls = EXCLUDED
        out[block["id"]] = {"class": cls, "reason": reason}
    return out


_SENTENCE_END = re.compile(r"(다|함|음|임|됨|요|[.!?。])\s*$")
TITLE_MAX_CHARS = 15  # 공백 제외
SHORT_TITLE_CHARS = 6  # 사전에 없어도 제목으로 볼 길이('내용', '활용데이터')


def title_like(block: dict, has_children: bool) -> bool:
    """구간 이름만 나타낼 수 있는 블록: heading, 또는 하위 항목을 거느린 제목형 항목.

    이런 블록만 annotations를 비울 수 있다. 파서가 소제목('내용', '향후계획')을 item으로
    분류하는 경우가 있어 종류만 보지 않는다. 하위 항목을 거느린 항목 중에도 목적·명세처럼
    내용이 있는 것이 많으므로, 섹션 사전의 제목어이거나 아주 짧은 표제만 인정한다.
    """
    if block["kind"] == "heading":
        return True
    raw = normalize(block.get("text", ""))
    text = re.sub(r"\s+", "", raw)
    if not has_children or not text or len(text) > TITLE_MAX_CHARS or ":" in text or "：" in text:
        return False
    if _SENTENCE_END.search(text):
        return False
    return len(text) <= SHORT_TITLE_CHARS or lookup(raw) != UNKNOWN


def table_rows(block: dict) -> list[list[dict]]:
    """행마다 셀 목록. 병합으로 덮인 빈 셀은 시작 셀의 값을 inherited_from과 함께 넣는다."""
    rows = block.get("rows") or []
    origins = {}
    for cell in block.get("cells") or []:
        for r in range(cell["row"], cell["row"] + cell.get("rowspan", 1)):
            for c in range(cell["col"], cell["col"] + cell.get("colspan", 1)):
                if (r, c) != (cell["row"], cell["col"]):
                    origins[(r, c)] = (cell["row"], cell["col"])
    out = []
    for r, row in enumerate(rows):
        cells = []
        for c, value in enumerate(row):
            text = normalize(value or "")
            origin = origins.get((r, c))
            if not text and origin and origin[0] < len(rows) and origin[1] < len(rows[origin[0]]):
                cells.append(
                    {
                        "col": c,
                        "text": normalize(rows[origin[0]][origin[1]] or ""),
                        "inherited_from": list(origin),
                    }
                )
            else:
                cells.append({"col": c, "text": text})
        out.append(cells)
    return out


def unit_count(block: dict) -> int:
    return len(block.get("rows") or []) if block["kind"] == "table" else 1


def chunks(doc: dict, classes: dict[str, dict], limit: int | None = None) -> list[list[str]]:
    """판정 대상 블록·표를 섹션 단위로 묶는다.

    이웃한 섹션은 판정 단위 합이 limit 이하인 동안 통째로 이어 붙인다. 섹션 하나가
    limit를 넘으면 그 섹션만 연속 블록으로 나눈다. 표는 나누지 않으며, 한 표가 limit를
    넘으면 그 표만 단독 묶음이 된다.
    """
    limit = limit or CHUNK_LIMIT
    groups: list[tuple[str | None, list[dict]]] = []
    for block in doc["blocks"]:
        if classes[block["id"]]["class"] not in (TARGET, TABLE):
            continue
        section = block.get("section_id")
        if not groups or groups[-1][0] != section:
            groups.append((section, []))
        groups[-1][1].append(block)

    out: list[list[str]] = []
    current: list[str] = []
    size = 0
    for _, blocks in groups:
        total = sum(unit_count(b) for b in blocks)
        if size + total <= limit:
            current.extend(b["id"] for b in blocks)
            size += total
            continue
        if current:
            out.append(current)
            current, size = [], 0
        if total <= limit:
            current, size = [b["id"] for b in blocks], total
            continue
        for block in blocks:
            n = unit_count(block)
            if current and size + n > limit:
                out.append(current)
                current, size = [], 0
            current.append(block["id"])
            size += n
        out.append(current)
        current, size = [], 0
    if current:
        out.append(current)
    return out
