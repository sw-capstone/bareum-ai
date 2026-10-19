import anyio
import pytest

from bareum_ai.pdf_parsing.types import ParseAttemptId

from .conftest import ApiCase

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("budget", ["PARSER", "EXECUTION"])
async def test_timeout_releases_capacity_for_new_attempt(
    api_case: ApiCase, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    # Given
    monkeypatch.setenv(f"BAREUM_AI_{budget}_TIMEOUT_SECONDS", "0.02")
    monkeypatch.setenv("BAREUM_AI_PROCESS_TERMINATION_GRACE_SECONDS", "0.01")
    if budget == "EXECUTION":
        api_case.source.release = anyio.Event()
    # When
    with anyio.fail_after(5):
        response = await api_case.post()
        retried = await api_case.post(
            api_case.request.model_copy(
                update={"parse_attempt_id": ParseAttemptId(202)}
            )
        )
    # Then
    assert response.status_code == retried.status_code == 504
    assert response.json()["error"]["code"] == "PARSER_TIMEOUT"
    assert response.json()["error"]["action"] == "CREATE_NEW_ATTEMPT"
    assert retried.json()["parseAttemptId"] == 202
    assert api_case.source.calls == 2
    assert list(api_case.temporary_root.iterdir()) == []
