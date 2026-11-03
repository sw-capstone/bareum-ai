from hashlib import sha256
from pathlib import Path

import pymupdf
import pytest

from bareum_ai.pdf_parsing.parser.worker_contract import ParserInput


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def parser_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("parser-source") / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), "Education was completed. A survey is planned.")
        document.save(path)
    return path


@pytest.fixture
def parser_job(tmp_path: Path, parser_source: Path) -> ParserInput:
    path = tmp_path / "source.pdf"
    path.write_bytes(parser_source.read_bytes())
    return ParserInput(
        source_path=path,
        original_filename="결과보고서.pdf",
        sha256=sha256(path.read_bytes()).hexdigest(),
        page_limit=60,
    )
