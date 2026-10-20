from typing import ClassVar, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    TypeAdapter,
    model_validator,
)
from pydantic_core import PydanticCustomError

from bareum_ai.pdf_parsing.types import AttemptIdentity, ExtractionQuality, JsonObject

_JSON_EXTENSIONS: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)


class ParsedPdf(BaseModel, frozen=True):
    """1.2.0 추출 JSON의 내부 필드와 추가 필드를 재해석 없이 보존한다."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    schema_version: Literal["1.2.0"]
    document: JsonObject
    parser: JsonObject
    pages: tuple[JsonObject, ...]
    sections: tuple[JsonObject, ...]
    blocks: tuple[JsonObject, ...]
    layout_regions: tuple[JsonObject, ...]
    warnings: tuple[JsonObject, ...]

    @model_validator(mode="after")
    def require_json_extensions(self) -> Self:
        if self.model_extra is not None:
            _ = _JSON_EXTENSIONS.validate_python(self.model_extra)
        return self


class SucceededResponse(AttemptIdentity):
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    started_at: AwareDatetime
    completed_at: AwareDatetime
    extraction_quality: ExtractionQuality
    parsed: ParsedPdf
    error: None = None

    @model_validator(mode="after")
    def require_chronological_completion(self) -> Self:
        if self.completed_at < self.started_at:
            raise PydanticCustomError(
                "invalid_completion_time", "완료 시각이 시작 시각보다 빠릅니다."
            )
        return self
