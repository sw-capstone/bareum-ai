import subprocess
import sys
from dataclasses import dataclass
from typing import assert_never

import anyio
from anyio.abc import Process
from pydantic import ValidationError

from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.worker_contract import (
    WORKER_RESULT,
    ParserInput,
    WorkerFailed,
    WorkerSucceeded,
)
from bareum_ai.pdf_parsing.settings import PdfParsingSettings
from bareum_ai.pdf_parsing.types import FailureDiagnostic, FailureStage


@dataclass(frozen=True, slots=True)
class ParserProcess:
    settings: PdfParsingSettings
    worker_module: str = "bareum_ai.pdf_parsing.parser.parser_worker"

    async def run(self, job: ParserInput) -> WorkerSucceeded:
        manifest = job.source_path.with_name("job.json")
        stage: FailureStage = "manifest_write"
        try:
            await anyio.Path(manifest).write_text(job.model_dump_json(), encoding="utf-8")
            stage = "worker_start"
            async with await anyio.open_process(
                [sys.executable, "-m", self.worker_module, str(manifest)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ) as process:
                stage = "worker_wait"
                try:
                    with anyio.fail_after(self.settings.parser_timeout_seconds):
                        exit_code = await process.wait()
                finally:
                    with anyio.CancelScope(shield=True):
                        await self._stop(process)
            if exit_code != 0:
                raise PdfParseApiError(
                    ErrorCode.PARSER_FAILED,
                    diagnostic=FailureDiagnostic(
                        stage="worker_exit", reason="nonzero_exit"
                    ),
                )
            stage = "result_read"
            output = await anyio.Path(manifest.with_name("result.json")).read_bytes()
            stage = "result_validation"
            result = await anyio.to_thread.run_sync(WORKER_RESULT.validate_json, output)
        except TimeoutError:
            raise PdfParseApiError(
                ErrorCode.PARSER_TIMEOUT,
                diagnostic=FailureDiagnostic(stage=stage, reason="timeout"),
            ) from None
        except (OSError, ValidationError) as exc:
            raise PdfParseApiError(
                ErrorCode.PARSER_FAILED,
                diagnostic=FailureDiagnostic.from_exception(stage, exc),
            ) from None
        match result:
            case WorkerSucceeded():
                return result
            case WorkerFailed(code=code, diagnostic=diagnostic):
                raise PdfParseApiError(code, diagnostic=diagnostic)
            case unreachable:
                assert_never(unreachable)

    async def _stop(self, process: Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            await process.wait()
            return
        with anyio.move_on_after(self.settings.process_termination_grace_seconds):
            await process.wait()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                await process.wait()
                return
            await process.wait()
