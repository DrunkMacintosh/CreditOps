from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class ApiError(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    code: str
    message_vi: str = Field(serialization_alias="messageVi")
    correlation_id: str = Field(serialization_alias="correlationId")
    retryable: bool


class ApiException(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message_vi: str,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message_vi = message_vi
        self.retryable = retryable
        self.headers = headers


def correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else "unknown"


def api_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message_vi: str,
    retryable: bool,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error = ApiError(
        code=code,
        message_vi=message_vi,
        correlation_id=correlation_id(request),
        retryable=retryable,
    )
    response = JSONResponse(
        status_code=status_code,
        content=error.model_dump(mode="json", by_alias=True),
        headers=headers,
    )
    response.headers["X-Correlation-ID"] = error.correlation_id
    return response


async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
    return api_error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message_vi=exc.message_vi,
        retryable=exc.retryable,
        headers=exc.headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del exc
    return api_error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message_vi="Dữ liệu yêu cầu không hợp lệ.",
        retryable=False,
    )


async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    del exc
    return api_error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message_vi="Hệ thống không thể xử lý yêu cầu.",
        retryable=False,
    )
