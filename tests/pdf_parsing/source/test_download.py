import tempfile
from datetime import UTC, datetime
from hashlib import sha256
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


async def test_download_keeps_verified_file_until_caller_finishes(
    source_request: PdfParseRequest, source_bytes: bytes
) -> None:
    # Given
    stream = ChunkStream((source_bytes[:8], source_bytes[8:]))
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, stream=stream)

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        async with downloader.download(source_request) as path:
            assert path.read_bytes() == source_bytes
    # Then
    assert not path.parent.exists()
    assert stream.closed
    assert requests[0].method == "GET"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert str(requests[0].url) == source_request.source.download_url.get_secret_value()


@pytest.mark.parametrize("overflow", [False, True])
async def test_receiving_limit_includes_exactly_twenty_million_bytes(
    source_request: PdfParseRequest, overflow: bool
) -> None:
    # Given
    body = b"a" * 20_000_000
    source = source_request.source.model_copy(
        update={"size_bytes": len(body), "sha256": sha256(body).hexdigest()}
    )
    request = source_request.model_copy(update={"source": source})
    chunks = (body, b"!", b"unread") if overflow else (body,)
    stream = ChunkStream(chunks)

    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=stream)

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        if overflow:
            with pytest.raises(PdfParseApiError) as caught:
                async with downloader.download(request):
                    pytest.fail("초과 원본이 전달됨")
            # Then
            assert caught.value.code == ErrorCode.FILE_TOO_LARGE
            assert stream.consumed == 2
        else:
            async with downloader.download(request) as path:
                # Then
                assert path.stat().st_size == 20_000_000
    assert stream.closed


@pytest.mark.parametrize(
    "body", [b"%PDF-1.7\nwrong bytes", b"short", b"%PDF-1.7\nsource data extra"]
)
async def test_actual_size_and_hash_must_match_identity(
    source_request: PdfParseRequest, body: bytes
) -> None:
    # Given
    stream = ChunkStream((body,))

    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=stream)

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(source_request):
                pytest.fail("다른 원본이 전달됨")
    # Then
    assert caught.value.code == ErrorCode.SOURCE_IDENTITY_MISMATCH
    assert caught.value.identity == source_request
    assert stream.closed


@pytest.mark.parametrize(
    ("length", "code"),
    [
        ("20000001", ErrorCode.FILE_TOO_LARGE),
        ("3", ErrorCode.SOURCE_IDENTITY_MISMATCH),
        ("invalid", ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE),
    ],
)
async def test_content_length_rejection_stops_before_body(
    source_request: PdfParseRequest, length: str, code: ErrorCode
) -> None:
    # Given
    stream = ChunkStream((b"unread",))

    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"Content-Length": length}, stream=stream)

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(source_request):
                pytest.fail("검증에 실패한 원본이 전달됨")
    # Then
    assert caught.value.code == code
    assert stream.consumed == 0
    assert stream.closed


@pytest.mark.parametrize("expired", [False, True])
async def test_invalid_source_is_rejected_before_network_request(
    source_request: PdfParseRequest, expired: bool
) -> None:
    # Given
    source = source_request.source.model_copy(
        update=(
            {"url_expires_at": datetime(2000, 1, 1, tzinfo=UTC)}
            if expired
            else {"size_bytes": 20_000_001}
        )
    )
    request = source_request.model_copy(update={"source": source})

    def respond(_: httpx2.Request) -> httpx2.Response:
        pytest.fail("요청 전 거부해야 할 원본에 접근함")

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(request):
                pytest.fail("잘못된 원본이 전달됨")
    # Then
    expected = ErrorCode.SOURCE_URL_EXPIRED if expired else ErrorCode.FILE_TOO_LARGE
    assert caught.value.code == expected


async def test_transport_failure_hides_signed_url(
    source_request: PdfParseRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def respond(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout(str(request.url), request=request)

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(source_request):
                pytest.fail("실패한 다운로드가 전달됨")
    # Then
    assert caught.value.code == ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE
    assert "signature=example" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert [entry async for entry in anyio.Path(tmp_path).iterdir()] == []
