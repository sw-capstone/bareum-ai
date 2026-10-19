from datetime import datetime
from urllib.parse import quote

import pytest
from pydantic import SecretStr

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.settings import PdfParsingSettings
from bareum_ai.pdf_parsing.source_download import validate_source_url


@pytest.mark.parametrize(
    ("key", "url"),
    [
        ("reports/a.pdf", "https://example.com/reports/a.pdf"),
        ("reports/a.pdf", "https://127.0.0.1/reports/a.pdf"),
        ("reports/a.pdf", "https://169.254.169.254/reports/a.pdf"),
        (
            "reports/a.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com.evil.com/reports/a.pdf",
        ),
        (
            "private/a.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/private/a.pdf",
        ),
        (
            "reports/a.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/b.pdf",
        ),
        (
            "reports/../a.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/%2e%2e/a.pdf",
        ),
        (
            "reports/a.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/../reports/a.pdf",
        ),
        (
            "reports/a%ZZ.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/a%ZZ.pdf",
        ),
        (
            "reports/a.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/%FF.pdf",
        ),
        (
            "reports/a\x00.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/a%00.pdf",
        ),
        (
            "reports/a\\b.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/a%5Cb.pdf",
        ),
        (
            "reports/a.pdf",
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/a.pdf\n?signature=secret",
        ),
    ],
)
def test_scope_rejects_untrusted_or_ambiguous_source(
    source_request: PdfParseRequest, key: str, url: str
) -> None:
    # Given
    source = source_request.source.model_copy(
        update={"object_key": key, "download_url": SecretStr(url)}
    )
    # When
    with pytest.raises(PdfParseApiError) as caught:
        validate_source_url(source, PdfParsingSettings(), source.url_expires_at)
    # Then
    assert caught.value.code == ErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "key", ["reports/101/결과 보고서.pdf", "reports/101/a%2Fb.pdf"]
)
def test_key_is_decoded_once_without_changing_signature(
    source_request: PdfParseRequest, now: datetime, key: str
) -> None:
    # Given
    settings = PdfParsingSettings()
    raw_url = (
        f"https://{settings.s3_allowed_host}/{quote(key)}?X-Amz-Signature=ab%2Fcd&z=1"
    )
    source = source_request.source.model_copy(
        update={"object_key": key, "download_url": SecretStr(raw_url)}
    )
    # When
    url = validate_source_url(source, settings, now)
    # Then
    assert str(url) == raw_url


def test_expiry_boundary_requires_a_new_url(source_request: PdfParseRequest) -> None:
    # Given
    source = source_request.source
    # When
    with pytest.raises(PdfParseApiError) as caught:
        validate_source_url(source, PdfParsingSettings(), source.url_expires_at)
    # Then
    assert caught.value.code == ErrorCode.SOURCE_URL_EXPIRED
