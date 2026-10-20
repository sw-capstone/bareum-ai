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


@dataclass(frozen=True, slots=True)
class ParserProcess:
    settings: PdfParsingSettings
    worker_module: str = "bareum_ai.pdf_parsing.parser.parser_worker"

    async def run(self, job: ParserInput) -> WorkerSucceeded:
        manifest = job.source_path.with_name("job.json")
        await anyio.Path(manifest).write_text(job.model_dump_json(), encoding="utf-8")
        try:
            async with await anyio.open_process(
                [sys.executable, "-m", self.worker_module, str(manifest)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ) as process:
                try:
                    with anyio.fail_after(self.settings.parser_timeout_seconds):
                        exit_code = await process.wait()
                finally:
                    with anyio.CancelScope(shield=True):
                        await self._stop(process)
            if exit_code != 0:
                raise PdfParseApiError(ErrorCode.PARSER_FAILED)
            output = await anyio.Path(manifest.with_name("result.json")).read_bytes()
            result = await anyio.to_thread.run_sync(WORKER_RESULT.validate_json, output)
        except TimeoutError:
            raise PdfParseApiError(ErrorCode.PARSER_TIMEOUT) from None
        except OSError, ValidationError:
            raise PdfParseApiError(ErrorCode.PARSER_FAILED) from None
        match result:
            case WorkerSucceeded():
                return result
            case WorkerFailed(code=code):
                raise PdfParseApiError(code)
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
