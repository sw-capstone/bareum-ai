"""공공보고서 PDF의 원문, 블록, 표, 구간을 추출한다."""

import argparse
import hashlib
import html
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pymupdf

from . import parser_layout

pymupdf.no_recommend_layout()

PARSER_NAME = "public-report-parser"
SCHEMA_VERSION = "1.0.0"

SPACE_GAP_RATIO = 0.15
DUP_OVERLAP_RATIO = 0.8
WRAP_MAX_SHORTFALL = 0.6
WRAP_MAX_VGAP = 1.2
ROW_OVERLAP_RATIO = 0.5
EDGE_BAND = 0.10
EDGE_BAND_WIDE = 0.12
RUNNING_HEAD_RATIO = 0.6
TITLE_TOP_RATIO = 0.60
TITLE_MIN_CHARS = 5
TITLE_MAX_CHARS = 100
TITLE_SIZE_RATIO = 1.2
MIN_FIGURE_SIZE = 8.0
FIGURE_VISION_MIN_AREA = 0.02
VECTOR_SHAPE_MIN = 5
VECTOR_SHAPE_MIN_SIDE = 8.0
GHOST_EMPTY_RATIO = 0.75
GHOST_MIN_COLS = 8
APPROVAL_MIN_HITS = 2
APPROVAL_GAP = 30.0
SHADOW_MAX_LUMA = 0.70
SCAN_COVER_RATIO = 0.7
OUTLINE_MIN_DRAWINGS = 50
OUTPUT_DIR = Path("parsed_output")
PAGE_NUM_RE = re.compile(r"^[\s\-–—]*\d{1,4}\s*(?:/\s*\d{1,4})?[\s\-–—]*$")
PAGE_NUM_DASHED_RE = re.compile(r"^[-–—]\s*\d{1,4}\s*[-–—]$")
DIGITS_RE = re.compile(r"\d+")
SECTION_NO_RE = re.compile(r"^(\d{1,2})\.?$")
MULTI_SPACE_RE = re.compile(r"\s{2,}")
LEADER_DOTS_RE = re.compile(r"[·․‧∙⋅.…]{4,}")
APPROVAL_FIELD_RE = re.compile(
    r"문서번호|등록번호|생산등록번호|등록일자|결재일자|시행일자|공개구분|공개여부|보존기간|방침번호|협조자|전결|대결"
)
TITLE_CELL_MARKER_RE = re.compile(r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|\d{1,2})\.?$")
ATTACHMENT_RE = re.compile(
    r"^(붙\s*임|참\s*고|첨\s*부|별\s*첨)(?=$|\s|\d|[.:])"
    r"\s*(\d{1,2}(?!\d))?\s*[.:]?\s*"
)
ORG_NAME_RE = re.compile(
    r"^[가-힣]{2,10}(시|도|군|구|청|원|과|팀|국|실|관|본부|센터|공사|공단|연구소|사업소|보건소|재단|협회)$"
)
SYMBOL_SQUARE = "\uf000\uf06d\uf071"
SYMBOL_CIRCLE = "\uf09e"
MARKER_PATTERNS = [
    (1, "roman", re.compile(r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.?)\s*")),
    (2, "number", re.compile(r"^(\d{1,2}\.)\s+(?!\d{1,2}\.)")),
    (3, "square", re.compile(r"^([□■ㅁ▢▣◻◼❑❒])\s*")),
    (3, "square", re.compile(r"^([" + SYMBOL_SQUARE + r"])\s*")),
    (3, "square", re.compile(r"^([mq])\s+")),
    (4, "circle", re.compile(r"^([ㅇ○◦●◯❍◎])\s*")),
    (4, "circle", re.compile(r"^([oO])\s+")),
    (4, "circle", re.compile(r"^([" + SYMBOL_CIRCLE + r"])\s*")),
    (5, "dash", re.compile(r"^([-‐‑–—])(?:\s+|(?=\D))")),
    (6, "dot", re.compile(r"^([·•∙‧▪▫‣․])\s*(?=\S)")),
    (6, "dot", re.compile(r"^([ㆍ])\s+")),
    (None, "note", re.compile(r"^([※*✱☞])\s*")),
    (None, "circled", re.compile(r"^([①-⑳])\s*(?=\S)")),
    (None, "paren", re.compile(r"^(\d{1,2}\))\s*(?=\S)")),
    (None, "hangul", re.compile(r"^([가나다라마바사아자차카타파하]\.)\s+")),
    (None, "paren", re.compile(r"^(\([0-9가-힣a-zA-Z]{1,3}\))\s*(?=\S)")),
    (None, "shape", re.compile(r"^([◆◇▶▷★☆▲△▼▽])\s*(?=\S)")),
]
NONSTANDARD_TYPES = ("circled", "paren", "hangul", "shape")
SYMBOL_MARKERS = SYMBOL_SQUARE + SYMBOL_CIRCLE + "mq"
HEADING_LEVELS = (1, 2)
MAX_SECTION_DEPTH = 16
SECTION_HEAD_MAX_CHARS = 20
SENTENCE_ENDING = re.compile(r"(함|음|임|됨|짐|옴|바람|할것|하였음|이다|한다|합니다)$")
RUNNING_KINDS = ("header", "footer", "page_number")
STACKED_KINDS = ("heading", "item", "attachment")
EXCLUDED_KINDS = RUNNING_KINDS + ("figure", "approval", "title_fragment")


@dataclass
class Word:
    text: str
    bbox: tuple
    source_start: int | None = None
    source_end: int | None = None


@dataclass
class Line:
    source_text: str
    text: str
    bbox: tuple
    size: float
    words: list[Word]
    trailing_space: bool = False
    line_count: int = 1
    transformations: list[str] = field(default_factory=list)
    is_light: bool = False


@dataclass
class Block:
    kind: str
    source_text: str
    text: str
    page: int
    bbox: tuple
    id: str = ""
    level: int | None = None
    marker: str | None = None
    marker_type: str | None = None
    indent_ta: float | None = None
    size: float = 0.0
    line_count: int = 1
    parent_id: str | None = None
    section_path: list[str] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)
    excluded_from_retrieval: bool = False
    requires_vision: bool = False
    rows: list[list[str]] = field(default_factory=list)
    source_rows: list[list[str]] = field(default_factory=list)
    section_id: str | None = None
    marker_raw: str | None = None
    marker_normalized: str | None = None
    cells: list[dict] = field(default_factory=list)
    layout_node_id: str | None = None
    source_fragments: list[dict] = field(default_factory=list)


@dataclass
class Section:
    """문서의 장(章). 의미 라벨은 붙이지 않는다."""

    section_id: str
    head_block_id: str
    head_text: str
    marker: str | None
    level: int
    block_ids: list[str] = field(default_factory=list)
    child_block_ids: list[str] = field(default_factory=list)
    page_range: tuple[int, int] = (0, 0)


@dataclass
class Stats:
    pages: int = 0
    restored_spaces: int = 0
    dropped_duplicates: int = 0
    merged_lines: int = 0
    joined_rows: int = 0
    running_elements: int = 0
    tables: int = 0
    vision_figures: int = 0
    vector_pages: int = 0
    dropped_shadows: int = 0
    attachments: int = 0
    ghost_grids: int = 0
    table_headings: int = 0
    approval_blocks: int = 0
    figures: int = 0
    sections: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class DocumentPages:
    metadata: list[dict[str, Any]]
    raw_text: list[str]
    lines: list[list[Line]]
    tables: list[list[Block]]
    figures: list[list[Block]]
    heights: list[float]
    layouts: list[list[dict]] = field(default_factory=list)


def union(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def center_in(bbox: tuple, box: tuple) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def overlap_ratio(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (ix * iy) / area if area else 0.0


def y_overlap(a: tuple, b: tuple) -> float:
    height = min(a[3] - a[1], b[3] - b[1])
    shared = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return shared / height if height else 0.0


def luminance(color: int) -> float:
    """PDF 색상 정수를 밝기(0=검정, 1=흰색)로 바꾼다."""
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def normalize_space(text: str) -> str:
    """추출 과정에서 겹친 공백만 정리한다. 원문 띄어쓰기는 검증 대상이므로 손대지 않는다."""
    return MULTI_SPACE_RE.sub(" ", text).strip()


def add_transformation(target: list[str], name: str) -> None:
    if name not in target:
        target.append(name)


def build_line(raw: dict, stats: Stats) -> Line | None:
    source_chars: list[str] = []
    chars: list[tuple[str, tuple | None, int | None]] = []
    prev = None
    source_length = 0
    transformations: list[str] = []

    for span in raw["spans"]:
        threshold = span["size"] * SPACE_GAP_RATIO
        for ch in span["chars"]:
            source_chars.append(ch["c"])
            if (
                not ch["c"].isspace()
                and prev is not None
                and chars
                and not chars[-1][0].isspace()
            ) and ch["bbox"][0] - prev["bbox"][2] > threshold:
                chars.append((" ", None, None))
                stats.restored_spaces += 1
                add_transformation(transformations, "space_restored")
            chars.append((ch["c"], tuple(ch["bbox"]), source_length))
            source_length += len(ch["c"])
            prev = ch

    joined = "".join(c for c, _, _ in chars)
    text = normalize_space(joined)
    if LEADER_DOTS_RE.search(text):
        text = normalize_space(LEADER_DOTS_RE.sub(" ", text))
        add_transformation(transformations, "leader_dots_removed")
    if not text:
        return None

    words: list[Word] = []
    buf: list[str] = []
    box: tuple | None = None
    start = end = None
    for c, bbox, offset in chars:
        if c.isspace():
            if buf and box:
                words.append(Word("".join(buf), box, start, end))
            buf, box = [], None
            start = end = None
        else:
            if not buf:
                start = offset
            end = offset + len(c)
            buf.append(c)
            box = bbox if box is None else union(box, bbox)
    if buf and box:
        words.append(Word("".join(buf), box, start, end))

    return Line(
        source_text="".join(source_chars),
        text=text,
        bbox=tuple(raw["bbox"]),
        size=max(s["size"] for s in raw["spans"]),
        words=words,
        trailing_space=joined != joined.rstrip(),
        transformations=transformations,
        is_light=all(
            luminance(s.get("color", 0)) > SHADOW_MAX_LUMA for s in raw["spans"]
        ),
    )


def extract_lines(page, stats: Stats) -> list[Line]:
    kept: list[Line] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block["type"] != 0:
            continue
        for raw in block["lines"]:
            line = build_line(raw, stats)
            if line is None:
                continue
            for i, prev in enumerate(kept):
                if (
                    line.text == prev.text
                    and overlap_ratio(line.bbox, prev.bbox) > DUP_OVERLAP_RATIO
                ):
                    if prev.is_light and not line.is_light:
                        kept[i] = line
                    stats.dropped_duplicates += 1
                    break
            else:
                kept.append(line)
    kept.sort(key=lambda entry: (round(entry.bbox[1], 1), entry.bbox[0]))
    return drop_shadows(kept, stats)


def drop_shadows(lines: list[Line], stats: Stats) -> list[Line]:
    """그림자 효과로 겹쳐 그린 옅은 색 줄을 버린다."""
    dark = [entry for entry in lines if not entry.is_light]
    out: list[Line] = []
    for line in lines:
        if (
            line.is_light
            and len(line.text) >= 2
            and any(
                line.text in other.text
                and len(other.text) > len(line.text)
                and y_overlap(line.bbox, other.bbox) > ROW_OVERLAP_RATIO
                and overlap_ratio(line.bbox, other.bbox) > ROW_OVERLAP_RATIO
                for other in dark
            )
        ):
            stats.dropped_shadows += 1
            continue
        out.append(line)
    return out


def cell_text(cell_lines: list[Line], stats: Stats) -> tuple[str, str]:
    """셀 안에서도 줄바꿈으로 끊긴 어절을 이어 붙인다. (정규화, 원문) 순으로 돌려준다."""
    merged = merge_wrapped(cell_lines, Stats())
    stats.merged_lines += len(cell_lines) - len(merged)
    return (
        " ".join(line.text for line in merged).strip(),
        "\n".join(line.source_text for line in cell_lines),
    )


def select_words(line: Line, indices: list[int]) -> Line:
    """어절을 투영하되 복원 공백으로 원문을 덮어쓰지 않는다."""
    indices = sorted(set(indices))
    if indices == list(range(len(line.words))):
        return replace(
            line, words=list(line.words), transformations=list(line.transformations)
        )
    groups: list[list[int]] = []
    for i in indices:
        if groups and i == groups[-1][-1] + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    chunks: list[str] = []
    words: list[Word] = []
    offset = 0
    for group in groups:
        first, last = line.words[group[0]], line.words[group[-1]]
        if first.source_start is None or last.source_end is None:
            raise ValueError("부분 셀 매핑에는 Word의 원문 offset이 필요합니다")
        start = 0 if group[0] == 0 else first.source_start
        end = (
            len(line.source_text)
            if group[-1] == len(line.words) - 1
            else last.source_end
        )
        chunk = line.source_text[start:end]
        chunks.append(chunk)
        for i in group:
            word = line.words[i]
            words.append(
                replace(
                    word,
                    source_start=word.source_start - start + offset,
                    source_end=word.source_end - start + offset,
                )
            )
        offset += len(chunk) + 1
    bbox = words[0].bbox
    for word in words[1:]:
        bbox = union(bbox, word.bbox)
    text = normalize_space(" ".join(w.text for w in words))
    if "leader_dots_removed" in line.transformations:
        text = normalize_space(LEADER_DOTS_RE.sub(" ", text))
    return replace(
        line,
        source_text="\n".join(chunks),
        text=text,
        bbox=bbox,
        words=words,
        trailing_space=line.trailing_space and indices[-1] == len(line.words) - 1,
        transformations=sorted(set(line.transformations) | {"source_words_projected"}),
    )


def table_cell_geometry(table) -> list[dict]:
    """빈 슬롯은 rows에 남기고, 실제 셀의 좌표와 병합 범위를 기록한다."""
    entries = [
        (r, c, tuple(cell))
        for r, row in enumerate(table.rows)
        for c, cell in enumerate(row.cells)
        if cell is not None
    ]
    xs = sorted({round(v, 3) for _, _, box in entries for v in (box[0], box[2])})
    ys = sorted({round(v, 3) for _, _, box in entries for v in (box[1], box[3])})
    return [
        {
            "row": r,
            "col": c,
            "bbox": list(box),
            "rowspan": max(1, ys.index(round(box[3], 3)) - ys.index(round(box[1], 3))),
            "colspan": max(1, xs.index(round(box[2], 3)) - xs.index(round(box[0], 3))),
        }
        for r, c, box in entries
    ]


def prune_grid(
    grid: list[list[str]], source: list[list[str]]
) -> tuple[list[list[str]], list[list[str]]]:
    """빈 행과 빈 열을 걷어낸다. find_tables 가 만든 유령 열이 여기서 사라진다."""
    if not grid:
        return [], []
    width = max(len(row) for row in grid)
    grid = [row + [""] * (width - len(row)) for row in grid]
    source = [row + [""] * (width - len(row)) for row in source]
    cols = [c for c in range(width) if any(row[c].strip() for row in grid)]
    rows = [r for r in range(len(grid)) if any(grid[r][c].strip() for c in cols)]
    return (
        [[grid[r][c] for c in cols] for r in rows],
        [[source[r][c] for c in cols] for r in rows],
    )


def is_ghost_grid(grid: list[list[str]]) -> bool:
    """find_tables 가 결재란 선을 페이지 전체로 늘려 만든 가짜 격자인지 본다."""
    cells = [c for row in grid for c in row]
    if not cells:
        return True
    empty = 1 - sum(1 for c in cells if c.strip()) / len(cells)
    cols = max(len(row) for row in grid)
    return empty > GHOST_EMPTY_RATIO and cols > GHOST_MIN_COLS


def approval_hits(text: str) -> int:
    return len(set(APPROVAL_FIELD_RE.findall(text)))


def heading_from_grid(grid: list[list[str]]) -> tuple[int, str, str] | None:
    """도형 안 장 제목이 1행 2칸 표로 잡힌 것을 되살린다. ['Ⅰ', '사업개요'] → 1층위."""
    if len(grid) != 1 or len(grid[0]) != 2:
        return None
    head, body = grid[0][0].strip(), grid[0][1].strip()
    m = TITLE_CELL_MARKER_RE.match(head)
    if not m or not (2 <= len(body) <= 30):
        return None
    level = 1 if m.group(1) in "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ" else 2
    return level, head, body


def _assign_table_words(
    geometry: list[dict], remaining: dict[int, Line], inside: list[int]
) -> tuple[dict[tuple[int, int], dict[int, list[int]]], dict[int, set[int]]]:
    assignments: dict[tuple[int, int], dict[int, list[int]]] = {}
    placed_words: dict[int, set[int]] = {i: set() for i in inside}

    for i in inside:
        for wi, word in enumerate(remaining[i].words):
            choices = [cell for cell in geometry if center_in(word.bbox, cell["bbox"])]
            if not choices:
                continue
            chosen = max(
                choices, key=lambda cell: overlap_ratio(word.bbox, cell["bbox"])
            )
            key = (chosen["row"], chosen["col"])
            assignments.setdefault(key, {}).setdefault(i, []).append(wi)
            placed_words[i].add(wi)
    return assignments, placed_words


def _build_table_grid(
    table,
    assignments: dict[tuple[int, int], dict[int, list[int]]],
    remaining: dict[int, Line],
    stats: Stats,
) -> tuple[list[list[str]], list[list[str]], set[str]]:
    grid: list[list[str]] = []
    source_grid: list[list[str]] = []
    transformations = {"table_cells_mapped"}
    for ri, row in enumerate(table.rows):
        cells: list[str] = []
        source_cells: list[str] = []
        for ci, cell in enumerate(row.cells):
            if cell is None:
                cells.append("")
                source_cells.append("")
                continue
            fragments: list[Line] = []
            for i, indices in assignments.get((ri, ci), {}).items():
                fragment = select_words(remaining[i], indices)
                fragments.append(fragment)
                transformations.update(fragment.transformations)
            fragments.sort(key=lambda entry: (round(entry.bbox[1], 1), entry.bbox[0]))
            text, source = cell_text(fragments, stats)
            cells.append(text)
            source_cells.append(source)
        grid.append(cells)
        source_grid.append(source_cells)

    return grid, source_grid, transformations


def dashed_table_lines(drawings, boxes):
    groups = {}
    for drawing in drawings:
        if drawing.get("type") not in ("s", "fs") or not drawing.get(
            "stroke_opacity", 1
        ):
            continue
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            a, b = item[1:3]
            dx, dy = abs(a.x - b.x), abs(a.y - b.y)
            axis = (
                0 if dy < 0.05 and dx >= 0.2 else 1 if dx < 0.05 and dy >= 0.2 else None
            )
            if axis is None or max(dx, dy) > 2:
                continue
            if not any(
                parser_layout.contains(box, (a.x, a.y), 0.5)
                and parser_layout.contains(box, (b.x, b.y), 0.5)
                for box in boxes
            ):
                continue
            fixed = round((a[1 - axis] + b[1 - axis]) / 2, 1)
            style = (
                tuple(drawing.get("color") or ()),
                round(drawing.get("width", 0), 2),
            )
            groups.setdefault((axis, fixed, style), set()).add(
                tuple(sorted((a[axis], b[axis])))
            )
    recovered = []
    for (axis, fixed, _), intervals in groups.items():
        chains = []
        for lo, hi in sorted(intervals):
            if chains and 0.05 <= lo - chains[-1][-1][1] <= 2:
                chains[-1].append((lo, hi))
            else:
                chains.append([(lo, hi)])
        for chain in chains:
            lo, hi = chain[0][0], chain[-1][1]
            coverage = sum(b - a for a, b in chain) / (hi - lo)
            if len(chain) >= 8 and hi - lo >= 12 and 0.15 <= coverage <= 0.85:
                recovered.append(
                    ((lo, fixed), (hi, fixed))
                    if axis == 0
                    else ((fixed, lo), (fixed, hi))
                )
    return recovered


def extract_tables(
    page, lines: list[Line], stats: Stats, warnings: list[dict]
) -> tuple[list[Block], list[Line]]:
    """표 블록과, 표에 흡수되지 않고 남은 본문 라인을 돌려준다."""
    try:
        found = page.find_tables().tables
        coarse = [
            t
            for t in found
            if any(c["colspan"] > 1 or c["rowspan"] > 1 for c in table_cell_geometry(t))
        ]
        recovered = set()
        if coarse:
            extra = dashed_table_lines(page.get_drawings(), [t.bbox for t in coarse])
            if extra:
                refined = page.find_tables(add_lines=extra).tables
                replacements = []
                for table in found:
                    matches = [
                        t
                        for t in refined
                        if max(abs(a - b) for a, b in zip(t.bbox, table.bbox)) < 1
                    ]
                    if len(matches) == 1 and len(matches[0].cells) > len(table.cells):
                        replacements.append(matches[0])
                        recovered.add(tuple(matches[0].bbox))
                    else:
                        replacements.append(table)
                found = replacements
    except Exception as exc:
        warnings.append(
            {
                "code": "table_detection_failed",
                "page": page.number + 1,
                "detail": str(exc),
            }
        )
        return [], lines

    blocks: list[Block] = []
    remaining = dict(enumerate(lines))
    approval_bottom = detect_approval_region(lines) if page.number == 0 else None

    for table in found:
        inside = [
            i
            for i in remaining
            if any(center_in(w.bbox, table.bbox) for w in remaining[i].words)
        ]
        if not inside:
            continue

        geometry = table_cell_geometry(table)
        assignments, placed_words = _assign_table_words(geometry, remaining, inside)
        partial = [
            i
            for i, placed in placed_words.items()
            if placed and len(placed) < len(remaining[i].words)
        ]
        cuts_approval = (
            partial and approval_bottom is not None and table.bbox[3] <= approval_bottom
        )
        if len(partial) >= 3 or cuts_approval:
            warnings.append(
                {
                    "code": "table_geometry_ambiguous",
                    "page": page.number + 1,
                    "bbox": list(table.bbox),
                    "partial_lines": len(partial),
                    "detail": "표 경계가 결재란 또는 여러 원문 줄을 가릅니다. 원문 줄로 보존했으며 표 구조 검수가 필요합니다.",
                }
            )
            continue
        grid, source_grid, transformations = _build_table_grid(
            table, assignments, remaining, stats
        )
        if tuple(table.bbox) in recovered:
            transformations.add("dashed_table_boundaries_recovered")

        compact, compact_source = prune_grid(grid, source_grid)
        if not compact:
            continue

        cover_form = (
            page.number == 0 and approval_hits("\n".join(" ".join(r) for r in grid)) > 0
        )
        if is_ghost_grid(compact) or (cover_form and is_ghost_grid(grid)):
            stats.ghost_grids += 1
            continue

        if (
            len(compact) == 1
            and partial
            and (
                (
                    len(compact[0]) == 1
                    and (
                        TITLE_CELL_MARKER_RE.fullmatch(compact[0][0].strip())
                        or any(starts_new_block(remaining[i].text) for i in partial)
                    )
                )
                or any(
                    MARKER_PATTERNS[0][2].match(remaining[i].text)
                    and 0 not in placed_words[i]
                    for i in partial
                )
            )
        ):
            continue

        size = max((remaining[i].size for i in inside), default=0.0)
        heading = heading_from_grid(compact)
        if heading:
            level, marker, text = heading
            blocks.append(
                Block(
                    kind="heading",
                    level=level,
                    marker=marker,
                    marker_type="roman" if level == 1 else "number",
                    marker_raw=compact_source[0][0].strip(),
                    marker_normalized=marker.rstrip(".") + ".",
                    source_text="\n".join(" | ".join(row) for row in source_grid),
                    text=text,
                    page=page.number + 1,
                    bbox=tuple(table.bbox),
                    size=size,
                    transformations=sorted(
                        transformations | {"heading_recovered_from_table"}
                    ),
                )
            )
            stats.table_headings += 1
        else:
            is_approval = (
                approval_hits("\n".join(" | ".join(r) for r in grid))
                >= APPROVAL_MIN_HITS
            )
            blocks.append(
                Block(
                    kind="approval" if is_approval else "table",
                    source_text="\n".join(" | ".join(row) for row in source_grid),
                    text="\n".join(" | ".join(row) for row in grid),
                    page=page.number + 1,
                    bbox=tuple(table.bbox),
                    size=size,
                    rows=grid,
                    source_rows=source_grid,
                    cells=geometry,
                    excluded_from_retrieval=is_approval,
                    transformations=sorted(transformations),
                )
            )
            if is_approval:
                stats.approval_blocks += 1
            else:
                stats.tables += 1

        for i, placed in placed_words.items():
            if not placed:
                continue
            leftover = [wi for wi in range(len(remaining[i].words)) if wi not in placed]
            if leftover:
                remaining[i] = select_words(remaining[i], leftover)
            else:
                del remaining[i]

    return blocks, [remaining[i] for i in sorted(remaining)]


def edge_position(bbox: tuple, height: float, band: float = EDGE_BAND) -> str | None:
    if bbox[3] < height * band:
        return "header"
    if bbox[1] > height * (1 - band):
        return "footer"
    return None


def detect_running_elements(
    pages: list[list[Line]], heights: list[float], stats: Stats
) -> dict[tuple[int, int], str]:
    counter: dict[str, int] = {}
    for lines, height in zip(pages, heights):
        keys = {
            DIGITS_RE.sub("#", entry.text)
            for entry in lines
            if edge_position(entry.bbox, height)
        }
        for key in keys:
            counter[key] = counter.get(key, 0) + 1

    threshold = max(2, math.ceil(len(pages) * RUNNING_HEAD_RATIO))
    result: dict[tuple[int, int], str] = {}
    for page_index, (lines, height) in enumerate(zip(pages, heights)):
        for line_index, line in enumerate(lines):
            if PAGE_NUM_DASHED_RE.match(line.text) and edge_position(
                line.bbox, height, EDGE_BAND_WIDE
            ):
                result[(page_index, line_index)] = "page_number"
                continue
            position = edge_position(line.bbox, height)
            if not position:
                continue
            if PAGE_NUM_RE.match(line.text):
                if any(
                    other is not line
                    and y_overlap(line.bbox, other.bbox) > ROW_OVERLAP_RATIO
                    for other in lines
                ):
                    continue
                result[(page_index, line_index)] = "page_number"
            elif counter.get(DIGITS_RE.sub("#", line.text), 0) >= threshold:
                result[(page_index, line_index)] = position
    stats.running_elements = len(result)
    return result


def detect_approval_region(lines: list[Line]) -> float | None:
    """표지의 기안문 결재란이 끝나는 y 를 돌려준다."""
    fields = [entry for entry in lines if APPROVAL_FIELD_RE.search(entry.text)]
    if (
        len({APPROVAL_FIELD_RE.search(entry.text).group(0) for entry in fields})
        < APPROVAL_MIN_HITS
    ):
        return None
    bottom = max(entry.bbox[3] for entry in fields)
    for line in sorted(lines, key=lambda entry: entry.bbox[1]):
        if line.bbox[1] < bottom:
            continue
        if line.bbox[1] - bottom > APPROVAL_GAP:
            break
        bottom = max(bottom, line.bbox[3])
    return bottom


def join_lines(
    group: list[Line], joiner: str, transformation: str, stats: Stats
) -> Line:
    bbox = group[0].bbox
    for line in group[1:]:
        bbox = union(bbox, line.bbox)
    stats.joined_rows += len(group) - 1
    words = []
    offset = 0
    for line in group:
        words.extend(shift_words(line.words, offset))
        offset += len(line.source_text) + len(joiner)
    return Line(
        source_text=joiner.join(entry.source_text for entry in group),
        text=joiner.join(entry.text for entry in group),
        bbox=bbox,
        size=group[0].size,
        words=words,
        trailing_space=group[-1].trailing_space,
        line_count=sum(entry.line_count for entry in group),
        transformations=sorted(
            {t for entry in group for t in entry.transformations} | {transformation}
        ),
        is_light=all(entry.is_light for entry in group),
    )


def shift_words(words: list[Word], offset: int) -> list[Word]:
    return [
        replace(
            w,
            source_start=w.source_start + offset
            if w.source_start is not None
            else None,
            source_end=w.source_end + offset if w.source_end is not None else None,
        )
        for w in words
    ]


def group_rows(lines: list[Line], stats: Stats) -> list[Line]:
    """같은 높이에 나란히 놓인 조각을 x 순서로 되돌리고, 제목 조각은 합친다."""
    rows: list[list[Line]] = []
    for line in lines:
        for row in rows:
            if y_overlap(row[0].bbox, line.bbox) > ROW_OVERLAP_RATIO:
                row.append(line)
                break
        else:
            rows.append([line])

    out: list[Line] = []
    for row in rows:
        row.sort(key=lambda entry: entry.bbox[0])

        section_no = SECTION_NO_RE.match(row[0].text) if len(row) >= 2 else None
        nearby = all(
            b.bbox[0] - a.bbox[2] <= max(a.size, b.size) * 1.5
            for a, b in zip(row, row[1:])
        )
        if len(row) > 1 and nearby and all(len(entry.text) == 1 for entry in row):
            out.append(join_lines(row, "", "visual_row_joined", stats))
        elif (
            section_no
            and len(row[1].text) > 1
            and row[1].bbox[0] - row[0].bbox[2] <= max(row[0].size, row[1].size) * 4
        ):
            number = replace(row[0], text=f"{section_no.group(1)}.")
            out.append(
                join_lines([number, row[1]], " ", "section_number_joined", stats)
            )
            out.extend(row[2:])
        else:
            out.extend(row)
    out.sort(key=lambda entry: (round(entry.bbox[1], 1), entry.bbox[0]))
    return out


def join_vertical_runs(lines: list[Line], stats: Stats) -> list[Line]:
    """한 글자씩 세로로 쌓아 놓은 라벨('의/무/기/준')을 한 낱말로 되돌린다."""
    buckets: dict[int, list[int]] = {}
    for i, line in enumerate(lines):
        if len(line.text) == 1:
            buckets.setdefault(round(line.bbox[0] / 3), []).append(i)

    replace: dict[int, Line] = {}
    drop: set[int] = set()

    def flush(run: list[int]) -> None:
        if len(run) < 2:
            return
        replace[run[0]] = join_lines(
            [lines[i] for i in run], "", "vertical_run_joined", stats
        )
        drop.update(run[1:])

    for group in buckets.values():
        group.sort(key=lambda i: lines[i].bbox[1])
        run = [group[0]]
        for prev, cur in zip(group, group[1:]):
            if (
                0
                <= lines[cur].bbox[1] - lines[prev].bbox[3]
                < lines[prev].size * WRAP_MAX_VGAP
            ):
                run.append(cur)
            else:
                flush(run)
                run = [cur]
        flush(run)

    return [replace.get(i, line) for i, line in enumerate(lines) if i not in drop]


def starts_new_block(text: str) -> bool:
    return bool(ATTACHMENT_RE.match(text)) or any(
        pattern.match(text) for _, _, pattern in MARKER_PATTERNS
    )


def continues_smaller_list_text(prev: Line, line: Line) -> bool:
    if not (
        prev.text.endswith(",")
        and any(
            pattern.match(prev.text)
            for level, _, pattern in MARKER_PATTERNS
            if level is not None and level >= 3
        )
        and prev.words
        and line.words
        and 0.5 * prev.size <= line.size < 0.85 * prev.size
    ):
        return False
    tail_height = prev.words[-1].bbox[3] - prev.words[-1].bbox[1]
    head_height = line.words[0].bbox[3] - line.words[0].bbox[1]
    return (
        0 < tail_height < 0.85 * prev.size
        and abs(tail_height - head_height) <= line.size * 0.15
    )


def merge_wrapped(
    lines: list[Line], stats: Stats, barriers: list[tuple] | None = None
) -> list[Line]:
    if not lines:
        return []
    barriers = barriers or []

    ambiguous = {
        i
        for i, a in enumerate(lines)
        if any(
            i != j
            and y_overlap(a.bbox, b.bbox) > ROW_OVERLAP_RATIO
            and (a.bbox[2] <= b.bbox[0] or b.bbox[2] <= a.bbox[0])
            for j, b in enumerate(lines)
        )
    }
    right_edge = max(line.bbox[2] for line in lines)
    paragraphs: list[Line] = []
    for i, line in enumerate(lines):
        if paragraphs:
            prev = paragraphs[-1]
            shortfall = right_edge - prev.bbox[2]
            vgap = line.bbox[1] - prev.bbox[3]
            smaller_continuation = continues_smaller_list_text(prev, line)
            blocked = any(
                box[1] < line.bbox[1]
                and box[3] > prev.bbox[3]
                and box[0] < max(prev.bbox[2], line.bbox[2])
                and box[2] > min(prev.bbox[0], line.bbox[0])
                for box in barriers
            )
            if (
                shortfall < prev.size * WRAP_MAX_SHORTFALL
                and 0 <= vgap < prev.size * WRAP_MAX_VGAP
                and abs(line.bbox[0] - prev.bbox[0]) <= prev.size * 2
                and (
                    abs(line.size - prev.size) <= prev.size * 0.15
                    or smaller_continuation
                )
                and i not in ambiguous
                and i - 1 not in ambiguous
                and not blocked
                and not starts_new_block(line.text)
            ):
                joiner = " " if prev.trailing_space or smaller_continuation else ""
                source_offset = len(prev.source_text) + 1
                prev.source_text = prev.source_text + "\n" + line.source_text
                prev.text = normalize_space(prev.text + joiner + line.text)
                prev.bbox = union(prev.bbox, line.bbox)
                prev.words = prev.words + shift_words(line.words, source_offset)
                prev.trailing_space = line.trailing_space
                prev.line_count += line.line_count
                prev.is_light = prev.is_light and line.is_light
                prev.transformations = sorted(
                    set(prev.transformations + line.transformations)
                    | {"wrapped_lines_merged"}
                )
                stats.merged_lines += 1
                continue
        paragraphs.append(
            replace(
                line, words=list(line.words), transformations=list(line.transformations)
            )
        )
    return paragraphs


def collapse_spaced(text: str) -> tuple[str, bool]:
    """'개 요', '( 경 영 기 획 과 )' 처럼 자간을 벌린 제목을 되돌린다."""
    tokens = text.split()
    if len(tokens) >= 2 and all(len(t) == 1 for t in tokens):
        return "".join(tokens), True
    return text, False


def text_left(line: Line) -> float:
    """선행 공백을 뺀 실제 글자 시작 x. 라인 bbox 는 공백 글자까지 포함한다."""
    return line.words[0].bbox[0] if line.words else line.bbox[0]


def classify(
    line: Line, body_left: float, page_no: int, forced_kind: str | None = None
) -> Block:
    indent_ta = (
        round((text_left(line) - body_left) / (line.size / 2), 1) if line.size else None
    )
    common = {
        "source_text": line.source_text,
        "page": page_no,
        "bbox": line.bbox,
        "indent_ta": indent_ta,
        "size": line.size,
        "line_count": line.line_count,
    }

    if forced_kind:
        return Block(
            kind=forced_kind,
            text=line.text,
            transformations=list(line.transformations),
            excluded_from_retrieval=True,
            **common,
        )

    for level, marker_type, pattern in MARKER_PATTERNS:
        m = pattern.match(line.text)
        if not m:
            continue
        content = line.text[m.end() :].strip()
        if not content:
            continue
        kind = (
            "note"
            if marker_type == "note"
            else ("heading" if level in HEADING_LEVELS else "item")
        )
        transformations = list(line.transformations)
        if m.group(1) in SYMBOL_MARKERS:
            add_transformation(transformations, "symbol_marker_mapped")
        if marker_type in NONSTANDARD_TYPES:
            add_transformation(transformations, "nonstandard_marker")
        if kind == "heading":
            content, collapsed = collapse_spaced(content)
            if collapsed:
                add_transformation(transformations, "spaced_title_collapsed")
        marker_raw = m.group(1)
        if marker_type == "number" and "section_number_joined" in transformations:
            original = re.match(r"\s*(\d{1,2}\.?)", line.source_text)
            if original:
                marker_raw = original.group(1)
        return Block(
            kind=kind,
            text=content,
            level=level,
            marker=marker_raw,
            marker_type=marker_type,
            marker_raw=marker_raw,
            marker_normalized=m.group(1),
            transformations=sorted(set(transformations)),
            **common,
        )

    m = ATTACHMENT_RE.match(line.text)
    if m:
        base = re.sub(r"\s+", "", m.group(1))
        marker = f"{base} {m.group(2)}" if m.group(2) else base
        original = ATTACHMENT_RE.match(line.source_text.lstrip())
        marker_raw = original.group(0).rstrip() if original else m.group(0).rstrip()
        return Block(
            kind="attachment",
            text=line.text[m.end() :].strip(),
            level=1,
            marker=marker_raw,
            marker_raw=marker_raw,
            marker_normalized=marker,
            marker_type="attachment",
            transformations=sorted(set(line.transformations)),
            **common,
        )

    text, collapsed = (
        collapse_spaced(line.text) if len(line.text) < 30 else (line.text, False)
    )
    transformations = list(line.transformations)
    if collapsed:
        add_transformation(transformations, "spaced_title_collapsed")
    return Block(
        kind="para", text=text, transformations=sorted(set(transformations)), **common
    )


def diagnose_empty_page(page) -> dict:
    """글자가 하나도 안 나온 페이지의 원인을 가른다."""
    rect = page.rect
    covering = [
        r
        for image in page.get_images(full=True)
        for r in page.get_image_rects(image[0])
        if r.width > rect.width * SCAN_COVER_RATIO
        and r.height > rect.height * SCAN_COVER_RATIO
    ]
    if covering:
        return {
            "code": "scanned_page",
            "detail": "페이지 전체가 이미지입니다. 텍스트를 얻으려면 OCR 이 필요합니다.",
        }
    if not page.get_fonts() and len(page.get_drawings()) >= OUTLINE_MIN_DRAWINGS:
        return {
            "code": "outlined_text",
            "detail": "글자가 벡터 곡선으로 변환되어 문자 정보가 없습니다(글꼴 0개). "
            "화면에는 글자로 보이지만 추출·검색이 불가능하며, 렌더링 후 OCR 이 필요합니다.",
        }
    return {"code": "blank_page", "detail": "내용이 없는 페이지입니다."}


def count_vector_shapes(page, tables: list[Block]) -> int:
    """표 괘선을 뺀 '면 도형'의 개수. 순서도·개념도가 있는 페이지를 가린다."""
    boxes = [t.bbox for t in tables]
    count = 0
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.width < VECTOR_SHAPE_MIN_SIDE or rect.height < VECTOR_SHAPE_MIN_SIDE:
            continue
        if any(overlap_ratio(tuple(rect), box) > 0.9 for box in boxes):
            continue
        count += 1
    return count


def mark_table_underlines(page, tables):
    from bisect import bisect_left, bisect_right

    tables = [t for t in tables if t.kind == "table" and t.cells]
    if not tables:
        return
    segments = set()
    for drawing in page.get_drawings():
        if (
            drawing.get("type") not in {"s", "fs"}
            or drawing.get("stroke_opacity", 1) <= 0
        ):
            continue
        if drawing.get("width", 0) > 2:
            continue
        for item in drawing["items"]:
            if item[0] == "l" and abs(item[1].y - item[2].y) < 0.1:
                x0, x1 = sorted((item[1].x, item[2].x))
                if x1 - x0 >= 3:
                    segments.add((x0, item[1].y, x1))
    if not segments:
        return
    glyphs = []
    line_id = 0
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span.get("chars", []):
                    if char["c"].strip():
                        glyphs.append((char["bbox"][3], line_id, char))
            line_id += 1
    glyphs.sort(key=lambda c: c[0])
    bottoms = [c[0] for c in glyphs]
    candidates = []
    for x0, y, x1 in sorted(segments):
        groups = {}
        for _, line_id, char in glyphs[
            bisect_left(bottoms, y - 1.5) : bisect_right(bottoms, y + 3)
        ]:
            a, _, c, _ = char["bbox"]
            if min(c, x1) - max(a, x0) >= (c - a) * 0.7:
                groups.setdefault(line_id, []).append(char)
        for chars in groups.values():
            chars.sort(key=lambda c: c["bbox"][0])
            if (
                abs(chars[0]["bbox"][0] - x0) <= 3
                and abs(chars[-1]["bbox"][2] - x1) <= 3
            ):
                candidates.append((x0, y, x1, chars))
    for table in tables:
        for cell in table.cells:
            box = cell["bbox"]
            value = table.rows[cell["row"]][cell["col"]]
            positions = [i for i, char in enumerate(value) if not char.isspace()]
            compact = "".join(value[i] for i in positions)
            ranges = set()
            for x0, y, x1, chars in candidates:
                if not (box[0] <= x0 < x1 <= box[2] and box[1] + 2 < y < box[3] - 2):
                    continue
                if not all(center_in(c["bbox"], box) for c in chars):
                    continue
                quote = "".join(c["c"] for c in chars)
                if compact.count(quote) != 1:
                    continue
                start = compact.index(quote)
                ranges.add((positions[start], positions[start + len(quote) - 1] + 1))
            if ranges:
                cell["decorations"] = [
                    {"kind": "underline", "start": a, "end": b, "text": value[a:b]}
                    for a, b in sorted(ranges)
                ]


def mark_visual_tables(page, tables, figures, warnings):
    drawings = page.get_drawings()
    for table in tables:
        if table.kind != "table":
            continue
        images = any(overlap_ratio(f.bbox, table.bbox) > 0.9 for f in figures)
        colors = set()
        for drawing in drawings:
            rect = drawing["rect"]
            fill = drawing.get("fill")
            if (
                fill is not None
                and drawing.get("fill_opacity", 1) > 0
                and len(fill) >= 3
                and max(fill) - min(fill) > 0.08
                and rect.width >= MIN_FIGURE_SIZE
                and rect.height >= MIN_FIGURE_SIZE
                and overlap_ratio(tuple(rect), table.bbox) > 0.9
            ):
                colors.add(tuple(round(c, 2) for c in fill))
        if images or len(colors) >= 3:
            table.requires_vision = True
            warnings.append(
                {
                    "code": "table_visual_content",
                    "page": page.number + 1,
                    "bbox": list(table.bbox),
                    "detail": "표 안에 이미지 또는 다색 도형이 있습니다. 텍스트·셀 추출만으로 시각 정보를 복원할 수 없어 원문 검수가 필요합니다.",
                }
            )


def extract_figures(page, stats: Stats) -> list[Block]:
    """삽입 이미지를 자리만 잡아 둔다. 내용 판독은 비전 단계로 넘긴다."""
    figures: list[Block] = []
    seen: set[tuple] = set()
    page_area = page.rect.width * page.rect.height
    for image in page.get_images(full=True):
        try:
            rects = page.get_image_rects(image[0])
        except Exception:
            continue
        for rect in rects:
            key = tuple(round(v, 1) for v in tuple(rect))
            if (
                key in seen
                or rect.width < MIN_FIGURE_SIZE
                or rect.height < MIN_FIGURE_SIZE
            ):
                continue
            seen.add(key)
            ratio = (rect.width * rect.height) / page_area if page_area else 0.0
            worth_vision = ratio >= FIGURE_VISION_MIN_AREA
            figures.append(
                Block(
                    kind="figure",
                    source_text="",
                    text="",
                    page=page.number + 1,
                    bbox=tuple(rect),
                    excluded_from_retrieval=True,
                    requires_vision=worth_vision,
                    transformations=["embedded_image_detected"],
                )
            )
            if worth_vision:
                stats.vision_figures += 1
    stats.figures += len(figures)
    return figures


def assign_hierarchy(blocks: list[Block], document_id: str) -> None:
    """부호 층위로 parent_id 를, 장 제목(1~2층위)으로 section_path 를 채운다."""
    for i, block in enumerate(blocks, start=1):
        block.id = f"{document_id}_b{i:04d}"

    stack: dict[int, Block] = {}
    for block in blocks:
        if block.kind in EXCLUDED_KINDS:
            continue
        if block.kind in STACKED_KINDS and block.level:
            for level in [entry for entry in stack if entry >= block.level]:
                del stack[level]
            ancestors = [entry for entry in stack if entry < block.level]
            block.parent_id = stack[max(ancestors)].id if ancestors else None
            stack[block.level] = block
        elif stack:
            block.parent_id = stack[max(stack)].id
        block.section_path = [
            f"{item.marker} {item.text}".strip()
            for level, item in sorted(stack.items())
            if level in HEADING_LEVELS and item is not block
        ]


def build_sections(blocks: list[Block], document_id: str) -> list[Section]:
    """문서의 장(章)마다 section_id 를 발급하고 범위를 묶는다."""
    children: dict[str, list[Block]] = {}
    for block in blocks:
        if block.parent_id:
            children.setdefault(block.parent_id, []).append(block)

    def looks_like_head(block: Block) -> bool:
        if block.kind == "attachment":
            return True
        text = re.sub(r"\s+", "", block.text)
        if not text or len(text) > SECTION_HEAD_MAX_CHARS:
            return False
        return not SENTENCE_ENDING.search(text)

    index = {b.id: b for b in blocks}
    candidates = {
        b.id
        for b in blocks
        if b.kind in STACKED_KINDS
        and b.level
        and children.get(b.id)
        and looks_like_head(b)
    }

    def enclosing(block: Block) -> str | None:
        """자신을 뺀, 가장 가까운 조상 후보의 블록 id."""
        parent_id = block.parent_id
        for _ in range(MAX_SECTION_DEPTH):
            if not parent_id:
                return None
            if parent_id in candidates:
                return parent_id
            parent = index.get(parent_id)
            parent_id = parent.parent_id if parent else None
        return None

    heads = [b for b in blocks if b.id in candidates and enclosing(b) is None]
    id_of = {b.id: f"{document_id}_s{i:03d}" for i, b in enumerate(heads, start=1)}

    sections = {
        b.id: Section(
            section_id=id_of[b.id],
            head_block_id=b.id,
            head_text=b.text,
            marker=b.marker,
            level=b.level,
            child_block_ids=[c.id for c in children.get(b.id, [])],
        )
        for b in heads
    }

    def owning_head(block: Block) -> str | None:
        """이 블록이 속한 장의 머리. 자기 자신이 장이면 자기 자신."""
        current = block
        for _ in range(MAX_SECTION_DEPTH):
            if current.id in sections:
                return current.id
            parent = index.get(current.parent_id or "")
            if not parent:
                return None
            current = parent
        return None

    pages: dict[str, list[int]] = {}
    for block in blocks:
        if block.kind in EXCLUDED_KINDS:
            continue
        owner = owning_head(block)
        if not owner:
            continue
        block.section_id = id_of[owner]
        sections[owner].block_ids.append(block.id)
        pages.setdefault(owner, []).append(block.page)

    for head_id, section in sections.items():
        seen = pages.get(head_id) or [0]
        section.page_range = (min(seen), max(seen))
    return list(sections.values())


def mark_document_title(blocks: list[Block], page_height: float) -> None:
    """첫 페이지 위쪽에서 본문보다 확연히 크게 찍힌 줄을 표지 제목으로 본다."""

    body_sizes = [b.size for b in blocks if b.size and b.text]
    if not body_sizes:
        return
    floor = statistics.median(body_sizes) * TITLE_SIZE_RATIO
    candidates = [
        b
        for b in blocks
        if b.page == 1
        and b.kind == "para"
        and b.bbox[1] < page_height * TITLE_TOP_RATIO
        and TITLE_MIN_CHARS <= len(b.text) <= TITLE_MAX_CHARS
        and b.size >= floor
        and not ORG_NAME_RE.match(b.text)
    ]
    if not candidates:
        return
    title = max(candidates, key=lambda b: (round(b.size, 1), len(b.text)))
    title.kind = "document_title"

    order = next(i for i, b in enumerate(blocks) if b is title)
    ids = {id(b) for b in candidates}
    for step in (-1, 1):
        i = order + step
        if not 0 <= i < len(blocks):
            continue
        other = blocks[i]
        short_continuation = (
            other.kind == "para"
            and other.page == title.page
            and 2 <= len(other.text) < TITLE_MIN_CHARS
            and abs(other.size - title.size) < 0.5
            and abs(
                (other.bbox[0] + other.bbox[2]) / 2
                - (title.bbox[0] + title.bbox[2]) / 2
            )
            <= title.size
        )
        if id(other) not in ids and not short_continuation:
            continue
        gap = (
            other.bbox[1] - title.bbox[3] if step > 0 else title.bbox[1] - other.bbox[3]
        )
        if gap < 0 or gap > title.size * WRAP_MAX_VGAP:
            continue
        head, tail = (title, other) if step > 0 else (other, title)
        title.text = f"{head.text} {tail.text}"
        title.source_text = f"{head.source_text}\n{tail.source_text}"
        title.bbox = union(title.bbox, other.bbox)
        title.line_count += other.line_count
        add_transformation(title.transformations, "title_lines_joined")
        other.kind = "title_fragment"
        other.excluded_from_retrieval = True
        break


def recover_decorative_headings(
    page, lines: list[Line]
) -> tuple[list[Block], list[Line]]:
    recovered = []
    consumed = set()
    drawings = page.get_drawings()
    for drawing in drawings:
        box = drawing["rect"]
        if not drawing.get("fill") or not (
            15 <= box.width <= 45 and 20 <= box.height <= 65
        ):
            continue
        markers = [
            line
            for line in lines
            if id(line) not in consumed
            and line.is_light
            and SECTION_NO_RE.fullmatch(line.text)
            and center_in(line.bbox, tuple(box))
        ]
        if len(markers) != 1:
            continue
        marker = markers[0]
        fragments = [
            line
            for line in lines
            if id(line) not in consumed
            and not line.is_light
            and abs(line.size - marker.size) < 0.5
            and box.x1 < text_left(line) < box.x1 + marker.size * 3
            and box.y0 - 2 <= line.bbox[1]
            and line.bbox[3] <= box.y1 + 2
            and re.fullmatch(r"[가-힣\s]+", line.text)
        ]
        if not 1 <= len(fragments) <= 3:
            continue
        if not any(
            stroke.get("type") == "s"
            and stroke.get("color") == drawing.get("fill")
            and stroke["rect"].height < 1
            and abs(stroke["rect"].y0 - box.y1) < 3
            and box.x1 < stroke["rect"].x0 <= min(text_left(line) for line in fragments)
            and stroke["rect"].x1 >= max(line.bbox[2] for line in fragments) - 1
            for stroke in drawings
        ):
            continue
        fragments.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
        text = "".join(re.sub(r"\s+", "", line.text) for line in fragments)
        if not 2 <= len(text) <= 20:
            continue
        members = [marker, *fragments]
        combined = tuple(parser_layout.bounds([line.bbox for line in members]))
        if any(
            id(line) not in {id(x) for x in members} and center_in(line.bbox, combined)
            for line in lines
        ):
            continue
        consumed.update(id(line) for line in members)
        recovered.append(
            Block(
                kind="heading",
                source_text="\n".join(line.source_text for line in members),
                text=text,
                page=page.number + 1,
                bbox=combined,
                level=2,
                marker=marker.text,
                marker_raw=marker.text,
                marker_normalized=marker.text.rstrip(".") + ".",
                marker_type="number",
                size=marker.size,
                line_count=len(members),
                transformations=["decorative_heading_recovered"],
                source_fragments=[
                    {"text": line.source_text, "bbox": list(line.bbox)}
                    for line in members
                ],
            )
        )
    return recovered, [line for line in lines if id(line) not in consumed]


def _extract_document_pages(doc, stats: Stats, warnings: list[dict]) -> DocumentPages:
    pages_meta: list[dict[str, Any]] = []
    raw_pages: list[str] = []
    page_lines: list[list[Line]] = []
    page_tables: list[list[Block]] = []
    page_figures: list[list[Block]] = []
    heights: list[float] = []
    layouts: list[list[dict]] = []

    try:
        for page in doc:
            raw_pages.append(page.get_text())
            meta = {
                "page": page.number + 1,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
            }
            pages_meta.append(meta)
            heights.append(page.rect.height)
            lines = extract_lines(page, stats)
            try:
                regions = parser_layout.inspect_page(
                    page, detect_approval_region(lines) if page.number == 0 else None
                )
            except Exception as exc:
                regions = []
                warnings.append(
                    {
                        "code": "layout_detection_failed",
                        "page": page.number + 1,
                        "detail": str(exc),
                    }
                )
                meta["requires_vision"] = True
            layouts.append(regions)
            if any(r["kind"] == "diagram" for r in regions):
                warnings.append(
                    {
                        "code": "diagram_requires_review",
                        "page": page.number + 1,
                        "detail": "상자와 텍스트 기반 관계입니다. 그림 화살표·의미 관계는 검수가 필요합니다.",
                    }
                )
                meta["requires_vision"] = True
            tables, body = extract_tables(page, lines, stats, warnings)
            mark_table_underlines(page, tables)
            headings, body = recover_decorative_headings(page, body)
            tables.extend(headings)
            figures = extract_figures(page, stats)
            mark_visual_tables(page, tables, figures, warnings)
            if any(t.requires_vision for t in tables):
                meta["requires_vision"] = True
            if any(
                w["code"] == "table_geometry_ambiguous" and w["page"] == page.number + 1
                for w in warnings
            ):
                meta["requires_vision"] = True

            shapes = count_vector_shapes(page, tables)
            if shapes >= VECTOR_SHAPE_MIN:
                meta["vector_shapes"] = shapes
                meta["requires_vision"] = True
                stats.vector_pages += 1
            page_lines.append(body)
            page_tables.append(tables)
            page_figures.append(figures)
            if not body and not tables:
                warnings.append({"page": page.number + 1, **diagnose_empty_page(page)})
    finally:
        doc.close()

    return DocumentPages(
        pages_meta, raw_pages, page_lines, page_tables, page_figures, heights, layouts
    )


def _assemble_blocks(pages: DocumentPages, stats: Stats) -> list[Block]:
    tagged = detect_running_elements(pages.lines, pages.heights, stats)

    if pages.lines:
        bottom = detect_approval_region(pages.lines[0])
        if bottom is not None:
            for i, line in enumerate(pages.lines[0]):
                if line.bbox[3] <= bottom and (0, i) not in tagged:
                    tagged[(0, i)] = "approval"
                    stats.approval_blocks += 1

    blocks: list[Block] = []
    for page_index, lines in enumerate(pages.lines):
        page_no = page_index + 1
        body = [line for i, line in enumerate(lines) if (page_index, i) not in tagged]
        body_left = min((text_left(line) for line in body), default=0.0)

        barriers = [
            b.bbox for b in pages.tables[page_index] + pages.figures[page_index]
        ]
        barriers.extend(lines[i].bbox for (p, i) in tagged if p == page_index)
        flow = merge_wrapped(
            join_vertical_runs(group_rows(body, stats), stats), stats, barriers
        )
        page_blocks = [classify(line, body_left, page_no) for line in flow]
        page_blocks += [
            classify(lines[i], body_left, page_no, forced_kind=kind)
            for (p, i), kind in tagged.items()
            if p == page_index
        ]
        page_blocks += pages.tables[page_index]
        page_blocks += pages.figures[page_index]
        page_blocks.sort(key=lambda b: (round(b.bbox[1], 1), b.bbox[0], b.kind))
        blocks.extend(page_blocks)

    return blocks


def recover_approval_blocks(
    blocks: list[Block], layouts: list[list[dict]]
) -> list[Block]:
    for page_no, regions in enumerate(layouts, 1):
        for region in regions:
            if region["kind"] != "approval_grid":
                continue
            box = region["bbox"]
            members = [
                b
                for b in blocks
                if b.page == page_no
                and parser_layout.contains(box, parser_layout.center(b.bbox))
            ]
            if not members or any(
                b.kind not in ("approval", "table")
                or not parser_layout.contains(box, (b.bbox[0], b.bbox[1]), 1)
                or not parser_layout.contains(box, (b.bbox[2], b.bbox[3]), 1)
                for b in members
            ):
                for block in blocks:
                    if (
                        block.page == page_no
                        and block.kind == "table"
                        and parser_layout.contains(block.bbox, (box[0], box[1]), 1)
                        and parser_layout.contains(block.bbox, (box[2], box[3]), 1)
                    ):
                        block.kind = "approval"
                        block.excluded_from_retrieval = True
                        add_transformation(
                            block.transformations, "approval_region_contains_grid"
                        )
                region["status"] = "needs_review"
                continue
            xs = sorted({n["bbox"][i] for n in region["nodes"] for i in (0, 2)})
            ys = sorted({n["bbox"][i] for n in region["nodes"] for i in (1, 3)})
            rows = [[""] * (len(xs) - 1) for _ in range(len(ys) - 1)]
            cells = []
            for node in region["nodes"]:
                x0, y0, x1, y1 = node["bbox"]
                row, col = ys.index(y0), xs.index(x0)
                rows[row][col] = node["text"]
                cells.append(
                    {
                        "row": row,
                        "col": col,
                        "bbox": node["bbox"],
                        "rowspan": ys.index(y1) - row,
                        "colspan": xs.index(x1) - col,
                    }
                )
            source = "\n".join(" | ".join(row) for row in rows)
            replacement = Block(
                kind="approval",
                source_text=source,
                text="\n".join(" | ".join(row) for row in rows),
                page=page_no,
                bbox=tuple(box),
                rows=rows,
                source_rows=[row[:] for row in rows],
                cells=cells,
                excluded_from_retrieval=True,
                transformations=["approval_cells_from_glyph_geometry"],
                source_fragments=[
                    {"text": b.source_text, "bbox": list(b.bbox)} for b in members
                ],
            )
            member_ids = {id(b) for b in members}
            blocks = [b for b in blocks if id(b) not in member_ids] + [replacement]
    return sorted(
        blocks, key=lambda b: (b.page, round(b.bbox[1], 1), b.bbox[0], b.kind)
    )


def implementation_hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), Path(parser_layout.__file__))
    }


def parse_pdf(pdf_path: Path) -> tuple[dict[str, Any], str, str]:
    started = time.perf_counter()
    stats = Stats()
    warnings: list[dict] = []
    digest = hashlib.sha256()
    with pdf_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    sha256 = digest.hexdigest()
    document_id = f"doc_{sha256[:16]}"

    doc = pymupdf.open(pdf_path)
    pages = _extract_document_pages(doc, stats, warnings)
    blocks = _assemble_blocks(pages, stats)
    blocks = recover_approval_blocks(blocks, pages.layouts)
    stats.tables = sum(b.kind == "table" for b in blocks)
    stats.approval_blocks = sum(b.kind == "approval" for b in blocks)

    stats.attachments = sum(1 for b in blocks if b.kind == "attachment")
    if blocks:
        mark_document_title(blocks, pages.heights[0])
    assign_hierarchy(blocks, document_id)
    layout_regions = parser_layout.attach_regions(blocks, pages.layouts, document_id)
    sections = build_sections(blocks, document_id)

    stats.sections = len(sections)
    stats.pages = len(pages.metadata)
    stats.elapsed_seconds = round(time.perf_counter() - started, 3)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "document": {
            "document_id": document_id,
            "filename": pdf_path.name,
            "sha256": sha256,
            "page_count": len(pages.metadata),
        },
        "parser": {
            "name": PARSER_NAME,
            "source_hashes": implementation_hashes(),
            "pymupdf_version": getattr(pymupdf, "__version__", "unknown"),
            "settings": {
                "space_gap_ratio": SPACE_GAP_RATIO,
                "wrap_max_shortfall": WRAP_MAX_SHORTFALL,
                "wrap_max_vgap": WRAP_MAX_VGAP,
                "edge_band": EDGE_BAND,
                "ghost_empty_ratio": GHOST_EMPTY_RATIO,
                "ghost_min_cols": GHOST_MIN_COLS,
                "figure_vision_min_area": FIGURE_VISION_MIN_AREA,
                "vector_shape_min": VECTOR_SHAPE_MIN,
            },
            "stats": stats.__dict__,
        },
        "pages": pages.metadata,
        "sections": [section_to_dict(s) for s in sections],
        "blocks": [block_to_dict(b) for b in blocks],
        "layout_regions": layout_regions,
        "warnings": warnings,
    }
    return (
        payload,
        to_markdown(pdf_path.stem, blocks, layout_regions),
        "\n".join(pages.raw_text),
    )


def block_to_dict(block: Block) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": block.id,
        "page": block.page,
        "kind": block.kind,
        "text": block.text,
        "source_text": block.source_text,
        "bbox": [round(v, 1) for v in block.bbox],
        "line_count": block.line_count,
    }
    optional = {
        "level": block.level,
        "marker": block.marker,
        "marker_raw": block.marker_raw,
        "marker_normalized": block.marker_normalized,
        "marker_type": block.marker_type,
        "indent_ta": block.indent_ta,
        "parent_id": block.parent_id,
        "section_id": block.section_id,
        "section_path": block.section_path,
        "transformations": block.transformations,
        "rows": block.rows,
        "source_rows": block.source_rows,
        "cells": block.cells,
        "layout_node_id": block.layout_node_id,
        "source_fragments": block.source_fragments,
    }
    data.update({k: v for k, v in optional.items() if v not in (None, [], "")})
    if block.excluded_from_retrieval:
        data["excluded_from_retrieval"] = True
    if block.requires_vision:
        data["requires_vision"] = True
    return data


def section_to_dict(section: Section) -> dict[str, Any]:
    return {
        "section_id": section.section_id,
        "head_block_id": section.head_block_id,
        "head_text": section.head_text,
        "marker": section.marker,
        "level": section.level,
        "child_block_ids": section.child_block_ids,
        "block_ids": section.block_ids,
        "page_range": list(section.page_range),
    }


def table_md(rows: list[list[str]], cells=()) -> list[str]:
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    decorations = {(c["row"], c["col"]): c.get("decorations", []) for c in cells}

    def cell(c, r, col):
        spans = decorations.get((r, col), [])
        if spans:
            parts, offset = [], 0
            for span in spans:
                a, b = span["start"], span["end"]
                if span["kind"] != "underline" or a < offset or c[a:b] != span["text"]:
                    continue
                parts.extend(
                    [html.escape(c[offset:a]), "<u>" + html.escape(c[a:b]) + "</u>"]
                )
                offset = b
            c = "".join(parts) + html.escape(c[offset:])
        return c.replace("|", "\\|").replace("\n", "<br>") or " "

    return [
        "| " + " | ".join(cell(c, 0, i) for i, c in enumerate(rows[0])) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
        *(
            "| " + " | ".join(cell(c, r, i) for i, c in enumerate(row)) + " |"
            for r, row in enumerate(rows[1:], 1)
        ),
    ]


def to_markdown(title: str, blocks: list[Block], layout_regions=()) -> str:
    out = [f"# {title}", ""]
    page = 0
    index = {b.id: b for b in blocks}
    diagrams = {}
    for region in layout_regions:
        if region["kind"] != "diagram":
            continue
        for node in region["nodes"]:
            for bid in node["block_ids"] + node["detail_block_ids"]:
                diagrams[bid] = region
        for b in blocks:
            if (
                b.page == region["page"]
                and b.text.strip() in "▶→➜►➔▷◀←◄"
                and b.text.strip()
                and parser_layout.contains(region["bbox"], parser_layout.center(b.bbox))
            ):
                diagrams[b.id] = region
    rendered = set()
    for block in blocks:
        if block.kind in EXCLUDED_KINDS:
            continue
        if block.page != page:
            page = block.page
            out += ["", f"<!-- page {page} -->", ""]
        if block.id in diagrams:
            region = diagrams[block.id]
            if region["id"] not in rendered:
                rendered.add(region["id"])
                out += ["", "**도식 — 좌표 기반 복원, 관계 검수 필요**", ""]
                for node in region["nodes"]:
                    out.append("- " + node["text"].replace("\n", " · "))
                    for bid in node["detail_block_ids"]:
                        out.append("  - " + index[bid].text.replace("\n", " "))
                order = {n["id"]: str(i + 1) for i, n in enumerate(region["nodes"])}
                directed = [
                    f"{order[e['source']]} → {order[e['target']]}"
                    for e in region["edges"]
                    if e["type"] == "directed"
                ]
                if directed:
                    out.append("\n확인된 화살표: " + ", ".join(directed))
                else:
                    out.append("\n단계는 화면상 순서이며 화살표 방향은 미확정입니다.")
                out.append("")
            continue
        if block.kind == "table":
            out += table_md(block.rows, block.cells) + [""]
        elif block.kind == "document_title":
            out += [f"**{block.text}**", ""]
        elif block.kind == "attachment":
            out += ["", f"**{block.marker}** {block.text}".rstrip(), ""]
        elif block.kind == "heading":
            out += [f"{'#' * ((block.level or 1) + 1)} {block.marker} {block.text}", ""]
        elif block.kind in ("item", "note"):
            out.append(
                f"{'  ' * max((block.level or 5) - 3, 0)}{block.marker} {block.text}"
            )
        else:
            out.append(block.text)
    return "\n".join(out).rstrip() + "\n"


def atomic_write(path: Path, text: str) -> None:
    """중단되어도 파일 하나가 잘린 상태로 남지 않도록 교체한다."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".parser-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_outputs(pdf_path: Path, out_dir: Path) -> dict[str, Any]:
    payload, markdown, raw_text = parse_pdf(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem
    atomic_write(out_dir / f"{stem}.md", markdown)
    atomic_write(out_dir / f"{stem}.raw.txt", raw_text)
    atomic_write(
        out_dir / f"{stem}.json", json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return payload


def summarize(payload: dict[str, Any]) -> str:
    stats = payload["parser"]["stats"]
    kinds: dict[str, int] = {}
    for block in payload["blocks"]:
        kinds[block["kind"]] = kinds.get(block["kind"], 0) + 1
    line = (
        f"{stats['pages']}p 블록{len(payload['blocks'])} "
        f"({' '.join(f'{k}{v}' for k, v in sorted(kinds.items()))}) | "
        f"공백+{stats['restored_spaces']} 병합{stats['merged_lines']} 행묶음{stats['joined_rows']} "
        f"중복-{stats['dropped_duplicates']} 여백{stats['running_elements']} "
        f"유령격자-{stats['ghost_grids']} 결재란{stats['approval_blocks']} 표제목+{stats['table_headings']} "
        f"그림자-{stats['dropped_shadows']} 붙임{stats['attachments']} "
        f"비전 그림{stats['vision_figures']}/{stats['figures']} 도형쪽{stats['vector_pages']} "
        f"| {stats['elapsed_seconds']:.2f}s"
    )
    if payload["warnings"]:
        line += f" | 경고{len(payload['warnings'])}"
    return line


def collect_pdfs(target: Path, pattern: str) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.rglob(pattern))
    raise FileNotFoundError(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="공공보고서 PDF 파서")
    parser.add_argument(
        "input",
        type=Path,
        help="PDF 파일 또는 폴더",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=OUTPUT_DIR, help="출력 폴더"
    )
    parser.add_argument("--glob", default="*.pdf", help="폴더 입력 시 파일 패턴")
    args = parser.parse_args()

    try:
        pdfs = collect_pdfs(args.input, args.glob)
    except FileNotFoundError as exc:
        print(f"경로를 찾을 수 없습니다: {exc}", file=sys.stderr)
        return 1
    if not pdfs:
        print("처리할 PDF가 없습니다.", file=sys.stderr)
        return 1
    failures = 0
    for pdf_path in pdfs:
        out_dir = (
            args.output / pdf_path.parent.relative_to(args.input)
            if args.input.is_dir()
            else args.output
        )
        try:
            payload = write_outputs(pdf_path, out_dir)
            print(f"{pdf_path.name}: {summarize(payload)}")
        except Exception as exc:
            failures += 1
            print(f"[실패] {pdf_path}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
