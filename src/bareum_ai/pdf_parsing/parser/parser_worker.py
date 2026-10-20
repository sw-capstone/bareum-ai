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


def execute(job: ParserInput) -> WorkerResult:
    pymupdf.TOOLS.mupdf_display_errors(on=False)
    pymupdf.TOOLS.mupdf_display_warnings(on=False)
    with (
        Path(os.devnull).open("w") as quiet,
        redirect_stdout(quiet),
        redirect_stderr(quiet),
    ):
        validate_pdf(job.source_path, job.page_limit)
        payload = parse_pdf(job.source_path)[0]
        parsed = ParsedPdf.model_validate_json(json.dumps(payload, allow_nan=False))
    parsed = parsed.model_copy(
        update={"document": parsed.document | {"filename": job.original_filename}}
    )
    return WorkerSucceeded(parsed=parsed, quality=validate_extraction(parsed, job))


def main() -> int:
    manifest = Path(sys.argv[1])
    try:
        result = execute(ParserInput.model_validate_json(manifest.read_bytes()))
    except PdfParseApiError as exc:
        result = WorkerFailed(code=exc.code)
    except OSError, ValidationError, RuntimeError, ValueError:
        result = WorkerFailed(code=ErrorCode.PARSER_FAILED)
    manifest.with_name("result.json").write_text(
        result.model_dump_json(), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
