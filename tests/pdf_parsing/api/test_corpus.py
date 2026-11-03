import os
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Final

import anyio
import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from .conftest import ApiCase

REPRESENTATIVE_DOCUMENT_IDS: Final = (
    "doc_645bfd07e6ac41ec",  # 병합 셀이 있는 표
    "doc_48c14cb84e2fea23",  # 결재 영역과 제목·절 구조
    "doc_5366ca993a75bb46",  # 도식 노드와 방향 연결
)


class CorpusRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    document_id: str
    pdf_path: Path


def corpus_paths() -> tuple[Path, ...]:
    root = os.environ.get("BAREUM_PARSER_CORPUS")
    if root is None:
        return ()
    manifest = Path(root) / "reports/parser_final_validation/documents.json"
    records = TypeAdapter(tuple[CorpusRecord, ...]).validate_json(manifest.read_bytes())
    paths = {record.document_id: record.pdf_path for record in records}
    return tuple(paths[identity] for identity in REPRESENTATIVE_DOCUMENT_IDS)


@pytest.mark.anyio
@pytest.mark.parametrize("source_path", corpus_paths())
async def test_user_pdf_passes_real_api_and_child_process(
    api_case: ApiCase, source_path: Path
) -> None:
    # Given
    body = await anyio.Path(source_path).read_bytes()
    api_case.source.body = body
    source = api_case.request.source.model_copy(
        update={
            "original_filename": source_path.name,
            "size_bytes": len(body),
            "sha256": sha256(body).hexdigest(),
        }
    )
    # When
    response = await api_case.post(
        api_case.request.model_copy(update={"source": source})
    )
    # Then
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["parsed"]["document"]["filename"] == source_path.name
    assert result["parsed"]["document"]["sha256"] == sha256(body).hexdigest()
    assert result["status"] == "SUCCEEDED"
