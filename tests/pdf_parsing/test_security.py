from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from bareum_ai.pdf_parsing.errors import (
    ErrorAction,
    ErrorCode,
    ParseErrorResponse,
)


@pytest.mark.parametrize(
    "authorization", [None, "Basic example", "Bearer example-wrong", "Bearer"]
)
def test_internal_request_rejects_missing_or_invalid_token(
    parsing_client: TestClient,
    request_json: str,
    authorization: str | None,
) -> None:
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization

    response = parsing_client.post(
        "/internal/test-boundary", content=request_json, headers=headers
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["www-authenticate"] == "Bearer"
    assert (
        ParseErrorResponse.model_validate_json(response.content).error.code
        == ErrorCode.UNAUTHORIZED
    )


def test_internal_request_accepts_configured_service_token(
    parsing_client: TestClient,
    request_json: str,
    internal_token: str,
) -> None:
    response = parsing_client.post(
        "/internal/test-boundary",
        content=request_json,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {internal_token}",
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["parseAttemptId"] == 201


@pytest.mark.parametrize(
    "case",
    [
        ('{"source":', HTTPStatus.BAD_REQUEST, ErrorCode.MALFORMED_JSON),
        (
            '{"source":{"downloadUrl":"https://example.com/?signature=example-private"}}',
            HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorCode.INVALID_REQUEST,
        ),
    ],
)
def test_request_validation_uses_safe_error_envelope(
    parsing_client: TestClient,
    internal_token: str,
    case: tuple[str, HTTPStatus, ErrorCode],
) -> None:
    content, expected_status, expected_code = case
    response = parsing_client.post(
        "/internal/test-boundary",
        content=content,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {internal_token}",
        },
    )

    body = ParseErrorResponse.model_validate_json(response.content)
    assert response.status_code == expected_status
    assert body.error.code == expected_code
    assert "example-private" not in response.text
    assert internal_token not in response.text


@pytest.mark.parametrize(
    "case",
    [
        (
            ErrorCode.SOURCE_URL_EXPIRED,
            HTTPStatus.FAILED_DEPENDENCY,
            ErrorAction.CREATE_NEW_ATTEMPT,
        ),
        (
            ErrorCode.PARSER_TIMEOUT,
            HTTPStatus.GATEWAY_TIMEOUT,
            ErrorAction.CREATE_NEW_ATTEMPT,
        ),
        (
            ErrorCode.CAPACITY_EXCEEDED,
            HTTPStatus.SERVICE_UNAVAILABLE,
            ErrorAction.CREATE_NEW_ATTEMPT,
        ),
        (
            ErrorCode.ATTEMPT_IN_PROGRESS,
            HTTPStatus.CONFLICT,
            ErrorAction.CREATE_NEW_ATTEMPT,
        ),
        (
            ErrorCode.FILE_TOO_LARGE,
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            ErrorAction.STOP,
        ),
        (
            ErrorCode.CONFIGURATION_UNAVAILABLE,
            HTTPStatus.SERVICE_UNAVAILABLE,
            ErrorAction.STOP,
        ),
    ],
)
def test_known_errors_have_http_status_and_safe_action(
    parsing_client: TestClient,
    case: tuple[ErrorCode, HTTPStatus, ErrorAction],
) -> None:
    code, expected_status, expected_action = case
    response = parsing_client.get(f"/internal/test-errors/{code.value}")

    body = ParseErrorResponse.model_validate_json(response.content)
    assert response.status_code == expected_status
    assert body.error.code == code
    assert body.error.action == expected_action


def test_missing_internal_configuration_rejects_request_without_exposing_token(
    parsing_client: TestClient,
    request_json: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAREUM_AI_INTERNAL_API_TOKEN")

    response = parsing_client.post(
        "/internal/test-boundary",
        content=request_json,
        headers={"Content-Type": "application/json", "Authorization": "Bearer example"},
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert (
        ParseErrorResponse.model_validate_json(response.content).error.code
        == ErrorCode.CONFIGURATION_UNAVAILABLE
    )


def test_health_remains_available_without_internal_configuration(
    parsing_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAREUM_AI_INTERNAL_API_TOKEN")

    response = parsing_client.get("/health")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}
