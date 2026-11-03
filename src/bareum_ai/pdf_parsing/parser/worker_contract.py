from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import Field, TypeAdapter

from bareum_ai.pdf_parsing.api.responses import ParsedPdf
from bareum_ai.pdf_parsing.errors import ErrorCode
from bareum_ai.pdf_parsing.types import (
    ContractModel,
    ExtractionQuality,
    FailureDiagnostic,
)


class ParserInput(ContractModel):
    source_path: Path
    original_filename: str
    sha256: str
    page_limit: int


class WorkerSucceeded(ContractModel):
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    parsed: ParsedPdf
    quality: ExtractionQuality


class WorkerFailed(ContractModel):
    status: Literal["FAILED"] = "FAILED"
    code: ErrorCode
    diagnostic: FailureDiagnostic | None = None


type WorkerResult = Annotated[
    WorkerSucceeded | WorkerFailed, Field(discriminator="status")
]
WORKER_RESULT: Final[TypeAdapter[WorkerResult]] = TypeAdapter(WorkerResult)
