import json
from pathlib import Path

import pytest

from bareum_ai.dataset.parsing.parser import parse_pdf
from bareum_ai.pdf_parsing.api.responses import ParsedPdf
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.validation import validate_extraction
from bareum_ai.pdf_parsing.parser.worker_contract import ParserInput
from bareum_ai.pdf_parsing.types import ExtractionQuality


@pytest.fixture(scope="module")
def normal_parsed_pdf(parser_source: Path) -> ParsedPdf:
    payload, _markdown, _raw = parse_pdf(parser_source)
    return ParsedPdf.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "damage", ["parent", "section", "page", "duplicate", "hash", "layout"]
)
def test_invalid_raw_output_is_rejected_before_success(
    parser_job: ParserInput, normal_parsed_pdf: ParsedPdf, damage: str
) -> None:
    # Given
    validate_extraction(normal_parsed_pdf, parser_job)
    payload = normal_parsed_pdf.model_dump(mode="json")
    if damage == "parent":
        payload["blocks"][0]["parent_id"] = "missing"
    if damage == "section":
        payload["blocks"][0]["section_id"] = "missing"
    if damage == "page":
        payload["blocks"][0]["page"] = 99
    if damage == "duplicate":
        payload["blocks"].append(payload["blocks"][0].copy())
    if damage == "hash":
        payload["document"]["sha256"] = "0" * 64
    if damage == "layout":
        payload["layout_regions"] = [
            {
                "id": "region1",
                "page": 1,
                "edges": [],
                "nodes": [
                    {"id": "node1", "block_ids": ["missing"], "detail_block_ids": []}
                ],
            }
        ]
    parsed = ParsedPdf.model_validate_json(json.dumps(payload))
    # When
    with pytest.raises(PdfParseApiError) as caught:
        validate_extraction(parsed, parser_job)
    # Then
    assert caught.value.code == ErrorCode.PARSER_FAILED


@pytest.mark.parametrize(
    "warning_case",
    [
        ("blank_page", ExtractionQuality.COMPLETE),
        ("table_detection_failed", ExtractionQuality.PARTIAL),
        ("table_cell_linebreak_review", ExtractionQuality.PARTIAL),
        ("future_warning", ExtractionQuality.PARTIAL),
    ],
)
def test_warning_policy_distinguishes_information_from_review(
    parser_job: ParserInput,
    normal_parsed_pdf: ParsedPdf,
    warning_case: tuple[str, ExtractionQuality],
) -> None:
    # Given
    warning, quality = warning_case
    parsed = normal_parsed_pdf.model_copy(update={"warnings": ({"code": warning},)})
    # When
    actual = validate_extraction(parsed, parser_job)
    # Then
    assert actual == quality
