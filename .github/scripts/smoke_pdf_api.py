#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
# 실행: SMOKE_IMAGE=<이미지> bash .github/scripts/smoke-image.sh
# 설치된 프로젝트 의존성을 사용하며 Docker 검사 전용 앱은 S3 HTTP 응답만 대체한다.

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Final

import anyio
import httpx2
import pymupdf

from bareum_ai.main import create_app
from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.api.responses import SucceededResponse
from bareum_ai.pdf_parsing.runtime import PdfParsingRuntime
from bareum_ai.pdf_parsing.settings import get_pdf_parsing_settings
from bareum_ai.pdf_parsing.source_download import create_source_client


def source_bytes() -> bytes:
    supplied = Path("/smoke/source.pdf").read_bytes()
    if supplied:
        return supplied
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), "Education was completed. A survey is planned.")
        return document.tobytes(no_new_id=True)


_SOURCE: Final = source_bytes()


def source_client() -> httpx2.AsyncClient:
    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=httpx2.ByteStream(_SOURCE))

    return httpx2.AsyncClient(transport=httpx2.MockTransport(respond))


app: Final = create_app(PdfParsingRuntime(source_client))


def request_json(request: PdfParseRequest) -> str:
    payload = request.model_dump(mode="json")
    payload["source"]["downloadUrl"] = request.source.download_url.get_secret_value()
    return json.dumps(payload)


def parse_request() -> PdfParseRequest:
    return PdfParseRequest.model_validate_json(
        json.dumps(
            {
                "reportId": 101,
                "parseAttemptId": 201,
                "source": {
                    "objectKey": "reports/101/source.pdf",
                    "originalFilename": "image-smoke.pdf",
                    "sizeBytes": len(_SOURCE),
                    "sha256": sha256(_SOURCE).hexdigest(),
                    "downloadUrl": (
                        "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
                        "reports/101/source.pdf?signature=example"
                    ),
                    "urlExpiresAt": "2099-01-01T00:00:00Z",
                },
            }
        )
    )


async def main() -> None:
    settings = get_pdf_parsing_settings()
    async with create_source_client() as client:
        client.base_url = httpx2.URL("http://127.0.0.1:8000")
        client.timeout = httpx2.Timeout(connect=5, read=330, write=10, pool=10)
        client.headers.update(
            {
                "Authorization": (
                    f"Bearer {settings.internal_api_token.get_secret_value()}"
                ),
                "Content-Type": "application/json",
            }
        )
        # Given: a running app with a real child parser and a fixed source PDF.
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        payload = request_json(parse_request())
        # When: the same completed request is submitted again.
        response = await client.post("/internal/v1/pdf-parses", content=payload)
        replay = await client.post("/internal/v1/pdf-parses", content=payload)
        # Then: each request returns a new execution, without cached result fields.
        assert response.status_code == replay.status_code == 200
        result = SucceededResponse.model_validate_json(response.content)
        repeated = SucceededResponse.model_validate_json(replay.content)
        assert repeated.started_at >= result.completed_at > result.started_at
        for item, raw in ((result, response), (repeated, replay)):
            assert item.report_id == 101
            assert item.parse_attempt_id == 201
            assert item.parsed.document["sha256"] == sha256(_SOURCE).hexdigest()
            assert item.parsed.pages
            assert item.parsed.blocks
            assert "stage" not in raw.json()
            assert "resultExpiresAt" not in raw.json()
        # When / Then: status lookup is removed and POST still requires auth.
        queried = await client.get("/internal/v1/pdf-parses/201")
        assert queried.status_code == 404
        del client.headers["Authorization"]
        unauthorized = await client.post("/internal/v1/pdf-parses", content=payload)
        assert unauthorized.status_code == 401
        sys.stdout.write(
            json.dumps(
                {
                    "status": result.status,
                    "quality": result.extraction_quality,
                    "pages": len(result.parsed.pages),
                    "blocks": len(result.parsed.blocks),
                    "sequentialReparse": True,
                    "removedGetStatus": queried.status_code,
                    "unauthorizedStatus": unauthorized.status_code,
                }
            )
            + "\n"
        )


if __name__ == "__main__":
    anyio.run(main)
