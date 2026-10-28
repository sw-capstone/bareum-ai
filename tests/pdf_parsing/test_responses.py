from datetime import UTC, datetime, timedelta
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from bareum_ai.dataset.parsing.parser import parse_pdf
from bareum_ai.pdf_parsing.api.responses import (
    ParsedPdf,
    SucceededResponse,
)
from bareum_ai.pdf_parsing.errors import ErrorAction, ErrorCode, ParseError
from bareum_ai.pdf_parsing.types import (
    ExtractionQuality,
    JsonObject,
    ParseAttemptId,
    ReportId,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "PROCESSING"},
        {"parsed": {}},
        {"status": "FAILED"},
        {"extractionQuality": "UNKNOWN"},
        {"status": "AWAITING_SOURCE"},
        {"status": "UNRECOGNIZED"},
        {"completedAt": "2000-01-01T00:00:00Z"},
    ],
)
def test_response_rejects_fields_inconsistent_with_execution_status(
    payload: JsonObject,
) -> None:
    raw = {
        "reportId": 101,
        "parseAttemptId": 201,
        "startedAt": "2026-10-18T13:00:00Z",
        "completedAt": "2026-10-18T13:01:00Z",
        "extractionQuality": "COMPLETE",
        "parsed": {
            "schema_version": "1.2.0",
            "document": {},
            "parser": {},
            "pages": [],
            "sections": [],
            "blocks": [],
            "layout_regions": [],
            "warnings": [],
        },
        **payload,
    }

    with pytest.raises(ValidationError):
        SucceededResponse.model_validate(raw)


def test_error_rejects_retry_instruction_that_conflicts_with_its_code() -> None:
    with pytest.raises(ValidationError):
        ParseError(
            code=ErrorCode.SOURCE_URL_EXPIRED,
            message="example",
            action=ErrorAction.FIX_REQUEST,
        )


def test_success_preserves_actual_parser_payload_without_camelizing_nested_keys(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "report.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 100), "Example report body.")
        document.save(pdf_path)
    parsed, _, _ = parse_pdf(pdf_path)
    completed = datetime(2026, 10, 18, 13, tzinfo=UTC)

    response = SucceededResponse(
        report_id=ReportId(101),
        parse_attempt_id=ParseAttemptId(201),
        started_at=completed - timedelta(seconds=5),
        completed_at=completed,
        extraction_quality=ExtractionQuality.COMPLETE,
        parsed=ParsedPdf.model_validate(parsed),
    )

    result = response.model_dump(mode="json")
    assert result["parsed"] == parsed
    assert result["reportId"] == 101
    assert result["extractionQuality"] == "COMPLETE"


def test_parser_extension_fields_survive_response_validation() -> None:
    raw = {
        "schema_version": "1.2.0",
        "document": {},
        "parser": {},
        "pages": [],
        "sections": [],
        "blocks": [],
        "layout_regions": [],
        "warnings": [],
        "extension": {"future_field": ["retained", None]},
    }

    parsed = ParsedPdf.model_validate(raw)

    assert parsed.model_dump(mode="json") == raw


def test_parser_extensions_reject_values_that_cannot_be_json() -> None:
    raw = {
        "schema_version": "1.2.0",
        "document": {},
        "parser": {},
        "pages": [],
        "sections": [],
        "blocks": [],
        "layout_regions": [],
        "warnings": [],
        "extension": {"future_field": Path("report.pdf")},
    }

    with pytest.raises(ValidationError):
        ParsedPdf.model_validate(raw)
