from enum import StrEnum
from typing import Annotated, ClassVar, NewType

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel

ReportId = NewType("ReportId", int)
ParseAttemptId = NewType("ParseAttemptId", int)
type ReportIdentifier = Annotated[ReportId, Field(strict=True, gt=0, le=2**63 - 1)]
type AttemptIdentifier = Annotated[
    ParseAttemptId, Field(strict=True, gt=0, le=2**63 - 1)
]
type JsonObject = dict[str, JsonValue]


class ContractModel(BaseModel):
    """API 경계에서는 camelCase를 사용하고 정의하지 않은 필드를 거절한다."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class AttemptIdentity(ContractModel):
    report_id: ReportIdentifier
    parse_attempt_id: AttemptIdentifier


class ExtractionQuality(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
