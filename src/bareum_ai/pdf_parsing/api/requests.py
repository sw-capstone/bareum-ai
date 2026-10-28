from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, SecretStr, StringConstraints, field_validator
from pydantic_core import PydanticCustomError

from bareum_ai.pdf_parsing.types import AttemptIdentity, ContractModel


class PdfSource(ContractModel):
    """원본 식별 정보와 갱신 가능한 다운로드 자격을 함께 받는다."""

    object_key: Annotated[str, StringConstraints(min_length=1)]
    original_filename: Annotated[str, StringConstraints(min_length=1)]
    size_bytes: Annotated[int, Field(strict=True, gt=0)]
    sha256: Annotated[
        str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$", to_lower=True)
    ]
    download_url: SecretStr = Field(repr=False)
    url_expires_at: AwareDatetime

    @field_validator("object_key", "original_filename")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise PydanticCustomError(
                "empty_source_metadata", "원본 정보가 비어 있습니다."
            )
        return value

    @field_validator("download_url")
    @classmethod
    def require_https_download(cls, value: SecretStr) -> SecretStr:
        """서명 값을 출력하지 않고 HTTPS URL의 기본 형식을 확인한다."""
        try:
            url = urlsplit(value.get_secret_value())
            port = url.port
        except ValueError:
            raise PydanticCustomError(
                "invalid_source_url", "원본 URL 형식이 잘못되었습니다."
            ) from None
        if (
            url.scheme != "https"
            or not url.hostname
            or port not in {None, 443}
            or url.username is not None
            or url.password is not None
            or bool(url.fragment)
        ):
            raise PydanticCustomError(
                "invalid_source_url", "HTTPS 원본 URL이 필요합니다."
            )
        return value


class PdfParseRequest(AttemptIdentity):
    """파일 제한과 파서 설정은 요청자가 지정할 수 없다."""

    source: PdfSource
