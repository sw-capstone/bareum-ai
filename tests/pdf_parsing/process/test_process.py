import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

import anyio
import pytest
from anyio.abc import Process
from anyio.streams.buffered import BufferedByteReceiveStream

from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.parser_process import ParserProcess
from bareum_ai.pdf_parsing.parser.worker_contract import ParserInput
from bareum_ai.pdf_parsing.settings import PdfParsingSettings
from bareum_ai.pdf_parsing.types import ExtractionQuality

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class WorkerObservation:
    ready: anyio.Event
    pids: list[int]


@pytest.fixture
def observed_worker(monkeypatch: pytest.MonkeyPatch) -> WorkerObservation:
    observation = WorkerObservation(anyio.Event(), [])
    open_process = anyio.open_process

    async def observe(
        command: Sequence[str], *, stdin: int, stdout: int, stderr: int
    ) -> Process:
        process = await open_process(
            command, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr
        )
        assert process.stdout is not None
        with anyio.fail_after(5):
            marker = await BufferedByteReceiveStream(process.stdout).receive_until(
                b"\n", 64
            )
        assert marker == b"READY"
        observation.pids.append(process.pid)
        observation.ready.set()
        return process

    monkeypatch.setattr(anyio, "open_process", observe)
    return observation


async def test_real_worker_extracts_pdf_and_preserves_original_filename(
    parser_job: ParserInput,
) -> None:
    # Given / When
    result = await ParserProcess(PdfParsingSettings()).run(parser_job)
    # Then
    assert result.parsed.document["filename"] == "결과보고서.pdf"
    assert result.parsed.document["sha256"] == parser_job.sha256
    assert result.quality == ExtractionQuality.COMPLETE
    assert any("Education" in block["text"] for block in result.parsed.blocks)


@pytest.mark.parametrize("mode", ["crash", "invalid"])
async def test_crashed_worker_or_invalid_output_is_a_safe_failure(
    parser_job: ParserInput, mode: str
) -> None:
    # Given
    job = parser_job.model_copy(update={"original_filename": mode})
    runner = ParserProcess(
        PdfParsingSettings(), "tests.pdf_parsing.process.fixture_worker"
    )
    # When
    with pytest.raises(PdfParseApiError) as caught:
        await runner.run(job)
    # Then
    assert caught.value.code == ErrorCode.PARSER_FAILED


async def test_timeout_kills_and_reaps_worker_ignoring_sigterm(
    parser_job: ParserInput, observed_worker: WorkerObservation
) -> None:
    # Given
    settings = PdfParsingSettings(
        parser_timeout_seconds=0.02, process_termination_grace_seconds=0.01
    )
    runner = ParserProcess(settings, "tests.pdf_parsing.process.fixture_worker")
    # When
    with pytest.raises(PdfParseApiError) as caught:
        await runner.run(parser_job)
    # Then
    assert caught.value.code == ErrorCode.PARSER_TIMEOUT
    assert len(observed_worker.pids) == 1
    assert observed_worker.pids[0] != os.getpid()
    with pytest.raises(ProcessLookupError):
        os.kill(observed_worker.pids[0], 0)


async def test_cancellation_returns_only_after_worker_has_exited(
    parser_job: ParserInput, observed_worker: WorkerObservation
) -> None:
    # Given
    settings = PdfParsingSettings(process_termination_grace_seconds=0.01)
    runner = ParserProcess(settings, "tests.pdf_parsing.process.fixture_worker")
    # When
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(runner.run, parser_job)
            await observed_worker.ready.wait()
            tasks.cancel_scope.cancel()
    # Then
    assert len(observed_worker.pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(observed_worker.pids[0], 0)
