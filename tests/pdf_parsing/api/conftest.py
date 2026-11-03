import json
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import anyio
import httpx2
import pymupdf
import pytest
from fastapi import FastAPI

from bareum_ai.main import create_app
from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.runtime import PdfParsingRuntime


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class SourceServer:
    """Accumulate source calls and control download completion for API scenarios."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.status = 200
        self.calls = 0
        self.entered = anyio.Event()
        self.release = anyio.Event()
        self.release.set()

    async def respond(self, _request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return httpx2.Response(self.status, stream=httpx2.ByteStream(self.body))


@dataclass(frozen=True, slots=True)
class ApiCase:
    client: httpx2.AsyncClient
    source: SourceServer
    request: PdfParseRequest
    temporary_root: Path
    application: FastAPI

    async def post(self, request: PdfParseRequest | None = None) -> httpx2.Response:
        value = request if request is not None else self.request
        payload = value.model_dump(mode="json")
        payload["source"]["downloadUrl"] = value.source.download_url.get_secret_value()
        return await self.client.post("/internal/v1/pdf-parses", json=payload)


@pytest.fixture
async def api_case(
    request_json: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[ApiCase]:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), "Education was completed. A survey is planned.")
        body = document.tobytes()
    source = SourceServer(body)
    payload = json.loads(request_json)
    payload["source"].update(
        {
            "sizeBytes": len(body),
            "sha256": sha256(body).hexdigest(),
            "urlExpiresAt": "2099-01-01T00:00:00Z",
        }
    )
    request = PdfParseRequest.model_validate_json(json.dumps(payload))

    def source_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(source.respond))

    runtime = PdfParsingRuntime(source_client)
    application = create_app(runtime)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=application),
            base_url="http://api.test",
            headers={"Authorization": "Bearer example-internal-token"},
        ) as client,
    ):
        yield ApiCase(client, source, request, tmp_path, application)
