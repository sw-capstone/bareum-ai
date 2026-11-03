from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from bareum_ai.pdf_parsing.api.requests import PdfParseRequest
from bareum_ai.pdf_parsing.api.responses import SucceededResponse
from bareum_ai.pdf_parsing.api.security import require_internal_api_token
from bareum_ai.pdf_parsing.errors import ParseErrorResponse, PdfParseApiError
from bareum_ai.pdf_parsing.runtime import PdfParsingRuntime
from bareum_ai.pdf_parsing.service import PdfParsingService


async def _cancel_on_disconnect(request: Request, scope: anyio.CancelScope) -> None:
    while True:
        if (await request.receive())["type"] == "http.disconnect":
            scope.cancel()
            return


def create_router(runtime: PdfParsingRuntime) -> APIRouter:
    router = APIRouter(
        prefix="/internal/v1/pdf-parses",
        dependencies=[Depends(require_internal_api_token)],
        tags=["PDF parsing"],
    )

    @router.post(
        "",
        response_model=SucceededResponse,
        summary="PDF 파싱 요청",
        description="원본 PDF를 다운로드·검증하고 기존 파서의 추출 결과를 반환합니다.",
        responses={
            code: {"model": ParseErrorResponse}
            for code in (400, 401, 409, 413, 415, 422, 424, 500, 503, 504)
        },
    )
    async def parse_pdf(
        payload: PdfParseRequest,
        request: Request,
        service: Annotated[PdfParsingService, Depends(runtime.service)],
    ) -> Response:
        result: SucceededResponse | None = None
        failure: PdfParseApiError | None = None
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(_cancel_on_disconnect, request, tasks.cancel_scope)
            try:
                result = await service.parse(payload)
            except PdfParseApiError as exc:
                failure = exc
            finally:
                tasks.cancel_scope.cancel()
        if failure is not None:
            raise failure
        if result is None:
            return Response(status_code=499)
        return Response(result.model_dump_json(), media_type="application/json")

    return router
