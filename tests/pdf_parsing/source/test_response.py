import logging

import httpx2
import pytest

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.settings import PdfParsingSettings
from bareum_ai.pdf_parsing.source_download import PdfSourceDownloader

from .streams import ChunkStream

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "response",
    [
        (
            403,
            b"<Error><Code>ExpiredToken</Code></Error>",
            ErrorCode.SOURCE_URL_EXPIRED,
        ),
        (
            400,
            b"<Error><Code>RequestExpired</Code></Error>",
            ErrorCode.SOURCE_URL_EXPIRED,
        ),
        (
            403,
            (
                b"<Error><Code>AccessDenied</Code>"
                b"<Message>Request has expired</Message></Error>"
            ),
            ErrorCode.SOURCE_URL_EXPIRED,
        ),
        (
            403,
            b"<Error><Code>AccessDenied</Code></Error>",
            ErrorCode.SOURCE_ACCESS_DENIED,
        ),
        (
            403,
            b"<Error><Code>SignatureDoesNotMatch</Code></Error>",
            ErrorCode.SOURCE_ACCESS_DENIED,
        ),
        (
            403,
            b"<Error><Code>InvalidAccessKeyId</Code></Error>",
            ErrorCode.SOURCE_ACCESS_DENIED,
        ),
        (403, b"invalid XML", ErrorCode.SOURCE_ACCESS_DENIED),
        (
            403,
            b"<Error>" + b" " * 8192 + b"<Code>ExpiredToken</Code></Error>",
            ErrorCode.SOURCE_ACCESS_DENIED,
        ),
        (
            403,
            (
                b'<!DOCTYPE Error [<!ENTITY code "ExpiredToken">]>'
                b"<Error><Code>&code;</Code></Error>"
            ),
            ErrorCode.SOURCE_ACCESS_DENIED,
        ),
        (404, b"", ErrorCode.SOURCE_NOT_FOUND),
        (408, b"", ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE),
        (429, b"", ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE),
        (503, b"", ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE),
        (206, b"partial", ErrorCode.SOURCE_ACCESS_DENIED),
        (302, b"", ErrorCode.SOURCE_ACCESS_DENIED),
    ],
)
async def test_http_errors_distinguish_expiry_from_permission_failure(
    source_request: PdfParseRequest, response: tuple[int, bytes, ErrorCode]
) -> None:
    # Given
    status, body, code = response
    stream = ChunkStream((body,))
    requested: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requested.append(request.url.host)
        return httpx2.Response(
            status, headers={"Location": "https://example.com/redirect"}, stream=stream
        )

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError) as caught:
            async with downloader.download(source_request):
                pytest.fail("실패한 응답이 원본으로 전달됨")
    # Then
    assert caught.value.code == code
    assert len(requested) == 1
    assert stream.closed


async def test_signed_url_is_hidden_even_with_http_debug_logs_enabled(
    source_request: PdfParseRequest, caplog: pytest.LogCaptureFixture
) -> None:
    # Given
    caplog.set_level(logging.DEBUG)
    url = source_request.source.download_url.get_secret_value()

    def respond(_: httpx2.Request) -> httpx2.Response:
        logging.getLogger("httpcore2.http11").debug("response headers: %s", url)
        return httpx2.Response(403, stream=ChunkStream((b"",)))

    # When
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        downloader = PdfSourceDownloader(client, PdfParsingSettings())
        with pytest.raises(PdfParseApiError):
            async with downloader.download(source_request):
                pytest.fail("거부된 원본이 전달됨")
    logging.getLogger("httpx2").info("unrelated-request")
    # Then
    assert url not in caplog.text
    assert "signature=example" not in caplog.text
    assert "unrelated-request" in caplog.text
