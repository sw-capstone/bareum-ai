import logging
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Final, assert_never
from urllib.parse import unquote_to_bytes, urlsplit
from xml.etree import ElementTree as ET

import anyio
import httpx2

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest, PdfSource
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.settings import PdfParsingSettings

_PRIVATE_DOWNLOAD: Final = ContextVar("private_source_download", default=False)


class _SourceLogFilter(logging.Filter):
    def filter(self, _record: logging.LogRecord) -> bool:
        # HTTPX의 INFO URL과 HTTPCore의 DEBUG 응답 헤더에도 서명이 포함될 수 있다.
        return not _PRIVATE_DOWNLOAD.get()


_SOURCE_LOG_FILTER: Final = _SourceLogFilter()


@dataclass(frozen=True, slots=True)
class SourceHttpResponse:
    status_code: int
    headers: httpx2.Headers
    body: httpx2.AsyncByteStream


@asynccontextmanager
async def stream_source(
    client: httpx2.AsyncClient, url: httpx2.URL
) -> AsyncIterator[SourceHttpResponse]:
    """서명 로그를 차단하고 취소된 요청의 소켓도 닫은 뒤 반환한다."""
    for name in (
        "httpx2",
        "httpcore2.connection",
        "httpcore2.http11",
        "httpcore2.http2",
        "httpcore2.proxy",
    ):
        logging.getLogger(name).addFilter(_SOURCE_LOG_FILTER)
    previous_context = _PRIVATE_DOWNLOAD.set(True)
    try:
        request = client.build_request(
            "GET", url, headers={"Accept-Encoding": "identity"}
        )
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            # aiter_raw()의 자동 닫기는 취소 중에 중단될 수 있어 스트림을 직접 소유한다.
            match response.stream:
                case httpx2.AsyncByteStream() as body:
                    yield SourceHttpResponse(
                        response.status_code, response.headers, body
                    )
                case httpx2.SyncByteStream():
                    raise PdfParseApiError(ErrorCode.CONFIGURATION_UNAVAILABLE)
                case unreachable:
                    assert_never(unreachable)
        finally:
            with anyio.CancelScope(shield=True):
                await response.aclose()
    finally:
        _PRIVATE_DOWNLOAD.reset(previous_context)


def create_source_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.AsyncHTTPTransport(
            http2=True,
            trust_env=False,
            retries=3,
            limits=httpx2.Limits(
                max_connections=200,
                max_keepalive_connections=40,
                keepalive_expiry=30.0,
            ),
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        ),
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
        trust_env=False,
        headers={"Accept-Encoding": "identity"},
    )


def validate_source_url(
    source: PdfSource, settings: PdfParsingSettings, now: datetime
) -> httpx2.URL:
    url = validate_source_scope(source, settings)
    if now >= source.url_expires_at:
        raise PdfParseApiError(ErrorCode.SOURCE_URL_EXPIRED)
    return url


def validate_source_scope(
    source: PdfSource, settings: PdfParsingSettings
) -> httpx2.URL:
    """실제 전송할 URL 경로를 한 번 디코딩해 원본 키와 비교한다."""
    raw_url = source.download_url.get_secret_value()
    if any(ord(char) <= 32 or ord(char) == 127 for char in raw_url):
        raise PdfParseApiError(ErrorCode.INVALID_REQUEST)
    try:
        url = httpx2.URL(raw_url)
        original_path = urlsplit(raw_url).path
        if re.search(r"%(?![0-9a-fA-F]{2})", original_path):
            raise PdfParseApiError(ErrorCode.INVALID_REQUEST)
        path = unquote_to_bytes(original_path).decode("utf-8", errors="strict")
        sent_path = unquote_to_bytes(url.raw_path.split(b"?", 1)[0]).decode(
            "utf-8", errors="strict"
        )
    except httpx2.InvalidURL, UnicodeError:
        raise PdfParseApiError(ErrorCode.INVALID_REQUEST) from None
    if (
        url.scheme != "https"
        or url.host != settings.s3_allowed_host
        or url.port not in {None, 443}
        or bool(url.userinfo)
        or bool(url.fragment)
        or not source.object_key.startswith(settings.s3_allowed_key_prefix)
        or path != "/" + source.object_key
        or sent_path != path
        or "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise PdfParseApiError(ErrorCode.INVALID_REQUEST)
    return url


_ERROR_BODY_LIMIT: Final = 8192


async def require_source_success(response: SourceHttpResponse) -> None:
    """만료 근거가 없는 403을 URL 만료로 바꾸지 않는다."""
    if response.status_code == 200:
        return
    if response.status_code == 404:
        raise PdfParseApiError(ErrorCode.SOURCE_NOT_FOUND)
    if response.status_code in {408, 429} or response.status_code >= 500:
        raise PdfParseApiError(ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE)
    if response.status_code in {400, 403}:
        body = bytearray()
        async for chunk in response.body:
            if len(body) + len(chunk) > _ERROR_BODY_LIMIT:
                break
            body.extend(chunk)
        if _is_expired_s3_response(bytes(body)):
            raise PdfParseApiError(ErrorCode.SOURCE_URL_EXPIRED)
    raise PdfParseApiError(ErrorCode.SOURCE_ACCESS_DENIED)


def _is_expired_s3_response(body: bytes) -> bool:
    try:
        text = body.decode("utf-8", errors="strict")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            return False
        # UTF-8만 허용하고 DTD·엔티티 선언을 위에서 차단한 제한 크기 응답이다.
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError, UnicodeError:
        return False
    code = root.findtext("Code")
    return code in {"ExpiredToken", "RequestExpired"} or (
        code == "AccessDenied" and root.findtext("Message") == "Request has expired"
    )


@dataclass(frozen=True, slots=True)
class PdfSourceDownloader:
    client: httpx2.AsyncClient
    settings: PdfParsingSettings

    @asynccontextmanager
    async def download(self, request: PdfParseRequest) -> AsyncIterator[Path]:
        """호출자가 파싱을 마칠 때까지 파일을 유지하고 취소 시에도 정리한다."""
        async with anyio.TemporaryDirectory(prefix="bareum-pdf-") as directory:
            path = Path(directory) / "source.pdf"
            try:
                with anyio.fail_after(self.settings.source_download_timeout_seconds):
                    await self._receive(request.source, path)
            except httpx2.RequestError, TimeoutError:
                raise PdfParseApiError(
                    ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE, request
                ) from None
            except PdfParseApiError as exc:
                raise PdfParseApiError(exc.code, request) from None
            yield path

    async def _receive(self, source: PdfSource, path: Path) -> None:
        url = validate_source_url(source, self.settings, datetime.now(UTC))
        limit = self.settings.max_file_size_bytes
        if source.size_bytes > limit:
            raise PdfParseApiError(ErrorCode.FILE_TOO_LARGE)
        async with stream_source(self.client, url) as response:
            await require_source_success(response)
            raw_length = response.headers.get("content-length")
            if raw_length is not None:
                try:
                    length = int(raw_length)
                except ValueError:
                    raise PdfParseApiError(
                        ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE
                    ) from None
                if length > limit:
                    raise PdfParseApiError(ErrorCode.FILE_TOO_LARGE)
                if length != source.size_bytes:
                    raise PdfParseApiError(ErrorCode.SOURCE_IDENTITY_MISMATCH)
            digest = sha256()
            size = 0
            async with await anyio.open_file(path, "wb") as output:
                async for chunk in response.body:
                    size += len(chunk)
                    if size > limit:
                        raise PdfParseApiError(ErrorCode.FILE_TOO_LARGE)
                    digest.update(chunk)
                    await output.write(chunk)
            if size != source.size_bytes or digest.hexdigest() != source.sha256:
                raise PdfParseApiError(ErrorCode.SOURCE_IDENTITY_MISMATCH)
