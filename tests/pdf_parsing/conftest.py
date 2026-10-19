from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bareum_ai.main import HealthResponse, health
from bareum_ai.pdf_parsing.api.http_errors import register_error_handlers
from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.api.security import require_internal_api_token
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.settings import (
    PdfParsingSettings,
    get_pdf_parsing_settings,
)


@pytest.fixture
def internal_token() -> str:
    return "example-internal-token"


@pytest.fixture(autouse=True)
def parsing_environment(
    monkeypatch: pytest.MonkeyPatch, internal_token: str
) -> Iterator[None]:
    for field in PdfParsingSettings.model_fields:
        monkeypatch.delenv(f"BAREUM_AI_{field.upper()}", raising=False)
    monkeypatch.setenv("BAREUM_AI_INTERNAL_API_TOKEN", internal_token)
    monkeypatch.setenv(
        "BAREUM_AI_S3_ALLOWED_HOST", "example-bucket.s3.ap-northeast-2.amazonaws.com"
    )
    monkeypatch.setenv("BAREUM_AI_S3_ALLOWED_KEY_PREFIX", "reports/")
    get_pdf_parsing_settings.cache_clear()
    yield
    get_pdf_parsing_settings.cache_clear()


@pytest.fixture
def request_json() -> str:
    return """{
      "reportId": 101,
      "parseAttemptId": 201,
      "source": {
        "objectKey": "reports/101/original.pdf",
        "originalFilename": "결과보고서.pdf",
        "sizeBytes": 455798,
        "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "downloadUrl": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/reports/101/original.pdf?signature=example",
        "urlExpiresAt": "2026-10-18T13:10:00Z"
      }
    }"""


@pytest.fixture
def parsing_client() -> Iterator[TestClient]:
    application = FastAPI()
    register_error_handlers(application)
    application.get("/health", response_model=HealthResponse)(health)

    @application.post("/internal/test-boundary")
    def request_boundary_probe(
        payload: PdfParseRequest,
        _authenticated: Annotated[None, Depends(require_internal_api_token)],
    ) -> PdfParseRequest:
        return payload

    @application.get("/internal/test-errors/{code}")
    def error_boundary_probe(code: ErrorCode) -> None:
        raise PdfParseApiError(code)

    with TestClient(application) as client:
        yield client
