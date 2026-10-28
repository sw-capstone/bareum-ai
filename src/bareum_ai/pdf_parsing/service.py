import logging
from datetime import UTC, datetime
from typing import Final, final

import anyio

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.api.responses import SucceededResponse
from bareum_ai.pdf_parsing.errors import ERROR_DEFINITIONS, ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.parser_process import ParserProcess
from bareum_ai.pdf_parsing.parser.worker_contract import ParserInput
from bareum_ai.pdf_parsing.settings import PdfParsingSettings
from bareum_ai.pdf_parsing.source_download import PdfSourceDownloader
from bareum_ai.pdf_parsing.types import (
    FailureDiagnostic,
    FailureStage,
    ParseAttemptId,
    ReportId,
)

_LOGGER: Final = logging.getLogger(__name__)


@final
class PdfParsingService:
    """현재 프로세스의 실행만 제한하며 완료 결과나 시도 이력을 보관하지 않는다."""

    def __init__(
        self,
        downloader: PdfSourceDownloader,
        parser: ParserProcess,
        settings: PdfParsingSettings,
    ) -> None:
        self._downloader = downloader
        self._parser = parser
        self._settings = settings
        self._attempts: set[ParseAttemptId] = set()
        self._reports: set[ReportId] = set()

    async def parse(self, request: PdfParseRequest) -> SucceededResponse:
        # 검사와 등록 사이에 await가 없어 단일 이벤트 루프에서 동시에 선점되지 않는다.
        if (
            request.parse_attempt_id in self._attempts
            or request.report_id in self._reports
        ):
            raise PdfParseApiError(ErrorCode.ATTEMPT_IN_PROGRESS, request)
        if len(self._attempts) >= self._settings.max_concurrent_parses:
            raise PdfParseApiError(ErrorCode.CAPACITY_EXCEEDED, request)
        self._attempts.add(request.parse_attempt_id)
        self._reports.add(request.report_id)
        started = datetime.now(UTC)
        stage: FailureStage = "source_download"
        try:
            with anyio.fail_after(self._settings.execution_timeout_seconds):
                async with self._downloader.download(request) as path:
                    stage = "parser"
                    result = await self._parser.run(
                        ParserInput(
                            source_path=path,
                            original_filename=request.source.original_filename,
                            sha256=request.source.sha256,
                            page_limit=self._settings.max_page_count,
                        )
                    )
                    stage = "execution"
            return SucceededResponse(
                report_id=request.report_id,
                parse_attempt_id=request.parse_attempt_id,
                started_at=started,
                completed_at=datetime.now(UTC),
                extraction_quality=result.quality,
                parsed=result.parsed,
            )
        except PdfParseApiError as exc:
            code = exc.code
            diagnostic = exc.diagnostic or FailureDiagnostic(
                stage=stage, reason="validation_failed"
            )
        except TimeoutError:
            code = ErrorCode.PARSER_TIMEOUT
            diagnostic = FailureDiagnostic(stage=stage, reason="timeout")
        except OSError as exc:
            code = ErrorCode.PARSER_FAILED
            diagnostic = FailureDiagnostic.from_exception(stage, exc)
        finally:
            self._attempts.remove(request.parse_attempt_id)
            self._reports.remove(request.report_id)
        _LOGGER.log(
            logging.ERROR
            if ERROR_DEFINITIONS[code].http_status >= 500
            else logging.WARNING,
            "pdf_parse_failed report_id=%s parse_attempt_id=%s code=%s stage=%s reason=%s",
            request.report_id,
            request.parse_attempt_id,
            code,
            diagnostic.stage,
            diagnostic.reason,
            extra={
                "report_id": request.report_id,
                "parse_attempt_id": request.parse_attempt_id,
                "code": code,
                "stage": diagnostic.stage,
                "reason": diagnostic.reason,
            },
        )
        raise PdfParseApiError(code, request) from None
