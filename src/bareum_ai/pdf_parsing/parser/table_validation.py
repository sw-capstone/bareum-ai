from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.types import JsonObject


class _Cell(BaseModel):
    """파서가 반환한 셀의 시작 위치와 병합 크기를 나타낸다."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    row: Annotated[int, Field(strict=True, ge=0)]
    col: Annotated[int, Field(strict=True, ge=0)]
    rowspan: Annotated[int, Field(strict=True, gt=0)]
    colspan: Annotated[int, Field(strict=True, gt=0)]


class _Table(BaseModel):
    """표의 크기와 셀 배치를 검사하는 데 필요한 추출 정보를 담는다."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    rows: Annotated[tuple[tuple[str, ...], ...], Field(min_length=1)]
    cells: tuple[_Cell, ...]


def validate_table(block: JsonObject) -> None:
    """셀의 위치와 병합 범위를 검사하고 겹치는 셀을 파싱 실패로 처리한다."""
    try:
        table = _Table.model_validate(block)
    except ValidationError:
        raise PdfParseApiError(ErrorCode.PARSER_FAILED) from None

    row_count = len(table.rows)
    column_count = max(len(row) for row in table.rows)
    if column_count == 0:
        raise PdfParseApiError(ErrorCode.PARSER_FAILED)

    covered: set[tuple[int, int]] = set()
    for cell in table.cells:
        row_end = cell.row + cell.rowspan
        column_end = cell.col + cell.colspan
        if (
            row_end > row_count
            or column_end > column_count
            or cell.col >= len(table.rows[cell.row])
        ):
            raise PdfParseApiError(ErrorCode.PARSER_FAILED)
        for row in range(cell.row, row_end):
            for column in range(cell.col, column_end):
                position = (row, column)
                if position in covered:
                    raise PdfParseApiError(ErrorCode.PARSER_FAILED)
                covered.add(position)
