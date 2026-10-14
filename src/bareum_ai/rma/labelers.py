"""라벨러 연결부. 실행기는 라벨러를 (system, user, schema, request) → 응답 문자열로만 다룬다.

- ReplayLabeler: 미리 정한 응답을 돌려준다. 테스트와 재현 실행에 쓴다.
- OpenAILabeler: OpenAI Responses API를 호출한다. API 키는 환경 변수로만 받는다.
"""

from collections.abc import Callable
import json
import os
import urllib.error
import urllib.request

API_KEY_ENV = "OPENAI_API_KEY"  # 키는 여기(환경 변수)로만 받는다. 코드·설정 파일에 적지 않는다
BASE_URL_ENV = "OPENAI_BASE_URL"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-6-sol"


class LabelerError(RuntimeError):
    """라벨러 호출 자체가 실패했다(네트워크·인증·응답 형식). 검증 실패와 구분한다."""


class ReplayLabeler:
    """respond(request, attempt)가 돌려준 값을 응답으로 쓴다. attempt는 같은 요청의 몇 번째 호출인지(1부터)."""

    def __init__(self, respond: Callable[[dict, int], str | dict], labeler_id: str = "replay"):
        self.respond = respond
        self.id = labeler_id
        self.settings: dict = {}
        self._attempts: dict[str, int] = {}

    def __call__(self, *, system: str, user: str, schema: dict, request: dict) -> str:
        key = request["request_id"]
        self._attempts[key] = self._attempts.get(key, 0) + 1
        value = self.respond(request, self._attempts[key])
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class OpenAILabeler:
    """Responses API의 구조화 출력(JSON 스키마)으로 응답을 받는다.

    키를 받기 전이라 실제 호출로 확인하지 않았다. 첫 시험 실행에서 요청·응답 형식을 확인한다.
    """

    def __init__(self, model: str = DEFAULT_MODEL, *, reasoning_effort: str | None = None, timeout: float = 600):
        self._key = os.environ.get(API_KEY_ENV)
        if not self._key:
            raise LabelerError(f"환경 변수 {API_KEY_ENV}에 API 키가 없습니다")
        self.base_url = os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")
        self.id = model
        self.timeout = timeout
        self.settings = {"model": model, "reasoning_effort": reasoning_effort, "structured_output": True}
        self.last_usage: dict | None = None  # 마지막 호출의 토큰 사용량(API 응답의 usage)

    def _body(self, system: str, user: str, schema: dict, name: str) -> dict:
        body = {
            "model": self.id,
            "instructions": system,
            "input": user,
            "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        }
        if self.settings["reasoning_effort"]:
            body["reasoning"] = {"effort": self.settings["reasoning_effort"]}
        return body

    def __call__(self, *, system: str, user: str, schema: dict, request: dict) -> str:
        name = request["schema_version"].replace("request", "response").replace("-", "_")
        data = json.dumps(self._body(system, user, schema, name)).encode()
        http = urllib.request.Request(
            f"{self.base_url}/responses",
            data=data,
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as error:
            raise LabelerError(f"API 오류 {error.code}: {error.read()[:500]!r}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise LabelerError(f"API 연결 실패: {error}") from error
        self.last_usage = payload.get("usage")
        texts = [
            part.get("text", "")
            for item in payload.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        ]
        if not texts:
            raise LabelerError(f"응답에 output_text가 없습니다: status={payload.get('status')}")
        return "".join(texts)
