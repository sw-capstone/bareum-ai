import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pymupdf
from pydantic import ValidationError

from bareum_ai.dataset.parsing.parser import parse_pdf
from bareum_ai.pdf_parsing.api.responses import ParsedPdf
from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.validation import validate_extraction, validate_pdf
from bareum_ai.pdf_parsing.parser.worker_contract import (
    ParserInput,
    WorkerFailed,
    WorkerResult,
    WorkerSucceeded,
)
from bareum_ai.pdf_parsing.types import FailureDiagnostic, FailureStage


def execute(job: ParserInput) -> WorkerResult:
    pymupdf.TOOLS.mupdf_display_errors(on=False)
    pymupdf.TOOLS.mupdf_display_warnings(on=False)
    stage: FailureStage = "pdf_validation"
    try:
        with (
            Path(os.devnull).open("w") as quiet,
            redirect_stdout(quiet),
            redirect_stderr(quiet),
        ):
            validate_pdf(job.source_path, job.page_limit)
            stage = "parser"
            payload = parse_pdf(job.source_path)[0]
            stage = "result_validation"
            parsed = ParsedPdf.model_validate_json(json.dumps(payload, allow_nan=False))
        parsed = parsed.model_copy(
            update={"document": parsed.document | {"filename": job.original_filename}}
        )
        stage = "extraction_validation"
        return WorkerSucceeded(parsed=parsed, quality=validate_extraction(parsed, job))
    except PdfParseApiError as exc:
        return WorkerFailed(
            code=exc.code,
            diagnostic=exc.diagnostic
            or FailureDiagnostic(stage=stage, reason="validation_failed"),
        )
    except (OSError, ValidationError, RuntimeError, ValueError) as exc:
        return WorkerFailed(
            code=ErrorCode.PARSER_FAILED,
            diagnostic=FailureDiagnostic.from_exception(stage, exc),
        )


def main() -> int:
    manifest = Path(sys.argv[1])
    try:
        result = execute(ParserInput.model_validate_json(manifest.read_bytes()))
    except (OSError, ValidationError, RuntimeError, ValueError) as exc:
        result = WorkerFailed(
            code=ErrorCode.PARSER_FAILED,
            diagnostic=FailureDiagnostic.from_exception("worker_input", exc),
        )
    manifest.with_name("result.json").write_text(
        result.model_dump_json(), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
