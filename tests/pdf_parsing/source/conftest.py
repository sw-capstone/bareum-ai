import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def source_bytes() -> bytes:
    return b"%PDF-1.7\nsource data"


@pytest.fixture
def source_request(request_json: str, source_bytes: bytes) -> PdfParseRequest:
    payload = json.loads(request_json)
    payload["source"]["sizeBytes"] = len(source_bytes)
    payload["source"]["sha256"] = sha256(source_bytes).hexdigest()
    payload["source"]["urlExpiresAt"] = "2099-01-01T00:00:00Z"
    return PdfParseRequest.model_validate_json(json.dumps(payload))


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 19, tzinfo=UTC)
