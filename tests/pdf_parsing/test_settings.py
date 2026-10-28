import pytest
from pydantic import ValidationError

from bareum_ai.pdf_parsing.settings import PdfParsingSettings, get_pdf_parsing_settings


def test_limits_are_loaded_from_approved_defaults() -> None:
    settings = PdfParsingSettings()

    assert settings.max_file_size_bytes == 20_000_000
    assert settings.max_page_count == 60
    assert settings.max_concurrent_parses == 1


def test_environment_can_lower_file_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAREUM_AI_MAX_FILE_SIZE_BYTES", "10000000")

    settings = PdfParsingSettings()

    assert settings.max_file_size_bytes == 10_000_000


def test_cached_loader_reads_and_validates_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BAREUM_AI_INTERNAL_API_TOKEN", "example-alternate-token")
    monkeypatch.setenv("BAREUM_AI_S3_ALLOWED_HOST", "alternate.s3.amazonaws.com")
    monkeypatch.setenv("BAREUM_AI_S3_ALLOWED_KEY_PREFIX", "uploads/")
    monkeypatch.setenv("BAREUM_AI_MAX_FILE_SIZE_BYTES", "10000000")

    settings = get_pdf_parsing_settings()

    assert settings.internal_api_token.get_secret_value() == "example-alternate-token"
    assert settings.s3_allowed_host == "alternate.s3.amazonaws.com"
    assert settings.s3_allowed_key_prefix == "uploads/"
    assert settings.max_file_size_bytes == 10_000_000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_FILE_SIZE_BYTES", "20000001"),
        ("MAX_PAGE_COUNT", "61"),
        ("MAX_CONCURRENT_PARSES", "0"),
        ("SOURCE_DOWNLOAD_TIMEOUT_SECONDS", "0"),
        ("SOURCE_DOWNLOAD_TIMEOUT_SECONDS", "inf"),
        ("SOURCE_DOWNLOAD_TIMEOUT_SECONDS", "nan"),
        ("INTERNAL_API_TOKEN", ""),
        ("INTERNAL_API_TOKEN", "   "),
        ("INTERNAL_API_TOKEN", "replace-me"),
        ("S3_ALLOWED_HOST", "*.amazonaws.com"),
        ("S3_ALLOWED_HOST", "https://example.com"),
        ("S3_ALLOWED_KEY_PREFIX", ""),
        ("S3_ALLOWED_KEY_PREFIX", "/reports/"),
        ("S3_ALLOWED_KEY_PREFIX", "reports"),
    ],
)
def test_configuration_rejects_unsafe_or_inconsistent_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(f"BAREUM_AI_{name}", value)

    with pytest.raises(ValidationError):
        PdfParsingSettings()


def test_configuration_representation_hides_internal_token() -> None:
    settings = PdfParsingSettings()

    assert settings.internal_api_token.get_secret_value() not in repr(settings)
    assert (
        settings.internal_api_token.get_secret_value() not in settings.model_dump_json()
    )
