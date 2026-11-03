import secrets
from typing import Annotated, Final

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from bareum_ai.pdf_parsing.errors import ErrorCode, PdfParseApiError
from bareum_ai.pdf_parsing.settings import PdfParsingSettings, get_pdf_parsing_settings

INTERNAL_BEARER: Final = HTTPBearer(auto_error=False, scheme_name="InternalApiToken")


def require_internal_api_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(INTERNAL_BEARER)
    ],
    settings: Annotated[PdfParsingSettings, Depends(get_pdf_parsing_settings)],
) -> None:
    """토큰 원문을 출력하지 않고 상수 시간 비교로 내부 호출을 인증한다."""
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode("utf-8"),
        settings.internal_api_token.get_secret_value().encode("utf-8"),
    ):
        raise PdfParseApiError(ErrorCode.UNAUTHORIZED)
