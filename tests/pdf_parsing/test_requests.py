import json

import pytest
from pydantic import ValidationError

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest


def test_camel_case_request_is_parsed_with_original_source_metadata(
    request_json: str,
) -> None:
    request = PdfParseRequest.model_validate_json(request_json)

    assert request.report_id == 101
    assert request.parse_attempt_id == 201
    assert request.source.original_filename == "결과보고서.pdf"
    assert request.source.size_bytes == 455798


@pytest.mark.parametrize("report_id", [True, "101", 0, -1, 2**63])
def test_report_identifier_rejects_non_long_positive_integers(
    request_json: str,
    report_id: bool | str | int,
) -> None:
    payload = json.loads(request_json)
    payload["reportId"] = report_id

    with pytest.raises(ValidationError):
        PdfParseRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("checksum", ["a" * 63, "g" * 64, "a" * 65])
def test_sha256_rejects_invalid_hexadecimal_digest(
    request_json: str, checksum: str
) -> None:
    payload = json.loads(request_json)
    payload["source"]["sha256"] = checksum

    with pytest.raises(ValidationError):
        PdfParseRequest.model_validate_json(json.dumps(payload))


def test_sha256_normalizes_hexadecimal_case_for_identity_comparison(
    request_json: str,
) -> None:
    payload = json.loads(request_json)
    payload["source"]["sha256"] = "A" * 64

    request = PdfParseRequest.model_validate_json(json.dumps(payload))

    assert request.source.sha256 == "a" * 64


@pytest.mark.parametrize(
    "field", ["maxFileSizeBytes", "maxPageCount", "parserSettings"]
)
def test_request_cannot_override_server_processing_policy(
    request_json: str, field: str
) -> None:
    payload = json.loads(request_json)
    payload[field] = 100_000_000

    with pytest.raises(ValidationError):
        PdfParseRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "url",
    [
        "http://example-bucket.s3.ap-northeast-2.amazonaws.com/file.pdf",
        "https://user:example@example.com/file.pdf",
        "https://example.com:8443/file.pdf",
        "https://example.com/file.pdf#fragment",
        "https://example.com:invalid/file.pdf",
    ],
)
def test_download_url_rejects_unsupported_access_forms(
    request_json: str, url: str
) -> None:
    payload = json.loads(request_json)
    payload["source"]["downloadUrl"] = url

    with pytest.raises(ValidationError):
        PdfParseRequest.model_validate_json(json.dumps(payload))


def test_source_expiry_requires_timezone(request_json: str) -> None:
    payload = json.loads(request_json)
    payload["source"]["urlExpiresAt"] = "2026-10-18T13:10:00"

    with pytest.raises(ValidationError):
        PdfParseRequest.model_validate_json(json.dumps(payload))


def test_download_url_is_hidden_from_request_representation(request_json: str) -> None:
    request = PdfParseRequest.model_validate_json(request_json)

    assert "signature=example" not in repr(request)
    assert "signature=example" not in request.model_dump_json()
    assert request.source.download_url.get_secret_value().endswith("signature=example")
