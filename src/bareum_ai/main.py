"""바름 AI 서비스의 HTTP 진입점."""

from typing import ClassVar, Final, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from bareum_ai.pdf_parsing.api.http_errors import register_error_handlers
from bareum_ai.pdf_parsing.api.router import create_router
from bareum_ai.pdf_parsing.runtime import PdfParsingRuntime


class HealthResponse(BaseModel):
    """HTTP 서버의 기동 상태를 나타내는 응답."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


def health() -> HealthResponse:
    """외부 서비스 호출 없이 HTTP 서버의 응답 가능 여부를 확인한다."""
    return HealthResponse()


def create_app(runtime: PdfParsingRuntime | None = None) -> FastAPI:
    parsing = runtime if runtime is not None else PdfParsingRuntime()
    application = FastAPI(title="바름 AI", lifespan=parsing.lifespan)
    register_error_handlers(application)
    application.include_router(create_router(parsing))
    application.get("/health", response_model=HealthResponse)(health)
    return application


app: Final[FastAPI] = create_app()
