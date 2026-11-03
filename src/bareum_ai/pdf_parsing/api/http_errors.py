from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response

from bareum_ai.pdf_parsing.errors import (
    ERROR_DEFINITIONS,
    ErrorCode,
    ParseError,
    ParseErrorResponse,
    PdfParseApiError,
)


def parse_api_error_handler(_request: Request, exc: PdfParseApiError) -> Response:
    """예외 원문 대신 정의된 오류 응답을 반환한다."""
    body = ParseErrorResponse(
        report_id=exc.identity.report_id if exc.identity else None,
        parse_attempt_id=exc.identity.parse_attempt_id if exc.identity else None,
        error=ParseError.from_code(exc.code),
    )
    headers = {
        ErrorCode.UNAUTHORIZED: {"WWW-Authenticate": "Bearer"},
        ErrorCode.ATTEMPT_IN_PROGRESS: {"Retry-After": "3"},
        ErrorCode.CAPACITY_EXCEEDED: {"Retry-After": "3"},
    }.get(exc.code)
    return Response(
        content=body.model_dump_json(),
        status_code=ERROR_DEFINITIONS[exc.code].http_status,
        media_type="application/json",
        headers=headers,
    )


def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """검증 오류에 포함된 URL·서명·입력 본문을 응답하지 않는다."""
    code = (
        ErrorCode.MALFORMED_JSON
        if any(error["type"] == "json_invalid" for error in exc.errors())
        else ErrorCode.INVALID_REQUEST
    )
    return parse_api_error_handler(request, PdfParseApiError(code))


def register_error_handlers(app: FastAPI) -> None:
    app.exception_handler(PdfParseApiError)(parse_api_error_handler)
    app.exception_handler(RequestValidationError)(request_validation_error_handler)
