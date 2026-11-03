import re
from functools import lru_cache
from typing import Annotated, ClassVar

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict

from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError


class PdfParsingSettings(BaseSettings):
    """비밀값과 원본 접근 범위는 필수이며 소스 파일의 기본값으로 대체하지 않는다."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="BAREUM_AI_",
        env_file=None,
        frozen=True,
        hide_input_in_errors=True,
        extra="forbid",
    )

    internal_api_token: SecretStr = Field(repr=False)
    s3_allowed_host: str
    s3_allowed_key_prefix: str
    max_file_size_bytes: Annotated[int, Field(gt=0, le=20_000_000)] = 20_000_000
    max_page_count: Annotated[int, Field(gt=0, le=60)] = 60
    max_concurrent_parses: Annotated[int, Field(gt=0)] = 1
    source_download_timeout_seconds: Annotated[
        float, Field(gt=0, allow_inf_nan=False)
    ] = 120.0
    parser_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 120.0
    execution_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = (
        300.0
    )
    process_termination_grace_seconds: Annotated[
        float, Field(gt=0, allow_inf_nan=False)
    ] = 2.0

    @field_validator("internal_api_token")
    @classmethod
    def require_token(cls, value: SecretStr) -> SecretStr:
        if (
            not value.get_secret_value().strip()
            or value.get_secret_value() == "replace-me"
        ):
            raise PydanticCustomError(
                "empty_internal_token", "내부 API 토큰이 필요합니다."
            )
        return value

    @field_validator("s3_allowed_host")
    @classmethod
    def require_exact_host(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", value):
            raise PydanticCustomError(
                "invalid_s3_host", "정확한 S3 호스트 이름이 필요합니다."
            )
        return value

    @field_validator("s3_allowed_key_prefix")
    @classmethod
    def require_key_prefix(cls, value: str) -> str:
        if not value.strip() or value.startswith("/") or not value.endswith("/"):
            raise PydanticCustomError(
                "invalid_s3_prefix", "슬래시로 끝나는 원본 키 접두사가 필요합니다."
            )
        return value


@lru_cache(maxsize=1)
def get_pdf_parsing_settings() -> PdfParsingSettings:
    """설정 오류의 입력값을 노출하지 않고 내부 API를 닫힌 상태로 둔다."""
    try:
        return PdfParsingSettings.model_validate({})
    except ValidationError:
        raise PdfParseApiError(ErrorCode.CONFIGURATION_UNAVAILABLE) from None
