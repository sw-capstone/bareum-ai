"""상태 확인 API의 HTTP 응답을 검증한다."""

from http import HTTPStatus

from fastapi.testclient import TestClient

from bareum_ai.main import HealthResponse, app


def test_health_returns_ok_when_app_is_running() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    payload = HealthResponse.model_validate_json(response.content)
    assert response.status_code == HTTPStatus.OK
    assert payload.status == "ok"


def test_health_rejects_post_when_only_get_is_supported() -> None:
    with TestClient(app) as client:
        response = client.post("/health")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.headers["allow"] == "GET"
