from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import final

import anyio
import httpx2
from fastapi import FastAPI

from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.parser_process import ParserProcess
from bareum_ai.pdf_parsing.service import PdfParsingService
from bareum_ai.pdf_parsing.settings import get_pdf_parsing_settings
from bareum_ai.pdf_parsing.source_download import (
    PdfSourceDownloader,
    create_source_client,
)


@final
class PdfParsingRuntime:
    def __init__(
        self,
        source_client_factory: Callable[[], httpx2.AsyncClient] = create_source_client,
    ) -> None:
        self._source_client_factory = source_client_factory
        self._lock = anyio.Lock()
        self._client: httpx2.AsyncClient | None = None
        self._service: PdfParsingService | None = None

    @asynccontextmanager
    async def lifespan(self, _application: FastAPI) -> AsyncGenerator[None]:
        async with self._source_client_factory() as client:
            self._client = client
            try:
                yield
            finally:
                self._service = None
                self._client = None

    async def service(self) -> PdfParsingService:
        async with self._lock:
            if self._service is None:
                if self._client is None:
                    raise PdfParseApiError(ErrorCode.CONFIGURATION_UNAVAILABLE)
                settings = get_pdf_parsing_settings()
                self._service = PdfParsingService(
                    PdfSourceDownloader(self._client, settings),
                    ParserProcess(settings),
                    settings,
                )
            return self._service
