"""파싱 오류 코드와 호출자의 다음 행동을 HTTP 경계에 연결한다."""

from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from types import MappingProxyType
from typing import Final, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from bareum_ai.pdf_parsing.types import (
    AttemptIdentifier,
    AttemptIdentity,
    ContractModel,
    ReportIdentifier,
)


class ErrorAction(StrEnum):
    CREATE_NEW_ATTEMPT = "CREATE_NEW_ATTEMPT"
    FIX_REQUEST = "FIX_REQUEST"
    STOP = "STOP"


class ErrorCode(StrEnum):
    MALFORMED_JSON = "MALFORMED_JSON"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    ATTEMPT_IN_PROGRESS = "ATTEMPT_IN_PROGRESS"
    SOURCE_URL_EXPIRED = "SOURCE_URL_EXPIRED"
    SOURCE_DOWNLOAD_TEMPORARY_FAILURE = "SOURCE_DOWNLOAD_TEMPORARY_FAILURE"
    SOURCE_ACCESS_DENIED = "SOURCE_ACCESS_DENIED"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    PAGE_LIMIT_EXCEEDED = "PAGE_LIMIT_EXCEEDED"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_CORRUPTED = "PDF_CORRUPTED"
    NO_EXTRACTABLE_TEXT = "NO_EXTRACTABLE_TEXT"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    PARSER_TIMEOUT = "PARSER_TIMEOUT"
    PARSER_FAILED = "PARSER_FAILED"
    CONFIGURATION_UNAVAILABLE = "CONFIGURATION_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """전송 상태와 안전한 기본 오류 설명."""

    http_status: HTTPStatus
    action: ErrorAction
    message: str


ERROR_DEFINITIONS: Final = MappingProxyType(
    {
        ErrorCode.MALFORMED_JSON: ErrorDefinition(
            HTTPStatus.BAD_REQUEST,
            ErrorAction.FIX_REQUEST,
            "JSON 형식이 잘못되었습니다.",
        ),
        ErrorCode.INVALID_REQUEST: ErrorDefinition(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorAction.FIX_REQUEST,
            "파싱 요청 형식이 잘못되었습니다.",
        ),
        ErrorCode.UNAUTHORIZED: ErrorDefinition(
            HTTPStatus.UNAUTHORIZED, ErrorAction.STOP, "내부 API 인증이 필요합니다."
        ),
        ErrorCode.ATTEMPT_IN_PROGRESS: ErrorDefinition(
            HTTPStatus.CONFLICT,
            ErrorAction.CREATE_NEW_ATTEMPT,
            "해당 보고서 또는 시도의 파싱이 진행 중입니다. 종료 후 새 시도로 요청하세요.",
        ),
        ErrorCode.SOURCE_URL_EXPIRED: ErrorDefinition(
            HTTPStatus.FAILED_DEPENDENCY,
            ErrorAction.CREATE_NEW_ATTEMPT,
            "원본 접근 URL을 새로 발급해야 합니다.",
        ),
        ErrorCode.SOURCE_DOWNLOAD_TEMPORARY_FAILURE: ErrorDefinition(
            HTTPStatus.FAILED_DEPENDENCY,
            ErrorAction.CREATE_NEW_ATTEMPT,
            "원본 다운로드를 다시 시도해야 합니다.",
        ),
        ErrorCode.SOURCE_ACCESS_DENIED: ErrorDefinition(
            HTTPStatus.FAILED_DEPENDENCY, ErrorAction.STOP, "원본에 접근할 수 없습니다."
        ),
        ErrorCode.SOURCE_NOT_FOUND: ErrorDefinition(
            HTTPStatus.FAILED_DEPENDENCY, ErrorAction.STOP, "원본을 찾을 수 없습니다."
        ),
        ErrorCode.SOURCE_IDENTITY_MISMATCH: ErrorDefinition(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorAction.STOP,
            "실제 원본과 요청 정보가 다릅니다.",
        ),
        ErrorCode.FILE_TOO_LARGE: ErrorDefinition(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            ErrorAction.STOP,
            "파일 크기 제한을 초과했습니다.",
        ),
        ErrorCode.UNSUPPORTED_MEDIA_TYPE: ErrorDefinition(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            ErrorAction.STOP,
            "PDF 파일이 필요합니다.",
        ),
        ErrorCode.PAGE_LIMIT_EXCEEDED: ErrorDefinition(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorAction.STOP,
            "페이지 수 제한을 초과했습니다.",
        ),
        ErrorCode.PDF_ENCRYPTED: ErrorDefinition(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorAction.STOP,
            "암호가 필요한 PDF는 지원하지 않습니다.",
        ),
        ErrorCode.PDF_CORRUPTED: ErrorDefinition(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorAction.STOP,
            "정상적으로 읽을 수 없는 PDF입니다.",
        ),
        ErrorCode.NO_EXTRACTABLE_TEXT: ErrorDefinition(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            ErrorAction.STOP,
            "추출 가능한 본문 텍스트가 없습니다.",
        ),
        ErrorCode.CAPACITY_EXCEEDED: ErrorDefinition(
            HTTPStatus.SERVICE_UNAVAILABLE,
            ErrorAction.CREATE_NEW_ATTEMPT,
            "현재 새 파싱을 시작할 수 없습니다.",
        ),
        ErrorCode.PARSER_TIMEOUT: ErrorDefinition(
            HTTPStatus.GATEWAY_TIMEOUT,
            ErrorAction.CREATE_NEW_ATTEMPT,
            "파싱 제한시간을 초과해 실행을 종료했습니다.",
        ),
        ErrorCode.PARSER_FAILED: ErrorDefinition(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            ErrorAction.STOP,
            "PDF 추출 실행 또는 결과 검증에 실패했습니다.",
        ),
        ErrorCode.CONFIGURATION_UNAVAILABLE: ErrorDefinition(
            HTTPStatus.SERVICE_UNAVAILABLE,
            ErrorAction.STOP,
            "PDF 파싱 서비스를 초기화할 수 없습니다.",
        ),
    }
)


class ParseError(ContractModel):
    """오류 코드에 맞는 행동을 함께 전달한다."""

    code: ErrorCode
    message: str
    action: ErrorAction

    @classmethod
    def from_code(cls, code: ErrorCode) -> Self:
        """내부 예외나 원본 URL을 포함하지 않는 오류를 만든다."""
        definition = ERROR_DEFINITIONS[code]
        return cls(code=code, message=definition.message, action=definition.action)

    @model_validator(mode="after")
    def require_matching_action(self) -> Self:
        """동일 오류 코드에 상반된 재시도 지시가 붙는 것을 막는다."""
        if self.action != ERROR_DEFINITIONS[self.code].action:
            raise PydanticCustomError(
                "error_action_mismatch", "오류 코드와 다음 행동이 일치하지 않습니다."
            )
        return self


class ParseErrorResponse(ContractModel):
    """접수 전 오류에는 아직 확인할 수 없는 식별자를 null로 둔다."""

    report_id: ReportIdentifier | None = None
    parse_attempt_id: AttemptIdentifier | None = None
    error: ParseError


class PdfParseApiError(Exception):
    """HTTP 경계에서 처리할 코드와 요청 식별자를 운반한다."""

    def __init__(
        self, code: ErrorCode, identity: AttemptIdentity | None = None
    ) -> None:
        self.code = code
        self.identity = identity
        super().__init__(ERROR_DEFINITIONS[code].message)
