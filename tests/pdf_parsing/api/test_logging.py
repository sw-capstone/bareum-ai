import logging
from dataclasses import dataclass

import anyio
import pytest

from bareum_ai.pdf_parsing import runtime
from bareum_ai.pdf_parsing.errors import ERROR_DEFINITIONS, ErrorCode
from bareum_ai.pdf_parsing.parser.parser_process import ParserProcess
from bareum_ai.pdf_parsing.settings import PdfParsingSettings

from .conftest import ApiCase

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class FailureCase:
    mode: str
    stage: str
    reason: str


@pytest.fixture
def fixture_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    def parser_process(settings: PdfParsingSettings) -> ParserProcess:
        return ParserProcess(
            settings, worker_module="tests.pdf_parsing.process.fixture_worker"
        )

    monkeypatch.setattr(runtime, "ParserProcess", parser_process)


@pytest.mark.usefixtures("fixture_parser")
@pytest.mark.parametrize(
    "failure",
    [
        FailureCase("crash", "worker_exit", "nonzero_exit"),
        FailureCase("invalid", "result_validation", "invalid_schema"),
        FailureCase("missing-result", "result_read", "io_error"),
        FailureCase("parser-exception", "parser", "execution_error"),
        FailureCase("invalid-schema", "result_validation", "invalid_schema"),
        FailureCase(
            "invalid-references", "extraction_validation", "invalid_result_references"
        ),
        FailureCase("overlapping-table", "table_validation", "overlapping_cells"),
    ],
)
async def test_failed_parse_logs_one_safe_diagnostic_and_preserves_response(
    api_case: ApiCase,
    caplog: pytest.LogCaptureFixture,
    failure: FailureCase,
) -> None:
    # Given
    source = api_case.request.source.model_copy(
        update={"original_filename": failure.mode + "-private-filename.pdf"}
    )
    request = api_case.request.model_copy(update={"source": source})
    caplog.set_level(logging.DEBUG)
    # When
    response = await api_case.post(request)
    # Then
    definition = ERROR_DEFINITIONS[ErrorCode.PARSER_FAILED]
    assert response.status_code == 500
    assert response.json() == {
        "reportId": 101,
        "parseAttemptId": 201,
        "error": {
            "code": "PARSER_FAILED",
            "message": definition.message,
            "action": "STOP",
        },
    }
    records = [record for record in caplog.records if record.name.endswith(".service")]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    assert record.__dict__["report_id"] == 101
    assert record.__dict__["parse_attempt_id"] == 201
    assert record.__dict__["code"] == "PARSER_FAILED"
    assert record.__dict__["stage"] == failure.stage
    assert record.__dict__["reason"] == failure.reason
    assert record.exc_info is None
    message = record.getMessage()
    assert "stage=" + failure.stage in message
    assert "reason=" + failure.reason in message
    for sensitive in (
        source.download_url.get_secret_value(),
        "signature=example",
        "example-internal-token",
        source.original_filename,
        "private-parser-content",
        "Merged header",
        "Education was completed.",
    ):
        assert sensitive not in caplog.text
    assert list(api_case.temporary_root.iterdir()) == []


async def test_successful_parse_does_not_log_failure(
    api_case: ApiCase, caplog: pytest.LogCaptureFixture
) -> None:
    # Given / When
    response = await api_case.post()
    # Then
    assert response.status_code == 200
    assert not [record for record in caplog.records if record.name.endswith(".service")]


async def test_execution_timeout_logs_once_with_request_ids(
    api_case: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("BAREUM_AI_EXECUTION_TIMEOUT_SECONDS", "0.02")
    api_case.source.release = anyio.Event()
    with anyio.fail_after(5):
        response = await api_case.post()
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "PARSER_TIMEOUT"
    records = [record for record in caplog.records if record.name.endswith(".service")]
    assert len(records) == 1
    assert records[0].__dict__["report_id"] == 101
    assert records[0].__dict__["parse_attempt_id"] == 201
    assert records[0].__dict__["stage"] == "source_download"
    assert records[0].__dict__["reason"] == "timeout"


@pytest.mark.usefixtures("fixture_parser")
async def test_parser_timeout_logs_once_after_worker_stops(
    api_case: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("BAREUM_AI_PARSER_TIMEOUT_SECONDS", "0.02")
    monkeypatch.setenv("BAREUM_AI_PROCESS_TERMINATION_GRACE_SECONDS", "0.01")
    with anyio.fail_after(5):
        response = await api_case.post()
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "PARSER_TIMEOUT"
    records = [record for record in caplog.records if record.name.endswith(".service")]
    assert len(records) == 1
    assert records[0].__dict__["stage"] == "worker_wait"
    assert records[0].__dict__["reason"] == "timeout"
    assert list(api_case.temporary_root.iterdir()) == []
