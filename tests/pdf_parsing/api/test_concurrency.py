import json

import anyio
import httpx2
import pytest
from starlette.types import Message, Scope

from bareum_ai.pdf_parsing.types import ParseAttemptId, ReportId

from .conftest import ApiCase

pytestmark = pytest.mark.anyio


async def test_active_identities_are_conflicts_and_distinct_report_hits_capacity(
    api_case: ApiCase,
) -> None:
    # Given
    api_case.source.release = anyio.Event()
    responses: list[httpx2.Response] = []

    async def first_request() -> None:
        responses.append(await api_case.post())

    # When
    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(first_request)
            await api_case.source.entered.wait()
            duplicate = await api_case.post()
            same_report = await api_case.post(
                api_case.request.model_copy(
                    update={"parse_attempt_id": ParseAttemptId(202)}
                )
            )
            same_attempt = await api_case.post(
                api_case.request.model_copy(update={"report_id": ReportId(102)})
            )
            other = await api_case.post(
                api_case.request.model_copy(
                    update={
                        "report_id": ReportId(102),
                        "parse_attempt_id": ParseAttemptId(202),
                    }
                )
            )
            api_case.source.release.set()
    # Then
    for response in (duplicate, same_report, same_attempt):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ATTEMPT_IN_PROGRESS"
        assert response.headers["retry-after"] == "3"
    assert other.status_code == 503
    assert other.json()["error"]["code"] == "CAPACITY_EXCEEDED"
    assert other.headers["retry-after"] == "3"
    assert responses[0].status_code == 200
    assert api_case.source.calls == 1


async def test_cancelled_post_releases_capacity_and_removes_temporary_files(
    api_case: ApiCase,
) -> None:
    # Given
    api_case.source.release = anyio.Event()
    caller_exited = anyio.Event()
    caller_scope = anyio.CancelScope()

    async def first_request() -> None:
        with caller_scope:
            await api_case.post()
        caller_exited.set()

    # When
    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(first_request)
            await api_case.source.entered.wait()
            caller_scope.cancel()
            await caller_exited.wait()
        remaining = list(api_case.temporary_root.iterdir())
        api_case.source.release.set()
        response = await api_case.post(
            api_case.request.model_copy(
                update={"parse_attempt_id": ParseAttemptId(202)}
            )
        )
    # Then
    assert remaining == []
    assert response.status_code == 200
    assert api_case.source.calls == 2


async def test_http_disconnect_stops_download_without_cancelling_the_server_task(
    api_case: ApiCase,
) -> None:
    api_case.source.release = anyio.Event()
    caller_exited = anyio.Event()
    messages: list[Message] = []
    payload = api_case.request.model_dump(mode="json")
    payload["source"]["downloadUrl"] = (
        api_case.request.source.download_url.get_secret_value()
    )
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/internal/v1/pdf-parses",
        "raw_path": b"/internal/v1/pdf-parses",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", api_case.client.headers["Authorization"].encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("api.test", 80),
    }
    send, receive = anyio.create_memory_object_stream[Message](2)

    async def record(message: Message) -> None:
        messages.append(message)

    async def call() -> None:
        await api_case.application(scope, receive.receive, record)
        caller_exited.set()

    async with send, receive:
        await send.send(
            {
                "type": "http.request",
                "body": json.dumps(payload).encode(),
                "more_body": False,
            }
        )
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(call)
                await api_case.source.entered.wait()
                await send.send({"type": "http.disconnect"})
                await caller_exited.wait()
    remaining = list(api_case.temporary_root.iterdir())
    api_case.source.release.set()
    retried = await api_case.post(
        api_case.request.model_copy(update={"parse_attempt_id": ParseAttemptId(202)})
    )

    assert messages[0]["status"] == 499
    assert remaining == []
    assert retried.status_code == 200
    assert api_case.source.calls == 2
