from datetime import UTC, datetime
from typing import final

import anyio

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.api.responses import SucceededResponse
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.parser_process import ParserProcess
from bareum_ai.pdf_parsing.parser.worker_contract import ParserInput
from bareum_ai.pdf_parsing.settings import PdfParsingSettings
from bareum_ai.pdf_parsing.source_download import PdfSourceDownloader
from bareum_ai.pdf_parsing.types import ParseAttemptId, ReportId


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
        try:
            with anyio.fail_after(self._settings.execution_timeout_seconds):
                async with self._downloader.download(request) as path:
                    result = await self._parser.run(
                        ParserInput(
                            source_path=path,
                            original_filename=request.source.original_filename,
                            sha256=request.source.sha256,
                            page_limit=self._settings.max_page_count,
                        )
                    )
            return SucceededResponse(
                report_id=request.report_id,
                parse_attempt_id=request.parse_attempt_id,
                started_at=started,
                completed_at=datetime.now(UTC),
                extraction_quality=result.quality,
                parsed=result.parsed,
            )
        except PdfParseApiError as exc:
            raise PdfParseApiError(exc.code, request) from None
        except TimeoutError:
            raise PdfParseApiError(ErrorCode.PARSER_TIMEOUT, request) from None
        except OSError:
            raise PdfParseApiError(ErrorCode.PARSER_FAILED, request) from None
        finally:
            self._attempts.remove(request.parse_attempt_id)
            self._reports.remove(request.report_id)
