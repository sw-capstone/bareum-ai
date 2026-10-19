from pathlib import Path

import pymupdf
import pytest

from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.parser.validation import validate_pdf


@pytest.mark.parametrize("page_count", [1, 60])
def test_actual_pdf_is_accepted_regardless_of_extension(
    tmp_path: Path, page_count: int
) -> None:
    # Given
    path = tmp_path / "source.bin"
    with pymupdf.open() as document:
        for _ in range(page_count):
            document.new_page()
        document.save(path)
    # When
    actual_count = validate_pdf(path, 60)
    # Then
    assert actual_count == page_count


def test_pdf_over_sixty_pages_is_rejected_without_truncation(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        for _ in range(61):
            document.new_page()
        document.save(path)
    # When
    with pytest.raises(PdfParseApiError) as caught:
        validate_pdf(path, 60)
    # Then
    assert caught.value.code == ErrorCode.PAGE_LIMIT_EXCEEDED


def test_pdf_requiring_password_is_rejected(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "encrypted.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="example-owner",
            user_pw="example-user",
        )
    # When
    with pytest.raises(PdfParseApiError) as caught:
        validate_pdf(path, 60)
    # Then
    assert caught.value.code == ErrorCode.PDF_ENCRYPTED


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"%PDF-1.7\nbroken data", ErrorCode.PDF_CORRUPTED),
        (b"this is not a PDF", ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        (b"", ErrorCode.UNSUPPORTED_MEDIA_TYPE),
    ],
)
def test_pdf_extension_cannot_hide_invalid_content(
    tmp_path: Path, body: bytes, code: ErrorCode
) -> None:
    # Given
    path = tmp_path / "source.pdf"
    path.write_bytes(body)
    # When
    with pytest.raises(PdfParseApiError) as caught:
        validate_pdf(path, 60)
    # Then
    assert caught.value.code == code
