from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from bareum_ai.pdf_parsing.api.responses import ParsedPdf
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.validation import validate_extraction
from bareum_ai.pdf_parsing.parser.worker_contract import ParserInput
from bareum_ai.pdf_parsing.types import ExtractionQuality, JsonObject


class CellCase(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    cells: tuple[JsonObject, ...]


def table_result(job: ParserInput, cells: tuple[JsonObject, ...]) -> ParsedPdf:
    return ParsedPdf(
        schema_version="1.2.0",
        document={"page_count": 1, "sha256": job.sha256},
        parser={},
        pages=({"page": 1, "width": 595, "height": 842},),
        sections=(),
        blocks=(
            {
                "id": "table1",
                "page": 1,
                "kind": "table",
                "text": "Merged header",
                "source_text": "Merged header",
                "bbox": [0, 0, 100, 100],
                "rows": [["Merged", "Header"], ["", ""]],
                "cells": list(cells),
            },
        ),
        layout_regions=(),
        warnings=(),
    )


@pytest.mark.parametrize("include_empty_cell", [True, False])
def test_merged_cells_allow_empty_cells_and_unresolved_slots(
    parser_job: ParserInput,
    include_empty_cell: bool,
) -> None:
    # Given
    cells: tuple[JsonObject, ...] = (
        {"row": 0, "col": 0, "rowspan": 2, "colspan": 1},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 1},
    )
    if include_empty_cell:
        empty_cell: JsonObject = {"row": 1, "col": 1, "rowspan": 1, "colspan": 1}
        cells += (empty_cell,)
    parsed = table_result(parser_job, cells)
    # When
    quality = validate_extraction(parsed, parser_job)
    # Then
    assert quality == ExtractionQuality.COMPLETE
    assert parsed.blocks[0]["cells"] == list(cells)


@pytest.mark.parametrize(
    "case",
    [
        CellCase(cells=({"row": -1, "col": 0, "rowspan": 1, "colspan": 1},)),
        CellCase(cells=({"row": 0, "col": -1, "rowspan": 1, "colspan": 1},)),
        CellCase(cells=({"row": 0, "col": 0, "rowspan": 0, "colspan": 1},)),
        CellCase(cells=({"row": 0, "col": 0, "rowspan": 1, "colspan": 0},)),
        CellCase(cells=({"row": 0, "col": 0, "rowspan": -1, "colspan": 1},)),
        CellCase(cells=({"row": 2, "col": 0, "rowspan": 1, "colspan": 1},)),
        CellCase(cells=({"row": 0, "col": 2, "rowspan": 1, "colspan": 1},)),
        CellCase(cells=({"row": 1, "col": 0, "rowspan": 2, "colspan": 1},)),
        CellCase(cells=({"row": 0, "col": 1, "rowspan": 1, "colspan": 2},)),
        CellCase(cells=({"row": "0", "col": 0, "rowspan": 1, "colspan": 1},)),
        CellCase(cells=({"row": True, "col": 0, "rowspan": 1, "colspan": 1},)),
        CellCase(cells=({"row": 0, "col": 0, "rowspan": 1.5, "colspan": 1},)),
        CellCase(cells=({"row": 0, "col": 0, "rowspan": 1},)),
        CellCase(
            cells=(
                {"row": 0, "col": 0, "rowspan": 1, "colspan": 1},
                {"row": 0, "col": 0, "rowspan": 1, "colspan": 1},
            )
        ),
        CellCase(
            cells=(
                {"row": 0, "col": 0, "rowspan": 2, "colspan": 2},
                {"row": 1, "col": 1, "rowspan": 1, "colspan": 1},
            )
        ),
    ],
)
def test_invalid_cell_structures_fail_before_success(
    parser_job: ParserInput,
    case: CellCase,
) -> None:
    # Given
    parsed = table_result(parser_job, case.cells)
    # When
    with pytest.raises(PdfParseApiError) as caught:
        _ = validate_extraction(parsed, parser_job)
    # Then
    assert caught.value.code == ErrorCode.PARSER_FAILED
