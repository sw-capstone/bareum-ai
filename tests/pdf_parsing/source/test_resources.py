import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import httpx2
import pytest

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.settings import PdfParsingSettings
from bareum_ai.pdf_parsing.source_download import PdfSourceDownloader

from .streams import ChunkStream

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def temporary_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


class WaitingStream(httpx2.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = anyio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"%PDF"
        self.entered.set()
        await anyio.sleep_forever()

    async def aclose(self) -> None:
        await anyio.lowlevel.checkpoint()
        self.closed = True


class BrokenStream(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"%PDF"
        message = "incomplete response"
        raise httpx2.RemoteProtocolError(message)


async def test_cancellation_closes_connection_and_removes_partial_file(
    source_request: PdfParseRequest, tmp_path: Path
) -> None:
    # Given
    stream = WaitingStream()

    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=stream)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())

        async def download() -> None:
            async with downloader.download(source_request):
                pytest.fail("완료되지 않은 원본이 전달됨")

        # When
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(download)
            await stream.entered.wait()
            tasks.cancel_scope.cancel()
    # Then
    assert stream.closed
    assert [entry async for entry in anyio.Path(tmp_path).iterdir()] == []


async def test_total_budget_stops_continuously_waiting_download(
    source_request: PdfParseRequest, tmp_path: Path
) -> None:
    # Given
    stream = WaitingStream()

    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=stream)

    settings = PdfParsingSettings(source_download_timeout_seconds=0.01)
    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, settings)
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(source_request):
                pytest.fail("시간을 초과한 다운로드가 전달됨")
    # Then
    assert caught.value.code == ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE
    assert stream.closed
    assert [entry async for entry in anyio.Path(tmp_path).iterdir()] == []


async def test_broken_body_is_retryable_and_partial_file_is_removed(
    source_request: PdfParseRequest, tmp_path: Path
) -> None:
    # Given
    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=BrokenStream())

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(source_request):
                pytest.fail("중단된 원본이 전달됨")
    # Then
    assert caught.value.code == ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE
    assert [entry async for entry in anyio.Path(tmp_path).iterdir()] == []


async def test_caller_failure_preserves_error_and_removes_downloaded_file(
    source_request: PdfParseRequest, source_bytes: bytes, tmp_path: Path
) -> None:
    # Given
    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=ChunkStream((source_bytes,)))

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(source_request):
                raise PdfParseApiError(ErrorCode.PDF_CORRUPTED)
    # Then
    assert caught.value.code == ErrorCode.PDF_CORRUPTED
    assert [entry async for entry in anyio.Path(tmp_path).iterdir()] == []
