from enum import StrEnum
from typing import Annotated, ClassVar, Literal, NewType, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
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


type FailureStage = Literal[
    "source_download",
    "execution",
    "pdf_validation",
    "parser",
    "result_validation",
    "extraction_validation",
    "table_validation",
    "worker_input",
    "manifest_write",
    "worker_start",
    "worker_wait",
    "worker_exit",
    "result_read",
]
type FailureReason = Literal[
    "invalid_schema",
    "io_error",
    "execution_error",
    "validation_failed",
    "invalid_table_schema",
    "empty_columns",
    "cell_out_of_bounds",
    "overlapping_cells",
    "invalid_result_references",
    "nonzero_exit",
    "timeout",
]


class FailureDiagnostic(ContractModel):
    stage: FailureStage
    reason: FailureReason

    @classmethod
    def from_exception(
        cls,
        stage: FailureStage,
        exc: OSError | ValidationError | RuntimeError | ValueError,
    ) -> Self:
        match exc:
            case ValidationError():
                reason: FailureReason = "invalid_schema"
            case OSError():
                reason = "io_error"
            case RuntimeError() | ValueError():
                reason = "execution_error"
            case unreachable:
                assert_never(unreachable)
        return cls(stage=stage, reason=reason)
