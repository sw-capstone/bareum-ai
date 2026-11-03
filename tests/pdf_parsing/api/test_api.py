from datetime import UTC, datetime

import httpx2
import pytest

from bareum_ai.main import create_app
from bareum_ai.pdf_parsing.runtime import PdfParsingRuntime
from bareum_ai.pdf_parsing.types import ParseAttemptId

from .conftest import ApiCase

pytestmark = pytest.mark.anyio


async def test_completed_post_runs_download_and_real_parser_again(
    api_case: ApiCase,
) -> None:
    # Given / When
    first = await api_case.post()
    second = await api_case.post()
    # Then
    assert first.status_code == second.status_code == 200
    for response in (first, second):
        body = response.json()
        assert body["status"] == "SUCCEEDED"
        assert body["parsed"]["schema_version"] == "1.2.0"
        assert body["parsed"]["document"]["filename"] == "결과보고서.pdf"
        assert body["extractionQuality"] == "COMPLETE"
        assert body["error"] is None
        assert "stage" not in body and "resultExpiresAt" not in body
    assert api_case.source.calls == 2
    assert list(api_case.temporary_root.iterdir()) == []


@pytest.mark.parametrize("failure", ["expired", "temporary"])
async def test_retryable_failure_is_retried_with_new_attempt(
    api_case: ApiCase, failure: str
) -> None:
    # Given
    request = api_case.request
    if failure == "expired":
        source = request.source.model_copy(
            update={"url_expires_at": datetime(2000, 1, 1, tzinfo=UTC)}
        )
        request = request.model_copy(update={"source": source})
    else:
        api_case.source.status = 503
    # When
    failed = await api_case.post(request)
    api_case.source.status = 200
    retried = await api_case.post(
        api_case.request.model_copy(update={"parse_attempt_id": ParseAttemptId(202)})
    )
    # Then
    assert failed.status_code == 424
    expected_code = (
        "SOURCE_URL_EXPIRED"
        if failure == "expired"
        else "SOURCE_DOWNLOAD_TEMPORARY_FAILURE"
    )
    assert failed.json()["error"]["code"] == expected_code
    assert failed.json()["reportId"] == 101
    assert failed.json()["parseAttemptId"] == 201
    assert failed.json()["error"]["action"] == "CREATE_NEW_ATTEMPT"
    assert "status" not in failed.json()
    assert retried.status_code == 200
    assert retried.json()["parseAttemptId"] == 202
    assert api_case.source.calls == (1 if failure == "expired" else 2)


async def test_get_is_absent_before_and_after_application_restart(
    api_case: ApiCase,
) -> None:
    # Given
    await api_case.post()
    application = create_app(
        PdfParsingRuntime(
            lambda: httpx2.AsyncClient(
                transport=httpx2.MockTransport(api_case.source.respond)
            )
        )
    )
    # When
    queried = await api_case.client.get("/internal/v1/pdf-parses/201")
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=application),
            base_url="http://api.test",
            headers={"Authorization": "Bearer example-internal-token"},
        ) as client,
    ):
        restarted = ApiCase(
            client,
            api_case.source,
            api_case.request,
            api_case.temporary_root,
            application,
        )
        missing = await client.get("/internal/v1/pdf-parses/201")
        repeated = await restarted.post()
    # Then
    assert queried.status_code == missing.status_code == 404
    assert repeated.status_code == 200
    assert api_case.source.calls == 2


async def test_real_routes_authenticate_before_downloading(api_case: ApiCase) -> None:
    # Given / When
    response = await api_case.client.post(
        "/internal/v1/pdf-parses",
        json={},
        headers={"Authorization": "Bearer example-wrong"},
    )
    # Then
    assert response.status_code == 401
    assert api_case.source.calls == 0
