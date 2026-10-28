import pytest

from bareum_ai.pdf_parsing import runtime
from bareum_ai.pdf_parsing.errors import ErrorAction, ErrorCode, ParseErrorResponse
from bareum_ai.pdf_parsing.parser.parser_process import ParserProcess
from bareum_ai.pdf_parsing.settings import PdfParsingSettings

from .conftest import ApiCase


@pytest.mark.anyio
async def test_overlapping_parser_cells_return_failed_response(
    api_case: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    def parser_process(settings: PdfParsingSettings) -> ParserProcess:
        return ParserProcess(
            settings, worker_module="tests.pdf_parsing.process.fixture_worker"
        )

    monkeypatch.setattr(runtime, "ParserProcess", parser_process)
    source = api_case.request.source.model_copy(
        update={"original_filename": "overlapping-table"}
    )
    request = api_case.request.model_copy(update={"source": source})
    # When
    response = await api_case.post(request)
    # Then
    assert response.status_code == 500
    body = ParseErrorResponse.model_validate_json(response.content)
    assert body.report_id == 101
    assert body.parse_attempt_id == 201
    assert body.error.code == ErrorCode.PARSER_FAILED
    assert body.error.action == ErrorAction.STOP
    assert list(api_case.temporary_root.iterdir()) == []
