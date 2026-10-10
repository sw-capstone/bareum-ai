"""바름 AI 서비스의 HTTP 진입점."""

from typing import ClassVar, Final, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """HTTP 서버의 기동 상태를 나타내는 응답."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


app: Final[FastAPI] = FastAPI(title="바름 AI")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """외부 서비스 호출 없이 HTTP 서버의 응답 가능 여부를 확인한다."""
    return HealthResponse()
