from pathlib import Path
from typing import Annotated, ClassVar, Final

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from bareum_ai.pdf_parsing.api.responses import ParsedPdf
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.table_validation import validate_table
from bareum_ai.pdf_parsing.parser.worker_contract import ParserInput
from bareum_ai.pdf_parsing.types import ExtractionQuality, FailureDiagnostic


class _Projection(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class _Document(_Projection):
    page_count: Annotated[int, Field(strict=True, gt=0)]
    sha256: str


class _Page(_Projection):
    page: Annotated[int, Field(strict=True, gt=0)]
    width: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    height: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    requires_vision: bool = False


class _Block(_Projection):
    id: Annotated[str, Field(min_length=1)]
    page: Annotated[int, Field(strict=True, gt=0)]
    kind: str
    text: str
    source_text: str
    bbox: tuple[float, float, float, float]
    parent_id: str | None = None
    section_id: str | None = None
    layout_node_id: str | None = None
    excluded_from_retrieval: bool = False
    requires_vision: bool = False


class _Section(_Projection):
    section_id: str
    head_block_id: str
    block_ids: tuple[str, ...]
    child_block_ids: tuple[str, ...]


class _Warning(_Projection):
    code: Annotated[str, Field(min_length=1)]


class _LayoutNode(_Projection):
    id: str
    block_ids: tuple[str, ...]
    detail_block_ids: tuple[str, ...]


class _LayoutEdge(_Projection):
    source: str
    target: str


class _LayoutRegion(_Projection):
    id: str
    page: int
    nodes: tuple[_LayoutNode, ...]
    edges: tuple[_LayoutEdge, ...]


_PAGES: Final = TypeAdapter(tuple[_Page, ...])
_BLOCKS: Final = TypeAdapter(tuple[_Block, ...])
_SECTIONS: Final = TypeAdapter(tuple[_Section, ...])
_WARNINGS: Final = TypeAdapter(tuple[_Warning, ...])
_REGIONS: Final = TypeAdapter(tuple[_LayoutRegion, ...])
_INFORMATIONAL_WARNINGS: Final = frozenset({"blank_page"})


def validate_pdf(path: Path, page_limit: int) -> int:
    try:
        with pymupdf.open(path) as document:
            if not document.is_pdf:
                raise PdfParseApiError(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
            if document.needs_pass:
                raise PdfParseApiError(ErrorCode.PDF_ENCRYPTED)
            page_count = document.page_count
            if page_count == 0:
                raise PdfParseApiError(ErrorCode.PDF_CORRUPTED)
            if page_count > page_limit:
                raise PdfParseApiError(ErrorCode.PAGE_LIMIT_EXCEEDED)
            # 페이지 트리까지 읽어 열기만 성공한 손상 PDF를 거른다.
            for page_index in range(page_count):
                document.load_page(page_index)
            return page_count
    except pymupdf.FileDataError, RuntimeError:
        with path.open("rb") as source:
            header = source.read(1024)
        code = (
            ErrorCode.PDF_CORRUPTED
            if b"%PDF-" in header
            else ErrorCode.UNSUPPORTED_MEDIA_TYPE
        )
        raise PdfParseApiError(code) from None


def validate_extraction(parsed: ParsedPdf, job: ParserInput) -> ExtractionQuality:
    document = _Document.model_validate(parsed.document)
    pages = _PAGES.validate_python(parsed.pages)
    blocks = _BLOCKS.validate_python(parsed.blocks)
    sections = _SECTIONS.validate_python(parsed.sections)
    warnings = _WARNINGS.validate_python(parsed.warnings)
    regions = _REGIONS.validate_python(parsed.layout_regions)
    tables = (block for block in parsed.blocks if block.get("kind") == "table")
    for table in tables:
        validate_table(table)
    page_ids = {page.page for page in pages}
    block_ids = {block.id for block in blocks}
    section_ids = {section.section_id for section in sections}
    node_ids = {node.id for region in regions for node in region.nodes}
    if (
        document.sha256 != job.sha256
        or document.page_count != len(pages)
        or page_ids != set(range(1, len(pages) + 1))
        or len(block_ids) != len(blocks)
        or len(section_ids) != len(sections)
        or any(block.page not in page_ids for block in blocks)
        or any(block.parent_id and block.parent_id not in block_ids for block in blocks)
        or any(
            block.section_id and block.section_id not in section_ids for block in blocks
        )
        or any(
            block.layout_node_id and block.layout_node_id not in node_ids
            for block in blocks
        )
        or any(region.page not in page_ids for region in regions)
        or len({region.id for region in regions}) != len(regions)
        or len(node_ids) != sum(len(region.nodes) for region in regions)
        or any(
            reference not in block_ids
            for region in regions
            for node in region.nodes
            for reference in (*node.block_ids, *node.detail_block_ids)
        )
        or any(
            edge.source not in {node.id for node in region.nodes}
            or edge.target not in {node.id for node in region.nodes}
            for region in regions
            for edge in region.edges
        )
        or any(
            reference not in block_ids
            for section in sections
            for reference in (
                section.head_block_id,
                *section.block_ids,
                *section.child_block_ids,
            )
        )
    ):
        raise PdfParseApiError(
            ErrorCode.PARSER_FAILED,
            diagnostic=FailureDiagnostic(
                stage="extraction_validation", reason="invalid_result_references"
            ),
        )
    if not any(
        block.text.strip()
        and not block.excluded_from_retrieval
        and block.kind not in {"header", "footer", "page_number", "approval", "figure"}
        for block in blocks
    ):
        raise PdfParseApiError(ErrorCode.NO_EXTRACTABLE_TEXT)
    if (
        any(warning.code not in _INFORMATIONAL_WARNINGS for warning in warnings)
        or any(page.requires_vision for page in pages)
        or any(block.requires_vision for block in blocks)
    ):
        return ExtractionQuality.PARTIAL
    return ExtractionQuality.COMPLETE
