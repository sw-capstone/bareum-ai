from hashlib import sha256

import pymupdf
import pytest

from .conftest import ApiCase

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("kind", ["blank", "scan", "partial", "blank_tail", "too_many"])
async def test_real_pdf_edge_cases_have_distinct_results(
    api_case: ApiCase, kind: str
) -> None:
    # Given
    with pymupdf.open() as document:
        page = document.new_page()
        if kind in {"partial", "blank_tail", "too_many"}:
            page.insert_text((72, 100), "Education was completed.")
        if kind in {"partial", "blank_tail"}:
            page = document.new_page()
        if kind in {"scan", "partial"}:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), 0)
            pixmap.clear_with(255)
            page.insert_image(page.rect, pixmap=pixmap)
        if kind == "too_many":
            for _ in range(60):
                document.new_page()
        body = document.tobytes()
    api_case.source.body = body
    source = api_case.request.source.model_copy(
        update={"size_bytes": len(body), "sha256": sha256(body).hexdigest()}
    )
    # When
    response = await api_case.post(
        api_case.request.model_copy(update={"source": source})
    )
    # Then
    if kind in {"blank", "scan", "too_many"}:
        assert response.status_code == 422
        expected = (
            "PAGE_LIMIT_EXCEEDED" if kind == "too_many" else "NO_EXTRACTABLE_TEXT"
        )
        assert response.json()["error"]["code"] == expected
    else:
        assert response.status_code == 200
        expected = "PARTIAL" if kind == "partial" else "COMPLETE"
        assert response.json()["extractionQuality"] == expected


async def test_corrupted_pdf_is_rejected_and_temporary_files_removed(
    api_case: ApiCase,
) -> None:
    # Given
    body = b"%PDF-1.7\nnot a valid document"
    api_case.source.body = body
    source = api_case.request.source.model_copy(
        update={"size_bytes": len(body), "sha256": sha256(body).hexdigest()}
    )
    # When
    response = await api_case.post(
        api_case.request.model_copy(update={"source": source})
    )
    # Then
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PDF_CORRUPTED"
    assert list(api_case.temporary_root.iterdir()) == []
